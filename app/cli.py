"""CLI entry point: run / step / export / status / reset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import StepNotImplementedError, load_config
from app.db.repository import DEFAULT_DB_PATH, open_repository
from app.export.xlsx import default_output_path, export_xlsx
from app.matching.matcher import run_match
from app.pipeline.ingest import run_ingest
from app.registry.loader import load_registry
from app.sources.universities.layer1 import run_layer1
from app.sources.universities.layer2 import run_layer2
from app.sources.vak.pipeline import run_vak


def _ensure_run(repo, *, is_full: bool) -> int:
  if is_full:
    return repo.create_run(is_full=True)
  existing = repo.find_resumable_run()
  if existing:
    return existing
  return repo.create_run(is_full=False)


def _prepare_run(repo, cfg, *, is_full: bool) -> int:
  run_id = _ensure_run(repo, is_full=is_full)
  if repo.count_table("universities") == 0:
    load_registry(repo)
  return run_id


def cmd_run(args: argparse.Namespace) -> int:
  cfg = load_config(args.config)
  cfg.validate_implemented_steps()
  with open_repository(args.db) as repo:
    run_id = _prepare_run(repo, cfg, is_full=args.full)
    run_ingest(repo, run_id, cfg, db_path=args.db, domain=args.domain)
    if cfg.run.match:
      run_match(repo, run_id)
    if cfg.run.layer2:
      # Must run after match: layer2's candidate query filters by
      # match_status, which run_match() has just populated above. Running
      # it any earlier (e.g. from inside run_ingest, as before) means the
      # filter always matches zero rows and layer2 silently does nothing.
      layer2_limit = args.layer2_limit if args.layer2_limit is not None else cfg.limits.layer2_limit
      run_layer2(
        args.db,
        run_id,
        request_delay_sec=cfg.limits.layer2_request_delay_sec,
        workers=cfg.limits.layer2_workers,
        domain=args.domain,
        limit=layer2_limit,
      )
    out = Path(args.out) if args.out else default_output_path()
    export_xlsx(repo, out, domain=args.domain)
    repo.finish_run(run_id, "success")
    print(f"Run {run_id} completed. Export: {out}")
  return 0


def cmd_step(args: argparse.Namespace) -> int:
  cfg = load_config(args.config)
  with open_repository(args.db) as repo:
    run_id = _prepare_run(repo, cfg, is_full=False)
    if args.step_name == "layer1":
      run_layer1(
        args.db,
        run_id,
        request_delay_sec=cfg.limits.request_delay_sec,
        max_universities=cfg.limits.max_universities,
        workers=cfg.limits.layer1_workers,
        domain=args.domain,
      )
    elif args.step_name == "vak":
      run_vak(
        args.db,
        run_id,
        request_delay_sec=cfg.limits.vak_request_delay_sec,
        max_pages=cfg.limits.vak_max_pages,
        detail_workers=cfg.limits.vak_detail_workers,
      )
    elif args.step_name == "layer2":
      run_layer2(
        args.db,
        run_id,
        request_delay_sec=cfg.limits.layer2_request_delay_sec,
        workers=cfg.limits.layer2_workers,
        domain=args.domain,
        limit=args.limit if args.limit is not None else cfg.limits.layer2_limit,
      )
    elif args.step_name == "match":
      run_match(repo, run_id)
    print(f"Step {args.step_name} completed for run {run_id}.")
  return 0


def cmd_export(args: argparse.Namespace) -> int:
  out = Path(args.out)
  with open_repository(args.db, init=False) as repo:
    export_xlsx(repo, out, domain=args.domain)
  print(f"Exported to {out}")
  return 0


def cmd_status(args: argparse.Namespace) -> int:
  db_path = Path(args.db)
  if not db_path.exists():
    print("Database not found.")
    return 0
  with open_repository(args.db, init=False) as repo:
    uni_ok = repo.execute(
      "SELECT COUNT(*) AS c FROM universities WHERE layer1_status = 'ok'"
    ).fetchone()["c"]
    uni_err = repo.execute(
      "SELECT COUNT(*) AS c FROM universities WHERE layer1_status IS NOT NULL "
      "AND layer1_status != 'ok'"
    ).fetchone()["c"]
    print(f"Universities layer1 ok: {uni_ok}, errors: {uni_err}")
    print(f"employees_raw: {repo.count_table('employees_raw')}")
    print(f"vak_raw: {repo.count_table('vak_raw')}")
    print(f"candidates: {repo.count_table('candidates')}")
    print(f"DB size: {db_path.stat().st_size} bytes")
    last = repo.execute(
      "SELECT status, finished_at FROM runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if last:
      state = "finished" if last["finished_at"] else "interrupted"
      print(f"Last run status: {last['status']} ({state})")
  return 0


def cmd_reset(args: argparse.Namespace) -> int:
  if Path(args.db).exists():
    with open_repository(args.db) as repo:
      repo.reset_database()
  print("Database reset.")
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(prog="app", description="Candidate search pipeline")
  parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
  parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to state.sqlite")
  parser.add_argument(
    "--domain", default=None,
    help="Restrict layer1 (and therefore the whole pipeline's output) to a single "
         "university domain, e.g. --domain utmn.ru",
  )
  sub = parser.add_subparsers(dest="command", required=True)

  p_run = sub.add_parser("run", help="Run full pipeline")
  p_run.add_argument("--full", action="store_true", help="Force full rebuild")
  p_run.add_argument("--out", default=None, help="Output xlsx path")
  p_run.add_argument(
    "--layer2-limit", type=int, default=None,
    help="Override limits.layer2_limit from config for this run, e.g. --layer2-limit 100 "
         "for a quick test without editing the yaml file",
  )
  p_run.set_defaults(func=cmd_run)

  p_step = sub.add_parser("step", help="Run a single step")
  p_step.add_argument("step_name", choices=["layer1", "vak", "layer2", "match"])
  p_step.add_argument(
    "--limit", type=int, default=None,
    help="layer2 only: max candidates to process (default: config limits.layer2_limit, currently 100)",
  )
  p_step.set_defaults(func=cmd_step)

  p_export = sub.add_parser("export", help="Export xlsx from database")
  p_export.add_argument("--out", default="output/candidates.xlsx")
  p_export.set_defaults(func=cmd_export)

  sub.add_parser("status", help="Print pipeline status").set_defaults(func=cmd_status)
  sub.add_parser("reset", help="Reset database with backup").set_defaults(func=cmd_reset)
  return parser


def main(argv: list[str] | None = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  try:
    return args.func(args)
  except StepNotImplementedError as exc:
    print(str(exc), file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())

"""CLI entry point: run / step / export / status / reset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import AppConfig, StepNotImplementedError, load_config
from app.db.repository import DEFAULT_DB_PATH, Repository, open_repository


def _placeholder_step(name: str) -> None:
  print(f"Step '{name}' is not wired yet.")


def cmd_run(args: argparse.Namespace) -> int:
  cfg = load_config(args.config)
  cfg.validate_implemented_steps()
  with open_repository(args.db) as repo:
    run_id = repo.create_run(is_full=args.full)
    print(f"Created run {run_id} (placeholder orchestration).")
  return 0


def cmd_step(args: argparse.Namespace) -> int:
  load_config(args.config)
  _placeholder_step(args.step_name)
  return 0


def cmd_export(args: argparse.Namespace) -> int:
  out = Path(args.out)
  print(f"Export to {out} is not wired yet.")
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
      print(f"Last run status: {last['status']}, finished_at: {last['finished_at']}")
  return 0


def cmd_reset(args: argparse.Namespace) -> int:
  from app.db.repository import backup_state

  backup_state("reset", db_path=args.db)
  if Path(args.db).exists():
    with open_repository(args.db) as repo:
      repo.reset_database()
  print("Database reset.")
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(prog="app", description="Candidate search pipeline")
  parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
  parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to state.sqlite")
  sub = parser.add_subparsers(dest="command", required=True)

  p_run = sub.add_parser("run", help="Run full pipeline")
  p_run.add_argument("--full", action="store_true", help="Force full rebuild")
  p_run.set_defaults(func=cmd_run)

  p_step = sub.add_parser("step", help="Run a single step")
  p_step.add_argument("step_name", choices=["layer1", "vak", "match"])
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

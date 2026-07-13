"""Parallel ingest: layer1 (per-university) and VAK run concurrently.

layer2 is NOT run from here -- it depends on `candidates.match_status`,
which is only populated by `run_match()` (called after this function
returns, see `cmd_run` in app/cli.py). Running it from here used to mean
layer2's candidate query (`match_status IN (...)`) always matched zero
rows, since match hadn't happened yet -- layer2 would silently no-op on
every run regardless of `run.layer2` in config.yaml.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import AppConfig
from app.db.repository import Repository
from app.sources.universities.layer1 import run_layer1
from app.sources.vak.pipeline import run_vak


def run_ingest(
  repo: Repository,
  run_id: int,
  cfg: AppConfig,
  *,
  db_path: Path | str,
  domain: str | None = None,
) -> None:
  """Run layer1 and/or vak, in parallel when both are enabled.

  `domain`: restrict layer1 to a single university's domain (e.g. run the
  whole pipeline against just one university for testing/debugging). VAK
  itself isn't per-university so it still runs its normal search, but
  match/export only ever see candidates tied to universities layer1
  actually processed -- so the end-to-end output is effectively scoped to
  that one domain.
  """
  do_layer1 = cfg.run.layer1
  do_vak = cfg.run.vak

  if not do_layer1 and not do_vak:
    return

  if do_layer1 and do_vak:
    with ThreadPoolExecutor(max_workers=2) as pool:
      futures = [
        pool.submit(
          run_layer1,
          db_path,
          run_id,
          request_delay_sec=cfg.limits.request_delay_sec,
          max_universities=cfg.limits.max_universities,
          workers=cfg.limits.layer1_workers,
          domain=domain,
        ),
        pool.submit(
          run_vak,
          db_path,
          run_id,
          request_delay_sec=cfg.limits.vak_request_delay_sec,
          max_pages=cfg.limits.vak_max_pages,
          detail_workers=cfg.limits.vak_detail_workers,
        ),
      ]
      for future in as_completed(futures):
        future.result()
  else:
    if do_layer1:
      run_layer1(
        db_path,
        run_id,
        request_delay_sec=cfg.limits.request_delay_sec,
        max_universities=cfg.limits.max_universities,
        workers=cfg.limits.layer1_workers,
        domain=domain,
      )
    if do_vak:
      run_vak(
        db_path,
        run_id,
        request_delay_sec=cfg.limits.vak_request_delay_sec,
        max_pages=cfg.limits.vak_max_pages,
        detail_workers=cfg.limits.vak_detail_workers,
      )


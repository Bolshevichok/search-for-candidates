"""Parallel ingest: layer1 (per-university) and VAK run concurrently until match."""

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
) -> None:
  """Run layer1 and/or vak; parallelize when both are enabled."""
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
    return

  if do_layer1:
    run_layer1(
      db_path,
      run_id,
      request_delay_sec=cfg.limits.request_delay_sec,
      max_universities=cfg.limits.max_universities,
      workers=cfg.limits.layer1_workers,
    )
  if do_vak:
    run_vak(
      db_path,
      run_id,
      request_delay_sec=cfg.limits.vak_request_delay_sec,
      max_pages=cfg.limits.vak_max_pages,
      detail_workers=cfg.limits.vak_detail_workers,
    )

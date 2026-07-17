from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import Limits
from app.sources.universities.layer1 import run_layer1
from app.sources.vak.pipeline import run_vak


def run_ingest(
  run_id: int,
  limits: Limits,
  *,
  db_path: Path | str,
  domain: str | None = None,
) -> None:
  with ThreadPoolExecutor(max_workers=2) as pool:
    futures = [
      pool.submit(
        run_layer1,
        db_path,
        run_id,
        request_delay_sec=limits.request_delay_sec,
        max_universities=limits.max_universities,
        workers=limits.layer1_workers,
        domain=domain,
      ),
      pool.submit(
        run_vak,
        db_path,
        run_id,
        request_delay_sec=limits.vak_request_delay_sec,
        max_pages=limits.vak_max_pages,
        detail_workers=limits.vak_detail_workers,
      ),
    ]
    for future in as_completed(futures):
      future.result()

"""Run VAK fetch step with pagination checkpoints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.db.repository import open_repository
from app.sources.http_client import HttpClient
from app.sources.vak.client import VakClient
from app.sources.vak.parser import parse_vak_record


def _fetch_record(
  item: dict[str, Any],
  *,
  is_pilot: bool,
  request_delay_sec: float,
  timeout: float,
) -> dict[str, Any]:
  with HttpClient(request_delay_sec=request_delay_sec, timeout=timeout) as client:
    vak = VakClient(client)
    vak_id = item.get("id")
    detail = vak.fetch_detail(str(vak_id)) if vak_id else None
    return parse_vak_record(item, detail, is_pilot_branch=is_pilot)


def _upsert_page_details(
  repo,
  items: list[dict[str, Any]],
  *,
  is_pilot: bool,
  request_delay_sec: float,
  timeout: float,
  detail_workers: int,
) -> None:
  if not items:
    return
  workers = max(1, min(detail_workers, len(items)))
  if workers == 1:
    for item in items:
      repo.upsert_vak_record(
        _fetch_record(
          item,
          is_pilot=is_pilot,
          request_delay_sec=request_delay_sec,
          timeout=timeout,
        )
      )
    return

  with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = {
      pool.submit(
        _fetch_record,
        item,
        is_pilot=is_pilot,
        request_delay_sec=request_delay_sec,
        timeout=timeout,
      ): item
      for item in items
    }
    for future in as_completed(futures):
      repo.upsert_vak_record(future.result())


def run_vak(
  db_path: Path | str,
  run_id: int,
  *,
  request_delay_sec: float = 0.0,
  timeout: float = 10.0,
  max_pages: int | None = None,
  detail_workers: int = 8,
) -> None:
  """Fetch VAK records for both is_pilot branches (FR-004).

  List pages are sequential (checkpoint); detail cards for each page run in parallel.
  """
  with open_repository(db_path, init=False) as repo:
    start_page = repo.get_vak_checkpoint(run_id) + 1

    with HttpClient(request_delay_sec=request_delay_sec, timeout=timeout) as client:
      vak = VakClient(client)
      for is_pilot in (False, True):
        branch_start = 1 if is_pilot else start_page
        pages_fetched = 0
        for page, items in vak.iter_pages(is_pilot=is_pilot, start_page=branch_start):
          _upsert_page_details(
            repo,
            items,
            is_pilot=is_pilot,
            request_delay_sec=request_delay_sec,
            timeout=timeout,
            detail_workers=detail_workers,
          )
          if not is_pilot:
            repo.mark_step_done(
              run_id,
              "vak",
              None,
              checkpoint_cursor=page,
            )
          pages_fetched += 1
          if max_pages is not None and pages_fetched >= max_pages:
            break

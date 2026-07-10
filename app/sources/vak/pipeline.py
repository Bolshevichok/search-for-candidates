"""Run VAK fetch step with pagination checkpoints."""

from __future__ import annotations

import sqlite3

from app.db.repository import Repository
from app.sources.http_client import HttpClient
from app.sources.vak.client import VakClient
from app.sources.vak.parser import parse_vak_record


def run_vak(
  repo: Repository,
  run_id: int,
  *,
  request_delay_sec: float = 0.0,
  timeout: float = 10.0,
  max_pages: int | None = None,
) -> None:
  """Fetch VAK records for both is_pilot branches (FR-004).

  max_pages caps how many pages are fetched per branch — intended for quick
  exploratory/smoke runs only (see config.limits.vak_max_pages). Leave it
  None for a real full run: the checkpoint logic assumes the false-branch
  cursor represents genuine progress through the whole dataset.
  """
  start_page = repo.get_vak_checkpoint(run_id) + 1

  with HttpClient(request_delay_sec=request_delay_sec, timeout=timeout) as client:
    vak = VakClient(client)
    for is_pilot in (False, True):
      branch_start = 1 if is_pilot else start_page
      pages_fetched = 0
      for page, items in vak.iter_pages(is_pilot=is_pilot, start_page=branch_start):
        for item in items:
          repo.upsert_vak_record(parse_vak_record(item, is_pilot_branch=is_pilot))
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

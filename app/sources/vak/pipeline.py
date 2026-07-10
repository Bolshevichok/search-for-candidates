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
) -> None:
  start_page = repo.get_vak_checkpoint(run_id) + 1
  pilot_universities = {
    int(row["university_id"])
    for row in repo.execute("SELECT university_id FROM universities WHERE is_pilot = 1").fetchall()
  }
  _ = pilot_universities  # both branches always fetched per FR-004

  with HttpClient(request_delay_sec=request_delay_sec, timeout=timeout) as client:
    vak = VakClient(client)
    for is_pilot in (False, True):
      branch_start = 1 if is_pilot else start_page
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

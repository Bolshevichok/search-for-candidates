from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.config import Limits
from app.db.repository import open_repository
from app.export.xlsx import export_xlsx
from app.matching.matcher import run_match
from app.pipeline.cancellation import CancellationToken
from app.pipeline.ingest import run_ingest
from app.registry.loader import load_registry
from app.sources.vk.browser import run_vk

StageCallback = Callable[[str], None]


def run_full_pipeline(
  run_id: int,
  limits: Limits,
  *,
  db_path: Path | str,
  output_path: Path,
  domain: str | None = None,
  cancel_token: CancellationToken | None = None,
  on_stage: StageCallback | None = None,
) -> Path:
  token = cancel_token or CancellationToken()
  token.check()

  def set_stage(stage: str) -> None:
    token.check()
    if on_stage is not None:
      on_stage(stage)

  with open_repository(db_path) as repo:
    if repo.count_table("universities") == 0 or repo.count_table("university_vk_communities") == 0:
      load_registry(repo)

  set_stage("ingest")
  run_ingest(run_id, limits, db_path=db_path, domain=domain, cancel_token=token)

  set_stage("match")
  with open_repository(db_path, init=False) as repo:
    run_match(repo, run_id, cancel_token=token)
  token.check()

  if limits.vk_enabled:
    set_stage("vk")
    run_vk(
      db_path,
      run_id,
      request_delay_sec=limits.vk_request_delay_sec,
      workers=limits.vk_workers,
      domain=domain,
      limit=limits.vk_limit,
      extract_public_contacts=limits.vk_extract_public_contacts,
      cancel_token=token,
    )

  set_stage("export")
  with open_repository(db_path, init=False) as repo:
    return export_xlsx(repo, output_path, domain=domain, cancel_token=token)

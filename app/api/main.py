from __future__ import annotations

import json
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api import service
from app.config import Limits, load_config
from app.db.repository import DEFAULT_DB_PATH, open_repository
from app.pipeline.cancellation import CancellationToken, PipelineCancelled
from app.pipeline.run import run_full_pipeline

_LOGGER = logging.getLogger(__name__)
_ACTIVE_JOB_STATUSES = {"queued", "ingest", "match", "vk", "export", "cancelling"}
_RUN_LIMIT_FIELDS = (
  "request_delay_sec",
  "max_universities",
  "vak_max_pages",
  "layer1_workers",
  "vak_request_delay_sec",
  "vak_detail_workers",
  "vk_workers",
  "vk_request_delay_sec",
  "vk_limit",
  "vk_extract_public_contacts",
  "vk_enabled",
)


@dataclass(frozen=True)
class ApiSettings:
  db_path: Path = DEFAULT_DB_PATH
  config_path: Path = Path("config.yaml")
  output_dir: Path = Path("output")

  @classmethod
  def from_env(cls) -> "ApiSettings":
    return cls(
      db_path=Path(os.environ.get("APP_DB_PATH", str(DEFAULT_DB_PATH))),
      config_path=Path(os.environ.get("APP_CONFIG_PATH", "config.yaml")),
      output_dir=Path(os.environ.get("APP_OUTPUT_DIR", "output")),
    )


class RunLimitsInput(BaseModel):
  request_delay_sec: float | None = Field(default=None, ge=0)
  max_universities: int | None = Field(default=None, ge=1)
  vak_max_pages: int | None = Field(default=None, ge=1)
  layer1_workers: int | None = Field(default=None, ge=1)
  vak_request_delay_sec: float | None = Field(default=None, ge=0)
  vak_detail_workers: int | None = Field(default=None, ge=1)
  vk_workers: int | None = Field(default=None, ge=1)
  vk_request_delay_sec: float | None = Field(default=None, ge=0)
  vk_limit: int | None = Field(default=None, ge=1)
  vk_extract_public_contacts: bool | None = None
  vk_enabled: bool | None = None


class RunCreateInput(BaseModel):
  domain: str | None = Field(default=None, max_length=255)
  limits: RunLimitsInput = Field(default_factory=RunLimitsInput)


def _limits_payload(limits: Limits) -> dict[str, Any]:
  raw = asdict(limits)
  return {name: raw[name] for name in _RUN_LIMIT_FIELDS}


class PipelineJobManager:
  def __init__(self, settings: ApiSettings) -> None:
    self.settings = settings
    self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
    self._tokens: dict[int, CancellationToken] = {}
    self._futures: dict[int, Future[None]] = {}
    self._lock = Lock()

  def prepare(self) -> None:
    with open_repository(self.settings.db_path) as repo:
      interrupted = repo.interrupt_active_pipeline_jobs()
    if interrupted:
      _LOGGER.warning("Marked interrupted API runs after restart: %s", interrupted)

  def shutdown(self) -> None:
    self._executor.shutdown(wait=False, cancel_futures=True)

  def start(self, request: RunCreateInput) -> dict[str, Any]:
    config = load_config(self.settings.config_path)
    updates = request.limits.model_dump(exclude_none=True)
    limits = replace(config.limits, **updates)
    domain = request.domain.strip().casefold() if request.domain and request.domain.strip() else None
    run_config = {"domain": domain, "limits": _limits_payload(limits)}

    with self._lock:
      with open_repository(self.settings.db_path) as repo:
        if repo.active_run_exists():
          raise RuntimeError("A pipeline run is already active")
        run_id = repo.create_run()
        output_path = self.settings.output_dir / f"candidates_run_{run_id}.xlsx"
        repo.create_pipeline_job(
          run_id,
          config_json=json.dumps(run_config, ensure_ascii=False),
          output_path=str(output_path),
        )
      token = CancellationToken()
      self._tokens[run_id] = token
      self._futures[run_id] = self._executor.submit(
        self._run, run_id, limits, domain, output_path, token,
      )

    with open_repository(self.settings.db_path, init=False) as repo:
      row = repo.get_pipeline_job(run_id)
      if row is None:
        raise RuntimeError("Pipeline run was not created")
      return service.run_payload(row, output_dir=self.settings.output_dir)

  def cancel(self, run_id: int) -> dict[str, Any] | None:
    with self._lock:
      with open_repository(self.settings.db_path, init=False) as repo:
        row = repo.get_pipeline_job(run_id)
        if row is None:
          return None
        current_status = row["status"] or row["run_status"]
        if current_status not in _ACTIVE_JOB_STATUSES:
          return service.run_payload(row, output_dir=self.settings.output_dir)
        repo.request_pipeline_cancel(run_id)
        row = repo.get_pipeline_job(run_id)
      token = self._tokens.get(run_id)
      if token is not None:
        token.cancel()
    return service.run_payload(row, output_dir=self.settings.output_dir) if row else None

  def _set_stage(self, run_id: int, stage: str) -> None:
    with open_repository(self.settings.db_path, init=False) as repo:
      row = repo.get_pipeline_job(run_id)
      if row is None:
        return
      if row["status"] == "cancelling":
        return
      repo.update_pipeline_job(run_id, status=stage)

  def _run(
    self,
    run_id: int,
    limits: Limits,
    domain: str | None,
    output_path: Path,
    token: CancellationToken,
  ) -> None:
    try:
      run_full_pipeline(
        run_id,
        limits,
        db_path=self.settings.db_path,
        output_path=output_path,
        domain=domain,
        cancel_token=token,
        on_stage=lambda stage: self._set_stage(run_id, stage),
      )
    except PipelineCancelled:
      output_path.unlink(missing_ok=True)
      output_path.with_name(f".{output_path.name}.partial").unlink(missing_ok=True)
      with open_repository(self.settings.db_path, init=False) as repo:
        repo.finish_run(run_id, "failed")
        repo.update_pipeline_job(run_id, status="cancelled")
    except Exception as exc:
      _LOGGER.exception("Pipeline run %s failed", run_id)
      with open_repository(self.settings.db_path, init=False) as repo:
        repo.finish_run(run_id, "failed")
        repo.update_pipeline_job(run_id, status="failed", error_message=f"{type(exc).__name__}: {exc}")
    else:
      with open_repository(self.settings.db_path, init=False) as repo:
        repo.finish_run(run_id, "success")
        repo.update_pipeline_job(run_id, status="success")
    finally:
      with self._lock:
        self._tokens.pop(run_id, None)
        self._futures.pop(run_id, None)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
  api_settings = settings or ApiSettings.from_env()
  manager = PipelineJobManager(api_settings)

  @asynccontextmanager
  async def lifespan(_: FastAPI):
    manager.prepare()
    try:
      yield
    finally:
      manager.shutdown()

  app = FastAPI(
    title="Поиск кандидатов",
    version="0.1.0",
    description="Локальный API для сбора, просмотра и выгрузки результатов.",
    lifespan=lifespan,
  )
  app.state.settings = api_settings
  app.state.jobs = manager
  @app.get("/", include_in_schema=False)
  def index() -> dict[str, str]:
    return {"name": "Поиск кандидатов API", "docs": "/docs"}

  @app.get("/api/v1/health")
  def health() -> dict[str, Any]:
    with open_repository(api_settings.db_path) as repo:
      return {
        "status": "ok",
        "database": str(api_settings.db_path),
        "candidates": repo.count_table("candidates"),
      }

  @app.get("/api/v1/analytics")
  def get_analytics(domain: str | None = None, run_id: int | None = None) -> dict[str, Any]:
    with open_repository(api_settings.db_path) as repo:
      return service.analytics(repo, domain=domain, run_id=run_id)

  @app.get("/api/v1/universities")
  def get_universities() -> list[dict[str, Any]]:
    with open_repository(api_settings.db_path) as repo:
      return service.list_universities(repo)

  @app.get("/api/v1/records/teachers")
  def get_teachers(
    q: str | None = Query(default=None, max_length=255),
    domain: str | None = None,
    has_vk: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
  ) -> dict[str, Any]:
    with open_repository(api_settings.db_path, init=False) as repo:
      return service.list_teachers(
        repo, query=q, domain=domain, has_vk=has_vk, limit=limit, offset=offset,
      )

  @app.get("/api/v1/records/vak")
  def get_vak_candidates(
    q: str | None = Query(default=None, max_length=255),
    domain: str | None = None,
    has_vk: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
  ) -> dict[str, Any]:
    with open_repository(api_settings.db_path, init=False) as repo:
      return service.list_vak_candidates(
        repo, query=q, domain=domain, has_vk=has_vk, limit=limit, offset=offset,
      )

  @app.get("/api/v1/candidates/{candidate_id}")
  def get_candidate(candidate_id: str) -> dict[str, Any]:
    with open_repository(api_settings.db_path, init=False) as repo:
      candidate = service.candidate_detail(repo, candidate_id)
    if candidate is None:
      raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate

  @app.get("/api/v1/errors")
  def get_errors(
    run_id: int | None = None,
    domain: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
  ) -> dict[str, Any]:
    with open_repository(api_settings.db_path, init=False) as repo:
      return service.list_errors(
        repo, run_id=run_id, domain=domain, limit=limit, offset=offset,
      )

  @app.get("/api/v1/runs/options")
  def run_options() -> dict[str, Any]:
    limits = load_config(api_settings.config_path).limits
    return {"defaults": {"domain": None, "limits": _limits_payload(limits)}}

  @app.get("/api/v1/runs")
  def list_runs(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    with open_repository(api_settings.db_path, init=False) as repo:
      rows = repo.list_pipeline_jobs(limit)
    return {
      "items": [service.run_payload(row, output_dir=api_settings.output_dir) for row in rows]
    }

  @app.get("/api/v1/runs/{run_id}")
  def get_run(run_id: int) -> dict[str, Any]:
    with open_repository(api_settings.db_path, init=False) as repo:
      row = repo.get_pipeline_job(run_id)
    if row is None:
      raise HTTPException(status_code=404, detail="Run not found")
    return service.run_payload(row, output_dir=api_settings.output_dir)

  @app.post("/api/v1/runs", status_code=status.HTTP_202_ACCEPTED)
  def start_run(payload: RunCreateInput, request: Request) -> dict[str, Any]:
    try:
      return request.app.state.jobs.start(payload)
    except RuntimeError as exc:
      raise HTTPException(status_code=409, detail=str(exc)) from exc

  @app.delete("/api/v1/runs/{run_id}", status_code=status.HTTP_202_ACCEPTED)
  def cancel_run(run_id: int, request: Request) -> dict[str, Any]:
    payload = request.app.state.jobs.cancel(run_id)
    if payload is None:
      raise HTTPException(status_code=404, detail="Run not found")
    return payload

  @app.get("/api/v1/runs/{run_id}/xlsx")
  def download_xlsx(run_id: int) -> FileResponse:
    with open_repository(api_settings.db_path, init=False) as repo:
      row = repo.get_pipeline_job(run_id)
    if row is None:
      raise HTTPException(status_code=404, detail="Run not found")
    payload = service.run_payload(row, output_dir=api_settings.output_dir)
    path_value = row["output_path"]
    if not payload["xlsx_available"] or not path_value:
      raise HTTPException(status_code=404, detail="XLSX is not available for this run")
    output_path = Path(path_value).resolve()
    output_dir = api_settings.output_dir.resolve()
    if output_dir != output_path.parent or not output_path.is_file():
      raise HTTPException(status_code=404, detail="XLSX is not available for this run")
    return FileResponse(
      output_path,
      media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      filename=f"candidates_run_{run_id}.xlsx",
    )

  return app


app = create_app()

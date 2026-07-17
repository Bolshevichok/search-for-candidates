from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import ApiSettings, create_app
from app.db.repository import open_repository
from app.pipeline.cancellation import CancellationToken


def _settings(tmp_path: Path) -> ApiSettings:
  config = tmp_path / "config.yaml"
  config.write_text("limits:\n  max_universities: 1\n", encoding="utf-8")
  return ApiSettings(
    db_path=tmp_path / "state.sqlite",
    config_path=config,
    output_dir=tmp_path / "output",
  )


def _seed_database(settings: ApiSettings) -> None:
  with open_repository(settings.db_path) as repo:
    run_id = repo.create_run()
    university_id = repo.upsert_university(
      official_name="Тестовый университет",
      aliases=[],
      domain="test.example",
      region=None,
      accreditation_status=None,
      is_pilot=False,
    )
    community_id = repo.upsert_vk_community(
      university_id=university_id,
      vk_group_id=None,
      vk_screen_name="test",
      vk_url="https://vk.com/test",
    )
    repo.upsert_candidate({
      "candidate_id": "C000001",
      "full_name": "Иванов Иван Иванович",
      "match_status": "site_no_vak",
      "university_id": university_id,
      "post": "Доцент",
      "disciplines": ["Математика"],
      "defenses": [],
      "candidate_content_hash": "teacher",
      "first_seen_run_id": run_id,
      "last_seen_run_id": run_id,
    })
    repo.upsert_candidate({
      "candidate_id": "C000002",
      "full_name": "Петров Пётр Петрович",
      "match_status": "vak_no_site",
      "university_id": university_id,
      "degree": "кандидат наук",
      "defenses": [{
        "date": "2024-01-01",
        "specialty": "1.1.1 Математика",
        "branch": "Физико-математические науки",
        "dissertation_type": "кандидатская",
        "topic": "Тестовая тема",
        "defend_org": "Тестовый университет",
        "is_pilot": False,
      }],
      "candidate_content_hash": "vak",
      "first_seen_run_id": run_id,
      "last_seen_run_id": run_id,
    })
    repo.upsert_candidate_vk_profile({
      "candidate_id": "C000001",
      "community_id": community_id,
      "profile_url": "https://vk.com/test_person",
      "vk_match_status": "matched",
    })
    repo.finish_run(run_id)


def _wait_for_terminal(client: TestClient, run_id: int) -> dict:
  for _ in range(100):
    payload = client.get(f"/api/v1/runs/{run_id}").json()
    if payload["status"] not in {"queued", "ingest", "match", "vk", "export", "cancelling"}:
      return payload
    time.sleep(0.02)
  raise AssertionError("Run did not finish")


def test_read_api_exposes_export_tables(tmp_path: Path) -> None:
  settings = _settings(tmp_path)
  _seed_database(settings)
  with TestClient(create_app(settings)) as client:
    teachers = client.get("/api/v1/records/teachers?domain=test.example").json()
    vak = client.get("/api/v1/records/vak?domain=test.example").json()
    analytics = client.get("/api/v1/analytics?run_id=1").json()
    root = client.get("/")

  assert teachers["total"] == 1
  assert teachers["items"][0]["fio"] == "Иванов Иван Иванович"
  assert teachers["items"][0]["vk_profile"] == "https://vk.com/test_person"
  assert vak["total"] == 1
  assert vak["items"][0]["topic"] == "Тестовая тема"
  assert analytics["records"]["candidates"] == 2
  assert root.status_code == 200
  assert root.json()["docs"] == "/docs"


def test_api_run_download_and_cancellation(tmp_path: Path, monkeypatch) -> None:
  settings = _settings(tmp_path)

  def successful_pipeline(
    run_id: int,
    limits,
    *,
    output_path: Path,
    cancel_token: CancellationToken,
    on_stage,
    **_: object,
  ) -> Path:
    on_stage("ingest")
    cancel_token.check()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"xlsx")
    on_stage("export")
    return output_path

  monkeypatch.setattr("app.api.main.run_full_pipeline", successful_pipeline)
  with TestClient(create_app(settings)) as client:
    invalid = client.post("/api/v1/runs", json={"limits": {"vk_workers": 0}})
    assert invalid.status_code == 422
    created = client.post("/api/v1/runs", json={"limits": {"vk_enabled": False}})
    assert created.status_code == 202
    run_id = created.json()["run_id"]
    completed = _wait_for_terminal(client, run_id)
    assert completed["status"] == "success"
    assert completed["xlsx_available"] is True
    download = client.get(completed["xlsx_url"])
    assert download.status_code == 200
    assert download.content == b"xlsx"

  def cancellable_pipeline(
    run_id: int,
    limits,
    *,
    cancel_token: CancellationToken,
    on_stage,
    **_: object,
  ) -> None:
    on_stage("ingest")
    while True:
      cancel_token.wait(0.01)

  monkeypatch.setattr("app.api.main.run_full_pipeline", cancellable_pipeline)
  with TestClient(create_app(settings)) as client:
    created = client.post("/api/v1/runs", json={})
    run_id = created.json()["run_id"]
    second_start = client.post("/api/v1/runs", json={})
    assert second_start.status_code == 409
    cancelled = client.delete(f"/api/v1/runs/{run_id}")
    assert cancelled.status_code == 202
    completed = _wait_for_terminal(client, run_id)
    assert completed["status"] == "cancelled"
    assert completed["xlsx_available"] is False

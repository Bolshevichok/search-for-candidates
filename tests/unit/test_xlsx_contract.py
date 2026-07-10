"""Tests for xlsx export contract columns."""

from pathlib import Path

from app.db.repository import Repository
from app.export.xlsx import CANDIDATE_COLUMNS, export_xlsx
from openpyxl import load_workbook


def test_xlsx_has_empty_layer2_vk_columns(tmp_path):
  db = tmp_path / "state.sqlite"
  out = tmp_path / "out.xlsx"
  with Repository(db) as repo:
    repo.init_schema()
    run_id = repo.create_run()
    repo.upsert_candidate(
      {
        "candidate_id": "c_00001",
        "full_name": "Test User",
        "identity_key": None,
        "match_status": "site_no_vak",
        "needs_review": False,
        "university_id": None,
        "department_id": None,
        "degree": None,
        "disciplines": [],
        "defenses": [],
        "candidate_content_hash": "abc",
        "first_seen_run_id": run_id,
        "last_seen_run_id": run_id,
      }
    )
    export_xlsx(repo, out)

  wb = load_workbook(out)
  headers = [cell.value for cell in wb["candidates"][1]]
  for col in ("email", "phone", "vk_url", "vk_score", "vk_status"):
    assert col in headers
  row = [cell.value for cell in wb["candidates"][2]]
  email_idx = headers.index("email")
  vk_idx = headers.index("vk_url")
  assert row[email_idx] in ("", None)
  assert row[vk_idx] in ("", None)

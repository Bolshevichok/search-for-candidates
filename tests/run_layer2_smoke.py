"""Smoke test for Layer 2.

Sets up a temporary sqlite DB, inserts a test university and candidate, monkeypatches
Crawl4AI and Yandex parser to return deterministic results, runs run_layer2 and
prints the updated candidate row.

Run:
  & .\.venv\Scripts\Activate.ps1
  python -m tests.run_layer2_smoke
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.db.repository import open_repository
import app.sources.universities.layer2 as layer2


def setup_db(path: Path) -> int:
    repo = open_repository(path)
    # create a run
    run_id = repo.create_run(is_full=False)
    # upsert a university
    uni_id = repo.upsert_university(
        official_name="Test University",
        aliases=None,
        domain="example.test",
        region=None,
        accreditation_status=None,
        vk_group_id=None,
        is_pilot=False,
    )
    # upsert a candidate with minimal required fields
    candidate = {
        "candidate_id": "c_test_001",
        "full_name": "Иванов Иван Иванович",
        "identity_key": None,
        "match_status": "site_no_vak",
        "needs_review": False,
        "university_id": uni_id,
        "department_id": None,
        "post": None,
        "degree": None,
        "academic_title": None,
        "disciplines": [],
        "gen_experience": None,
        "spec_experience": None,
        "source_url": None,
        "defenses": [],
        "candidate_content_hash": "h_test",
        "first_seen_run_id": run_id,
        "last_seen_run_id": run_id,
    }
    repo.upsert_candidate(candidate)
    repo.close()
    return run_id


def monkeypatch_layer2(temp_html: str | None = None):
    # Force employees endpoint to exist
    layer2.check_employees_endpoint = lambda domain, client: True

    # Fake Crawl4AI: return provided html
    async def fake_crawl(self, url: str, full_name: str):
        return temp_html or "<html><body><div class=\"staff\">ivanov &lt;ivanov@example.test&gt; +7-900-000-00-00</div></body></html>"

    layer2.Crawl4AICrawler.crawl = fake_crawl

    # Fake Yandex parser to extract deterministic values
    def fake_parse(self, html: str, full_name: str, candidate_id: str):
        return {
            "email": "ivanov@example.test",
            "phone": "+7-900-000-00-00",
            "contact_type": "personal",
            "confidence": "high",
            "method": "fake",
        }

    layer2.YandexLLMParser.parse = fake_parse


def run_smoke():
    tmp = tempfile.NamedTemporaryFile(prefix="state_", suffix=".sqlite", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    try:
        run_id = setup_db(db_path)
        monkeypatch_layer2()

        # Run layer2
        layer2.run_layer2(db_path, run_id, request_delay_sec=0.0, workers=1)

        # Inspect DB
        repo = open_repository(db_path, init=False)
        row = repo.execute("SELECT candidate_id, email, phone, contact_type, contact_source_url FROM candidates WHERE candidate_id = ?", ("c_test_001",)).fetchone()
        print("Candidate row after layer2:")
        print(dict(row) if row else None)
        repo.close()
    finally:
        try:
            os.unlink(db_path)
        except Exception:
            pass


if __name__ == "__main__":
    run_smoke()

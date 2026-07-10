"""Tests for matcher status assignment and possible_namesakes behavior."""

from __future__ import annotations

import json

from app.matching.identity_key import build_identity_key
from app.matching.matcher import run_match
from app.matching.normalize import normalize_fio
from app.db.repository import Repository


def _seed_university(repo: Repository) -> int:
  return repo.upsert_university(
    official_name="Уральский федеральный университет",
    aliases=["УрФУ"],
    domain="urfu.ru",
    region="Свердловская область",
    accreditation_status="Действующее",
    vk_group_id=None,
    is_pilot=False,
  )


def test_site_and_vak_status(tmp_path):
  db = tmp_path / "state.sqlite"
  with Repository(db) as repo:
    repo.init_schema()
    uni_id = _seed_university(repo)
    run_id = repo.create_run()
    fio = "Иванов Алексей Петрович"
    fio_norm = normalize_fio(fio)
    repo.upsert_employee(
      {
        "university_id": uni_id,
        "fio": fio,
        "fio_normalized": fio_norm,
        "degree": "кандидат технических наук",
        "disciplines": ["Технические науки"],
        "identity_key": build_identity_key(fio_norm, uni_id, "d", 10, None),
        "source_url": "https://urfu.ru/p",
      }
    )
    repo.upsert_vak_record(
      {
        "vak_id": "v1",
        "fio": fio,
        "fio_normalized": fio_norm,
        "dissertation_type": "Кандидатская",
        "specialty": "05.13.18 - Технические науки",
        "branch": "Технические науки",
        "topic": "Тема",
        "defend_org": "Уральский федеральный университет",
        "date_defend": "2024-01-01",
        "is_pilot_branch": False,
      }
    )
    run_match(repo, run_id)
    row = repo.execute(
      "SELECT match_status FROM candidates WHERE full_name = ?",
      (fio,),
    ).fetchone()
    assert row["match_status"] == "site_and_vak"


def test_possible_namesakes_keeps_full_cards(tmp_path):
  db = tmp_path / "state.sqlite"
  with Repository(db) as repo:
    repo.init_schema()
    uni_id = _seed_university(repo)
    run_id = repo.create_run()
    fio = "Кузнецов Дмитрий Сергеевич"
    fio_norm = normalize_fio(fio)
    repo.upsert_employee(
      {
        "university_id": uni_id,
        "fio": fio,
        "fio_normalized": fio_norm,
        "degree": "кандидат наук",
        "disciplines": ["Информатика", "Программирование"],
        "department_id": "inst-1",
        "identity_key": build_identity_key(fio_norm, uni_id, "inst-1", 5, None),
        "source_url": "https://urfu.ru/p2",
      }
    )
    repo.upsert_vak_record(
      {
        "vak_id": "v2",
        "fio": fio,
        "fio_normalized": fio_norm,
        "dissertation_type": "Докторская",
        "specialty": "06.02.01 - Ветеринария",
        "branch": "Ветеринария",
        "topic": "Тема2",
        "defend_org": "Уральский федеральный университет",
        "date_defend": "2023-05-01",
        "is_pilot_branch": False,
      }
    )
    run_match(repo, run_id)
    site = repo.execute(
      "SELECT * FROM candidates WHERE match_status = 'site_no_vak'"
    ).fetchone()
    vak = repo.execute(
      "SELECT * FROM candidates WHERE match_status = 'vak_no_site'"
    ).fetchone()
    assert site is not None
    assert vak is not None
    assert site["full_name"] == "Кузнецов Дмитрий Сергеевич"
    assert site["department_id"] == "inst-1"
    assert json.loads(site["disciplines"]) == ["Информатика", "Программирование"]
    assert vak["full_name"] == "Кузнецов Дмитрий Сергеевич"
    assert json.loads(vak["defenses"])[0]["branch"] == "Ветеринария"
    assert repo.execute("SELECT COUNT(*) AS c FROM possible_namesakes").fetchone()["c"] == 1
    assert site["needs_review"] == 1
    assert vak["needs_review"] == 1

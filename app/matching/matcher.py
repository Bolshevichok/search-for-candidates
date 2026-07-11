"""Match layer1 employees with VAK records (4 statuses + possible_namesakes)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from rapidfuzz import fuzz, process

from app.db.repository import Repository
from app.matching.normalize import normalize_organization
from app.sources.vak.parser import split_specialty

_DEGREE_RANK = {
  "кандидат": 1,
  "доктор": 2,
}


def _degree_rank(value: str | None) -> int:
  if not value:
    return 0
  lowered = value.casefold()
  for key, rank in _DEGREE_RANK.items():
    if key in lowered:
      return rank
  return 0


def _candidate_content_hash(record: dict[str, Any]) -> str:
  payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _format_candidate_id(seq: int) -> str:
  return f"c_{seq:05d}"


def _org_matches(defend_org: str, official_name: str, aliases: list[str]) -> bool:
  target = normalize_organization(defend_org)
  choices = [normalize_organization(official_name), *[normalize_organization(a) for a in aliases]]
  match = process.extractOne(target, choices, scorer=fuzz.token_set_ratio)
  return bool(match and match[1] >= 80)


def _fields_conflict(site: dict[str, Any], vak: dict[str, Any]) -> str | None:
  site_rank = _degree_rank(site.get("degree"))
  vak_rank = _degree_rank(vak.get("dissertation_type"))
  if site_rank and vak_rank and site_rank < vak_rank:
    return "степень на сайте ниже, чем в ВАК-записи"
  site_disciplines = json.loads(site.get("disciplines") or "[]")
  branch = (vak.get("branch") or "").casefold()
  specialty = (vak.get("specialty") or "").casefold()
  if branch and site_disciplines:
    related = any(
      branch in d.casefold() or d.casefold() in branch or specialty in d.casefold()
      for d in site_disciplines
    )
    if not related:
      return "специальность ВАК и дисциплины сайта из не связанных областей"
  return None


def _defense_from_vak(vak: dict[str, Any]) -> dict[str, Any]:
  specialty = vak.get("specialty")
  code, name = split_specialty(specialty)
  return {
    "date": vak.get("date_defend"),
    "specialty": specialty,
    "specialty_code": code,
    "specialty_name": name,
    "branch": vak.get("branch"),
    "dissertation_type": vak.get("dissertation_type"),
    "topic": vak.get("topic"),
    "defend_org": vak.get("defend_org"),
    "council_cipher": vak.get("council_cipher"),
    "org_address": vak.get("org_address"),
    "org_phone": vak.get("org_phone"),
    "is_pilot": bool(vak.get("is_pilot_branch")),
  }


def _upsert(repo: Repository, card: dict[str, Any]) -> None:
  card["candidate_content_hash"] = _candidate_content_hash(card)
  repo.upsert_candidate(card)


def run_match(repo: Repository, run_id: int) -> None:
  repo.clear_candidates_for_run()
  employees = repo.execute("SELECT * FROM employees_raw").fetchall()
  vak_rows = repo.execute("SELECT * FROM vak_raw").fetchall()
  universities = {
    int(row["university_id"]): row
    for row in repo.execute("SELECT * FROM universities").fetchall()
  }

  vak_by_fio: dict[str, list[Any]] = {}
  for row in vak_rows:
    vak_by_fio.setdefault(row["fio_normalized"], []).append(row)

  matched_vak_ids: set[str] = set()
  seq = 1

  for emp in employees:
    uni = universities.get(int(emp["university_id"]))
    if not uni:
      continue
    aliases = json.loads(uni["aliases"] or "[]")
    matches = vak_by_fio.get(emp["fio_normalized"], [])
    chosen_vak = None
    status = "site_no_vak"
    handled = False

    for vak in matches:
      conflict = _fields_conflict(dict(emp), dict(vak))
      org_ok = _org_matches(vak["defend_org"], uni["official_name"], aliases)
      if org_ok and conflict:
        site_id = _format_candidate_id(seq)
        seq += 1
        vak_id = _format_candidate_id(seq)
        seq += 1
        _upsert(
          repo,
          {
            "candidate_id": site_id,
            "full_name": emp["fio"],
            "identity_key": emp["identity_key"],
            "match_status": "site_no_vak",
            "needs_review": False,
            "university_id": emp["university_id"],
            "department_id": emp["department_id"],
            "degree": emp["degree"],
            "disciplines": json.loads(emp["disciplines"] or "[]"),
            "defenses": [],
            "first_seen_run_id": run_id,
            "last_seen_run_id": run_id,
          },
        )
        _upsert(
          repo,
          {
            "candidate_id": vak_id,
            "full_name": vak["fio"],
            "identity_key": None,
            "match_status": "vak_no_site",
            "needs_review": False,
            "university_id": None,
            "department_id": None,
            "degree": vak["dissertation_type"],
            "disciplines": None,
            "defenses": [_defense_from_vak(dict(vak))],
            "first_seen_run_id": run_id,
            "last_seen_run_id": run_id,
          },
        )
        repo.add_possible_namesake(site_id, vak_id, conflict)
        matched_vak_ids.add(vak["vak_id"])
        handled = True
        break
      if org_ok:
        chosen_vak = vak
        status = "site_and_vak"
        break
      if chosen_vak is None:
        chosen_vak = vak
        status = "site_and_vak_probable"

    if handled:
      continue

    if chosen_vak:
      matched_vak_ids.add(chosen_vak["vak_id"])
      _upsert(
        repo,
        {
          "candidate_id": _format_candidate_id(seq),
          "full_name": emp["fio"],
          "identity_key": emp["identity_key"],
          "match_status": status,
          "needs_review": False,
          "university_id": emp["university_id"],
          "department_id": emp["department_id"],
          "degree": emp["degree"] or chosen_vak["dissertation_type"],
          "disciplines": json.loads(emp["disciplines"] or "[]"),
          "defenses": [_defense_from_vak(dict(chosen_vak))],
          "first_seen_run_id": run_id,
          "last_seen_run_id": run_id,
        },
      )
      seq += 1
    else:
      _upsert(
        repo,
        {
          "candidate_id": _format_candidate_id(seq),
          "full_name": emp["fio"],
          "identity_key": emp["identity_key"],
          "match_status": "site_no_vak",
          "needs_review": False,
          "university_id": emp["university_id"],
          "department_id": emp["department_id"],
          "degree": emp["degree"],
          "disciplines": json.loads(emp["disciplines"] or "[]"),
          "defenses": [],
          "first_seen_run_id": run_id,
          "last_seen_run_id": run_id,
        },
      )
      seq += 1

  for vak in vak_rows:
    if vak["vak_id"] in matched_vak_ids:
      continue
    _upsert(
      repo,
      {
        "candidate_id": _format_candidate_id(seq),
        "full_name": vak["fio"],
        "identity_key": None,
        "match_status": "vak_no_site",
        "needs_review": False,
        "university_id": None,
        "department_id": None,
        "degree": vak["dissertation_type"],
        "disciplines": None,
        "defenses": [_defense_from_vak(dict(vak))],
        "first_seen_run_id": run_id,
        "last_seen_run_id": run_id,
      },
    )
    seq += 1

  repo.mark_step_done(run_id, "match", None)

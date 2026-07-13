"""XLSX export: separate site and VAK sheets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.db.repository import Repository
from app.sources.vak.parser import split_specialty

SITE_STATUSES = frozenset({"site_no_vak", "site_and_vak", "site_and_vak_probable"})
VAK_STATUSES = frozenset({"vak_no_site", "site_and_vak", "site_and_vak_probable"})

SITE_COLUMNS = [
  "full_name",
  "match_status",
  "needs_review",
  "university",
  "department",
  "post",
  "degree",
  "academic_title",
  "disciplines",
  "gen_experience",
  "spec_experience",
  "email",
  "phone",
  "contact_url",
  "source_url",
  "vk_url",
  "vk_score",
  "vk_status",
]

VAK_COLUMNS = [
  "full_name",
  "match_status",
  "needs_review",
  "branch",
  "specialty_code",
  "specialty_name",
  "dissertation_type",
  "topic",
  "defense_date",
  "defend_org",
  "council_cipher",
  "org_address",
  "org_phone",
  "is_pilot",
]


def _latest_defense(defenses_json: str | None) -> dict[str, Any]:
  if not defenses_json:
    return {}
  items = json.loads(defenses_json)
  if not items:
    return {}
  return max(items, key=lambda d: d.get("date") or "")


def _defense_fields(defense: dict[str, Any]) -> dict[str, Any]:
  specialty = defense.get("specialty")
  code = defense.get("specialty_code") or ""
  name = defense.get("specialty_name") or ""
  if specialty and not code and not name:
    code, name = split_specialty(specialty)
  return {
    "branch": defense.get("branch") or "",
    "specialty_code": code,
    "specialty_name": name,
    "dissertation_type": defense.get("dissertation_type") or "",
    "topic": defense.get("topic") or "",
    "defense_date": defense.get("date") or "",
    "defend_org": defense.get("defend_org") or "",
    "council_cipher": defense.get("council_cipher") or "",
    "org_address": defense.get("org_address") or "",
    "org_phone": defense.get("org_phone") or "",
    "is_pilot": defense.get("is_pilot") if defense else "",
  }


def export_xlsx(repo: Repository, output_path: Path, domain: str | None = None) -> Path:
  output_path.parent.mkdir(parents=True, exist_ok=True)
  wb = Workbook()

  ws_site = wb.active
  ws_site.title = "site_employees"
  ws_site.append(SITE_COLUMNS)

  ws_vak = wb.create_sheet("vak_candidates")
  ws_vak.append(VAK_COLUMNS)

  universities = {
    int(row["university_id"]): row
    for row in repo.execute("SELECT * FROM universities").fetchall()
  }
  allowed_university_ids: set[int] | None = None
  if domain is not None:
    allowed_university_ids = {
      uid for uid, row in universities.items() if row["domain"] == domain
    }

  for row in repo.execute("SELECT * FROM candidates ORDER BY candidate_id").fetchall():
    if allowed_university_ids is not None and int(row["university_id"] or -1) not in allowed_university_ids:
      continue
    status = row["match_status"]
    uni = universities.get(int(row["university_id"])) if row["university_id"] else None
    disciplines = json.loads(row["disciplines"] or "[]") if row["disciplines"] else []
    defense = _latest_defense(row["defenses"])
    vak_fields = _defense_fields(defense)

    if status in SITE_STATUSES:
      ws_site.append(
        [
          row["full_name"],
          status,
          bool(row["needs_review"]),
          uni["official_name"] if uni else "",
          row["department_id"] or "",
          row["post"] or "",
          row["degree"] or "",
          row["academic_title"] or "",
          "; ".join(disciplines),
          row["gen_experience"] if row["gen_experience"] is not None else "",
          row["spec_experience"] if row["spec_experience"] is not None else "",
          row["email"] or "",
          row["phone"] or "",
          row["contact_source_url"] or "",
          row["source_url"] or "",
          row["vk_url"] or "",
          row["vk_score"] if row["vk_score"] is not None else "",
          row["vk_status"] or "",
        ]
      )

    if status in VAK_STATUSES:
      ws_vak.append(
        [
          row["full_name"],
          status,
          bool(row["needs_review"]),
          vak_fields["branch"],
          vak_fields["specialty_code"],
          vak_fields["specialty_name"],
          vak_fields["dissertation_type"],
          vak_fields["topic"],
          vak_fields["defense_date"],
          vak_fields["defend_org"],
          vak_fields["council_cipher"],
          vak_fields["org_address"],
          vak_fields["org_phone"],
          vak_fields["is_pilot"],
        ]
      )

  ws_namesakes = wb.create_sheet("possible_namesakes")
  ws_namesakes.append(
    ["site_full_name", "site_university", "vak_full_name", "vak_defend_org", "reason"]
  )
  for row in repo.execute(
    """
    SELECT pn.reason,
           sc.full_name AS site_full_name,
           sv.official_name AS site_university,
           vc.full_name AS vak_full_name,
           json_extract(vc.defenses, '$[0].defend_org') AS vak_defend_org
    FROM possible_namesakes pn
    JOIN candidates sc ON sc.candidate_id = pn.site_candidate_id
    JOIN candidates vc ON vc.candidate_id = pn.vak_candidate_id
    LEFT JOIN universities sv ON sv.university_id = sc.university_id
  """
  ).fetchall():
    ws_namesakes.append(
      [
        row["site_full_name"],
        row["site_university"] or "",
        row["vak_full_name"],
        row["vak_defend_org"] or "",
        row["reason"],
      ]
    )

  ws_errors = wb.create_sheet("university_errors")
  ws_errors.append(["university", "domain", "error_type", "last_attempt_at"])
  for row in repo.execute(
    """
    SELECT u.official_name, u.domain, ue.error_type, ue.last_attempt_at
    FROM university_errors ue
    JOIN universities u ON u.university_id = ue.university_id
    ORDER BY ue.id
  """
  ).fetchall():
    ws_errors.append(
      [row["official_name"], row["domain"] or "", row["error_type"], row["last_attempt_at"]]
    )

  ws_meta = wb.create_sheet("run_meta")
  ws_meta.append(
    [
      "run_id",
      "started_at",
      "finished_at",
      "universities_ok",
      "universities_error",
      "candidates_total",
      "site_and_vak",
      "site_and_vak_probable",
      "vak_no_site",
      "site_no_vak",
      "is_full_run",
    ]
  )
  run = repo.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
  if run:
    ok = repo.execute(
      "SELECT COUNT(*) AS c FROM universities WHERE layer1_status = 'ok'"
    ).fetchone()["c"]
    err = repo.execute(
      "SELECT COUNT(*) AS c FROM university_errors WHERE run_id = ?",
      (run["run_id"],),
    ).fetchone()["c"]
    counts = {
      row["match_status"]: row["c"]
      for row in repo.execute(
        "SELECT match_status, COUNT(*) AS c FROM candidates GROUP BY match_status"
      ).fetchall()
    }
    total = sum(counts.values())
    ws_meta.append(
      [
        run["run_id"],
        run["started_at"],
        run["finished_at"] or "",
        ok,
        err,
        total,
        counts.get("site_and_vak", 0),
        counts.get("site_and_vak_probable", 0),
        counts.get("vak_no_site", 0),
        counts.get("site_no_vak", 0),
        bool(run["is_full"]),
      ]
    )

  wb.save(output_path)
  return output_path


def default_output_path() -> Path:
  stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
  return Path("output") / f"candidates_{stamp}.xlsx"

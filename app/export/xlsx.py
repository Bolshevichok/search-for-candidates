"""XLSX export per contracts/xlsx-contract.md."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.db.repository import Repository

CANDIDATE_COLUMNS = [
  "full_name",
  "match_status",
  "needs_review",
  "university",
  "department",
  "degree",
  "disciplines",
  "defense_date",
  "dissertation_type",
  "specialty",
  "branch",
  "topic",
  "defend_org",
  "is_pilot",
  "email",
  "phone",
  "contact_type",
  "contact_url",
  "vk_url",
  "vk_score",
  "vk_status",
  "source_notes",
]

STATUS_NOTES = {
  "site_and_vak": "Совпадение ФИО и организации",
  "site_and_vak_probable": "Совпадение ФИО, организация отличается",
  "vak_no_site": "Только ВАК",
  "site_no_vak": "Только сайт вуза",
}


def _latest_defense(defenses_json: str | None) -> dict[str, Any]:
  if not defenses_json:
    return {}
  items = json.loads(defenses_json)
  if not items:
    return {}
  return max(items, key=lambda d: d.get("date") or "")


def export_xlsx(repo: Repository, output_path: Path) -> Path:
  output_path.parent.mkdir(parents=True, exist_ok=True)
  wb = Workbook()
  ws_candidates = wb.active
  ws_candidates.title = "candidates"
  ws_candidates.append(CANDIDATE_COLUMNS)

  universities = {
    int(row["university_id"]): row
    for row in repo.execute("SELECT * FROM universities").fetchall()
  }

  for row in repo.execute("SELECT * FROM candidates ORDER BY candidate_id").fetchall():
    uni = universities.get(int(row["university_id"])) if row["university_id"] else None
    defense = _latest_defense(row["defenses"])
    disciplines = json.loads(row["disciplines"] or "[]") if row["disciplines"] else []
    ws_candidates.append(
      [
        row["full_name"],
        row["match_status"],
        bool(row["needs_review"]),
        uni["official_name"] if uni else "",
        row["department_id"] or "",
        row["degree"] or "",
        "; ".join(disciplines),
        defense.get("date") or "",
        defense.get("dissertation_type") or "",
        defense.get("specialty") or "",
        defense.get("branch") or "",
        defense.get("topic") or "",
        defense.get("defend_org") or "",
        defense.get("is_pilot") if defense else "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        STATUS_NOTES.get(row["match_status"], row["match_status"]),
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

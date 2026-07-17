from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.repository import Repository
from app.export.xlsx import (
  MATCH_STATUS_LABELS,
  SITE_STATUSES,
  VAK_STATUSES,
  _best_vk_profile,
  _defense_fields,
  _degree_award_process,
  _latest_defense,
)


def _json_list(value: str | None) -> list[Any]:
  if not value:
    return []
  try:
    parsed = json.loads(value)
  except json.JSONDecodeError:
    return []
  return parsed if isinstance(parsed, list) else []


def _status_label(status: str) -> str:
  return MATCH_STATUS_LABELS.get(status, status)


def _candidate_filters(
  *,
  statuses: frozenset[str],
  query: str | None,
  domain: str | None,
  has_vk: bool | None,
) -> tuple[str, list[Any]]:
  where = ["c.match_status IN (" + ", ".join("?" for _ in statuses) + ")"]
  params: list[Any] = list(statuses)
  if query:
    where.append("lower(c.full_name) LIKE ?")
    params.append(f"%{query.casefold()}%")
  if domain:
    where.append("u.domain = ?")
    params.append(domain)
  if has_vk is True:
    where.append(
      "EXISTS (SELECT 1 FROM candidate_vk_profiles p "
      "WHERE p.candidate_id = c.candidate_id AND p.profile_url <> '')"
    )
  elif has_vk is False:
    where.append(
      "NOT EXISTS (SELECT 1 FROM candidate_vk_profiles p "
      "WHERE p.candidate_id = c.candidate_id AND p.profile_url <> '')"
    )
  return " AND ".join(where), params


def _candidate_page(
  repo: Repository,
  *,
  statuses: frozenset[str],
  query: str | None,
  domain: str | None,
  has_vk: bool | None,
  limit: int,
  offset: int,
) -> tuple[list[dict[str, Any]], int]:
  where, params = _candidate_filters(
    statuses=statuses, query=query, domain=domain, has_vk=has_vk,
  )
  base = "FROM candidates c LEFT JOIN universities u ON u.university_id = c.university_id"
  total = int(repo.execute(
    f"SELECT COUNT(*) AS c {base} WHERE {where}", params,
  ).fetchone()["c"])
  rows = repo.execute(
    f"""
    SELECT c.*, u.official_name AS university_name, u.domain AS university_domain
    {base}
    WHERE {where}
    ORDER BY c.full_name, c.candidate_id
    LIMIT ? OFFSET ?
    """,
    [*params, limit, offset],
  ).fetchall()
  return [dict(row) for row in rows], total


def list_teachers(
  repo: Repository,
  *,
  query: str | None = None,
  domain: str | None = None,
  has_vk: bool | None = None,
  limit: int = 50,
  offset: int = 0,
) -> dict[str, Any]:
  candidates, total = _candidate_page(
    repo,
    statuses=SITE_STATUSES,
    query=query,
    domain=domain,
    has_vk=has_vk,
    limit=limit,
    offset=offset,
  )
  items: list[dict[str, Any]] = []
  for row in candidates:
    vk = _best_vk_profile(repo, row["candidate_id"])
    items.append(
      {
        "candidate_id": row["candidate_id"],
        "fio": row["full_name"],
        "match_status": row["match_status"],
        "match_status_label": _status_label(row["match_status"]),
        "university": row["university_name"] or "",
        "post": row["post"] or "",
        "degree": row["degree"] or "",
        "academic_title": row["academic_title"] or "",
        "disciplines": "; ".join(str(item) for item in _json_list(row["disciplines"])),
        "spec_experience": row["spec_experience"],
        "email": row["email"] or "",
        "phone": row["phone"] or "",
        "vk_profile": vk.get("profile_url") or "",
        "vk_email": vk.get("public_email") or "",
        "vk_phone": vk.get("public_phone") or "",
        "source_url": row["source_url"] or "",
      }
    )
  return {"items": items, "total": total, "limit": limit, "offset": offset}


def list_vak_candidates(
  repo: Repository,
  *,
  query: str | None = None,
  domain: str | None = None,
  has_vk: bool | None = None,
  limit: int = 50,
  offset: int = 0,
) -> dict[str, Any]:
  candidates, total = _candidate_page(
    repo,
    statuses=VAK_STATUSES,
    query=query,
    domain=domain,
    has_vk=has_vk,
    limit=limit,
    offset=offset,
  )
  items: list[dict[str, Any]] = []
  for row in candidates:
    fields = _defense_fields(_latest_defense(row["defenses"]))
    vk = _best_vk_profile(repo, row["candidate_id"])
    items.append(
      {
        "candidate_id": row["candidate_id"],
        "fio": row["full_name"],
        "match_status": row["match_status"],
        "match_status_label": _status_label(row["match_status"]),
        "branch": fields["branch"],
        "specialty_code": fields["specialty_code"],
        "specialty_name": fields["specialty_name"],
        "dissertation_type": fields["dissertation_type"],
        "topic": fields["topic"],
        "defense_date": fields["defense_date"],
        "defend_org": fields["defend_org"],
        "council_cipher": fields["council_cipher"],
        "org_address": fields["org_address"],
        "org_phone": fields["org_phone"],
        "is_pilot": _degree_award_process(fields["is_pilot"]),
        "vk_profile": vk.get("profile_url") or "",
        "vk_email": vk.get("public_email") or "",
        "vk_phone": vk.get("public_phone") or "",
      }
    )
  return {"items": items, "total": total, "limit": limit, "offset": offset}


def candidate_detail(repo: Repository, candidate_id: str) -> dict[str, Any] | None:
  row = repo.execute(
    """
    SELECT c.*, u.official_name AS university_name, u.domain AS university_domain
    FROM candidates c
    LEFT JOIN universities u ON u.university_id = c.university_id
    WHERE c.candidate_id = ?
    """,
    (candidate_id,),
  ).fetchone()
  if row is None:
    return None
  profiles = repo.execute(
    """
    SELECT p.profile_url, p.vk_match_status, p.public_email, p.public_phone,
           p.evidence_url, p.checked_at, vc.vk_url AS community_url
    FROM candidate_vk_profiles p
    JOIN university_vk_communities vc ON vc.community_id = p.community_id
    WHERE p.candidate_id = ?
    ORDER BY p.vk_match_status, p.checked_at DESC, p.profile_url
    """,
    (candidate_id,),
  ).fetchall()
  result = dict(row)
  result["match_status_label"] = _status_label(result["match_status"])
  result["university"] = result.pop("university_name") or ""
  result["disciplines"] = _json_list(result["disciplines"])
  result["defenses"] = _json_list(result["defenses"])
  result["vk_profiles"] = [dict(profile) for profile in profiles if profile["profile_url"]]
  return result


def list_universities(repo: Repository) -> list[dict[str, Any]]:
  rows = repo.execute(
    """
    SELECT u.university_id, u.official_name, u.domain, u.region, u.layer1_status,
           COUNT(DISTINCT c.candidate_id) AS candidate_count
    FROM universities u
    LEFT JOIN candidates c ON c.university_id = u.university_id
    GROUP BY u.university_id
    ORDER BY u.official_name
    """
  ).fetchall()
  return [dict(row) for row in rows]


def list_errors(
  repo: Repository,
  *,
  run_id: int | None,
  domain: str | None,
  limit: int,
  offset: int,
) -> dict[str, Any]:
  where = ["1 = 1"]
  params: list[Any] = []
  if run_id is not None:
    where.append("ue.run_id = ?")
    params.append(run_id)
  if domain:
    where.append("u.domain = ?")
    params.append(domain)
  predicate = " AND ".join(where)
  total = int(repo.execute(
    f"""
    SELECT COUNT(*) AS c
    FROM university_errors ue
    JOIN universities u ON u.university_id = ue.university_id
    WHERE {predicate}
    """,
    params,
  ).fetchone()["c"])
  rows = repo.execute(
    f"""
    SELECT ue.id, ue.run_id, ue.error_type, ue.message, ue.last_attempt_at,
           u.official_name AS university, u.domain
    FROM university_errors ue
    JOIN universities u ON u.university_id = ue.university_id
    WHERE {predicate}
    ORDER BY ue.id DESC
    LIMIT ? OFFSET ?
    """,
    [*params, limit, offset],
  ).fetchall()
  return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _run_scope(repo: Repository, requested_run_id: int | None) -> int | None:
  if requested_run_id is not None:
    return requested_run_id
  row = repo.execute("SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
  return int(row["run_id"]) if row else None


def analytics(repo: Repository, *, domain: str | None, run_id: int | None) -> dict[str, Any]:
  selected_run_id = _run_scope(repo, run_id)
  university_where = "WHERE domain = ?" if domain else ""
  university_params: list[Any] = [domain] if domain else []
  total_universities = int(repo.execute(
    f"SELECT COUNT(*) AS c FROM universities {university_where}", university_params,
  ).fetchone()["c"])

  processed = 0
  errors = 0
  if selected_run_id is not None:
    processed_sql = (
      """
      SELECT COUNT(DISTINCT pu.university_id) AS c
      FROM processed_universities pu
      JOIN universities u ON u.university_id = pu.university_id
      WHERE pu.run_id = ?
      """ + (" AND u.domain = ?" if domain else "")
    )
    processed = int(repo.execute(
      processed_sql, [selected_run_id, *university_params],
    ).fetchone()["c"])
    error_sql = (
      """
      SELECT COUNT(*) AS c
      FROM university_errors ue
      JOIN universities u ON u.university_id = ue.university_id
      WHERE ue.run_id = ?
      """ + (" AND u.domain = ?" if domain else "")
    )
    errors = int(repo.execute(error_sql, [selected_run_id, *university_params]).fetchone()["c"])

  candidate_base = "FROM candidates c LEFT JOIN universities u ON u.university_id = c.university_id"
  candidate_where = "WHERE u.domain = ?" if domain else ""
  candidate_params = university_params
  candidate_rows = repo.execute(
    f"SELECT c.match_status, COUNT(*) AS c {candidate_base} {candidate_where} GROUP BY c.match_status",
    candidate_params,
  ).fetchall()
  by_status = {row["match_status"]: int(row["c"]) for row in candidate_rows}
  employee_count = int(repo.execute(
    "SELECT COUNT(*) AS c FROM employees_raw e "
    "JOIN universities u ON u.university_id = e.university_id "
    + ("WHERE u.domain = ?" if domain else ""),
    university_params,
  ).fetchone()["c"])
  vak_count = int(repo.execute("SELECT COUNT(*) AS c FROM vak_raw").fetchone()["c"])

  contact_rows = repo.execute(
    f"""
    SELECT
      SUM(CASE WHEN c.email IS NOT NULL AND c.email <> '' THEN 1 ELSE 0 END) AS site_email,
      SUM(CASE WHEN c.phone IS NOT NULL AND c.phone <> '' THEN 1 ELSE 0 END) AS site_phone,
      SUM(CASE WHEN c.email IS NOT NULL OR c.phone IS NOT NULL THEN 1 ELSE 0 END) AS any_site
    {candidate_base} {candidate_where}
    """,
    candidate_params,
  ).fetchone()
  vk_rows = repo.execute(
    f"""
    SELECT
      COUNT(DISTINCT CASE WHEN p.profile_url <> '' THEN p.candidate_id END) AS profiles,
      COUNT(DISTINCT CASE WHEN p.vk_match_status = 'matched' THEN p.candidate_id END) AS matched,
      COUNT(DISTINCT CASE WHEN p.vk_match_status = 'ambiguous' THEN p.candidate_id END) AS ambiguous,
      COUNT(DISTINCT CASE WHEN p.public_email IS NOT NULL AND p.public_email <> '' THEN p.candidate_id END) AS email,
      COUNT(DISTINCT CASE WHEN p.public_phone IS NOT NULL AND p.public_phone <> '' THEN p.candidate_id END) AS phone
    FROM candidate_vk_profiles p
    JOIN candidates c ON c.candidate_id = p.candidate_id
    LEFT JOIN universities u ON u.university_id = c.university_id
    {candidate_where}
    """,
    candidate_params,
  ).fetchone()
  return {
    "run_id": selected_run_id,
    "scope": {"domain": domain or None},
    "universities": {"total": total_universities, "processed": processed, "errors": errors},
    "records": {
      "employees_raw": employee_count,
      "vak_raw": vak_count,
      "candidates": sum(by_status.values()),
      "by_match_status": by_status,
    },
    "contacts": {
      "site_email": int(contact_rows["site_email"] or 0),
      "site_phone": int(contact_rows["site_phone"] or 0),
      "site_any": int(contact_rows["any_site"] or 0),
      "vk_email": int(vk_rows["email"] or 0),
      "vk_phone": int(vk_rows["phone"] or 0),
    },
    "vk": {
      "profiles": int(vk_rows["profiles"] or 0),
      "matched": int(vk_rows["matched"] or 0),
      "ambiguous": int(vk_rows["ambiguous"] or 0),
    },
  }


def run_payload(row: Any, *, output_dir: Path) -> dict[str, Any]:
  value = dict(row)
  state = value.get("status") or value.get("run_status")
  started_at = value.get("started_at")
  finished_at = value.get("finished_at")
  duration_seconds: int | None = None
  if started_at:
    try:
      end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
      duration_seconds = max(0, int((end - datetime.fromisoformat(started_at)).total_seconds()))
    except ValueError:
      duration_seconds = None
  config = value.get("config_json")
  try:
    config_value = json.loads(config) if config else None
  except json.JSONDecodeError:
    config_value = None
  output_path = Path(value["output_path"]) if value.get("output_path") else None
  output_available = bool(output_path and output_path.is_file() and state == "success")
  return {
    "run_id": value["run_id"],
    "status": state,
    "run_status": value.get("run_status"),
    "started_at": started_at,
    "finished_at": finished_at,
    "duration_seconds": duration_seconds,
    "cancel_requested_at": value.get("cancel_requested_at"),
    "error_message": value.get("error_message"),
    "config": config_value,
    "xlsx_available": output_available,
    "xlsx_url": f"/api/v1/runs/{value['run_id']}/xlsx" if output_available else None,
  }

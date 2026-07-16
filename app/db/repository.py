from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path("data/state.sqlite")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Repository:
  def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    self.db_path = Path(db_path)
    self.db_path.parent.mkdir(parents=True, exist_ok=True)
    self.conn = sqlite3.connect(self.db_path)
    self.conn.row_factory = sqlite3.Row
    self.conn.execute("PRAGMA foreign_keys = ON")
    self.conn.execute("PRAGMA journal_mode = WAL")
    self.conn.execute("PRAGMA busy_timeout = 30000")

  def close(self) -> None:
    self.conn.close()

  def __enter__(self) -> Repository:
    return self

  def __exit__(self, *args: object) -> None:
    self.close()

  def init_schema(self) -> None:
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    self.conn.executescript(ddl)
    self.conn.commit()

  def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Cursor:
    return self.conn.execute(sql, params)

  def commit(self) -> None:
    self.conn.commit()

  def create_run(self) -> int:
    cur = self.execute(
      "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
      (utc_now_iso(),),
    )
    self.commit()
    return int(cur.lastrowid)

  def finish_run(self, run_id: int, status: str = "success") -> None:
    self.execute(
      "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
      (utc_now_iso(), status, run_id),
    )
    self.commit()

  def find_resumable_run(self) -> int | None:
    row = self.execute(
      "SELECT run_id FROM runs WHERE status = 'running' AND finished_at IS NULL "
      "ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return int(row["run_id"]) if row else None

  def mark_university_processed(self, run_id: int, university_id: int) -> None:
    self.execute(
      """
      INSERT INTO processed_universities (run_id, university_id, completed_at)
      VALUES (?, ?, ?)
      ON CONFLICT(run_id, university_id) DO UPDATE SET completed_at = excluded.completed_at
      """,
      (run_id, university_id, utc_now_iso()),
    )
    self.commit()

  def get_processed_university_ids(self, run_id: int) -> set[int]:
    rows = self.execute(
      "SELECT university_id FROM processed_universities WHERE run_id = ?",
      (run_id,),
    ).fetchall()
    return {int(r["university_id"]) for r in rows}

  def upsert_university(
    self,
    *,
    official_name: str,
    aliases: list[str] | None,
    domain: str | None,
    region: str | None,
    accreditation_status: str | None,
    is_pilot: bool,
  ) -> int:
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)
    row = self.execute("SELECT university_id FROM universities WHERE domain = ?", (domain,)).fetchone()
    if row:
      university_id = int(row["university_id"])
      self.execute(
        """
        UPDATE universities SET
          official_name = ?, aliases = ?, region = ?, accreditation_status = ?, is_pilot = ?
        WHERE university_id = ?
        """,
        (
          official_name,
          aliases_json,
          region,
          accreditation_status,
          int(is_pilot),
          university_id,
        ),
      )
    else:
      cur = self.execute(
        """
        INSERT INTO universities
          (official_name, aliases, domain, region, accreditation_status, is_pilot)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
          official_name,
          aliases_json,
          domain,
          region,
          accreditation_status,
          int(is_pilot),
        ),
      )
      university_id = int(cur.lastrowid)
    self.commit()
    return university_id

  def upsert_vk_community(
    self,
    *,
    university_id: int,
    vk_group_id: str | None,
    vk_screen_name: str | None,
    vk_url: str,
    kind: str = "primary",
    verification_source_url: str | None = None,
    active: bool = True,
  ) -> int:
    row = self.execute(
      "SELECT community_id FROM university_vk_communities WHERE university_id = ? AND vk_url = ?",
      (university_id, vk_url),
    ).fetchone()
    if row:
      community_id = int(row["community_id"])
      self.execute(
        """
        UPDATE university_vk_communities
        SET vk_group_id = ?, vk_screen_name = ?, kind = ?, verification_source_url = ?, active = ?
        WHERE community_id = ?
        """,
        (vk_group_id, vk_screen_name, kind, verification_source_url, int(active), community_id),
      )
    else:
      cur = self.execute(
        """
        INSERT INTO university_vk_communities
          (university_id, vk_group_id, vk_screen_name, vk_url, kind, verification_source_url, active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (university_id, vk_group_id, vk_screen_name, vk_url, kind, verification_source_url, int(active)),
      )
      community_id = int(cur.lastrowid)
    self.commit()
    return community_id

  def upsert_candidate_vk_profile(self, record: dict[str, Any]) -> None:
    self.execute(
      """
      INSERT INTO candidate_vk_profiles (
        candidate_id, community_id, profile_url, vk_match_status,
        public_email, public_phone, evidence_url, checked_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(candidate_id, community_id) DO UPDATE SET
        profile_url = excluded.profile_url,
        vk_match_status = excluded.vk_match_status,
        public_email = excluded.public_email,
        public_phone = excluded.public_phone,
        evidence_url = excluded.evidence_url,
        checked_at = excluded.checked_at
      """,
      (
        record["candidate_id"],
        record["community_id"],
        record.get("profile_url"),
        record["vk_match_status"],
        record.get("public_email"),
        record.get("public_phone"),
        record.get("evidence_url"),
        record.get("checked_at") or utc_now_iso(),
      ),
    )
    self.commit()

  def list_universities(
    self,
    limit: int | None = None,
    domain: str | None = None,
  ) -> list[sqlite3.Row]:
    sql = "SELECT * FROM universities"
    params: list[Any] = []
    if domain is not None:
      sql += " WHERE domain = ?"
      params.append(domain)
    sql += " ORDER BY university_id"
    if limit is not None:
      sql += f" LIMIT {int(limit)}"
    return list(self.execute(sql, params).fetchall())

  def set_university_layer1_status(self, university_id: int, status: str) -> None:
    self.execute(
      "UPDATE universities SET layer1_status = ? WHERE university_id = ?",
      (status, university_id),
    )
    self.commit()

  def record_university_error(
    self,
    run_id: int,
    university_id: int,
    error_type: str,
    message: str | None = None,
  ) -> None:
    self.execute(
      """
      INSERT INTO university_errors (run_id, university_id, error_type, message, last_attempt_at)
      VALUES (?, ?, ?, ?, ?)
      """,
      (run_id, university_id, error_type, message, utc_now_iso()),
    )
    self.commit()

  def upsert_employee(self, record: dict[str, Any]) -> None:
    existing = self.execute(
      "SELECT employee_id, disciplines FROM employees_raw WHERE university_id = ? AND identity_key = ?",
      (record["university_id"], record["identity_key"]),
    ).fetchone()
    disciplines = record.get("disciplines") or []
    if existing:
      old = json.loads(existing["disciplines"] or "[]")
      merged = list(dict.fromkeys(old + disciplines))
      self.execute(
        """
        UPDATE employees_raw SET
          fio = ?, fio_normalized = ?, post = ?, degree = ?, academic_title = ?,
          department_raw = ?, department_id = ?, disciplines = ?,
          gen_experience = ?, spec_experience = ?,
          teaching_level = ?, employee_qualification = ?, prof_development = ?, teaching_op = ?,
          source_url = ?
        WHERE employee_id = ?
        """,
        (
          record["fio"],
          record["fio_normalized"],
          record.get("post"),
          record.get("degree"),
          record.get("academic_title"),
          record.get("department_raw"),
          record.get("department_id"),
          json.dumps(merged, ensure_ascii=False),
          record.get("gen_experience"),
          record.get("spec_experience"),
          record.get("teaching_level"),
          record.get("employee_qualification"),
          record.get("prof_development"),
          record.get("teaching_op"),
          record["source_url"],
          existing["employee_id"],
        ),
      )
    else:
      self.execute(
        """
        INSERT INTO employees_raw (
          university_id, fio, fio_normalized, post, degree, academic_title,
          department_raw, department_id, disciplines, gen_experience, spec_experience,
          teaching_level, employee_qualification, prof_development, teaching_op,
          identity_key, source_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
          record["university_id"],
          record["fio"],
          record["fio_normalized"],
          record.get("post"),
          record.get("degree"),
          record.get("academic_title"),
          record.get("department_raw"),
          record.get("department_id"),
          json.dumps(disciplines, ensure_ascii=False),
          record.get("gen_experience"),
          record.get("spec_experience"),
          record.get("teaching_level"),
          record.get("employee_qualification"),
          record.get("prof_development"),
          record.get("teaching_op"),
          record["identity_key"],
          record["source_url"],
        ),
      )
    self.commit()

  def upsert_vak_record(self, record: dict[str, Any]) -> None:
    self.execute(
      """
      INSERT INTO vak_raw (
        vak_id, old_id, fio, fio_normalized, dissertation_type, specialty, branch,
        topic, defend_org, council_cipher, org_address, org_phone, date_defend,
        is_pilot_branch
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(vak_id) DO UPDATE SET
        old_id = excluded.old_id,
        fio = excluded.fio,
        fio_normalized = excluded.fio_normalized,
        dissertation_type = excluded.dissertation_type,
        specialty = excluded.specialty,
        branch = excluded.branch,
        topic = excluded.topic,
        defend_org = excluded.defend_org,
        council_cipher = excluded.council_cipher,
        org_address = excluded.org_address,
        org_phone = excluded.org_phone,
        date_defend = excluded.date_defend,
        is_pilot_branch = excluded.is_pilot_branch
      """,
      (
        record["vak_id"],
        record.get("old_id"),
        record["fio"],
        record["fio_normalized"],
        record["dissertation_type"],
        record.get("specialty"),
        record.get("branch"),
        record.get("topic"),
        record["defend_org"],
        record.get("council_cipher"),
        record.get("org_address"),
        record.get("org_phone"),
        record.get("date_defend"),
        int(record["is_pilot_branch"]),
      ),
    )
    self.commit()

  def clear_candidates_for_run(self) -> None:
    self.execute("DELETE FROM possible_namesakes")
    self.execute("DELETE FROM candidate_vk_profiles")
    self.execute("DELETE FROM candidates")
    self.commit()

  def upsert_candidate(self, record: dict[str, Any]) -> None:
    self.execute(
      """
      INSERT INTO candidates (
        candidate_id, full_name, identity_key, match_status,
        university_id, department_id, post, degree, academic_title, disciplines,
        gen_experience, spec_experience, source_url, defenses,
        email, phone, contact_type, contact_source_url, candidate_content_hash,
        first_seen_run_id, last_seen_run_id
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)
      ON CONFLICT(candidate_id) DO UPDATE SET
        full_name = excluded.full_name,
        identity_key = excluded.identity_key,
        match_status = excluded.match_status,
        university_id = excluded.university_id,
        department_id = excluded.department_id,
        post = excluded.post,
        degree = excluded.degree,
        academic_title = excluded.academic_title,
        disciplines = excluded.disciplines,
        gen_experience = excluded.gen_experience,
        spec_experience = excluded.spec_experience,
        source_url = excluded.source_url,
        defenses = excluded.defenses,
        candidate_content_hash = excluded.candidate_content_hash,
        last_seen_run_id = excluded.last_seen_run_id
      """,
      (
        record["candidate_id"],
        record["full_name"],
        record.get("identity_key"),
        record["match_status"],
        record.get("university_id"),
        record.get("department_id"),
        record.get("post"),
        record.get("degree"),
        record.get("academic_title"),
        json.dumps(record.get("disciplines") or [], ensure_ascii=False) if record.get("disciplines") is not None else None,
        record.get("gen_experience"),
        record.get("spec_experience"),
        record.get("source_url"),
        json.dumps(record.get("defenses") or [], ensure_ascii=False) if record.get("defenses") is not None else None,
        record["candidate_content_hash"],
        record["first_seen_run_id"],
        record["last_seen_run_id"],
      ),
    )
    self.commit()

  def add_possible_namesake(
    self,
    site_candidate_id: str,
    vak_candidate_id: str,
    reason: str,
  ) -> None:
    self.execute(
      """
      INSERT INTO possible_namesakes (site_candidate_id, vak_candidate_id, reason)
      VALUES (?, ?, ?)
      """,
      (site_candidate_id, vak_candidate_id, reason),
    )
    self.commit()

  def count_table(self, table: str) -> int:
    row = self.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row["c"])

def open_repository(db_path: Path | str = DEFAULT_DB_PATH, init: bool = True) -> Repository:
  repo = Repository(db_path)
  if init:
    repo.init_schema()
  return repo

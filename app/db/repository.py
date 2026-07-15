"""SQLite persistence, checkpoints, and backup helpers."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path("data/state.sqlite")
DEFAULT_BACKUP_DIR = Path("data/backups")
MAX_BACKUPS = 5


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
    self._migrate_vak_raw()
    self._migrate_employees_raw()
    self._migrate_candidates()
    self._remove_vk_columns()
    self.conn.commit()

  def _migrate_employees_raw(self) -> None:
    existing = {row["name"] for row in self.execute("PRAGMA table_info(employees_raw)")}
    for col, col_type in (
      ("teaching_level", "TEXT"),
      ("employee_qualification", "TEXT"),
      ("prof_development", "TEXT"),
      ("teaching_op", "TEXT"),
    ):
      if col not in existing:
        self.execute(f"ALTER TABLE employees_raw ADD COLUMN {col} {col_type}")

  def _migrate_candidates(self) -> None:
    existing = {row["name"] for row in self.execute("PRAGMA table_info(candidates)")}
    for col, col_type in (
      ("post", "TEXT"),
      ("academic_title", "TEXT"),
      ("gen_experience", "INTEGER"),
      ("spec_experience", "INTEGER"),
      ("source_url", "TEXT"),
    ):
      if col not in existing:
        self.execute(f"ALTER TABLE candidates ADD COLUMN {col} {col_type}")

  def _remove_vk_columns(self) -> None:
    """Remove obsolete VK data from databases created by older versions."""
    for table, columns in (
      ("universities", ("vk_group_id",)),
      ("candidates", ("vk_url", "vk_score", "vk_status")),
    ):
      existing = {row["name"] for row in self.execute(f"PRAGMA table_info({table})")}
      for column in columns:
        if column in existing:
          self.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

  def _migrate_vak_raw(self) -> None:
    existing = {row["name"] for row in self.execute("PRAGMA table_info(vak_raw)")}
    for col, col_type in (
      ("council_cipher", "TEXT"),
      ("org_address", "TEXT"),
      ("org_phone", "TEXT"),
    ):
      if col not in existing:
        self.execute(f"ALTER TABLE vak_raw ADD COLUMN {col} {col_type}")

  def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Cursor:
    return self.conn.execute(sql, params)

  def commit(self) -> None:
    self.conn.commit()

  def create_run(self, is_full: bool = False) -> int:
    cur = self.execute(
      "INSERT INTO runs (started_at, status, is_full) VALUES (?, 'running', ?)",
      (utc_now_iso(), int(is_full)),
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

  def mark_step_done(
    self,
    run_id: int,
    step: str,
    university_id: int | None = None,
    *,
    university_site_hash: str | None = None,
    checkpoint_cursor: int | None = None,
  ) -> None:
    self.execute(
      """
      INSERT INTO run_steps (run_id, step, university_id, status, university_site_hash, checkpoint_cursor)
      VALUES (?, ?, ?, 'done', ?, ?)
      ON CONFLICT(run_id, step, university_id) DO UPDATE SET
        status = 'done',
        university_site_hash = COALESCE(excluded.university_site_hash, run_steps.university_site_hash),
        checkpoint_cursor = COALESCE(excluded.checkpoint_cursor, run_steps.checkpoint_cursor),
        error_message = NULL
      """,
      (run_id, step, university_id, university_site_hash, checkpoint_cursor),
    )
    self.commit()

  def mark_step_error(
    self,
    run_id: int,
    step: str,
    university_id: int | None,
    message: str,
  ) -> None:
    self.execute(
      """
      INSERT INTO run_steps (run_id, step, university_id, status, error_message)
      VALUES (?, ?, ?, 'error', ?)
      ON CONFLICT(run_id, step, university_id) DO UPDATE SET
        status = 'error',
        error_message = excluded.error_message
      """,
      (run_id, step, university_id, message),
    )
    self.commit()

  def get_done_university_ids(self, run_id: int, step: str = "layer1") -> set[int]:
    rows = self.execute(
      "SELECT university_id FROM run_steps WHERE run_id = ? AND step = ? AND status = 'done' "
      "AND university_id IS NOT NULL",
      (run_id, step),
    ).fetchall()
    return {int(r["university_id"]) for r in rows}

  def get_vak_checkpoint(self, run_id: int) -> int:
    row = self.execute(
      "SELECT checkpoint_cursor FROM run_steps WHERE run_id = ? AND step = 'vak' "
      "AND university_id IS NULL AND status = 'done' ORDER BY id DESC LIMIT 1",
      (run_id,),
    ).fetchone()
    return int(row["checkpoint_cursor"]) if row and row["checkpoint_cursor"] is not None else 0

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

  def list_universities(self, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM universities ORDER BY university_id"
    if limit is not None:
      sql += f" LIMIT {int(limit)}"
    return list(self.execute(sql).fetchall())

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
    self.execute("DELETE FROM candidates")
    self.commit()

  def upsert_candidate(self, record: dict[str, Any]) -> None:
    self.execute(
      """
      INSERT INTO candidates (
        candidate_id, full_name, identity_key, match_status, needs_review,
        university_id, department_id, post, degree, academic_title, disciplines,
        gen_experience, spec_experience, source_url, defenses,
        email, phone, contact_type, contact_source_url, candidate_content_hash,
        first_seen_run_id, last_seen_run_id
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?)
      ON CONFLICT(candidate_id) DO UPDATE SET
        full_name = excluded.full_name,
        identity_key = excluded.identity_key,
        match_status = excluded.match_status,
        needs_review = excluded.needs_review,
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
        int(record.get("needs_review", False)),
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
    self.execute(
      "UPDATE candidates SET needs_review = 1 WHERE candidate_id IN (?, ?)",
      (site_candidate_id, vak_candidate_id),
    )
    self.commit()

  def count_table(self, table: str) -> int:
    row = self.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row["c"])

  def reset_database(self) -> None:
    for table in (
      "possible_namesakes",
      "university_errors",
      "run_steps",
      "candidates",
      "employees_raw",
      "vak_raw",
      "runs",
      "universities",
    ):
      self.execute(f"DELETE FROM {table}")
    self.commit()


def open_repository(db_path: Path | str = DEFAULT_DB_PATH, init: bool = True) -> Repository:
  repo = Repository(db_path)
  if init:
    repo.init_schema()
  return repo


def backup_state(
  reason: str,
  *,
  db_path: Path | str = DEFAULT_DB_PATH,
  backup_dir: Path | str = DEFAULT_BACKUP_DIR,
  max_backups: int = MAX_BACKUPS,
) -> Path | None:
  src = Path(db_path)
  if not src.exists():
    return None
  dest_dir = Path(backup_dir)
  dest_dir.mkdir(parents=True, exist_ok=True)
  stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
  dest = dest_dir / f"state_{reason}_{stamp}.sqlite"
  shutil.copy2(src, dest)
  backups = sorted(dest_dir.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
  for old in backups[max_backups:]:
    old.unlink(missing_ok=True)
  return dest

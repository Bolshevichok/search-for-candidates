"""XLSX export: separate site and VAK sheets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

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
]

SITE_HEADERS = [
  "ФИО",
  "Где найден кандидат",
  "Требует проверки",
  "Университет",
  "Кафедра",
  "Должность",
  "Учёная степень",
  "Учёное звание",
  "Преподаваемые дисциплины",
  "Общий стаж, лет",
  "Стаж по специальности, лет",
  "Электронная почта",
  "Телефон",
  "Страница с контактами",
  "Страница источника",
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

VAK_HEADERS = [
  "ФИО",
  "Где найден кандидат",
  "Требует проверки",
  "Отрасль науки",
  "Код специальности",
  "Название специальности",
  "Тип диссертации",
  "Тема диссертации",
  "Дата защиты",
  "Организация защиты",
  "Шифр совета",
  "Адрес организации",
  "Телефон организации",
  "Порядок присуждения степени",
]

MATCH_STATUS_LABELS = {
  "site_and_vak": "Найден на сайте вуза и в ВАК",
  "site_and_vak_probable": "Вероятно найден на сайте вуза и в ВАК",
  "vak_no_site": "Найден в ВАК, не найден на сайте вуза",
  "site_no_vak": "Найден на сайте вуза, не найден в ВАК",
}

HEADER_NOTES = {
  "Где найден кандидат": (
    "Показывает, в каком источнике найдена запись: на сайте вуза, в базе ВАК или в обоих."
  ),
  "Порядок присуждения степени": (
    "Самостоятельное присуждение — степень присуждена вузом или научной организацией, "
    "которые имеют такое право. Защита через диссертационный совет ВАК — обычный порядок защиты."
  ),
}

_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
_CELL_ALIGNMENT = Alignment(vertical="top", wrap_text=True)
_MAX_COLUMN_WIDTH = 50
_MIN_COLUMN_WIDTH = 12


def _format_sheet(ws: Any) -> None:
  """Make an exported sheet readable without manual Excel adjustments."""
  ws.freeze_panes = "A2"
  ws.auto_filter.ref = ws.dimensions
  ws.row_dimensions[1].height = 32

  for cell in ws[1]:
    cell.fill = _HEADER_FILL
    cell.font = _HEADER_FONT
    cell.alignment = _HEADER_ALIGNMENT
    if cell.value in HEADER_NOTES:
      cell.comment = Comment(HEADER_NOTES[cell.value], "Поиск кандидатов")

  for row in ws.iter_rows(min_row=2):
    for cell in row:
      cell.alignment = _CELL_ALIGNMENT

  for column_cells in ws.iter_cols():
    width = max(
      (len(str(cell.value)) for cell in column_cells if cell.value is not None),
      default=0,
    )
    ws.column_dimensions[get_column_letter(column_cells[0].column)].width = min(
      max(width + 2, _MIN_COLUMN_WIDTH), _MAX_COLUMN_WIDTH
    )


def _latest_defense(defenses_json: str | None) -> dict[str, Any]:
  if not defenses_json:
    return {}
  items = json.loads(defenses_json)
  if not items:
    return {}
  return max(items, key=lambda d: d.get("date") or "")


def _match_status_label(status: str) -> str:
  return MATCH_STATUS_LABELS.get(status, status)


def _yes_no(value: Any) -> str:
  return "Да" if value else "Нет"


def _degree_award_process(value: Any) -> str:
  if value:
    return "Самостоятельное присуждение степени вузом"
  return "Защита через диссертационный совет ВАК"


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


def export_xlsx(repo: Repository, output_path: Path) -> Path:
  output_path.parent.mkdir(parents=True, exist_ok=True)
  wb = Workbook()

  ws_site = wb.active
  ws_site.title = "Сотрудники вузов"
  ws_site.append(SITE_HEADERS)

  ws_vak = wb.create_sheet("Кандидаты ВАК")
  ws_vak.append(VAK_HEADERS)

  universities = {
    int(row["university_id"]): row
    for row in repo.execute("SELECT * FROM universities").fetchall()
  }

  for row in repo.execute("SELECT * FROM candidates ORDER BY candidate_id").fetchall():
    status = row["match_status"]
    uni = universities.get(int(row["university_id"])) if row["university_id"] else None
    disciplines = json.loads(row["disciplines"] or "[]") if row["disciplines"] else []
    defense = _latest_defense(row["defenses"])
    vak_fields = _defense_fields(defense)

    if status in SITE_STATUSES:
      ws_site.append(
        [
          row["full_name"],
          _match_status_label(status),
          _yes_no(row["needs_review"]),
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
        ]
      )

    if status in VAK_STATUSES:
      ws_vak.append(
        [
          row["full_name"],
          _match_status_label(status),
          _yes_no(row["needs_review"]),
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
          _degree_award_process(vak_fields["is_pilot"]),
        ]
      )

  ws_namesakes = wb.create_sheet("Возможные тёзки")
  ws_namesakes.append(
    [
      "ФИО на сайте вуза",
      "Университет",
      "ФИО в базе ВАК",
      "Организация защиты",
      "Причина проверки",
    ]
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

  ws_errors = wb.create_sheet("Ошибки вузов")
  ws_errors.append(["Университет", "Домен", "Тип ошибки", "Последняя попытка"])
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

  ws_meta = wb.create_sheet("Сведения о выгрузке")
  ws_meta.append(
    [
      "ID запуска",
      "Начало",
      "Окончание",
      "Вузов обработано успешно",
      "Вузов с ошибками",
      "Всего кандидатов",
      "Есть на сайте и в ВАК",
      "Вероятно есть на сайте и в ВАК",
      "Есть в ВАК, нет на сайте",
      "Есть на сайте, нет в ВАК",
      "Полный запуск",
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

  for worksheet in wb.worksheets:
    _format_sheet(worksheet)

  wb.save(output_path)
  return output_path


def default_output_path() -> Path:
  stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
  return Path("output") / f"candidates_{stamp}.xlsx"

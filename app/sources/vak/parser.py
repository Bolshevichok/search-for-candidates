"""Map VAK API JSON records to vak_raw rows."""

from __future__ import annotations

from typing import Any

from app.matching.normalize import normalize_fio


def parse_vak_record(item: dict[str, Any], *, is_pilot_branch: bool) -> dict[str, Any]:
  vak_id = str(item.get("id") or item.get("uuid") or item.get("old_id"))
  fio = (item.get("fio") or "").strip()
  return {
    "vak_id": vak_id,
    "old_id": item.get("old_id"),
    "fio": fio,
    "fio_normalized": normalize_fio(fio),
    "dissertation_type": item.get("dissertation_type") or "",
    "specialty": item.get("specialty"),
    "branch": item.get("branch"),
    "topic": item.get("dissertation_name"),
    "defend_org": item.get("defend_org") or "",
    "date_defend": item.get("date_defend"),
    "is_pilot_branch": is_pilot_branch,
  }

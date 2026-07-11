"""Map VAK API JSON records to vak_raw rows."""

from __future__ import annotations

from typing import Any

from app.matching.normalize import normalize_fio


def split_specialty(value: str | None) -> tuple[str, str]:
  """Split API specialty string «код - название» into code and name."""
  if not value:
    return "", ""
  text = value.strip()
  sep = " - "
  if sep in text:
    code, name = text.split(sep, 1)
    return code.strip(), name.strip()
  return text, ""


def parse_vak_record(
  list_item: dict[str, Any],
  detail: dict[str, Any] | None,
  *,
  is_pilot_branch: bool,
) -> dict[str, Any]:
  """Merge list stub with detail card — list alone lacks specialty, org contacts, etc."""
  merged = {**list_item, **(detail or {})}
  vak_id = str(merged.get("id") or merged.get("uuid") or merged.get("old_id"))
  fio = (merged.get("fio") or "").strip()
  return {
    "vak_id": vak_id,
    "old_id": merged.get("old_id"),
    "fio": fio,
    "fio_normalized": normalize_fio(fio),
    "dissertation_type": merged.get("dissertation_type") or "",
    "specialty": merged.get("specialty"),
    "branch": merged.get("branch"),
    "topic": merged.get("dissertation_name") or merged.get("topic"),
    "defend_org": (merged.get("defend_org") or "").strip(),
    "council_cipher": merged.get("council_cipher"),
    "org_address": merged.get("org_address"),
    "org_phone": merged.get("org_phone"),
    "date_defend": merged.get("date_defend"),
    "is_pilot_branch": is_pilot_branch,
  }

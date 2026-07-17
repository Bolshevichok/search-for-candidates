from __future__ import annotations

from typing import Any

from app.matching.identity_key import (
  build_identity_key,
  experience_within_tolerance,
  merge_disciplines,
)

_SCALAR_FIELDS = (
  "post",
  "degree",
  "academic_title",
  "department_raw",
  "department_id",
  "teaching_level",
  "employee_qualification",
  "prof_development",
  "teaching_op",
  "email",
  "phone",
  "source_url",
)


def _experience_value(record: dict[str, Any]) -> int | None:
  return record.get("spec_experience") if record.get("spec_experience") is not None else record.get(
    "gen_experience"
  )


def should_merge_employees(left: dict[str, Any], right: dict[str, Any]) -> bool:
  if left["fio_normalized"] != right["fio_normalized"]:
    return False
  if left["university_id"] != right["university_id"]:
    return False
  dept_l = left.get("department_id") or ""
  dept_r = right.get("department_id") or ""
  if dept_l and dept_r and dept_l != dept_r:
    return False
  exp_l = _experience_value(left)
  exp_r = _experience_value(right)
  if exp_l is not None and exp_r is not None:
    return experience_within_tolerance(exp_l, exp_r)
  return True


def merge_employee_records(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
  """Combine two rows for the same person; prefer non-empty scalar fields."""
  primary, secondary = (left, right) if _richness(left) >= _richness(right) else (right, left)
  merged = dict(primary)
  for field in _SCALAR_FIELDS:
    if not merged.get(field) and secondary.get(field):
      merged[field] = secondary[field]
  for field in ("gen_experience", "spec_experience"):
    a = merged.get(field)
    b = secondary.get(field)
    if b is not None and (a is None or b > a):
      merged[field] = b
  merged["disciplines"] = merge_disciplines(
    merged.get("disciplines") or [],
    secondary.get("disciplines") or [],
  )
  merged["identity_key"] = build_identity_key(
    merged["fio_normalized"],
    int(merged["university_id"]),
    merged.get("department_id"),
    merged.get("gen_experience"),
    merged.get("spec_experience"),
  )
  return merged


def _richness(record: dict[str, Any]) -> int:
  score = len(record.get("disciplines") or [])
  for field in _SCALAR_FIELDS:
    if record.get(field):
      score += 1
  if record.get("gen_experience") is not None:
    score += 1
  if record.get("spec_experience") is not None:
    score += 1
  return score


def dedupe_employees(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
  merged: list[dict[str, Any]] = []
  for record in records:
    found = False
    for idx, existing in enumerate(merged):
      if should_merge_employees(existing, record):
        merged[idx] = merge_employee_records(existing, record)
        found = True
        break
    if not found:
      merged.append(dict(record))
  return merged

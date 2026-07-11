"""Build stable identity_key for employee deduplication (§4.1.1)."""

from __future__ import annotations


def format_experience(gen_experience: int | None, spec_experience: int | None) -> str:
  if gen_experience is not None:
    return str(gen_experience)
  if spec_experience is not None:
    return str(spec_experience)
  return ""


def build_identity_key(
  fio_normalized: str,
  university_id: int,
  department_id: str | None,
  gen_experience: int | None,
  spec_experience: int | None,
) -> str:
  dept = department_id or ""
  exp = format_experience(gen_experience, spec_experience)
  return f"{fio_normalized}|{university_id}|{dept}|{exp}"


def parse_identity_key(key: str) -> tuple[str, int, str, str]:
  fio, uni, dept, exp = key.rsplit("|", 3)
  return fio, int(uni), dept, exp


def experience_within_tolerance(
  left: int | None,
  right: int | None,
  *,
  tolerance: int = 2,
) -> bool:
  if left is None and right is None:
    return True
  if left is None or right is None:
    return False
  return abs(left - right) <= tolerance


def identity_keys_equivalent(
  left: str,
  right: str,
  *,
  tolerance: int = 2,
) -> bool:
  lfio, luni, ldept, lexp = parse_identity_key(left)
  rfio, runi, rdept, rexp = parse_identity_key(right)
  if lfio != rfio or luni != runi or ldept != rdept:
    return False
  if not lexp and not rexp:
    return True
  if not lexp or not rexp:
    return lexp == rexp
  return experience_within_tolerance(int(lexp), int(rexp), tolerance=tolerance)


def merge_disciplines(*groups: list[str]) -> list[str]:
  seen: set[str] = set()
  merged: list[str] = []
  for group in groups:
    for item in group:
      if item not in seen:
        seen.add(item)
        merged.append(item)
  return merged

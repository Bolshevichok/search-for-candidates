"""Load university_registry.csv into the universities table."""

from __future__ import annotations

import csv
from pathlib import Path

from app.db.repository import Repository

DEFAULT_REGISTRY_PATH = Path("data/university_registry.csv")


def _parse_aliases(raw: str | None) -> list[str]:
  if not raw:
    return []
  if "|" in raw:
    return [part.strip() for part in raw.split("|") if part.strip()]
  return [raw.strip()] if raw.strip() else []


def load_registry(
  repo: Repository,
  registry_path: Path | str = DEFAULT_REGISTRY_PATH,
) -> int:
  path = Path(registry_path)
  count = 0
  with path.open(encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
      domain = (row.get("domain") or "").strip() or None
      is_pilot = str(row.get("is_pilot", "")).strip().lower() in {"1", "true", "yes"}
      repo.upsert_university(
        official_name=row["official_name"].strip(),
        aliases=_parse_aliases(row.get("aliases")),
        domain=domain,
        region=(row.get("region") or "").strip() or None,
        accreditation_status=(row.get("accreditation_status") or "").strip() or None,
        is_pilot=is_pilot,
      )
      count += 1
  return count

from __future__ import annotations

import csv
from pathlib import Path

from app.db.repository import Repository

DEFAULT_REGISTRY_PATH = Path("data/university_registry.csv")
DEFAULT_VK_COMMUNITIES_PATH = Path("data/university_vk_communities.csv")


def _parse_aliases(raw: str | None) -> list[str]:
  if not raw:
    return []
  if "|" in raw:
    return [part.strip() for part in raw.split("|") if part.strip()]
  return [raw.strip()] if raw.strip() else []


def load_registry(
  repo: Repository,
  registry_path: Path | str = DEFAULT_REGISTRY_PATH,
  vk_communities_path: Path | str = DEFAULT_VK_COMMUNITIES_PATH,
) -> int:
  path = Path(registry_path)
  count = 0
  with path.open(encoding="utf-8-sig", newline="") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
      domain = (row.get("domain") or "").strip() or None
      is_pilot = str(row.get("is_pilot", "")).strip().lower() in {"1", "true", "yes"}
      university_id = repo.upsert_university(
        official_name=row["official_name"].strip(),
        aliases=_parse_aliases(row.get("aliases")),
        domain=domain,
        region=(row.get("region") or "").strip() or None,
        accreditation_status=(row.get("accreditation_status") or "").strip() or None,
        is_pilot=is_pilot,
      )
      vk_url = (row.get("vk_url") or "").strip()
      if vk_url:
        repo.upsert_vk_community(
          university_id=university_id,
          vk_group_id=(row.get("vk_group_id") or "").strip() or None,
          vk_screen_name=(row.get("vk_screen_name") or "").strip() or None,
          vk_url=vk_url,
          verification_source_url=None,
          kind="primary",
        )
      count += 1

  extra_path = Path(vk_communities_path)
  if extra_path.exists():
    universities = {
      row["domain"]: int(row["university_id"])
      for row in repo.execute("SELECT university_id, domain FROM universities WHERE domain IS NOT NULL").fetchall()
    }
    with extra_path.open(encoding="utf-8-sig", newline="") as fh:
      for row in csv.DictReader(fh):
        university_id = universities.get((row.get("domain") or "").strip())
        vk_url = (row.get("vk_url") or "").strip()
        if not university_id or not vk_url:
          continue
        repo.upsert_vk_community(
          university_id=university_id,
          vk_group_id=(row.get("vk_group_id") or "").strip() or None,
          vk_screen_name=(row.get("vk_screen_name") or "").strip() or None,
          vk_url=vk_url,
          kind=(row.get("kind") or "primary").strip(),
          verification_source_url=(row.get("verification_source_url") or "").strip() or None,
          active=(row.get("active") or "true").strip().lower() in {"1", "true", "yes"},
        )
  return count

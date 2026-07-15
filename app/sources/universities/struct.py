from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process
from selectolax.parser import HTMLParser

from app.sources.http_client import HttpClient

_STRUCT_PATH = "/sveden/struct"
_MIN_SCORE = 75


def _clean_text(value: str) -> str:
  return re.sub(r"\s+", " ", value).strip()


def extract_struct_subdivisions(html: str) -> list[str]:
  tree = HTMLParser(html)
  names: list[str] = []
  seen: set[str] = set()
  for node in tree.css("[itemprop]"):
    prop = node.attributes.get("itemprop", "")
    if prop in {"name", "subdivisionName", "department"}:
      text = _clean_text(node.text(separator=" "))
      if text and text not in seen:
        seen.add(text)
        names.append(text)
  if not names:
    for row in tree.css("table tr"):
      cells = [_clean_text(c.text(separator=" ")) for c in row.css("td, th")]
      cells = [c for c in cells if c]
      if cells:
        candidate = cells[0]
        if len(candidate) > 3 and candidate not in seen:
          seen.add(candidate)
          names.append(candidate)
  return names


@dataclass
class DepartmentResolver:
  client: HttpClient
  _cache: dict[tuple[int, str], str | None] = field(default_factory=dict)
  _struct_cache: dict[int, list[str]] = field(default_factory=dict)

  def fetch_struct_names(self, domain: str, university_id: int) -> list[str]:
    if university_id in self._struct_cache:
      return self._struct_cache[university_id]
    url = f"https://{domain}{_STRUCT_PATH}"
    response = self.client.get(url)
    if response.status_code != 200:
      self._struct_cache[university_id] = []
      return []
    names = extract_struct_subdivisions(response.text)
    self._struct_cache[university_id] = names
    return names

  def resolve(
    self,
    university_id: int,
    domain: str,
    department_raw: str | None,
  ) -> str | None:
    if not department_raw:
      return None
    cache_key = (university_id, department_raw)
    if cache_key in self._cache:
      return self._cache[cache_key]
    subdivisions = self.fetch_struct_names(domain, university_id)
    if not subdivisions:
      self._cache[cache_key] = None
      return None
    match = process.extractOne(
      department_raw,
      subdivisions,
      scorer=fuzz.token_set_ratio,
    )
    if match and match[1] >= _MIN_SCORE:
      self._cache[cache_key] = match[0]
      return match[0]
    self._cache[cache_key] = None
    return None

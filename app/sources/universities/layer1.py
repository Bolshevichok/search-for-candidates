"""Layer 1: /sveden/employees index and program pages → employees_raw."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from app.db.repository import Repository
from app.matching.identity_key import build_identity_key, merge_disciplines
from app.matching.normalize import normalize_fio
from app.sources.http_client import HttpClient
from app.sources.universities.struct import DepartmentResolver

_EMPLOYEES_PATH = "/sveden/employees"


def _clean_text(value: str) -> str:
  return re.sub(r"\s+", " ", value).strip()


def _parse_int(value: str | None) -> int | None:
  if not value:
    return None
  digits = re.search(r"\d+", value.replace(",", "."))
  return int(digits.group()) if digits else None


def _itemprop_text(block: Any, name: str) -> str | None:
  node = block.css_first(f'[itemprop="{name}"]')
  if not node:
    return None
  text = _clean_text(node.text(separator=" "))
  return text or None


def _itemprop_list(block: Any, name: str) -> list[str]:
  values: list[str] = []
  for node in block.css(f'[itemprop="{name}"]'):
    text = _clean_text(node.text(separator=" "))
    if text and text not in values:
      values.append(text)
  return values


def extract_program_links(html: str, base_url: str) -> list[str]:
  tree = HTMLParser(html)
  links: list[str] = []
  seen: set[str] = set()
  for node in tree.css("a[href]"):
    href = node.attributes.get("href", "")
    if not href or href.startswith("#"):
      continue
    absolute = urljoin(base_url, href)
    if absolute in seen:
      continue
    text = _clean_text(node.text(separator=" "))
  # collect links from table rows that look like program pages
    if "/sveden/" in absolute and absolute != base_url.rstrip("/"):
      seen.add(absolute)
      links.append(absolute)
    elif text.lower() in {"перейти", "подробнее", "ссылка"}:
      seen.add(absolute)
      links.append(absolute)
  return links


def parse_teaching_staff(html: str, source_url: str) -> list[dict[str, Any]]:
  tree = HTMLParser(html)
  records: list[dict[str, Any]] = []
  blocks = tree.css('[itemprop="teachingStaff"]')
  if not blocks:
    blocks = tree.css("tr")
  for block in blocks:
    fio = _itemprop_text(block, "fio")
    if not fio:
      continue
    records.append(
      {
        "fio": fio,
        "fio_normalized": normalize_fio(fio),
        "post": _itemprop_text(block, "post"),
        "degree": _itemprop_text(block, "degree"),
        "academic_title": _itemprop_text(block, "academStat"),
        "department_raw": _itemprop_text(block, "department") or _itemprop_text(block, "subdivision"),
        "disciplines": _itemprop_list(block, "teachingDiscipline"),
        "gen_experience": _parse_int(_itemprop_text(block, "genExperience")),
        "spec_experience": _parse_int(_itemprop_text(block, "specExperience")),
        "source_url": source_url,
      }
    )
  return records


def compute_site_hash(*parts: str) -> str:
  digest = hashlib.sha256()
  for part in parts:
    digest.update(part.encode("utf-8", errors="ignore"))
  return digest.hexdigest()


@dataclass
class Layer1Runner:
  repo: Repository
  client: HttpClient
  resolver: DepartmentResolver
  run_id: int

  def run(self, max_universities: int | None = None) -> None:
    done = self.repo.get_done_university_ids(self.run_id, "layer1")
    universities = self.repo.list_universities(limit=max_universities)
    for uni in universities:
      university_id = int(uni["university_id"])
      if university_id in done:
        continue
      domain = uni["domain"]
      if not domain:
        self.repo.set_university_layer1_status(university_id, "unresolved_domain")
        self.repo.record_university_error(
          self.run_id, university_id, "unresolved_domain", "No domain in registry"
        )
        self.repo.mark_step_done(self.run_id, "layer1", university_id)
        continue
      try:
        self._process_university(uni)
      except Exception as exc:  # noqa: BLE001 — per-university failure must not stop run
        self.repo.set_university_layer1_status(university_id, "unreachable")
        self.repo.record_university_error(
          self.run_id, university_id, "unreachable", str(exc)
        )
        self.repo.mark_step_error(self.run_id, "layer1", university_id, str(exc))

  def _process_university(self, uni: Any) -> None:
    university_id = int(uni["university_id"])
    domain = uni["domain"]
    index_url = f"https://{domain}{_EMPLOYEES_PATH}"
    response = self.client.get(index_url)
    if response.status_code != 200:
      self.repo.set_university_layer1_status(university_id, "unreachable")
      self.repo.record_university_error(
        self.run_id,
        university_id,
        "unreachable",
        f"HTTP {response.status_code} for {index_url}",
      )
      self.repo.mark_step_done(self.run_id, "layer1", university_id)
      return

    raw_parts = [response.text]
    program_links = extract_program_links(response.text, index_url)
    if not program_links:
      program_links = [index_url]

    aggregated: dict[str, dict[str, Any]] = {}
    for link in program_links:
      page = self.client.get(link)
      if page.status_code != 200:
        continue
      raw_parts.append(page.text)
      for record in parse_teaching_staff(page.text, link):
        dept_id = self.resolver.resolve(university_id, domain, record.get("department_raw"))
        identity_key = build_identity_key(
          record["fio_normalized"],
          university_id,
          dept_id,
          record.get("gen_experience"),
          record.get("spec_experience"),
        )
        record["university_id"] = university_id
        record["department_id"] = dept_id
        record["identity_key"] = identity_key
        existing = aggregated.get(identity_key)
        if existing:
          existing["disciplines"] = merge_disciplines(
            existing.get("disciplines") or [],
            record.get("disciplines") or [],
          )
        else:
          aggregated[identity_key] = record

    if not aggregated and response.status_code == 200:
      self.repo.set_university_layer1_status(university_id, "unexpected_structure")
      self.repo.record_university_error(
        self.run_id,
        university_id,
        "unexpected_structure",
        "No teachingStaff records parsed",
      )
    else:
      for record in aggregated.values():
        self.repo.upsert_employee(record)
      self.repo.set_university_layer1_status(university_id, "ok")

    site_hash = compute_site_hash(*raw_parts)
    self.repo.mark_step_done(
      self.run_id,
      "layer1",
      university_id,
      university_site_hash=site_hash,
    )


def run_layer1(
  repo: Repository,
  run_id: int,
  *,
  request_delay_sec: float = 1.5,
  max_universities: int | None = None,
) -> None:
  with HttpClient(request_delay_sec=request_delay_sec, timeout=15.0) as client:
    resolver = DepartmentResolver(client=client)
    runner = Layer1Runner(repo=repo, client=client, resolver=resolver, run_id=run_id)
    runner.run(max_universities=max_universities)

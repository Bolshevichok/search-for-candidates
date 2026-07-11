"""Layer 1: /sveden/employees index and program pages → employees_raw."""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from app.db.repository import Repository, open_repository
from app.matching.employee_merge import dedupe_employees
from app.matching.identity_key import build_identity_key
from app.matching.normalize import normalize_fio
from app.sources.http_client import HttpClient
from app.sources.universities.struct import DepartmentResolver

_EMPLOYEES_PATH = "/sveden/employees"
_SKIP_LINK_PARTS = (
  "/sveden/struct",
  "/sveden/edustandarts",
  "/sveden/document",
  "/sveden/grants",
  "/sveden/material",
  "/sveden/objects",
  "/sveden/catering",
  "/sveden/international",
  "/sveden/managers",
)


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


def _is_program_link(url: str, index_url: str) -> bool:
  norm = url.rstrip("/").casefold()
  if norm == index_url.rstrip("/").casefold():
    return False
  lowered = norm
  for skip in _SKIP_LINK_PARTS:
    if skip.casefold() in lowered:
      return False
  if "infopage" in lowered or "ppssp" in lowered:
    return True
  return "/sveden/" in lowered


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
    if _is_program_link(absolute, base_url):
      seen.add(absolute)
      links.append(absolute)
    elif text.lower() in {"перейти", "подробнее", "ссылка"}:
      if _is_program_link(absolute, base_url):
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
        "teaching_level": _itemprop_text(block, "teachingLevel"),
        "employee_qualification": _itemprop_text(block, "employeeQualification"),
        "prof_development": _itemprop_text(block, "profDevelopment"),
        "teaching_op": _itemprop_text(block, "teachingOp"),
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

  def process_university(self, uni: Any) -> None:
    university_id = int(uni["university_id"])
    domain = uni["domain"]
    if not domain:
      self.repo.set_university_layer1_status(university_id, "unresolved_domain")
      self.repo.record_university_error(
        self.run_id, university_id, "unresolved_domain", "No domain in registry"
      )
      self.repo.mark_step_done(self.run_id, "layer1", university_id)
      return

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
      program_links = []

    parsed: list[dict[str, Any]] = []
    for link in program_links:
      page = self.client.get(link)
      if page.status_code != 200:
        continue
      raw_parts.append(page.text)
      parsed.extend(parse_teaching_staff(page.text, link))

    if not parsed:
      for record in parse_teaching_staff(response.text, index_url):
        parsed.append(record)

    enriched: list[dict[str, Any]] = []
    for record in parsed:
      dept_id = self.resolver.resolve(university_id, domain, record.get("department_raw"))
      record["university_id"] = university_id
      record["department_id"] = dept_id
      record["identity_key"] = build_identity_key(
        record["fio_normalized"],
        university_id,
        dept_id,
        record.get("gen_experience"),
        record.get("spec_experience"),
      )
      enriched.append(record)

    aggregated = dedupe_employees(enriched)

    if not aggregated:
      self.repo.set_university_layer1_status(university_id, "unexpected_structure")
      self.repo.record_university_error(
        self.run_id,
        university_id,
        "unexpected_structure",
        "No teachingStaff records parsed",
      )
    else:
      for record in aggregated:
        self.repo.upsert_employee(record)
      self.repo.set_university_layer1_status(university_id, "ok")

    site_hash = compute_site_hash(*raw_parts)
    self.repo.mark_step_done(
      self.run_id,
      "layer1",
      university_id,
      university_site_hash=site_hash,
    )


def _run_one_university(
  db_path: Path | str,
  run_id: int,
  uni: Any,
  request_delay_sec: float,
) -> None:
  university_id = int(uni["university_id"])
  try:
    with HttpClient(request_delay_sec=request_delay_sec, timeout=15.0) as client:
      with open_repository(db_path, init=False) as repo:
        resolver = DepartmentResolver(client=client)
        runner = Layer1Runner(repo=repo, client=client, resolver=resolver, run_id=run_id)
        runner.process_university(uni)
  except Exception as exc:  # noqa: BLE001
    with open_repository(db_path, init=False) as repo:
      repo.set_university_layer1_status(university_id, "unreachable")
      repo.record_university_error(run_id, university_id, "unreachable", str(exc))
      repo.mark_step_error(run_id, "layer1", university_id, str(exc))


def run_layer1(
  db_path: Path | str,
  run_id: int,
  *,
  request_delay_sec: float = 1.5,
  max_universities: int | None = None,
  workers: int = 4,
) -> None:
  with open_repository(db_path, init=False) as repo:
    done = repo.get_done_university_ids(run_id, "layer1")
    universities = [
      uni
      for uni in repo.list_universities(limit=max_universities)
      if int(uni["university_id"]) not in done
    ]

  if not universities:
    return

  if workers <= 1 or len(universities) == 1:
    with HttpClient(request_delay_sec=request_delay_sec, timeout=15.0) as client:
      with open_repository(db_path, init=False) as repo:
        resolver = DepartmentResolver(client=client)
        runner = Layer1Runner(repo=repo, client=client, resolver=resolver, run_id=run_id)
        for uni in universities:
          university_id = int(uni["university_id"])
          if not uni["domain"]:
            repo.set_university_layer1_status(university_id, "unresolved_domain")
            repo.record_university_error(
              run_id, university_id, "unresolved_domain", "No domain in registry"
            )
            repo.mark_step_done(run_id, "layer1", university_id)
            continue
          try:
            runner.process_university(uni)
          except Exception as exc:  # noqa: BLE001
            repo.set_university_layer1_status(university_id, "unreachable")
            repo.record_university_error(run_id, university_id, "unreachable", str(exc))
            repo.mark_step_error(run_id, "layer1", university_id, str(exc))
    return

  pool_workers = min(workers, len(universities))
  with ThreadPoolExecutor(max_workers=pool_workers) as pool:
    futures = [
      pool.submit(_run_one_university, db_path, run_id, uni, request_delay_sec)
      for uni in universities
    ]
    for future in as_completed(futures):
      future.result()

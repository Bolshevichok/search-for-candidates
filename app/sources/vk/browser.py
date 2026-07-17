from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from selectolax.parser import HTMLParser

from app.db.repository import open_repository, utc_now_iso


_LOGGER = logging.getLogger(__name__)
_MOBILE_ORIGIN = "https://m.vk.com"
_MOBILE_UA = (
  "Mozilla/5.0 (Linux; Android 11; Mobile) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
)
_MEMBER_COUNT_RE = re.compile(r"(\d[\d\s\u00a0]*)\s+ПОДПИСЧИК", re.I)
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^\+?[0-9()\-\s]{7,}$")


@dataclass(frozen=True)
class VkCandidateTask:
  candidate_id: str
  full_name: str
  community_id: int
  vk_url: str


@dataclass(frozen=True)
class _MobileMember:
  profile_url: str
  profile_name: str
  page_url: str
  offset: int


@dataclass(frozen=True)
class VkProfileResult:
  candidate_id: str
  community_id: int
  profile_url: str | None
  vk_match_status: str
  evidence_url: str | None
  public_email: str | None = None
  public_phone: str | None = None

  def as_record(self) -> dict[str, Any]:
    return {
      "candidate_id": self.candidate_id,
      "community_id": self.community_id,
      "profile_url": self.profile_url or "",
      "vk_match_status": self.vk_match_status,
      "public_email": self.public_email,
      "public_phone": self.public_phone,
      "evidence_url": self.evidence_url,
      "checked_at": utc_now_iso(),
    }


def normalize_text(value: str) -> str:
  return " ".join(re.sub(r"[^а-яёa-z0-9 ]", " ", value.lower()).split())


def clean_profile_url(url: str) -> str | None:
  parts = urlsplit(url)
  if parts.scheme not in {"http", "https"} or parts.netloc.lower() not in {
    "vk.com", "www.vk.com", "m.vk.com",
  }:
    return None
  path = parts.path.rstrip("/")
  if not path or path in {"/search", "/feed", "/join", "/login"}:
    return None
  if path.startswith(("/wall", "/club", "/public", "/video", "/audio", "/groups", "/away.php")):
    return None
  return urlunsplit(("https", "vk.com", path, "", ""))


def mobile_members_url(group_url: str, offset: int = 0) -> str:
  screen_name = urlsplit(group_url).path.strip("/")
  if not screen_name:
    raise ValueError(f"VK community URL has no screen name: {group_url}")
  return f"{_MOBILE_ORIGIN}/{screen_name}?act=members&offset={offset}"


def _mobile_profile_info_url(profile_url: str) -> str:
  return f"{_MOBILE_ORIGIN}/{urlsplit(profile_url).path.strip('/')}?act=info"


def _member_name_keys(full_name: str) -> set[str]:
  parts = normalize_text(full_name).split()
  if len(parts) < 2:
    return set()
  surname, given = parts[:2]
  return {f"{surname} {given}", f"{given} {surname}"}


def parse_mobile_members(html: str, *, page_url: str, offset: int) -> tuple[int | None, list[_MobileMember]]:
  tree = HTMLParser(html)
  count_match = _MEMBER_COUNT_RE.search(tree.text(separator=" "))
  member_count = int(re.sub(r"\D", "", count_match.group(1))) if count_match else None
  members: list[_MobileMember] = []
  seen: set[str] = set()
  for anchor in tree.css("a.inline_item"):
    href = anchor.attributes.get("href", "")
    name = " ".join(anchor.text(separator=" ").split())
    if href.startswith("/"):
      href = f"https://vk.com{href}"
    profile_url = clean_profile_url(href)
    if not profile_url or not name or profile_url in seen:
      continue
    seen.add(profile_url)
    members.append(_MobileMember(profile_url, name, page_url, offset))
  return member_count, members


class VkMobileMemberSearcher:
  def __init__(self, *, request_delay_sec: float, extract_public_contacts: bool) -> None:
    self.request_delay_sec = max(0.0, request_delay_sec)
    self.extract_public_contacts = extract_public_contacts
    self._browser: Browser | None = None
    self._context: BrowserContext | None = None
    self._page: Page | None = None
    self._playwright = None

  @property
  def page(self) -> Page:
    if self._page is None:
      raise RuntimeError("VK browser is not open")
    return self._page

  @property
  def context(self) -> BrowserContext:
    if self._context is None:
      raise RuntimeError("VK browser is not open")
    return self._context

  def __enter__(self) -> "VkMobileMemberSearcher":
    self._playwright = sync_playwright().start()
    self._browser = self._playwright.chromium.launch(headless=True)
    self._context = self._browser.new_context(
      viewport={"width": 390, "height": 844},
      user_agent=_MOBILE_UA,
      locale="ru-RU",
    )
    self._page = self._context.new_page()
    return self

  def __exit__(self, *args: object) -> None:
    if self._context:
      self._context.close()
    if self._browser:
      self._browser.close()
    if self._playwright:
      self._playwright.stop()

  def _public_contacts(self, profile_url: str) -> tuple[str | None, str | None]:
    if not self.extract_public_contacts:
      return None, None
    self.page.goto(_mobile_profile_info_url(profile_url), wait_until="domcontentloaded", timeout=45_000)
    email = None
    phone = None
    mailto = self.page.locator("a[href^='mailto:']")
    tel = self.page.locator("a[href^='tel:']")
    if mailto.count():
      value = unquote((mailto.first.get_attribute("href") or "")[7:]).strip()
      if _EMAIL_RE.fullmatch(value):
        email = value
    if tel.count():
      value = unquote((tel.first.get_attribute("href") or "")[4:]).strip()
      if _PHONE_RE.fullmatch(value):
        phone = value
    return email, phone

  def search_community(self, tasks: list[VkCandidateTask]) -> list[VkProfileResult]:
    if not tasks:
      return []
    first_url = mobile_members_url(tasks[0].vk_url)
    target_ids_by_name: dict[str, set[str]] = {}
    for task in tasks:
      for key in _member_name_keys(task.full_name):
        target_ids_by_name.setdefault(key, set()).add(task.candidate_id)

    self.page.goto(first_url, wait_until="domcontentloaded", timeout=45_000)
    member_count, members = parse_mobile_members(self.page.content(), page_url=first_url, offset=0)
    if member_count is None:
      raise RuntimeError(f"Could not read public member count from {first_url}")
    pages = (member_count + 49) // 50
    found: dict[str, dict[str, _MobileMember]] = {}

    for index in range(pages):
      offset = index * 50
      page_url = mobile_members_url(tasks[0].vk_url, offset)
      if index:
        response = self.context.request.get(page_url, headers={"Referer": first_url})
        if not response.ok:
          raise RuntimeError(f"Mobile members UI returned HTTP {response.status} at offset={offset}")
        _, members = parse_mobile_members(response.text(), page_url=page_url, offset=offset)
      for member in members:
        for candidate_id in target_ids_by_name.get(normalize_text(member.profile_name), set()):
          found.setdefault(candidate_id, {})[member.profile_url] = member
      if index < pages - 1 and self.request_delay_sec:
        time.sleep(self.request_delay_sec)

    results: list[VkProfileResult] = []
    for task in tasks:
      matches = list(found.get(task.candidate_id, {}).values())
      if not matches:
        results.append(VkProfileResult(task.candidate_id, task.community_id, None, "not_found", first_url))
      elif len(matches) > 1:
        results.extend(
          VkProfileResult(
            task.candidate_id,
            task.community_id,
            member.profile_url,
            "ambiguous",
            member.page_url,
          )
          for member in matches
        )
      else:
        member = matches[0]
        email = None
        phone = None
        if self.extract_public_contacts:
          try:
            email, phone = self._public_contacts(member.profile_url)
          except Exception as exc:
            _LOGGER.info("Could not read public contacts on %s: %s", member.profile_url, type(exc).__name__)
        results.append(VkProfileResult(
          task.candidate_id,
          task.community_id,
          member.profile_url,
          "matched",
          member.page_url,
          email,
          phone,
        ))
    return results


def _load_tasks(db_path: Path, *, domain: str | None, limit: int, refresh: bool) -> list[VkCandidateTask]:
  with open_repository(db_path) as repo:
    sql = """
      SELECT c.candidate_id, c.full_name, vc.community_id, vc.vk_url
      FROM candidates c
      JOIN universities u ON u.university_id = c.university_id
      JOIN university_vk_communities vc ON vc.university_id = u.university_id AND vc.active = 1
      WHERE c.university_id IS NOT NULL
        AND c.match_status IN ('site_and_vak', 'site_and_vak_probable', 'site_no_vak', 'vak_no_site')
    """
    params: list[Any] = []
    if not refresh:
      sql += """
        AND NOT EXISTS (
          SELECT 1
          FROM candidate_vk_profiles p
          WHERE p.candidate_id = c.candidate_id AND p.community_id = vc.community_id
        )
      """
    if domain:
      sql += " AND u.domain = ?"
      params.append(domain)
    sql += " ORDER BY vc.community_id, c.candidate_id LIMIT ?"
    params.append(max(0, limit))
    rows = repo.execute(sql, params).fetchall()
  return [VkCandidateTask(row["candidate_id"], row["full_name"], int(row["community_id"]), row["vk_url"]) for row in rows]


def _search_community(
  tasks: list[VkCandidateTask], *, request_delay_sec: float, extract_public_contacts: bool,
) -> list[VkProfileResult]:
  try:
    with VkMobileMemberSearcher(
      request_delay_sec=request_delay_sec,
      extract_public_contacts=extract_public_contacts,
    ) as searcher:
      return searcher.search_community(tasks)
  except Exception as exc:
    _LOGGER.warning("VK community scan error for %s: %s", tasks[0].vk_url if tasks else "?", exc)
    return [VkProfileResult(task.candidate_id, task.community_id, None, "error", mobile_members_url(task.vk_url)) for task in tasks]


def run_vk(
  db_path: Path | str,
  run_id: int,
  *,
  request_delay_sec: float = 0.15,
  workers: int = 1,
  domain: str | None = None,
  limit: int = 100,
  extract_public_contacts: bool = False,
  refresh: bool = False,
) -> None:
  del run_id
  path = Path(db_path)
  tasks = _load_tasks(path, domain=domain, limit=limit, refresh=refresh)
  if not tasks:
    _LOGGER.info("No candidates to process for VK")
    return

  grouped: dict[int, list[VkCandidateTask]] = {}
  for task in tasks:
    grouped.setdefault(task.community_id, []).append(task)
  _LOGGER.info(
    "Indexing public VK members for %s candidates in %s community(s), %s worker(s)",
    len(tasks), len(grouped), max(1, workers),
  )
  results: list[VkProfileResult] = []
  with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
    futures = [
      executor.submit(
        _search_community,
        community_tasks,
        request_delay_sec=request_delay_sec,
        extract_public_contacts=extract_public_contacts,
      )
      for community_tasks in grouped.values()
    ]
    for future in as_completed(futures):
      results.extend(future.result())

  with open_repository(path) as repo:
    if refresh:
      for task in tasks:
        repo.execute(
          "DELETE FROM candidate_vk_profiles WHERE candidate_id = ? AND community_id = ?",
          (task.candidate_id, task.community_id),
        )
      repo.commit()
    for result in results:
      repo.upsert_candidate_vk_profile(result.as_record())
  counts: dict[str, int] = {}
  for result in results:
    counts[result.vk_match_status] = counts.get(result.vk_match_status, 0) + 1
  _LOGGER.info("VK processing completed: %s", counts)

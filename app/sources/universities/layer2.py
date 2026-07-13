"""Layer 2 contacts: strict /sveden/employees extraction + AI-assisted fallback crawler.

Input contract (per candidate with a university):
    {
        "candidate_id": "c_00123",
        "full_name": "Иванов Алексей Петрович",
        "university_domain": "urfu.ru",
        "department": "Институт радиоэлектроники и информационных технологий"
    }

Output contract (merged back into candidates by candidate_id):
    {
        "candidate_id": "c_00123",
        "crawl_status": "page_found",
        "contact_type": "personal",
        "email": "a.p.ivanov@urfu.ru",
        "phone": null,
        "source_url": "https://urfu.ru/.../staff/personov/ivanov-ap",
        "confidence": "high"
    }

Two independent paths, tried in this order:

  1. STRICT /sveden/employees path (this module, no LLM, no headless browser).
     `/sveden/employees` is a page every accredited RU university is legally
     required to publish (Приказ Рособрнадзора №1493) with a fixed schema.org
     microdata structure (itemprop="fio", "post", ...). Because the structure
     is standardized we can parse it deterministically with selectolax, the
     same way app/sources/universities/layer1.py already does, and match the
     requested candidate strictly, scoped to that person's own markup block.
     A plain HTTP GET is enough -- there is no JS rendering on this page, so
     Crawl4AI (a headless browser) is not used here at all.

  2. FALLBACK generic-site path (Crawl4AI + Yandex LLM), used only when the
     university has no working /sveden/employees page. This is best-effort,
     since site structure outside /sveden/ is not standardized.

Full mechanics: candidate-pipeline-architecture.md §6.
Stub contract: specs/001-core-pipeline-mvp/contracts/future-layer2-vk-stub-contract.md
"""

from __future__ import annotations

import asyncio
import heapq
import html as html_lib
import itertools
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from app.db.repository import Repository, open_repository
from app.matching.normalize import normalize_fio, split_fio_parts
from app.sources.http_client import HttpClient

_LOGGER = logging.getLogger(__name__)
_EMPLOYEES_PATH = "/sveden/employees"

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?:\+7|8|7)[\s\-]?\(?\d{3,4}\)?[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}(?:\s*\(\d{2,6}\))?"
    r"|\+\d{1,3}[\s\-]?\d{7,14}"
)


@dataclass
class Layer2Contract:
    """Output contract for layer 2."""
    candidate_id: str
    crawl_status: str  # 'page_found', 'page_not_found', 'page_skipped', 'error'
    contact_type: str | None = None  # 'personal', 'department', 'institute', 'none'
    full_name: str | None = None  # Person's full name found on the page
    email: str | None = None
    phone: str | None = None
    source_url: str | None = None
    confidence: str | None = None  # 'high', 'medium', 'low'
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "crawl_status": self.crawl_status,
            "full_name": self.full_name,
            "contact_type": self.contact_type,
            "email": self.email,
            "phone": self.phone,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "error_message": self.error_message,
        }


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _html_to_text(value: str) -> str:
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", value, flags=re.I)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _itemprop_text(block: Any, name: str) -> str | None:
    node = block.css_first(f'[itemprop="{name}"]')
    if not node:
        return None
    text = _clean_text(node.text(separator=" "))
    return text or None


_BLOCK_SELECTORS = ("tr", "li", "article", "dd")
_MAX_SNIPPET_CHARS = 6000


def _find_person_html_snippet(html: str, pattern: re.Pattern[str]) -> str | None:
    """Find the smallest common per-person container (table row, list item,
    ...) whose text matches `pattern`, and return its outer HTML.

    This matters because real pages routinely have several KB of
    <head>/nav/inline-scripts before any content -- a blind html[:N] budget
    can be entirely consumed by boilerplate and never reach the row/card
    that actually has this person's contact in it, even though the person
    is right there on the page (see: Гончаренко/utmn.ru regression).
    """
    tree = HTMLParser(html)
    for selector in _BLOCK_SELECTORS:
        for node in tree.css(selector):
            text = _clean_text(node.text(separator=" "))
            if not pattern.search(text):
                continue
            block_html = (node.html or "").strip()
            if block_html and len(block_html) <= _MAX_SNIPPET_CHARS:
                return block_html
    return None


def _relevant_content_for_name(html: str, full_name: str) -> str:
    """Best-effort scoped content around this person, for both the regex
    fallback and the LLM prompt: the smallest per-person block if one can
    be found, else a text window centered on the name match, else the head
    of the cleaned, tag-stripped page text."""
    target_last, target_first, target_patr = split_fio_parts(full_name)
    pattern = _build_loose_name_pattern(target_last, target_first, target_patr, strict=True)
    if pattern:
        snippet = _find_person_html_snippet(html, pattern)
        if snippet:
            return snippet
        plain_text = _html_to_text(html)
        match = pattern.search(plain_text)
        if match:
            start = max(0, match.start() - 1000)
            end = min(len(plain_text), match.end() + 1000)
            return plain_text[start:end]
    return _html_to_text(html)[:4000]


# ---------------------------------------------------------------------------
# Strict /sveden/employees path (no Crawl4AI, no LLM)
# ---------------------------------------------------------------------------


@dataclass
class EmployeeRecord:
    """One person's block on /sveden/employees, scoped so contacts can never
    leak from a neighbouring person's row/card."""

    fio: str
    fio_normalized: str
    post: str | None
    email: str | None
    phone: str | None
    source_url: str


def _scoped_contact(block: Any, itemprop_name: str, pattern: re.Pattern[str]) -> str | None:
    """Look for a contact value ONLY inside this person's own markup block.

    Preference order: explicit itemprop (rare but some universities add it),
    then a regex match restricted to this block's own text -- never the full
    page text, which is what let the old implementation pick up a contact
    that actually belonged to a different row on the page.
    """
    explicit = _itemprop_text(block, itemprop_name)
    if explicit:
        match = pattern.search(explicit)
        if match:
            return match.group()
        return explicit
    block_text = _clean_text(block.text(separator=" "))
    match = pattern.search(block_text)
    return match.group() if match else None


def parse_employees_directory(html: str, source_url: str) -> list[EmployeeRecord]:
    """Parse /sveden/employees into per-person records using the same
    itemprop microdata contract as layer1.parse_teaching_staff, scoped so
    that email/phone extraction cannot cross person boundaries.
    """
    tree = HTMLParser(html)
    blocks = tree.css('[itemprop="teachingStaff"]')
    if not blocks:
        blocks = tree.css("tr")

    records: list[EmployeeRecord] = []
    for block in blocks:
        fio = _itemprop_text(block, "fio")
        if not fio:
            continue
        records.append(
            EmployeeRecord(
                fio=fio,
                fio_normalized=normalize_fio(fio),
                post=_itemprop_text(block, "post"),
                email=_scoped_contact(block, "email", _EMAIL_RE),
                phone=_scoped_contact(block, "phone", _PHONE_RE),
                source_url=source_url,
            )
        )
    return records


def fetch_employees_directory(domain: str, client: HttpClient) -> list[EmployeeRecord] | None:
    """Fetch and parse /sveden/employees with a plain HTTP GET.

    Kept for standalone/one-off use (e.g. tests, ad-hoc scripts). The
    run_layer2 pipeline path does NOT call this -- layer1 already fetched
    and parsed this exact page for every university, so layer2 reads that
    result back from employees_raw instead (see
    `load_employees_directory_from_db`) rather than downloading it again.
    """
    url = f"https://{domain}{_EMPLOYEES_PATH}"
    try:
        response = client.get(url)
    except Exception as e:  # noqa: BLE001
        _LOGGER.debug(f"Failed to fetch {url}: {e}")
        return None
    if response.status_code != 200:
        return None
    return parse_employees_directory(response.text, url)


def check_employees_endpoint(domain: str, client: HttpClient) -> bool:
    """Check if /sveden/employees endpoint exists and is parseable."""
    return fetch_employees_directory(domain, client) is not None


def load_employees_directory_from_db(
    repo: Repository, university_id: int
) -> list[EmployeeRecord] | None:
    """Reuse layer1's already-fetched-and-parsed /sveden/employees rows for
    this university instead of downloading and re-parsing the page again.

    Returns None if layer1 never got a working /sveden/employees page for
    this university (status other than 'ok') -- that's the signal for the
    caller to fall back to the generic-site Crawl4AI+LLM path, same as when
    the old HTTP fetch returned a 404/error. An empty list means layer1's
    page was reachable and parsed but had no matching person rows -- that is
    NOT the same as "missing", so it does not trigger the fallback path.
    """
    status = repo.get_university_layer1_status(university_id)
    if status != "ok":
        return None
    rows = repo.get_employees_for_university(university_id)
    return [
        EmployeeRecord(
            fio=row["fio"],
            fio_normalized=row["fio_normalized"],
            post=row.get("post"),
            email=row.get("email"),
            phone=row.get("phone"),
            source_url=row["source_url"],
        )
        for row in rows
    ]


def _same_person(a_last: str, a_first: str, a_patr: str | None, full_name: str) -> bool:
    b_last, b_first, b_patr = split_fio_parts(full_name)
    return a_last == b_last and a_first == b_first and (a_patr or None) == (b_patr or None)


def match_employee(
    records: list[EmployeeRecord], full_name: str
) -> tuple[EmployeeRecord | None, str]:
    """Strictly match a candidate against parsed /sveden/employees records.

    Returns (record_or_None, status) where status is one of:
      'exact'      -- normalized FIO matched exactly, one record
      'initials'   -- surname + first-initial (+ patronymic-initial if the
                       candidate has one) matched exactly, one record
      'ambiguous'  -- more than one record matches at the same strictness
                       level; we refuse to guess which one is right
      'not_found'  -- no record matches

    Unlike the previous implementation, a surname-only or surname+first-
    initial-only match is never accepted as sufficient on its own when the
    candidate's full name includes a patronymic: two teachers can easily
    share a surname and first initial, and guessing wrongly means writing
    someone else's email into the database.
    """
    target_norm = normalize_fio(full_name)
    target_last, target_first, target_patr = split_fio_parts(full_name)
    if not target_last or not target_first:
        return None, "not_found"

    exact = [r for r in records if r.fio_normalized == target_norm]
    if len(exact) == 1:
        return exact[0], "exact"
    if len(exact) > 1:
        return None, "ambiguous"

    initials_matches: list[EmployeeRecord] = []
    for r in records:
        r_last, r_first, r_patr = split_fio_parts(r.fio)
        if r_last != target_last:
            continue
        if not r_first or r_first[0] != target_first[0]:
            continue
        if target_patr:
            # Candidate FIO has a patronymic: require the site record to
            # either match it by initial, or be silent about it. Two
            # different explicit patronymics for the same surname+initial
            # are two different people -- reject, do not guess.
            if r_patr and r_patr[0] != target_patr[0]:
                continue
        initials_matches.append(r)

    if len(initials_matches) == 1:
        return initials_matches[0], "initials"
    if len(initials_matches) > 1:
        return None, "ambiguous"
    return None, "not_found"


def _domain_matches(email: str, domain: str) -> bool:
    email_domain = email.rsplit("@", 1)[-1].lower()
    domain = domain.lower()
    return email_domain == domain or email_domain.endswith("." + domain)


def contract_from_employees_match(
    candidate_id: str,
    full_name: str,
    university_domain: str,
    record: EmployeeRecord | None,
    status: str,
) -> Layer2Contract:
    if status == "ambiguous":
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="page_found",
            contact_type="none",
            confidence="low",
            error_message="ambiguous_name_match_on_sveden_employees",
        )
    if status == "not_found" or record is None:
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="page_found",
            contact_type="none",
            confidence="low",
        )

    if not record.email and not record.phone:
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="page_found",
            full_name=record.fio,
            contact_type="none",
            confidence="high" if status == "exact" else "medium",
            source_url=record.source_url,
        )

    confidence = "high" if status == "exact" else "medium"
    if record.email and not _domain_matches(record.email, university_domain):
        # Contact was found scoped to the right person, but the email's
        # domain doesn't belong to the university -- keep it (could be a
        # legitimate personal/mail.ru address) but don't call it 'high'.
        confidence = "low" if confidence == "medium" else "medium"

    return Layer2Contract(
        candidate_id=candidate_id,
        crawl_status="page_found",
        full_name=record.fio,
        contact_type="personal",
        email=record.email,
        phone=record.phone,
        confidence=confidence,
        source_url=record.source_url,
    )


# ---------------------------------------------------------------------------
# Fallback path for universities WITHOUT /sveden/employees (Crawl4AI + LLM)
# ---------------------------------------------------------------------------


class YandexLLMParser:
    """Parse crawled HTML using Yandex Cloud LLM API. Used only by the
    fallback path, never for /sveden/employees."""

    def __init__(self) -> None:
        self.folder_id = os.getenv("YANDEX_FOLDER_ID", "").strip()
        self.api_key = os.getenv("YANDEX_API_KEY", "").strip()
        self.model = os.getenv("YANDEX_MODEL", "yandexgpt-lite").strip()
        self.base_url = os.getenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1").strip()
        if not self.folder_id or not self.api_key:
            _LOGGER.info("Yandex credentials not configured; using regex fallback")
        if not OPENAI_AVAILABLE:
            _LOGGER.info("openai package not installed; using regex fallback")
        self.available = bool(self.folder_id and self.api_key and self.model and OPENAI_AVAILABLE)

    def _extract_with_regex(self, html: str, full_name: str) -> dict[str, Any]:
        """Fallback: extract contacts only near the requested person name."""
        if not full_name.strip():
            return {
                "full_name": None,
                "email": None,
                "phone": None,
                "contact_type": "none",
                "confidence": "low",
                "method": "regex",
            }

        target_last, target_first, target_patr = split_fio_parts(full_name)
        pattern = _build_loose_name_pattern(target_last, target_first, target_patr, strict=True)
        if not pattern or not pattern.search(_html_to_text(html)):
            return {
                "full_name": None,
                "email": None,
                "phone": None,
                "contact_type": "none",
                "confidence": "low",
                "method": "regex",
            }

        context = _html_to_text(_relevant_content_for_name(html, full_name))

        emails = list(dict.fromkeys(_EMAIL_RE.findall(context)))
        phones = list(dict.fromkeys(_PHONE_RE.findall(context)))

        result: dict[str, Any] = {
            "full_name": full_name,
            "contact_type": "personal" if emails or phones else "none",
            "confidence": "medium" if emails or phones else "low",
            "method": "regex",
        }
        if emails:
            result["email"] = emails[0]
        if phones:
            result["phone"] = phones[0]
        return result

    async def parse(self, html: str, full_name: str, candidate_id: str) -> dict[str, Any]:
        """Parse HTML using Yandex LLM or fallback to regex."""
        if not full_name.strip():
            _LOGGER.warning("Layer2 parser called without full_name for %s", candidate_id)
            return self._extract_with_regex(html, full_name)

        if not self.available:
            return self._extract_with_regex(html, full_name)

        llm_result = await self._parse_with_yandex_llm(html, full_name)
        if llm_result:
            return llm_result

        return self._extract_with_regex(html, full_name)

    async def _parse_with_yandex_llm(self, html: str, full_name: str) -> dict[str, Any] | None:
        """Call Yandex GPT API via OpenAI AsyncOpenAI client."""
        if not self.available:
            return None

        content = _relevant_content_for_name(html, full_name)
        prompt = f"""Analyze the provided HTML content and extract contact information ONLY for this person from the database: {full_name}.
        Treat common Russian name variants as the same person when surname and initials match, for example "Иванов Иван Иванович", "Иванов Иван", "Иванов И.И.", or "Иванов И.".
        Do not extract contacts for another person with a different surname, different first-name initial, or (when a patronymic is given below) a different patronymic -- two people can share a surname and first name.
        If this person is not present in the HTML, return null values and contact_type "none".

        Extract:
        1. name as it appears in the HTML, only if it matches the requested person or a valid abbreviated variant
        2. email address next to or clearly belonging to the requested person
        3. phone number next to or clearly belonging to the requested person

        Return ONLY a JSON object with this structure (no markdown, no backticks):
        {{"full_name": "Full name or null", "email": "email@example.com or null", "phone": "+7... or null", "contact_type": "personal or none", "confidence": "high or medium or low"}}

        HTML content:
        {content}"""

        try:
            async with AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                project=self.folder_id,
            ) as client:
                completion = await client.chat.completions.create(
                    model=f"gpt://{self.folder_id}/{self.model}",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that extracts contact information from HTML. Respond with JSON only, no markdown."
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0.1,
                )

            if not getattr(completion, "choices", None):
                _LOGGER.warning("Yandex LLM did not return choices")
                return None

            message_text = getattr(completion.choices[0].message, "content", None)
            if not message_text:
                _LOGGER.warning("Yandex LLM did not return message content")
                return None

            try:
                json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', message_text, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                    found_name = parsed.get("full_name")
                    if found_name and normalize_fio(found_name) != normalize_fio(full_name):
                        target_last, target_first, target_patr = split_fio_parts(full_name)
                        found_last, found_first, found_patr = split_fio_parts(found_name)
                        mismatch = (
                            found_last != target_last
                            or not found_first
                            or found_first[0] != target_first[0]
                            or (target_patr and found_patr and found_patr[0] != target_patr[0])
                        )
                        if mismatch:
                            _LOGGER.info("Yandex LLM returned another person: %s for %s", found_name, full_name)
                            return None
                    return {
                        "full_name": found_name,
                        "email": parsed.get("email"),
                        "phone": parsed.get("phone"),
                        "contact_type": parsed.get("contact_type"),
                        "confidence": parsed.get("confidence", "medium"),
                        "method": "yandex_llm",
                    }
            except (json.JSONDecodeError, AttributeError) as exc:
                _LOGGER.warning(f"Failed to parse Yandex LLM JSON: {exc}")

        except Exception as e:
            _LOGGER.warning(f"Yandex LLM call failed: {e}")

        return None


class Crawl4AICrawler:
    """Crawl contacts using Crawl4AI. Only used by the fallback path for
    universities without a working /sveden/employees page -- never for
    /sveden/employees itself, which is static and needs no JS rendering.

    Pass a shared, already-running `AsyncWebCrawler` (`browser`) to reuse
    across many `.crawl()` calls -- launching a full browser per page, as a
    fresh `async with AsyncWebCrawler(...)` per call used to do, is by far
    the most expensive part of the fallback crawl. run_layer2 launches one
    browser for the whole run and shares it across every candidate/task;
    leave `browser=None` (the default) for standalone/one-off use (e.g. the
    manual smoke test script), which keeps the old launch-per-call behavior.
    """

    def __init__(self, browser: "AsyncWebCrawler | None" = None) -> None:
        self._browser = browser

    async def crawl(self, url: str) -> str | None:
        """Crawl a page using Crawl4AI."""
        if not CRAWL4AI_AVAILABLE:
            return None

        try:
            if self._browser is not None:
                result = await self._browser.arun(url=url, wait_for="body", timeout=15)
                return result.html if result.success else None
            browser_config = BrowserConfig(ignore_https_errors=True, verbose=False)
            async with AsyncWebCrawler(config=browser_config) as browser:
                result = await browser.arun(
                    url=url,
                    wait_for="body",
                    timeout=15,
                )
                if result.success:
                    return result.html
        except Exception as e:
            _LOGGER.warning(f"Crawl4AI error for {url}: {e}")
        return None


def _build_loose_name_pattern(
    last: str, first: str, patronymic: str | None, *, strict: bool = False
) -> re.Pattern[str] | None:
    """Build a pattern that recognizes this person's name on a page in any
    of the forms Russian university sites commonly use.

    Matching only the abbreviated "Фамилия И.О." form misses the common
    case of a fully spelled-out name -- "Гончаренко Анастасия Николаевна" --
    which real contact pages (kontakty/, o-shkole/, etc.) overwhelmingly
    use instead. This covers, in either word order:
      - full name:           Фамилия Имя Отчество / Имя Отчество Фамилия
      - full, no patronymic: Фамилия Имя / Имя Фамилия
      - abbreviated:          Фамилия И.О. / И.О. Фамилия

    `strict`: when the candidate has a patronymic, drop the
    patronymic-agnostic alternatives (surname+first-name alone). Without
    this, "Иванов Иван" matches "Иванов Иван Петрович" *and* "Иванов Иван
    Сидорович" equally -- fine for "is this person worth a closer look on
    this page" (used while walking the site), but not for deciding which
    block/page's contact actually belongs to our candidate: a page listing
    a same-surname-same-first-name colleague right next to ours would let
    their contact get attributed to us. Callers that are about to pick a
    specific contact value should pass strict=True.
    """
    if not last or not first:
        return None
    last_e = re.escape(last)
    first_e = re.escape(first)
    first_initial_e = re.escape(first[0])

    alternatives: list[str] = []
    if not (strict and patronymic):
        alternatives.append(rf"{last_e}\s+{first_e}\b")
        alternatives.append(rf"{first_e}\s+{last_e}\b")

    if patronymic:
        patr_e = re.escape(patronymic)
        patr_initial_e = re.escape(patronymic[0])
        alternatives.append(rf"{last_e}\s+{first_e}\s+{patr_e}\b")
        alternatives.append(rf"{first_e}\s+{patr_e}\s+{last_e}\b")
        alternatives.append(rf"{last_e}\s+{first_initial_e}\.?\s*{patr_initial_e}\.?(?=\W|$)")
        alternatives.append(rf"{first_initial_e}\.?\s*{patr_initial_e}\.?\s+{last_e}(?=\W|$)")
    elif strict:
        # No patronymic on either side to compare -- strict degrades to the
        # same as loose here, there's nothing more specific to require.
        alternatives.append(rf"{last_e}\s+{first_initial_e}\.?(?=\W|$)")
        alternatives.append(rf"{first_initial_e}\.?\s+{last_e}(?=\W|$)")
    else:
        alternatives.append(rf"{last_e}\s+{first_initial_e}\.?(?=\W|$)")
        alternatives.append(rf"{first_initial_e}\.?\s+{last_e}(?=\W|$)")

    pattern = r"\b(?:" + "|".join(alternatives) + r")"
    return re.compile(pattern, re.IGNORECASE)


# Site-wide crawl, not just the homepage's own links -- but still capped:
# an unbounded crawl of a real university site (thousands of pages, PDFs,
# calendars, news archives...) isn't practical to walk fully within a
# per-candidate request. This cap is generous enough to reach staff pages
# that are several clicks deep, while keeping one candidate's fallback
# lookup bounded.
_MAX_FALLBACK_PAGES = 600

_STAFF_LINK_TEXT_KEYWORDS = (
    "сотрудник", "преподавател", "кафедр", "контакт", "персонал",
    "структур", "институт", "факультет", "о нас", "команда", "коллектив",
    "деканат", "адми",
)
_STAFF_LINK_HREF_KEYWORDS = (
    "sotrudnik", "prepodavatel", "kafedra", "kontakt", "staff", "person",
    "team", "about", "struktur", "faculty", "employee", "personal", "people",
    "sveden", "contacts",
)
_SKIP_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".mp4", ".mp3", ".avi", ".css", ".js", ".woff", ".woff2", ".ttf",
)


def _domain_is_blocked(url: str, blocked_keywords: tuple[str, ...]) -> bool:
    """True if any of `blocked_keywords` occurs (case-insensitive substring
    match) in the URL's domain -- lets callers exclude whole domains from
    the fallback crawl (e.g. social networks, unrelated portals a
    university links to) without touching the crawl logic itself."""
    if not blocked_keywords:
        return False
    domain = urlparse(url).netloc.lower()
    return any(keyword.lower() in domain for keyword in blocked_keywords if keyword.strip())


def _score_internal_link(href: str, text: str) -> int:
    """Rank a link by how likely it is to be a staff/contacts page, so the
    fallback crawler tries the promising pages first within its budget
    instead of walking links in document order."""
    href_l = href.lower()
    text_l = text.lower()
    score = 0
    if any(k in text_l for k in _STAFF_LINK_TEXT_KEYWORDS):
        score += 2
    if any(k in href_l for k in _STAFF_LINK_HREF_KEYWORDS):
        score += 2
    return score


def _extract_internal_links(
    html: str, base_url: str, blocked_keywords: tuple[str, ...] = ()
) -> list[tuple[int, str]]:
    """Same-domain, crawlable links from a page as (score, url) pairs --
    called for EVERY page the crawler visits, not just the homepage, so
    links nested several pages deep are still discovered. Links whose
    domain matches `blocked_keywords` are dropped here (same check as the
    homepage/start URL in `_crawl_site_for_name`)."""
    tree = HTMLParser(html)
    domain = urlparse(base_url).netloc
    seen: set[str] = set()
    results: list[tuple[int, str]] = []
    for node in tree.css("a[href]"):
        href = node.attributes.get("href", "")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0]
        parsed = urlparse(absolute)
        if parsed.netloc != domain:
            continue
        if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
            continue
        if absolute.rstrip("/") == base_url.rstrip("/") or absolute in seen:
            continue
        if _domain_is_blocked(absolute, blocked_keywords):
            continue
        seen.add(absolute)
        text = _clean_text(node.text(separator=" "))
        results.append((_score_internal_link(absolute, text), absolute))
    return results


async def _fetch_page(
    url: str, crawler: "Crawl4AICrawler", client: HttpClient, request_delay_sec: float = 0.0
) -> str | None:
    html = await crawler.crawl(url) if CRAWL4AI_AVAILABLE else None
    if not html:
        try:
            response = await asyncio.to_thread(client.get, url)
            html = response.text if response.status_code == 200 else None
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug(f"Failed to fetch {url}: {e}")
            html = None
    if request_delay_sec:
        await asyncio.sleep(request_delay_sec)
    return html


@dataclass
class _SiteCrawlState:
    """Resumable BFS state for one university's fallback crawl. Created once
    per domain and reused across every candidate at that university (via
    `_advance_and_find`), instead of re-walking the site from scratch for
    each one -- with dozens/hundreds of candidates typically sharing a
    university, restarting a ~200-page crawl per candidate is what made a
    single-university run take hours."""

    frontier: list[tuple[int, int, str]]
    visited: set[str]
    queued: set[str]
    pages: dict[str, str]  # url -> html, every page fetched so far
    counter: "itertools.count[int]"
    exhausted: bool = False  # True once the frontier is empty / budget used up


def _new_site_crawl_state(base_url: str) -> _SiteCrawlState:
    counter = itertools.count()
    return _SiteCrawlState(
        frontier=[(0, next(counter), base_url)],
        visited=set(),
        queued={base_url.rstrip("/")},
        pages={},
        counter=counter,
    )


async def _advance_and_find(
    state: _SiteCrawlState,
    crawler: "Crawl4AICrawler",
    client: HttpClient,
    pattern: re.Pattern[str],
    blocked_domain_keywords: tuple[str, ...] = (),
    max_pages: int = _MAX_FALLBACK_PAGES,
    request_delay_sec: float = 0.0,
) -> tuple[str, str] | None:
    """Look for a page matching `pattern`, first among pages this domain's
    crawl has already fetched (`state.pages`, free -- no network calls), and
    only if that misses, keep walking the site from wherever the shared
    frontier left off (not from the start), caching every new page fetched
    into `state.pages` for the next candidate to search first. Pages are
    still tried staff/contact-likely-first via `_score_internal_link`.
    """
    for url, html in state.pages.items():
        if pattern.search(_html_to_text(html)):
            return url, html
    if state.exhausted:
        return None

    while state.frontier and len(state.visited) < max_pages:
        _neg_score, _seq, url = heapq.heappop(state.frontier)
        norm = url.rstrip("/")
        if norm in state.visited:
            continue
        state.visited.add(norm)

        html = await _fetch_page(url, crawler, client, request_delay_sec)
        if not html:
            continue
        state.pages[url] = html

        for score, link in _extract_internal_links(html, url, blocked_domain_keywords):
            link_norm = link.rstrip("/")
            if link_norm in state.visited or link_norm in state.queued:
                continue
            state.queued.add(link_norm)
            heapq.heappush(state.frontier, (-score, next(state.counter), link))

        if pattern.search(_html_to_text(html)):
            return url, html

    state.exhausted = True
    return None


async def _fallback_crawl_and_parse(
    candidate_id: str,
    full_name: str,
    university_domain: str,
    client: HttpClient,
    blocked_domain_keywords: tuple[str, ...] = (),
    site_crawl_states: dict[str, "_SiteCrawlState"] | None = None,
    crawler: "Crawl4AICrawler | None" = None,
    request_delay_sec: float = 0.0,
) -> Layer2Contract:
    """Best-effort discovery for universities that don't publish a working
    /sveden/employees page (or that do, but without contact info -- see
    `crawl_and_parse_contact`). Site structure here is not standardized, so
    this is the only place Crawl4AI/LLM are used.

    `site_crawl_states`: shared, mutable per-domain crawl progress (see
    `_SiteCrawlState`). Pass the same dict across every candidate in a run
    (run_layer2 does this) so the site is only ever crawled as far as
    needed, and never restarted for a domain already visited.

    `crawler`: a shared `Crawl4AICrawler` wrapping one already-launched
    browser (see run_layer2) -- avoids relaunching a browser per page.
    Leave `None` to have this function create its own (launch-per-call),
    fine for a single one-off lookup but wasteful across many candidates.
    """
    base_url = f"https://{university_domain}".rstrip("/")

    target_last, target_first, target_patr = split_fio_parts(full_name)
    pattern = _build_loose_name_pattern(target_last, target_first, target_patr)
    if not pattern:
        return Layer2Contract(candidate_id=candidate_id, crawl_status="page_not_found")

    if _domain_is_blocked(base_url, blocked_domain_keywords):
        return Layer2Contract(candidate_id=candidate_id, crawl_status="page_not_found")

    if site_crawl_states is None:
        site_crawl_states = {}
    state = site_crawl_states.get(university_domain)
    if state is None:
        state = _new_site_crawl_state(base_url)
        site_crawl_states[university_domain] = state

    if crawler is None:
        crawler = Crawl4AICrawler()
    parser = YandexLLMParser()

    match = await _advance_and_find(
        state, crawler, client, pattern, blocked_domain_keywords,
        request_delay_sec=request_delay_sec,
    )
    if not match:
        return Layer2Contract(candidate_id=candidate_id, crawl_status="page_not_found")
    matched_url, html = match

    try:
        parse_result = await parser.parse(html, full_name, candidate_id)
    except Exception as e:
        _LOGGER.error(f"Parse error for {candidate_id}: {e}")
        return Layer2Contract(candidate_id=candidate_id, crawl_status="error", error_message=str(e), source_url=matched_url)

    return Layer2Contract(
        candidate_id=candidate_id,
        crawl_status="page_found",
        full_name=parse_result.get("full_name") or full_name,
        email=parse_result.get("email"),
        phone=parse_result.get("phone"),
        contact_type=parse_result.get("contact_type") or "none",
        confidence=parse_result.get("confidence", "low"),
        source_url=matched_url,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


_UNFETCHED = object()  # sentinel: caller did not pass a pre-fetched directory


async def crawl_and_parse_contact(
    candidate_id: str,
    full_name: str,
    university_domain: str,
    search_term: str | None = None,
    source_url: str | None = None,
    client: HttpClient | None = None,
    employees_directory: Any = _UNFETCHED,
    blocked_domain_keywords: tuple[str, ...] = (),
    site_crawl_states: dict[str, "_SiteCrawlState"] | None = None,
    crawler: "Crawl4AICrawler | None" = None,
    request_delay_sec: float = 0.0,
) -> Layer2Contract:
    """Crawl and parse contact info for a candidate.

    `employees_directory`: pass the /sveden/employees records for this
    university (a `list[EmployeeRecord]`, or `None` if layer1 never found a
    working page) -- normally sourced from the DB via
    `load_employees_directory_from_db`, since layer1 already fetched and
    parsed this exact page for every university and re-downloading it in
    layer2 would just repeat that work. Leave the default sentinel only for
    one-off/manual calls with no DB context, which fetches over HTTP
    directly; pass `None` explicitly to force the fallback path.

    `blocked_domain_keywords`: case-insensitive substrings that, if found
    in a domain, exclude it from the fallback crawler (both the starting
    university domain and any links discovered while crawling it). Only
    applies to the fallback path -- the strict /sveden/employees path never
    leaves the university's own domain to begin with.

    `site_crawl_states`: shared, mutable per-domain fallback-crawl progress
    (see `_SiteCrawlState`). Pass the SAME dict across every candidate call
    in a batch (run_layer2 does this) so a university's site is crawled
    incrementally, resuming from where the last candidate's lookup left
    off, instead of restarting a full site walk per candidate.
    """
    if client is None:
        client = HttpClient(request_delay_sec=2.0)

    if not full_name.strip():
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="error",
            error_message="full_name is required for layer2 contact search",
        )

    directory = employees_directory
    if directory is _UNFETCHED:
        directory = fetch_employees_directory(university_domain, client)

    strict_contract: Layer2Contract | None = None
    if directory is not None:
        record, status = match_employee(directory, full_name)
        strict_contract = contract_from_employees_match(
            candidate_id, full_name, university_domain, record, status
        )
        if strict_contract.contact_type != "none":
            return strict_contract
        # /sveden/employees is a legally-mandated public roster (FIO, post,
        # degree, disciplines...) but essentially never publishes personal
        # email/phone -- so a "found the person, no contact" result here is
        # normal, not a dead end. Fall through to the generic-site crawl
        # (department/contacts pages) instead of stopping.

    fallback_contract = await _fallback_crawl_and_parse(
        candidate_id, full_name, university_domain, client,
        blocked_domain_keywords=blocked_domain_keywords,
        site_crawl_states=site_crawl_states,
        crawler=crawler,
        request_delay_sec=request_delay_sec,
    )
    if fallback_contract.contact_type != "none":
        return fallback_contract
    # Neither path found a contact -- prefer the strict-path result if we
    # have one, since it carries richer metadata (matched record, name
    # confidence) than a plain "page_not_found" from the fallback crawl.
    return strict_contract if strict_contract is not None else fallback_contract


def run_layer2(
    db_path: Path | str,
    run_id: int,
    *,
    request_delay_sec: float = 2.0,
    workers: int = 2,
    blocked_domain_keywords: list[str] | tuple[str, ...] = (),
    domain: str | None = None,
    limit: int = 100,
) -> None:
    """Run Layer 2 crawling and parsing for candidates.

    `blocked_domain_keywords`: domains matching any of these (substring,
    case-insensitive) are skipped by the fallback crawler -- e.g. social
    networks or unrelated portals a university links to that would
    otherwise waste crawl budget without ever having the candidate's
    contact info.

    `domain`: restrict to one university's domain (e.g. "utmn.ru") -- same
    convention as run_layer1/export_xlsx/run_ingest, for testing/debugging
    a single site without burning Crawl4AI/LLM calls on the rest of the DB.

    `limit`: cap on how many candidates to process this call (default 100,
    same as before); pass a smaller number for a quick smoke run.
    """
    blocked_domain_keywords = tuple(blocked_domain_keywords)
    db_path = Path(db_path)
    with open_repository(db_path) as repo:
        sql = """
            SELECT c.candidate_id, c.full_name, c.source_url, u.domain, u.university_id
            FROM candidates c
            JOIN universities u ON c.university_id = u.university_id
            WHERE c.email IS NULL
              AND c.university_id IS NOT NULL
              AND u.domain IS NOT NULL
              AND c.match_status IN ('site_and_vak', 'site_and_vak_probable', 'site_no_vak')
        """
        params: list[Any] = []
        if domain is not None:
            sql += " AND u.domain = ?"
            params.append(domain)
        sql += " LIMIT ?"
        params.append(limit)
        rows = repo.execute(sql, params)
        candidates = [
            {
                "candidate_id": row["candidate_id"],
                "full_name": row["full_name"],
                "source_url": row["source_url"],
                "domain": row["domain"],
                "university_id": int(row["university_id"]),
            }
            for row in rows
        ]

    if not candidates:
        _LOGGER.info("No candidates to process for layer2")
        return

    _LOGGER.info(f"Processing {len(candidates)} candidates with layer2 ({workers} concurrent)")

    asyncio.run(_run_layer2_candidates(
        candidates,
        db_path,
        request_delay_sec=request_delay_sec,
        workers=workers,
        blocked_domain_keywords=blocked_domain_keywords,
    ))

    _LOGGER.info("Layer 2 processing completed")


async def _run_layer2_candidates(
    candidates: list[dict[str, Any]],
    db_path: Path,
    *,
    request_delay_sec: float,
    workers: int,
    blocked_domain_keywords: tuple[str, ...],
) -> None:
    """Process every candidate concurrently, bounded by `workers` at a time,
    sharing one browser instance and one incremental per-domain site walk
    (`site_crawl_states`) across all of them. Launching a fresh browser and
    restarting the site walk per candidate -- what the old sequential loop
    did -- is what made a single university's batch take hours; this keeps
    both shared while still letting up to `workers` candidates make network
    requests at the same time.
    """
    # Read /sveden/employees once per university_id from employees_raw
    # instead of fetching it again: layer1 already downloaded and parsed
    # this exact page for every university, so layer2 reuses that result
    # rather than re-crawling it (no network call for the strict path at
    # all, cached in-process per university_id for candidates that share a
    # university).
    directory_cache: dict[int, list[EmployeeRecord] | None] = {}
    # Shared across every candidate in this batch (see `_SiteCrawlState`):
    # a university's fallback site crawl resumes for the next candidate
    # instead of restarting.
    site_crawl_states: dict[str, "_SiteCrawlState"] = {}
    semaphore = asyncio.Semaphore(max(1, workers))
    # sqlite3 writes aren't safe to interleave even across coroutines on one
    # thread (one task's commit could land mid another's); this only
    # serializes the (fast) write, not the (slow) crawl/parse work above it.
    write_lock = asyncio.Lock()

    with open_repository(db_path) as repo:
        for cand in candidates:
            if cand["university_id"] not in directory_cache:
                directory_cache[cand["university_id"]] = load_employees_directory_from_db(
                    repo, cand["university_id"]
                )

        async def process_one(cand: dict[str, Any], crawler: "Crawl4AICrawler | None", http_client: HttpClient) -> None:
            async with semaphore:
                try:
                    contract = await crawl_and_parse_contact(
                        candidate_id=cand["candidate_id"],
                        full_name=cand["full_name"],
                        university_domain=cand["domain"],
                        source_url=cand["source_url"],
                        client=http_client,
                        employees_directory=directory_cache[cand["university_id"]],
                        blocked_domain_keywords=blocked_domain_keywords,
                        site_crawl_states=site_crawl_states,
                        crawler=crawler,
                        # HttpClient itself is constructed with delay=0 below;
                        # this is the single place the delay is applied now,
                        # right after each real network fetch (see
                        # _fetch_page), instead of once per candidate.
                        request_delay_sec=request_delay_sec,
                    )
                except Exception as e:
                    _LOGGER.error(f"Error processing candidate {cand['candidate_id']}: {e}")
                    return

            async with write_lock:
                repo.execute("""
                    UPDATE candidates
                    SET email = ?, phone = ?, contact_type = ?, contact_source_url = ?
                    WHERE candidate_id = ?
                """, (
                    contract.email,
                    contract.phone,
                    contract.contact_type,
                    contract.source_url,
                    contract.candidate_id,
                ))
                repo.conn.commit()
            _LOGGER.info(f"Processed {contract.candidate_id}: {contract.crawl_status}")

        # request_delay_sec=0.0 here: HttpClient.get() has its own blocking
        # time.sleep(request_delay_sec) internally, which would double up
        # with the asyncio.sleep already applied in _fetch_page.
        with HttpClient(request_delay_sec=0.0) as http_client:
            if CRAWL4AI_AVAILABLE:
                browser_config = BrowserConfig(ignore_https_errors=True, verbose=False)
                async with AsyncWebCrawler(config=browser_config) as browser:
                    crawler = Crawl4AICrawler(browser)
                    await asyncio.gather(*(process_one(c, crawler, http_client) for c in candidates))
            else:
                await asyncio.gather(*(process_one(c, None, http_client) for c in candidates))

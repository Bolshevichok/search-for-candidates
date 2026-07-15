
from __future__ import annotations

import asyncio
import heapq
import html as html_lib
import itertools
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

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

from app.db.repository import open_repository
from app.matching.normalize import normalize_fio, split_fio_parts
from app.sources.http_client import HttpClient

_LOGGER = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?:\+7|8|7)[\s\-]?\(?\d{3,4}\)?[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}(?:\s*\(\d{2,6}\))?"
    r"|\+\d{1,3}[\s\-]?\d{7,14}"
)

_TRANSLIT_MAPS = (
    {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
     "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
     "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
     "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
     "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"},
    {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
     "ж": "j", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
     "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
     "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
     "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"},
)


def _surname_translit_variants(last_name: str) -> tuple[str, ...]:
    last = last_name.lower().strip()
    if not last:
        return ()
    variants = {
        "".join(m.get(ch, ch) for ch in last) for m in _TRANSLIT_MAPS
    }
    return tuple(v for v in variants if len(v) >= 3)


_SKIP_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".mp4", ".mp3", ".avi", ".css", ".js", ".woff", ".woff2", ".ttf",
)

_BLOCKED_URL_KEYWORDS = (
    "news", "novost", "press", "media",
    "event", "sobyti", "anons", "afisha", "calendar", "kalendar",
    "gallery", "galere", "photo", "foto", "video",
    "blog", "forum", "arhiv", "archive",
    "abitur", "priem", "admission", "postuplen",
    "login", "signin", "signup", "logout", "authoriz", "avtoriz", "register",
    "basket", "cart", "bitrix", "captcha",
    "print=", "rss", "sitemap", "sveden"
)


def _url_blocked(url: str, blocked: tuple[str, ...]) -> bool:
    low = unquote(url).lower()
    return any(word in low for word in blocked)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@dataclass
class Layer2Contract:
    """Output contract for layer 2."""
    candidate_id: str
    crawl_status: str
    contact_type: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    source_url: str | None = None
    confidence: str | None = None
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

def _html_to_text(value: str) -> str:
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", value, flags=re.I)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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


_PERSONAL_PAGE_SELECTORS = ("title", "h1", "h2", "h3")
_MAX_PERSONAL_PAGE_CHARS = 20000


def _is_personal_page(html: str, pattern: re.Pattern[str]) -> bool:
    """A page whose title/heading IS the person's name is their personal
    card -- the whole page is about them, so contacts anywhere on it
    (sidebar, table far below the header) belong to them."""
    tree = HTMLParser(html)
    for selector in _PERSONAL_PAGE_SELECTORS:
        for node in tree.css(selector):
            if pattern.search(_clean_text(node.text(separator=" "))):
                return True
    return False


def _relevant_content_for_name(html: str, full_name: str) -> str:
    """Best-effort scoped content around this person, for both the regex
    fallback and the LLM prompt: the whole page if it's the person's own
    card (name in title/h1 -- contacts can sit far from the name), else the
    smallest per-person block, else a text window centered on the name
    match, else the head of the cleaned, tag-stripped page text."""
    target_last, target_first, target_patr = split_fio_parts(full_name)
    pattern = _build_loose_name_pattern(target_last, target_first, target_patr, strict=True)
    if pattern:
        if _is_personal_page(html, pattern):
            return _html_to_text(html)[:_MAX_PERSONAL_PAGE_CHARS]
        snippet = _find_person_html_snippet(html, pattern)
        if snippet:
            return snippet
        plain_text = _html_to_text(html)
        match = pattern.search(plain_text)
        if match:
            start = max(0, match.start() - 2000)
            end = min(len(plain_text), match.end() + 2000)
            return plain_text[start:end]
    return _html_to_text(html)[:4000]



class YandexLLMParser:
    """Parse crawled HTML using Yandex Cloud LLM API, with a regex fallback
    scoped to the requested person."""

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
    """Fetch pages with Crawl4AI (JS rendering).

    Pass a shared, already-running `AsyncWebCrawler` (`browser`) to reuse
    across many `.crawl()` calls -- launching a full browser per page is by
    far the most expensive part of the crawl. run_layer2 launches one
    browser for the whole run and shares it across every candidate/task;
    leave `browser=None` (the default) for standalone/one-off use, which
    keeps the launch-per-call behavior.
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


def _name_part_regex(part: str) -> str:
    """Regex for one name part; е and ё are interchangeable because
    normalize_fio/split_fio_parts fold ё to е while real pages keep ё
    (Пётр, Фёдор, Семёнова...)."""
    return "".join(
        "[еёЕЁ]" if ch in "еёЕЁ" else re.escape(ch) for ch in part
    )


def _build_loose_name_pattern(
    last: str, first: str, patronymic: str | None, *, strict: bool = False
) -> re.Pattern[str] | None:
    """Build a pattern that recognizes this person's name on a page in any
    of the forms Russian university sites commonly use.

    Matching only the abbreviated "Фамилия И.О." form misses the common
    case of a fully spelled-out name -- "Гончаренко Анастасия Николаевна" --
    which real pages overwhelmingly use instead. This covers, in either
    word order:
      - full name:           Фамилия Имя Отчество / Имя Отчество Фамилия
      - full, no patronymic: Фамилия Имя / Имя Фамилия
      - abbreviated:          Фамилия И.О. / И.О. Фамилия

    `strict`: when the candidate has a patronymic, drop the
    patronymic-agnostic alternatives (surname+first-name alone). Without
    this, "Иванов Иван" matches "Иванов Иван Петрович" *and* "Иванов Иван
    Сидорович" equally -- fine for "is this person worth a closer look on
    this page" (used while walking the section), but not for deciding which
    block/page's contact actually belongs to our candidate. Callers that
    are about to pick a specific contact value should pass strict=True.
    """
    if not last or not first:
        return None
    last_e = _name_part_regex(last)
    first_e = _name_part_regex(first)
    first_initial_e = _name_part_regex(first[0])

    alternatives: list[str] = []
    if not (strict and patronymic):
        alternatives.append(rf"{last_e}\s+{first_e}\b")
        alternatives.append(rf"{first_e}\s+{last_e}\b")

    if patronymic:
        patr_e = _name_part_regex(patronymic)
        patr_initial_e = _name_part_regex(patronymic[0])
        alternatives.append(rf"{last_e}\s+{first_e}\s+{patr_e}\b")
        alternatives.append(rf"{first_e}\s+{patr_e}\s+{last_e}\b")
        alternatives.append(rf"{last_e}\s+{first_initial_e}\.?\s*{patr_initial_e}\.?(?=\W|$)")
        alternatives.append(rf"{first_initial_e}\.?\s*{patr_initial_e}\.?\s+{last_e}(?=\W|$)")
    elif strict:
        alternatives.append(rf"{last_e}\s+{first_initial_e}\.?(?=\W|$)")
        alternatives.append(rf"{first_initial_e}\.?\s+{last_e}(?=\W|$)")
    else:
        alternatives.append(rf"{last_e}\s+{first_initial_e}\.?(?=\W|$)")
        alternatives.append(rf"{first_initial_e}\.?\s+{last_e}(?=\W|$)")

    pattern = r"\b(?:" + "|".join(alternatives) + r")"
    return re.compile(pattern, re.IGNORECASE)


async def _fetch_page(
    url: str, crawler: "Crawl4AICrawler", client: HttpClient, request_delay_sec: float = 0.0
) -> str | None:
    html = await crawler.crawl(url) if CRAWL4AI_AVAILABLE else None
    if not html:
        try:
            response = await asyncio.to_thread(client.get, url)
            html = response.text if response.status_code == 200 else None
        except Exception as e:
            _LOGGER.debug(f"Failed to fetch {url}: {e}")
            html = None
    if request_delay_sec:
        await asyncio.sleep(request_delay_sec)
    return html


_SEED_HOST_TEMPLATES = ("{domain}", "www.{domain}")

_MAX_FETCHES_PER_CANDIDATE = 40
_MAX_FETCHES_PER_DOMAIN = 300

_STAFF_KEYWORDS = (
    "sotrudnik", "prepodavatel", "kafedra", "staff", "person", "employee",
    "teacher", "pps", "sostav", "kontakt", "contact", "people", "professor",
    "employees", "faculty",
)

_SINGLE_LETTER_RE = re.compile(r"^[А-ЯЁа-яёA-Za-z]$")
_LETTER_QUERY_RE = re.compile(r"^(?:[A-Za-z_]+=)?([А-ЯЁа-яё])$")
_PAGINATION_RE = re.compile(r"[?&](?:page|p|pagen[_\d]*)=\d+|/page/\d+", re.I)


@dataclass
class _DomainCrawlState:
    """Per-university crawl cache shared across all of its candidates:
    fetched page HTML ('' = fetch failed, don't retry) and the network-fetch
    budget counter."""

    pages: dict[str, str] = field(default_factory=dict)
    fetches: int = 0


def _norm_url(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def _same_site(url: str, domain: str) -> bool:
    netloc = urlparse(url).netloc.lower().split(":", 1)[0]
    domain = domain.lower()
    return netloc == domain or netloc.endswith("." + domain)


def _page_links(
    html: str,
    base_url: str,
    domain: str,
    blocked: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
) -> list[tuple[str, str]]:
    """(absolute_url, anchor_text) pairs for crawlable same-site links."""
    tree = HTMLParser(html)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for node in tree.css("a[href]"):
        href = node.attributes.get("href", "")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0]
        if not absolute.startswith(("http://", "https://")):
            continue
        if not _same_site(absolute, domain):
            continue
        if urlparse(absolute).path.lower().endswith(_SKIP_EXTENSIONS):
            continue
        if _url_blocked(absolute, blocked):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append((absolute, _clean_text(node.text(separator=" "))))
    return out


def _links_near_name(
    html: str,
    base_url: str,
    domain: str,
    pattern: re.Pattern[str],
    blocked: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
) -> list[str]:
    """Links inside the same per-person markup block (table row, list item,
    card) where the candidate's name appears -- on index/search pages that
    nearest link is almost always the personal page."""
    tree = HTMLParser(html)
    out: list[str] = []
    seen: set[str] = set()
    for selector in ("tr", "li", "dd", "article", "td", "p", "div"):
        for node in tree.css(selector):
            text = _clean_text(node.text(separator=" "))
            if not text or len(text) > 2000 or not pattern.search(text):
                continue
            for a in node.css("a[href]"):
                href = a.attributes.get("href", "")
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absolute = urljoin(base_url, href).split("#", 1)[0]
                if not absolute.startswith(("http://", "https://")):
                    continue
                if not _same_site(absolute, domain):
                    continue
                if urlparse(absolute).path.lower().endswith(_SKIP_EXTENSIONS):
                    continue
                if _url_blocked(absolute, blocked):
                    continue
                if absolute in seen:
                    continue
                seen.add(absolute)
                out.append(absolute)
        if out:
            break
    return out


def _alphabet_letter(url: str, anchor: str) -> str | None:
    """If this link is an alphabet-index entry ('А', 'Б', ?letter=В,
    ?%D0%90), return its letter; otherwise None."""
    if _SINGLE_LETTER_RE.match(anchor):
        return anchor
    query = unquote(urlparse(url).query).strip()
    match = _LETTER_QUERY_RE.match(query)
    if match:
        return match.group(1)
    return None


def _score_link(
    url: str,
    anchor: str,
    target_last: str,
    translit_variants: tuple[str, ...],
    name_pattern: re.Pattern[str] | None,
) -> int | None:
    """Priority score for a link, higher = crawled sooner. None = skip
    (an alphabet-index link for a different letter than the surname's)."""
    letter = _alphabet_letter(url, anchor)
    if letter is not None:
        return 90 if letter.lower() == target_last[:1].lower() else None

    score = 0
    low = unquote(url).lower()
    anchor_low = anchor.lower()
    last_low = target_last.lower()
    if name_pattern is not None and name_pattern.search(anchor):
        score += 120
    elif last_low and (last_low in anchor_low or last_low in low):
        score += 100
    if any(t in low for t in translit_variants):
        score += 80
    if any(k in low or k in anchor_low for k in _STAFF_KEYWORDS):
        score += 30
    if _PAGINATION_RE.search(low):
        score += 10
    return score


async def _smart_crawl_for_candidate(
    candidate_id: str,
    full_name: str,
    domain: str,
    state: _DomainCrawlState,
    crawler: "Crawl4AICrawler",
    client: HttpClient,
    request_delay_sec: float = 0.0,
    source_url: str | None = None,
    blocked_keywords: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
) -> Layer2Contract | None:
    """Best-first crawl of the university site for this candidate.

    Returns a contract with contacts as soon as an LLM/regex parse of a
    page mentioning the person yields an email/phone; a contact-less
    page_found contract if the person was seen but no contact surfaced;
    None if the person was never found."""
    target_last, target_first, target_patr = split_fio_parts(full_name)
    if not target_last or not target_first:
        return None
    loose_pattern = _build_loose_name_pattern(target_last, target_first, target_patr)
    if loose_pattern is None:
        return None
    translit = _surname_translit_variants(target_last)

    counter = itertools.count()
    frontier: list[tuple[int, int, str]] = []
    queued: set[str] = set()
    visited: set[str] = set()

    def push(url: str, score: int) -> None:
        norm = _norm_url(url)
        if norm in visited or norm in queued:
            return
        queued.add(norm)
        heapq.heappush(frontier, (-score, next(counter), url))

    if source_url and _same_site(source_url, domain) and not _url_blocked(source_url, blocked_keywords):
        push(source_url, 70)
    for template in _SEED_HOST_TEMPLATES:
        push(f"https://{template.format(domain=domain)}/", 15)

    parser = YandexLLMParser()
    best_no_contact: Layer2Contract | None = None
    fetched = 0

    while frontier and fetched < _MAX_FETCHES_PER_CANDIDATE:
        _, _, url = heapq.heappop(frontier)
        norm = _norm_url(url)
        if norm in visited:
            continue
        visited.add(norm)

        html = state.pages.get(norm)
        if html is None:
            if state.fetches >= _MAX_FETCHES_PER_DOMAIN:
                break
            state.fetches += 1
            fetched += 1
            html = await _fetch_page(url, crawler, client, request_delay_sec) or ""
            state.pages[norm] = html
        if not html:
            continue

        page_text = _html_to_text(html)
        name_on_page = bool(loose_pattern.search(page_text))

        if name_on_page:
            for near in _links_near_name(html, url, domain, loose_pattern, blocked_keywords):
                push(near, 200)
            try:
                parse_result = await parser.parse(html, full_name, candidate_id)
            except Exception as e:
                _LOGGER.warning(f"Layer2 parse error for {candidate_id} on {url}: {e}")
                parse_result = None
            if parse_result and (parse_result.get("email") or parse_result.get("phone")):
                return Layer2Contract(
                    candidate_id=candidate_id,
                    crawl_status="page_found",
                    full_name=parse_result.get("full_name") or full_name,
                    email=parse_result.get("email"),
                    phone=parse_result.get("phone"),
                    contact_type=parse_result.get("contact_type") or "personal",
                    confidence=parse_result.get("confidence", "medium"),
                    source_url=url,
                )
            if best_no_contact is None:
                best_no_contact = Layer2Contract(
                    candidate_id=candidate_id,
                    crawl_status="page_found",
                    full_name=full_name,
                    contact_type="none",
                    confidence="low",
                    source_url=url,
                )

        for link, anchor in _page_links(html, url, domain, blocked_keywords):
            score = _score_link(link, anchor, target_last, translit, loose_pattern)
            if score is None:
                continue
            push(link, score)

    return best_no_contact



async def crawl_and_parse_contact(
    candidate_id: str,
    full_name: str,
    university_domain: str,
    search_term: str | None = None,
    source_url: str | None = None,
    client: HttpClient | None = None,
    crawl_states: dict[str, "_DomainCrawlState"] | None = None,
    crawler: "Crawl4AICrawler | None" = None,
    request_delay_sec: float = 0.0,
    blocked_keywords: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
) -> Layer2Contract:
    """Crawl and parse contact info for a candidate.

    `crawl_states`: shared, mutable per-domain crawl cache (see
    `_DomainCrawlState`). Pass the SAME dict across every candidate call in
    a batch (run_layer2 does this) so pages fetched for one candidate are
    reused for the next instead of being re-downloaded.
    """
    if client is None:
        client = HttpClient(request_delay_sec=2.0)

    if not full_name.strip():
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="error",
            error_message="full_name is required for layer2 contact search",
        )

    if crawl_states is None:
        crawl_states = {}
    state = crawl_states.get(university_domain)
    if state is None:
        state = _DomainCrawlState()
        crawl_states[university_domain] = state
    crawl_contract = await _smart_crawl_for_candidate(
        candidate_id, full_name, university_domain, state,
        crawler if crawler is not None else Crawl4AICrawler(), client,
        request_delay_sec=request_delay_sec,
        source_url=source_url,
        blocked_keywords=blocked_keywords,
    )
    if crawl_contract is not None:
        return crawl_contract
    return Layer2Contract(candidate_id=candidate_id, crawl_status="page_not_found")




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

    `blocked_domain_keywords`: extra words to add to the builtin URL
    stop-list `_BLOCKED_URL_KEYWORDS` (news, gallery, login...); links whose
    URL contains any of them are never fetched or followed.

    `domain`: restrict to one university's domain (e.g. "utmn.ru") -- same
    convention as run_layer1/export_xlsx/run_ingest, for testing/debugging
    a single site without burning Crawl4AI/LLM calls on the rest of the DB.

    `limit`: cap on how many candidates to process this call (default 100);
    pass a smaller number for a quick smoke run.
    """
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
        blocked_keywords=_BLOCKED_URL_KEYWORDS + tuple(
            w.lower() for w in blocked_domain_keywords
        ),
    ))

    _LOGGER.info("Layer 2 processing completed")


async def _run_layer2_candidates(
    candidates: list[dict[str, Any]],
    db_path: Path,
    *,
    request_delay_sec: float,
    workers: int,
    blocked_keywords: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
) -> None:
    """Process every candidate concurrently, bounded by `workers` at a time,
    sharing one browser instance and one per-domain crawl cache
    (`crawl_states`) across all of them."""
    crawl_states: dict[str, "_DomainCrawlState"] = {}
    semaphore = asyncio.Semaphore(max(1, workers))
    write_lock = asyncio.Lock()

    with open_repository(db_path) as repo:
        async def process_one(cand: dict[str, Any], crawler: "Crawl4AICrawler | None", http_client: HttpClient) -> None:
            async with semaphore:
                try:
                    contract = await crawl_and_parse_contact(
                        candidate_id=cand["candidate_id"],
                        full_name=cand["full_name"],
                        university_domain=cand["domain"],
                        source_url=cand["source_url"],
                        client=http_client,
                        crawl_states=crawl_states,
                        crawler=crawler,
                        request_delay_sec=request_delay_sec,
                        blocked_keywords=blocked_keywords,
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

        with HttpClient(request_delay_sec=0.0) as http_client:
            if CRAWL4AI_AVAILABLE:
                browser_config = BrowserConfig(ignore_https_errors=True, verbose=False)
                async with AsyncWebCrawler(config=browser_config) as browser:
                    crawler = Crawl4AICrawler(browser)
                    await asyncio.gather(*(process_one(c, crawler, http_client) for c in candidates))
            else:
                crawler = Crawl4AICrawler()
                await asyncio.gather(*(process_one(c, crawler, http_client) for c in candidates))

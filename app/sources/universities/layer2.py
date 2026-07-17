from __future__ import annotations

import asyncio
import functools
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

def _surname_translit_variants(last_name: str) -> tuple[str, ...]:
    """Transliterate a surname both ways, return variants of length >= 3."""
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

# Банворды
_BLOCKED_URL_KEYWORDS = (
    "news", "novost", "press", "media",
    "event", "sobyti", "anons", "afisha", "calendar", "kalendar",
    "gallery", "galere", "photo", "foto", "video",
    "blog", "forum", "arhiv", "archive",
    "abitur", "priem", "admission", "postuplen",
    "login", "signin", "signup", "logout", "authoriz", "avtoriz", "register",
    "basket", "cart", "bitrix", "captcha",
    "print=", "rss", "sitemap", "pretendent", "postupa", "vakansii", "blank", "otpusk",
    "sveden", "gramot", "pozdrav", "posdraw", "mezhd", "otnosh", "united", "sertif", "certif",
    "old", "new", "bot", "feed", "recom", "reklama", "ad", "director", "uprav", "stud", "abit"
)
# Приоритет ворды
_STAFF_KEYWORDS = (
    "sotrudnik", "prepodavatel", "kafedra", "staff", "person", "employee",
    "teacher", "pps", "sostav", "kontakt", "contact", "people", "professor",
    "employees", "faculty",
)

_ANTIBOT_MARKERS = (
    "just a moment", "cf-browser-verification", "checking your browser",
    "attention required! | cloudflare", "enable javascript to run this app",
    "включите javascript",
)

# RU->EN transliteration maps for scoring URLs that embed the surname (two variants: х->kh|h, й->y|j, ц->ts|c).
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

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?:\+7|8|7)[\s\-]?\(?\d{3,4}\)?[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}(?:\s*\(\d{2,6}\))?"
    r"|\+\d{1,3}[\s\-]?\d{7,14}"
)
_MAILTO_RE = re.compile(r'href=["\']mailto:([^"\'?]+)', re.I)
_TEL_HREF_RE = re.compile(r'href=["\']tel:([^"\']+)', re.I)


_MAX_FETCHES_PER_CANDIDATE = 40  # максимум страниц на кандидата
_MAX_FETCHES_PER_DOMAIN = 3000  # максимум страниц на ВУЗ

_MIN_HTTP_TEXT_CHARS = 500


def _url_blocked(url: str, blocked: tuple[str, ...]) -> bool:
    """True if the URL contains any blocked keyword."""
    low = unquote(url).lower()
    return any(word in low for word in blocked)


def _clean_text(value: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", value).strip()


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


@functools.lru_cache(maxsize=256)
def _html_to_text(value: str) -> str:
    """Strip scripts/styles/tags, unescape entities, collapse whitespace."""
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", value, flags=re.I)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


@functools.lru_cache(maxsize=64)
def _parse_tree(html: str) -> HTMLParser:
    """Cached selectolax parse tree, shared across all read-only call sites for one page."""
    return HTMLParser(html)

def _extract_mailto_tel(html: str) -> tuple[list[str], list[str]]:
    """Pull email/phone out of mailto:/tel: href attributes before _html_to_text would drop them."""
    emails: list[str] = []
    for raw in _MAILTO_RE.findall(html):
        match = _EMAIL_RE.search(unquote(raw))
        if match:
            emails.append(match.group(0))

    phones: list[str] = []
    for raw in _TEL_HREF_RE.findall(html):
        addr = unquote(raw).strip()
        match = _PHONE_RE.search(addr)
        if match:
            phones.append(match.group(0))
        elif len(re.sub(r"\D", "", addr)) >= 7:
            phones.append(addr)  # tel: hrefs are usually a clean number even without _PHONE_RE's formatting

    return list(dict.fromkeys(emails)), list(dict.fromkeys(phones))


_BLOCK_SELECTORS = ("tr", "li", "article", "dd")
_MAX_SNIPPET_CHARS = 6000


def _find_person_html_snippet(html: str, pattern: re.Pattern[str]) -> str | None:
    """Smallest per-person container (row/list-item/...) whose text matches `pattern`; its outer HTML, or None."""
    tree = _parse_tree(html)
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
    """True if the person's name is in title/h1/h2/h3 -- the whole page is their own card."""
    tree = _parse_tree(html)
    for selector in _PERSONAL_PAGE_SELECTORS:
        for node in tree.css(selector):
            if pattern.search(_clean_text(node.text(separator=" "))):
                return True
    return False


def _with_mailto_tel(text: str, html_scope: str) -> str:
    """Append mailto:/tel: contacts found in `html_scope` to the already-stripped `text`."""
    emails, phones = _extract_mailto_tel(html_scope)
    if not emails and not phones:
        return text
    return f"{text}\n[href contacts: {' '.join(emails + phones)}]"


def _relevant_content_for_name(html: str, full_name: str) -> str:
    """Scoped text for this person: whole page if it's their own card, else nearest block, else a window around the name match, else the head of the page."""
    target_last, target_first, target_patr = split_fio_parts(full_name)
    pattern = _build_loose_name_pattern(target_last, target_first, target_patr, strict=True)
    if pattern:
        if _is_personal_page(html, pattern):
            return _with_mailto_tel(_html_to_text(html)[:_MAX_PERSONAL_PAGE_CHARS], html)
        snippet = _find_person_html_snippet(html, pattern)
        if snippet:
            return _with_mailto_tel(_html_to_text(snippet)[:_MAX_SNIPPET_CHARS], snippet)
        plain_text = _html_to_text(html)
        match = pattern.search(plain_text)
        if match:
            start = max(0, match.start() - 2000)
            end = min(len(plain_text), match.end() + 2000)
            return plain_text[start:end]
    return _html_to_text(html)[:4000]


# --- Contact parsing (Yandex LLM with regex fallback) and page fetching ---


class YandexLLMParser:
    """Parse crawled HTML for one person's contacts: Yandex Cloud LLM, with a name-scoped regex fallback."""

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
        """Fallback: extract contacts only from content scoped to the requested person."""
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
            "_email_count": len(emails),  # internal: used by parse() to skip the LLM call
        }
        if emails:
            result["email"] = emails[0]
        if phones:
            result["phone"] = phones[0]
        return result

    async def parse(self, html: str, full_name: str, candidate_id: str) -> dict[str, Any]:
        """Regex first; call the LLM only when regex is ambiguous (not the person's own page and >1 email found)."""
        if not full_name.strip():
            _LOGGER.warning("Layer2 parser called without full_name for %s", candidate_id)
            return self._extract_with_regex(html, full_name)

        regex_result = self._extract_with_regex(html, full_name)
        if regex_result.get("email") or regex_result.get("phone"):
            target_last, target_first, target_patr = split_fio_parts(full_name)
            page_pattern = _build_loose_name_pattern(target_last, target_first, target_patr, strict=True)
            is_personal = bool(page_pattern) and _is_personal_page(html, page_pattern)
            if is_personal or regex_result.get("_email_count") == 1:
                return regex_result

        if not self.available:
            return regex_result

        llm_result = await self._parse_with_yandex_llm(html, full_name)
        if llm_result:
            return llm_result

        return regex_result

    async def _parse_with_yandex_llm(self, html: str, full_name: str) -> dict[str, Any] | None:
        """Call Yandex GPT via the OpenAI-compatible AsyncOpenAI client; None on any failure/mismatch."""
        if not self.available:
            return None

        content = _relevant_content_for_name(html, full_name)
        prompt = f"""Extract email/phone for ONLY this person: {full_name}.
Name match rule: same surname + same first-name initial (+ same patronymic initial, if given) = same person, e.g. "Иванов И.И." matches "Иванов Иван Иванович". Any different initial/patronymic = a different person -> return nulls, contact_type "none".
If the person isn't in TEXT, return nulls, contact_type "none".
Return ONLY this JSON (no markdown):
{{"full_name": "as found or null", "email": "or null", "phone": "or null", "contact_type": "personal or none", "confidence": "high or medium or low"}}

TEXT:
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
                    max_tokens=200,
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
    """Fetch pages with Crawl4AI (real browser, JS rendering); pass a shared running `browser` to avoid a launch per page."""

    def __init__(self, browser: "AsyncWebCrawler | None" = None) -> None:
        self._browser = browser

    async def crawl(self, url: str) -> str | None:
        """Crawl one page; None on any failure (logged) or if crawl4ai isn't installed."""
        if not CRAWL4AI_AVAILABLE:
            return None

        try:
            if self._browser is not None:
                result = await self._browser.arun(url=url, wait_for="body", timeout=15)
                if result.success:
                    return result.html

                _LOGGER.warning(
                    "Crawl4AI unsuccessful for %s (result.success=False)",
                    url,
                )
                return None
            browser_config = BrowserConfig(ignore_https_errors=True, verbose=False)
            async with AsyncWebCrawler(config=browser_config) as browser:
                result = await browser.arun(
                    url=url,
                    wait_for="body",
                    timeout=15,
                )
                if result.success:
                    return result.html

                _LOGGER.warning(
                    "Crawl4AI unsuccessful for %s (result.success=False)",
                    url,
                )
                return None
        except Exception as e:
            _LOGGER.warning(f"Crawl4AI error for {url}: {e}")
        return None


def _name_part_regex(part: str) -> str:
    """Regex for one name part; е/ё interchangeable (pages keep ё, normalize_fio folds it to е)."""
    return "".join(
        "[еёЕЁ]" if ch in "еёЕЁ" else re.escape(ch) for ch in part
    )

#
def _build_loose_name_pattern(
    last: str, first: str, patronymic: str | None, *, strict: bool = False
) -> re.Pattern[str] | None:
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


def _looks_like_js_shell_or_challenge(html: str) -> bool:
    """True if the page is an anti-bot challenge or a near-empty JS-app shell."""
    low = html.lower()
    if any(marker in low for marker in _ANTIBOT_MARKERS):
        return True
    return len(_html_to_text(html)) < _MIN_HTTP_TEXT_CHARS

# Сначала быстрый http запрос, если ответа нет / антибот - через браузер с Crawl4AI
async def _fetch_page(
    url: str,
    crawler: "Crawl4AICrawler",
    client: HttpClient,
    request_delay_sec: float = 0.0,
    prefer_browser: bool = False,
) -> str | None:
    """Fetch one page: plain HTTP first (fast), escalate to the real browser only if HTTP failed/empty or looks like a JS shell/anti-bot page (unless `prefer_browser`, which tries the browser first)."""
    html: str | None = None
    if prefer_browser:
        html = await crawler.crawl(url) if CRAWL4AI_AVAILABLE else None

    if not html:
        try:
            response = await asyncio.to_thread(client.get, url)
            html = response.text if response.status_code == 200 else None
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug(f"Failed to fetch {url}: {e}")
            html = None

    if not prefer_browser and CRAWL4AI_AVAILABLE:
        if not html or _looks_like_js_shell_or_challenge(html):
            browser_html = await crawler.crawl(url)
            if browser_html:
                html = browser_html

    if request_delay_sec:
        await asyncio.sleep(request_delay_sec)
    return html


# --- Smart priority-driven crawl: every discovered link is scored for how likely it leads to THIS
# candidate's personal page, and the frontier is a priority queue (links next to the FIO first,
# alphabet-index links only for the matching letter, surname-in-URL beats staff-section beats
# everything else, pagination gets a small boost). Never leaves the university's own site;
# HTML is cached per university and shared across all of its candidates.

_SEED_HOST_TEMPLATES = ("{domain}", "www.{domain}")

_SINGLE_LETTER_RE = re.compile(r"^[А-ЯЁа-яёA-Za-z]$")
_LETTER_QUERY_RE = re.compile(r"^(?:[A-Za-z_]+=)?([А-ЯЁа-яё])$")
_PAGINATION_RE = re.compile(r"[?&](?:page|p|pagen[_\d]*)=\d+|/page/\d+", re.I)


@dataclass
class _DomainCrawlState:
    """Per-university crawl cache shared across all its candidates: fetched HTML, in-flight fetches (dedup racing requests), fetch-budget counter."""

    pages: dict[str, str] = field(default_factory=dict)
    pending: dict[str, "asyncio.Future[str]"] = field(default_factory=dict)
    fetches: int = 0


def _norm_url(url: str) -> str:
    """Drop fragment, strip trailing slash -- cache/visited key."""
    return url.split("#", 1)[0].rstrip("/")


def _same_site(url: str, domain: str) -> bool:
    """True if url's host is exactly `domain` or a subdomain of it."""
    netloc = urlparse(url).netloc.lower().split(":", 1)[0]
    domain = domain.lower()
    return netloc == domain or netloc.endswith("." + domain)


def _page_links(
    html: str,
    base_url: str,
    domain: str,
    blocked: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
) -> list[tuple[str, str]]:
    """(absolute_url, anchor_text) pairs for crawlable same-site links on the page."""
    tree = _parse_tree(html)
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


_MAX_NEAR_NAME_BLOCK_CHARS = 3000


def _links_near_name(
    html: str,
    base_url: str,
    domain: str,
    pattern: re.Pattern[str],
    blocked: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
) -> list[str]:
    """Links inside the same per-person block (row/card/...) as the matched name -- almost always the personal-page link. `div` included for grid-style staff directories; size-checked before `.text()` to avoid re-serializing huge nav wrappers."""
    tree = _parse_tree(html)
    out: list[str] = []
    seen: set[str] = set()
    for selector in ("tr", "li", "dd", "article", "td", "p", "div"):
        for node in tree.css(selector):
            raw_html = node.html or ""
            if not raw_html or len(raw_html) > _MAX_NEAR_NAME_BLOCK_CHARS:
                continue
            text = _clean_text(node.text(separator=" "))
            if not text or not pattern.search(text):
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
            break  # smallest matching container type already gave the nearest links

    return out


def _alphabet_letter(url: str, anchor: str) -> str | None:
    """If this link is an alphabet-index entry ('А', ?letter=В...), its letter; else None."""
    if _SINGLE_LETTER_RE.match(anchor):
        return anchor
    query = unquote(urlparse(url).query).strip()
    match = _LETTER_QUERY_RE.match(query)
    if match:
        return match.group(1)
    return None

# Приоритезация ссылок
def _score_link(
    url: str,
    anchor: str,
    target_last: str,
    translit_variants: tuple[str, ...],
    name_pattern: re.Pattern[str] | None,
) -> int | None:
    """Priority score for a link, higher = crawled sooner; None = skip (alphabet-index link for another letter)."""
    letter = _alphabet_letter(url, anchor)
    if letter is not None:
        return 90 if letter.lower() == target_last[:1].lower() else None

    score = 0
    low = unquote(url).lower()
    anchor_low = anchor.lower()
    last_low = target_last.lower()
    if name_pattern is not None and name_pattern.search(anchor): # URL совпадает с ФИО
        score += 120
    elif last_low and (last_low in anchor_low or last_low in low): # Фамилия в URL или тексте ссылки
        score += 100
    if any(t in low for t in translit_variants): # Фамилия в транслите в URL (суммируется)
        score += 80
    if any(k in low or k in anchor_low for k in _STAFF_KEYWORDS): # На странице встречется приоритет ворд
        score += 30
    if _PAGINATION_RE.search(low): # Если ссылка похожа на пагинацию ?page=2, /page/3
        score += 10
    return score


async def _smart_crawl_for_candidate(
    candidate_id: str,
    full_name: str,
    domain: str,
    state: _DomainCrawlState,
    crawler: "Crawl4AICrawler",
    client: HttpClient,
    parser: "YandexLLMParser",
    request_delay_sec: float = 0.0,
    source_url: str | None = None,
    blocked_keywords: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
    prefer_browser: bool = False,
    max_fetches_per_candidate: int = _MAX_FETCHES_PER_CANDIDATE,
) -> Layer2Contract | None:
    """Best-first crawl of the site for this candidate: returns contacts as soon as found, else a contact-less page_found contract if the person was seen, else None."""
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

    best_no_contact: Layer2Contract | None = None
    best_is_personal = False
    fetched = 0

    while frontier and fetched < max_fetches_per_candidate:
        _, _, url = heapq.heappop(frontier)
        norm = _norm_url(url)
        if norm in visited:
            continue
        visited.add(norm)

        html = state.pages.get(norm)
        if html is None:
            pending = state.pending.get(norm)
            if pending is not None:
                html = await pending  # another candidate's crawl is already fetching this exact URL
            else:
                if state.fetches >= _MAX_FETCHES_PER_DOMAIN:
                    break
                future: "asyncio.Future[str]" = asyncio.get_running_loop().create_future()
                state.pending[norm] = future
                state.fetches += 1
                fetched += 1
                try:
                    html = await _fetch_page(
                        url, crawler, client, request_delay_sec, prefer_browser=prefer_browser,
                    ) or ""
                    state.pages[norm] = html
                finally:
                    future.set_result(html)
                    del state.pending[norm]
        if not html:
            continue

        page_text = _html_to_text(html)
        name_on_page = bool(loose_pattern.search(page_text))

        if name_on_page:
            for near in _links_near_name(html, url, domain, loose_pattern, blocked_keywords):
                push(near, 200)
            try:
                parse_result = await parser.parse(html, full_name, candidate_id)
            except Exception as e:  # noqa: BLE001
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
            page_is_personal = _is_personal_page(html, loose_pattern)
            if best_no_contact is None or (page_is_personal and not best_is_personal):
                best_is_personal = page_is_personal
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


# --- Entry points ---


async def crawl_and_parse_contact(
    candidate_id: str,
    full_name: str,
    university_domain: str,
    search_term: str | None = None,
    source_url: str | None = None,
    client: HttpClient | None = None,
    crawl_states: dict[str, "_DomainCrawlState"] | None = None,
    crawler: "Crawl4AICrawler | None" = None,
    parser: "YandexLLMParser | None" = None,
    request_delay_sec: float = 0.0,
    blocked_keywords: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
    prefer_browser: bool = False,
    max_fetches_per_candidate: int = _MAX_FETCHES_PER_CANDIDATE,
) -> Layer2Contract:
    """Single-candidate entry point. Pass a shared `crawl_states`/`parser` across a batch (run_layer2 does) to reuse fetched pages and avoid re-reading env vars per candidate."""
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
        parser if parser is not None else YandexLLMParser(),
        request_delay_sec=request_delay_sec,
        source_url=source_url,
        blocked_keywords=blocked_keywords,
        prefer_browser=prefer_browser,
        max_fetches_per_candidate=max_fetches_per_candidate,
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
    prefer_browser: bool = False,
    max_fetches_per_candidate: int = _MAX_FETCHES_PER_CANDIDATE,
) -> None:
    db_path = Path(db_path)
    with open_repository(db_path) as repo:
        sql = """
            SELECT c.candidate_id, c.full_name, c.source_url, u.domain, u.university_id
            FROM candidates c
            JOIN universities u ON c.university_id = u.university_id
            WHERE c.email IS NULL
              AND c.contact_type IS NULL
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
        prefer_browser=prefer_browser,
        max_fetches_per_candidate=max_fetches_per_candidate,
    ))

    _LOGGER.info("Layer 2 processing completed")


async def _run_layer2_candidates(
    candidates: list[dict[str, Any]],
    db_path: Path,
    *,
    request_delay_sec: float,
    workers: int,
    blocked_keywords: tuple[str, ...] = _BLOCKED_URL_KEYWORDS,
    prefer_browser: bool = False,
    max_fetches_per_candidate: int = _MAX_FETCHES_PER_CANDIDATE,
) -> None:
    """Process every candidate concurrently (bounded by `workers`), sharing one browser, one per-domain crawl cache, and one parser across all of them."""
    crawl_states: dict[str, "_DomainCrawlState"] = {}
    parser = YandexLLMParser()
    semaphore = asyncio.Semaphore(max(1, workers))
    write_lock = asyncio.Lock()  # sqlite3 writes aren't safe to interleave across coroutines

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
                        parser=parser,
                        request_delay_sec=request_delay_sec,
                        blocked_keywords=blocked_keywords,
                        prefer_browser=prefer_browser,
                        max_fetches_per_candidate=max_fetches_per_candidate,
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
                    contract.source_url if (contract.email or contract.phone) else None,
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

"""Layer 2 contacts crawler + AI parser with Crawl4AI and Yandex LLM.

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

Full mechanics: candidate-pipeline-architecture.md §6.
Stub contract: specs/001-core-pipeline-mvp/contracts/future-layer2-vk-stub-contract.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

try:
    from crawl4ai import AsyncWebCrawler
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False

import httpx

from app.db.repository import Repository, open_repository
from app.sources.http_client import HttpClient

_LOGGER = logging.getLogger(__name__)
_EMPLOYEES_PATH = "/sveden/employees"


@dataclass
class Layer2Contract:
    """Output contract for layer 2."""
    candidate_id: str
    crawl_status: str  # 'page_found', 'page_not_found', 'error'
    contact_type: str | None = None  # 'personal', 'university'
    email: str | None = None
    phone: str | None = None
    source_url: str | None = None
    confidence: str | None = None  # 'high', 'medium', 'low'
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "crawl_status": self.crawl_status,
            "contact_type": self.contact_type,
            "email": self.email,
            "phone": self.phone,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "error_message": self.error_message,
        }


class YandexLLMParser:
    """Parse crawled HTML using Yandex Cloud LLM API."""

    def __init__(self) -> None:
        self.folder_id = os.getenv("YANDEX_FOLDER_ID", "").strip()
        self.api_key = os.getenv("YANDEX_API_KEY", "").strip()
        if not self.folder_id or not self.api_key:
            _LOGGER.info("Yandex credentials not configured; using regex fallback")
        self.available = bool(self.folder_id and self.api_key)

    def _extract_with_regex(self, html: str, full_name: str) -> dict[str, Any]:
        """Fallback: Extract email and phone using regex patterns."""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        phone_pattern = r'(?:\+7|8)?[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|\+\d{1,3}\s?\d{1,14}'

        emails = list(set(re.findall(email_pattern, html)))
        phones = list(set(re.findall(phone_pattern, html)))

        result: dict[str, Any] = {
            "confidence": "low",
            "method": "regex",
        }
        if emails:
            result["email"] = emails[0]
        if phones:
            result["phone"] = phones[0]
        return result

    def parse(self, html: str, full_name: str, candidate_id: str) -> dict[str, Any]:
        """Parse HTML using Yandex LLM or fallback to regex."""
        if not self.available:
            return self._extract_with_regex(html, full_name)

        llm_result = self._parse_with_yandex_llm(html, full_name)
        if llm_result:
            return llm_result
        
        return self._extract_with_regex(html, full_name)

    def _parse_with_yandex_llm(self, html: str, full_name: str) -> dict[str, Any] | None:
        """Call Yandex GPT API via HTTP."""
        if not (self.folder_id and self.api_key):
            return None

        prompt = f"""Analyze the provided HTML content and extract contact information for {full_name}.
        Extract:
        1. email address (personal or university)
        2. phone number (if available)

        Return ONLY a JSON object with this structure (no markdown, no backticks):
        {{"email": "email@example.com or null", "phone": "+7... or null", "contact_type": "personal or university or null", "confidence": "high or medium or low"}}

        HTML content:
        {html[:2000]}"""

        try:
            url = "https://llm.api.cloud.yandex.net:443/llm/v1alpha/chat/completion"
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model_uri": f"gpt://{self.folder_id}/yandexgpt-lite",
                "completion_options": {
                    "stream": False,
                    "temperature": 0.1,
                    "max_tokens": 500,
                },
                "messages": [
                    {
                        "role": "system",
                        "text": "You are a helpful assistant that extracts contact information from HTML. Respond with JSON only, no markdown."
                    },
                    {
                        "role": "user",
                        "text": prompt
                    }
                ]
            }

            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    if "result" in data and "message" in data["result"]:
                        message_text = data["result"]["message"].get("text", "")
                        try:
                            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', message_text, re.DOTALL)
                            if json_match:
                                parsed = json.loads(json_match.group())
                                return {
                                    "email": parsed.get("email"),
                                    "phone": parsed.get("phone"),
                                    "contact_type": parsed.get("contact_type"),
                                    "confidence": parsed.get("confidence", "medium"),
                                    "method": "yandex_llm",
                                }
                        except (json.JSONDecodeError, AttributeError):
                            pass
                else:
                    _LOGGER.warning(f"Yandex API error: {response.status_code}")
        except Exception as e:
            _LOGGER.warning(f"Yandex LLM call failed: {e}")
        
        return None


class Crawl4AICrawler:
    """Crawl contacts using Crawl4AI."""

    async def crawl(self, url: str, full_name: str) -> str | None:
        """Crawl a page using Crawl4AI."""
        if not CRAWL4AI_AVAILABLE:
            return None

        try:
            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(
                    url=url,
                    wait_for=".staff-card, [itemprop='email'], [itemprop='phone'], .contact, .person",
                    timeout=15,
                )
                if result.success:
                    return result.html
        except Exception as e:
            _LOGGER.warning(f"Crawl4AI error for {url}: {e}")
        return None


def check_employees_endpoint(domain: str, client: HttpClient) -> bool:
    """Check if /sveden/employees endpoint exists."""
    url = f"https://{domain}{_EMPLOYEES_PATH}"
    try:
        response = client.get(url)
        return response.status_code == 200
    except Exception as e:
        _LOGGER.debug(f"Failed to check {url}: {e}")
        return False


async def crawl_and_parse_contact(
    candidate_id: str,
    full_name: str,
    university_domain: str,
    search_term: str | None = None,
    client: HttpClient | None = None,
) -> Layer2Contract:
    """Crawl and parse contact info for a candidate."""
    if client is None:
        client = HttpClient(request_delay_sec=2.0)

    base_url = f"https://{university_domain}"
    employees_url = f"{base_url}{_EMPLOYEES_PATH}"

    # Step 1: Check if employees page exists
    if not check_employees_endpoint(university_domain, client):
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="page_not_found",
        )

    # Step 2: Prepare search URL (URL-encode the search parameter)
    search_query = search_term or full_name.split()[-1]
    params = {"search": search_query}
    search_url = f"{employees_url}?{urlencode(params)}"

    # Step 3: Crawl using Crawl4AI if available
    crawler = Crawl4AICrawler()
    html = None
    if CRAWL4AI_AVAILABLE:
        html = await crawler.crawl(search_url, full_name)

    if not html:
        try:
            response = client.get(search_url)
            if response.status_code == 200:
                html = response.text
        except Exception as e:
            _LOGGER.warning(f"Failed to fetch {search_url}: {e}")

    if not html:
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="page_not_found",
        )

    # Step 4: Parse using Yandex LLM
    parser = YandexLLMParser()
    try:
        parse_result = parser.parse(html, full_name, candidate_id)
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="page_found",
            email=parse_result.get("email"),
            phone=parse_result.get("phone"),
            contact_type=parse_result.get("contact_type"),
            confidence=parse_result.get("confidence", "low"),
            source_url=search_url,
        )
    except Exception as e:
        _LOGGER.error(f"Parse error for {candidate_id}: {e}")
        return Layer2Contract(
            candidate_id=candidate_id,
            crawl_status="error",
            error_message=str(e),
            source_url=search_url,
        )


def run_layer2(
    db_path: Path | str,
    run_id: int,
    *,
    request_delay_sec: float = 2.0,
    workers: int = 2,
) -> None:
    """Run Layer 2 crawling and parsing for candidates."""
    db_path = Path(db_path)
    with open_repository(db_path) as repo:
        # Fetch candidates that need layer2 processing
        rows = repo.execute("""
            SELECT c.candidate_id, c.full_name, u.domain
            FROM candidates c
            JOIN universities u ON c.university_id = u.university_id
            WHERE c.email IS NULL
            LIMIT 100
        """)
        candidates = [
            {
                "candidate_id": row["candidate_id"],
                "full_name": row["full_name"],
                "domain": row["domain"],
            }
            for row in rows
        ]

    if not candidates:
        _LOGGER.info("No candidates to process for layer2")
        return

    _LOGGER.info(f"Processing {len(candidates)} candidates with layer2")

    # Process candidates sequentially using asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        with HttpClient(request_delay_sec=request_delay_sec) as http_client:
            for cand in candidates:
                try:
                    contract = loop.run_until_complete(
                        crawl_and_parse_contact(
                            candidate_id=cand["candidate_id"],
                            full_name=cand["full_name"],
                            university_domain=cand["domain"],
                            client=http_client,
                        )
                    )
                    # Update database with results
                    with open_repository(db_path) as repo:
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
                except Exception as e:
                    _LOGGER.error(f"Error processing candidate: {e}")
                
                time.sleep(request_delay_sec)
    finally:
        loop.close()

    _LOGGER.info("Layer 2 processing completed")



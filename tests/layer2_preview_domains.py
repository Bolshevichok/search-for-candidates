"""Preview Layer2 crawling and parsing results for domains from data/university_registry.csv."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Iterator

import os

from app.sources.universities.layer2 import check_employees_endpoint, crawl_and_parse_contact
from app.sources.http_client import HttpClient

REGISTRY = Path(__file__).resolve().parents[1] / "data" / "university_registry.csv"

# Force fallback to HTTP parsing in this preview environment.
os.environ["YANDEX_FOLDER_ID"] = ""
os.environ["YANDEX_API_KEY"] = ""
try:
    import app.sources.universities.layer2 as layer2_module
    layer2_module.CRAWL4AI_AVAILABLE = False
except ImportError:
    pass


def read_domains(limit: int = 10) -> Iterator[str]:
    count = 0
    with REGISTRY.open("r", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            domain = row.get("domain")
            if not domain:
                continue
            yield domain.strip()
            count += 1
            if limit and count >= limit:
                break


def run_preview(limit: int = 10) -> None:
    domains = list(read_domains(limit=limit))
    client = HttpClient(request_delay_sec=1.0, timeout=15.0)
    print(f"Checking {len(domains)} domains from registry")

    for domain in domains:
        print("\n---")
        print(f"Domain: {domain}")
        try:
            exists = check_employees_endpoint(domain, client)
            print(f"/sveden/employees exists: {exists}")
        except Exception as exc:
            print(f"Error checking endpoint: {exc}")
            continue

        if not exists:
            continue

        # Use a common surname to see the search output
        try:
            contract = asyncio.run(
                crawl_and_parse_contact(
                    candidate_id="preview",
                    full_name="Иванов Иван Иванович",
                    university_domain=domain,
                    search_term="Иванов",
                    client=client,
                )
            )
            print("Parsed result:")
            print(contract.to_dict())
        except Exception as exc:
            print(f"Error crawling/parsing: {exc}")


if __name__ == "__main__":
    run_preview(limit=10)

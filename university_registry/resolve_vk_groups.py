"""
Resolve vk_group_id for rows in data/university_registry.csv.

Requires VK_TOKEN in .env or environment (user/service token with groups.search).

Usage:
  python university_registry/resolve_vk_groups.py
  python university_registry/resolve_vk_groups.py --limit 20
  python university_registry/resolve_vk_groups.py --only-missing
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_CSV = ROOT / "data/university_registry.csv"
ENV_PATH = ROOT / ".env"
API = "https://api.vk.ru/method/groups.search"


def load_token() -> str:
    token = os.environ.get("VK_TOKEN", "").strip()
    if token:
        return token
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("VK_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("VK_TOKEN not found. Put it in .env or environment.")


def vk_search(token: str, query: str) -> dict | None:
    params = {
        "q": query,
        "type": "page",
        "count": "10",
        "v": "5.199",
        "access_token": token,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "error" in payload:
        code = payload["error"].get("error_code")
        if code in (6, 9):
            time.sleep(2)
            return vk_search(token, query)
        raise RuntimeError(payload["error"])
    items = payload.get("response", {}).get("items") or []
    return items[0] if items else None


def pick_query(row: dict) -> str:
    aliases = row.get("aliases", "")
    first_alias = aliases.split("|")[0] if aliases else ""
    name = row["official_name"]
    # Prefer short alias if present and not too long
    if first_alias and len(first_alias) <= 80:
        return first_alias
    # Strip legal form prefix
    cleaned = re.sub(
        r"(?i)^(?:федеральное|государственное|автономная|негосударственное|"
        r"образовательное|аккредитованное|частное|бюджетное)[^«]*",
        "",
        name,
    ).strip(" «»")
    return cleaned[:100] or name[:100]


def score_candidate(item: dict, row: dict) -> int:
    score = 0
    title = (item.get("name") or "").lower()
    domain = (row.get("domain") or "").lower()
    official = row["official_name"].lower()
    if domain and domain.split(".")[0] in title:
        score += 5
    for token in re.findall(r"[a-zа-яё0-9]{4,}", official):
        if token in title:
            score += 1
    if item.get("is_closed"):
        score -= 2
    return score


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()

    if not REGISTRY_CSV.exists():
        raise SystemExit(f"Missing {REGISTRY_CSV}. Run build_university_registry.py first.")

    token = load_token()
    with REGISTRY_CSV.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    updated = 0
    processed = 0
    for row in rows:
        if args.only_missing and row.get("vk_group_id"):
            continue
        if args.limit and processed >= args.limit:
            break

        query = pick_query(row)
        try:
            params = {
                "q": query,
                "type": "page",
                "count": "10",
                "v": "5.199",
                "access_token": token,
            }
            url = API + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            items = payload.get("response", {}).get("items") or []
            if not items:
                row["source_notes"] = (row.get("source_notes") or "") + ";vk_not_found"
            else:
                best = max(items, key=lambda it: score_candidate(it, row))
                row["vk_group_id"] = str(best["id"])
                row["vk_screen_name"] = best.get("screen_name") or ""
                row["vk_url"] = f"https://vk.com/{best.get('screen_name') or 'club' + str(best['id'])}"
                row["source_notes"] = (row.get("source_notes") or "") + ";vk_groups.search"
                updated += 1
        except Exception as exc:  # noqa: BLE001 - batch tool, keep going
            row["source_notes"] = (row.get("source_notes") or "") + f";vk_error={exc}"
        processed += 1
        time.sleep(args.delay)

    fieldnames = rows[0].keys() if rows else []
    with REGISTRY_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Processed {processed}, vk_group_id filled for {updated} rows -> {REGISTRY_CSV}")


if __name__ == "__main__":
    main()

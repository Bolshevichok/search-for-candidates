"""
Resolve vk_group_id via browser: scrape VK links from university sites,
then open VK pages in Chromium to extract group_id.

No VK API token required.

Usage:
  python university_registry/resolve_vk_browser.py --only-missing --limit 20
  python university_registry/resolve_vk_browser.py --only-missing
"""
from __future__ import annotations

import argparse
import csv
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def safe_print(text: str) -> None:
    """Avoid Windows console UnicodeEncodeError on VK titles with emoji."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("cp1251", errors="replace").decode("cp1251"))
REGISTRY_CSV = ROOT / "data/university_registry.csv"
REVIEW_CSV = ROOT / "data/university_registry_vk_review.csv"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VK_LINK_RE = re.compile(
    r"https?://(?:m\.)?vk\.com/(?!js/|share|login|feed|search|away|video|wall|clip|topic)([a-zA-Z0-9._-]+)"
)
SKIP_SCREEN = {
    "away", "docs", "audio", "apps", "settings", "id", "club", "public",
    "api", "dev", "support", "mobile", "join", "invite",
}


def pick_search_query(row: dict) -> str:
    aliases = (row.get("aliases") or "").split("|")
    candidates: list[str] = []
    for part in aliases:
        part = part.strip().strip('"')
        if not part:
            continue
        low = part.lower()
        if any(x in low for x in ("образователь", "федеральн", "государствен", "автономн", "учрежден")):
            continue
        if len(part) <= 60:
            candidates.append(part)
    if candidates:
        return min(candidates, key=len)
    domain = (row.get("domain") or "").strip()
    if domain:
        return domain.split(".")[0]
    return (row.get("official_name") or "")[:50]


def fetch_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def vk_links_from_site(domain: str) -> list[str]:
    if not domain:
        return []
    urls = [
        f"https://{domain}/",
        f"https://{domain}/sveden/common",
        f"http://{domain}/",
        f"http://{domain}/sveden/common",
    ]
    found: list[str] = []
    seen: set[str] = set()
    for url in urls:
        try:
            html = fetch_html(url)
        except Exception:
            continue
        for m in VK_LINK_RE.finditer(html):
            screen = m.group(1)
            if screen.lower() in SKIP_SCREEN or screen in seen:
                continue
            if screen.startswith("club") and screen[4:].isdigit():
                screen = f"public{screen[4:]}"
            seen.add(screen)
            found.append(screen)
        if found:
            break
    return found


def yandex_vk_links(page, query: str) -> list[str]:
    url = "https://yandex.ru/search/?text=" + urllib.parse.quote(f"site:vk.com {query} университет")
    page.goto(url, wait_until="networkidle", timeout=45000)
    time.sleep(1.5)
    html = page.content()
    out: list[str] = []
    seen: set[str] = set()
    for m in VK_LINK_RE.finditer(html):
        screen = m.group(1)
        if screen.lower() in SKIP_SCREEN or screen in seen:
            continue
        seen.add(screen)
        out.append(screen)
    return out


def fetch_group_id(page, screen_name: str) -> tuple[str, str, str]:
    url = f"https://vk.com/{screen_name}"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(1.0)
    title = page.title()
    if "not found" in title.lower() or "страница не найдена" in title.lower():
        return "", "", title
    data = page.evaluate(
        """() => {
          const html = document.documentElement.innerHTML;
          const m1 = html.match(/"group_id":(\\d+)/);
          const m2 = html.match(/public(\\d+)/);
          return {
            group_id: m1 ? m1[1] : (m2 ? m2[1] : ''),
            path: location.pathname.replace(/^\\//, ''),
            title: document.title
          };
        }"""
    )
    gid = str(data.get("group_id") or "")
    path = data.get("path") or screen_name
    return gid, path, data.get("title") or title


def score_screen(screen: str, title: str, query: str, domain: str) -> int:
    score = 0
    s = screen.lower()
    t = title.lower()
    q = query.lower()
    d = domain.lower().split(".")[0] if domain else ""
    if d and (d in s or d in t):
        score += 10
    for token in re.findall(r"[a-zа-яё0-9]{3,}", q):
        if token in t or token in s:
            score += 2
    if any(x in t for x in ("официаль", "университет", "институт", "академ")):
        score += 3
    if any(x in s for x in ("abitur", "student", "press", "mag_", "kz", "filial")):
        score -= 2
    return score


def resolve_row(page, row: dict) -> dict | None:
    domain = (row.get("domain") or "").strip()
    query = pick_search_query(row)
    candidates: list[str] = []

    # 1) VK link on official site — most reliable
    candidates.extend(vk_links_from_site(domain))

    # 2) Obvious short-name guesses
    if domain:
        prefix = domain.split(".")[0]
        for guess in {prefix, prefix.replace("-", ""), f"club{prefix}"}:
            if guess and guess not in candidates:
                candidates.append(guess)

    def pick_best(screens: list[str]) -> dict | None:
        best: dict | None = None
        best_score = -999
        for screen in screens[:8]:
            gid, path, title = fetch_group_id(page, screen)
            if not gid:
                continue
            sc = score_screen(path, title, query, domain)
            if sc > best_score:
                best_score = sc
                best = {
                    "vk_group_id": gid,
                    "vk_screen_name": path,
                    "vk_url": f"https://vk.com/{path}",
                    "match_title": title,
                }
        return best

    hit = pick_best(candidates)
    if hit:
        return hit

    # 3) Yandex site:vk.com search — only if site/guess failed
    yandex_candidates = yandex_vk_links(page, query)
    return pick_best(yandex_candidates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()

    with REGISTRY_CSV.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    fieldnames = rows[0].keys()

    review: list[dict] = []
    resolved = processed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        page = browser.new_page(user_agent=UA)

        for row in rows:
            if args.only_missing and row.get("vk_group_id"):
                continue
            if args.limit and processed >= args.limit:
                break

            processed += 1
            query = pick_search_query(row)
            safe_print(f"[{processed}] {row['id']} :: {query}")

            try:
                hit = resolve_row(page, row)
            except Exception as exc:  # noqa: BLE001
                hit = None
                row["source_notes"] = (row.get("source_notes") or "") + f";vk_browser_error={exc}"

            if hit:
                row["vk_group_id"] = hit["vk_group_id"]
                row["vk_screen_name"] = hit["vk_screen_name"]
                row["vk_url"] = hit["vk_url"]
                notes = row.get("source_notes") or ""
                if "vk_browser" not in notes:
                    row["source_notes"] = notes + ";vk_browser"
                resolved += 1
                title = (hit.get("match_title") or "")[:60]
                safe_print(f"    -> {hit['vk_url']} ({title})")
            else:
                review.append({
                    "id": row["id"],
                    "query": query,
                    "official_name": row["official_name"],
                    "domain": row.get("domain") or "",
                })
                safe_print("    -> not found")

            with REGISTRY_CSV.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
            time.sleep(args.delay)

        browser.close()

    if review:
        with REVIEW_CSV.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["id", "query", "official_name", "domain"])
            w.writeheader()
            w.writerows(review)

    safe_print(f"Done: processed={processed}, resolved={resolved}, review={len(review)}")


if __name__ == "__main__":
    main()

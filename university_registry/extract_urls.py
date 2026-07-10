"""Extract data-file URLs from obrnadzor RAOO open-data HTML page."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "data/raw/raoo_page.html").read_text(encoding="utf-8", errors="ignore")

for url in sorted(set(re.findall(r"https?://[^\s\"'<>]+", html))):
    lower = url.lower()
    if any(x in lower for x in (".csv", ".json", ".xml", ".zip", "upload/opendata", "7701537808", "isga")):
        print(url)

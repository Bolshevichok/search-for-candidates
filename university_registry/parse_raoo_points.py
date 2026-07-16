import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "data/raw/raoo_page.html").read_text(encoding="utf-8", errors="ignore")
match = re.search(r"var point = (\[.*?\]);", html, re.S)
if not match:
    raise SystemExit("point array not found in data/raw/raoo_page.html")

raw = match.group(1)
raw = re.sub(r",\s*]", "]", raw)
raw = re.sub(r",\s*}", "}", raw)
points = json.loads(raw)

out = ROOT / "data/raw/raoo_map_points.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(points, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Saved {len(points)} map points -> {out}")

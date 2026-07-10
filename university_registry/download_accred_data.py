"""Download official accred registry ZIP from ISGA (Rosobrnadzor)."""
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/accredreestr.zip"
URL = "https://isga.obrnadzor.gov.ru/api/spa/accredreestr/opendata/filezip"

OUT.parent.mkdir(parents=True, exist_ok=True)
print(f"Downloading {URL} ...")
urllib.request.urlretrieve(URL, OUT)
print(f"Saved {OUT.stat().st_size // (1024 * 1024)} MB -> {OUT}")

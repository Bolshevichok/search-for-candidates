import itertools
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
zip_path = ROOT / "data/raw/accredreestr.zip"
out = ROOT / "data/raw/xml_sample.txt"

with zipfile.ZipFile(zip_path) as z:
    with z.open("data-20260710-structure-20160713.xml") as f:
        sample = b"".join(itertools.islice(f, 200))
out.write_bytes(sample)
print(f"Wrote {len(sample)} bytes to {out}")

from __future__ import annotations

import csv
import html
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "data/raw/accredreestr.zip"
OUT_CSV = ROOT / "data/university_registry.csv"
OUT_JSON = ROOT / "data/university_registry.json"
META_JSON = ROOT / "data/university_registry.meta.json"

HE_LEVEL_RE = re.compile(
    r"(?i)высшее\s+образование|высшее\s+профессиональное|послевузовское\s+профессиональное"
)
SCHOOL_NAME_RE = re.compile(
    r"(?i)\b(школ|сош|оош|гимназ|лицей|детск|дошкольн|колледж\s+№|техникум\s+№)\b"
)
PILOT_NAME_RE = re.compile(
    r"(?i)федеральн\w+\s+университет|национальн\w+\s+исследовательск\w+|"
    r"московский\s+государственный\s+университет|"
    r"санкт[- ]?петербургский\s+государственный\s+университет|"
    r"новосибирский\s+государственный\s+университет|"
    r"московский\s+физико-технический\s+институт|"
    r"национальный\s+исследовательский\s+университет"
)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text(elem: ET.Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return html.unescape(elem.text).strip()


def normalize_domain(raw: str) -> str:
    raw = raw.strip()
    if not raw or raw.upper() in {"NULL", "-", "NONE"}:
        return ""
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")
    try:
        host = urlparse(raw).netloc or urlparse("https://" + raw).path.split("/")[0]
    except Exception:
        return ""
    host = host.lower().removeprefix("www.")
    return host if "." in host else ""


def is_higher_ed(name: str, edu_levels: set[str]) -> bool:
    if SCHOOL_NAME_RE.search(name):
        return False
    return any(HE_LEVEL_RE.search(level) for level in edu_levels)


def is_pilot(name: str) -> bool:
    return bool(PILOT_NAME_RE.search(name))


def aliases_from(short_name: str, full_name: str) -> list[str]:
    out: list[str] = []
    for value in (short_name, full_name):
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


def xml_member_name() -> str:
    with zipfile.ZipFile(ZIP_PATH) as z:
        names = [n for n in z.namelist() if n.endswith(".xml")]
        if not names:
            raise SystemExit(f"No XML in {ZIP_PATH}")
        return names[0]


def iter_certificates(xml_name: str):
    with zipfile.ZipFile(ZIP_PATH) as z:
        with z.open(xml_name) as raw:
            for _, elem in ET.iterparse(raw, events=("end",)):
                if local(elem.tag) != "Certificate":
                    continue
                yield elem
                elem.clear()


def parse_certificate(cert: ET.Element) -> dict | None:
    status = text(cert.find("StatusName"))
    if status != "Действующее":
        return None

    org = cert.find("ActualEducationOrganization")
    if org is None:
        return None
    if text(org.find("IsBranch")) == "1":
        return None

    full_name = text(org.find("FullName")) or text(cert.find("EduOrgFullName"))
    short_name = text(org.find("ShortName")) or text(cert.find("EduOrgShortName"))
    if not full_name:
        return None

    edu_levels = {
        text(node)
        for node in cert.iter()
        if local(node.tag) == "EduLevelName" and text(node)
    }
    if not is_higher_ed(full_name, edu_levels):
        return None

    ogrn = text(org.find("OGRN")) or text(cert.find("EduOrgOGRN"))
    inn = text(org.find("INN")) or text(cert.find("EduOrgINN"))
    region = text(org.find("RegionName")) or text(cert.find("RegionName"))
    domain = normalize_domain(text(org.find("WebSite")))

    return {
        "official_name": full_name,
        "aliases": aliases_from(short_name, full_name),
        "domain": domain,
        "region": region,
        "inn": inn,
        "ogrn": ogrn,
        "accreditation_status": status,
        "is_pilot": is_pilot(full_name),
        "org_id": text(org.find("Id")),
    }


def main() -> None:
    if not ZIP_PATH.exists():
        raise SystemExit(f"Missing {ZIP_PATH}. Run: python university_registry/download_accred_data.py")

    xml_name = xml_member_name()
    print(f"Parsing {xml_name} from {ZIP_PATH.name} ...")

    by_ogrn: dict[str, dict] = {}
    seen = 0
    kept = 0

    for cert in iter_certificates(xml_name):
        seen += 1
        if seen % 5000 == 0:
            print(f"  scanned {seen} certificates, kept {len(by_ogrn)} unique HE orgs ...")
        row = parse_certificate(cert)
        if not row:
            continue
        key = row["ogrn"] or row["org_id"] or row["official_name"]
        prev = by_ogrn.get(key)
        if prev is None or (not prev["domain"] and row["domain"]):
            by_ogrn[key] = row
            kept += 1

    rows = sorted(by_ogrn.values(), key=lambda r: r["official_name"].lower())
    for i, row in enumerate(rows, start=1):
        row["id"] = f"uni_{i:04d}"
        row["vk_group_id"] = ""
        row["vk_screen_name"] = ""
        row["vk_url"] = ""
        row["source_notes"] = "obrnadzor_accredreestr"

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "official_name",
        "aliases",
        "domain",
        "region",
        "inn",
        "ogrn",
        "accreditation_status",
        "vk_group_id",
        "vk_screen_name",
        "vk_url",
        "is_pilot",
        "source_notes",
    ]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out = {k: row[k] for k in fieldnames if k in row}
            out["aliases"] = "|".join(row["aliases"])
            out["is_pilot"] = "true" if row["is_pilot"] else "false"
            writer.writerow(out)

    OUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    META_JSON.write_text(
        json.dumps(
            {
                "source_zip": str(ZIP_PATH),
                "source_xml": xml_name,
                "certificates_scanned": seen,
                "universities_kept": len(rows),
                "with_domain": sum(1 for r in rows if r["domain"]),
                "is_pilot_count": sum(1 for r in rows if r["is_pilot"]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Done: {len(rows)} accredited HE orgs -> {OUT_CSV}")
    print(f"  with domain: {sum(1 for r in rows if r['domain'])}")
    print(f"  is_pilot: {sum(1 for r in rows if r['is_pilot'])}")


if __name__ == "__main__":
    main()

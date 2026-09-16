#!/usr/bin/env python3
"""Fetch specialty recipes → candidates JSONL; optional vault writes.

Verticals: pi, accounting, dentist, insurance, real-estate, hvac, roofing,
medical, engineering, family, immigration.
"""
from __future__ import annotations

import argparse
import os
import csv
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path
import html as htmlmod
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from fetch_recipe import fetch_recipe, load_reg
from note_io import (
    DEFAULT_VAULT,
    default_city,
    DIRECTORY_HOSTS,
    find_by_firm_phone,
    fmt_phone,
    fm_get,
    host_blocked,
    new_business_note,
    new_law_note,
    update_note_fields,
    website_value,
)

SKIP_NAME_RE = re.compile(
    r"(ticket|traffic ticket|district attorney|county court|"
    r"farmers insurance|texas tech|university health|unnamed|lawyer directory|"
    r"\bh\s*&\s*r\s*block\b|\bhr\s*block\b|jackson\s*hewitt|liberty\s*tax|"
    r"\bstate\s*farm\b|\ballstate\b|\bgeico\b|\bprogressive\b|\bnationwide\b|"
    r"\baspen\s*dental\b|\bheartland\s*dental\b|\bwestern\s*dental\b|"
    r"\bre/?max\b|\bcentury\s*21\b|\bcoldwell\b|\bkeller\s*williams\b|"
    r"\bberkshire\s*hathaway\b|\bbetter\s*homes\b|\bexp\s*realty\b|"
    r"\bredfin\b|\bcompass\s*real\b|"
    r"\bapartments?\b|\bapartment\s+homes\b)",
    re.I,
)
CHAIN_RE = re.compile(
    r"\b(morgan & morgan|"
    r"h\s*&\s*r\s*block|hr\s*block|jackson\s*hewitt|"
    r"liberty\s*tax|state\s*farm|allstate|geico|progressive|nationwide|"
    r"aspen\s*dental|heartland\s*dental|"
    r"re/?max|century\s*21|coldwell\s*banker|keller\s*williams|"
    r"berkshire\s*hathaway|better\s*homes\s*and\s*gardens|exp\s*realty)\b",
    re.I,
)
NATIONAL_PHONE_PREFIXES = ("800", "888", "877", "866")

def city_allow() -> set[str] | None:
    raw = os.environ.get("GEO_CITIES", "").strip()
    if not raw:
        return None
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def city_allowed(city: str) -> bool:
    allow = city_allow()
    if allow is None:
        return True
    return (city or "").strip().lower() in allow

VERTICAL_CATEGORY = {
    "pi": "Law",
    "accounting": "Accounting",
    "dentist": "Dentist",
    "insurance": "Insurance",
    "real-estate": "Real estate",
    "hvac": "HVAC",
    "roofing": "Roofing",
    "medical": "Medical",
    "engineering": "Engineering",
    "family": "Law",
    "immigration": "Law",
}

VERTICAL_PRACTICE = {
    "pi": "personal-injury",
    "family": "family",
    "immigration": "immigration",
}

VERTICAL_SOURCE_LABEL = {
    "pi": "Justia",
    "accounting": "Comptroller",
    "dentist": "OpenStreetMap",
    "insurance": "OpenStreetMap",
    "real-estate": "TREC",
    "hvac": "TDLR",
    "roofing": "Comptroller",
    "medical": "OpenStreetMap",
    "engineering": "Comptroller",
    "family": "Justia",
    "immigration": "Justia",
}

# NPPES taxonomy desc fragments to KEEP (org medical practices)
NPPES_KEEP_RE = re.compile(
    r"(family\s*medicine|internal\s*medicine|pediatric|clinic|physician|"
    r"multi-?specialty|urgent\s*care|primary\s*care|cardiology|dermatology|"
    r"orthop|obstetrics|gynecolog|ophthalm|neurolog|psychiatr|urolog|"
    r"gastroenter|endocrin|rheumat|nephrolog|pulmon|oncolog|surgery|"
    r"otolaryng|allerg|radiolog|anesthes|patholog|emergency\s*medicine|"
    r"general\s*practice|doctor|medical\s*group|health\s*center)",
    re.I,
)
NPPES_SKIP_RE = re.compile(
    r"(skilled\s*nursing|nursing\s*facility|pharmacy|laboratory|ambulance|"
    r"home\s*health|hospice|durable\s*medical|dialysis|imaging\s*center|"
    r"dental|chiropract|optometr|podiatr|physical\s*therap|occupational\s*therap|"
    r"speech|massage|acupuncture|hearing\s*aid|prosthetic)",
    re.I,
)

# Comptroller 23822 mixes plumbing + HVAC — keep HVAC-ish names for hvac vertical
HVAC_NAME_RE = re.compile(
    r"(hvac|h\.?v\.?a\.?c|air\s*cond|a/?c\b|heating|cooling|furnace|refrigerat|"
    r"climate|mechanical\s*(air|heat)|heat\s*&\s*air|heating\s*&\s*air)",
    re.I,
)
PLUMBING_ONLY_RE = re.compile(
    r"^\s*(.+?\s+)?(plumbing|plumber|pipe\s*fit|drain|sewer|water\s*heater)\s*(.+)?$",
    re.I,
)


def _addr_str(addr) -> str:
    if not addr:
        return ""
    if isinstance(addr, str):
        return addr
    street = addr.get("streetAddress") or ""
    if isinstance(street, list):
        street = ", ".join(street)
    parts = [
        street,
        addr.get("addressLocality") or "",
        addr.get("addressRegion") or "",
        addr.get("postalCode") or "",
    ]
    return ", ".join(p for p in parts if p)


def _city_from_addr(addr) -> str:
    if isinstance(addr, dict):
        loc = (addr.get("addressLocality") or "").strip()
        if loc:
            return loc
    return default_city()


def _title_case_city(city: str) -> str:
    c = (city or "").strip()
    if not c:
        return default_city()
    return c.title()


def skip_candidate(name: str, phone: str, address: str, vertical: str = "") -> str | None:
    if not name or len(name.strip()) < 3:
        return "unnamed"
    if name.strip().lower() in {"office", "law office", "law offices", "the office"}:
        return "generic_office_label"
    if re.fullmatch(r"(the\s+)?(law\s+)?offices?", name.strip(), re.I):
        return "generic_office_label"
    if SKIP_NAME_RE.search(name):
        return "junk_name"
    if CHAIN_RE.search(name):
        return "chain"
    if re.search(r"\b(mexico|méxico|,\s*mx\b)", address or "", re.I) or re.search(r"\b(mexico|méxico)\b", name or "", re.I):
        return "out_of_country"
    if re.search(r"\b(body\s*shop|collision|auto\s*body|paint\s*shop|dent\s*punish)\b", name, re.I):
        return "junk_name"
    ph = re.sub(r"\D", "", phone or "")
    if len(ph) == 11 and ph.startswith("1"):
        ph = ph[1:]
    if len(ph) == 10 and ph[:3] in NATIONAL_PHONE_PREFIXES:
        return "national_phone"
    if phone and ("+52" in str(phone) or str(phone).strip().startswith("52")):
        return "mexico_phone"
    if re.search(r"\bcirujano\s+dentista\b", name or "", re.I):
        return "junk_name"
    # roofing supply / materials only
    if vertical == "roofing" and re.search(r"\b(supply|supplies|materials|wholesale)\b", name, re.I):
        if not re.search(r"\b(roofing\s+co|roofers?|roofing\s+(inc|llc|corp))\b", name, re.I):
            return "supply_not_contractor"
    return None


def city_from_source_url(source_url: str) -> str:
    m = re.search(r"/([^/]+)/(texas|tx)\b", (source_url or "").lower())
    if m:
        return _title_case_city(m.group(1).replace("-", " "))
    return default_city()


def parse_justia_html(html: str, recipe_id: str, source_url: str) -> list[dict]:
    out = []
    scripts = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    )
    people = []
    for s in scripts:
        try:
            d = json.loads(s)
        except Exception:
            continue
        if isinstance(d, list) and d and isinstance(d[0], dict) and d[0].get("@type") == "Person":
            people = d
            break
    seen = set()
    for person in people:
        wl = person.get("workLocation") or {}
        if isinstance(wl, list):
            wl = wl[0] if wl else {}
        if not (isinstance(wl, dict) and wl.get("@type") == "LegalService" and wl.get("name")):
            continue
        firm = htmlmod.unescape(wl.get("name") or "").strip()
        phone = wl.get("telephone") or person.get("telephone") or ""
        address = _addr_str(wl.get("address"))
        city = _city_from_addr(wl.get("address"))
        key = (firm.lower(), fmt_phone(phone))
        if key in seen:
            continue
        seen.add(key)
        profile = person.get("url") or ""
        cand = {
            "firm": firm,
            "attorney": htmlmod.unescape(person.get("name") or ""),
            "phone": fmt_phone(phone) or phone,
            "address": address,
            "city": city,
            "profile_url": profile,
            "candidate_website": "",
            "recipe": recipe_id,
            "source_url": source_url,
            "source": "Justia",
        }
        out.append(cand)
    return out


def parse_findlaw_html(html: str, recipe_id: str, source_url: str) -> list[dict]:
    out = []
    for m in re.finditer(
        r'(?:website-button-link|Visit Website)[^>]{0,200}href=["\'](https?://[^"\']+)["\']|'
        r'href=["\'](https?://[^"\']+)["\'][^>]{0,200}(?:website-button-link|Visit Website)',
        html,
        re.I,
    ):
        url = m.group(1) or m.group(2)
        if host_blocked(url):
            continue
        start = max(0, m.start() - 500)
        chunk = html[start : m.end() + 100]
        name_m = re.search(r'(?:firm-name|attorney-name|org-name)[^>]*>\s*([^<]{3,80})', chunk, re.I)
        if not name_m:
            name_m = re.search(r'<h[12][^>]*>\s*([^<]{3,80})', chunk, re.I)
        firm = (name_m.group(1).strip() if name_m else "") or urlparse(url).netloc
        phone_m = re.search(r'tel:([+\d\-() ]+)', chunk)
        phone = phone_m.group(1) if phone_m else ""
        out.append(
            {
                "firm": firm,
                "attorney": "",
                "phone": fmt_phone(phone) or phone,
                "address": "",
                "city": city_from_source_url(source_url),
                "profile_url": "",
                "candidate_website": url.split("?")[0],
                "recipe": recipe_id,
                "source_url": source_url,
                "source": "FindLaw",
            }
        )
    if not out:
        for m in re.finditer(r'<h[23][^>]*>\s*([^<]{3,100})</h[23]>', html, re.I):
            firm = re.sub(r"\s+", " ", m.group(1)).strip()
            chunk = html[m.start() : m.start() + 800]
            phone_m = re.search(r'tel:([+\d\-() ]+)', chunk)
            phone = phone_m.group(1) if phone_m else ""
            if not phone:
                continue
            out.append(
                {
                    "firm": firm,
                    "attorney": "",
                    "phone": fmt_phone(phone) or phone,
                    "address": "",
                    "city": city_from_source_url(source_url),
                    "profile_url": "",
                    "candidate_website": "",
                    "recipe": recipe_id,
                    "source_url": source_url,
                    "source": "FindLaw",
                }
            )
    return out


def enrich_justia_profile_websites(cands: list[dict], limit_profiles: int = 0) -> None:
    from urllib.request import Request, urlopen

    n = 0
    for c in cands:
        if c.get("candidate_website") or not c.get("profile_url"):
            continue
        if limit_profiles and n >= limit_profiles:
            break
        url = c["profile_url"].split("?")[0]
        if "/contact" in url:
            url = url.replace("/contact", "")
        try:
            req = Request(url, headers={"User-Agent": "OutreachTools/1.0", "Accept": "text/html"})
            raw = urlopen(req, timeout=25).read(200_000).decode("utf-8", "ignore")
        except Exception:
            continue
        n += 1
        sites = []
        for pat in (
            r'data-vars-action=["\']ProfileWebsite["\'][^>]*href=["\']([^"\']+)',
            r'href=["\']([^"\']+)["\'][^>]*data-vars-action=["\']ProfileWebsite["\']',
            r'rel=["\']me["\'][^>]*href=["\']([^"\']+)',
            r'href=["\']([^"\']+)["\'][^>]*rel=["\']me["\']',
        ):
            sites.extend(re.findall(pat, raw, re.I))
        for s in sites:
            if s.startswith("/"):
                s = urljoin(url, s)
            if host_blocked(s):
                continue
            c["candidate_website"] = s.split("?")[0]
            break


def parse_soda_json(raw: bytes | str, recipe_id: str, source_url: str, vertical: str = "") -> list[dict]:
    """Parse Texas Comptroller SODA JSON rows → candidates."""
    try:
        rows = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Route TREC / TDLR shaped rows
        if "license_type" in row and "full_name" in row:
            continue  # handled by parse_trec_json
        if "license_type" in row and "business_name" in row:
            continue  # handled by parse_tdlr_json
        firm = (row.get("outlet_name") or row.get("taxpayer_name") or "").strip()
        if not firm:
            continue
        if vertical == "hvac" and recipe_id.startswith("comptroller_hvac"):
            # 23822 mixes plumbing — prefer HVAC-ish; drop clear plumbing-only
            if PLUMBING_ONLY_RE.search(firm) and not HVAC_NAME_RE.search(firm):
                continue
            if not HVAC_NAME_RE.search(firm):
                # keep if NAICS implies HVAC specialty via name tokens weak — drop generic plumbers
                if re.search(r"\bplumb", firm, re.I):
                    continue
        city_raw = (row.get("outlet_city") or row.get("taxpayer_city") or "").strip()
        if city_raw and not city_allowed(city_raw):
            continue
        city = _title_case_city(city_raw) if city_raw else default_city()
        street = (row.get("outlet_address") or row.get("taxpayer_address") or "").strip()
        zipc = (row.get("outlet_zip_code") or row.get("taxpayer_zip_code") or "").strip()
        state = (row.get("outlet_state") or row.get("taxpayer_state") or "TX").strip()
        address = ", ".join(p for p in [street, city, state, zipc] if p)
        phone = ""
        key = (firm.lower(), city.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "firm": firm,
                "attorney": "",
                "phone": phone,
                "address": address,
                "city": city,
                "profile_url": "",
                "candidate_website": "",
                "recipe": recipe_id,
                "source_url": source_url,
                "source": "Comptroller",
                "naics": str(row.get("outlet_naics_code") or ""),
            }
        )
    return out


def parse_trec_json(raw: bytes | str, recipe_id: str, source_url: str) -> list[dict]:
    """TREC Broker/Sales SODA → firm candidates (Broker Company preferred)."""
    try:
        rows = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        lt = (row.get("license_type") or "").strip().lower()
        status = (row.get("status") or "").strip().lower()
        if status and status != "active":
            continue
        # Prefer Broker Company firms; skip Sales Agent individuals
        if "sales agent" in lt:
            continue
        if "broker company" not in lt and lt != "broker":
            # allow Broker Individual only if no company — skip individuals
            if "individual" in lt:
                continue
        firm = (row.get("full_name") or row.get("key_name") or "").strip()
        if not firm:
            continue
        county = (row.get("county") or "").strip()
        city = _title_case_city(county) if county else default_city()
        key = firm.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "firm": firm,
                "attorney": "",
                "phone": "",
                "address": f"{city}, TX" if city else "",
                "city": city,
                "profile_url": "",
                "candidate_website": "",
                "recipe": recipe_id,
                "source_url": source_url,
                "source": "TREC",
                "license": row.get("license_number") or "",
            }
        )
    return out


def parse_tdlr_json(raw: bytes | str, recipe_id: str, source_url: str) -> list[dict]:
    """TDLR All Licenses SODA → contractor firms."""
    try:
        rows = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        lt = (row.get("license_type") or "").strip()
        # Skip technicians / apprentices
        if re.search(r"technician|apprentice|journeyman|registrant", lt, re.I):
            continue
        firm = (row.get("business_name") or row.get("owner_name") or "").strip()
        if not firm:
            continue
        # Skip person-looking LAST, FIRST when owner == business for technicians already skipped
        phone = (row.get("business_telephone") or row.get("owner_telephone") or "").strip()
        csz = (row.get("business_city_state_zip") or row.get("mailing_address_city_state_zip") or "").strip()
        street = (row.get("business_address_line1") or row.get("mailing_address_line1") or "").strip()
        city = default_city()
        m = re.search(r"([A-Za-z .]+),\s*([A-Z]{2})\s*(\d{5})?", csz)
        if m:
            city = _title_case_city(m.group(1).strip())
        county = (row.get("business_county") or "").strip()
        if not m and county:
            city = _title_case_city(county)
        address = ", ".join(p for p in [street, csz or f"{city}, TX"] if p)
        key = (firm.lower(), fmt_phone(phone) or street.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "firm": firm,
                "attorney": "",
                "phone": fmt_phone(phone) or phone,
                "address": address,
                "city": city,
                "profile_url": "",
                "candidate_website": "",
                "recipe": recipe_id,
                "source_url": source_url,
                "source": "TDLR",
                "license": row.get("license_number") or "",
            }
        )
    return out


def parse_overpass_json(raw: bytes | str, recipe_id: str, source_url: str) -> list[dict]:
    """Parse Overpass JSON elements tags → candidates."""
    try:
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
    except Exception:
        return []
    els = data.get("elements") if isinstance(data, dict) else None
    if not isinstance(els, list):
        return []
    out = []
    seen = set()
    for el in els:
        tags = el.get("tags") or {}
        if not isinstance(tags, dict):
            continue
        firm = (tags.get("name") or tags.get("operator") or "").strip()
        if not firm:
            continue
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
        if website and host_blocked(website):
            website = ""
        city_raw = (
            tags.get("addr:city")
            or tags.get("addr:suburb")
            or tags.get("is_in:city")
            or ""
        ).strip()
        city = _title_case_city(city_raw) if city_raw else default_city()
        street = " ".join(
            p
            for p in [
                tags.get("addr:housenumber") or "",
                tags.get("addr:street") or "",
            ]
            if p
        ).strip()
        zipc = tags.get("addr:postcode") or ""
        state = tags.get("addr:state") or ""
        address = ", ".join(p for p in [street, city, state, zipc] if p)
        key = (firm.lower(), fmt_phone(phone) or street.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "firm": firm,
                "attorney": "",
                "phone": fmt_phone(phone) or phone,
                "address": address,
                "city": city,
                "profile_url": "",
                "candidate_website": website.split("?")[0] if website else "",
                "recipe": recipe_id,
                "source_url": source_url,
                "source": "OpenStreetMap",
                "osm_id": f"{el.get('type', 'node')}/{el.get('id', '')}",
            }
        )
    return out


def parse_tsbde_csv(raw: bytes | str, recipe_id: str, source_url: str) -> list[dict]:
    """Parse TSBDE Dentist.csv if downloadable; filter to region cities."""
    text = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    fields_lower = {f.lower().strip(): f for f in reader.fieldnames if f}

    def col(*names):
        for n in names:
            if n.lower() in fields_lower:
                return fields_lower[n.lower()]
        return None

    name_c = col("Business Name", "Practice Name", "Facility Name", "Name", "Licensee Name", "DBA")
    first_c = col("First Name", "First")
    last_c = col("Last Name", "Last")
    city_c = col("City", "Practice City", "Business City", "Mailing City", "City Name")
    phone_c = col("Phone", "Telephone", "Business Phone", "Practice Phone")
    addr_c = col("Address", "Street", "Practice Address", "Business Address", "Address1", "Mailing Address")
    zip_c = col("Zip", "Zip Code", "ZIP", "Postal Code", "Practice Zip")
    out = []
    seen = set()
    for row in reader:
        city_raw = (row.get(city_c) or "").strip() if city_c else ""
        if city_raw and not city_allowed(city_raw):
            continue
        firm = ""
        if name_c:
            firm = (row.get(name_c) or "").strip()
        if not firm and first_c and last_c:
            firm = f"Dr. {(row.get(first_c) or '').strip()} {(row.get(last_c) or '').strip()}".strip()
        if not firm and last_c:
            firm = (row.get(last_c) or "").strip()
        if not firm:
            continue
        phone = (row.get(phone_c) or "").strip() if phone_c else ""
        street = (row.get(addr_c) or "").strip() if addr_c else ""
        zipc = (row.get(zip_c) or "").strip() if zip_c else ""
        city = _title_case_city(city_raw)
        address = ", ".join(p for p in [street, city, "TX", zipc] if p)
        key = (firm.lower(), fmt_phone(phone) or street.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "firm": firm,
                "attorney": "",
                "phone": fmt_phone(phone) or phone,
                "address": address,
                "city": city,
                "profile_url": "",
                "candidate_website": "",
                "recipe": recipe_id,
                "source_url": source_url,
                "source": "TSBDE",
            }
        )
    return out


def parse_nppes_json(raw: bytes | str, recipe_id: str, source_url: str) -> list[dict]:
    """NPPES Registry API JSON → medical org candidates."""
    try:
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
    except Exception:
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    out = []
    seen = set()
    for row in results:
        if not isinstance(row, dict):
            continue
        basic = row.get("basic") or {}
        firm = (basic.get("organization_name") or "").strip()
        # Prefer DBA other_names
        for on in row.get("other_names") or []:
            if isinstance(on, dict) and on.get("organization_name"):
                if (on.get("type") or "").lower().startswith("doing") or on.get("code") == "3":
                    firm = on["organization_name"].strip()
                    break
        if not firm:
            continue
        taxonomies = row.get("taxonomies") or []
        tax_text = " ".join(
            str(t.get("desc") or "") for t in taxonomies if isinstance(t, dict)
        )
        if NPPES_SKIP_RE.search(tax_text) and not NPPES_KEEP_RE.search(tax_text):
            continue
        if tax_text and not NPPES_KEEP_RE.search(tax_text):
            # keep if no taxonomy text issues — unknown orgs skipped when clearly non-medical
            if NPPES_SKIP_RE.search(firm):
                continue
            # soft: require keep match when taxonomy present
            continue
        locs = [a for a in (row.get("addresses") or []) if isinstance(a, dict)]
        loc = next((a for a in locs if a.get("address_purpose") == "LOCATION"), None) or (
            locs[0] if locs else {}
        )
        phone = loc.get("telephone_number") or basic.get("authorized_official_telephone_number") or ""
        city_raw = (loc.get("city") or default_city()).strip()
        if city_raw and not city_allowed(city_raw):
            continue
        city = _title_case_city(city_raw)
        street = (loc.get("address_1") or "").strip()
        zipc = (loc.get("postal_code") or "")[:5]
        state = loc.get("state") or "TX"
        address = ", ".join(p for p in [street, city, state, zipc] if p)
        key = (firm.lower(), fmt_phone(phone) or street.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "firm": firm,
                "attorney": "",
                "phone": fmt_phone(phone) or phone,
                "address": address,
                "city": city,
                "profile_url": "",
                "candidate_website": "",
                "recipe": recipe_id,
                "source_url": source_url,
                "source": "NPPES",
                "npi": str(row.get("number") or ""),
            }
        )
    return out


VERTICAL_RECIPES = {
    "pi": [
        "justia_pi_texas",
        "justia_pi_texas_p2",
        "findlaw_pi_texas",
    ],
    "accounting": [
        "comptroller_cpa_texas",
        "osm_accountants",
    ],
    "dentist": [
        "osm_dentists",
        "tsbde_dentists",
    ],
    "insurance": [
        "osm_insurance",
        "comptroller_insurance_texas",
    ],
    "real-estate": [
        "osm_real_estate",
        "trec_broker_companies_texas",
        "comptroller_real_estate_texas",
    ],
    "hvac": [
        "osm_hvac",
        "tdlr_ac_contractors_texas",
        "comptroller_hvac_texas",
    ],
    "roofing": [
        "osm_roofing",
        "comptroller_roofing_texas",
    ],
    "medical": [
        "osm_medical",
        "nppes_orgs_texas",
    ],
    "engineering": [
        "osm_engineering",
        "comptroller_engineering_texas",
        # tbpe_pe_roster intentionally omitted — no city/county columns
    ],
    "family": [
        "justia_family_texas",
        "justia_family_texas_p2",
    ],
    "immigration": [
        "justia_immigration_texas",
    ],
}

ALL_VERTICALS = list(VERTICAL_RECIPES.keys())


def write_prove(vault: Path, vertical: str, body: str) -> Path:
    day = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    path = vault / "Sources" / f"specialty-{vertical}-{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def parse_recipe(rid: str, raw: bytes, src: dict, vertical: str = "") -> list[dict]:
    kind = src.get("kind", "")
    url = src.get("url", "")
    parse_hint = src.get("parse") or []
    if isinstance(parse_hint, str):
        parse_hint = [parse_hint]

    if kind == "nppes" or rid.startswith("nppes_"):
        return parse_nppes_json(raw, rid, url)
    if "trec" in parse_hint or rid.startswith("trec_"):
        return parse_trec_json(raw, rid, url)
    if "tdlr" in parse_hint or rid.startswith("tdlr_"):
        return parse_tdlr_json(raw, rid, url)
    if kind == "soda":
        # detect TREC/TDLR by payload shape
        try:
            rows = json.loads(raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw)
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                if "full_name" in rows[0] and "license_type" in rows[0]:
                    return parse_trec_json(raw, rid, url)
                if "business_name" in rows[0] and "license_type" in rows[0]:
                    return parse_tdlr_json(raw, rid, url)
        except Exception:
            pass
        return parse_soda_json(raw, rid, url, vertical=vertical)
    if kind == "overpass":
        return parse_overpass_json(raw, rid, url)
    if kind == "csv" or rid.startswith("tsbde_"):
        return parse_tsbde_csv(raw, rid, url)
    if kind == "zip_csv" or rid.startswith("tbpe_"):
        # PE roster has no city — return empty with note left to rejects
        return []
    html = raw.decode("utf-8", "ignore")
    if "justia" in rid:
        return parse_justia_html(html, rid, url)
    if "findlaw" in rid:
        return parse_findlaw_html(html, rid, url)
    if raw[:1] in (b"[", b"{"):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return parse_soda_json(raw, rid, url, vertical=vertical)
            if isinstance(data, dict) and "elements" in data:
                return parse_overpass_json(raw, rid, url)
            if isinstance(data, dict) and "results" in data:
                return parse_nppes_json(raw, rid, url)
        except Exception:
            pass
    return parse_justia_html(html, rid, url)


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="specialty-directory")
    ap.add_argument(
        "--vertical",
        default="pi",
        choices=ALL_VERTICALS,
    )
    ap.add_argument("--geo", default="texas")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--dry-run", action="store_true", help="candidates JSONL only; no vault writes")
    ap.add_argument("--no-push", action="store_true", help="skip immediate GitHub backup (debug/tests only)")
    ap.add_argument("--out", default="", help="candidates JSONL path")
    ap.add_argument("--limit", type=int, default=0, help="max candidates to keep after parse")
    ap.add_argument("--fetch-profiles", type=int, default=0, help="fetch N Justia profiles for websites")
    ap.add_argument("--recipes", default="", help="comma recipe ids (override vertical map)")
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    reg, _ = load_reg()
    recipe_ids = [r.strip() for r in args.recipes.split(",") if r.strip()] or VERTICAL_RECIPES.get(
        args.vertical, []
    )
    if not recipe_ids:
        print(f"no recipes for vertical {args.vertical}", file=sys.stderr)
        return 1

    all_cands: list[dict] = []
    rejects: list[str] = []
    for rid in recipe_ids:
        if rid not in reg["sources"]:
            rejects.append(f"missing_recipe:{rid}")
            continue
        src = reg["sources"][rid]
        try:
            raw = fetch_recipe(rid, reg)
        except Exception as e:
            rejects.append(f"fetch_fail:{rid}:{e}")
            continue
        chunk = parse_recipe(rid, raw, src, vertical=args.vertical)
        if not chunk and src.get("kind") == "zip_csv":
            rejects.append(f"skip_no_geo:{rid}:PE roster has no city/county columns")
        all_cands.extend(chunk)

    if args.fetch_profiles and args.vertical in {"pi", "family", "immigration"}:
        enrich_justia_profile_websites(all_cands, limit_profiles=args.fetch_profiles)

    filtered = []
    for c in all_cands:
        reason = skip_candidate(
            c.get("firm", ""), c.get("phone", ""), c.get("address", ""), vertical=args.vertical
        )
        if reason:
            rejects.append(f"{reason}:{c.get('firm')}")
            continue
        if c.get("candidate_website") and host_blocked(c["candidate_website"]):
            c["candidate_website"] = ""
        filtered.append(c)

    deduped = []
    seen = set()
    for c in filtered:
        key = (c["firm"].lower().strip(), fmt_phone(c.get("phone") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    if args.limit:
        deduped = deduped[: args.limit]

    day = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    runs = vault / "Sources" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else runs / f"specialty-{args.vertical}-{day}.jsonl"
    tmp_path = Path("/tmp") / out_path.name
    for dest in (out_path, tmp_path):
        with dest.open("w", encoding="utf-8") as f:
            for c in deduped:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    category = VERTICAL_CATEGORY.get(args.vertical, "Law")
    practice = VERTICAL_PRACTICE.get(args.vertical)
    is_law = category == "Law"
    created = 0
    updated = 0
    skipped_existing = 0
    # Build firm/phone index once (find_by_firm_phone alone is O(n*m) over large vault)
    name_index: dict[str, Path] = {}
    phone_index: dict[str, Path] = {}
    if not args.dry_run:
        from note_io import iter_notes, norm_name
        for pth in iter_notes(vault):
            try:
                tx = pth.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            nn = norm_name(fm_get(tx, "name"))
            ph = fmt_phone(fm_get(tx, "phone"))
            if nn and nn not in name_index:
                name_index[nn] = pth
            if ph and ph not in phone_index:
                phone_index[ph] = pth

        def _find_existing(firm: str, phone: str):
            want = norm_name(firm)
            want_phone = fmt_phone(phone) if phone else ""
            if want_phone and want_phone in phone_index:
                hit = phone_index[want_phone]
                hit_name = norm_name(fm_get(hit.read_text(encoding="utf-8", errors="replace"), "name"))
                if want == hit_name or (want and want in hit_name) or (hit_name and hit_name in want):
                    return hit
            if want and want in name_index:
                return name_index[want]
            # partial containment scan on name keys (bounded)
            if want and len(want) >= 5:
                for k, v in name_index.items():
                    if want in k or k in want:
                        return v
            return None

        for c in deduped:
            existing = _find_existing(c["firm"], c.get("phone") or "")
            if existing:
                fields = {}
                text = existing.read_text(encoding="utf-8", errors="replace")
                if c.get("phone") and not fm_get(text, "phone"):
                    fields["phone"] = c["phone"]
                if c.get("address") and not fm_get(text, "address"):
                    fields["address"] = c["address"]
                if is_law and practice:
                    if "practice" not in text.lower() or not fm_get(text, "practice"):
                        fields["practice"] = practice
                if c.get("candidate_website") and not website_value(text) and not host_blocked(c["candidate_website"]):
                    if c.get("source") == "OpenStreetMap":
                        fields["website"] = c["candidate_website"]
                if fields:
                    update_note_fields(existing, fields)
                    updated += 1
                else:
                    skipped_existing += 1
                if c.get("candidate_website") and not website_value(text) and "candidate_website:" not in text:
                    if c.get("source") != "OpenStreetMap":
                        research = f"\n- candidate_website: {c['candidate_website']} (from {c['recipe']}; not verified)\n"
                        existing.write_text(text.rstrip() + research, encoding="utf-8")
            else:
                if is_law:
                    new_law_note(
                        c["firm"],
                        phone=c.get("phone") or "",
                        address=c.get("address") or "",
                        city=c.get("city") or default_city(),
                        practice=practice or "personal-injury",
                        source=c.get("source") or "Justia",
                        notes=f"specialty-directory {args.vertical} {c.get('recipe')}",
                        vault=vault,
                    )
                else:
                    site = ""
                    if c.get("candidate_website") and c.get("source") == "OpenStreetMap":
                        site = c["candidate_website"]
                    new_business_note(
                        category,
                        c["firm"],
                        phone=c.get("phone") or "",
                        address=c.get("address") or "",
                        city=c.get("city") or default_city(),
                        website=site,
                        source=c.get("source") or VERTICAL_SOURCE_LABEL.get(args.vertical, "specialty-directory"),
                        notes=f"specialty-directory {args.vertical} {c.get('recipe')}",
                        vault=vault,
                    )
                created += 1
                nn = norm_name(c["firm"])
                if nn:
                    # re-resolve path for index
                    from note_io import slugify as _slug
                    # best-effort: use _find after write
                    np = _find_existing(c["firm"], c.get("phone") or "")
                    if np is None:
                        # freshly written — locate by slug
                        from note_io import vault_biz
                        biz = vault_biz(vault)
                        candp = biz / f"{_slug(c['firm'])}.md"
                        if candp.exists():
                            np = candp
                    if np:
                        name_index[nn] = np
                        phn = fmt_phone(c.get("phone") or "")
                        if phn:
                            phone_index[phn] = np
                if c.get("candidate_website") and c.get("source") != "OpenStreetMap":
                    p = _find_existing(c["firm"], c.get("phone") or "")
                    if p:
                        t = p.read_text(encoding="utf-8", errors="replace")
                        p.write_text(
                            t.rstrip()
                            + f"\n- candidate_website: {c['candidate_website']} (from {c['recipe']}; not verified)\n",
                            encoding="utf-8",
                        )

    prove = f"""---
type: source
name: "specialty-{args.vertical}-{day}"
---

# Prove log — specialty-{args.vertical} — {day}

## Goal
Specialty-directory ingest for {args.vertical} / {args.geo}

## Mode
specialty-directory

## Counts
| Metric | Value |
|---|---|
| recipes | {len(recipe_ids)} |
| candidates | {len(deduped)} |
| created | {created} |
| updated | {updated} |
| skipped_existing | {skipped_existing} |
| rejects | {len(rejects)} |
| dry_run | {args.dry_run} |

## Sources / recipes used
{chr(10).join('- `'+r+'`' for r in recipe_ids)}
- UA: OutreachTools/1.0

## Sample (up to 15)
{chr(10).join(str(i+1)+'. '+c['firm']+' | '+ (c.get('phone') or '') for i,c in enumerate(deduped[:15]))}

## Rejects / skips (sample)
{chr(10).join('- '+r for r in rejects[:40])}

## Blind-spot check
- Matrix row: {category}
- Specialty required? see coverage.json — ran specialty-directory: Y
- Artifacts: `{out_path}`

## Artifacts
- JSONL: `{out_path}`
- tmp: `{tmp_path}`
"""
    prove_path = write_prove(vault, args.vertical, prove)
    print(f"candidates\t{len(deduped)}\t{out_path}")
    print(f"created\t{created}\tupdated\t{updated}\tskipped\t{skipped_existing}")
    print(f"prove\t{prove_path}")
    print(f"rejects\t{len(rejects)}")

    mutated = (created + updated) > 0 and not args.dry_run
    try:
        from push_backup import maybe_after_mutation

        maybe_after_mutation(
            mutated=mutated,
            dry_run=args.dry_run,
            no_push=args.no_push,
            vault=vault,
        )
    except Exception as e:
        print(f"push-backup_error\t{e}", file=sys.stderr)

    try:
        from coverage_check import evaluate

        cov_cat = category
        prac = None
        if args.vertical == "pi":
            prac = "pi"
        elif args.vertical in {"family", "immigration"}:
            prac = args.vertical
        rc = evaluate(cov_cat, prac, vault)
        print(f"coverage-check_exit\t{rc}")
    except Exception as e:
        print(f"coverage-check_error\t{e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""Shared vault note read/write/dedupe helpers for outreach-leads."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

UA = "OutreachTools/1.0"


def default_vault() -> Path:
    raw = os.environ.get("VAULT_ROOT") or os.environ.get("OUTREACH_VAULT")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.cwd() / "data").resolve()


def default_city() -> str:
    return os.environ.get("DEFAULT_CITY", "Texas")


DEFAULT_VAULT = default_vault()

DIRECTORY_HOSTS = (
    "justia.com",
    "findlaw.com",
    "lawyers.com",
    "avvo.com",
    "facebook.com",
    "yelp.com",
    "linkedin.com",
    "superpages.com",
    "google.com",
    "maps.google",
    "yellowpages.com",
    "bbb.org",
    "wikipedia.org",
    "wixsite.com",
    "martindale.com",
    "usattorneys.com",
)


def vault_biz(vault: Path | None = None) -> Path:
    return (vault or DEFAULT_VAULT) / "Businesses"


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").upper()
    s = re.sub(
        r"\b(INCORPORATED|INC|LLC|L\.L\.C|CO|COMPANY|CORP|CORPORATION|LTD|LP|PLLC|PC|P\.C|PA|P\.A|THE|FOUNDATION|CHURCH|MINISTRIES|DBA|ATTORNEY|ATTORNEYS|LAW|FIRM|OFFICE|OFFICES)\b",
        "",
        s,
    )
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return " ".join(s.split())


def fmt_phone(raw) -> str:
    if raw is None:
        return ""
    d = re.sub(r"\D", "", str(raw))
    if d.startswith("1") and len(d) == 11:
        d = d[1:]
    return f"+1-{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else ""


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80] or "unnamed"


def parse_front(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm_raw, body = parts[1], parts[2]
    data: dict = {}
    for line in fm_raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip()] = v.strip().strip('"')
    return data, body


def fm_get(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*\"?([^\n\"]*)\"?\s*$", text, re.M)
    if not m:
        return ""
    return (m.group(1) or "").strip()


def website_value(text: str) -> str:
    """Return website URL or '' if missing/empty-string."""
    w = fm_get(text, "website")
    if not w or w.lower() in {"n/a", "none", "na", "null", "-"}:
        return ""
    return w


def category_has(text: str, category: str) -> bool:
    """Match category: \"[[Law]]\" or category: Law (wikilink-aware)."""
    raw = fm_get(text, "category")
    if not raw:
        return False
    cat = category.strip()
    if f"[[{cat}]]" in raw:
        return True
    # bare token equality (avoid Lawn matching Law via substring on bare text)
    bare = re.sub(r"[\[\]\"]", "", raw).strip()
    return bare == cat


def practice_is_pi(text: str) -> bool:
    prac = fm_get(text, "practice").lower()
    if prac in {"personal-injury", "pi", "trial"}:
        return True
    if "personal-injury" in prac or prac == "trial":
        return True
    name = fm_get(text, "name").lower()
    return any(x in name for x in ("personal injury", "injury law", "trial attorney", "trial lawyers", "trial law"))


def set_fm(text: str, key: str, val: str) -> str:
    """Set or insert a frontmatter key. Empty val writes key: \"\" for website."""
    if key == "website" and val == "":
        line = 'website: ""'
    elif val == "":
        line = f"{key}:"
    elif key in {"practice", "status", "type", "locally_owned", "research_status", "profile_score"} and not str(val).startswith("[["):
        if key == "locally_owned":
            line = f"{key}: {val}"
        elif key in {"practice", "status", "type", "research_status"}:
            line = f"{key}: {val}"
        else:
            line = f'{key}: "{val}"'
    else:
        # quote strings; preserve existing wikilink style for category/city/source
        if str(val).startswith("[[") or key in {"category", "city", "source"}:
            line = f'{key}: "{val}"' if not str(val).startswith('"') else f"{key}: {val}"
            if not line.endswith('"') and '"' not in val:
                line = f'{key}: "{val}"'
        else:
            line = f'{key}: "{val}"'
    if re.search(rf"^{re.escape(key)}:", text, re.M):
        return re.sub(rf"^{re.escape(key)}:[ \t]*[^\n]*$", line, text, count=1, flags=re.M)
    # insert before closing --- of frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1].rstrip("\n") + "\n" + line + "\n"
            return "---" + fm + "---" + parts[2]
    return text


def host_blocked(url: str) -> bool:
    from urllib.parse import urlparse

    if not url:
        return True
    u = url if re.match(r"^https?://", url, re.I) else "https://" + url
    host = urlparse(u).netloc.lower()
    return any(b in host for b in DIRECTORY_HOSTS)


def iter_notes(vault: Path | None = None):
    biz = vault_biz(vault)
    if not biz.is_dir():
        return
    for p in sorted(biz.glob("*.md")):
        yield p


def find_by_firm_phone(name: str, phone: str = "", vault: Path | None = None) -> Path | None:
    want = norm_name(name)
    want_phone = fmt_phone(phone) if phone else ""
    phone_hit = None
    name_hit = None
    for p in iter_notes(vault):
        text = p.read_text(encoding="utf-8", errors="replace")
        n = norm_name(fm_get(text, "name"))
        ph = fmt_phone(fm_get(text, "phone"))
        if want_phone and ph and ph == want_phone:
            if n == want or (want and want in n) or (n and n in want):
                return p
            phone_hit = phone_hit or p
        if want and n == want:
            name_hit = name_hit or p
    return name_hit or phone_hit


def new_law_note(
    name: str,
    *,
    phone: str = "",
    address: str = "",
    city: str = "",
    practice: str = "personal-injury",
    source: str = "Justia",
    notes: str = "",
    vault: Path | None = None,
) -> Path:
    biz = vault_biz(vault)
    biz.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    path = biz / f"{slug}.md"
    n = 2
    while path.exists():
        path = biz / f"{slug}-{n}.md"
        n += 1
    phone_fmt = fmt_phone(phone)
    status = "call-ready" if phone_fmt else "prospect"
    city = city or default_city()
    city_link = city if city.startswith("[[") else f"[[{city}]]"
    src_link = source if source.startswith("[[") else f"[[{source}]]"
    body_notes = notes or f"{source} specialty-directory ingest"
    text = f"""---
type: business
name: "{name}"
city: "{city_link}"
category: "[[Law]]"
practice: {practice}
phone: "{phone_fmt}"
website: ""
address: "{address}"
owner:
ein:
license:
locally_owned: true
status: {status}
source: "{src_link}"
research_status: pending
profile_score: "40"
last_called:
notes: "{body_notes}"
---

# {name}

Independent [[Law]] in {city_link}. Source: {src_link}.

- City: {city_link}
- Category: [[Law]]
- Practice: {practice}
- Phone: {phone_fmt}
- Website:

{body_notes}

## Angle
Why this firm might be a fit (ops, missed calls, paper intake).

## Research

## Contacts

## Call log
"""
    path.write_text(text, encoding="utf-8")
    return path


def new_business_note(
    category: str,
    name: str,
    *,
    phone: str = "",
    address: str = "",
    city: str = "",
    website: str = "",
    source: str = "specialty-directory",
    notes: str = "",
    vault: Path | None = None,
) -> Path:
    """Generic specialty note writer for Accounting / Dentist / Insurance (mirrors new_law_note)."""
    biz = vault_biz(vault)
    biz.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    path = biz / f"{slug}.md"
    n = 2
    while path.exists():
        path = biz / f"{slug}-{n}.md"
        n += 1
    phone_fmt = fmt_phone(phone)
    status = "call-ready" if phone_fmt else "prospect"
    city_link = city if city.startswith("[[") else f"[[{city}]]"
    src_link = source if source.startswith("[[") else f"[[{source}]]"
    cat = category.strip()
    if cat.startswith("[["):
        cat_link = cat
        cat_bare = re.sub(r"[\[\]]", "", cat)
    else:
        cat_link = f"[[{cat}]]"
        cat_bare = cat
    city = city or default_city()
    # Never write directory hosts as website
    site = ""
    if website and not host_blocked(website):
        site = website.strip()
    body_notes = notes or f"{source} specialty-directory ingest"
    # Escape quotes in address/notes for YAML
    address_q = (address or "").replace('"', "'")
    notes_q = body_notes.replace('"', "'")
    name_q = name.replace('"', "'")
    text = f"""---
type: business
name: "{name_q}"
city: "{city_link}"
category: "{cat_link}"
phone: "{phone_fmt}"
website: "{site}"
address: "{address_q}"
owner:
ein:
license:
locally_owned: true
status: {status}
source: "{src_link}"
research_status: pending
profile_score: "40"
last_called:
notes: "{notes_q}"
---

# {name_q}

Independent {cat_link} in {city_link}. Source: {src_link}.

- City: {city_link}
- Category: {cat_link}
- Phone: {phone_fmt}
- Website: {site}
- Address: {address_q}

{body_notes}

## Angle
Why this firm might be a fit (ops, missed calls, paper intake).

## Research

## Contacts

## Call log
"""
    path.write_text(text, encoding="utf-8")
    return path


def update_note_fields(path: Path, fields: dict) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    for k, v in fields.items():
        if v is None:
            continue
        if k == "website" and host_blocked(str(v)):
            continue
        if k == "phone":
            v = fmt_phone(v) or v
        # do not blank existing filled website
        if k == "website" and website_value(text) and not v:
            continue
        if k == "phone" and fm_get(text, "phone") and not v:
            continue
        text = set_fm(text, k, str(v))
    path.write_text(text, encoding="utf-8")

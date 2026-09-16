#!/usr/bin/env python3
"""Build named points-of-contact (POC) for firm research profiles.

Re-fetches attorney/team/about/people/staff/lawyers pages, extracts candidate
contacts (schema.org Person/Attorney, mailto near names, title patterns, email
local-part heuristics), ranks with poc_score, and writes:

  contacts[], best_poc, inbox_fallback → Research/firms/<slug>.json
  Research/firms/shortlists/<category-slug>-poc.json
  index.json best_poc summary fields (filtered by category when set)
  Sources/poc-rank-<category-slug>-YYYY-MM-DD.md prove log

Never invents emails. Same-domain only. UA: OutreachTools/1.0.
"""
from __future__ import annotations

import argparse
import os
import html as html_lib
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from zoneinfo import ZoneInfo

# Reuse hardened fetch + domain helpers from website_emails
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from note_io import DEFAULT_VAULT, UA  # noqa: E402
from website_emails import (  # noqa: E402
    EMAIL_RE,
    MAILTO_RE,
    FREE_MAIL_DOMAINS,
    host_of,
    http_get_with_retries,
    registrable_domain,
    same_registrable,
    normalize_url,
)

MT = ZoneInfo("America/Chicago")

SKIP_FIRM_JSON = frozenset({"index.json", "poc-shortlist.json"})


def category_slug(category: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (category or "all").strip().lower()).strip("-")
    return s or "all"


def norm_category(c: str | None) -> str:
    return (c or "").strip()


def profile_category(profile: dict) -> str:
    return norm_category(profile.get("category") or "")


def category_matches(profile: dict, want: str | None) -> bool:
    """If want empty/all, match everything. Else case-insensitive equality."""
    if not want or want.lower() in {"all", "*"}:
        return True
    have = profile_category(profile)
    if not have:
        return True  # legacy profiles without category still process when filtering
    return have.lower() == want.strip().lower()


PEOPLE_PATH_RE = re.compile(
    r"/(attorney|attorneys|lawyer|lawyers|team|our-team|people|staff|"
    r"about|about-us|our-firm|bio|bios|meet|leadership|partners?|"
    r"members?|professionals?)(/|$)",
    re.I,
)
PEOPLE_LABELS = {
    "attorney",
    "attorneys",
    "lawyer",
    "lawyers",
    "team",
    "about",
    "people",
    "staff",
}
BIO_PATH_RE = re.compile(
    r"/(attorney|attorneys|lawyer|lawyers|team|our-team|people|staff|"
    r"bio|bios|professionals?|our-attorneys)/[^/]+/?$"
    r"|/(attorney-profile|attorney-bio|lawyer-profile)/?$",
    re.I,
)
CAREERS_PRIVACY_RE = re.compile(
    r"/(career|careers|jobs?|employment|scholarship|privacy|terms|"
    r"cookie|disclaimer)(/|$)",
    re.I,
)

ROLE_INBOX_LOCALS = frozenset(
    {
        "info",
        "contact",
        "office",
        "admin",
        "hello",
        "marketing",
        "intake",
        "intakes",
        "team",
        "support",
        "frontdesk",
        "reception",
        "noreply",
        "no-reply",
        "donotreply",
        "webmaster",
        "media",
        "press",
        "hr",
        "billing",
        "accounting",
        "careers",
        "jobs",
        "help",
        "service",
        "services",
        "general",
        "mail",
        "email",
        "enquiries",
        "inquiry",
        "inquiries",
        "labinotiteam",
    }
)

OWNER_TITLE_RE = re.compile(
    r"\b(owner|founder|founding\s+partner|managing\s+partner|managing\s+attorney|"
    r"partner|principal|president|shareholder|co-?founder|of\s+counsel|"
    r"senior\s+partner|name\s+partner)\b",
    re.I,
)
TITLE_NEAR_RE = re.compile(
    r"\b(Esq\.?|Partner|Founder|Founding\s+Partner|Managing\s+Attorney|"
    r"Managing\s+Partner|Principal|President|Shareholder|Attorney\s+at\s+Law|"
    r"Of\s+Counsel|Associate|Counsel)\b",
    re.I,
)
# First Last (optional middle initial) with optional suffix
PERSON_NAME_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+)+)"
    r"(?:\s*,?\s*(?:Esq\.?|Jr\.?|Sr\.?|III|II|IV))?"
)
PHONE_RE = re.compile(
    r"(?:tel:|phone[:\s]*|call[:\s]*)?\+?1?[-.\s(]*\d{3}[-.\s)]*\d{3}[-.\s]*\d{4}",
    re.I,
)
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

COMMON_PEOPLE_PATHS = (
    "/attorneys",
    "/attorneys/",
    "/attorney",
    "/attorney/",
    "/lawyers",
    "/lawyers/",
    "/lawyer",
    "/team",
    "/team/",
    "/our-team",
    "/our-team/",
    "/people",
    "/people/",
    "/staff",
    "/staff/",
    "/about",
    "/about/",
    "/about-us",
    "/about-us/",
    "/our-firm",
    "/our-firm/",
    "/meet-our-team",
    "/meet-the-team",
    "/professionals",
    "/leadership",
)

FIRMISH_NAME_RE = re.compile(
    r"\b(law\s+firm|law\s+office|pllc|p\.?c\.?|llp|llc|associates|group|"
    r"legal|attorneys?\s+at\s+law|company|title|escrow)\b",
    re.I,
)


def vault_day() -> str:
    return datetime.now(MT).strftime("%Y-%m-%d")


def vault_stamp() -> str:
    return datetime.now(MT).strftime("%Y-%m-%d %H:%M %Z")


def vault_iso() -> str:
    return datetime.now(MT).isoformat(timespec="seconds")


def strip_tags(s: str) -> str:
    s = html_lib.unescape(s or "")
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    s = TAG_RE.sub(" ", s)
    return WS_RE.sub(" ", s).strip()


def email_local(email: str) -> str:
    return (email or "").split("@", 1)[0].lower().strip()


def is_role_inbox(email: str) -> bool:
    local = email_local(email)
    if local in ROLE_INBOX_LOCALS:
        return True
    # compound like office.manager still role-ish when starts with role token
    head = local.split(".", 1)[0].split("+", 1)[0]
    return head in ROLE_INBOX_LOCALS


def is_personal_looking_email(email: str) -> bool:
    if not email or "@" not in email:
        return False
    if is_role_inbox(email):
        return False
    local = email_local(email)
    if len(local) < 2:
        return False
    # first.last / first_last / flast / firstlast
    if re.match(r"^[a-z]{1,20}[._-][a-z]{1,20}$", local):
        return True
    if re.match(r"^[a-z]\.?[a-z]{2,20}$", local):  # jsmith / j.smith
        return True
    if re.match(r"^[a-z]{3,30}$", local) and local not in ROLE_INBOX_LOCALS:
        return True
    return False


def personal_email_bonus(email: str) -> int:
    """Heuristic score bump for local-part shape (not inventing)."""
    if not email or is_role_inbox(email):
        return 0
    local = email_local(email)
    if re.match(r"^[a-z]+\.[a-z]+$", local):
        return 3  # first.last
    if re.match(r"^[a-z]+[a-z]+$", local) and len(local) >= 5:
        return 2  # firstlast
    if re.match(r"^[a-z][a-z]{2,}$", local):
        return 2  # flast-ish
    if is_personal_looking_email(email):
        return 3
    return 0


def looks_like_person_name(name: str, firm_name: str = "") -> bool:
    name = WS_RE.sub(" ", (name or "").strip())
    if not name or len(name) < 3 or len(name) > 80:
        return False
    if FIRMISH_NAME_RE.search(name):
        return False
    if firm_name and name.lower().strip() == firm_name.lower().strip():
        return False
    # Practice-area / nav / section false positives
    if re.search(
        r"\b(accident|injury|truck|car|wrongful|death|practice|areas?|"
        r"attorneys?|lawyers?|positions?|past|meet|our|team|staff|"
        r"privacy|policy|contact|about|home|blog|news|results?|"
        r"testimonials?|faq|services?|free|consultation)\b",
        name,
        re.I,
    ):
        return False
    tokens = name.split()
    if len(tokens) < 2:
        return False
    if len(tokens) > 4:
        return False
    bad = {
        "the", "and", "of", "at", "law", "firm", "office", "offices",
        "attorney", "attorneys", "lawyer", "lawyers", "associate", "associates",
        "partner", "partners", "president", "founder", "managing",
    }
    for tok in tokens:
        if tok.lower().rstrip(".") in bad:
            return False
        if not re.match(r"^[A-Za-z][A-Za-z'.-]*$", tok):
            return False
    return True


def normalize_phone(raw: str) -> str | None:
    if not raw:
        return None
    d = re.sub(r"\D", "", raw)
    if d.startswith("1") and len(d) == 11:
        d = d[1:]
    if len(d) == 10:
        return f"+1-{d[:3]}-{d[3:6]}-{d[6:]}"
    return None


def page_is_careers_privacy(url: str) -> bool:
    return bool(CAREERS_PRIVACY_RE.search(urlparse(url).path or ""))


def page_is_peopleish(url: str, label: str | None = None) -> bool:
    if label and label.lower().strip() in PEOPLE_LABELS:
        return True
    path = urlparse(url).path or ""
    return bool(PEOPLE_PATH_RE.search(path))


def clean_email(raw: str) -> str | None:
    if not raw:
        return None
    addr = html_lib.unescape(raw).strip()
    addr = unquote(addr).split("?")[0].strip().rstrip(".,;:)>]\"'")
    addr = addr.replace("%40", "@")
    if "@" not in addr:
        return None
    e = addr.lower()
    if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")):
        return None
    if " " in e or e.count("@") != 1:
        return None
    local, domain = e.split("@", 1)
    if not local or not domain or "." not in domain:
        return None
    if domain in FREE_MAIL_DOMAINS:
        return None
    return e


def name_from_email_local(local: str) -> str | None:
    """Best-effort display name from first.last local — not inventing email."""
    local = (local or "").lower()
    if is_role_inbox(local + "@x.com") if False else False:
        return None
    if local in ROLE_INBOX_LOCALS:
        return None
    if "." in local:
        parts = [p for p in local.split(".") if p.isalpha() and 1 < len(p) < 20]
        if len(parts) >= 2:
            return " ".join(p.capitalize() for p in parts[:3])
    # flast → "F. Last" when plausible
    if re.match(r"^[a-z][a-z]{2,19}$", local) and local not in ROLE_INBOX_LOCALS:
        return f"{local[0].upper()}. {local[1:].capitalize()}"
    return None


def name_matches_email(name: str, email: str) -> bool:
    if not name or not email or "@" not in email:
        return False
    local = email_local(email)
    if is_role_inbox(email):
        return False
    tokens = [t.lower() for t in re.findall(r"[A-Za-z]+", name) if len(t) > 1]
    if len(tokens) < 2:
        return False
    first, last = tokens[0], tokens[-1]
    candidates = {
        first + last,
        first + "." + last,
        first + "_" + last,
        first[0] + last,
        first[0] + "." + last,
        last + first,
        last + "." + first,
    }
    return local in candidates or local.replace("-", "") in {
        c.replace(".", "").replace("_", "") for c in candidates
    }


class ContactAgg:
    def __init__(self) -> None:
        self.name: str | None = None
        self.title: str | None = None
        self.email: str | None = None
        self.phone: str | None = None
        self.profile_url: str | None = None
        self.source_pages: set[str] = set()
        self.kind: str = "inbox"  # person|inbox
        self.from_attorney_bio: bool = False
        self.from_careers_privacy: bool = False
        self.schema_person: bool = False
        self.from_linkedin: bool = False
        self.sources: set[str] = set()

    def key(self) -> str:
        if self.email:
            return f"email:{self.email.lower()}"
        if self.name:
            return f"name:{(self.name or '').lower()}"
        return f"page:{self.profile_url or id(self)}"

    def merge(self, other: "ContactAgg") -> None:
        if other.name and (
            not self.name
            or (looks_like_person_name(other.name) and not looks_like_person_name(self.name or ""))
        ):
            self.name = other.name
        if other.title and (not self.title or len(other.title) > len(self.title or "")):
            self.title = other.title
        if other.email and not self.email:
            self.email = other.email
        elif other.email and self.email and is_role_inbox(self.email) and not is_role_inbox(other.email):
            self.email = other.email
        if other.phone and not self.phone:
            self.phone = other.phone
        if other.profile_url and (
            not self.profile_url or BIO_PATH_RE.search(other.profile_url or "")
        ):
            self.profile_url = other.profile_url
        self.source_pages |= other.source_pages
        self.from_attorney_bio = self.from_attorney_bio or other.from_attorney_bio
        self.from_careers_privacy = self.from_careers_privacy or other.from_careers_privacy
        self.schema_person = self.schema_person or other.schema_person
        self.from_linkedin = self.from_linkedin or other.from_linkedin
        self.sources |= other.sources
        self._refresh_kind()

    def _refresh_kind(self) -> None:
        if self.name and looks_like_person_name(self.name):
            self.kind = "person"
        elif self.email and not is_role_inbox(self.email) and is_personal_looking_email(self.email):
            # personal email without confirmed name still person-ish if we can derive name
            derived = name_from_email_local(email_local(self.email))
            if derived:
                self.name = self.name or derived
                self.kind = "person"
            else:
                self.kind = "person" if self.name else "inbox"
        else:
            self.kind = "inbox"

    def poc_score(self) -> int:
        self._refresh_kind()
        score = 0
        if self.name and looks_like_person_name(self.name):
            score += 5
        title = self.title or ""
        if re.search(
            r"\b(owner|founder|founding\s+partner|managing\s+partner|managing\s+attorney|co-?founder)\b",
            title,
            re.I,
        ):
            score += 4
        elif re.search(
            r"\b(partner|principal|president|shareholder)\b",
            title,
            re.I,
        ):
            score += 3
        if self.email and is_personal_looking_email(self.email):
            score += 3
        elif self.email and personal_email_bonus(self.email) >= 2:
            score += 3
        if self.phone:
            score += 2
        if self.from_attorney_bio or (
            self.profile_url and BIO_PATH_RE.search(self.profile_url or "")
        ):
            score += 1
        if self.from_linkedin and self.name and looks_like_person_name(self.name):
            score += 1
        if self.email and is_role_inbox(self.email):
            score -= 3
        if self.from_careers_privacy:
            score -= 2
        return score

    def to_dict(self) -> dict:
        self._refresh_kind()
        sources = sorted(self.sources) if self.sources else (
            ["linkedin"] if self.from_linkedin else ["website"]
        )
        primary = "linkedin" if self.from_linkedin and "website" not in sources else (
            "website+linkedin" if "linkedin" in sources and "website" in sources else sources[0]
        )
        return {
            "name": self.name,
            "title": self.title,
            "email": self.email,
            "phone": self.phone,
            "profile_url": self.profile_url,
            "source_pages": sorted(self.source_pages),
            "source": primary,
            "sources": sources,
            "schema_person": bool(self.schema_person),
            "poc_score": self.poc_score(),
            "kind": self.kind,
        }


def walk_jsonld(obj, out: list[dict], *, under_review: bool = False) -> None:
    if isinstance(obj, dict):
        t = obj.get("@type")
        types = t if isinstance(t, list) else ([t] if t else [])
        types = [str(x) for x in types]
        type_tails = {x.split("/")[-1] for x in types}
        # Skip review / rating author Persons (client testimonials ≠ firm POCs)
        is_reviewish = bool(type_tails & {"Review", "AggregateRating", "Rating"})
        next_review = under_review or is_reviewish
        interesting = {"Person", "Attorney"}
        if type_tails & interesting and not next_review:
            # Also skip if @id/url looks like a review anchor
            url = str(obj.get("url") or obj.get("@id") or "")
            if "#review" not in url.lower():
                out.append(obj)
        for k, v in obj.items():
            # author/review under Review objects
            key_review = next_review or k in {"review", "reviews", "author"}
            walk_jsonld(v, out, under_review=key_review)
    elif isinstance(obj, list):
        for v in obj:
            walk_jsonld(v, out, under_review=under_review)


def extract_schema_people(html: str, page_url: str, site_reg: str, firm_name: str) -> list[ContactAgg]:
    found: list[ContactAgg] = []
    for m in JSONLD_RE.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        people: list[dict] = []
        walk_jsonld(data, people)
        for person in people:
            name = person.get("name")
            if isinstance(name, dict):
                name = name.get("name") or name.get("@value")
            if isinstance(name, list):
                name = " ".join(str(x) for x in name if x)
            name = strip_tags(str(name or "")).strip() or None
            title = person.get("jobTitle") or person.get("title")
            if isinstance(title, list):
                title = ", ".join(str(x) for x in title if x)
            title = strip_tags(str(title or "")).strip() or None
            email = None
            em = person.get("email")
            if isinstance(em, list):
                em = em[0] if em else None
            if isinstance(em, str):
                if em.lower().startswith("mailto:"):
                    em = em.split(":", 1)[1]
                email = clean_email(em)
            if email and not same_registrable(email.split("@", 1)[-1], site_reg):
                email = None
            phone = None
            tel = person.get("telephone") or person.get("phone")
            if isinstance(tel, list):
                tel = tel[0] if tel else None
            if isinstance(tel, str):
                phone = normalize_phone(tel)
            url = person.get("url") or person.get("@id")
            if isinstance(url, str) and url.startswith("http"):
                profile = url
            else:
                profile = page_url
            # Prefer human names; skip firm-as-Person
            if name and not looks_like_person_name(name, firm_name):
                # description may hold a person name
                desc = strip_tags(str(person.get("description") or ""))
                mname = PERSON_NAME_RE.search(desc[:200]) if desc else None
                if mname and looks_like_person_name(mname.group(1), firm_name):
                    name = mname.group(1)
                else:
                    name = None
            if not name and not email:
                continue
            c = ContactAgg()
            c.name = name
            c.title = title
            c.email = email
            c.phone = phone
            c.profile_url = profile
            c.source_pages.add(page_url)
            c.schema_person = True
            c.from_attorney_bio = bool(BIO_PATH_RE.search(urlparse(page_url).path or ""))
            c.from_careers_privacy = page_is_careers_privacy(page_url)
            c._refresh_kind()
            found.append(c)
    return found


def window_around(text: str, start: int, end: int, radius: int = 220) -> str:
    a = max(0, start - radius)
    b = min(len(text), end + radius)
    return text[a:b]


def extract_pattern_contacts(html: str, page_url: str, site_reg: str, firm_name: str) -> list[ContactAgg]:
    """mailto near names + First Last, Esq./Partner patterns + nearby email/tel."""
    found: list[ContactAgg] = []
    text = strip_tags(html)
    # Build list of email spans in stripped text
    email_spans: list[tuple[int, int, str]] = []
    for m in EMAIL_RE.finditer(text):
        e = clean_email(m.group(1))
        if not e:
            continue
        if not same_registrable(e.split("@", 1)[-1], site_reg):
            continue
        email_spans.append((m.start(), m.end(), e))

    # mailto from raw html (positions approximate via search in text)
    for m in MAILTO_RE.finditer(html or ""):
        e = clean_email(m.group(1))
        if not e:
            continue
        if not same_registrable(e.split("@", 1)[-1], site_reg):
            continue
        idx = text.lower().find(e.lower())
        if idx < 0:
            email_spans.append((0, 0, e))
        else:
            email_spans.append((idx, idx + len(e), e))

    # Dedupe email spans by address keeping first
    seen_e: set[str] = set()
    uniq_spans: list[tuple[int, int, str]] = []
    for s, e, addr in email_spans:
        if addr in seen_e:
            continue
        seen_e.add(addr)
        uniq_spans.append((s, e, addr))

    is_bio = bool(BIO_PATH_RE.search(urlparse(page_url).path or ""))
    for start, end, email in uniq_spans:
        ctx = window_around(text, start, end, 160 if not is_bio else 280) if end > start else text[:400]
        name = None
        title = None
        # Strict: only bind a name when local-part matches, or single bio-page candidate
        matched = []
        for nm in PERSON_NAME_RE.finditer(ctx):
            candidate = nm.group(1).strip()
            if not looks_like_person_name(candidate, firm_name):
                continue
            if name_matches_email(candidate, email):
                matched.append(candidate)
        if matched:
            name = matched[0]
        elif is_bio and not is_role_inbox(email):
            # On a bio URL, allow the first plausible person name in a tight window
            for nm in PERSON_NAME_RE.finditer(ctx):
                candidate = nm.group(1).strip()
                if looks_like_person_name(candidate, firm_name):
                    name = candidate
                    break
        elif is_personal_looking_email(email):
            # Derive display name from first.last local — never invent the email itself
            name = name_from_email_local(email_local(email))
        # Title near email (only when we have a bound name or bio page)
        if name or is_bio:
            tm2 = OWNER_TITLE_RE.search(ctx)
            if tm2:
                title = tm2.group(0)
            else:
                tm = TITLE_NEAR_RE.search(ctx)
                if tm:
                    title = tm.group(0)
        phone = None
        pm = PHONE_RE.search(ctx)
        if pm:
            phone = normalize_phone(pm.group(0))
        c = ContactAgg()
        c.name = name
        c.title = title
        c.email = email
        c.phone = phone
        c.profile_url = page_url
        c.source_pages.add(page_url)
        c.from_attorney_bio = is_bio
        c.from_careers_privacy = page_is_careers_privacy(page_url)
        if not name and is_role_inbox(email):
            c.kind = "inbox"
        else:
            c._refresh_kind()
        found.append(c)

    # Title patterns without email: "First Last, Partner" / "First Last, Esq."
    for m in re.finditer(
        r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+)+)\s*,\s*"
        r"(Esq\.?|Partner|Founder|Founding\s+Partner|Managing\s+Partner|"
        r"Managing\s+Attorney|Principal|President|Shareholder|Attorney\s+at\s+Law)",
        text,
    ):
        name = m.group(1).strip()
        title = m.group(2).strip()
        if not looks_like_person_name(name, firm_name):
            continue
        # nearby email — only if local-part matches this name (avoid listing-page mixups)
        ctx = window_around(text, m.start(), m.end(), 300)
        email = None
        for em in EMAIL_RE.findall(ctx):
            e = clean_email(em)
            if not e or not same_registrable(e.split("@", 1)[-1], site_reg):
                continue
            if name_matches_email(name, e):
                email = e
                break
            # role inbox on bio page OK to attach
            if is_bio and is_role_inbox(e):
                email = e
        phone = None
        pm = PHONE_RE.search(ctx)
        if pm:
            phone = normalize_phone(pm.group(0))
        c = ContactAgg()
        c.name = name
        c.title = title
        c.email = email
        c.phone = phone
        c.profile_url = page_url
        c.source_pages.add(page_url)
        c.from_attorney_bio = bool(BIO_PATH_RE.search(urlparse(page_url).path or ""))
        c.from_careers_privacy = page_is_careers_privacy(page_url)
        c._refresh_kind()
        found.append(c)

    return found



TEL_HREF_RE = re.compile(r"""href=["']tel:([^"']+)["']""", re.I)
TEL_BARE_RE = re.compile(r"""tel:([+\d][\d().\s\-%]{7,22})""", re.I)
SCHEMA_TEL_RE = re.compile(r'"telephone"\s*:\s*"([^"]+)"', re.I)
def local_area_codes() -> frozenset[str]:
    raw = os.environ.get("LOCAL_AREA_CODES", "")
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def prefer_local_phone(phones: list[str]) -> str | None:
    """Prefer LOCAL_AREA_CODES when set; otherwise first verified phone."""
    normed = []
    for p in phones:
        n = normalize_phone(p)
        if n:
            normed.append(n)
    if not normed:
        return None
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for n in normed:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    codes = local_area_codes()
    local = [n for n in uniq if codes and n[3:6] in codes]
    return (local or uniq)[0]


def bio_subject_name(html: str, page_url: str, firm_name: str) -> str | None:
    """Best-effort person name for an attorney bio URL — from slug, title, or h1."""
    path = urlparse(page_url).path or ""
    # slug after last people-ish segment
    m = re.search(
        r"/(?:attorney|attorneys|lawyer|lawyers|team|our-team|people|staff|"
        r"bio|bios|professionals?|our-attorneys)/([^/]+)/?$",
        path,
        re.I,
    )
    slug = unquote(m.group(1)) if m else ""
    slug = re.sub(r"[-_]+", " ", slug).strip()
    slug_name = None
    if slug and not re.search(r"\d", slug):
        cand = " ".join(w.capitalize() for w in slug.split() if w)
        # Kenneth-g-egan → Kenneth G Egan
        parts = []
        for w in slug.replace(".", " ").split():
            if len(w) == 1:
                parts.append(w.upper() + ".")
            else:
                parts.append(w.capitalize())
        cand = " ".join(parts)
        if looks_like_person_name(cand, firm_name):
            slug_name = cand

    text = strip_tags(html)
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.I | re.S)
    # Prefer URL slug name when present — titles often truncate accents
    # (e.g. "David J. Muñoz" → "David J. Mu") and break merge keys.
    if slug_name:
        return slug_name
    for raw in (
        strip_tags(h1_m.group(1)) if h1_m else "",
        strip_tags(title_m.group(1)) if title_m else "",
        text[:300],
    ):
        if not raw:
            continue
        for nm in PERSON_NAME_RE.finditer(raw):
            candidate = nm.group(1).strip()
            if looks_like_person_name(candidate, firm_name):
                return candidate
    return None


def extract_page_phones(html: str) -> list[str]:
    """All tel: / schema telephone / labeled phone strings on a page (raw)."""
    raw: list[str] = []
    for m in TEL_HREF_RE.finditer(html or ""):
        raw.append(unquote(m.group(1)))
    for m in TEL_BARE_RE.finditer(html or ""):
        raw.append(unquote(m.group(1)))
    for m in SCHEMA_TEL_RE.finditer(html or ""):
        raw.append(m.group(1))
    # labeled visible phones
    text = strip_tags(html)
    for m in re.finditer(
        r"(?:phone|call|tel|office|fax)\s*[:#]?\s*(\+?1?[-.\s(]*\d{3}[-.\s)]*\d{3}[-.\s]*\d{4})",
        text,
        re.I,
    ):
        raw.append(m.group(1))
    return raw


def extract_bio_page_phones(
    html: str, page_url: str, site_reg: str, firm_name: str
) -> list[ContactAgg]:
    """On attorney bio pages, attach page phones to the bio subject.

    Firm footer/header numbers often sit outside name windows; tel: on a bio URL
    is still a verified association for that attorney (prefer local area codes).
    """
    path = urlparse(page_url).path or ""
    is_bio = bool(BIO_PATH_RE.search(path))
    # Soft bio: attorney-profile / about pages with a single dominant person name
    if not is_bio:
        soft = bool(
            re.search(r"/(attorney-profile|lawyer-profile|attorney-bio)/?$", path, re.I)
        )
        if not soft:
            return []
        is_bio = True
    name = bio_subject_name(html, page_url, firm_name)
    if not name:
        return []
    phone = prefer_local_phone(extract_page_phones(html))
    if not phone:
        return []
    c = ContactAgg()
    c.name = name
    c.phone = phone
    c.profile_url = page_url
    c.source_pages.add(page_url)
    c.from_attorney_bio = True
    c.from_careers_privacy = page_is_careers_privacy(page_url)
    c.sources.add("website")
    c._refresh_kind()
    return [c]


def extract_same_site_bio_links(html: str, page_url: str, site_reg: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in HREF_RE.finditer(html or ""):
        href = m.group(1).strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        try:
            nu = normalize_url(href, page_url)
        except Exception:
            continue
        if registrable_domain(host_of(nu)) != site_reg:
            continue
        path = urlparse(nu).path or ""
        if not (BIO_PATH_RE.search(path) or PEOPLE_PATH_RE.search(path)):
            continue
        key = nu.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(nu)
    return out


def discover_seed_urls(profile: dict, run: dict | None) -> list[str]:
    website = (profile.get("website") or "").strip()
    if not website:
        return []
    if not re.match(r"^https?://", website, re.I):
        website = "https://" + website
    site_host = host_of(website)
    site_reg = registrable_domain(site_host)
    base = website.rstrip("/")
    # If website path is deep (practice page), use origin
    p = urlparse(website)
    origin = f"{p.scheme}://{p.netloc}"

    found: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        if not u:
            return
        try:
            nu = normalize_url(u, origin)
        except Exception:
            return
        if registrable_domain(host_of(nu)) != site_reg:
            return
        key = nu.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        found.append(nu)

    add(origin + "/")
    for path in COMMON_PEOPLE_PATHS:
        add(origin + path)

    for kp in profile.get("key_pages") or []:
        label = (kp.get("label") or "").lower()
        url = kp.get("url")
        if page_is_peopleish(url or "", label) or label in PEOPLE_LABELS:
            add(url)

    for em in profile.get("emails") or []:
        for pg in em.get("pages") or []:
            if page_is_peopleish(pg):
                add(pg)

    if run:
        for pg in run.get("pages") or []:
            if pg.get("status") not in (200, "200", None):
                # still consider URL if path peopleish for re-fetch
                pass
            url = pg.get("final_url") or pg.get("url")
            if url and page_is_peopleish(url):
                add(url)
            # outbound samples from people pages
            if url and page_is_peopleish(url):
                for link in pg.get("outbound_same_site_sample") or []:
                    if page_is_peopleish(link) or BIO_PATH_RE.search(urlparse(link).path or ""):
                        add(link)

    # Prefer people paths first
    def seed_score(u: str) -> tuple[int, str]:
        path = urlparse(u).path or "/"
        score = 0
        if BIO_PATH_RE.search(path):
            score += 5
        if PEOPLE_PATH_RE.search(path):
            score += 3
        if CAREERS_PRIVACY_RE.search(path):
            score -= 5
        return (-score, u)

    found.sort(key=seed_score)
    return found


def load_research_run(vault: Path, profile: dict) -> dict | None:
    sources = profile.get("sources") or {}
    rel = sources.get("research_json")
    candidates: list[Path] = []
    if rel:
        candidates.append(vault / rel)
    slug = profile.get("slug") or ""
    if slug:
        runs = vault / "Sources" / "runs"
        candidates.extend(sorted(runs.glob(f"research-{slug}-*.json"), reverse=True))
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def fetch_pages(urls: list[str], *, max_pages: int = 25, workers: int = 4) -> list[dict]:
    urls = urls[:max_pages]
    results: list[dict] = []

    def one(u: str) -> dict:
        r = http_get_with_retries(u)
        return {
            "url": u,
            "final_url": r.get("final_url") or u,
            "status": r.get("http_code") or 0,
            "fetch_status": r.get("status"),
            "body": r.get("body") or "" if r.get("status") == "ok" else "",
            "error": r.get("error"),
            "attempts": r.get("attempts"),
        }

    if not urls:
        return results
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(one, u): u for u in urls}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                u = futs[fut]
                results.append(
                    {
                        "url": u,
                        "final_url": u,
                        "status": 0,
                        "fetch_status": "error",
                        "body": "",
                        "error": str(e),
                        "attempts": 0,
                    }
                )
    # stable-ish order by original
    order = {u: i for i, u in enumerate(urls)}
    results.sort(key=lambda r: order.get(r["url"], 999))
    return results


def merge_contacts(contacts: list[ContactAgg]) -> list[ContactAgg]:
    """Merge carefully: personal emails key identity; role inboxes do NOT collapse people."""
    by_personal_email: dict[str, ContactAgg] = {}
    by_name: dict[str, ContactAgg] = {}
    inboxes: dict[str, ContactAgg] = {}
    orphan: list[ContactAgg] = []

    for c in contacts:
        c._refresh_kind()
        if c.name and looks_like_person_name(c.name):
            k = c.name.lower()
            if k in by_name:
                # Prefer richer title / bio when merging same person
                by_name[k].merge(c)
                # If both have role inbox email, keep it; if one has personal, prefer personal (merge handles)
            else:
                by_name[k] = c
            continue
        if c.email and is_personal_looking_email(c.email):
            k = c.email.lower()
            if k in by_personal_email:
                by_personal_email[k].merge(c)
            else:
                by_personal_email[k] = c
            continue
        if c.email and is_role_inbox(c.email):
            k = c.email.lower()
            if k in inboxes:
                inboxes[k].merge(c)
            else:
                # Strip accidental non-person name
                c.name = None
                c.kind = "inbox"
                inboxes[k] = c
            continue
        orphan.append(c)

    # Attach personal-email-only rows into matching named people
    for ek, ec in list(by_personal_email.items()):
        matched = None
        for nk, nc in by_name.items():
            if name_matches_email(nc.name or "", ek):
                matched = nk
                break
            if nc.email and nc.email.lower() == ek:
                matched = nk
                break
        if matched:
            by_name[matched].merge(ec)
            del by_personal_email[ek]

    # Named people that only share a role inbox: keep name identity; ensure inbox also listed
    for nc in by_name.values():
        if nc.email and is_role_inbox(nc.email):
            k = nc.email.lower()
            if k not in inboxes:
                ib = ContactAgg()
                ib.email = nc.email
                ib.source_pages |= set(nc.source_pages)
                ib.kind = "inbox"
                inboxes[k] = ib

    merged = list(by_name.values()) + list(by_personal_email.values()) + list(inboxes.values()) + orphan
    final: dict[str, ContactAgg] = {}
    for c in merged:
        if c.name and looks_like_person_name(c.name):
            key = f"name:{c.name.lower()}"
        elif c.email:
            key = f"email:{c.email.lower()}"
        else:
            key = c.key()
        if key in final:
            final[key].merge(c)
        else:
            final[key] = c
    return list(final.values())


def pick_best_poc(contacts: list[ContactAgg]) -> ContactAgg | None:
    if not contacts:
        return None

    def rank_key(c: ContactAgg) -> tuple:
        personal = bool(c.email and not is_role_inbox(c.email) and is_personal_looking_email(c.email))
        owner = bool(OWNER_TITLE_RE.search(c.title or ""))
        founderish = bool(re.search(r"\b(founder|founding|owner|managing)\b", c.title or "", re.I))
        role_email = bool(c.email and is_role_inbox(c.email))
        return (
            1 if c.kind == "person" else 0,
            1 if personal else 0,
            1 if founderish else 0,
            c.poc_score(),
            1 if owner else 0,
            0 if role_email else (1 if c.email else 0),
            1 if c.phone else 0,
        )

    ranked = sorted(contacts, key=rank_key, reverse=True)
    # Prefer named person over pure inbox even if inbox somehow scores higher after penalties
    persons = [c for c in ranked if c.kind == "person"]
    if persons:
        # Prefer person with personal email
        personal_persons = [
            c for c in persons if c.email and not is_role_inbox(c.email)
        ]
        if personal_persons:
            return sorted(personal_persons, key=rank_key, reverse=True)[0]
        return persons[0]
    return ranked[0]


def pick_inbox_fallback(contacts: list[ContactAgg], profile_emails: list[dict]) -> dict | None:
    # Prefer role inbox with most pages
    candidates: list[tuple[int, str, list[str]]] = []
    seen: set[str] = set()
    for c in contacts:
        if c.email and is_role_inbox(c.email) and c.email not in seen:
            seen.add(c.email)
            candidates.append((len(c.source_pages), c.email, sorted(c.source_pages)))
    for em in profile_emails or []:
        e = (em.get("email") or "").lower()
        if e and is_role_inbox(e) and e not in seen:
            seen.add(e)
            pages = list(em.get("pages") or [])
            candidates.append((len(pages), e, pages))
    if not candidates:
        # any email as weak fallback
        for c in contacts:
            if c.email and c.email not in seen:
                return {"email": c.email, "pages": sorted(c.source_pages)}
        for em in profile_emails or []:
            if em.get("email"):
                return {"email": em["email"].lower(), "pages": list(em.get("pages") or [])}
        return None
    candidates.sort(key=lambda x: -x[0])
    _, email, pages = candidates[0]
    return {"email": email, "pages": pages}



def load_linkedin_results(vault: Path) -> dict[str, dict]:
    """slug -> latest linkedin-results row (queue/apply artifacts)."""
    runs = vault / "Sources" / "runs"
    by_slug: dict[str, dict] = {}
    if not runs.is_dir():
        return by_slug
    for path in sorted(runs.glob("linkedin-results-*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = (row.get("slug") or "").strip()
            if not slug and row.get("path"):
                slug = Path(str(row["path"])).stem
            if slug:
                by_slug[slug] = row
    return by_slug


def parse_note_contacts(note_text: str) -> list[dict]:
    """Parse ## Contacts bullets: Name — Title — … — email — profile_url."""
    m = re.search(r"^##\s+Contacts\s*$", note_text, re.M)
    if not m:
        return []
    rest = note_text[m.end() :]
    next_h = re.search(r"^##\s+", rest, re.M)
    body = rest[: next_h.start()] if next_h else rest
    out: list[dict] = []
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("-"):
            continue
        s = s[1:].strip()
        if not s:
            continue
        parts = [p.strip() for p in s.split("—") if p.strip()]
        if not parts:
            continue
        name = parts[0]
        title = parts[1] if len(parts) > 1 else None
        email = None
        profile_url = None
        phone = None
        for part in parts[2:]:
            if "@" in part and " " not in part.strip():
                email = clean_email(part)
            elif "linkedin.com" in part.lower() or part.startswith("http"):
                profile_url = part.strip()
            elif re.search(r"\d{3}", part):
                phone = normalize_phone(part) or phone
        out.append(
            {
                "name": name,
                "title": title,
                "email": email,
                "phone": phone,
                "profile_url": profile_url,
            }
        )
    return out


def linkedin_contacts_from_row(row: dict) -> tuple[list[ContactAgg], dict]:
    """Build ContactAgg list + linkedin status meta from a results row."""
    contacts_out: list[ContactAgg] = []
    li_url = (row.get("linkedin_company") or "").strip() or None
    raw = row.get("contacts") or []
    masked = 0
    named = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip() or None
        title = (item.get("title") or "").strip() or None
        email = clean_email(item.get("email") or "")
        phone = normalize_phone(item.get("phone") or "") if item.get("phone") else None
        profile_url = (item.get("profile_url") or "").strip() or None
        is_masked = (not name) or name.lower() in {
            "linkedin member",
            "linkedin member…",
            "member",
            "linkedIn member".lower(),
        } or name.lower().startswith("linkedin member")
        if is_masked:
            masked += 1
            # Keep title-only signal as inbox-ish lead, not a named POC
            c = ContactAgg()
            c.name = None
            c.title = title
            c.email = email
            c.phone = phone
            c.profile_url = profile_url or li_url
            c.from_linkedin = True
            c.sources.add("linkedin")
            if li_url:
                c.source_pages.add(li_url)
            c.kind = "inbox"
            if title or email:
                contacts_out.append(c)
            continue
        if name and not looks_like_person_name(name):
            continue
        named += 1
        c = ContactAgg()
        c.name = name
        c.title = title
        c.email = email
        c.phone = phone
        c.profile_url = profile_url or li_url
        c.from_linkedin = True
        c.sources.add("linkedin")
        if li_url:
            c.source_pages.add(li_url)
        if profile_url:
            c.source_pages.add(profile_url)
        c._refresh_kind()
        contacts_out.append(c)
    # explicit owner string
    owner = (row.get("owner") or "").strip()
    if owner and looks_like_person_name(owner):
        c = ContactAgg()
        c.name = owner
        c.title = c.title or "Owner"
        c.from_linkedin = True
        c.sources.add("linkedin")
        if li_url:
            c.source_pages.add(li_url)
        c._refresh_kind()
        contacts_out.append(c)
        named += 1

    if named:
        status = "linked"
    elif masked:
        status = "names_masked"
    elif li_url:
        status = "linked_no_people"
    else:
        status = "not_linked_yet"
    meta = {
        "company_url": li_url,
        "status": status,
        "contacts_named": named,
        "contacts_masked": masked,
        "from": "linkedin-results",
    }
    return contacts_out, meta


def resolve_linkedin_for_firm(
    vault: Path,
    profile: dict,
    li_by_slug: dict[str, dict],
) -> tuple[list[ContactAgg], dict]:
    slug = profile.get("slug") or ""
    existing = profile.get("linkedin") or {}
    li_url = existing.get("company_url")
    note_rel = (profile.get("sources") or {}).get("note")
    note_text = ""
    note_path = vault / note_rel if note_rel else None
    if note_path and note_path.exists():
        note_text = note_path.read_text(encoding="utf-8", errors="replace")
        # frontmatter linkedin_company
        m = re.search(r'^linkedin_company:\s*"?([^"\n]+)"?\s*$', note_text, re.M)
        if m:
            li_url = li_url or m.group(1).strip()

    row = li_by_slug.get(slug)
    contacts: list[ContactAgg] = []
    meta = {
        "company_url": li_url,
        "status": existing.get("status") or ("linked" if li_url else "not_linked_yet"),
    }

    if row:
        contacts, meta = linkedin_contacts_from_row(row)
        if not meta.get("company_url"):
            meta["company_url"] = li_url
    elif note_text:
        # Parse applied ## Contacts (may be from prior LinkedIn apply)
        parsed = parse_note_contacts(note_text)
        masked = named = 0
        for item in parsed:
            name = item.get("name")
            is_masked = (not name) or str(name).lower().startswith("linkedin member")
            c = ContactAgg()
            c.from_linkedin = True
            c.sources.add("linkedin")
            if li_url:
                c.source_pages.add(li_url)
            if is_masked:
                masked += 1
                c.name = None
                c.title = item.get("title")
                c.email = item.get("email")
                c.phone = item.get("phone")
                c.profile_url = item.get("profile_url") or li_url
                c.kind = "inbox"
            else:
                if not looks_like_person_name(name or ""):
                    continue
                named += 1
                c.name = name
                c.title = item.get("title")
                c.email = item.get("email")
                c.phone = item.get("phone")
                c.profile_url = item.get("profile_url") or li_url
                c._refresh_kind()
            contacts.append(c)
        if named:
            meta = {"company_url": li_url, "status": "linked", "contacts_named": named, "contacts_masked": masked, "from": "note-contacts"}
        elif masked:
            meta = {"company_url": li_url, "status": "names_masked", "contacts_named": 0, "contacts_masked": masked, "from": "note-contacts"}
        elif li_url:
            meta = {"company_url": li_url, "status": "linked_no_people", "from": "note-fm"}
    elif li_url:
        meta = {"company_url": li_url, "status": "pending_people_lookup", "from": "company_url_only"}
    else:
        meta = {"company_url": None, "status": "not_linked_yet", "from": "none"}

    return contacts, meta


def append_linkedin_queue(vault: Path, rows: list[dict]) -> Path | None:
    if not rows:
        return None
    day = vault_day()
    runs = vault / "Sources" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / f"linkedin-queue-{day}.jsonl"
    # append unique by slug
    existing: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("slug") or "")
            except Exception:
                pass
    with out.open("a", encoding="utf-8") as f:
        for r in rows:
            if r.get("slug") in existing:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            existing.add(r.get("slug") or "")
    return out


def process_firm(
    vault: Path,
    profile_path: Path,
    *,
    max_pages: int = 25,
    workers: int = 4,
    dry_run: bool = False,
    skip_fetch: bool = False,
    li_by_slug: dict[str, dict] | None = None,
) -> dict:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    slug = profile.get("slug") or profile_path.stem
    firm_name = profile.get("name") or slug
    website = profile.get("website") or ""
    site_reg = registrable_domain(host_of(website)) if website else ""

    run = load_research_run(vault, profile)
    seeds = discover_seed_urls(profile, run)

    if skip_fetch:
        pages = []
    else:
        # First wave fetch
        pages = fetch_pages(seeds, max_pages=max_pages, workers=workers)

    # Expand bio links from successful people listing pages
    extra: list[str] = []
    have = {normalize_url(p["url"]).rstrip("/").lower() for p in pages}
    for p in pages:
        if p.get("fetch_status") != "ok" or not p.get("body"):
            continue
        url = p.get("final_url") or p["url"]
        if not page_is_peopleish(url):
            continue
        for link in extract_same_site_bio_links(p["body"], url, site_reg):
            key = link.rstrip("/").lower()
            if key not in have:
                have.add(key)
                extra.append(link)
    # Cap expansion
    remaining = max(0, max_pages - len(pages))
    if extra and remaining:
        extra_sorted = sorted(
            extra,
            key=lambda u: (0 if BIO_PATH_RE.search(urlparse(u).path or "") else 1, u),
        )
        pages.extend(fetch_pages(extra_sorted[:remaining], max_pages=remaining, workers=workers))

    raw_contacts: list[ContactAgg] = []
    pages_ok = 0
    for p in pages:
        if p.get("fetch_status") != "ok" or not p.get("body"):
            continue
        pages_ok += 1
        url = p.get("final_url") or p["url"]
        body = p["body"]
        raw_contacts.extend(extract_schema_people(body, url, site_reg, firm_name))
        raw_contacts.extend(extract_pattern_contacts(body, url, site_reg, firm_name))
        raw_contacts.extend(extract_bio_page_phones(body, url, site_reg, firm_name))

    # Also harvest profile emails list as inbox/person seeds (no invention — already extracted)
    for em in profile.get("emails") or []:
        e = clean_email(em.get("email") or "")
        if not e:
            continue
        if site_reg and not same_registrable(e.split("@", 1)[-1], site_reg):
            continue
        c = ContactAgg()
        c.email = e
        for pg in em.get("pages") or []:
            c.source_pages.add(pg)
            if BIO_PATH_RE.search(urlparse(pg).path or ""):
                c.from_attorney_bio = True
            if page_is_careers_privacy(pg):
                c.from_careers_privacy = True
        # Derive name from local if personal
        if is_personal_looking_email(e):
            derived = name_from_email_local(email_local(e))
            if derived:
                c.name = derived
        c._refresh_kind()
        raw_contacts.append(c)

    # When skip_fetch, reuse prior website contacts already on the profile
    if skip_fetch:
        for item in profile.get("contacts") or []:
            if not isinstance(item, dict):
                continue
            srcs = item.get("sources") or ([item["source"]] if item.get("source") else [])
            if "linkedin" in srcs and "website" not in srcs and item.get("source") == "linkedin":
                continue  # LI re-loaded below
            c = ContactAgg()
            c.name = item.get("name")
            if c.name and not looks_like_person_name(c.name, firm_name):
                c.name = None
            c.title = item.get("title")
            c.email = clean_email(item.get("email") or "") if item.get("email") else None
            c.phone = item.get("phone")
            c.profile_url = item.get("profile_url")
            c.schema_person = bool(item.get("schema_person"))
            for pg in item.get("source_pages") or []:
                c.source_pages.add(pg)
            if "linkedin" in (item.get("sources") or []) or item.get("source") == "linkedin":
                c.from_linkedin = True
                c.sources.add("linkedin")
            else:
                c.sources.add("website")
            if item.get("kind") == "inbox" and not c.name:
                c.kind = "inbox"
            else:
                c._refresh_kind()
            if c.email or (c.name and looks_like_person_name(c.name, firm_name)):
                raw_contacts.append(c)
        pages_ok = int((profile.get("poc_fetch") or {}).get("pages_ok") or 0)

    # Tag website-derived contacts
    for c in raw_contacts:
        if not c.from_linkedin:
            c.sources.add("website")

    # LinkedIn: use apply artifacts / note ## Contacts / company URL (never invent)
    if li_by_slug is None:
        li_by_slug = load_linkedin_results(vault)
    li_contacts, li_meta = resolve_linkedin_for_firm(vault, profile, li_by_slug)
    raw_contacts.extend(li_contacts)

    merged = merge_contacts(raw_contacts)
    # Drop contacts with no email and no person name
    merged = [
        c
        for c in merged
        if c.email or (c.name and looks_like_person_name(c.name, firm_name))
    ]
    # Role-inbox on a person is only kept when schema.org asserted it (shared footers otherwise)
    for c in merged:
        if (
            c.kind == "person"
            and c.email
            and is_role_inbox(c.email)
            and not c.schema_person
        ):
            c.email = None
    merged.sort(key=lambda c: c.poc_score(), reverse=True)

    best = pick_best_poc(merged)
    # best_poc null if we only have weak inbox with no person
    best_poc_dict = None
    if best:
        if best.kind == "person" or (best.email and not is_role_inbox(best.email)):
            best_poc_dict = best.to_dict()
        elif best.kind == "person":
            best_poc_dict = best.to_dict()
        else:
            # inbox-only top — still record as best_poc per schema but shortlist filters person+email
            # Spec: best_poc top contact or null — use top even if inbox
            best_poc_dict = best.to_dict()

    inbox_fb = pick_inbox_fallback(merged, profile.get("emails") or [])

    contacts_out = [c.to_dict() for c in merged]

    result = {
        "slug": slug,
        "name": firm_name,
        "website": website,
        "pages_seeded": len(seeds),
        "pages_fetched": len(pages),
        "pages_ok": pages_ok,
        "contacts_n": len(contacts_out),
        "best_poc": best_poc_dict,
        "inbox_fallback": inbox_fb,
        "linkedin": li_meta,
        "needs_linkedin_queue": bool(
            website
            and li_meta.get("status") in {
                "not_linked_yet",
                "pending_people_lookup",
                "names_masked",
            }
        ),
    }

    if dry_run:
        result["contacts"] = contacts_out
        return result

    profile["contacts"] = contacts_out
    profile["best_poc"] = best_poc_dict
    profile["inbox_fallback"] = inbox_fb
    profile["linkedin"] = {
        "company_url": li_meta.get("company_url"),
        "status": li_meta.get("status"),
        "contacts_named": li_meta.get("contacts_named"),
        "contacts_masked": li_meta.get("contacts_masked"),
        "from": li_meta.get("from"),
    }
    profile["poc_fetch"] = {
        "pages_seeded": len(seeds),
        "pages_fetched": len(pages),
        "pages_ok": pages_ok,
        "skip_fetch": skip_fetch,
    }
    profile["poc_updated"] = vault_iso()
    profile["poc_updated_stamp"] = vault_stamp()
    profile["updated"] = vault_iso()
    profile["updated_stamp"] = vault_stamp()
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def rebuild_index(vault: Path, *, category: str | None = None) -> dict:
    firms_dir = vault / "Research" / "firms"
    entries = []
    for path in sorted(firms_dir.glob("*.json")):
        if path.name in SKIP_FIRM_JSON or path.parent.name == "shortlists":
            continue
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not category_matches(p, category):
            continue
        best = p.get("best_poc")
        entry = {
            "slug": p.get("slug") or path.stem,
            "name": p.get("name"),
            "website": p.get("website"),
            "category": profile_category(p) or None,
            "path": f"Research/firms/{path.name}",
            "emails_n": len(p.get("emails") or []),
            "contacts_n": len(p.get("contacts") or []),
            "updated": p.get("updated"),
        }
        if best:
            entry["best_poc_name"] = best.get("name")
            entry["best_poc_email"] = best.get("email")
            entry["best_poc_title"] = best.get("title")
            entry["best_poc_kind"] = best.get("kind")
            entry["best_poc_score"] = best.get("poc_score")
        else:
            entry["best_poc_name"] = None
            entry["best_poc_email"] = None
            entry["best_poc_title"] = None
            entry["best_poc_kind"] = None
            entry["best_poc_score"] = None
        ib = p.get("inbox_fallback") or {}
        entry["inbox_fallback_email"] = ib.get("email")
        entries.append(entry)

    index = {
        "updated": vault_iso(),
        "category": norm_category(category) or None,
        "count": len(entries),
        "profiles": entries,
    }
    (firms_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index


def write_shortlist(vault: Path, *, category: str | None = None) -> list[dict]:
    firms_dir = vault / "Research" / "firms"
    rows = []
    for path in sorted(firms_dir.glob("*.json")):
        if path.name in SKIP_FIRM_JSON or path.parent.name == "shortlists":
            continue
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not category_matches(p, category):
            continue
        best = p.get("best_poc") or {}
        email = (best.get("email") or "").lower()
        name = (best.get("name") or "").strip()
        local = email_local(email) if email else ""
        personal_shape = bool(re.match(r"^[a-z]+[._-][a-z]+$", local)) or bool(
            re.match(r"^[a-z]\.[a-z]+$", local)
        )
        named_ok = bool(name) and looks_like_person_name(name) and len(name.split()) >= 2
        if (
            best.get("kind") == "person"
            and email
            and not is_role_inbox(email)
            and (personal_shape or named_ok)
        ):
            rows.append(
                {
                    "slug": p.get("slug") or path.stem,
                    "name": p.get("name"),
                    "website": p.get("website"),
                    "category": profile_category(p) or None,
                    "best_poc": best,
                    "poc_score": best.get("poc_score") or 0,
                }
            )
    rows.sort(key=lambda r: (-(r.get("poc_score") or 0), r.get("slug") or ""))
    cat = norm_category(category) or "all"
    cslug = category_slug(cat)
    out = {
        "updated": vault_iso(),
        "updated_stamp": vault_stamp(),
        "category": cat if cat != "all" else None,
        "count": len(rows),
        "firms": rows,
    }
    out_dir = firms_dir / "shortlists"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{cslug}-poc.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Compat symlink/copy for Law flat path used by earlier docs
    if cslug == "law":
        (firms_dir / "poc-shortlist.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return rows


def write_prove(vault: Path, results: list[dict], shortlist: list[dict], *, category: str | None = None) -> Path:
    day = vault_day()
    named = 0
    inbox_only = 0
    none = 0
    for r in results:
        best = r.get("best_poc")
        if best and best.get("kind") == "person" and best.get("email"):
            named += 1
        elif best and best.get("email"):
            inbox_only += 1
        elif r.get("inbox_fallback") and r["inbox_fallback"].get("email"):
            inbox_only += 1
        else:
            # check if best is person without email
            if best and best.get("kind") == "person":
                none += 1  # named but no email — count as none for email outreach
            else:
                none += 1

    # Recompute from written profiles for accuracy
    firms_dir = vault / "Research" / "firms"
    named = named_role_email = inbox_only = none = 0
    named_rows = []
    for path in sorted(firms_dir.glob("*.json")):
        if path.name in SKIP_FIRM_JSON or path.parent.name == "shortlists":
            continue
        p = json.loads(path.read_text(encoding="utf-8"))
        if not category_matches(p, category):
            continue
        best = p.get("best_poc")
        ib = p.get("inbox_fallback")
        if best and best.get("kind") == "person" and best.get("email"):
            if is_role_inbox(best["email"]):
                named_role_email += 1
            else:
                named += 1
                named_rows.append(p)
        elif best and best.get("kind") == "person":
            none += 1  # named but no email
        elif (best and best.get("email")) or (ib and ib.get("email")):
            inbox_only += 1
        else:
            none += 1

    lines = [
        f"# POC rank — {norm_category(category) or 'all'} — {day}",
        "",
        f"Stamp: {vault_stamp()}",
        f"UA: {UA}",
        f"Category: {norm_category(category) or 'all'}",
        "",
        "## Counts",
        "",
        f"- Firms processed: {len(results)}",
        f"- Named POC + personal email: **{named}**",
        f"- Named POC + role-inbox email only (info/marketing/…): **{named_role_email}**",
        f"- Inbox-only (no named POC): **{inbox_only}**",
        f"- None / named-without-email: **{none}**",
        f"- Shortlist size: **{len(shortlist)}**",
        "",
        "## Scoring",
        "",
        "- +5 named person",
        "- +4 title matches owner|founder|managing partner|partner|principal|president|shareholder",
        "- +3 personal-looking email (not role inbox)",
        "- +2 phone direct",
        "- +1 source attorney bio page",
        "- −3 role inbox (info|contact|office|admin|hello|marketing|intake|team|support|…)",
        "- −2 careers/scholarship/privacy-only page source",
        "",
        "## Top shortlist",
        "",
    ]
    for i, row in enumerate(shortlist[:15], 1):
        b = row.get("best_poc") or {}
        lines.append(
            f"{i}. **{row.get('name')}** — {b.get('name')} — {b.get('title') or '—'} — "
            f"`{b.get('email')}` — score {b.get('poc_score')} — {row.get('website')}"
        )
    if not shortlist:
        lines.append("_No firms with named POC + email yet._")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Never invent emails; same-domain only.",
            "- General info@ / contact@ / team@ are inbox_fallback, not best_poc goal.",
            "- Script: `scripts/build_poc.py` (outreach-leads).",
            "",
        ]
    )
    cslug = category_slug(norm_category(category) or "all")
    path = vault / "Sources" / f"poc-rank-{cslug}-{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def iter_firm_profiles(vault: Path, *, category: str | None = None) -> list[Path]:
    firms_dir = vault / "Research" / "firms"
    out: list[Path] = []
    for path in sorted(firms_dir.glob("*.json")):
        if path.name in SKIP_FIRM_JSON:
            continue
        try:
            prof = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not category_matches(prof, category):
            continue
        out.append(path)
    return out


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build ranked POCs for firm profiles (any category)")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument(
        "--category",
        default="Law",
        help="CRM category to process (Law, Logistics, HVAC, …). Use all for every profile.",
    )
    ap.add_argument("--slug", default="", help="Single firm slug")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-prove", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Do not re-fetch websites; re-rank from existing contacts + LinkedIn artifacts",
    )
    ap.add_argument(
        "--from-slug",
        default="",
        help="Resume alphabetically from this slug (inclusive)",
    )
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    category = norm_category(args.category) or "Law"
    profiles = iter_firm_profiles(vault, category=category)
    if args.slug:
        profiles = [p for p in profiles if p.stem == args.slug]
        if not profiles:
            print(f"FAIL\tno profile for slug {args.slug}", file=sys.stderr)
            return 2
    if args.from_slug:
        profiles = [p for p in profiles if p.stem >= args.from_slug]
    if args.limit:
        profiles = profiles[: args.limit]

    li_by_slug = load_linkedin_results(vault)
    queue_rows: list[dict] = []

    results: list[dict] = []
    print(
        f"## build_poc\tvault={vault}\tcategory={category}\tn={len(profiles)}"
        f"\tskip_fetch={args.skip_fetch}\tli_results={len(li_by_slug)}\tua={UA}"
    )
    for i, path in enumerate(profiles, 1):
        print(f"[{i}/{len(profiles)}] {path.stem} ...", flush=True)
        try:
            rec = process_firm(
                vault,
                path,
                max_pages=args.max_pages,
                workers=args.workers,
                dry_run=args.dry_run,
                skip_fetch=args.skip_fetch,
                li_by_slug=li_by_slug,
            )
            best = rec.get("best_poc") or {}
            print(
                f"  ok pages={rec['pages_ok']}/{rec['pages_fetched']} "
                f"contacts={rec['contacts_n']} "
                f"best={best.get('name') or '—'} <{best.get('email') or '—'}> "
                f"score={best.get('poc_score')} kind={best.get('kind')}",
                flush=True,
            )
            results.append(rec)
            if rec.get("needs_linkedin_queue") and not args.dry_run:
                note = None
                try:
                    prof = json.loads(path.read_text(encoding="utf-8"))
                    note = (prof.get("sources") or {}).get("note")
                except Exception:
                    pass
                queue_rows.append(
                    {
                        "slug": rec["slug"],
                        "path": str(vault / note) if note else None,
                        "firm": rec.get("name"),
                        "website": rec.get("website"),
                        "linkedin_company": (rec.get("linkedin") or {}).get("company_url"),
                        "linkedin_status": (rec.get("linkedin") or {}).get("status"),
                        "reason": "poc_needs_named_li_or_unmask",
                    }
                )
            li = rec.get("linkedin") or {}
            print(
                f"    linkedin={li.get('status')} named_li={li.get('contacts_named')}",
                flush=True,
            )
        except Exception as e:
            print(f"  FAIL {path.stem}: {e}", flush=True)
            results.append({"slug": path.stem, "error": str(e), "best_poc": None})

    if args.dry_run:
        named = sum(
            1
            for r in results
            if (r.get("best_poc") or {}).get("kind") == "person"
            and (r.get("best_poc") or {}).get("email")
        )
        print(f"DRY-RUN done named_poc+email={named}/{len(results)}")
        return 0

    shortlist = write_shortlist(vault, category=category)
    rebuild_index(vault, category=category)
    if not args.dry_run and queue_rows:
        qpath = append_linkedin_queue(vault, queue_rows)
        print(f"linkedin-queue\t{qpath}\trows={len(queue_rows)}")
    prove_path = None
    if not args.no_prove:
        prove_path = write_prove(vault, results, shortlist, category=category)
        print(f"prove\t{prove_path}")

    named = sum(
        1
        for r in shortlist
    )
    print(f"shortlist\t{named}")
    print(f"index\t{vault / 'Research/firms/index.json'}")

    if not args.no_push:
        import push_backup

        rc = push_backup.run(
            [
                "--vault",
                str(vault),
            ]
        )
        if rc != 0:
            print(f"WARN\tpush-backup exit {rc}", file=sys.stderr)
            return rc
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())

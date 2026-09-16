#!/usr/bin/env python3
"""Website phone enrich for outreach-leads (PI-first, any Law with website).

Crawl contact/about/team/attorney/homepage for tel: + visible phones.
- Attach to Research/firms/<slug>.json best_poc / contacts when page is that
  person's bio OR their name appears on the page with a phone (solo/about).
- Set note frontmatter phone if empty (firm main line; LOCAL_AREA_CODES if set).
Never invents phones. Never writes directory hosts to website:.
UA: OutreachTools/1.0. Retries on timeout.
"""
from __future__ import annotations

import argparse
import os
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from note_io import (  # noqa: E402
    DEFAULT_VAULT,
    UA,
    category_has,
    fm_get,
    fmt_phone,
    host_blocked,
    iter_notes,
    practice_is_pi,
    set_fm,
    website_value,
)
from website_emails import (  # noqa: E402
    COMMON_PATHS,
    host_of,
    http_get_with_retries,
    normalize_url,
    registrable_domain,
)

MT = ZoneInfo("America/Chicago")

def local_area() -> frozenset[str]:
    raw = os.environ.get("LOCAL_AREA_CODES", "")
    return frozenset(x.strip() for x in raw.split(",") if x.strip())

TEL_HREF_RE = re.compile(r"""href=["']tel:([^"']+)["']""", re.I)
TEL_BARE_RE = re.compile(r"""tel:([+\d][\d().\s\-%]{7,22})""", re.I)
SCHEMA_TEL_RE = re.compile(r'"telephone"\s*:\s*"([^"]+)"', re.I)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

BIO_HINT_RE = re.compile(
    r"/(attorney|attorneys|lawyer|lawyers|team|our-team|people|staff|"
    r"bio|bios|professionals?|our-attorneys)/[^/]+/?$"
    r"|/(attorney-profile|attorney-bio|lawyer-profile)/?$",
    re.I,
)

CONTACTISH_RE = re.compile(
    r"/(contact|contact-us|about|about-us|our-firm|team|attorneys?)(/|$)",
    re.I,
)


def vault_day() -> str:
    return datetime.now(MT).strftime("%Y-%m-%d")


def vault_stamp() -> str:
    return datetime.now(MT).strftime("%Y-%m-%d %H:%M %Z")


def strip_tags(s: str) -> str:
    s = re.sub(r"(?is)<script.*?>.*?</script>", " ", s or "")
    s = re.sub(r"(?is)<style.*?>.*?</style>", " ", s)
    return WS_RE.sub(" ", TAG_RE.sub(" ", s)).strip()


def normalize_phone(raw: str) -> str | None:
    p = fmt_phone(raw)
    return p or None


def prefer_local(phones: list[str]) -> str | None:
    uniq: list[str] = []
    seen: set[str] = set()
    for raw in phones:
        n = normalize_phone(raw)
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    if not uniq:
        return None
    local = [n for n in uniq if n[3:6] in local_area()]
    return (local or uniq)[0]


def extract_phones(html: str) -> list[str]:
    raw: list[str] = []
    for m in TEL_HREF_RE.finditer(html or ""):
        raw.append(unquote(m.group(1)))
    for m in TEL_BARE_RE.finditer(html or ""):
        raw.append(unquote(m.group(1)))
    for m in SCHEMA_TEL_RE.finditer(html or ""):
        raw.append(m.group(1))
    text = strip_tags(html)
    for m in re.finditer(
        r"(?:phone|call|tel|office)\s*[:#]?\s*(\+?1?[-.\s(]*\d{3}[-.\s)]*\d{3}[-.\s]*\d{4})",
        text,
        re.I,
    ):
        raw.append(m.group(1))
    # bare visible NANP when labeled poorly — only digit groups near tel icons already covered
    return raw


def firm_tokens(name: str) -> list[str]:
    stop = {
        "the", "and", "of", "law", "firm", "office", "offices", "attorney",
        "attorneys", "pllc", "pc", "pa", "llc", "llp", "inc", "p", "c", "l",
        "injury", "personal", "trial", "lawyer", "lawyers",
    }
    toks = re.findall(r"[A-Za-z]{3,}", name or "")
    return [t.lower() for t in toks if t.lower() not in stop]


def page_mentions_firm(html: str, firm_name: str) -> bool:
    text = strip_tags(html).lower()
    toks = firm_tokens(firm_name)
    if not toks:
        return True
    hits = sum(1 for t in toks if t in text)
    return hits >= max(1, min(2, len(toks)))


def name_on_page(html: str, person: str) -> bool:
    if not person:
        return False
    text = strip_tags(html).lower()
    parts = [p.lower().rstrip(".") for p in re.findall(r"[A-Za-z]+", person) if len(p) > 1]
    if len(parts) < 2:
        return False
    # require first + last
    return parts[0] in text and parts[-1] in text


def seed_urls(website: str, extra: list[str] | None = None) -> list[str]:
    if not website:
        return []
    if not re.match(r"^https?://", website, re.I):
        website = "https://" + website
    p = urlparse(website)
    origin = f"{p.scheme}://{p.netloc}"
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        try:
            nu = normalize_url(u, origin)
        except Exception:
            return
        key = nu.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        out.append(nu)

    add(origin + "/")
    add(website)
    for path in COMMON_PATHS:
        add(origin + path)
        add(origin + path + "/")
    # extra known bio / contact URLs from profile
    for u in extra or []:
        if u and registrable_domain(host_of(u)) == registrable_domain(p.netloc):
            add(u)
    return out


def fetch(url: str) -> dict:
    r = http_get_with_retries(url)
    return {
        "url": url,
        "final_url": r.get("final_url") or url,
        "status": r.get("status"),
        "http_code": r.get("http_code") or 0,
        "body": r.get("body") or "" if r.get("status") == "ok" else "",
        "error": r.get("error"),
    }


def load_pi_targets(vault: Path, *, slug: str | None = None, limit: int = 0) -> list[dict]:
    firms_dir = vault / "Research" / "firms"
    targets: list[dict] = []
    for path in iter_notes(vault):
        if slug and path.stem != slug:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not category_has(text, "Law"):
            continue
        if not practice_is_pi(text):
            continue
        web = website_value(text)
        if host_blocked(web):
            web = ""
        # skip known false-positive vineyard domain for Poulos
        if "zinvalle.com" in (web or "").lower():
            web = ""
        phone = fmt_phone(fm_get(text, "phone"))
        prof_path = firms_dir / f"{path.stem}.json"
        profile = None
        if prof_path.exists():
            try:
                profile = json.loads(prof_path.read_text(encoding="utf-8"))
            except Exception:
                profile = None
        bp = (profile or {}).get("best_poc") or {}
        # collect known person source pages
        extra_urls: list[str] = []
        for c in (profile or {}).get("contacts") or []:
            for pg in c.get("source_pages") or []:
                extra_urls.append(pg)
            if c.get("profile_url"):
                extra_urls.append(c["profile_url"])
        if bp.get("profile_url"):
            extra_urls.append(bp["profile_url"])
        # use profile website if note website empty but profile has official
        if not web and profile and profile.get("website"):
            pw = profile["website"]
            if pw.startswith("http") and not host_blocked(pw) and "zinvalle.com" not in pw.lower():
                web = pw
        targets.append(
            {
                "slug": path.stem,
                "name": fm_get(text, "name") or path.stem,
                "note_path": path,
                "note_text": text,
                "website": web,
                "note_phone": phone,
                "profile": profile,
                "profile_path": prof_path if prof_path.exists() else None,
                "best_poc_name": bp.get("name"),
                "best_poc_phone": fmt_phone(bp.get("phone") or ""),
                "extra_urls": extra_urls,
            }
        )
        if limit and len(targets) >= limit:
            break
    return targets


def crawl_firm(t: dict, *, max_pages: int = 18) -> dict:
    website = t["website"]
    if not website:
        return {"slug": t["slug"], "skipped": "no_website", "pages": [], "phones": []}
    seeds = seed_urls(website, t.get("extra_urls"))[:max_pages]
    pages = []
    all_phones: list[dict] = []
    for u in seeds:
        page = fetch(u)
        pages.append(
            {
                "url": page["url"],
                "final_url": page["final_url"],
                "status": page["status"],
                "http_code": page["http_code"],
                "error": page["error"],
            }
        )
        body = page.get("body") or ""
        if page["status"] != "ok" or not body:
            continue
        if not page_mentions_firm(body, t["name"]):
            # still allow homepage phones if schema tel present and site already verified
            if urlparse(page["final_url"]).path not in ("", "/"):
                continue
        phones = extract_phones(body)
        if not phones:
            continue
        final = page["final_url"]
        is_bio = bool(BIO_HINT_RE.search(urlparse(final).path or ""))
        poc = t.get("best_poc_name")
        mentions_poc = name_on_page(body, poc) if poc else False
        for raw in phones:
            n = normalize_phone(raw)
            if not n:
                continue
            all_phones.append(
                {
                    "phone": n,
                    "source_url": final,
                    "is_bio": is_bio,
                    "mentions_poc": mentions_poc,
                    "contactish": bool(CONTACTISH_RE.search(urlparse(final).path or "")),
                }
            )
        time.sleep(0.15)
    return {
        "slug": t["slug"],
        "website": website,
        "pages": pages,
        "phones": all_phones,
    }


def pick_firm_phone(phones: list[dict]) -> tuple[str | None, str | None]:
    if not phones:
        return None, None
    # prefer contactish local, then any local, then any contactish, then first
    def score(p: dict) -> tuple:
        local = 1 if p["phone"][3:6] in local_area() else 0
        return (1 if p.get("contactish") else 0, local, 1 if not p.get("is_bio") else 0)

    best = sorted(phones, key=score, reverse=True)[0]
    # among top tier prefer local
    locals_ = [p for p in phones if p["phone"][3:6] in local_area()]
    pool = locals_ or phones
    contact = [p for p in pool if p.get("contactish")]
    chosen = (contact or pool)[0]
    return chosen["phone"], chosen["source_url"]


def pick_poc_phone(phones: list[dict], poc_name: str | None) -> tuple[str | None, str | None]:
    if not poc_name or not phones:
        return None, None
    bio = [p for p in phones if p.get("is_bio") and p.get("mentions_poc")]
    named = [p for p in phones if p.get("mentions_poc")]
    pool = bio or named
    if not pool:
        return None, None
    local = [p for p in pool if p["phone"][3:6] in local_area()]
    chosen = (local or pool)[0]
    return chosen["phone"], chosen["source_url"]


def update_profile_poc_phone(
    profile: dict, phone: str, source_url: str, poc_name: str
) -> bool:
    changed = False
    bp = profile.get("best_poc")
    if isinstance(bp, dict) and bp.get("name"):
        # only fill empty
        if not fmt_phone(bp.get("phone") or ""):
            bp["phone"] = phone
            pages = list(bp.get("source_pages") or [])
            if source_url not in pages:
                pages.append(source_url)
            bp["source_pages"] = pages
            srcs = set(bp.get("sources") or [])
            srcs.add("website")
            bp["sources"] = sorted(srcs)
            profile["best_poc"] = bp
            changed = True
    for c in profile.get("contacts") or []:
        if not isinstance(c, dict):
            continue
        if (c.get("name") or "").lower() != poc_name.lower():
            continue
        if not fmt_phone(c.get("phone") or ""):
            c["phone"] = phone
            pages = list(c.get("source_pages") or [])
            if source_url not in pages:
                pages.append(source_url)
            c["source_pages"] = pages
            changed = True
    return changed


def clear_bad_website(vault: Path, slug: str, bad_host: str) -> bool:
    note = vault / "Businesses" / f"{slug}.md"
    if not note.exists():
        return False
    text = note.read_text(encoding="utf-8")
    web = website_value(text)
    if bad_host not in (web or "").lower():
        return False
    text2 = set_fm(text, "website", "")
    # also body bullet if present
    text2 = re.sub(
        rf"(- Website:\s*){re.escape(web)}",
        r"\1",
        text2,
        count=1,
    )
    stamp = vault_stamp()
    if "## Research" in text2:
        text2 = text2.rstrip() + (
            f"\n- {stamp}: cleared false-positive website {web} "
            f"(Zin Valle Vineyards, not law firm). Phone not written.\n"
        )
    else:
        text2 = text2.rstrip() + (
            f"\n\n## Research\n- {stamp}: cleared false-positive website {web} "
            f"(Zin Valle Vineyards, not law firm). Phone not written.\n"
        )
    note.write_text(text2, encoding="utf-8")
    # profile
    pp = vault / "Research" / "firms" / f"{slug}.json"
    if pp.exists():
        try:
            d = json.loads(pp.read_text(encoding="utf-8"))
            if bad_host in (d.get("website") or "").lower():
                d["website"] = ""
                d["updated"] = datetime.now(MT).isoformat(timespec="seconds")
                d["updated_stamp"] = stamp
                pp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    return True


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crawl firm sites for phones → notes + best_poc")
    ap.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=18)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--practice", default="pi", help="pi = personal-injury/trial only")
    args = ap.parse_args(argv)

    vault: Path = args.vault
    before_notes = 0
    before_poc = 0
    targets = load_pi_targets(vault, slug=args.slug, limit=args.limit)
    # before counts across all PI
    all_pi = load_pi_targets(vault)
    for t in all_pi:
        if t["note_phone"]:
            before_notes += 1
        if t["best_poc_phone"]:
            before_poc += 1

    enriched_note: list[dict] = []
    enriched_poc: list[dict] = []
    cleared = []
    crawled = 0
    skipped_no_web = 0
    jsonl_rows: list[dict] = []

    # Clear known bad website first
    if not args.dry_run and (not args.slug or args.slug == "the-law-office-of-victor-f-poulos-p-c"):
        if clear_bad_website(vault, "the-law-office-of-victor-f-poulos-p-c", "zinvalle.com"):
            cleared.append("the-law-office-of-victor-f-poulos-p-c")

    for t in targets:
        if not t["website"]:
            skipped_no_web += 1
            jsonl_rows.append({"slug": t["slug"], "skipped": "no_website"})
            continue
        crawled += 1
        result = crawl_firm(t, max_pages=args.max_pages)
        phones = result.get("phones") or []
        firm_phone, firm_src = pick_firm_phone(phones)
        poc_phone, poc_src = pick_poc_phone(phones, t.get("best_poc_name"))

        row = {
            "slug": t["slug"],
            "name": t["name"],
            "website": t["website"],
            "firm_phone": firm_phone,
            "firm_src": firm_src,
            "poc_name": t.get("best_poc_name"),
            "poc_phone": poc_phone,
            "poc_src": poc_src,
            "phones_n": len(phones),
            "pages_ok": sum(1 for p in result.get("pages") or [] if p.get("status") == "ok"),
        }

        # note phone if empty
        if firm_phone and not t["note_phone"]:
            row["action_note"] = "fill_phone"
            if not args.dry_run:
                text = t["note_path"].read_text(encoding="utf-8")
                text = set_fm(text, "phone", firm_phone)
                # body bullet
                text = re.sub(
                    r"(- Phone:\s*)\s*$",
                    rf"\g<1>{firm_phone}",
                    text,
                    count=1,
                    flags=re.M,
                )
                if re.search(r"^- Phone:\s*$", text, re.M):
                    text = re.sub(r"^(- Phone:\s*)$", rf"\1{firm_phone}", text, count=1, flags=re.M)
                stamp = vault_stamp()
                note_line = (
                    f"- {stamp}: phone {firm_phone} from {firm_src} "
                    f"via website_phones (UA {UA})."
                )
                if "## Research" in text:
                    text = text.rstrip() + "\n" + note_line + "\n"
                else:
                    text = text.rstrip() + "\n\n## Research\n" + note_line + "\n"
                # status bump prospect → call-ready when phone set
                if fm_get(text, "status") == "prospect":
                    text = set_fm(text, "status", "call-ready")
                t["note_path"].write_text(text, encoding="utf-8")
            enriched_note.append(
                {
                    "firm": t["name"],
                    "slug": t["slug"],
                    "poc": None,
                    "phone": firm_phone,
                    "source": firm_src,
                }
            )

        # best_poc phone if named and empty
        if (
            poc_phone
            and t.get("best_poc_name")
            and not t["best_poc_phone"]
            and t.get("profile")
            and t.get("profile_path")
        ):
            row["action_poc"] = "fill_best_poc_phone"
            if not args.dry_run:
                prof = json.loads(t["profile_path"].read_text(encoding="utf-8"))
                if update_profile_poc_phone(prof, poc_phone, poc_src or "", t["best_poc_name"]):
                    prof["updated"] = datetime.now(MT).isoformat(timespec="seconds")
                    prof["updated_stamp"] = vault_stamp()
                    t["profile_path"].write_text(
                        json.dumps(prof, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
            enriched_poc.append(
                {
                    "firm": t["name"],
                    "slug": t["slug"],
                    "poc": t["best_poc_name"],
                    "phone": poc_phone,
                    "source": poc_src,
                }
            )

        jsonl_rows.append(row)
        print(
            f"{t['slug']}\tfirm={firm_phone or '-'}\tpoc={poc_phone or '-'}\t"
            f"pages_ok={row['pages_ok']}\tphones_n={row['phones_n']}"
        )

    # after counts
    after_all = load_pi_targets(vault)
    after_notes = sum(1 for t in after_all if t["note_phone"])
    after_poc = sum(1 for t in after_all if t["best_poc_phone"])

    runs = vault / "Sources" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    day = vault_day()
    jsonl_path = runs / f"website-phones-{day}.jsonl"
    if not args.dry_run:
        with jsonl_path.open("w", encoding="utf-8") as f:
            for r in jsonl_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "before_notes_with_phone": before_notes,
        "after_notes_with_phone": after_notes,
        "before_best_poc_phone": before_poc,
        "after_best_poc_phone": after_poc,
        "enriched_note_n": len(enriched_note),
        "enriched_poc_n": len(enriched_poc),
        "cleared_bad_website": cleared,
        "crawled": crawled,
        "skipped_no_web": skipped_no_web,
        "enriched_note": enriched_note,
        "enriched_poc": enriched_poc,
        "jsonl": str(jsonl_path) if not args.dry_run else None,
        "dry_run": args.dry_run,
        "stamp": vault_stamp(),
    }
    summary_path = runs / f"website-phones-summary-{day}.json"
    if not args.dry_run:
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

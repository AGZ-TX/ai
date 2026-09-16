#!/usr/bin/env python3
"""Website / sitemap email enrich layer for outreach-leads.

Discover firm pages via sitemap + common paths, extract same-registrable-domain
emails (mailto + regex), write ## Emails + optional email: onto vault notes.
Never invents emails. Never writes linkedin.com into website:.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
import http.client
import socket
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

from note_io import (
    DEFAULT_VAULT,
    UA,
    category_has,
    fm_get,
    host_blocked,
    iter_notes,
    practice_is_pi,
    set_fm,
    website_value,
)

SCRIPT_DIR = Path(__file__).resolve().parent

COMMON_PATHS = (
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/people",
    "/attorneys",
    "/staff",
    "/our-firm",
    "/attorneys-staff",
)

PRIORITY_RE = re.compile(
    r"/(contact|about|team|our-team|people|staff|attorney|attorneys|bio|bios|"
    r"lawyer|lawyers|our-firm|meet|leadership|partners?|members?)(/|$)",
    re.I,
)

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
MAILTO_RE = re.compile(r"mailto:([^\s\"'<>?#]+)", re.I)

# Free / consumer mail — never keep unless the *firm* registrable domain is these (impossible for firms).
FREE_MAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "aol.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "protonmail.com",
    "proton.me",
    "gmx.com",
    "mail.com",
    "ymail.com",
    "googlemail.com",
}

MULTI_PART_SUFFIXES = {
    "co.uk",
    "com.au",
    "co.nz",
    "co.jp",
    "com.br",
    "co.za",
    "com.mx",
    "org.uk",
    "net.au",
}

SITEMAP_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
}

EMAILS_HEADING = re.compile(r"^##\s+Emails\s*$", re.M)

BEST_LOCALS = ("info", "contact", "office", "admin", "hello", "intake", "frontdesk")


def vault_day() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")


def vault_stamp() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z")


def registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 without external deps."""
    host = (host or "").lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return host
    parts = host.split(".")
    if len(parts) >= 3:
        suffix2 = ".".join(parts[-2:])
        if suffix2 in MULTI_PART_SUFFIXES:
            return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def host_of(url: str) -> str:
    u = url if re.match(r"^https?://", url, re.I) else "https://" + url
    return urlparse(u).netloc.lower().split("@")[-1]


def same_registrable(email_domain: str, site_domain: str) -> bool:
    ed = registrable_domain(email_domain)
    sd = registrable_domain(site_domain)
    if not ed or not sd:
        return False
    if ed in FREE_MAIL_DOMAINS and ed != sd:
        return False
    return ed == sd


def normalize_url(url: str, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    p = urlparse(url)
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        # keep as-is for fetch; only normalize path trailing
        pass
    path = p.path or "/"
    # drop fragment; keep query lightly (sitemaps rarely need it)
    return urlunparse((scheme, netloc, path, "", "", ""))


# Fail-fast connect; allow slow read. Retries on timeout/connection/5xx.
CONNECT_TIMEOUT = 6.0
READ_TIMEOUT = 22.0
RETRY_BACKOFFS = (0.5, 1.5, 3.0)
MAX_ATTEMPTS = 1 + len(RETRY_BACKOFFS)


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason, (TimeoutError, socket.timeout)
    ):
        return True
    return "timed out" in str(exc).lower()


def _http_get_once(
    url: str,
    *,
    connect_timeout: float = CONNECT_TIMEOUT,
    read_timeout: float = READ_TIMEOUT,
    max_redirects: int = 8,
) -> tuple[int, str, str]:
    """Split connect/read timeouts via http.client (urllib has single timeout)."""
    current = url
    for _ in range(max_redirects + 1):
        p = urlparse(current)
        if p.scheme not in ("http", "https"):
            raise ValueError(f"unsupported scheme: {p.scheme}")
        host = p.hostname
        if not host:
            raise ValueError(f"no host: {current}")
        path = p.path or "/"
        if p.query:
            path = path + "?" + p.query
        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "close",
            "Host": p.netloc,
        }
        if p.scheme == "https":
            ctx = ssl.create_default_context()
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, p.port or 443, timeout=connect_timeout, context=ctx
            )
        else:
            conn = http.client.HTTPConnection(
                host, p.port or 80, timeout=connect_timeout
            )
        try:
            conn.connect()
            if conn.sock is not None:
                conn.sock.settimeout(read_timeout)
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                body = raw.decode(charset, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")
            code = int(resp.status)
            if code in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                conn.close()
                if not loc:
                    return code, current, body
                current = urljoin(current, loc)
                continue
            return code, current, body
        finally:
            try:
                conn.close()
            except Exception:
                pass
    raise urllib.error.URLError(f"too many redirects for {url}")


def http_get_with_retries(url: str, *, max_attempts: int = MAX_ATTEMPTS) -> dict:
    """Fetch with retries. status: ok|timeout|http_error|error."""
    attempts = 0
    last_error = None
    last_code = 0
    final_url = url
    body = ""
    outcome = "error"
    while attempts < max_attempts:
        attempts += 1
        try:
            code, final_url, body = _http_get_once(url)
            last_code = code
            if 200 <= code < 400:
                return {
                    "status": "ok",
                    "http_code": code,
                    "final_url": final_url,
                    "body": body,
                    "attempts": attempts,
                    "error": None,
                }
            if 400 <= code < 500:
                return {
                    "status": "http_error",
                    "http_code": code,
                    "final_url": final_url,
                    "body": body,
                    "attempts": attempts,
                    "error": f"HTTP {code}",
                }
            last_error = f"HTTP {code}"
            outcome = "http_error"
            if attempts < max_attempts:
                time.sleep(RETRY_BACKOFFS[min(attempts - 1, len(RETRY_BACKOFFS) - 1)])
                continue
            return {
                "status": "http_error",
                "http_code": code,
                "final_url": final_url,
                "body": body,
                "attempts": attempts,
                "error": last_error,
            }
        except Exception as e:
            last_error = str(e)
            outcome = "timeout" if _is_timeout(e) else "error"
            if attempts < max_attempts:
                time.sleep(RETRY_BACKOFFS[min(attempts - 1, len(RETRY_BACKOFFS) - 1)])
                continue
            break
    return {
        "status": outcome,
        "http_code": last_code,
        "final_url": final_url,
        "body": body,
        "attempts": attempts,
        "error": last_error,
    }


def http_get(url: str, timeout: float = 20.0) -> tuple[int, str, str]:
    """Return (status, final_url, body_text). Raises on network failure (compat)."""
    # timeout arg kept for callers; split timeouts preferred via http_get_with_retries
    del timeout
    r = http_get_with_retries(url)
    if r["status"] == "ok" or (r.get("http_code") or 0) >= 400:
        return int(r["http_code"] or 0), r["final_url"] or url, r.get("body") or ""
    raise urllib.error.URLError(r.get("error") or "fetch failed")


def parse_sitemap_locs(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls)."""
    pages: list[str] = []
    nested: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # fallback regex
        for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, re.I):
            pages.append(m.group(1).strip())
        return pages, nested

    tag = root.tag.lower() if isinstance(root.tag, str) else ""
    # strip ns
    local = tag.split("}")[-1] if "}" in tag else tag

    def children(name: str):
        # with and without namespace
        yield from root.findall(f"{{http://www.sitemaps.org/schemas/sitemap/0.9}}{name}")
        yield from root.findall(name)
        yield from root.findall(f"sm:{name}", SITEMAP_NS)

    if local == "sitemapindex":
        for sm in children("sitemap"):
            loc_el = sm.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            if loc_el is None:
                loc_el = sm.find("loc")
            if loc_el is not None and (loc_el.text or "").strip():
                nested.append(loc_el.text.strip())
    else:
        for url_el in children("url"):
            loc_el = url_el.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            if loc_el is None:
                loc_el = url_el.find("loc")
            if loc_el is not None and (loc_el.text or "").strip():
                pages.append(loc_el.text.strip())
        # some indexes mislabeled
        if not pages:
            for sm in children("sitemap"):
                loc_el = sm.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
                if loc_el is None:
                    loc_el = sm.find("loc")
                if loc_el is not None and (loc_el.text or "").strip():
                    nested.append(loc_el.text.strip())
    return pages, nested


def robots_sitemaps(robots_text: str) -> list[str]:
    out = []
    for line in robots_text.splitlines():
        if line.lower().startswith("sitemap:"):
            u = line.split(":", 1)[1].strip()
            if u:
                out.append(u)
    return out


def discover_urls(website: str, errors: list[str]) -> list[str]:
    """Sitemap + robots + homepage + common paths. Same-host only."""
    base = website.rstrip("/")
    if not re.match(r"^https?://", base, re.I):
        base = "https://" + base
    site_host = host_of(base)
    site_reg = registrable_domain(site_host)
    found: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if not u:
            return
        nu = normalize_url(u, base)
        h = host_of(nu)
        if registrable_domain(h) != site_reg:
            return
        if host_blocked(nu):
            return
        key = nu.rstrip("/").lower() or nu.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(nu)

    add(base + "/")
    for path in COMMON_PATHS:
        add(base + path)
        add(base + path + "/")

    sitemap_seeds = [base + "/sitemap.xml", base + "/sitemap_index.xml"]
    try:
        code, _, body = http_get(base + "/robots.txt")
        if code == 200:
            sitemap_seeds = robots_sitemaps(body) + sitemap_seeds
    except Exception as e:
        errors.append(f"robots.txt:{e}")

    # BFS nested sitemaps (cap depth/count)
    queue = list(dict.fromkeys(sitemap_seeds))  # dedupe preserve order
    fetched_smaps: set[str] = set()
    all_page_locs: list[str] = []
    max_smaps = 25

    while queue and len(fetched_smaps) < max_smaps:
        sm_url = queue.pop(0)
        if sm_url in fetched_smaps:
            continue
        fetched_smaps.add(sm_url)
        try:
            code, final, body = http_get(sm_url)
            if code >= 400:
                errors.append(f"sitemap_http_{code}:{sm_url}")
                continue
            # only parse if looks like xml
            if "<" not in body[:500]:
                continue
            pages, nested = parse_sitemap_locs(body)
            all_page_locs.extend(pages)
            for n in nested:
                if n not in fetched_smaps:
                    queue.append(n)
            time.sleep(0.15)
        except Exception as e:
            errors.append(f"sitemap:{sm_url}:{e}")

    # Huge sitemap: filter hard to priority paths before adding
    if len(all_page_locs) > 500:
        filtered = [u for u in all_page_locs if PRIORITY_RE.search(urlparse(u).path or "")]
        errors.append(f"sitemap_large:{len(all_page_locs)}_filtered_to_{len(filtered)}")
        all_page_locs = filtered

    for u in all_page_locs:
        add(u)

    return found


def prioritize_urls(urls: list[str], max_pages: int) -> list[str]:
    def score(u: str) -> tuple[int, int, str]:
        path = urlparse(u).path or "/"
        pri = 0 if PRIORITY_RE.search(path) else 1
        # shorter paths slightly preferred among peers
        return (pri, len(path), u.lower())

    ordered = sorted(urls, key=score)
    # stable: priority first already via sort
    return ordered[: max(1, max_pages)]


def extract_emails(html: str) -> list[str]:
    text = html_lib.unescape(html or "")
    found: list[str] = []
    seen: set[str] = set()
    for m in MAILTO_RE.finditer(text):
        addr = m.group(1).strip()
        # mailto may include ?subject=
        addr = addr.split("?")[0].strip()
        addr = addr.replace("%40", "@")
        if "@" in addr:
            e = addr.lower()
            if e not in seen:
                seen.add(e)
                found.append(e)
    for m in EMAIL_RE.finditer(text):
        e = m.group(1).lower().rstrip(".,;:)>]")
        # skip image/asset false positives
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")):
            continue
        if e not in seen:
            seen.add(e)
            found.append(e)
    return found


def pick_best_inbox(emails: list[str]) -> str:
    if not emails:
        return ""
    by_local = {e.split("@", 1)[0]: e for e in emails}
    for loc in BEST_LOCALS:
        if loc in by_local:
            return by_local[loc]
    # prefer local that starts with preferred prefixes
    for loc in BEST_LOCALS:
        for e in emails:
            if e.split("@", 1)[0].startswith(loc):
                return e
    return emails[0]


def upsert_emails_section(text: str, bullets: list[str]) -> str:
    block = "## Emails\n" + "\n".join(bullets) + "\n"
    if EMAILS_HEADING.search(text):
        return re.sub(
            r"^##\s+Emails\s*\n(?:.*?)(?=^##\s|\Z)",
            block + "\n",
            text,
            count=1,
            flags=re.M | re.S,
        )
    # Prefer before ## Contacts or ## Call log
    for marker in ("## Contacts", "## Call log"):
        if marker in text:
            return text.replace(marker, block + "\n" + marker, 1)
    if "## Research" in text:
        m = re.search(r"(^##\s+Research\s*\n(?:.*?)(?=^##\s|\Z))", text, re.M | re.S)
        if m:
            end = m.end(1)
            return text[:end].rstrip() + "\n\n" + block + "\n" + text[end:].lstrip("\n")
    return text.rstrip() + "\n\n" + block + "\n"


def matches_filters(
    text: str,
    category: str | None,
    practice: str | None,
    slug: str | None,
    path: Path,
) -> bool:
    if slug:
        stem = path.stem.lower()
        s = slug.lower().removesuffix(".md")
        if stem != s and s not in stem:
            return False
    if category and not category_has(text, category):
        return False
    if practice:
        pl = practice.lower()
        if pl in {"pi", "personal-injury", "trial"}:
            if not practice_is_pi(text):
                return False
        elif pl not in fm_get(text, "practice").lower():
            return False
    return True


def list_candidates(
    vault: Path,
    *,
    category: str | None,
    practice: str | None,
    limit: int,
    slug: str | None,
) -> list[tuple[Path, str, str]]:
    """Return (path, website, firm) for notes with official http(s) website."""
    rows: list[tuple[Path, str, str]] = []
    for p in iter_notes(vault):
        text = p.read_text(encoding="utf-8", errors="replace")
        if not matches_filters(text, category, practice, slug, p):
            continue
        site = website_value(text)
        if not site:
            continue
        if not re.match(r"^https?://", site, re.I):
            continue
        if host_blocked(site):
            continue
        firm = fm_get(text, "name") or p.stem
        rows.append((p, site, firm))
        if limit and len(rows) >= limit:
            break
    return rows


def enrich_one(
    path: Path,
    website: str,
    *,
    max_pages: int,
    dry_run: bool,
) -> dict:
    errors: list[str] = []
    site_host = host_of(website)
    site_reg = registrable_domain(site_host)

    urls = discover_urls(website, errors)
    to_fetch = prioritize_urls(urls, max_pages)

    email_pages: dict[str, list[str]] = defaultdict(list)
    pages_fetched = 0

    if dry_run:
        return {
            "slug": path.stem,
            "path": str(path),
            "website": website,
            "emails": [],
            "pages_fetched": 0,
            "pages_planned": to_fetch,
            "pages_planned_n": len(to_fetch),
            "errors": errors,
            "status": "dry-run",
            "dry_run": True,
        }

    page_outcomes: list[dict] = []
    for u in to_fetch:
        r = http_get_with_retries(u)
        pages_fetched += 1
        page_outcomes.append({
            "url": u,
            "final_url": r.get("final_url") or u,
            "status": r["status"],
            "http_code": r.get("http_code") or 0,
            "attempts": r.get("attempts") or 1,
            "error": r.get("error"),
        })
        if r["status"] != "ok":
            errors.append(
                f"fetch:{r['status']}:{u}:{r.get('error') or r.get('http_code')}"
            )
            continue
        body = r.get("body") or ""
        final = r.get("final_url") or u
        for em in extract_emails(body):
            domain = em.split("@", 1)[-1]
            if not same_registrable(domain, site_reg):
                continue
            page_url = normalize_url(final or u)
            if page_url not in email_pages[em]:
                email_pages[em].append(page_url)
        time.sleep(0.05)

    emails_out = [
        {"email": e, "pages": pages}
        for e, pages in sorted(email_pages.items(), key=lambda kv: kv[0])
    ]

    # mutate note
    text = path.read_text(encoding="utf-8", errors="replace")
    new_text = text
    wrote_section = False
    wrote_fm = False

    if emails_out:
        bullets = [
            f"- `{row['email']}` — found on: {', '.join(row['pages'])}"
            for row in emails_out
        ]
        new_text = upsert_emails_section(new_text, bullets)
        wrote_section = True
        existing = fm_get(new_text, "email").strip()
        if not existing or existing.lower() in {"n/a", "none", "na", "null", "-"}:
            best = pick_best_inbox([r["email"] for r in emails_out])
            if best:
                new_text = set_fm(new_text, "email", best)
                wrote_fm = True

    stamp = vault_stamp()
    n = len(emails_out)
    research_line = (
        f"- {stamp}: website emails via enrich --layer emails "
        f"(sitemap/pages, {pages_fetched} pages, {n} same-domain email(s)). "
        f"No Google Places. Never invent emails."
    )
    if "## Research" in new_text:
        if "website emails via enrich --layer emails" not in new_text:
            new_text = new_text.replace("## Research", f"## Research\n{research_line}", 1)
        else:
            # still append dated line if today's stamp not present
            if research_line not in new_text:
                new_text = new_text.replace("## Research", f"## Research\n{research_line}", 1)
    else:
        new_text = new_text.rstrip() + f"\n\n## Research\n{research_line}\n"

    mutated = new_text != text
    if mutated:
        path.write_text(new_text, encoding="utf-8")

    return {
        "slug": path.stem,
        "website": website,
        "emails": emails_out,
        "pages_fetched": pages_fetched,
        "page_outcomes": page_outcomes,
        "errors": errors,
        "status": "wrote" if mutated else "no-change",
        "wrote_section": wrote_section,
        "wrote_fm_email": wrote_fm,
        "path": str(path),
    }


def write_jsonl(vault: Path, rows: list[dict], dry_run: bool) -> Path | None:
    day = vault_day()
    runs = vault / "Sources" / "runs"
    out = runs / f"website-emails-{day}.jsonl"
    if dry_run:
        print(f"dry-run jsonl would write {out} rows={len(rows)}")
        return None
    runs.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for r in rows:
            slim = {
                "slug": r.get("slug"),
                "website": r.get("website"),
                "emails": r.get("emails") or [],
                "pages_fetched": r.get("pages_fetched", 0),
                "page_outcomes": r.get("page_outcomes") or [],
                "errors": r.get("errors") or [],
            }
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    print(f"jsonl\t{out}\trows={len(rows)}")
    return out


def write_prove(vault: Path, category: str, stats: dict) -> Path:
    day = vault_day()
    cat = (category or "all").lower()
    path = vault / "Sources" / f"enrich-emails-{cat}-{day}.md"
    samples = stats.get("samples") or []
    rejects = stats.get("rejects") or []
    body = f"""---
type: source
name: "enrich-emails-{cat}-{day}"
---

# Prove log — enrich/emails — {day}

## Goal
Enrich notes layer=emails (sitemap / website crawl → same-domain emails)

## Mode
enrich --layer emails

## Counts
| Metric | Value |
|---|---|
| notes processed | {stats.get('processed', 0)} |
| notes mutated | {stats.get('mutated', 0)} |
| emails found (total unique) | {stats.get('emails_total', 0)} |
| skipped (no website / directory) | {stats.get('skipped', 0)} |
| dry-run | {stats.get('dry_run', False)} |

## Sources / recipes used
- website_emails.py (sitemap.xml / sitemap_index.xml / robots.txt Sitemap:)
- UA: OutreachTools/1.0
- Same registrable domain only; never invent emails; no Google Places

## Sample
{chr(10).join(str(i+1)+'. '+s for i,s in enumerate(samples[:20]))}

## Rejects / skips / errors
{chr(10).join('- '+r for r in rejects[:40])}

## Blind-spot check
- Directory hosts written to website:: 0
- linkedin.com never written to website:
- Free-mail (gmail/yahoo/…) kept only if firm domain (never)
- Layer: emails

## Artifacts
- `{path}`
- `Sources/runs/website-emails-{day}.jsonl`
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="website_emails")
    ap.add_argument("--category", default=None)
    ap.add_argument("--practice", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    cands = list_candidates(
        vault,
        category=args.category,
        practice=args.practice,
        limit=args.limit,
        slug=args.slug,
    )

    if not cands:
        print("no candidates with http(s) website:")
        print("push-backup\tskip\tno-vault-mutation")
        if not args.dry_run:
            prove = write_prove(
                vault,
                args.category or "all",
                {
                    "processed": 0,
                    "mutated": 0,
                    "emails_total": 0,
                    "skipped": 0,
                    "dry_run": False,
                    "samples": [],
                    "rejects": ["no candidates"],
                },
            )
            print(f"prove\t{prove}")
        else:
            print("push-backup\tskip\tdry-run")
        return 0

    results: list[dict] = []
    samples: list[str] = []
    rejects: list[str] = []
    mutated_n = 0
    emails_total = 0

    for path, site, firm in cands:
        print(f"enrich\t{path.stem}\t{site}")
        row = enrich_one(path, site, max_pages=args.max_pages, dry_run=args.dry_run)
        results.append(row)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "slug": row["slug"],
                        "website": row["website"],
                        "pages_planned_n": row.get("pages_planned_n"),
                        "pages_planned_sample": (row.get("pages_planned") or [])[:12],
                        "errors": row.get("errors"),
                        "status": "dry-run",
                    },
                    ensure_ascii=False,
                )
            )
            samples.append(
                f"{row['slug']}: plan {row.get('pages_planned_n')} pages from {site}"
            )
        else:
            print(
                json.dumps(
                    {
                        "slug": row["slug"],
                        "website": row["website"],
                        "emails": row.get("emails"),
                        "pages_fetched": row.get("pages_fetched"),
                        "status": row.get("status"),
                        "errors": row.get("errors"),
                    },
                    ensure_ascii=False,
                )
            )
            n = len(row.get("emails") or [])
            emails_total += n
            if row.get("status") == "wrote":
                mutated_n += 1
            samples.append(
                f"{row['slug']}: {n} email(s), pages_fetched={row.get('pages_fetched')}"
            )
            for err in (row.get("errors") or [])[:5]:
                rejects.append(f"{row['slug']}: {err}")

    write_jsonl(vault, results, dry_run=args.dry_run)

    stats = {
        "processed": len(results),
        "mutated": mutated_n,
        "emails_total": emails_total,
        "skipped": 0,
        "dry_run": args.dry_run,
        "samples": samples,
        "rejects": rejects,
    }

    if args.dry_run:
        print(f"dry-run notes: {len(results)}")
        print("push-backup\tskip\tdry-run")
        return 0

    prove = write_prove(vault, args.category or "all", stats)
    print(f"processed\t{stats['processed']}")
    print(f"mutated\t{stats['mutated']}")
    print(f"emails_total\t{stats['emails_total']}")
    print(f"prove\t{prove}")

    try:
        from push_backup import maybe_after_mutation

        maybe_after_mutation(
            mutated=mutated_n > 0,
            dry_run=False,
            no_push=args.no_push,
            vault=vault,
        )
    except Exception as e:
        print(f"push-backup_error\t{e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(run())

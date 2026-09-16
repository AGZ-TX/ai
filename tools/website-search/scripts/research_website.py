#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import html as html_lib
import http.client
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

UA = "OutreachTools/1.0"

CONNECT_TIMEOUT = 6.0
READ_TIMEOUT = 22.0
RETRY_BACKOFFS = (0.5, 1.5, 3.0)
MAX_ATTEMPTS = 1 + len(RETRY_BACKOFFS)

COMMON_PATHS = (
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/people",
    "/blog",
    "/pricing",
    "/services",
    "/service",
    "/attorneys",
    "/staff",
    "/faq",
    "/success-stories",
    "/case-studies",
)

PRIORITY_RE = re.compile(
    r"/(contact|about|team|our-team|people|staff|attorney|attorneys|bio|bios|"
    r"lawyer|lawyers|our-firm|meet|leadership|partners?|members?|"
    r"blog|post|posts|service|services|practice|practices|pricing|faq|"
    r"case|cases|success|success-stories|case-studies?)(/|$)",
    re.I,
)

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
MAILTO_RE = re.compile(r"mailto:([^\s\"'<>?#]+)", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    re.I | re.S,
)
META_DESC_RE2 = re.compile(
    r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
    re.I | re.S,
)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
HREF_RE = re.compile(r"""href=["']([^"'#]+)["']""", re.I)
JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
LLMS_TXT_LINK_RE = re.compile(
    r"""href=["']([^"']*llms\.txt[^"']*)["']""", re.I
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
HORIZ_WS_RE = re.compile(r"[^\S\n]+")
MULTI_NL_RE = re.compile(r"\n{3,}")
SCRIPTISH_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.I | re.S)
BLOCK_END_RE = re.compile(
    r"</(?:p|div|h[1-6]|li|tr|section|article|blockquote|header|footer)\s*>|<br\b[^>]*/?>",
    re.I,
)
HEADINGS_RE = re.compile(r"<h([1-3])\b[^>]*>(.*?)</h\1>", re.I | re.S)
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
ALT_ATTR_RE = re.compile(r"""\balt\s*=\s*(["'])(.*?)\1""", re.I | re.S)
LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)
BUTTON_RE = re.compile(r"<button\b[^>]*>(.*?)</button>", re.I | re.S)
INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.I)
INPUT_TYPE_RE = re.compile(
    r"""\btype\s*=\s*["']?(submit|button|image)["']?\b""",
    re.I,
)
ROLE_BUTTON_RE = re.compile(
    r"""<(?P<tag>[a-zA-Z][\w:-]*)\b(?P<attrs>[^>]*\brole\s*=\s*["']button["'][^>]*)>(?P<inner>.*?)</(?P=tag)\s*>""",
    re.I | re.S,
)
A_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)
QUOTED_ATTR_RE = re.compile(r"""\b([^\s=]+)\s*=\s*(["'])(.*?)\2""", re.I | re.S)
CTA_WORDS_RE = re.compile(
    r"\b(?:contact|call|book|schedule|request|get|start|apply|sign|learn|"
    r"subscribe|download|claim|talk|meet|quote|consult|hire|email|text|"
    r"chat|shop|buy|order|join|register|reserve|send|submit|free|now|"
    r"today|click|read|more|demo|trial|pricing)\b",
    re.I,
)

HEADING_CAP = 40
IMAGE_ALT_CAP = 30
LIST_ITEM_CAP = 40
CTA_CAP = 20
FULL_TEXT_CHAR_CAP = 200_000
DIGEST_PREVIEW_WORDS = 400

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

AI_BOTS = ("GPTBot", "ClaudeBot", "Google-Extended", "PerplexityBot")

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def vault_day() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")


def vault_stamp() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z")


def registrable_domain(host: str) -> str:
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


def slug_from_host(host: str) -> str:
    h = registrable_domain(host) or host
    h = h.replace(".", "-")
    return re.sub(r"[^a-z0-9\-]+", "-", h.lower()).strip("-") or "site"


def normalize_start(url: str) -> str:
    u = (url or "").strip()
    if not u:
        raise ValueError("empty url")
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = p.path or "/"
    return urlunparse((scheme, netloc, path, "", "", ""))


def origin_of(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, "", "", "", ""))


def normalize_url(url: str, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    p = urlparse(url)
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = p.path or "/"
    return urlunparse((scheme, netloc, path, "", "", ""))


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, socket.timeout):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout)):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in name


def _is_retryable_conn(exc: BaseException) -> bool:
    if _is_timeout(exc):
        return True
    if isinstance(exc, (ConnectionError, ConnectionResetError, BrokenPipeError, OSError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, http.client.RemoteDisconnected):
        return True
    return False


def _set_read_timeout(conn: http.client.HTTPConnection, read_timeout: float) -> None:
    conn.connect()
    if conn.sock is not None:
        conn.sock.settimeout(read_timeout)


def _http_outcome(
    status: str,
    http_code: int,
    final_url: str,
    body: str,
    attempts: int,
    error: str | None,
) -> dict:
    return {
        "status": status,
        "http_code": http_code,
        "final_url": final_url,
        "body": body,
        "attempts": attempts,
        "error": error,
    }


def _is_ok_http(code: int) -> bool:
    return 200 <= code < 400


def _is_client_http(code: int) -> bool:
    return 400 <= code < 500


def _http_get_once(
    url: str,
    *,
    connect_timeout: float = CONNECT_TIMEOUT,
    read_timeout: float = READ_TIMEOUT,
    max_redirects: int = 8,
) -> tuple[int, str, str]:
    current = url
    for _ in range(max_redirects + 1):
        p = urlparse(current)
        if p.scheme not in ("http", "https"):
            raise ValueError(f"unsupported scheme: {p.scheme}")
        host = p.hostname
        if not host:
            raise ValueError(f"no host in url: {current}")
        port = p.port
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
                host, port or 443, timeout=connect_timeout, context=ctx
            )
        else:
            conn = http.client.HTTPConnection(host, port or 80, timeout=connect_timeout)
        try:
            _set_read_timeout(conn, read_timeout)
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            code = int(resp.status)
            if code in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                conn.close()
                if not loc:
                    return code, current, text
                current = urljoin(current, loc)
                continue
            return code, current, text
        finally:
            try:
                conn.close()
            except Exception:
                pass
    raise urllib.error.URLError(f"too many redirects for {url}")


def http_get_with_retries(
    url: str,
    *,
    connect_timeout: float = CONNECT_TIMEOUT,
    read_timeout: float = READ_TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    attempts = 0
    last_error: str | None = None
    last_code = 0
    final_url = url
    body = ""
    outcome_status = "error"

    while attempts < max_attempts:
        attempts += 1
        try:
            code, final_url, body = _http_get_once(
                url, connect_timeout=connect_timeout, read_timeout=read_timeout
            )
            last_code = code
            if _is_ok_http(code):
                return _http_outcome("ok", code, final_url, body, attempts, None)
            if _is_client_http(code):
                return _http_outcome(
                    "http_error", code, final_url, body, attempts, f"HTTP {code}"
                )
            last_error = f"HTTP {code}"
            outcome_status = "http_error"
            if attempts < max_attempts:
                time.sleep(RETRY_BACKOFFS[min(attempts - 1, len(RETRY_BACKOFFS) - 1)])
                continue
            return _http_outcome(
                "http_error", code, final_url, body, attempts, last_error
            )
        except Exception as e:
            last_error = str(e)
            if _is_timeout(e):
                outcome_status = "timeout"
            elif _is_retryable_conn(e):
                outcome_status = "error"
            else:
                return _http_outcome("error", 0, url, "", attempts, last_error)
            if attempts < max_attempts and _is_retryable_conn(e):
                time.sleep(RETRY_BACKOFFS[min(attempts - 1, len(RETRY_BACKOFFS) - 1)])
                continue
            break

    return _http_outcome(
        outcome_status, last_code, final_url, body, attempts, last_error
    )


def strip_tags(s: str) -> str:
    s = TAG_RE.sub(" ", s or "")
    s = html_lib.unescape(s)
    return WS_RE.sub(" ", s).strip()


def _quoted_attr(tag: str, name: str) -> str:
    for m in QUOTED_ATTR_RE.finditer(tag or ""):
        if m.group(1).lower() == name.lower():
            return html_lib.unescape(m.group(3)).strip()
    return ""


def _unique_capped(items: list[str], cap: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        text = (raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= cap:
            break
    return out


def _clean_page_text(html: str) -> str:
    text = SCRIPTISH_RE.sub("", html or "")
    text = BLOCK_END_RE.sub("\n", text)
    text = TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    lines = [HORIZ_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return MULTI_NL_RE.sub("\n\n", text).strip()


def _store_full_text(cleaned: str, include_full_text: bool) -> tuple[str, bool]:
    if not include_full_text:
        return "", False
    if len(cleaned) <= FULL_TEXT_CHAR_CAP:
        return cleaned, False
    window = cleaned[:FULL_TEXT_CHAR_CAP]
    cut = None
    for i in range(len(window) - 1, -1, -1):
        if window[i].isspace():
            cut = i
            break
    if cut is None or cut == 0:
        return window, True
    return window[:cut], True


def _extract_headings(html: str) -> list[str]:
    texts = [strip_tags(m.group(2)) for m in HEADINGS_RE.finditer(html or "")]
    return _unique_capped(texts, HEADING_CAP)


def _extract_image_alts(html: str) -> list[str]:
    alts: list[str] = []
    for tag in IMG_TAG_RE.finditer(html or ""):
        am = ALT_ATTR_RE.search(tag.group(0))
        if not am:
            continue
        alts.append(html_lib.unescape(am.group(2)).strip())
    return _unique_capped(alts, IMAGE_ALT_CAP)


def _extract_list_items(html: str) -> list[str]:
    texts = [strip_tags(m.group(1)) for m in LI_RE.finditer(html or "")]
    return _unique_capped(texts, LIST_ITEM_CAP)


def _extract_cta_like(html: str) -> list[str]:
    labels: list[str] = []
    src = html or ""
    for m in BUTTON_RE.finditer(src):
        labels.append(strip_tags(m.group(1)))
    for m in INPUT_TAG_RE.finditer(src):
        tag = m.group(0)
        if not INPUT_TYPE_RE.search(tag):
            continue
        labels.append(
            _quoted_attr(tag, "value")
            or _quoted_attr(tag, "aria-label")
            or _quoted_attr(tag, "alt")
        )
    for m in ROLE_BUTTON_RE.finditer(src):
        labels.append(
            strip_tags(m.group("inner")) or _quoted_attr(m.group("attrs"), "aria-label")
        )
    for m in A_RE.finditer(src):
        label = strip_tags(m.group(1))
        if not (2 <= len(label) <= 48):
            continue
        if not CTA_WORDS_RE.search(label):
            continue
        labels.append(label)
    return _unique_capped(labels, CTA_CAP)


def parse_sitemap_locs(xml_text: str) -> tuple[list[str], list[str]]:
    pages: list[str] = []
    nested: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, re.I):
            pages.append(m.group(1).strip())
        return pages, nested

    tag = root.tag.lower() if isinstance(root.tag, str) else ""
    local = tag.split("}")[-1] if "}" in tag else tag

    def children(name: str):
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


def parse_ai_bot_robots(robots_text: str) -> dict[str, dict]:
    lines = robots_text.splitlines()
    agents: dict[str, dict] = {b: {"allows": [], "disallows": []} for b in AI_BOTS}
    current: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            ua = line.split(":", 1)[1].strip()
            current = []
            for b in AI_BOTS:
                if ua.lower() == b.lower() or ua == "*":
                    current.append(b if ua != "*" else "*")
            if ua == "*":
                current = ["*"]
            continue
        if not current:
            continue
        if low.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            for c in current:
                if c == "*":
                    for b in AI_BOTS:
                        agents[b]["disallows"].append(path)
                elif c in agents:
                    agents[c]["disallows"].append(path)
        elif low.startswith("allow:"):
            path = line.split(":", 1)[1].strip()
            for c in current:
                if c == "*":
                    for b in AI_BOTS:
                        agents[b]["allows"].append(path)
                elif c in agents:
                    agents[c]["allows"].append(path)
    summary = {}
    for b, data in agents.items():
        dis = [d for d in data["disallows"] if d != ""]
        alw = data["allows"]
        if any(d == "/" for d in dis) and not alw:
            signal = "disallow-all"
        elif any(d == "/" for d in dis):
            signal = "disallow-/ with allows"
        elif dis:
            signal = f"partial-disallow ({len(dis)} rules)"
        elif alw:
            signal = f"allow-rules ({len(alw)})"
        else:
            signal = "no-specific-rules"
        summary[b] = {
            "signal": signal,
            "allows": alw[:8],
            "disallows": dis[:8],
        }
    return summary


def discover_urls(
    start_url: str,
    errors: list[str],
    *,
    fetch_all: bool,
    priority_only: bool,
) -> tuple[list[str], dict]:
    start = normalize_start(start_url)
    origin = origin_of(start)
    site_host = host_of(start)
    site_reg = registrable_domain(site_host)
    found: list[str] = []
    seen: set[str] = set()
    meta: dict = {
        "origin": origin,
        "site_host": site_host,
        "site_reg": site_reg,
        "sitemap_urls_raw": 0,
        "sitemap_filtered": False,
        "sitemaps_fetched": [],
        "robots_status": None,
    }

    def add(u: str) -> None:
        if not u:
            return
        nu = normalize_url(u, origin)
        h = host_of(nu)
        if registrable_domain(h) != site_reg:
            return
        key = nu.rstrip("/").lower() or nu.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(nu)

    add(start)
    add(origin + "/")
    for path in COMMON_PATHS:
        add(origin + path)
        add(origin + path + "/")

    sitemap_seeds = [origin + "/sitemap.xml", origin + "/sitemap_index.xml"]
    robots = http_get_with_retries(origin + "/robots.txt")
    code = int(robots["http_code"] or 0)
    body = robots.get("body") or ""
    err = None if robots["status"] == "ok" else robots.get("error")
    meta["robots_status"] = code
    if err and code == 0:
        errors.append(f"robots.txt:{err}")
    elif code == 200:
        sitemap_seeds = robots_sitemaps(body) + sitemap_seeds
        meta["ai_bots"] = parse_ai_bot_robots(body)
        meta["robots_body_present"] = True
    else:
        meta["ai_bots"] = {
            b: {"signal": "robots-unreachable", "allows": [], "disallows": []}
            for b in AI_BOTS
        }
        meta["robots_body_present"] = False

    queue = list(dict.fromkeys(sitemap_seeds))
    fetched_smaps: set[str] = set()
    all_page_locs: list[str] = []
    max_smaps = 25

    while queue and len(fetched_smaps) < max_smaps:
        sm_url = queue.pop(0)
        if sm_url in fetched_smaps:
            continue
        fetched_smaps.add(sm_url)
        got = http_get_with_retries(sm_url)
        code = int(got["http_code"] or 0)
        final = got["final_url"] or sm_url
        body = got.get("body") or ""
        err = None if got["status"] == "ok" else got.get("error")
        if err or code >= 400 or code == 0:
            errors.append(f"sitemap:{sm_url}:{err or code}")
            continue
        if "<" not in body[:500]:
            continue
        meta["sitemaps_fetched"].append(final or sm_url)
        pages, nested = parse_sitemap_locs(body)
        all_page_locs.extend(pages)
        for n in nested:
            if n not in fetched_smaps:
                queue.append(n)
        time.sleep(0.05)

    meta["sitemap_urls_raw"] = len(all_page_locs)

    if len(all_page_locs) > 500 and not fetch_all:
        filtered = [u for u in all_page_locs if PRIORITY_RE.search(urlparse(u).path or "")]
        errors.append(f"sitemap_large:{len(all_page_locs)}_filtered_to_{len(filtered)}")
        all_page_locs = filtered
        meta["sitemap_filtered"] = True

    if priority_only:
        all_page_locs = [
            u for u in all_page_locs if PRIORITY_RE.search(urlparse(u).path or "")
        ]

    for u in all_page_locs:
        add(u)

    return found, meta


def prioritize_urls(urls: list[str], max_pages: int) -> list[str]:
    def score(u: str) -> tuple[int, int, str]:
        path = urlparse(u).path or "/"
        pri = 0 if PRIORITY_RE.search(path) else 1
        if path in ("", "/"):
            pri = -1
        return (pri, len(path), u.lower())

    ordered = sorted(urls, key=score)
    return ordered[: max(1, max_pages)]


def extract_emails(html: str) -> list[str]:
    text = html_lib.unescape(html or "")
    found: list[str] = []
    seen: set[str] = set()
    for m in MAILTO_RE.finditer(text):
        addr = m.group(1).strip().split("?")[0].strip().replace("%40", "@")
        if "@" in addr:
            e = addr.lower()
            if e not in seen:
                seen.add(e)
                found.append(e)
    for m in EMAIL_RE.finditer(text):
        e = m.group(1).lower().rstrip(".,;:)>]")
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")):
            continue
        if e not in seen:
            seen.add(e)
            found.append(e)
    return found


def extract_schema_types(html: str) -> list[str]:
    types: list[str] = []
    seen: set[str] = set()
    for m in JSONLD_RE.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        def walk(obj):
            if isinstance(obj, dict):
                t = obj.get("@type")
                if isinstance(t, str) and t not in seen:
                    seen.add(t)
                    types.append(t)
                elif isinstance(t, list):
                    for x in t:
                        if isinstance(x, str) and x not in seen:
                            seen.add(x)
                            types.append(x)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        walk(data)
    return types


def extract_page(
    html: str,
    final_url: str,
    site_reg: str,
    include_emails: bool,
    include_full_text: bool = True,
) -> dict:
    src = html or ""
    title_m = TITLE_RE.search(src)
    title = strip_tags(title_m.group(1)) if title_m else ""
    desc = ""
    dm = META_DESC_RE.search(src) or META_DESC_RE2.search(src)
    if dm:
        desc = strip_tags(dm.group(1))[:500]
    h1s = [strip_tags(m.group(1)) for m in H1_RE.finditer(src)][:5]
    cleaned = _clean_page_text(src)
    full_text, full_text_truncated = _store_full_text(cleaned, include_full_text)
    if include_full_text:
        word_count = len(full_text.split())
    else:
        word_count = len(cleaned.split())

    same_site_links: list[str] = []
    seen_links: set[str] = set()
    for m in HREF_RE.finditer(src):
        href = m.group(1).strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        try:
            nu = normalize_url(href, final_url)
        except Exception:
            continue
        if registrable_domain(host_of(nu)) != site_reg:
            continue
        key = nu.rstrip("/").lower()
        if key in seen_links:
            continue
        seen_links.add(key)
        same_site_links.append(nu)
        if len(same_site_links) >= 12:
            break

    emails: list[str] = []
    if include_emails:
        for em in extract_emails(src):
            domain = em.split("@", 1)[-1]
            if same_registrable(domain, site_reg):
                emails.append(em)

    schema_types = extract_schema_types(src)
    llms_mentions = [m.group(1) for m in LLMS_TXT_LINK_RE.finditer(src)]

    return {
        "title": title,
        "meta_description": desc,
        "h1s": h1s,
        "word_count": word_count,
        "outbound_same_site_sample": same_site_links,
        "emails": emails,
        "schema_types": schema_types,
        "llms_txt_mentions": llms_mentions,
        "full_text": full_text,
        "full_text_truncated": full_text_truncated,
        "headings": _extract_headings(src),
        "image_alts": _extract_image_alts(src),
        "list_items": _extract_list_items(src),
        "cta_like": _extract_cta_like(src),
    }


def theme_hints(pages: list[dict]) -> list[str]:
    bag: Counter[str] = Counter()
    stop = {
        "the", "and", "for", "with", "from", "your", "our", "this", "that", "are",
        "was", "were", "been", "have", "has", "had", "not", "but", "you", "all",
        "can", "home", "page", "website", "official", "llc", "inc", "ltd", "pllc",
        "pc", "com", "www", "https", "http",
    }
    for p in pages:
        if p.get("status") != "ok":
            continue
        blob = " ".join([p.get("title") or ""] + (p.get("h1s") or []))
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", blob.lower()):
            if tok in stop:
                continue
            bag[tok] += 1
    return [f"{w} ({n})" for w, n in bag.most_common(25) if n >= 2] or [
        f"{w} ({n})" for w, n in bag.most_common(15)
    ]


def probe_llms_txt(origin: str) -> dict:
    url = origin.rstrip("/") + "/llms.txt"
    r = http_get_with_retries(url)
    out = {
        "url": url,
        "status": r["http_code"],
        "outcome": r["status"],
        "final_url": r["final_url"],
        "present": False,
        "bytes": 0,
        "preview": "",
        "error": r.get("error"),
        "attempts": r.get("attempts"),
    }
    body = r.get("body") or ""
    if r["status"] == "ok" and body:
        head = body.lstrip()[:200].lower()
        if head.startswith("<!doctype") or head.startswith("<html"):
            out["present"] = False
            out["preview"] = "(HTML response, not plain llms.txt)"
        else:
            out["present"] = True
            out["bytes"] = len(body.encode("utf-8", errors="replace"))
            out["preview"] = body[:400].replace("\r\n", "\n")
    return out


def outcome_counts(pages: list[dict]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for p in pages:
        c[p.get("status") or "error"] += 1
    return {
        "ok": c.get("ok", 0),
        "timeout": c.get("timeout", 0),
        "http_error": c.get("http_error", 0),
        "error": c.get("error", 0),
        "total": len(pages),
    }


def empty_page_rec(url: str) -> dict:
    return {
        "url": url,
        "final_url": url,
        "status": "error",
        "http_code": 0,
        "attempts": 0,
        "error": None,
        "title": "",
        "meta_description": "",
        "h1s": [],
        "word_count": 0,
        "outbound_same_site_sample": [],
        "emails": [],
        "schema_types": [],
        "llms_txt_mentions": [],
        "full_text": "",
        "full_text_truncated": False,
        "headings": [],
        "image_alts": [],
        "list_items": [],
        "cta_like": [],
    }


def fetch_one_page(
    u: str,
    site_reg: str,
    include_emails: bool,
    include_full_text: bool = True,
) -> dict:
    r = http_get_with_retries(u)
    rec = empty_page_rec(u)
    rec["final_url"] = r.get("final_url") or u
    rec["status"] = r["status"]
    rec["http_code"] = r.get("http_code") or 0
    rec["attempts"] = r.get("attempts") or 1
    rec["error"] = r.get("error")
    body = r.get("body") or ""
    if r["status"] == "ok" and body:
        head = body[:4000].lower()
        if (
            "<html" in head
            or "<!doctype" in body[:500].lower()
            or "<title" in head
        ):
            extracted = extract_page(
                body, rec["final_url"], site_reg, include_emails, include_full_text
            )
            rec.update(extracted)
    return rec


def _fetch_pages(
    urls: list[str],
    site_reg: str,
    include_emails: bool,
    concurrency: int,
    *,
    pace: float = 0.0,
    include_full_text: bool = True,
) -> list[dict]:
    if not urls:
        return []
    concurrency = max(1, min(8, int(concurrency)))
    if concurrency <= 1 or len(urls) <= 1:
        out: list[dict] = []
        for u in urls:
            out.append(fetch_one_page(u, site_reg, include_emails, include_full_text))
            if pace:
                time.sleep(pace)
        return out
    by_url: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {
            ex.submit(fetch_one_page, u, site_reg, include_emails, include_full_text): u
            for u in urls
        }
        for fut in as_completed(futs):
            by_url[futs[fut]] = fut.result()
    return [by_url[u] for u in urls]


def write_markdown(path: Path, run: dict) -> None:
    site = run["site"]
    pages = run["pages"]
    emails_map = run.get("emails_map") or {}
    llms = run.get("llms_txt") or {}
    bots = run.get("ai_bots") or {}
    day = run.get("day") or vault_day()
    stamp = run.get("stamp") or vault_stamp()
    oc = run.get("outcome_counts") or outcome_counts(pages)

    ok_n = oc.get("ok", 0)
    lines = [
        "---",
        "type: research",
        f'name: "site research {site.get("reg") or site.get("host")}"',
        f"date: {day}",
        f'source: "{run.get("start_url")}"',
        f"skill: website-search",
        "---",
        "",
        f"# Site research: {site.get('reg') or site.get('host')}",
        "",
        f"Generated {stamp} via `research_website.py` (website-search). "
        f"UA `{UA}`. Not a single-page WebFetch skim.",
        "",
        "## Overview",
        "",
        f"- Start URL: `{run.get('start_url')}`",
        f"- Origin: `{site.get('origin')}`",
        f"- Registrable domain: `{site.get('reg')}`",
        f"- Pages discovered (pre-cap): {run.get('discovered_n', 0)}",
        f"- Pages fetched: {len(pages)} (ok {ok_n}; "
        f"timeout {oc.get('timeout', 0)}; http_error {oc.get('http_error', 0)}; "
        f"error {oc.get('error', 0)})",
        f"- Sitemap URLs raw: {run.get('sitemap_urls_raw', 0)}"
        + (" (priority-filtered)" if run.get("sitemap_filtered") else ""),
        f"- Sitemaps fetched: {len(run.get('sitemaps_fetched') or [])}",
        f"- Concurrency: {run.get('flags', {}).get('concurrency', 1)}",
        "",
        "## Page inventory",
        "",
        "| URL | Title | Outcome | HTTP | Attempts | Words |",
        "|---|---|---|---|---|---|",
    ]
    for p in pages:
        title = (p.get("title") or "").replace("|", "\\|")[:80]
        err = (p.get("error") or "").replace("|", "\\|")[:40]
        outcome = p.get("status") or ""
        if err and outcome != "ok":
            outcome = f"{outcome} ({err})"
        lines.append(
            f"| {p.get('final_url') or p.get('url')} | {title} | {outcome} | "
            f"{p.get('http_code', 0)} | {p.get('attempts', 1)} | {p.get('word_count', 0)} |"
        )

    flags = run.get("flags") or {}
    full_text_on = flags.get("full_text", True)
    lines.extend(["", "## Page copy", ""])
    if not full_text_on:
        lines.append("Full text was disabled (`--no-full-text`).")
        lines.append("")
    for p in pages:
        if p.get("status") != "ok":
            continue
        page_title = (p.get("title") or "").replace("\n", " ").strip()
        page_url = p.get("final_url") or p.get("url") or ""
        lines.append(f"### {page_title or page_url}")
        lines.append("")
        meta = (p.get("meta_description") or "").replace("\n", " ").strip()
        if meta:
            lines.append(meta)
            lines.append("")
        headings = [h.replace("\n", " ").strip() for h in (p.get("headings") or []) if h]
        if headings:
            shown = ", ".join(headings)
            if len(shown) > 400:
                shown = shown[:400].rsplit(" ", 1)[0] + " ..."
            lines.append(f"Headings: {shown}")
            lines.append("")
        ctas = [c.replace("\n", " ").strip() for c in (p.get("cta_like") or []) if c]
        if ctas:
            lines.append(f"CTAs: {', '.join(ctas)}")
            lines.append("")
        alts = [a.replace("\n", " ").strip() for a in (p.get("image_alts") or []) if a]
        if alts:
            shown = ", ".join(alts)
            if len(shown) > 400:
                shown = shown[:400].rsplit(" ", 1)[0] + " ..."
            lines.append(f"Image alts: {shown}")
            lines.append("")
        if full_text_on:
            preview = " ".join((p.get("full_text") or "").split()[:DIGEST_PREVIEW_WORDS])
            if preview:
                lines.append(preview)
                lines.append("")
            lines.append("full_text in JSON")
            lines.append("")

    lines.extend(["", "## Theme hints (from titles / h1s)", ""])
    hints = run.get("theme_hints") or []
    if hints:
        for h in hints:
            lines.append(f"- {h}")
    else:
        lines.append("- (insufficient title/h1 signal)")

    lines.extend(["", "## Emails (same-domain)", ""])
    if emails_map:
        for em, pages_found in sorted(emails_map.items()):
            lines.append(f"- `{em}` found on {', '.join(pages_found)}")
    else:
        lines.append("- none extracted (or `--include-emails` off)")

    lines.extend(["", "## LLM-readiness", ""])
    lines.append(
        f"- llms.txt: {'present' if llms.get('present') else 'absent or unreachable'} "
        f"(`{llms.get('url')}`, status {llms.get('status')})"
    )
    if llms.get("preview"):
        preview = llms["preview"].replace("\n", "\n  ")
        lines.append(f"- llms.txt preview:\n\n```\n{preview}\n```")
    schema_all: Counter[str] = Counter()
    for p in pages:
        for t in p.get("schema_types") or []:
            schema_all[t] += 1
    if schema_all:
        lines.append(
            "- schema.org @types seen: "
            + ", ".join(f"{t} ({n})" for t, n in schema_all.most_common(20))
        )
    else:
        lines.append("- schema.org @types seen: none in fetched pages")
    llms_mentions = []
    for p in pages:
        for m in p.get("llms_txt_mentions") or []:
            llms_mentions.append(f"{m} on {p.get('final_url') or p.get('url')}")
    if llms_mentions:
        lines.append("- in-page llms.txt link mentions:")
        for m in llms_mentions[:10]:
            lines.append(f"  - {m}")

    lines.extend(["", "### AI-bot robots signals", ""])
    if bots:
        for b in AI_BOTS:
            info = bots.get(b) or {}
            lines.append(f"- {b}: {info.get('signal', 'unknown')}")
    else:
        lines.append("- robots.txt not available; no bot signals")

    if run.get("errors"):
        lines.extend(["", "## Errors / notes", ""])
        for e in run["errors"][:40]:
            lines.append(f"- {e}")

    lines.extend(
        [
            "",
            "## Related skills",
            "",
            "- website-to-api. HAR then an API recipe when a known UI path is token-expensive.",
            "- outreach-leads. Reuse this digest before enrich. This skill does not write CRM notes.",
            "",
        ]
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _url_key(u: str) -> str:
    return (u or "").rstrip("/").lower()


def research(
    start_url: str,
    *,
    max_pages: int,
    fetch_all: bool,
    priority_only: bool,
    include_emails: bool,
    concurrency: int = 4,
    include_full_text: bool = True,
) -> dict:
    errors: list[str] = []
    start = normalize_start(start_url)
    discovered, meta = discover_urls(
        start, errors, fetch_all=fetch_all, priority_only=priority_only
    )
    to_fetch = prioritize_urls(discovered, max_pages)
    site_reg = meta["site_reg"]
    origin = meta["origin"]
    concurrency = max(1, min(8, int(concurrency)))

    email_pages: dict[str, list[str]] = defaultdict(list)
    pages = _fetch_pages(
        to_fetch,
        site_reg,
        include_emails,
        concurrency,
        pace=0.05,
        include_full_text=include_full_text,
    )
    for rec in pages:
        u = rec.get("url") or rec.get("final_url") or ""
        if rec.get("status") != "ok":
            errors.append(
                f"fetch:{rec.get('status')}:{u}:{rec.get('error') or rec.get('http_code')}"
            )
        for em in rec.get("emails") or []:
            page_url = normalize_url(rec.get("final_url") or u)
            if page_url not in email_pages[em]:
                email_pages[em].append(page_url)

    llms = probe_llms_txt(origin)
    stamp = vault_stamp()
    day = vault_day()
    oc = outcome_counts(pages)

    run = {
        "skill": "website-search",
        "ua": UA,
        "start_url": start,
        "day": day,
        "stamp": stamp,
        "site": {
            "origin": origin,
            "host": meta["site_host"],
            "reg": site_reg,
        },
        "discovered_n": len(discovered),
        "sitemap_urls_raw": meta.get("sitemap_urls_raw", 0),
        "sitemap_filtered": meta.get("sitemap_filtered", False),
        "sitemaps_fetched": meta.get("sitemaps_fetched") or [],
        "robots_status": meta.get("robots_status"),
        "ai_bots": meta.get("ai_bots") or {},
        "llms_txt": llms,
        "pages": pages,
        "emails_map": dict(sorted(email_pages.items())),
        "theme_hints": theme_hints(pages),
        "outcome_counts": oc,
        "errors": errors,
        "flags": {
            "max_pages": max_pages,
            "fetch_all": fetch_all,
            "priority_only": priority_only,
            "include_emails": include_emails,
            "full_text": include_full_text,
            "concurrency": concurrency,
            "connect_timeout": CONNECT_TIMEOUT,
            "read_timeout": READ_TIMEOUT,
            "max_attempts": MAX_ATTEMPTS,
        },
    }
    return run


def retry_failed_pages(
    prior: dict,
    *,
    include_emails: bool | None = None,
    include_full_text: bool | None = None,
    concurrency: int = 4,
) -> dict:
    pages_prior = list(prior.get("pages") or [])
    site = prior.get("site") or {}
    site_reg = site.get("reg") or registrable_domain(site.get("host") or "")
    flags = prior.get("flags") or {}
    if include_emails is None:
        include_emails = bool(flags.get("include_emails"))
    if include_full_text is None:
        include_full_text = bool(flags.get("full_text", True))
    concurrency = max(1, min(8, int(concurrency)))

    urls = [
        p.get("url") or p.get("final_url")
        for p in pages_prior
        if (p.get("status") or "") != "ok" and (p.get("url") or p.get("final_url"))
    ]
    refetched = {
        _url_key(rec.get("url") or rec.get("final_url") or ""): rec
        for rec in _fetch_pages(
            urls,
            site_reg,
            include_emails,
            concurrency,
            include_full_text=include_full_text,
        )
    }

    merged: list[dict] = []
    email_pages: dict[str, list[str]] = defaultdict(list)
    errors: list[str] = list(prior.get("errors") or [])
    errors.append(f"retry_failed:re-fetched_{len(urls)}_of_{len(pages_prior)}")

    seen_keys: set[str] = set()
    for p in pages_prior:
        k = _url_key(p.get("url") or p.get("final_url") or "")
        if not k or k in seen_keys:
            continue
        seen_keys.add(k)
        merged.append(refetched.get(k, p))

    for rec in merged:
        if rec.get("status") != "ok":
            continue
        for em in rec.get("emails") or []:
            page_url = normalize_url(rec.get("final_url") or rec.get("url") or "")
            if page_url and page_url not in email_pages[em]:
                email_pages[em].append(page_url)

    for em, pages_found in (prior.get("emails_map") or {}).items():
        for pu in pages_found:
            if pu not in email_pages[em]:
                email_pages[em].append(pu)

    oc = outcome_counts(merged)
    stamp = vault_stamp()
    day = vault_day()
    out = dict(prior)
    out["day"] = day
    out["stamp"] = stamp
    out["pages"] = merged
    out["emails_map"] = dict(sorted(email_pages.items()))
    out["theme_hints"] = theme_hints(merged)
    out["outcome_counts"] = oc
    out["errors"] = errors
    out["flags"] = dict(flags)
    out["flags"]["retry_failed"] = True
    out["flags"]["concurrency"] = concurrency
    out["flags"]["include_emails"] = include_emails
    out["flags"]["full_text"] = include_full_text
    origin = site.get("origin")
    if origin:
        out["llms_txt"] = probe_llms_txt(origin)
    return out


def _vault() -> Path:
    raw = os.environ.get("VAULT_ROOT")
    return Path(raw).expanduser().resolve() if raw else (Path.cwd() / "data")


def default_json_path(host: str) -> Path:
    day = vault_day()
    slug = slug_from_host(host)
    return _vault() / "Sources" / "runs" / f"research-{slug}-{day}.json"


def default_md_path(host: str) -> Path:
    day = vault_day()
    slug = slug_from_host(host)
    return _vault() / "Research" / f"site-{slug}-{day}.md"


def default_paths(host: str) -> tuple[Path, Path]:
    return default_md_path(host), default_json_path(host)


def exit_code_for_run(run_data: dict) -> int:
    pages = run_data.get("pages") or []
    oc = run_data.get("outcome_counts") or outcome_counts(pages)
    return 0 if oc.get("ok", 0) > 0 else 2


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="research_website",
        description="Crawl sitemap + pages. Write a JSON run artifact. Markdown is off unless requested.",
    )
    ap.add_argument(
        "url",
        nargs="?",
        default=None,
        help="https URL or bare domain (omit when using --retry-failed alone)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Write a markdown digest to this path (off unless passed)",
    )
    ap.add_argument(
        "--markdown",
        nargs="?",
        const="",
        default=None,
        help="Write a markdown digest. Optional path. Default is Research/site-<slug>-<day>.md",
    )
    ap.add_argument(
        "--json",
        dest="json_out",
        default=None,
        help="JSON run artifact path (primary output; default Sources/runs/)",
    )
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument(
        "--priority-only",
        action="store_true",
        help="Only keep priority-keyword paths from sitemap",
    )
    ap.add_argument(
        "--all",
        dest="fetch_all",
        action="store_true",
        help="Do not priority-filter when sitemap >500 URLs",
    )
    ap.add_argument(
        "--include-emails",
        action="store_true",
        help="Extract same-domain emails (like outreach-leads emails layer)",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Parallel page fetches within one site (default 4, cap 8)",
    )
    ap.add_argument(
        "--retry-failed",
        metavar="PRIOR_JSON",
        default=None,
        help="Re-fetch non-ok pages from a prior run JSON and merge",
    )
    ap.add_argument(
        "--no-full-text",
        action="store_true",
        help="Skip storing page body text (signals still extract)",
    )
    args = ap.parse_args(argv)

    concurrency = max(1, min(8, int(args.concurrency)))

    if args.retry_failed:
        prior_path = Path(args.retry_failed)
        if not prior_path.is_file():
            print(f"error\tprior json not found: {prior_path}", file=sys.stderr)
            return 2
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        run_data = retry_failed_pages(
            prior,
            include_emails=True if args.include_emails else None,
            include_full_text=False if args.no_full_text else None,
            concurrency=concurrency,
        )
        host = (run_data.get("site") or {}).get("host") or "site"
    else:
        if not args.url:
            print("error\turl required (or use --retry-failed)", file=sys.stderr)
            return 2
        try:
            start = normalize_start(args.url)
        except ValueError as e:
            print(f"error\t{e}", file=sys.stderr)
            return 2

        run_data = research(
            start,
            max_pages=args.max_pages,
            fetch_all=args.fetch_all,
            priority_only=args.priority_only,
            include_emails=args.include_emails,
            concurrency=concurrency,
            include_full_text=not args.no_full_text,
        )
        host = run_data["site"]["host"]

    out_js = Path(args.json_out) if args.json_out else default_json_path(host)
    out_md = None
    if args.out:
        out_md = Path(args.out)
    elif args.markdown is not None:
        out_md = Path(args.markdown) if args.markdown else default_md_path(host)

    if out_md is not None:
        write_markdown(out_md, run_data)
    out_js.parent.mkdir(parents=True, exist_ok=True)
    out_js.write_text(json.dumps(run_data, ensure_ascii=False, indent=2), encoding="utf-8")

    oc = run_data.get("outcome_counts") or outcome_counts(run_data.get("pages") or [])
    print(f"start\t{run_data.get('start_url')}")
    print(f"origin\t{(run_data.get('site') or {}).get('origin')}")
    print(f"discovered\t{run_data.get('discovered_n', 0)}")
    print(f"fetched\t{len(run_data.get('pages') or [])}")
    print(
        f"outcomes\tok={oc.get('ok', 0)}\ttimeout={oc.get('timeout', 0)}"
        f"\thttp_error={oc.get('http_error', 0)}\terror={oc.get('error', 0)}"
    )
    print(f"emails\t{len(run_data.get('emails_map') or {})}")
    llms = run_data.get("llms_txt") or {}
    print(f"llms_txt\t{llms.get('present')}\t{llms.get('status')}")
    if out_md is not None:
        print(f"digest\t{out_md}")
    print(f"json\t{out_js}")

    return exit_code_for_run(run_data)


if __name__ == "__main__":
    sys.exit(run())

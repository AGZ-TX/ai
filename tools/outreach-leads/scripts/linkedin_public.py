#!/usr/bin/env python3
"""Public LinkedIn HTML only. No keys, cookies, login, Voyager, or scraping service.

People are the public company employee sample, NOT a complete staff directory.
Hiring means company-matched listings observed, never proof a vacancy is unfilled.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

UA = "OutreachTools/1.0"
BASE = "https://www.linkedin.com"
GUEST_JOBS = BASE + "/jobs-guest/jobs/api/seeMoreJobPostings"
MAX_BYTES = 3_000_000
MASKED = {"linkedin member", "linkedin user", "member", "private member"}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: str) -> str:
    return " ".join(value.split()).strip()


def linkedin_url(value: str, kind: str = "company") -> str:
    """Strict canonical identity; reject deceptive hosts, credentials and paths."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        u = urlsplit(urljoin(BASE, value.strip()))
        host = (u.hostname or "").lower()
        if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
            return ""
        if host not in {"linkedin.com", "www.linkedin.com"} and not re.fullmatch(r"[a-z]{2,3}\.linkedin\.com", host):
            return ""
        path = unquote(u.path)
        match = re.fullmatch(r"/" + re.escape(kind) + r"/([\w-]+)/?(?:jobs/?)?", path) if kind == "company" else re.fullmatch(r"/in/([\w-]+)/?", path)
        if not match:
            return ""
        return f"{BASE}/{kind}/{match[1].lower()}/"
    except (ValueError, TypeError):
        return ""


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False)

    def walk(self):
        todo = [self]
        while todo:
            n = todo.pop()
            yield n
            todo.extend(reversed([c for c in n.children if isinstance(c, Node)]))

    def text(self) -> str:
        chunks = []
        todo = [self]
        while todo:
            n = todo.pop()
            if isinstance(n, str):
                chunks.append(n)
            elif n.tag not in {"script", "style"}:
                todo.extend(reversed(n.children))
        return clean(" ".join(chunks))

    def has_class(self, token: str) -> bool:
        return token in self.attrs.get("class", "").split()


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.count = 0
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 60_000 or len(self.stack) > 160:
            raise ValueError("HTML complexity limit")
        n = Node(tag, dict((k, v or "") for k, v in attrs), parent=self.stack[-1])
        self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def first_text(node: Node, *, classes=(), tags=()) -> str:
    for n in node.walk():
        if n.tag in tags or any(n.has_class(c) for c in classes):
            text = n.text()
            if text:
                return text
    return ""


@dataclass
class Page:
    url: str
    status: str
    html: str = ""
    http_status: int | None = None
    reason: str = ""

    def evidence(self) -> dict:
        return {"url": self.url, "status": self.status, "http_status": self.http_status, "reason": self.reason}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Validate every destination before following it ourselves.


def public_host(url: str) -> None:
    """Reject local/private destinations before connecting, including redirects."""
    u = urlsplit(url)
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
        raise ValueError("invalid public URL")
    if u.port not in (None, 80, 443):
        raise ValueError("nonstandard port")
    addresses = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("non-public destination")


def linkedin_request_allowed(url: str) -> bool:
    try:
        u = urlsplit(url)
        host = (u.hostname or "").lower()
        if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
            return False
        if host not in {"www.linkedin.com", "linkedin.com"} and not re.fullmatch(r"[a-z]{2,3}\.linkedin\.com", host):
            return False
        return bool(linkedin_url(url)) or u.path in {"/jobs/search", "/jobs/search/", "/jobs-guest/jobs/api/seeMoreJobPostings"}
    except ValueError:
        return False


def wall(html: str, url: str) -> bool:
    # Normal public pages contain sign-in CTAs. Those alone are NOT a login wall.
    if re.search(r"/(authwall|checkpoint|uas/login|login|signup)(?:[/?]|$)", url):
        return True
    head = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.S | re.I)
    title = clean(re.sub(r"<[^>]+>", " ", head[1])).lower() if head else ""
    return bool(re.search(r"security verification|authwall|verify (?:you are|your identity)|linkedin (?:login|sign in)|sign (?:in|up) \| linkedin", title)) or bool(re.search(r'<(?:form|main)\b[^>]*(?:id|action)=[\"\'][^\"\']*(?:challenge|checkpoint)', html, re.I))


class PublicHTTP:
    """Bounded, paced anonymous GETs; one block stops LinkedIn for this batch."""
    def __init__(self, delay: float = 2.0, timeout: float = 15.0):
        if not 1 <= delay <= 60 or not 1 <= timeout <= 60:
            raise ValueError("delay must be 1..60s and timeout 1..60s")
        self.delay, self.timeout = delay, timeout
        self.last = 0.0
        self.stopped = ""
        self.opener = build_opener(NoRedirect())

    def get(self, url: str, *, website: bool = False) -> Page:
        if not website and self.stopped:
            return Page(url, self.stopped, reason="batch stopped after LinkedIn refused access")
        original = url
        try:
            origin = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        except ValueError:
            return Page(original, "unsafe_url", reason="invalid URL")
        for hop in range(4):
            try:
                if not website and not linkedin_request_allowed(url):
                    return Page(original, "blocked" if wall("", url) else "unsafe_url", reason="redirect or URL outside public endpoint allowlist")
                if website and (urlsplit(url).hostname or "").lower().removeprefix("www.") != origin:
                    return Page(original, "unsafe_url", reason="official-site redirect changed host")
                public_host(url)
                if not website:
                    time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
                    self.last = time.monotonic()
                req = Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.8"})
                with self.opener.open(req, timeout=self.timeout) as response:
                    body = response.read(MAX_BYTES + 1)
                    code = response.status
                    content_type = response.headers.get("Content-Type", "")
                    charset = response.headers.get_content_charset() or "utf-8"
                if len(body) > MAX_BYTES:
                    return Page(original, "too_large", http_status=code)
                if content_type and not any(t in content_type.lower() for t in ("text/html", "application/xhtml")):
                    return Page(original, "parse_error", http_status=code, reason="expected HTML")
                html = body.decode(charset, errors="replace")
                if not website and wall(html, url):
                    self.stopped = "blocked"
                    return Page(original, "blocked", http_status=code, reason="login or verification wall")
                return Page(url, "ok", html, code)
            except HTTPError as e:
                if e.code in {301, 302, 303, 307, 308} and hop < 3:
                    url = urljoin(url, e.headers.get("Location", ""))
                    e.close()
                    if not website and wall("", url):
                        self.stopped = "blocked"
                        return Page(original, "blocked", http_status=302, reason="login redirect")
                    continue
                status = "rate_limited" if e.code == 429 else "blocked" if e.code in {401, 403, 999} else "not_found" if e.code == 404 else "http_error"
                reason = ("retry_after=" + e.headers.get("Retry-After", "unspecified")) if e.code == 429 else ""
                code = e.code
                e.close()
                if not website and status in {"blocked", "rate_limited"}:
                    self.stopped = status
                return Page(original, status, http_status=code, reason=reason)
            except (URLError, OSError, TimeoutError) as e:
                return Page(original, "network_error", reason=str(e)[:240])
            except (ValueError, LookupError) as e:
                return Page(original, "unsafe_url", reason=str(e)[:240])
        return Page(original, "http_error", reason="redirect limit")


def discover_company(html: str) -> str:
    """Only accept an unambiguous company link from the supplied official site."""
    root = Document(html).root
    links = {linkedin_url(n.attrs.get("href", "")) for n in root.walk() if n.tag == "a"}
    links.discard("")
    return next(iter(links)) if len(links) == 1 else ""


def parse_company(html: str, company: str, max_people: int = 100) -> dict:
    root = Document(html).root
    canonical = next((linkedin_url(n.attrs.get("href", "")) for n in root.walk() if n.tag == "link" and n.attrs.get("rel") == "canonical"), "")
    ids = set()
    for n in root.walk():
        if n.tag != "a":
            continue
        href = urljoin(BASE, n.attrs.get("href", ""))
        u = urlsplit(href)
        if u.hostname not in {"www.linkedin.com", "linkedin.com"} or not u.path.startswith("/jobs/"):
            continue
        # Only company job navigation, not unrelated job links in the footer.
        if n.text().lower() not in {"jobs", "see jobs", "view jobs", "see all jobs", "view all jobs"}:
            continue
        vals = parse_qs(u.query).get("f_C", [])
        if len(vals) == 1 and vals[0].isdigit():
            ids.add(vals[0])
    company_id = next(iter(ids)) if len(ids) == 1 else None
    requested_slug = company.rstrip("/").rsplit("/", 1)[-1]
    if canonical and canonical != company and requested_slug != company_id:
        return {"status": "company_mismatch", "contacts": [], "company_id": None, "masked": 0}
    company = canonical or company
    sections = []
    for n in root.walk():
        if n.tag != "section":
            continue
        heading = first_text(n, tags=("h2",)).lower()
        if n.attrs.get("data-test-id") == "employees-at" or heading.startswith("employees at "):
            sections.append(n)
    if not canonical and not company_id and not sections:
        return {"status": "parse_error", "contacts": [], "company_id": None, "masked": 0}
    contacts, seen, masked = [], set(), 0
    for section in sections:
        for a in section.walk():
            profile = linkedin_url(a.attrs.get("href", ""), "in") if a.tag == "a" else ""
            if not profile or profile in seen:
                continue
            card, ancestor = a, a
            while ancestor is not section:
                if ancestor.tag in {"li", "article"} or ancestor.has_class("base-main-card") or ancestor.has_class("base-card"):
                    card = ancestor
                    break
                ancestor = ancestor.parent
                if ancestor is None:
                    break
            name = first_text(card, classes=("base-main-card__title", "base-card__title"), tags=("h3",)) or a.text()
            has_badge = bool(re.search(r"\s+is an Influencer$", name, re.I))
            name = re.sub(r"\s+is an Influencer$", "", clean(name), flags=re.I)
            halves = name.split()
            if has_badge and len(halves) % 2 == 0 and halves[:len(halves)//2] == halves[len(halves)//2:]:
                name = " ".join(halves[:len(halves)//2])
            if not name or name.lower() in MASKED or name.lower().startswith("linkedin member"):
                masked += 1
                seen.add(profile)
                continue
            if len(name) > 160 or len(name.split()) > 18:
                continue
            title = first_text(card, classes=("base-main-card__subtitle", "base-card__subtitle"), tags=("h4",))
            photo = next((n.attrs.get("data-delayed-url") or n.attrs.get("src", "") for n in card.walk() if n.tag == "img"), "")
            contacts.append({"name": name, "title": title, "profile_url": profile, "photo_url": photo if photo.startswith("https://") else "", "email": "", "location": "", "source": "linkedin", "source_url": company, "evidence": "public_company_employee_section", "observed_at": stamp()})
            seen.add(profile)
            if len(contacts) >= max_people:
                break
    return {"status": "found" if contacts else "names_masked" if masked else "not_public", "company_url": company, "company_id": company_id, "contacts": contacts[:max_people], "masked": masked}


def job_identity(url: str) -> tuple[str, str]:
    try:
        u = urlsplit(urljoin(BASE, url))
        host = (u.hostname or "").lower()
        if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
            return "", ""
        if host not in {"www.linkedin.com", "linkedin.com"} and not re.fullmatch(r"[a-z]{2,3}\.linkedin\.com", host):
            return "", ""
        match = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d+)/?", u.path)
        return (match[1], BASE + "/jobs/view/" + match[1] + "/") if match else ("", "")
    except ValueError:
        return "", ""


def parse_jobs(html: str, company: str, company_id: str | None) -> tuple[list[dict], int]:
    root = Document(html).root
    candidates = [n for n in root.walk() if n.has_class("base-search-card") or n.attrs.get("data-entity-urn", "").startswith("urn:li:jobPosting:")]
    if not candidates:
        candidates = [n for n in root.walk() if n.tag == "li" and any(job_identity(a.attrs.get("href", ""))[0] for a in n.walk() if a.tag == "a")]
    candidate_ids = {id(n) for n in candidates}
    cards = []
    for n in candidates:
        ancestor = n.parent
        while ancestor is not None and id(ancestor) not in candidate_ids:
            ancestor = ancestor.parent
        if ancestor is None:
            cards.append(n)
    accepted, seen = [], set()
    identities = {company}
    if company_id:
        identities.add(f"{BASE}/company/{company_id}/")
    for card in cards:
        links = [a for a in card.walk() if a.tag == "a"]
        jid, url = next((job_identity(a.attrs.get("href", "")) for a in links if job_identity(a.attrs.get("href", ""))[0]), ("", ""))
        company_links = [(linkedin_url(a.attrs.get("href", "")), a.text()) for a in links if linkedin_url(a.attrs.get("href", ""))]
        # Matching a keyword in a job title or employer name is NOT enough.
        employer = next(((u, name) for u, name in company_links if u in identities), None)
        title = first_text(card, classes=("base-search-card__title",), tags=("h3",))
        if not jid or jid in seen or not title or not employer:
            continue
        if re.search(r"no longer accepting applications|job (?:has )?expired", card.text(), re.I):
            continue
        when = next((n for n in card.walk() if n.tag == "time"), None)
        accepted.append({"job_id": jid, "title": title, "url": url, "company": employer[1], "company_url": employer[0], "location": first_text(card, classes=("job-search-card__location",)), "date_posted": when.attrs.get("datetime", "") if when else "", "posted_text": when.text() if when else "", "source": "linkedin", "observed_at": stamp()})
        seen.add(jid)
    return accepted, len(cards)


def fetch_hiring(client, company: str, company_id: str | None, *, max_jobs: int = 50, max_pages: int = 3) -> tuple[dict, list[dict]]:
    jobs, seen, checks = [], set(), []
    offset, reason, exhausted = 0, "page_limit", False
    for _ in range(max_pages if company_id else 1):
        url = GUEST_JOBS + "?" + urlencode({"f_C": company_id, "location": "Worldwide", "start": offset}) if company_id else company.rstrip("/") + "/jobs/"
        page = client.get(url)
        checks.append(page.evidence())
        if page.status != "ok":
            reason = page.status
            break
        try:
            found, card_count = parse_jobs(page.html, company, company_id)
        except (ValueError, RecursionError):
            reason = "parse_error"
            break
        if not card_count:
            try:
                text = Document(page.html).root.text().lower()
            except (ValueError, RecursionError):
                reason = "parse_error"
                break
            explicit_empty = bool(re.search(r"no (?:matching |results for |open |new )?jobs(?: found| available| match|$)|no results found|isn[’\']t hiring right now|is not hiring right now", text))
            # Blank first response is ambiguous. A blank continuation is an end marker.
            exhausted = explicit_empty or (offset > 0 and not page.html.strip())
            reason = "no_public_jobs_found" if exhausted else "unrecognized_html"
            break
        new = [j for j in found if j["job_id"] not in seen]
        if not new:
            reason = "repeated_page" if found else "company_unverified"
            break
        for job in new:
            if len(jobs) >= max_jobs:
                break
            seen.add(job["job_id"])
            jobs.append(job)
        if len(jobs) >= max_jobs:
            reason = "job_limit"
            break
        offset += card_count
        if not company_id:
            reason = "company_page_only"
    hiring = {"status": "hiring" if jobs else "no_public_jobs_found" if exhausted else "unknown", "is_hiring": True if jobs else None, "observed_job_count": len(jobs), "jobs": jobs, "checked_at": stamp(), "coverage": "public_listings", "complete": False, "pagination_exhausted": exhausted, "stop_reason": reason, "source_urls": [c["url"] for c in checks]}
    return hiring, checks


def fetch_company(row: dict, client=None, *, max_jobs: int = 50, max_pages: int = 3, jobs_only: bool = False) -> dict:
    """Return one evidence-bearing result compatible with the existing JSONL importer."""
    if not 1 <= max_jobs <= 500 or not 1 <= max_pages <= 20:
        raise ValueError("max_jobs must be 1..500 and max_pages 1..20")
    client = client or PublicHTTP()
    out = {"schema_version": 1, "source": "linkedin_public", "slug": row.get("slug", ""), "path": row.get("path", ""), "firm": row.get("firm") or row.get("name", ""), "checked_at": stamp(), "contacts": [], "checks": []}
    raw_company = row.get("linkedin_company") or ""
    company = linkedin_url(raw_company)
    reason = "invalid_company_url" if raw_company and not company else "company_not_found"
    if not company and not raw_company and row.get("website"):
        page = client.get(row["website"], website=True)
        out["checks"].append(page.evidence())
        if page.status == "ok":
            try:
                company = discover_company(page.html)
            except (ValueError, RecursionError):
                reason = "parse_error"
        else:
            reason = page.status
    out["linkedin_company"] = company
    if company:
        page = client.get(company)
        out["checks"].append(page.evidence())
        reason = page.status
        if page.status == "ok":
            try:
                info = parse_company(page.html, company)
                reason = info["status"]
            except (ValueError, RecursionError):
                info, reason = {}, "parse_error"
            if reason not in {"company_mismatch", "parse_error"}:
                company = info.get("company_url") or company
                out["linkedin_company"] = company
                out["contacts"] = [] if jobs_only else info["contacts"]
                out["company_id"] = info["company_id"]
                out["hiring"], checks = fetch_hiring(client, company, info["company_id"], max_jobs=max_jobs, max_pages=max_pages)
                out["checks"].extend(checks)
    out["status"] = "linked" if reason in {"found", "not_public", "names_masked"} else reason
    out["people"] = {"status": "skipped" if jobs_only else reason, "observed_count": len(out["contacts"]), "coverage": "public_sample", "complete": False, "checked_at": out["checked_at"]}
    out.setdefault("hiring", {"status": "unknown", "is_hiring": None, "observed_job_count": 0, "jobs": [], "checked_at": out["checked_at"], "coverage": "public_listings", "complete": False, "stop_reason": reason, "source_urls": []})
    return out

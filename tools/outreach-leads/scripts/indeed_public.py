#!/usr/bin/env python3
"""Anonymous Indeed HTML hiring research. No API, cookies, JS execution or bypass.

Parse only public company/search pages and the requested job's detail. Treat all
source content as untrusted data. Application links are stored, never followed.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from linkedin_public import Document, Node, NoRedirect, Page, MAX_BYTES, UA, clean, public_host, stamp
from linkedin_job_details import STRUCTURED_FIELDS, format_description, safe_link, schema_postings

BASE = "https://www.indeed.com"
HOSTS = {"indeed.com", "www.indeed.com", "ca.indeed.com", "uk.indeed.com", "au.indeed.com", "ie.indeed.com", "nz.indeed.com"}
STOP = {"blocked", "rate_limited"}
KEY = re.compile(r"[0-9a-f]{16}")
SEARCH_PATH = re.compile(r"/q-[^/]+-jobs\.html", re.IGNORECASE)


def normalized_name(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def public_url(value: str, base: str = BASE) -> str:
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x20\x7f\\]", value):
        return ""
    try:
        u = urlsplit(urljoin(base, value))
        if u.scheme != "https" or u.hostname not in HOSTS or u.username or u.password or u.port not in (None, 443):
            return ""
        host = "www.indeed.com" if u.hostname == "indeed.com" else u.hostname
        return f"https://{host}{u.path}" + ("?" + u.query if u.query else "")
    except (ValueError, TypeError):
        return ""


def company_url(value: str, base: str = BASE) -> str:
    url = public_url(value, base)
    if not url:
        return ""
    u = urlsplit(url)
    match = re.fullmatch(r"/cmp/([\w.,&'()\-]+)(?:/jobs)?/?", unquote(u.path))
    if not match or match[1] in {".", ".."}:
        return ""
    safe_slug_chars = "-._,&'()"
    return f"{u.scheme}://{u.netloc}/cmp/{quote(match[1].casefold(), safe=safe_slug_chars)}"


def search_url(value: str, base: str = BASE) -> str:
    """Return a public Indeed keyword/location search URL without guessing its filters."""
    url = public_url(value, base)
    if not url or not SEARCH_PATH.fullmatch(unquote(urlsplit(url).path)):
        return ""
    return url


def job_identity(value: str, base: str = BASE) -> tuple[str, str]:
    url = public_url(value, base)
    if not url:
        return "", ""
    u = urlsplit(url)
    search = bool(search_url(url))
    if u.path not in {"/viewjob", "/rc/clk", "/pagead/clk"} and not company_url(url) and not search:
        return "", ""
    query = parse_qs(u.query)
    keys = query.get("vjk", []) if search else query.get("jk", [])
    if len(keys) != 1 or not KEY.fullmatch(keys[0]):
        return "", ""
    return keys[0], f"{u.scheme}://{u.netloc}/viewjob?jk={keys[0]}"


def request_allowed(value: str) -> bool:
    url = public_url(value)
    if not url:
        return False
    u = urlsplit(url)
    if search_url(url):
        return True
    if u.path == "/viewjob":
        return bool(job_identity(url)[0])
    return bool(company_url(url)) or u.path == "/jobs"


def wall(html: str, url: str) -> bool:
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.hostname == "secure.indeed.com" or re.search(r"/(?:auth|account|login|challenge|cdn-cgi)(?:/|$)", u.path):
        return True
    title = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.I | re.S)
    heading = clean(re.sub(r"<[^>]*>", " ", title[1])).lower() if title else ""
    # A normal Sign in / Apply CTA is not a wall.
    return bool(re.search(r"just a moment|access denied|additional verification|verify (?:you are|your identity)|security check|sign in.*indeed", heading)) or bool(re.search(r"<(?:form|div)\b[^>]*(?:id=[\"']challenge-form|class=[\"'][^\"']*(?:h-captcha|g-recaptcha)|data-testid=[\"']challenge)", html, re.I))


class IndeedHTTP:
    """Independent refusal state; bounded anonymous requests to an allowlist."""
    def __init__(self, delay: float = 2, timeout: float = 15):
        if not 1 <= delay <= 60 or not 1 <= timeout <= 60:
            raise ValueError("delay/timeout must be 1..60 seconds")
        self.delay, self.timeout, self.last, self.stopped = delay, timeout, 0.0, ""
        self.opener = build_opener(NoRedirect())

    def get(self, url: str, *, website: bool = False) -> Page:
        if self.stopped:
            return Page(url, self.stopped, reason="Indeed batch stopped after refusal")
        original = url
        try:
            origin = (urlsplit(url).hostname or "").lower().removeprefix("www.")
            for hop in range(4):
                if wall("", url):
                    self.stopped = "blocked"
                    return Page(original, "blocked", reason="login/challenge redirect")
                if not website and not request_allowed(url):
                    return Page(original, "unsafe_url", reason="outside public Indeed allowlist")
                if website and (urlsplit(url).hostname or "").lower().removeprefix("www.") != origin:
                    return Page(original, "unsafe_url", reason="official website redirect changed host")
                public_host(url)
                time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
                self.last = time.monotonic()
                try:
                    request = Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.8"})
                    with self.opener.open(request, timeout=self.timeout) as response:
                        body = response.read(MAX_BYTES + 1)
                        status = response.status
                        content_type = response.headers.get("Content-Type", "")
                        charset = response.headers.get_content_charset() or "utf-8"
                    if len(body) > MAX_BYTES:
                        return Page(original, "too_large", http_status=status)
                    if content_type and not any(x in content_type.lower() for x in ("text/html", "application/xhtml")):
                        return Page(original, "parse_error", http_status=status, reason="expected HTML")
                    html = body.decode(charset, errors="replace")
                    if not website and wall(html, url):
                        self.stopped = "blocked"
                        return Page(original, "blocked", http_status=status, reason="login/verification wall")
                    return Page(url, "ok", html, status)
                except HTTPError as error:
                    code, headers = error.code, error.headers
                    error.close()
                    if code in {301, 302, 303, 307, 308} and hop < 3:
                        destination = headers.get("Location", "")
                        if not destination:
                            return Page(original, "http_error", http_status=code, reason="redirect without Location")
                        url = urljoin(url, destination)
                        continue
                    state = "rate_limited" if code == 429 else "blocked" if code in {401, 403, 999} else "not_found" if code in {404, 410} else "http_error"
                    if not website and state in STOP:
                        self.stopped = state
                    reason = "retry_after=" + headers.get("Retry-After", "unspecified") if code == 429 else ""
                    return Page(original, state, http_status=code, reason=reason)
        except (URLError, OSError, TimeoutError) as error:
            return Page(original, "network_error", reason=str(error)[:240])
        except (ValueError, LookupError) as error:
            return Page(original, "unsafe_url", reason=str(error)[:240])
        return Page(original, "http_error", reason="redirect limit")


def find(root: Node, *, ids=(), tests=(), classes=(), tags=()) -> Node | None:
    return next((n for n in root.walk() if not excluded(n) and (n.attrs.get("id") in ids or n.attrs.get("data-testid") in tests or any(n.has_class(c) for c in classes) or n.tag in tags)), None)


def text(root: Node, **selectors) -> str:
    node = find(root, **selectors)
    return node.text() if node else ""


def description(node: Node, base: str) -> tuple[str, str]:
    # The shared sanitizer defaults to LinkedIn. Resolve all relative links to
    # this actual Indeed page first, without requesting any of them.
    for child in node.walk():
        if child.tag == "a":
            child.attrs["href"] = safe_link(child.attrs.get("href", ""), base)
    return format_description(node)


def discover_company(html: str, base: str) -> str:
    links = {company_url(n.attrs.get("href", ""), base) for n in Document(html).root.walk() if n.tag == "a" and n.attrs.get("href", "").startswith("https://")}
    links.discard("")
    return next(iter(links)) if len(links) == 1 else ""


def embedded_records(root: Node) -> list[dict]:
    """Read valid JSON already in the public HTML; never evaluate JavaScript."""
    values = []
    decoder = json.JSONDecoder()
    for n in root.walk():
        if n.tag != "script":
            continue
        raw = "".join(c for c in n.children if isinstance(c, str))
        if n.attrs.get("type") == "application/json" or n.attrs.get("id") == "__NEXT_DATA__":
            try:
                values.append(json.loads(raw))
            except (ValueError, RecursionError):
                pass
        else:
            for match in re.finditer(r'(?:window\._initialData|window\.mosaic\.providerData\[[\"\']mosaic-provider-jobcards[\"\']\])\s*=\s*', raw):
                try:
                    value, _ = decoder.raw_decode(raw[match.end():].lstrip())
                    values.append(value)
                except (ValueError, RecursionError):
                    pass
    found, budget = [], 0
    while values:
        value = values.pop()
        budget += 1
        if budget > 60_000:
            raise ValueError("embedded JSON complexity limit")
        if isinstance(value, dict):
            key = value.get("jobkey") or value.get("jobKey") or value.get("jk")
            if isinstance(key, str) and KEY.fullmatch(key) and isinstance(value.get("title") or value.get("jobTitle"), str):
                found.append(value)
            else:
                values.extend(v for v in value.values() if isinstance(v, (dict, list)))
        elif isinstance(value, list):
            values.extend(value)
    return found


def excluded(node: Node) -> bool:
    while node:
        marker = " ".join(node.attrs.get(k, "") for k in ("id", "data-testid", "aria-label", "class")).lower()
        if node.tag in {"aside", "footer", "nav"} or any(k in marker for k in ("recommend", "related-job", "similar-job", "other-compan")):
            return True
        node = node.parent
    return False


def employer_match(name: str, link: str, firm: str, company: str) -> bool:
    # Explicit conflicting company identities always win over a matching name.
    if company and link:
        return company == link
    return bool(firm and name and normalized_name(name) == normalized_name(firm))


def schema_ids(item: dict, base: str) -> set[str]:
    found = set()
    for key in ("url", "@id", "mainEntityOfPage"):
        value = item.get(key, [])
        for val in value if isinstance(value, list) else [value]:
            if isinstance(val, dict):
                val = val.get("@id") or val.get("url")
            if isinstance(val, str) and job_identity(val, base)[0]:
                found.add(job_identity(val, base)[0])
    return found


def org_data(item: dict, base: str) -> tuple[str, str]:
    org = item.get("hiringOrganization") or {}
    if not isinstance(org, dict):
        return "", ""
    links = []
    for key in ("url", "sameAs", "@id"):
        value = org.get(key, [])
        links.extend(value if isinstance(value, list) else [value])
    identities = {company_url(v, base) for v in links if isinstance(v, str)} - {""}
    return str(org.get("name") or ""), next(iter(identities)) if len(identities) == 1 else ""


def parse_listings(html: str, url: str, firm: str, company: str = "") -> tuple[list[dict], str, str]:
    """Return company-matched cards, a validated next URL and an outcome."""
    if wall(html, url):
        return [], "", "blocked"
    root = Document(html).root
    canon = next((public_url(n.attrs.get("href", ""), url) for n in root.walk() if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split()), "")
    if company and canon and company_url(canon) != company:
        return [], "", "company_mismatch"
    company_scope = bool(company and (company_url(canon) == company or normalized_name(text(root, tags=("h1",))) == normalized_name(firm + " Jobs and Careers")))
    jobs = {}

    def add(jid, title, name, link, fields, *, scoped=False):
        if not isinstance(jid, str) or not KEY.fullmatch(jid) or not isinstance(title, str) or not clean(title):
            return
        linked = company_url(link, url) if isinstance(link, str) else ""
        name = name if isinstance(name, str) else ""
        matches = employer_match(name, linked, firm, company)
        if not matches and not (scoped and company_scope and not name and not linked):
            return
        job_url = job_identity("/viewjob?jk=" + jid, url)[1]
        record = {"job_id": jid, "source_job_key": "indeed:" + jid, "source": "indeed", "title": clean(title), "url": job_url, "job_url": job_url,
                  "company": name or firm, "company_url": linked or company, "company_match": "company_page" if company_scope else "exact_employer_name",
                  "observed_at": stamp(), "source_url": url, **fields}
        jobs.setdefault(jid, record)

    for n in root.walk():
        if n.tag != "a" or excluded(n):
            continue
        jid, _ = job_identity(n.attrs.get("href", ""), url)
        if not jid:
            continue
        card = n
        ancestor = n
        while ancestor and ancestor is not root:
            if ancestor.tag in {"li", "article"} or ancestor.attrs.get("data-jk") == jid or ancestor.has_class("job_seen_beacon") or ancestor.attrs.get("data-testid") in {"job-card", "cmp-job-list-item", "job-list-item"}:
                card = ancestor
                break
            ancestor = ancestor.parent
        title = text(card, tags=("h2", "h3")) or n.attrs.get("title") or n.text()
        company_node = find(card, tests=("company-name", "companyName"), classes=("companyName",))
        name = company_node.text() if company_node else ""
        links = [(company_url(a.attrs.get("href", ""), url), a.text()) for a in card.walk() if a.tag == "a" and company_url(a.attrs.get("href", ""), url) and not job_identity(a.attrs.get("href", ""), url)[0]]
        if len({u for u, _ in links}) > 1:
            continue
        linked = links[0][0] if links else ""
        if links and not name:
            name = links[0][1]
        fields = {"location": text(card, tests=("text-location", "job-location"), classes=("companyLocation",)),
                  "pay_text": text(card, tests=("salary-snippet", "job-salary"), classes=("salary-snippet-container", "salary-snippet", "salaryOnly")),
                  "posted_text": text(card, tests=("myJobsStateDate",), classes=("date",)), "listing_status": "observed"}
        # Keep every visible attribute on THIS card, not the surrounding page.
        fields["listing_text"] = card.text()
        add(jid, title, name, linked, fields, scoped=True)
    for item in schema_postings(root):
        ids = schema_ids(item, url)
        if len(ids) != 1:
            continue
        name, linked = org_data(item, url)
        add(next(iter(ids)), item.get("title") or item.get("name"), name, linked, {"listing_structured_data": deepcopy(item)}, scoped=True)
    for item in embedded_records(root):
        raw_company = item.get("company") or {}
        name = raw_company.get("name", "") if isinstance(raw_company, dict) else raw_company
        link = item.get("companyUrl") or (raw_company.get("url", "") if isinstance(raw_company, dict) else "")
        title = item.get("title") or item.get("jobTitle")
        jid = item.get("jobkey") or item.get("jobKey") or item.get("jk")
        keep = {k: deepcopy(item[k]) for k in ("formattedLocation", "salarySnippet", "salary", "jobTypes", "attributes", "benefits", "pubDate", "formattedRelativeTime", "snippet", "isExpired") if k in item}
        add(jid, title, name, link, {"listing_metadata": keep}, scoped=True)
    next_url = ""
    for n in root.walk():
        if n.tag != "a":
            continue
        label = n.attrs.get("aria-label", "") + " " + n.text()
        if "next" not in n.attrs.get("rel", "").split() and not re.search(r"\bnext\b", label, re.I):
            continue
        candidate = public_url(n.attrs.get("href", ""), url)
        if not candidate or urlsplit(candidate).netloc != urlsplit(url).netloc:
            continue
        before, after = urlsplit(url), urlsplit(candidate)
        a, b = parse_qs(before.query), parse_qs(after.query)
        if before.path.rstrip("/") != after.path.rstrip("/"):
            continue
        if any(a.get(k) != b.get(k) for k in {"q", "l", "location", "clearPrefilter"}):
            continue
        if candidate != url:
            next_url = candidate
            break
    # Multiple company identities with the same name cannot establish a firm.
    identities = {j["company_url"] for j in jobs.values()} - {""}
    if not company and len(identities) > 1:
        return [], "", "ambiguous_company"
    empty = bool(re.search(r"\b(?:no jobs (?:found|available)|0 jobs at|no (?:current|open) jobs|isn[’']t hiring right now)\b", root.text(), re.I))
    return list(jobs.values()), next_url, "found" if jobs else "no_public_jobs_found" if empty else "unrecognized_html"


def parse_detail(html: str, job: dict) -> dict:
    jid, url = job_identity(job.get("url", ""))
    if jid != job.get("job_id") or not jid:
        raise ValueError("invalid Indeed job identity")
    if len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("oversized HTML")
    if wall(html, url):
        return {"detail_status": "blocked"}
    root = Document(html).root
    identities = set()
    for n in root.walk():
        value = n.attrs.get("href") if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split() else n.attrs.get("content") if n.tag == "meta" and n.attrs.get("property") == "og:url" else None
        if value and job_identity(value, url)[0]:
            identities.add(job_identity(value, url)[0])
    if identities and identities != {jid}:
        return {"detail_status": "job_mismatch"}
    postings = schema_postings(root)
    matched = [p for p in postings if schema_ids(p, url) == {jid}]
    schema = matched[0] if len(matched) == 1 else postings[0] if len(postings) == 1 and not schema_ids(postings[0], url) and identities == {jid} else {}
    if postings and not schema:
        return {"detail_status": "job_mismatch"}
    title_node = find(root, classes=("jobsearch-JobInfoHeader-title",), tests=("jobsearch-JobInfoHeader-title",))
    title = title_node.text() if title_node else str(schema.get("title") or "")
    if not schema and not (identities == {jid} and title):
        return {"detail_status": "parse_error"}
    org_name, org_link = org_data(schema, url)
    org = schema.get("hiringOrganization") or {}
    if isinstance(org, dict):
        org_links = set()
        for key in ("url", "sameAs", "@id"):
            value = org.get(key, [])
            org_links.update(company_url(v, url) for v in (value if isinstance(value, list) else [value]) if isinstance(v, str))
        org_links.discard("")
        if len(org_links) > 1:
            return {"detail_status": "company_mismatch"}
    company_node = find(root, tests=("inlineHeader-companyName", "jobsearch-CompanyInfoContainer"))
    if company_node:
        links = {company_url(n.attrs.get("href", ""), url) for n in company_node.walk() if n.tag == "a"} - {""}
        if len(links) > 1 or org_link and links and org_link not in links:
            return {"detail_status": "company_mismatch"}
        org_link = next(iter(links)) if links else org_link
        org_name = org_name or company_node.text()
    if org_link and job.get("company_url") and org_link != job["company_url"]:
        return {"detail_status": "company_mismatch"}
    if org_name and not employer_match(org_name, org_link, job.get("company", ""), job.get("company_url", "")):
        return {"detail_status": "company_mismatch"}
    node = find(root, ids=("jobDescriptionText",), tests=("jobDescriptionText",), classes=("jobsearch-jobDescriptionText",))
    source = "html"
    if node is None and isinstance(schema.get("description"), str):
        node, source = Document(schema["description"]).root, "json_ld"
    plain, markup = description(node, url) if node is not None else ("", "")
    result = {"detail_status": "ok" if plain else "partial", "description": plain or None, "description_html": markup or None,
              "description_source": source if plain else None, "description_scope": "public_detail_page", "description_truncated": False,
              "metadata_sources": {}, "listing_status": "observed"}
    if title:
        result["title"] = title
    if schema:
        result["structured_data"] = deepcopy(schema)  # only this JobPosting, never page/session state
    for key, target in STRUCTURED_FIELDS.items():
        if key in schema:
            result[target] = deepcopy(schema[key])
            result["metadata_sources"][target] = "json_ld"
    for target, selectors in {
        "pay_text": {"ids": ("salaryInfoAndJobType",), "tests": ("salary-snippet",)},
        "location": {"tests": ("job-location", "inlineHeader-companyLocation"), "ids": ("jobLocationText",)},
        "benefits_text": {"ids": ("benefits",), "tests": ("benefits",)},
        "job_details_text": {"ids": ("jobDetailsSection",), "tests": ("job-details",)},
        "posted_text": {"tests": ("job-age",), "classes": ("jobsearch-JobMetadataFooter",)},
    }.items():
        value = text(root, **selectors)
        if value:
            result[target] = value
            result["metadata_sources"][target] = "html"
    result["pay_basis"] = "estimated" if "estimatedSalary" in schema and "baseSalary" not in schema or re.search(r"indeed.*estimat|estimated pay", result.get("pay_text", ""), re.I) else "base_salary" if "baseSalary" in schema else "unspecified"
    status_text = text(root, tests=("job-expired-message", "jobsearch-JobExpiredBanner"), classes=("jobsearch-JobExpiredBanner",))
    if re.search(r"expired|no longer available|no longer accepting|closed", status_text, re.I):
        result["listing_status"] = "closed"
    if isinstance(schema.get("validThrough"), str):
        try:
            expiry = datetime.fromisoformat(schema["validThrough"].replace("Z", "+00:00"))
            if len(schema["validThrough"]) == 10:
                expiry = expiry.replace(hour=23, minute=59, second=59, microsecond=999999)
            expiry = expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry
            if expiry < datetime.now(timezone.utc):
                result["listing_status"] = "closed"
        except ValueError:
            pass
    applications = []
    for n in root.walk():
        if n.tag != "a" or excluded(n):
            continue
        if n.attrs.get("id") not in {"applyButtonLink", "indeedApplyButton"} and n.attrs.get("data-testid") not in {"apply-button", "applyButtonLink", "applyButtonLinkContainer"}:
            continue
        link = safe_link(n.attrs.get("href", ""), url)
        if link and not job_identity(link)[0] and not company_url(link) and not wall("", link):
            applications.append(link)
    if applications:
        result["apply_url"] = applications[0]
        result["application_links"] = list(dict.fromkeys(applications))
    return result


def fetch_detail(job: dict, client) -> dict:
    result = deepcopy(job)
    result.update(description=None, description_html=None, detail_checked_at=stamp(), detail_checks=[])
    if getattr(client, "stopped", ""):
        result.update(detail_status="not_attempted", detail_stop_reason=client.stopped)
        return result
    page = client.get(job["url"])
    evidence = page.evidence()
    evidence["requested_url"] = job["url"]
    result["detail_checks"] = [evidence]
    result["detail_status"] = page.status
    if page.status != "ok":
        if page.status in STOP:
            client.stopped = page.status
        return result
    if job_identity(page.url)[0] != job["job_id"]:
        result["detail_status"] = "job_mismatch"
        return result
    try:
        result.update(parse_detail(page.html, job))
    except (ValueError, TypeError, RecursionError):
        result["detail_status"] = "parse_error"
    evidence["parse_status"] = result["detail_status"]
    if result["detail_status"] in STOP:
        client.stopped = result["detail_status"]
    return result


def fetch_company(row: dict, client=None, *, max_jobs: int = 50, max_pages: int = 3) -> dict:
    if not 1 <= max_jobs <= 500 or not 1 <= max_pages <= 20:
        raise ValueError("max_jobs 1..500; max_pages 1..20")
    client = client or IndeedHTTP()
    firm = clean(row.get("firm") or row.get("name") or "")
    result = {"source": "indeed_public", "schema_version": 1, "slug": row.get("slug", ""), "path": row.get("path", ""), "firm": firm, "checked_at": stamp(), "checks": []}
    raw_company = row.get("indeed_company") or ""
    company = company_url(raw_company)
    reason = "invalid_company_url" if raw_company and not company else "company_not_found"
    if not raw_company and row.get("website") and not getattr(client, "stopped", ""):
        page = client.get(row["website"], website=True)
        result["checks"].append(page.evidence())
        if page.status == "ok":
            try:
                company = discover_company(page.html, row["website"])
            except (ValueError, RecursionError):
                reason = "parse_error"
    search = not company and not raw_company and bool(firm)
    url = company + "/jobs?clearPrefilter=1" if company else BASE + "/jobs?" + urlencode({"q": 'company:"' + firm + '"', "l": ""}) if search else ""
    result.update(company_url=company or None, discovery="known_or_official_link" if company else "exact_employer_search" if search else "unresolved")
    jobs, seen_urls = {}, set()
    exhausted = False
    for _ in range(max_pages if url else 0):
        if url in seen_urls:
            reason = "repeated_page"
            break
        seen_urls.add(url)
        page = client.get(url)
        evidence = page.evidence()
        result["checks"].append(evidence)
        if page.status != "ok":
            reason = page.status
            if reason in STOP:
                client.stopped = reason
            break
        # A redirect may not silently change the employer or search filters.
        before, after = urlsplit(url), urlsplit(page.url)
        if before.netloc != after.netloc or before.path.rstrip("/") != after.path.rstrip("/") or any(parse_qs(before.query).get(k) != parse_qs(after.query).get(k) for k in ("q", "l")):
            reason = "unexpected_redirect"
            break
        try:
            found, next_url, outcome = parse_listings(page.html, page.url, firm, company)
        except (ValueError, TypeError, RecursionError):
            found, next_url, outcome = [], "", "parse_error"
        evidence["parse_status"] = outcome
        if outcome in STOP:
            client.stopped = outcome
        new = [j for j in found if j["job_id"] not in jobs]
        if not new:
            reason = "repeated_page" if found else outcome
            exhausted = outcome == "no_public_jobs_found"
            break
        for job in new[:max_jobs - len(jobs)]:
            jobs[job["job_id"]] = job
        if len(jobs) >= max_jobs:
            reason = "job_limit"
            break
        if not next_url:
            reason = "no_public_next_page"
            break
        reason, url = "page_limit", next_url
    # Do not mix duplicate-named employers discovered on different pages.
    employers = {j["company_url"] for j in jobs.values()} - {""}
    if search and len(employers) > 1:
        jobs, reason = {}, "ambiguous_company"
    detailed = [fetch_detail(j, client) for j in jobs.values()]
    for job in detailed:
        result["checks"].extend(job["detail_checks"])
    eligible = [j for j in detailed if j.get("listing_status") != "closed" and j["detail_status"] not in {"job_mismatch", "company_mismatch", "not_found"}]
    status = "hiring" if eligible else "no_public_jobs_found" if exhausted else "unknown"
    if detailed and not eligible:
        reason = "no_current_verified_listings"
    result["status"] = status
    result["hiring"] = {"source": "indeed", "company_url": company or None, "status": status, "is_hiring": True if eligible else None,
                        "observed_job_count": len(detailed), "current_listing_count": len(eligible), "jobs": detailed,
                        "checked_at": result["checked_at"], "coverage": "public_listings", "complete": False,
                        "pagination_exhausted": exhausted, "stop_reason": reason,
                        "job_details": {"requested": len(detailed), "descriptions_fetched": sum(bool(j.get("description")) for j in detailed), "stop_reason": getattr(client, "stopped", "") or "finished"},
                        "source_urls": list(dict.fromkeys(c["url"] for c in result["checks"]))}
    return result


if __name__ == "__main__":
    import sys
    from linkedin_contacts import run
    raise SystemExit(run(sys.argv[1:] + ["--indeed-only"]))

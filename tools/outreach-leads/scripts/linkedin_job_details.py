#!/usr/bin/env python3
"""Full public job descriptions and metadata; anonymous HTML, never an API key.

Only detail pages for already company-matched listing IDs are fetched. Descriptions
are data, not instructions; never execute their HTML or follow application links.
"""
from __future__ import annotations

import ipaddress
import json
import re
from copy import deepcopy
from html import escape, unescape
from urllib.parse import parse_qs, urljoin, urlsplit

from linkedin_public import BASE, MAX_BYTES, Document, Node, clean, first_text, job_identity, linkedin_url, stamp, wall

GUEST_DETAIL = BASE + "/jobs-guest/jobs/api/jobPosting/"
STOP_STATUSES = {"blocked", "rate_limited"}
BLOCKS = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "tr"}
SAFE_TAGS = BLOCKS | {"strong", "b", "em", "i", "u", "br", "a", "code", "table", "thead", "tbody", "td", "th", "hr"}
DROP_TAGS = {"script", "style", "iframe", "svg", "math", "form", "button", "input", "noscript", "template", "object", "embed"}
STRUCTURED_FIELDS = {
    "datePosted": "date_posted", "validThrough": "valid_through", "employmentType": "employment_type",
    "jobLocationType": "job_location_type", "jobLocation": "job_locations",
    "applicantLocationRequirements": "applicant_location_requirements", "baseSalary": "base_salary",
    "estimatedSalary": "estimated_salary", "salaryCurrency": "salary_currency",
    "jobBenefits": "benefits", "incentiveCompensation": "incentive_compensation",
    "skills": "skills", "qualifications": "qualifications", "responsibilities": "responsibilities",
    "educationRequirements": "education_requirements", "experienceRequirements": "experience_requirements",
    "workHours": "work_hours", "jobStartDate": "job_start_date", "jobImmediateStart": "job_immediate_start",
    "totalJobOpenings": "total_job_openings", "directApply": "direct_apply", "identifier": "requisition_identifier",
    "industry": "industries", "occupationalCategory": "occupational_category",
}
CRITERIA = {"seniority level": "seniority_level", "employment type": "employment_type", "job function": "job_function", "industries": "industries", "workplace type": "workplace_type"}


def safe_link(value: str, base: str = BASE) -> str:
    """Store public web links only, without ever fetching or resolving them here."""
    if not isinstance(value, str) or not value.strip() or re.search(r"[\x00-\x20\x7f\\]", value):
        return ""
    try:
        url = urljoin(base, unescape(value))
        u = urlsplit(url)
        host = (u.hostname or "").lower().rstrip(".")
        if u.scheme not in {"http", "https"} or not host or u.username or u.password or u.port not in (None, 80, 443):
            return ""
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return url
    except ValueError:
        return ""


def format_description(node: Node) -> tuple[str, str]:
    """Keep all source text and basic formatting, discard executable markup."""
    text, html = [], []
    def visit(n):
        if isinstance(n, str):
            text.append(n)
            html.append(escape(n, quote=False))
            return
        if n.tag in DROP_TAGS:
            return
        tag = n.tag if n.tag in SAFE_TAGS else ""
        if n.tag in BLOCKS:
            text.append("\n")
        if n.tag == "li":
            text.append("- ")
        if n.tag in {"br", "hr"}:
            text.append("\n")
        if tag:
            attr = ""
            if tag == "a":
                link = safe_link(n.attrs.get("href", ""))
                if link:
                    attr = ' href="' + escape(link, quote=True) + '" rel="nofollow noopener noreferrer"'
            html.append("<" + tag + attr + ">")
        for child in n.children:
            visit(child)
        if tag and tag not in {"br", "hr"}:
            html.append("</" + tag + ">")
        if n.tag in BLOCKS:
            text.append("\n")
        elif n.tag in {"td", "th"}:
            text.append("\t")
    visit(node)
    lines = [clean(line) for line in "".join(text).splitlines()]
    plain = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return plain, "".join(html).strip()


class DetailDocument(Document):
    def handle_comment(self, data):
        # LinkedIn sometimes exposes the offsite apply URL in a JSON string
        # inside a comment. Capture only that named public field, not other code.
        node = self.stack[-1]
        if node.tag == "code" and node.attrs.get("id") == "applyUrl":
            node.children.append(data)


def select(node: Node, *classes: str) -> Node | None:
    return next((n for n in node.walk() if any(n.has_class(c) for c in classes)), None)


def schema_postings(root: Node) -> list[dict]:
    """Parse JSON-LD JobPosting objects only; bounded traversal, no eval."""
    found = []
    for script in root.walk():
        if script.tag != "script" or script.attrs.get("type", "").split(";", 1)[0].strip().lower() != "application/ld+json":
            continue
        raw = "".join(c for c in script.children if isinstance(c, str))
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        todo, count = [value], 0
        while todo:
            item = todo.pop()
            count += 1
            if count > 60_000:
                raise ValueError("JSON-LD complexity limit")
            if isinstance(item, list):
                todo.extend(reversed(item))
            elif isinstance(item, dict):
                types = item.get("@type", [])
                types = [types] if isinstance(types, str) else types if isinstance(types, list) else []
                if any(isinstance(t, str) and t.rstrip("/").rsplit("/", 1)[-1] == "JobPosting" for t in types):
                    found.append(item)
                else:
                    todo.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return found


def schema_job_ids(item: dict) -> set[str]:
    ids = set()
    for key in ("url", "@id", "mainEntityOfPage"):
        values = item.get(key, [])
        for value in values if isinstance(values, list) else [values]:
            if isinstance(value, dict):
                value = value.get("@id") or value.get("url")
            if isinstance(value, str):
                jid = job_identity(value)[0]
                if jid:
                    ids.add(jid)
    return ids


def application_url(value: str, job_id: str) -> str:
    link = safe_link(value)
    if not link:
        return ""
    u = urlsplit(link)
    if linkedin_url(link) or job_identity(link)[0]:
        return ""  # A company or job page is not a direct application target.
    if (u.hostname or "") == "linkedin.com" or (u.hostname or "").endswith(".linkedin.com"):
        if u.path != "/jobs/view/externalApply":
            return ""
        query = parse_qs(u.query)
        if query.get("jobId") not in (None, [job_id]):
            return ""
        destinations = query.get("url", [])
        return safe_link(destinations[0]) if len(destinations) == 1 else ""
    return link


def parse_job_detail(html: str, job: dict, company_id: str | None = None) -> dict:
    """Extract only the requested job, never descriptions of recommended jobs."""
    jid, canonical = job_identity(job.get("url") or "")
    if not jid or str(job.get("job_id", "")) != jid:
        raise ValueError("invalid job identity")
    if len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("detail HTML exceeds response limit")
    if wall(html, canonical):
        return {"detail_status": "blocked"}
    root = DetailDocument(html).root
    page_ids = set()
    for n in root.walk():
        value = n.attrs.get("href", "") if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split() else n.attrs.get("content", "") if n.tag == "meta" and n.attrs.get("property") == "og:url" else ""
        identity = job_identity(value)[0] if value else ""
        if identity:
            page_ids.add(identity)
    if page_ids and page_ids != {jid}:
        return {"detail_status": "job_mismatch"}
    postings = schema_postings(root)
    matched = [item for item in postings if schema_job_ids(item) == {jid}]
    anonymous = [item for item in postings if not schema_job_ids(item)]
    schema = matched[0] if len(matched) == 1 else anonymous[0] if len(postings) == 1 and len(anonymous) == 1 else {}
    if postings and not schema and not page_ids:
        return {"detail_status": "job_mismatch"}
    top = select(root, "top-card-layout", "topcard")
    top_scope = top or root
    if top:
        urn = top.attrs.get("data-entity-urn", "")
        if urn.startswith("urn:li:jobPosting:") and urn.rsplit(":", 1)[-1] != jid:
            return {"detail_status": "job_mismatch"}
    employers = set()
    for n in top_scope.walk():
        if n.tag == "a" and n.has_class("topcard__org-name-link"):
            employer = linkedin_url(n.attrs.get("href", ""))
            if employer:
                employers.add(employer)
    org = schema.get("hiringOrganization") or {}
    if isinstance(org, dict):
        for key in ("sameAs", "url", "@id"):
            values = org.get(key, [])
            for value in values if isinstance(values, list) else [values]:
                if isinstance(value, str) and linkedin_url(value):
                    employers.add(linkedin_url(value))
    accepted = {linkedin_url(job.get("company_url", ""))}
    if company_id and str(company_id).isdigit():
        accepted.add(f"{BASE}/company/{company_id}/")
    if employers and not employers.issubset(accepted - {""}):
        return {"detail_status": "company_mismatch"}
    # Full pages and guest fragments must have a real top card or matching schema.
    if not top and not schema:
        return {"detail_status": "parse_error"}
    description_node = None
    for section in root.walk():
        if section.has_class("description") or section.has_class("core-section-container") and section.attrs.get("data-section") == "description":
            description_node = select(section, "show-more-less-html__markup")
            if description_node:
                break
    description_source = "html"
    description, description_html = format_description(description_node) if description_node else ("", "")
    if not description:
        raw = schema.get("description")
        if isinstance(raw, dict):
            raw = raw.get("text")
        if isinstance(raw, str) and raw.strip():
            description_node = Document(raw).root
            description_source = "json_ld"
            description, description_html = format_description(description_node)
    result = {"detail_status": "ok" if description else "partial", "job_url": canonical,
              "description": description, "description_html": description_html,
              "description_source": description_source if description else None,
              "description_truncated": False, "description_scope": "public_detail_page",
              "metadata_sources": {}}
    sources = result["metadata_sources"]
    for source_key, output_key in STRUCTURED_FIELDS.items():
        value = schema.get(source_key)
        if value is not None and value != "" and value != [] and value != {}:
            result[output_key] = deepcopy(value)
            sources[output_key] = "json_ld"
    if isinstance(org, dict) and org:
        result["hiring_organization"] = {k: deepcopy(v) for k, v in org.items() if k in {"name", "url", "sameAs", "logo", "description"}}
        sources["hiring_organization"] = "json_ld"
    title = first_text(top_scope, classes=("top-card-layout__title",))
    if not title and isinstance(schema.get("title"), str):
        title = clean(schema["title"])
    if title:
        result["title"] = title
    location = first_text(top_scope, classes=("topcard__flavor--bullet",))
    if location:
        result["location"] = location
    for section in root.walk():
        if not section.has_class("description__job-criteria-item"):
            continue
        label = first_text(section, classes=("description__job-criteria-subheader",), tags=("h3",)).lower().rstrip(":")
        value = first_text(section, classes=("description__job-criteria-text",))
        key = CRITERIA.get(label)
        if key and value:
            # Prefer the human-readable value; retain structured values separately.
            if key in result:
                result.setdefault("structured_metadata", {})[key] = result[key]
            result[key] = value
            sources[key] = "html"
    for key, classes in {
        "applicant_count_text": ("num-applicants__caption", "num-applicants__figure", "topcard__flavor--metadata"),
        "posted_text": ("posted-time-ago__text",),
        "salary_text": ("salary", "compensation__salary", "salary__range"),
    }.items():
        scope = top_scope if key in {"applicant_count_text", "posted_text"} else root
        values = [n.text() for n in scope.walk() if any(n.has_class(c) for c in classes)]
        value = next((v for v in values if v and (key != "applicant_count_text" or re.search(r"applicant", v, re.I))), "")
        if value:
            result[key] = value
            sources[key] = "html"
    # Closure must come from the job's status/top card, not words in its description.
    closed = first_text(root, classes=("closed-job__flavor--closed", "closed-job__flavor"))
    if not closed and top:
        closed = top.text()
    if re.search(r"no longer accepting applications|job (?:has )?expired|no longer available", closed, re.I):
        result["listing_status"] = "closed"
    else:
        result["listing_status"] = "observed"  # Not a guarantee that a vacancy is unfilled.
    for n in root.walk():
        if n.tag == "code" and n.attrs.get("id") == "applyUrl":
            raw = "".join(c for c in n.children if isinstance(c, str)).strip()
            try:
                value = application_url(json.loads(raw), jid)
            except (ValueError, TypeError):
                value = ""
            if value:
                result["apply_url"] = value
                sources["apply_url"] = "html"
                break
        if n.tag != "a":
            continue
        marker = n.attrs.get("data-tracking-control-name", "")
        if not (n.has_class("apply-button") or n.has_class("apply-button--offsite") or marker.endswith("_apply-link-offsite") or n.attrs.get("id") == "applyUrl"):
            continue
        value = application_url(n.attrs.get("href", ""), jid)
        if value:
            result["apply_url"] = value
            sources["apply_url"] = "html"
            break
    poster = select(root, "message-the-recruiter")
    if poster:
        profile = next((linkedin_url(n.attrs.get("href", ""), "in") for n in poster.walk() if n.tag == "a" and linkedin_url(n.attrs.get("href", ""), "in")), "")
        name = first_text(poster, classes=("base-main-card__title",), tags=("h3",))
        if profile and name:
            result["job_poster"] = {"name": name, "profile_url": profile, "title": first_text(poster, classes=("base-main-card__subtitle",), tags=("h4",))}
    return result


def fetch_job_detail(job: dict, client, company_id: str | None = None) -> dict:
    """At most two paced GETs; never retry or change route after refusal."""
    jid, url = job_identity(job.get("url") or "")
    out = dict(job)
    out.update(job_url=url, description=None, description_html=None,
               detail_status="not_attempted", detail_checked_at=stamp(), detail_checks=[])
    if not jid or str(job.get("job_id", "")) != jid:
        out["detail_status"] = "unsafe_url"
        return out
    best = {}
    for endpoint in (url, GUEST_DETAIL + jid):
        if getattr(client, "stopped", "") in STOP_STATUSES:
            out["detail_status"] = "not_attempted"
            out["detail_stop_reason"] = client.stopped
            break
        page = client.get(endpoint)
        evidence = page.evidence()
        evidence["requested_url"] = endpoint
        out["detail_checks"].append(evidence)
        out["detail_source_url"] = page.url
        out["detail_status"] = page.status
        if page.status != "ok":
            break  # No alternate endpoint for 404, rate limit, wall or timeout.
        redirected_id = job_identity(page.url)[0]
        if redirected_id and redirected_id != jid:
            out["detail_status"] = "job_mismatch"
            break
        try:
            parsed = parse_job_detail(page.html, job, company_id)
        except (ValueError, TypeError, RecursionError):
            parsed = {"detail_status": "parse_error"}
        evidence["parse_status"] = parsed["detail_status"]
        out["detail_status"] = parsed["detail_status"]
        if parsed["detail_status"] in {"ok", "partial"}:
            best = {**best, **parsed}
            out.update(best)
        if parsed["detail_status"] not in {"partial", "parse_error"}:
            break
    # Keep partial metadata even when the second attempt fails; do not disguise
    # the latest failure as successful, and never fill description from a teaser.
    if best and not out.get("description"):
        for key, value in best.items():
            if key != "detail_status":
                out[key] = value
    return out


def enrich_job_details(hiring: dict, client, company_id: str | None = None) -> list[dict]:
    """In-place additive job enrichment; dedupe requests and retain every card."""
    cache, checks, jobs = {}, [], []
    stopped = getattr(client, "stopped", "") or (hiring.get("stop_reason") if hiring.get("stop_reason") in STOP_STATUSES else "")
    for job in hiring.get("jobs", []):
        key = job.get("job_id")
        if stopped:
            row = dict(job)
            row.update(job_url=job.get("url"), description=None, description_html=None,
                       detail_status="not_attempted", detail_stop_reason=stopped,
                       detail_checked_at=stamp(), detail_checks=[])
        elif key in cache:
            row = deepcopy(cache[key])
        else:
            row = fetch_job_detail(job, client, company_id)
            cache[key] = deepcopy(row)
            checks.extend(row["detail_checks"])
            if row["detail_status"] in STOP_STATUSES:
                stopped = row["detail_status"]
                client.stopped = stopped
        jobs.append(row)
    hiring["jobs"] = jobs
    counts = {}
    for job in jobs:
        status = job["detail_status"]
        counts[status] = counts.get(status, 0) + 1
    hiring["job_details"] = {"requested": len(jobs), "descriptions_fetched": sum(bool(j.get("description")) for j in jobs),
                             "status_counts": counts, "http_requests": len(checks), "stop_reason": stopped or "finished",
                             "coverage": "collected_public_listings_only"}
    hiring["source_urls"] = list(dict.fromkeys(hiring.get("source_urls", []) + [c["url"] for c in checks]))
    hiring["closed_job_count"] = sum(j.get("listing_status") == "closed" for j in jobs)
    if jobs and hiring["closed_job_count"] == len(jobs):
        hiring["status"], hiring["is_hiring"] = "unknown", None
        hiring["stop_reason"] = "all_observed_listings_closed"
    return checks

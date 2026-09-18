"""Evidence-backed company resolution before public people or job enrichment.

Names locate candidates; they never verify employers. No authenticated endpoints,
API keys, fuzzy entity joins, shared-domain guesses, or access-control bypasses.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from copy import deepcopy
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit

from linkedin_public import Document, Node, Page, linkedin_url, parse_company, stamp

POLICY = "official-company-identity-v1"
BOARDS = ("linkedin", "indeed")
INDEED_HOSTS = {"indeed.com", "www.indeed.com", "ca.indeed.com", "uk.indeed.com", "au.indeed.com", "ie.indeed.com", "nz.indeed.com"}
SHARED_HOSTS = {"linkedin.com", "indeed.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "google.com", "sites.google.com", "github.io", "wixsite.com", "wordpress.com", "blogspot.com", "linktr.ee", "yelp.com"}
ORG_TYPES = {"Organization", "Corporation", "LocalBusiness", "LegalService", "ProfessionalService", "MedicalOrganization", "EducationalOrganization", "NGO"}
REFUSED = {"blocked", "rate_limited"}


def normalize_name(value: str) -> str:
    # Keep business words. In particular do NOT strip Law, Construction, etc.
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")))


def company_ref(value: str, source: str, base: str = "") -> str:
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x20\x7f\\]", value):
        return ""
    absolute = urljoin(base, value) if base else value
    if not absolute.startswith("https://"):
        return ""
    if source == "linkedin":
        return linkedin_url(absolute)
    if source != "indeed":
        return ""
    try:
        u = urlsplit(absolute)
        m = re.fullmatch(r"/cmp/([\w.-]+)(?:/jobs)?/?", unquote(u.path))
        if u.hostname not in INDEED_HOSTS or u.username or u.password or u.port not in (None, 443) or not m or m[1] in {".", ".."}:
            return ""
        host = "www.indeed.com" if u.hostname == "indeed.com" else u.hostname
        return f"https://{host}/cmp/{quote(m[1].casefold(), safe='-._')}"
    except (ValueError, TypeError):
        return ""


def official_host(value: str) -> str:
    """Exact IDNA hostname, ignoring only www. Never guess registrable domains."""
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f\\]", value):
        return ""
    try:
        u = urlsplit(value)
        if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password or u.port not in (None, 80, 443):
            return ""
        host = u.hostname.encode("idna").decode().lower().rstrip(".").removeprefix("www.")
        if "." not in host or any(host == h or host.endswith("." + h) for h in SHARED_HOSTS):
            return ""
        try:
            ipaddress.ip_address(host)
            return ""  # Company identity needs a domain, not an IP literal.
        except ValueError:
            return host
    except (ValueError, UnicodeError):
        return ""


def target_for(row: dict) -> dict:
    names = [row.get("firm") or row.get("name") or "", row.get("legal_name") or ""]
    aliases = row.get("company_aliases") or []
    if isinstance(aliases, list):
        names.extend(x for x in aliases if isinstance(x, str))
    target = {"slug": str(row.get("slug") or ""), "official_host": official_host(row.get("website") or ""),
              "names": sorted({normalize_name(x) for x in names if isinstance(x, str) and normalize_name(x)})}
    target["fingerprint"] = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()
    return target


def excluded(node: Node) -> bool:
    while node:
        marker = " ".join(node.attrs.get(k, "") for k in ("id", "class", "data-testid", "data-test-id", "aria-label")).lower()
        if node.tag in {"aside", "article"} or any(word in marker for word in ("recommend", "related", "similar", "affiliate", "partner", "client", "testimonial", "employees-at", "job-description", "jobdescription")):
            return True
        if node.tag == "section":
            headings = [n.text().lower() for n in node.children[:6] if isinstance(n, Node) and n.tag in {"h2", "h3"}]
            if headings and re.search(r"^(?:our |trusted )?(?:clients|partners|affiliates)|people also|similar companies", headings[0]):
                return True
        node = node.parent
    return False


def objects(root: Node) -> list[dict]:
    """Only document-level JSON-LD and @graph/mainEntity, not review authors."""
    result = []
    for n in root.walk():
        if n.tag != "script" or n.attrs.get("type", "").lower() != "application/ld+json":
            continue
        try:
            value = json.loads("".join(c for c in n.children if isinstance(c, str)))
        except (ValueError, RecursionError):
            continue
        todo, budget = [value], 0
        while todo:
            value = todo.pop()
            budget += 1
            if budget > 2000:
                raise ValueError("identity JSON complexity limit")
            if isinstance(value, list):
                todo.extend(value)
            elif isinstance(value, dict):
                types = value.get("@type", [])
                types = types if isinstance(types, list) else [types]
                if set(x.rsplit("/", 1)[-1] for x in types if isinstance(x, str)) & ORG_TYPES:
                    result.append(value)
                for key in ("@graph", "mainEntity"):
                    if isinstance(value.get(key), (dict, list)):
                        todo.append(value[key])
    return result


def refs(obj: dict) -> list[str]:
    out = []
    for key in ("url", "@id", "sameAs"):
        value = obj.get(key, [])
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, dict) and isinstance(v.get("@id"), str):
                out.append(v["@id"])
    return out


def website_link(value: str, base: str) -> str:
    url = urljoin(base, value)
    try:
        u = urlsplit(url)
        # Decode a displayed LinkedIn outbound link; never request it here.
        if u.hostname in {"www.linkedin.com", "linkedin.com"} and u.path == "/redir/redirect":
            values = parse_qs(u.query).get("url", [])
            url = values[0] if len(values) == 1 else ""
        return url if official_host(url) else ""
    except ValueError:
        return ""


def profile_facts(html: str, url: str, source: str) -> dict:
    root = Document(html).root
    requested = company_ref(url, source)
    canonical = {company_ref(n.attrs.get("href", ""), source, url) for n in root.walk()
                 if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split()} - {""}
    company = next(iter(canonical)) if len(canonical) == 1 else requested
    name = next((n.text() for n in root.walk() if n.tag == "h1" and not excluded(n)), "")
    name = re.sub(r"\s+(?:Careers and Employment|Jobs and Careers)$", "", name, flags=re.I)
    websites, locations, industries, aliases = set(), [], [], []
    scoped_orgs = [o for o in objects(root) if company in {company_ref(v, source, url) for v in refs(o)}]
    for obj in scoped_orgs:
        if not name and isinstance(obj.get("name"), str):
            name = obj["name"]
        aliases.extend(v for k in ("name", "legalName", "alternateName")
                       for v in (obj[k] if isinstance(obj.get(k), list) else [obj.get(k)]) if isinstance(v, str))
        websites.update(v for v in refs(obj) if official_host(v))
        if obj.get("address"):
            locations.append(obj["address"])
    for n in root.walk():
        if excluded(n):
            continue
        if n.tag == "dt" and n.parent:
            siblings = [c for c in n.parent.children if isinstance(c, Node)]
            idx = next(i for i, c in enumerate(siblings) if c is n)
            dd = siblings[idx + 1] if idx + 1 < len(siblings) and siblings[idx + 1].tag == "dd" else None
            if dd is None:
                continue
            label = n.text().lower().strip(": ")
            if label in {"website", "company website", "official website"}:
                websites.update(website_link(c.attrs.get("href", ""), url) for c in dd.walk() if c.tag == "a")
            elif label in {"headquarters", "location", "address"}:
                locations.append(dd.text())
            elif label == "industry":
                industries.append(dd.text())
        if n.tag == "a":
            marker = " ".join(n.attrs.get(k, "") for k in ("data-testid", "data-test-id", "aria-label")).lower()
            label = n.text().lower().strip()
            labelled = label in {"website", "company website", "official website", "visit website"} or (name and label == name.lower() + " website")
            if labelled or "about-us__website" in marker or "companyinfo-companylink" in marker:
                websites.add(website_link(n.attrs.get("href", ""), url))
    websites.discard("")
    company_id = None
    if source == "linkedin":
        info = parse_company(html, requested)
        company_id = info.get("company_id")
    aliases = list(dict.fromkeys([name] + aliases))
    return {"company_url": company, "company_id": company_id, "display_name": name,
            "observed_names": [a for a in aliases if a], "websites": sorted(websites),
            "locations": locations, "industries": industries, "canonical_conflict": len(canonical) > 1,
            "requested_url": requested}


def official_candidates(html: str, url: str, source: str, target: dict) -> tuple[dict, list[str]]:
    root = Document(html).root
    found, pages = {}, []
    for obj in objects(root):
        # An unrelated Organization in testimonials is not the site's identity.
        if not any(official_host(v) == target["official_host"] for v in refs(obj) if official_host(v)):
            continue
        if normalize_name(str(obj.get("name") or "")) not in target["names"] and normalize_name(str(obj.get("legalName") or "")) not in target["names"]:
            continue
        for v in refs(obj):
            company = company_ref(v, source, url)
            if company:
                found[company] = {"kind": "official_same_as", "source_url": url, "target_url": company}
    for n in root.walk():
        if n.tag != "a" or excluded(n):
            continue
        absolute = urljoin(url, n.attrs.get("href", ""))
        company = company_ref(absolute, source)
        label = normalize_name(n.attrs.get("aria-label") or n.text())
        social = source in label or label in target["names"]
        # Icon-only links need explicit social/header/footer placement.
        ancestor = n
        while ancestor and not social:
            social = ancestor.tag in {"header", "footer"} or "social" in ancestor.attrs.get("class", "").lower()
            ancestor = ancestor.parent
        if company and social:
            found.setdefault(company, {"kind": "official_link", "source_url": url, "target_url": company})
        if official_host(absolute) == target["official_host"] and absolute != url:
            path = urlsplit(absolute).path.lower().strip("/")
            if re.fullmatch(r"(?:[a-z]{2}/)?(?:about(?:-us)?|contact(?:-us)?|careers?|jobs|company)/?", path):
                pages.append(absolute.split("#")[0])
    return found, list(dict.fromkeys(pages))


def evaluate(target: dict, facts: dict, source: str, proof: dict | None = None) -> dict:
    rec = {"policy": POLICY, "source": source, "target": deepcopy(target), "status": "unverified",
           "reason": "no_domain_evidence", "checked_at": stamp(), "evidence": [],
           **{k: facts.get(k) for k in ("company_url", "company_id", "display_name", "observed_names", "websites", "locations", "industries", "canonical_conflict", "requested_url")}}
    expected = target["official_host"]
    company = facts.get("company_url")
    requested = facts.get("requested_url")
    numeric_alias = source == "linkedin" and facts.get("company_id") and requested == company_ref("https://www.linkedin.com/company/" + str(facts["company_id"]), source)
    hosts = {official_host(u) for u in facts.get("websites", [])} - {""}
    matched_name = bool(set(normalize_name(n) for n in facts.get("observed_names", [])) & set(target["names"]))
    if not expected:
        rec["reason"] = "missing_official_domain"
    elif facts.get("canonical_conflict") or (company != requested and not numeric_alias):
        rec.update(status="conflict", reason="company_redirect_conflict")
    elif hosts and hosts != {expected}:
        rec.update(status="conflict", reason="official_domain_conflict")
    elif not facts.get("display_name"):
        rec.update(status="unverified", reason="missing_company_profile_name")
    elif proof and company_ref(proof.get("target_url", ""), source) == company and official_host(proof.get("source_url", "")) == expected:
        rec.update(status="verified", reason="official_site_identity_link", evidence=[proof])
    elif hosts == {expected} and matched_name:
        rec.update(status="verified", reason="profile_domain_and_name", evidence=[{"kind": "profile_website", "source_url": company, "target_url": facts["websites"][0]}])
    elif hosts == {expected}:
        rec["reason"] = "name_or_legal_entity_conflict"
    elif matched_name:
        rec["reason"] = "name_only_not_identity"
    return rec


class IdentityHTTP:
    """Per-firm cache; reuse successful identity pages without duplicate GETs."""
    def __init__(self, client):
        self.client, self.pages, self.checks = client, {}, []

    @property
    def stopped(self):
        return getattr(self.client, "stopped", "")

    @stopped.setter
    def stopped(self, value):
        self.client.stopped = value

    def get(self, url, **kwargs):
        key = (url, bool(kwargs.get("website")))
        if self.stopped:
            return Page(url, self.stopped, reason="provider stopped after refusal")
        if key in self.pages:
            return self.pages[key]
        page = self.client.get(url, **kwargs)
        if page.status == "ok" and re.search(r"<title[^>]*>\s*(?:Just a moment|Access denied|Security verification|Sign in|LinkedIn Login)", page.html, re.I):
            page = Page(page.url, "blocked", http_status=page.http_status, reason="identity page login/challenge")
        if page.status in REFUSED and not kwargs.get("website"):
            self.stopped = page.status
        self.checks.append({**page.evidence(), "requested_url": url, "purpose": "company_identity"})
        # Bound retained HTML. Job listing/detail responses are not cached.
        if len(self.pages) < 10 and page.status == "ok" and (kwargs.get("website") or any(company_ref(url, b) for b in BOARDS)):
            self.pages[key] = page
        return page


def resolve_company(row: dict, source: str, client) -> tuple[dict, IdentityHTTP]:
    if source not in BOARDS:
        raise ValueError("unknown company source")
    target = target_for(row)
    cached = IdentityHTTP(client)
    base = "https://www.linkedin.com" if source == "linkedin" else "https://www.indeed.com"
    audits, proofs, candidates, attempted = [], {}, [], set()
    raw = row.get(source + "_company") or ""
    if company_ref(raw, source):
        candidates.append(company_ref(raw, source))
    default = {"policy": POLICY, "source": source, "target": target, "status": "unverified", "reason": "company_not_found", "company_url": None, "checked_at": stamp(), "evidence": []}
    if not target["official_host"]:
        return {**default, "reason": "missing_official_domain", "candidates": [], "checks": []}, cached

    def inspect():
        for candidate in list(dict.fromkeys(candidates))[:5]:
            if candidate in attempted or cached.stopped:
                continue
            attempted.add(candidate)
            page = cached.get(candidate)
            if page.status != "ok":
                audits.append({**default, "company_url": candidate, "status": page.status, "reason": page.status})
                continue
            try:
                facts = profile_facts(page.html, candidate, source)
                # A redirect may not silently substitute a different company.
                final = company_ref(page.url, source)
                if final and final != facts["company_url"]:
                    facts["canonical_conflict"] = True
                audits.append(evaluate(target, facts, source, proofs.get(facts["company_url"])))
            except (ValueError, TypeError, RecursionError):
                audits.append({**default, "company_url": candidate, "status": "parse_error", "reason": "unrecognized_company_profile"})

    def verified():
        return [a for a in audits if a["status"] == "verified"]

    inspect()
    # Stored URLs are candidates, not a permanent override. Resolve alternatives
    # from the official site when a stored mapping lacks evidence or conflicts.
    if not verified() and not cached.stopped:
        pending, seen = [row["website"]], set()
        while pending and len(seen) < 3 and not cached.stopped:
            url = pending.pop(0)
            if url in seen:
                continue
            seen.add(url)
            page = cached.get(url, website=True)
            if page.status != "ok" or official_host(page.url) != target["official_host"]:
                continue
            try:
                links, more = official_candidates(page.html, page.url, source, target)
                proofs.update(links)
                pending.extend(u for u in more if u not in seen)
                candidates.extend(links)
                # Re-evaluate already fetched candidates using new official proof.
                for i, audit in enumerate(audits):
                    if audit.get("company_url") in links and audit.get("requested_url"):
                        audits[i] = evaluate(target, audit, source, links[audit["company_url"]])
                inspect()
            except (ValueError, TypeError, RecursionError):
                continue
            if verified():
                break
    if not verified() and not cached.stopped and len(attempted) < 5:
        firm = str(row.get("firm") or row.get("name") or "")
        url = base + ("/jobs/search?" + urlencode({"keywords": firm}) if source == "linkedin" else "/jobs?" + urlencode({"q": 'company:"' + firm + '"', "l": ""}))
        page = cached.get(url)
        if page.status == "ok":
            try:
                for n in Document(page.html).root.walk():
                    if n.tag == "a" and not excluded(n) and normalize_name(n.text()) in target["names"]:
                        candidate = company_ref(n.attrs.get("href", ""), source, page.url)
                        if candidate:
                            candidates.append(candidate)
                inspect()
            except (ValueError, TypeError, RecursionError):
                pass
    accepted = {a["company_url"]: a for a in verified()}
    selected = next(iter(accepted.values())) if len(accepted) == 1 else dict(default)
    if len(accepted) > 1:
        selected.update(status="ambiguous", reason="multiple_verified_company_profiles")
    elif not accepted and cached.stopped:
        selected.update(status=cached.stopped, reason="identity_verification_" + cached.stopped)
    elif not accepted and any(a["status"] == "conflict" for a in audits):
        selected.update(status="conflict", reason="company_identity_conflict")
    elif not accepted and audits:
        selected["reason"] = "insufficient_company_identity_evidence"
    # Do not serialize HTML/cookies into identity records.
    selected = deepcopy(selected)
    if len(set(candidates)) > 5:
        selected.update(status="ambiguous", reason="identity_candidate_limit", company_url=None, evidence=[])
    selected["candidates"] = [{k: a.get(k) for k in ("company_url", "company_id", "display_name", "websites", "status", "reason", "evidence")} for a in audits]
    selected["checks"] = list(cached.checks)
    return selected, cached


def unknown_hiring(identity: dict) -> dict:
    return {"status": "unknown", "is_hiring": None, "jobs": [], "observed_job_count": 0, "current_listing_count": 0,
            "checked_at": identity.get("checked_at"), "coverage": "identity_unverified", "complete": False,
            "stop_reason": identity.get("reason", "identity_unverified"), "source_urls": [], "company_identity": deepcopy(identity)}


def fetch_verified(row: dict, source: str, client, fetcher, **kwargs) -> dict:
    """Owning CLI/prep entry point: resolve first, then fetch the verified company."""
    identity, cached = resolve_company(row, source, client)
    if identity["status"] == "verified":
        selected = {**row, source + "_company": identity["company_url"]}
        if identity.get("display_name"):
            selected["firm"] = identity["display_name"]
        out = fetcher(selected, cached, **kwargs)
    else:
        out = {"source": source + "_public", "schema_version": 3, "slug": row.get("slug", ""), "path": row.get("path", ""),
               "checked_at": identity["checked_at"], "status": "identity_" + identity["status"], "checks": [], "hiring": unknown_hiring(identity)}
        if source == "linkedin":
            out.update(contacts=[], linkedin_company="", people={"status": "identity_unverified", "observed_count": 0, "complete": False})
        else:
            out.update(company_url=None, discovery="identity_unverified")
    out["identity"] = identity
    out.setdefault("hiring", unknown_hiring(identity))["company_identity"] = deepcopy(identity)
    out["checks"] = identity["checks"] + out.get("checks", [])
    return out


def trusted(identity: dict, row: dict, source: str) -> bool:
    if not isinstance(identity, dict) or identity.get("policy") != POLICY or identity.get("status") != "verified":
        return False
    names, evidence = identity.get("observed_names", []), identity.get("evidence", [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names) or not isinstance(evidence, list):
        return False
    target = target_for(row)
    if not target["official_host"] or identity.get("target") != target or identity.get("source") != source:
        return False
    company = company_ref(identity.get("company_url", ""), source)
    if not company:
        return False
    for evidence in identity.get("evidence", []):
        if not isinstance(evidence, dict):
            continue
        kind = evidence.get("kind")
        if kind in {"official_link", "official_same_as"} and official_host(evidence.get("source_url", "")) == target["official_host"] and company_ref(evidence.get("target_url", ""), source) == company:
            return True
        if kind == "profile_website" and company_ref(evidence.get("source_url", ""), source) == company and official_host(evidence.get("target_url", "")) == target["official_host"] and set(normalize_name(n) for n in identity.get("observed_names", [])) & set(target["names"]):
            return True
    return False


def guard_row(row: dict, target: dict) -> dict:
    """Offline import gate. Old/name-only/misbound reports stay quarantined."""
    out = deepcopy(row)
    if out.get("source") != "linkedin_public":
        return out  # Explicit legacy manual contacts are not auto-verified.
    for source, data in (("linkedin", out), ("indeed", out.get("indeed"))):
        if not isinstance(data, dict) or (source == "linkedin" and out.get("linkedin_skipped")):
            continue
        identity = data.get("identity") or {}
        expected = company_ref(data.get("linkedin_company" if source == "linkedin" else "company_url", "") or "", source)
        valid = trusted(identity, target, source) and expected == identity.get("company_url")
        if not valid:
            preserved = deepcopy(identity) if isinstance(identity, dict) else {}
            status = preserved.get("status") if preserved.get("target") == target_for(target) else "unverified"
            if status == "verified" or not status:
                status = "unverified"
            identity = {**preserved, "policy": POLICY, "source": source, "target": target_for(target), "status": status,
                        "reason": preserved.get("reason") if status in REFUSED | {"conflict", "ambiguous"} else "missing_or_misbound_identity_evidence",
                        "checked_at": data.get("checked_at", ""), "company_url": None, "evidence": []}
            quarantine = data.setdefault("identity_quarantine", {})
            if data.get("hiring", {}).get("jobs"):
                quarantine["hiring"] = deepcopy(data["hiring"])
            if source == "linkedin":
                if data.get("contacts"):
                    quarantine["contacts"] = deepcopy(data["contacts"])
                data.update(contacts=[], linkedin_company="", people={"status": "identity_unverified", "observed_count": 0, "complete": False})
                data.pop("owner", None)
            else:
                data["company_url"] = None
            data.update(identity=identity, hiring=unknown_hiring(identity), status="identity_" + status)
        else:
            allowed = {identity["company_url"]}
            if source == "linkedin" and identity.get("company_id"):
                allowed.add(company_ref("https://www.linkedin.com/company/" + str(identity["company_id"]), source))
            hiring = data.get("hiring") or unknown_hiring(identity)
            good, bad = [], []
            for job in hiring.get("jobs", []):
                link = company_ref(job.get("company_url", "") or "", source)
                (good if link in allowed else bad).append(job)
            if bad:
                data.setdefault("identity_quarantine", {})["jobs"] = bad
                hiring.update(jobs=good, observed_job_count=len(good))
                eligible = [j for j in good if j.get("listing_status") != "closed" and j.get("detail_status") not in {"not_found", "job_mismatch", "company_mismatch"}]
                hiring.update(status="hiring" if eligible else "unknown", is_hiring=True if eligible else None,
                              current_listing_count=len(eligible), stop_reason="job_employer_identity_conflict")
            hiring["company_identity"] = deepcopy(identity)
            data["hiring"] = hiring
    return out


def merge_identity(profile: dict, row: dict, target: dict) -> None:
    """Persist proofs and retire previously unverified automatic contacts/POCs."""
    record = deepcopy(profile.get("company_identity") or {"sources": {}})
    sources = record.setdefault("sources", {})
    prior = sources.get("linkedin") or (profile.get("hiring_sources", {}).get("linkedin", {}).get("company_identity")) or (profile.get("hiring", {}).get("sources", {}).get("linkedin", {}).get("company_identity")) or {}
    incoming = row.get("identity") or {}
    if not row.get("linkedin_skipped"):
        same = trusted(prior, target, "linkedin") and trusted(incoming, target, "linkedin") and prior.get("company_url") == incoming.get("company_url")
        if not same:
            old = [c for c in profile.get("contacts", []) if isinstance(c, dict) and c.get("source") == "linkedin"]
            if old:
                record.setdefault("quarantine", {})["previous_linkedin_contacts"] = old
                profile["contacts"] = [c for c in profile.get("contacts", []) if c not in old]
            best = profile.get("best_poc") or {}
            if best.get("source") == "linkedin":
                profile["best_poc"] = None
    for source, data in (("linkedin", row), ("indeed", row.get("indeed"))):
        if not isinstance(data, dict) or (source == "linkedin" and row.get("linkedin_skipped")):
            continue
        identity = data.get("identity") or {}
        previous = sources.get(source) or {}
        if previous.get("status") == "verified" and previous != identity:
            record.setdefault("last_verified", {})[source] = deepcopy(previous)
        sources[source] = deepcopy(identity)
        if data.get("identity_quarantine"):
            record.setdefault("quarantine", {})[source] = deepcopy(data["identity_quarantine"])
    record.update(policy=POLICY, target=target_for(target))
    profile["company_identity"] = record

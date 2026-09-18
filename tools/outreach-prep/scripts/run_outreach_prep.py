#!/usr/bin/env python3
"""Outreach prep — one category week, cold-rerunnable.

Pipeline (prep only; outbound calls stay operator-gated):
  1. coverage-check → specialty-directory if thin (unless --skip-specialty)
  2. queue notes with official websites
  3. website-search crawl → Research/firms/<slug>.json
  4. Public LinkedIn company people + hiring (default ON; --skip-linkedin to opt out)
  5. enrich emails if profiles lack addresses
  6. build_poc / re-rank best_poc (named LI/web decision-maker beats info@)
  7. shortlist JSON under Research/firms/shortlists/
  8. push-backup on real writes

LinkedIn uses anonymous public HTML only. No keys, cookies or signed-in session.
People coverage is a public sample; unknown hiring is not a negative result.
Legacy structured JSONL can still be imported with --linkedin-results.
"""
from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

UA = "OutreachTools/1.0"
MT = ZoneInfo(os.environ.get("VAULT_TZ", "America/Chicago"))

ALIASES: dict[str, tuple[str, str | None]] = {
    "pi": ("Law", "personal-injury"),
    "personal-injury": ("Law", "personal-injury"),
    "personal injury": ("Law", "personal-injury"),
    "injury": ("Law", "personal-injury"),
    "trial": ("Law", "personal-injury"),
    "family": ("Law", "family"),
    "family-law": ("Law", "family"),
    "immigration": ("Law", "immigration"),
    "law": ("Law", None),
    "lawyer": ("Law", None),
    "lawyers": ("Law", None),
    "attorney": ("Law", None),
    "attorneys": ("Law", None),
    "trucking": ("Logistics", None),
    "truck": ("Logistics", None),
    "freight": ("Logistics", None),
    "logistics": ("Logistics", None),
    "oilfield": ("Oilfield", None),
    "oil": ("Oilfield", None),
    "oil-field": ("Oilfield", None),
    "hvac": ("HVAC", None),
    "ac": ("HVAC", None),
    "a/c": ("HVAC", None),
    "a-c": ("HVAC", None),
    "roofing": ("Roofing", None),
    "roof": ("Roofing", None),
    "dentist": ("Dentist", None),
    "dental": ("Dentist", None),
    "accounting": ("Accounting", None),
    "cpa": ("Accounting", None),
    "insurance": ("Insurance", None),
    "real-estate": ("Real estate", None),
    "real estate": ("Real estate", None),
    "realtor": ("Real estate", None),
    "realty": ("Real estate", None),
    "medical": ("Medical", None),
    "clinic": ("Medical", None),
    "doctor": ("Medical", None),
    "engineering": ("Engineering", None),
    "engineer": ("Engineering", None),
}

SPECIALTY_VERTICAL: dict[tuple[str, str | None], str | None] = {
    ("Law", "personal-injury"): "pi",
    ("Law", "pi"): "pi",
    ("Law", "trial"): "pi",
    ("Law", "family"): "family",
    ("Law", "immigration"): "immigration",
    ("Accounting", None): "accounting",
    ("Dentist", None): "dentist",
    ("Insurance", None): "insurance",
    ("Real estate", None): "real-estate",
    ("HVAC", None): "hvac",
    ("Roofing", None): "roofing",
    ("Medical", None): "medical",
    ("Engineering", None): "engineering",
}

DIRECTORY_HOSTS = (
    "justia.com", "findlaw.com", "lawyers.com", "avvo.com", "facebook.com",
    "yelp.com", "linkedin.com", "superpages.com", "google.com", "maps.google",
    "yellowpages.com", "bbb.org", "wikipedia.org", "wixsite.com",
    "martindale.com", "usattorneys.com",
)

STOP = {
    "the", "and", "for", "with", "from", "that", "this", "your", "our", "you",
    "are", "was", "were", "will", "can", "has", "have", "had", "not", "but",
    "all", "any", "how", "who", "what", "when", "where", "why", "its", "into",
    "a", "an", "of", "to", "in", "on", "at", "by", "or", "as", "is", "be",
}

def _tools_root() -> Path:
    raw = os.environ.get("TOOLS_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _tool_candidates(kind: str) -> list[Path]:
    names = {
        "outreach": "outreach-leads",
        "research": "website-search",
        "api": "website-to-api",
    }
    name = names[kind]
    root = _tools_root()
    return [
        root / name,
        Path.cwd() / "tools" / name,
        Path.cwd() / name,
    ]


def vault_now() -> datetime:
    return datetime.now(MT)


def vault_day() -> str:
    return vault_now().strftime("%Y-%m-%d")


def vault_stamp() -> str:
    return vault_now().strftime("%Y-%m-%d %H:%M %Z")


def vault_iso() -> str:
    return vault_now().isoformat(timespec="seconds")


def category_slug(category: str) -> str:
    s = unicodedata.normalize("NFKD", category or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "category"


def resolve_alias(raw: str, practice: str | None) -> tuple[str, str | None]:
    key = (raw or "").strip().lower()
    if key in ALIASES:
        cat, prac = ALIASES[key]
        return cat, practice or prac
    return raw.strip(), practice


def find_tool_root(kind: str) -> Path | None:
    for p in _tool_candidates(kind):
        if p.is_dir() and (p / "scripts").is_dir():
            return p
    return None


def script_path(root: Path | None, name: str) -> Path | None:
    if root is None:
        return None
    p = root / "scripts" / name
    return p if p.is_file() else None


def run_cmd(cmd: list[str], *, dry_run: bool, label: str) -> tuple[int, str, str]:
    print(f"\n## {label}")
    print("$ " + " ".join(cmd))
    if dry_run and any(
        x in label
        for x in (
            "specialty-directory",
            "website-search",
            "enrich-emails",
            "build-poc",
            "push-backup",
        )
    ):
        print("(dry-run: skipped)")
        return 0, "", ""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "").rstrip()
    err = (proc.stderr or "").rstrip()
    if out:
        print(out[-4000:] if len(out) > 4000 else out)
    if err and proc.returncode != 0:
        print(err[-2000:] if len(err) > 2000 else err, file=sys.stderr)
    print(f"exit={proc.returncode}")
    return proc.returncode, out, err


def fm_get(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:[ \t]*\"?([^\n\"]*)\"?[ \t]*$", text, re.M)
    if not m:
        return ""
    return (m.group(1) or "").strip()


def website_value(text: str) -> str:
    w = fm_get(text, "website")
    if not w or w.lower() in {"n/a", "none", "na", "null", "-"}:
        return ""
    return w.rstrip("\\").strip().strip('"').strip("'")


def category_has(text: str, category: str) -> bool:
    raw = fm_get(text, "category")
    if not raw:
        return False
    cat = category.strip()
    if f"[[{cat}]]" in raw:
        return True
    bare = re.sub(r"[\[\]\"]", "", raw).strip()
    return bare == cat


def practice_matches(text: str, practice: str | None) -> bool:
    if not practice:
        return True
    prac = fm_get(text, "practice").lower()
    want = practice.lower().strip()
    aliases = {
        "pi": {"personal-injury", "pi", "trial"},
        "personal-injury": {"personal-injury", "pi", "trial"},
        "trial": {"personal-injury", "pi", "trial"},
        "family": {"family", "family-law"},
        "immigration": {"immigration", "immigration-law"},
    }
    accepted = aliases.get(want, {want})
    if prac in accepted:
        return True
    if want in prac or prac in accepted:
        return True
    name = fm_get(text, "name").lower()
    if want in {"pi", "personal-injury", "trial"}:
        return any(
            x in name
            for x in ("personal injury", "injury law", "trial attorney", "trial lawyers", "trial law")
        )
    return False


def host_blocked(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return any(h in host for h in DIRECTORY_HOSTS)


def queue_firms(vault: Path, category: str, practice: str | None, max_firms: int) -> list[dict]:
    biz = vault / "Businesses"
    items: list[dict] = []
    if not biz.is_dir():
        return items
    for path in sorted(biz.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not category_has(text, category):
            continue
        if not practice_matches(text, practice):
            continue
        website = website_value(text)
        if not website or not website.startswith(("http://", "https://")):
            continue
        if host_blocked(website):
            continue
        items.append(
            {
                "path": str(path.resolve()),
                "slug": path.stem,
                "name": fm_get(text, "name") or path.stem,
                "website": website,
                "practice": fm_get(text, "practice") or (practice or ""),
                "linkedin_company": fm_get(text, "linkedin_company"),
                "email": fm_get(text, "email"),
            }
        )
        if len(items) >= max_firms:
            break
    return items


def pick_key_pages(pages: list[dict]) -> list[dict]:
    wanted = [
        ("contact", re.compile(r"/(contact|contact-us)/?$", re.I)),
        ("about", re.compile(r"/(about|about-us|our-firm)/?$", re.I)),
        ("team", re.compile(r"/(team|our-team|people|staff)/?$", re.I)),
        ("attorneys", re.compile(r"/(attorneys|attorney|lawyers)/?$", re.I)),
    ]
    found: dict[str, dict] = {}
    for label, cre in wanted:
        for p in pages:
            if p.get("status") != 200:
                continue
            url = p.get("final_url") or p.get("url") or ""
            path = urlparse(url).path or "/"
            if cre.search(path):
                found[label] = {
                    "label": label,
                    "url": url,
                    "title": (p.get("title") or "").strip(),
                    "status": 200,
                }
                break
    order = ["contact", "about", "team", "attorneys"]
    return [found[lab] for lab in order if lab in found]


def theme_words(themes: list[str], n: int = 8) -> list[str]:
    out = []
    for t in themes:
        word = re.sub(r"\s*\(\d+\)\s*$", "", t).strip()
        if word.lower() in STOP or len(word) < 3:
            continue
        out.append(word)
        if len(out) >= n:
            break
    return out


def schema_types_list(pages: list[dict]) -> list[str]:
    c: Counter[str] = Counter()
    for p in pages:
        for t in p.get("schema_types") or []:
            c[t] += 1
    return [t for t, _ in c.most_common(20)]


def compact_ai_bots(ai_bots: dict) -> dict:
    out = {}
    for bot, info in sorted((ai_bots or {}).items()):
        if not isinstance(info, dict):
            out[bot] = {"signal": str(info)}
            continue
        disallows = info.get("disallows") or []
        notable = [d for d in disallows if d not in ("/wp-admin/", "/wp-includes/", "/cgi-bin/")]
        sig = info.get("signal") or ""
        if sig.startswith("partial-disallow") and not notable:
            continue
        entry = {"signal": sig}
        if notable:
            entry["disallows"] = notable[:6]
        out[bot] = entry
    return out


def build_summary(name: str, category: str, pages: list[dict], themes: list[str], website: str) -> str:
    ok = sum(1 for p in pages if p.get("status") == 200)
    host = urlparse(website).netloc or website
    bits = theme_words(themes, 6)
    if bits:
        return (
            f"{name} ({host}) — {category} site; crawl fetched {ok} OK pages. "
            f"Title/h1 theme signals: {', '.join(bits)}."
        )
    return f"{name} ({host}) — {category} site researched; {ok} OK pages fetched."


def build_profile(
    *,
    q: dict,
    category: str,
    run: dict,
    out_md: Path,
    out_js: Path,
    vault: Path,
) -> dict:
    pages = run.get("pages") or []
    themes_raw = run.get("theme_hints") or []
    emails_map = run.get("emails_map") or {}
    llms = run.get("llms_txt") or {}
    linkedin_url = q.get("linkedin_company") or None
    practice = q.get("practice") or None
    emails = [{"email": em, "pages": list(urls)} for em, urls in sorted(emails_map.items())]
    themes = theme_words(themes_raw, 8) or themes_raw[:8]
    note_rel = str(Path(q["path"]).relative_to(vault)) if Path(q["path"]).is_relative_to(vault) else q["path"]
    return {
        "slug": q["slug"],
        "name": q["name"],
        "website": q["website"],
        "category": category,
        "practice": practice or None,
        "summary": build_summary(q["name"], category, pages, themes_raw, q["website"]),
        "emails": emails,
        "key_pages": pick_key_pages(pages),
        "themes": themes,
        "theme_hints_raw": themes_raw[:12],
        "schema_types": schema_types_list(pages),
        "llm_readiness": {
            "llms_txt": bool(llms.get("present")),
            "llms_txt_status": llms.get("status"),
            "llms_txt_url": llms.get("url"),
            "ai_bots": compact_ai_bots(run.get("ai_bots") or {}),
        },
        "linkedin": {
            "company_url": linkedin_url,
            "status": "linked" if linkedin_url else "not_linked_yet",
        },
        "crawl": {
            "pages_fetched": len(pages),
            "pages_ok": sum(1 for p in pages if p.get("status") == 200),
            "discovered_n": run.get("discovered_n"),
            "ua": run.get("ua") or UA,
        },
        "sources": {
            "research_md": str(out_md.relative_to(vault)) if out_md.exists() else str(out_md),
            "research_json": str(out_js.relative_to(vault)) if out_js.is_relative_to(vault) else str(out_js),
            "note": note_rel,
        },
        "updated": vault_iso(),
        "updated_stamp": vault_stamp(),
        "contacts": [],
        "best_poc": None,
        "inbox_fallback": None,
    }


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_profile(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def poc_rank_key(profile: dict) -> tuple:
    best = profile.get("best_poc") or {}
    kind = (best.get("kind") or "").lower()
    score = best.get("poc_score")
    if score is None:
        score = -1
    named = 1 if kind == "person" and (best.get("name") or best.get("email")) else 0
    has_inbox = 1 if (profile.get("inbox_fallback") or {}).get("email") else 0
    return (named, int(score) if isinstance(score, (int, float)) else -1, has_inbox)


def build_shortlist(vault: Path, category: str, slugs: list[str], day: str) -> Path:
    firms_dir = vault / "Research" / "firms"
    ranked = []
    for slug in slugs:
        p = load_profile(firms_dir / f"{slug}.json")
        if not p:
            continue
        best = p.get("best_poc")
        entry = {
            "slug": p.get("slug") or slug,
            "name": p.get("name"),
            "website": p.get("website"),
            "category": p.get("category") or category,
            "practice": p.get("practice"),
            "best_poc": best,
            "inbox_fallback": p.get("inbox_fallback"),
            "poc_score": (best or {}).get("poc_score") if best else None,
            "emails_n": len(p.get("emails") or []),
            "linkedin_people": (p.get("linkedin") or {}).get("people"),
            "hiring": {key: (p.get("hiring") or {}).get(key) for key in (
                "status", "is_hiring", "observed_job_count", "checked_at", "stop_reason"
            )},
        }
        ranked.append(entry)
    ranked.sort(
        key=lambda e: poc_rank_key(
            {"best_poc": e.get("best_poc"), "inbox_fallback": e.get("inbox_fallback")}
        ),
        reverse=True,
    )
    out = {
        "category": category,
        "day": day,
        "updated": vault_iso(),
        "updated_stamp": vault_stamp(),
        "count": len(ranked),
        "rank_order": "best_poc named first; inbox_fallback last",
        "firms": ranked,
    }
    path = firms_dir / "shortlists" / f"{category_slug(category)}-{day}.json"
    write_json(path, out)
    return path


def is_masked_name(name: str | None) -> bool:
    n = (name or "").strip().lower()
    return (not n) or n in {"linkedin member", "linkedin user", "member"}


def merge_linkedin_into_profiles(vault: Path, results_path: Path, *, category: str) -> dict:
    """Merge linkedin-results JSONL into Research/firms/<slug>.json contacts[]."""
    firms_dir = vault / "Research" / "firms"
    stats = {"rows": 0, "merged": 0, "blocked": 0, "masked": 0, "missing_profile": 0}
    if not results_path.is_file():
        return stats
    owner_re = re.compile(
        r"\b(owner|founder|co-?founder|ceo|president|managing\s+partner|partner|principal|shareholder)\b",
        re.I,
    )
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        stats["rows"] += 1
        slug = (row.get("slug") or "").strip()
        if not slug or Path(slug).name != slug or slug in {".", ".."}:
            continue
        if row.get("source") == "linkedin_public":
            # The public importer owns lossless merging and partial/stale evidence.
            outreach = find_tool_root("outreach")
            if outreach and str(outreach / "scripts") not in sys.path:
                sys.path.insert(0, str(outreach / "scripts"))
            from linkedin_contacts import merge_public_profile, validate_row
            validate_row(row)
            merge_public_profile(vault, row)
            stats["merged"] += 1
            stats["blocked"] += int(row.get("status") in {"blocked", "rate_limited"})
            stats["masked"] += int((row.get("people") or {}).get("status") == "names_masked")
            continue
        profile_path = firms_dir / f"{slug}.json"
        profile = load_profile(profile_path)
        if not profile:
            stats["missing_profile"] += 1
            continue
        li = dict(profile.get("linkedin") or {})
        company = (row.get("linkedin_company") or li.get("company_url") or "").strip() or None
        if company:
            li["company_url"] = company
        contacts_in = row.get("contacts") or []
        if not isinstance(contacts_in, list):
            contacts_in = []
        notes = (row.get("notes") or "").lower()
        names_masked = bool(row.get("names_masked")) or "names_masked" in notes or (
            contacts_in
            and all(is_masked_name(c.get("name") if isinstance(c, dict) else None) for c in contacts_in)
        )
        blocked = bool(row.get("blocked")) or (row.get("status") or "").lower() == "blocked" or "blocked" in notes
        if blocked:
            li["status"] = "blocked"
            stats["blocked"] += 1
        elif names_masked:
            li["status"] = "names_masked"
            stats["masked"] += 1
        elif company:
            li["status"] = "linked"
        else:
            li["status"] = li.get("status") or "attempted"
        li["updated"] = vault_iso()
        profile["linkedin"] = li

        existing = [c for c in (profile.get("contacts") or []) if isinstance(c, dict)]
        if contacts_in and not blocked and not names_masked:
            existing = [c for c in existing if (c.get("source") or "").lower() != "linkedin"]
        for c in contacts_in:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip() or None
            title = (c.get("title") or "").strip() or None
            email = (c.get("email") or "").strip() or None
            entry = {
                "name": name,
                "title": title,
                "email": email,
                "phone": (c.get("phone") or "").strip() or None,
                "profile_url": (c.get("profile_url") or "").strip() or None,
                "photo_url": (c.get("photo_url") or c.get("pfp") or "").strip() or None,
                "location": (c.get("location") or "").strip() or None,
                "source": "linkedin",
                "kind": "person" if name and not is_masked_name(name) else "linkedin_card",
            }
            score = 0
            if entry["kind"] == "person":
                score += 5
            if title and owner_re.search(title):
                score += 4
            if email and not email.lower().startswith(
                ("info@", "contact@", "office@", "hello@", "admin@", "team@")
            ):
                score += 3
            entry["poc_score"] = score
            existing.append(entry)
        profile["contacts"] = existing

        candidates = [c for c in existing if c.get("kind") == "person" and c.get("name")]
        candidates.sort(key=lambda c: int(c.get("poc_score") or 0), reverse=True)
        if candidates:
            top = candidates[0]
            prev = profile.get("best_poc") or {}
            prev_score = int(prev.get("poc_score") or 0) if prev else -1
            top_score = int(top.get("poc_score") or 0)
            prev_kind = (prev.get("kind") or "").lower() if prev else ""
            if (
                (not prev)
                or prev_kind != "person"
                or top_score >= prev_score
                or (top.get("source") == "linkedin" and prev_kind != "person")
            ):
                profile["best_poc"] = {
                    "name": top.get("name"),
                    "title": top.get("title"),
                    "email": top.get("email"),
                    "phone": top.get("phone"),
                    "profile_url": top.get("profile_url"),
                    "source_pages": [top.get("profile_url")] if top.get("profile_url") else [],
                    "poc_score": top_score,
                    "kind": "person",
                    "source": top.get("source") or "linkedin",
                }
        write_json(profile_path, profile)
        stats["merged"] += 1
    return stats


def find_linkedin_results(vault: Path, day: str, explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    candidate = vault / "Sources" / "runs" / f"linkedin-results-{day}.jsonl"
    return candidate if candidate.is_file() else None


def write_prove(
    vault: Path,
    *,
    category: str,
    practice: str | None,
    day: str,
    lines: list[str],
    dry_run: bool,
) -> Path:
    cat_slug = category_slug(category)
    path = vault / "Sources" / f"outreach-prep-{cat_slug}-{day}.md"
    prac = f" / {practice}" if practice else ""
    body = [
        "---",
        "type: source",
        f'name: "outreach-prep — {category}{prac} — {day}"',
        "---",
        "",
        f"# Prove log — outreach-prep — {category}{prac} — {day}",
        "",
        "## Goal",
        f"Prep week for {category}{prac}: cover → crawl → LinkedIn → POC → shortlist (calls gated).",
        "",
        "## Mode",
        "outreach-prep" + (" (dry-run)" if dry_run else ""),
        "",
        "## Stamp",
        vault_stamp(),
        "",
        "## Log",
        "",
    ]
    for line in lines:
        body.append(f"- {line}")
    body.extend(
        [
            "",
            "## Blind-spot check",
            "- Calls operator-gated (prep only)? Y",
            "- Named best_poc preferred over info@? Y",
            "- LinkedIn attempted for queued firms (or --skip-linkedin)? Y",
            "- Blocked LI continues week? Y",
            "",
            "## Artifacts",
            "- `Research/firms/<slug>.json`",
            f"- `Research/firms/shortlists/{cat_slug}-{day}.json`",
            "- this prove log",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def specialty_vertical_for(category: str, practice: str | None) -> str | None:
    if (category, practice) in SPECIALTY_VERTICAL:
        return SPECIALTY_VERTICAL[(category, practice)]
    if (category, None) in SPECIALTY_VERTICAL:
        return SPECIALTY_VERTICAL[(category, None)]
    if category == "Law":
        key = (practice or "personal-injury").lower()
        if key in {"pi", "personal-injury", "trial", ""}:
            return "pi"
        if key in {"family", "family-law"}:
            return "family"
        if key in {"immigration", "immigration-law"}:
            return "immigration"
        return "pi"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="run_outreach_prep",
        description="One-vertical outreach prep week (cover → crawl → LinkedIn → POC → shortlist)",
    )
    ap.add_argument("--category", required=True, help="Vault category or alias (Law, trucking, HVAC, pi, …)")
    ap.add_argument("--practice", default=None, help="e.g. personal-injury / family / immigration")
    ap.add_argument("--max-firms", type=int, default=50)
    ap.add_argument("--max-pages", type=int, default=35)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--vault", default=os.environ.get("VAULT_ROOT", str(Path.cwd() / "data")))
    ap.add_argument("--skip-specialty", action="store_true")
    ap.add_argument("--skip-poc", action="store_true")
    ap.add_argument("--skip-emails", action="store_true", help="skip enrich --layer emails when thin")
    ap.add_argument(
        "--skip-linkedin",
        action="store_true",
        help="opt out of public LinkedIn people + hiring (default ON)",
    )
    ap.add_argument(
        "--linkedin-results",
        default=None,
        help="import this JSONL instead of fetching fresh public LinkedIn results",
    )
    ap.add_argument("--linkedin-max-jobs", type=int, default=50)
    ap.add_argument("--linkedin-job-pages", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    if args.max_firms < 1 or not 1 <= args.linkedin_max_jobs <= 500 or not 1 <= args.linkedin_job_pages <= 20:
        ap.error("max-firms >= 1; linkedin-max-jobs 1..500; linkedin-job-pages 1..20")
    if args.linkedin_results and not Path(args.linkedin_results).is_file():
        ap.error("--linkedin-results file does not exist")

    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        print(f"FAIL: vault missing: {vault}", file=sys.stderr)
        return 2

    category, practice = resolve_alias(args.category, args.practice)
    day = vault_day()
    log: list[str] = []
    wrote = False
    applied_linkedin_results = None

    print(f"# outreach-prep — {category}" + (f" / {practice}" if practice else ""))
    print(f"vault={vault}")
    print(f"day={day}  stamp={vault_stamp()}")
    print(f"dry_run={args.dry_run}  max_firms={args.max_firms}  max_pages={args.max_pages}")
    if args.category.strip().lower() != category.lower() or (
        args.practice != practice and args.practice
    ):
        print(f"alias: {args.category!r} → category={category!r} practice={practice!r}")
        log.append(f"alias {args.category!r} → {category}/{practice}")

    outreach = find_tool_root("outreach")
    research = find_tool_root("research")
    coverage_py = script_path(outreach, "coverage_check.py")
    specialty_py = script_path(outreach, "specialty_directory.py")
    emails_py = script_path(outreach, "website_emails.py")
    enrich_py = script_path(outreach, "enrich.py")
    build_poc_py = script_path(outreach, "build_poc.py")
    push_py = script_path(outreach, "push_backup.py")
    research_py = script_path(research, "research_website.py")
    outreach_cli = script_path(outreach, "outreach_leads.py")
    linkedin_py = script_path(outreach, "linkedin_contacts.py")

    log.append(f"outreach root: {outreach or 'MISSING'}")
    log.append(f"research root: {research or 'MISSING'}")

    # --- 1. coverage-check ---
    coverage_rc = 0
    if coverage_py:
        cmd = ["python3", str(coverage_py), "--category", category, "--vault", str(vault)]
        if practice:
            cmd.extend(["--practice", practice])
        coverage_rc, out, _ = run_cmd(cmd, dry_run=False, label="coverage-check")
        verdict = "COVERED" if coverage_rc == 0 else "NOT COVERED"
        log.append(f"coverage-check → {verdict} (exit {coverage_rc})")
    else:
        print("\n## coverage-check\n(skip: coverage_check.py not found)")
        log.append("coverage-check SKIPPED — script missing")
        coverage_rc = 2

    # --- 2. specialty if thin ---
    if coverage_rc != 0 and not args.skip_specialty:
        vert = specialty_vertical_for(category, practice)
        if vert and specialty_py:
            cmd = [
                "python3",
                str(specialty_py),
                "--vertical",
                vert,
                "--vault",
                str(vault),
                "--limit",
                str(args.max_firms),
            ]
            if args.dry_run:
                cmd.append("--dry-run")
            if args.no_push or args.dry_run:
                cmd.append("--no-push")
            rc, _, _ = run_cmd(cmd, dry_run=args.dry_run, label=f"specialty-directory ({vert})")
            log.append(f"specialty-directory {vert} exit={rc}")
            if not args.dry_run and rc == 0:
                wrote = True
        elif not vert:
            print("\n## specialty-directory\n(no specialty vertical mapped for this category — skip)")
            log.append("specialty-directory SKIPPED — no vertical map")
        else:
            print("\n## specialty-directory\n(skip: specialty_directory.py missing)")
            log.append("specialty-directory SKIPPED — script missing")
    elif args.skip_specialty:
        log.append("specialty-directory skipped via --skip-specialty")
    else:
        log.append("specialty-directory not needed (COVERED)")

    # --- 3. queue notes with websites ---
    queue = queue_firms(vault, category, practice, args.max_firms)
    print(f"\n## queue\nfirms_with_website={len(queue)} (cap {args.max_firms})")
    for q in queue[:15]:
        print(f"  - {q['slug']}  {q['website']}")
    if len(queue) > 15:
        print(f"  … +{len(queue) - 15} more")
    log.append(f"queue={len(queue)}")

    queue_path = vault / "Sources" / "runs" / f"outreach-queue-{category_slug(category)}-{day}.jsonl"
    if not args.dry_run:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        with queue_path.open("w", encoding="utf-8") as f:
            for q in queue:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")
        wrote = True
        log.append(f"queue jsonl → {queue_path.relative_to(vault)}")
    else:
        log.append(f"queue jsonl would be {queue_path}")

    # --- 4. website-search → firm profiles ---
    profiles_written: list[str] = []
    research_errors: list[str] = []
    if not research_py:
        print("\n## website-search\n(skip: research_website.py missing)")
        log.append("website-search SKIPPED — script missing")
    elif args.dry_run:
        print("\n## website-search\n(dry-run: would crawl)")
        for q in queue:
            print(f"  would research {q['website']} → Research/firms/{q['slug']}.json")
        log.append(f"website-search dry-run planned={len(queue)}")
    else:
        firms_dir = vault / "Research" / "firms"
        firms_dir.mkdir(parents=True, exist_ok=True)
        runs_dir = vault / "Sources" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        for i, q in enumerate(queue, 1):
            slug = q["slug"]
            out_md = firms_dir / f"{slug}-{day}.md"
            out_js = runs_dir / f"research-{slug}-{day}.json"
            profile_path = firms_dir / f"{slug}.json"
            print(f"\n## website-search [{i}/{len(queue)}] {slug}")
            run = None
            if out_js.is_file():
                try:
                    run = json.loads(out_js.read_text(encoding="utf-8"))
                    if run.get("pages"):
                        print(f"reuse {out_js}")
                    else:
                        run = None
                except json.JSONDecodeError:
                    run = None
            if run is None:
                cmd = [
                    "python3",
                    str(research_py),
                    q["website"],
                    "--out",
                    str(out_md),
                    "--json",
                    str(out_js),
                    "--max-pages",
                    str(args.max_pages),
                    "--concurrency",
                    str(max(1, min(8, args.concurrency))),
                    "--include-emails",
                ]
                rc, _, err = run_cmd(cmd, dry_run=False, label=f"crawl {slug}")
                if rc != 0 and not out_js.is_file():
                    research_errors.append(f"{slug}: research exit {rc}")
                    continue
                try:
                    run = json.loads(out_js.read_text(encoding="utf-8"))
                except Exception as e:
                    research_errors.append(f"{slug}: {e}")
                    continue
            profile = build_profile(
                q=q, category=category, run=run, out_md=out_md, out_js=out_js, vault=vault
            )
            prior = load_profile(profile_path)
            if prior:
                for k in ("contacts", "best_poc", "inbox_fallback"):
                    if prior.get(k) is not None and profile.get(k) in (None, []):
                        profile[k] = prior[k]
                for k in ("linkedin", "hiring", "linkedin_last_successful_hiring"):
                    if prior.get(k) is not None:
                        profile[k] = prior[k]
            write_json(profile_path, profile)
            profiles_written.append(slug)
            wrote = True
            print(f"wrote {profile_path}")
        log.append(f"profiles_written={len(profiles_written)}")
        if research_errors:
            log.append("research errors: " + "; ".join(research_errors[:8]))

    # --- 5. Public LinkedIn company people + hiring (default ON) ---
    if args.skip_linkedin:
        log.append("linkedin skipped via --skip-linkedin")
        print("\n## linkedin\n(skipped via --skip-linkedin)")
    elif args.dry_run:
        print(f"\n## linkedin\n(dry-run: would fetch public people + hiring for {len(queue)} queued firms; no login or API keys)")
        log.append(f"linkedin public dry-run planned={len(queue)}")
    elif not linkedin_py:
        print("\n## linkedin\n(skip: linkedin_contacts.py missing)")
        log.append("linkedin public SKIPPED — script missing")
    else:
        if args.linkedin_results:
            results = Path(args.linkedin_results)
            cmd = [sys.executable, str(linkedin_py), "--apply", str(results)]
        else:
            # A run-scoped path avoids silently reusing another vertical's results.
            token = vault_now().strftime("%Y%m%dT%H%M%S%f")
            scope = category_slug(category + "-" + (practice or "all"))
            results = vault / "Sources" / "runs" / f"linkedin-results-{scope}-{token}.jsonl"
            cmd = [sys.executable, str(linkedin_py), "--queue", str(queue_path), "--output", str(results),
                   "--max-jobs", str(args.linkedin_max_jobs), "--job-pages", str(args.linkedin_job_pages)]
        cmd.extend(["--vault", str(vault), "--no-push"])
        rc, _, _ = run_cmd(cmd, dry_run=False, label="linkedin public people + hiring")
        log.append(f"linkedin public exit={rc} results={results}")
        if rc == 0 and results.is_file():
            applied_linkedin_results = results
            merge_stats = merge_linkedin_into_profiles(vault, results, category=category)
            log.append("linkedin merge profiles: " + json.dumps(merge_stats))
            hiring_counts = Counter()
            for line in results.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    hiring_counts[(row.get("hiring") or {}).get("status", "not_checked")] += 1
            log.append("linkedin hiring outcomes (public coverage only): " + json.dumps(hiring_counts))
            wrote = True
        else:
            log.append("linkedin enrichment failed; no fresh hiring conclusion; week continues")

    # --- 6. enrich emails if needed ---
    need_email_slugs = []
    if not args.dry_run:
        for slug in profiles_written or [q["slug"] for q in queue]:
            p = load_profile(vault / "Research" / "firms" / f"{slug}.json")
            if not p:
                continue
            if not (p.get("emails") or []):
                need_email_slugs.append(slug)
    if args.skip_emails:
        log.append("enrich emails skipped via --skip-emails")
    elif args.dry_run:
        log.append("enrich emails dry-run (deferred)")
        print("\n## enrich emails\n(dry-run: skipped)")
    elif need_email_slugs and (emails_py or enrich_py or outreach_cli):
        print(f"\n## enrich emails\nneed={len(need_email_slugs)}")
        if outreach_cli:
            cmd = [
                "python3",
                str(outreach_cli),
                "enrich",
                "--layer",
                "emails",
                "--category",
                category,
                "--limit",
                str(min(len(need_email_slugs), args.max_firms)),
                "--max-pages",
                str(args.max_pages),
                "--vault",
                str(vault),
            ]
            if practice:
                cmd.extend(["--practice", practice])
            if args.no_push:
                cmd.append("--no-push")
            rc, _, _ = run_cmd(cmd, dry_run=False, label="enrich emails")
            log.append(f"enrich emails exit={rc} need={len(need_email_slugs)}")
            if rc == 0:
                wrote = True
        elif emails_py:
            cmd = [
                "python3",
                str(emails_py),
                "--category",
                category,
                "--limit",
                str(min(len(need_email_slugs), args.max_firms)),
                "--max-pages",
                str(args.max_pages),
                "--vault",
                str(vault),
            ]
            if practice:
                cmd.extend(["--practice", practice])
            if args.no_push:
                cmd.append("--no-push")
            rc, _, _ = run_cmd(cmd, dry_run=False, label="website_emails")
            log.append(f"website_emails exit={rc}")
            if rc == 0:
                wrote = True
    else:
        log.append("enrich emails not needed or tools missing")

    # --- 7. build_poc ---
    if args.skip_poc:
        log.append("build-poc skipped via --skip-poc")
        print("\n## build-poc\n(skipped)")
    elif args.dry_run:
        print("\n## build-poc\n(dry-run: skipped)")
        log.append("build-poc dry-run")
    elif build_poc_py:
        cmd = [
            "python3",
            str(build_poc_py),
            "--vault",
            str(vault),
            "--limit",
            str(args.max_firms),
            "--max-pages",
            str(min(20, args.max_pages)),
        ]
        if args.no_push:
            cmd.append("--no-push")
        if len(queue) <= 10:
            for q in queue:
                c2 = cmd + ["--slug", q["slug"]]
                rc, _, _ = run_cmd(c2, dry_run=False, label=f"build-poc {q['slug']}")
                if rc == 0:
                    wrote = True
            log.append(f"build-poc per-slug n={len(queue)}")
        else:
            rc, _, _ = run_cmd(cmd, dry_run=False, label="build-poc")
            log.append(f"build-poc exit={rc}")
            if rc == 0:
                wrote = True
    else:
        print("\n## build-poc\nTODO: build_poc.py missing under outreach-leads/scripts")
        log.append("TODO: build_poc.py missing — named POC ranking not run")

    # POC ranking rewrites linkedin metadata; restore this run's public evidence
    # without re-fetching or changing the website-derived rankings.
    if applied_linkedin_results is not None and not args.dry_run:
        stats = merge_linkedin_into_profiles(vault, applied_linkedin_results, category=category)
        log.append(f"linkedin evidence restored after POC: merged={stats['merged']}")

    # --- 8. shortlist ---
    shortlist_path = None
    if args.dry_run:
        print(
            f"\n## shortlist\n(dry-run: would write Research/firms/shortlists/"
            f"{category_slug(category)}-{day}.json for {len(queue)} firms)"
        )
        log.append("shortlist dry-run")
    else:
        slugs = profiles_written or [q["slug"] for q in queue]
        existing = []
        for q in queue:
            if (vault / "Research" / "firms" / f"{q['slug']}.json").is_file():
                existing.append(q["slug"])
        use = existing or slugs
        shortlist_path = build_shortlist(vault, category, use, day)
        wrote = True
        print(f"\n## shortlist\nwrote {shortlist_path}")
        log.append(f"shortlist → {shortlist_path.relative_to(vault)}")

    # --- prove log ---
    if not args.dry_run:
        prove = write_prove(
            vault,
            category=category,
            practice=practice,
            day=day,
            lines=log,
            dry_run=False,
        )
        wrote = True
        print(f"\n## prove\nwrote {prove}")
    else:
        print("\n## prove (dry-run plan)")
        for line in log:
            print(f"  - {line}")
        prove = vault / "Sources" / f"outreach-prep-{category_slug(category)}-{day}.md"
        print(f"  would write {prove}")

    # --- 9. push-backup ---
    if args.dry_run or args.no_push or not wrote:
        print("\n## push-backup\n(skipped: dry-run / --no-push / no writes)")
        log.append("push-backup skipped")
    elif push_py:
        rc, out, _ = run_cmd(
            ["python3", str(push_py), "--vault", str(vault)],
            dry_run=False,
            label="push-backup",
        )
        log.append(f"push-backup exit={rc}")
        if "http" in (out or "").lower():
            for line in out.splitlines():
                if "http" in line.lower() and ("github" in line.lower() or "commit" in line.lower()):
                    print(f"commit: {line.strip()}")
    elif outreach_cli:
        rc, _, _ = run_cmd(
            ["python3", str(outreach_cli), "push-backup", "--vault", str(vault)],
            dry_run=False,
            label="push-backup",
        )
        log.append(f"push-backup exit={rc}")
    else:
        print("\n## push-backup\n(skip: push_backup.py missing)")
        log.append("push-backup SKIPPED — script missing")

    print("\n## done")
    print(f"category={category} practice={practice or '-'}")
    print(f"queued={len(queue)} profiles={len(profiles_written)} dry_run={args.dry_run}")
    if shortlist_path:
        print(f"shortlist={shortlist_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

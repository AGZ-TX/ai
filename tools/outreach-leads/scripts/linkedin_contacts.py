#!/usr/bin/env python3
"""Fetch public LinkedIn people + hiring, or queue/apply legacy JSONL.

Existing `enrich --layer contacts` now fetches and applies in one pass.
No authenticated LinkedIn session, API key, or third-party service is used.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from note_io import DEFAULT_VAULT, category_has, fm_get, iter_notes, practice_is_pi, website_value
from linkedin_public import MASKED, PublicHTTP, clean, fetch_company, linkedin_url

OWNER_TITLE_RE = re.compile(r"\b(owner|founder|co-?founder|ceo|president|managing\s+partner|principal)\b", re.I)
CONTACTS_HEADING = re.compile(r"^##[ \t]+Contacts[ \t]*$", re.M)


def vault_day() -> str:
    return datetime.now(ZoneInfo(os.environ.get("VAULT_TZ", "America/Chicago"))).strftime("%Y-%m-%d")


def has_contacts_section(text: str) -> bool:
    m = re.search(r"^##[ \t]+Contacts[ \t]*\n(.*?)(?=^##[ \t]|\Z)", text, re.M | re.S)
    return bool(m and any(line.strip().startswith("- ") for line in m[1].splitlines()))


def matches_filters(text, category, practice, slug, path) -> bool:
    if slug and path.stem != slug.removesuffix(".md"):
        return False  # Exact identity: never update a different similarly named firm.
    if category and not category_has(text, category):
        return False
    if practice:
        if practice.lower() in {"pi", "personal-injury", "trial"}:
            return practice_is_pi(text)
        return practice.lower() in fm_get(text, "practice").lower()
    return True


def note_row(path: Path, text: str) -> dict:
    # Unlike the legacy fm_get regex, an empty field cannot consume the next line.
    field = re.search(r"^linkedin_company:[ \t]*([^\n]*)$", text, re.M)
    company = field[1].strip().strip('"').strip("'") if field else ""
    if not company:
        links = {linkedin_url(u) for u in re.findall(r'https?://[^\s<>"\)]+', text)} - {""}
        company = next(iter(links)) if len(links) == 1 else ""
    return {"slug": path.stem, "path": str(path.resolve()), "firm": fm_get(text, "name") or path.stem, "website": website_value(text), "city": re.sub(r'[\[\]"]', "", fm_get(text, "city")).strip(), "phone": fm_get(text, "phone"), "linkedin_company": company}


def list_candidates(vault, *, category=None, practice=None, limit=0, slug=None, force=False) -> list[dict]:
    rows = []
    for path in iter_notes(vault):
        if not path.resolve().is_relative_to((vault / "Businesses").resolve()) or path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8")
        if not matches_filters(text, category, practice, slug, path) or (not force and has_contacts_section(text)):
            continue
        row = note_row(path, text)
        # Reuse a known canonical company from the existing research profile.
        if not row["linkedin_company"]:
            profile_path = vault / "Research" / "firms" / f"{path.stem}.json"
            if profile_path.is_file() and not profile_path.is_symlink():
                try:
                    profile = json.loads(profile_path.read_text(encoding="utf-8"))
                    row["linkedin_company"] = (profile.get("linkedin") or {}).get("company_url") or ""
                except (ValueError, AttributeError):
                    pass
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return rows


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            name = f.name
            f.write(text)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def write_queue(vault, rows, dry_run=False):
    path = vault / "Sources" / "runs" / f"linkedin-queue-{vault_day()}.jsonl"
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    if dry_run:
        print(f"dry-run queue rows: {len(rows)} (would write {path})")
        return None
    atomic_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"queue\t{path}\trows={len(rows)}")
    return path


def _normalize_contact(c: dict) -> dict:
    if not isinstance(c, dict):
        return {}
    def value(key, alias=""):
        raw = c.get(key) or c.get(alias) or ""
        return clean(raw) if isinstance(raw, str) else ""
    return {"name": value("name"), "title": value("title"), "email": value("email"), "location": value("location"), "photo_url": value("photo_url", "pfp"), "profile_url": value("profile_url", "url")}


def _format_contact_bullet(c: dict) -> str:
    n = _normalize_contact(c)
    return "- " + " — ".join(n[k] for k in ("name", "title", "location", "email", "photo_url", "profile_url") if n[k])


def _pick_owner(contacts, explicit):
    if isinstance(explicit, str) and explicit.strip():
        return clean(explicit)
    return next((c["name"] for c in contacts if c.get("name") and OWNER_TITLE_RE.search(c.get("title", ""))), "")


def upsert_section(text: str, heading: str, body: str) -> str:
    block = f"## {heading}\n{body.rstrip()}\n"
    pattern = rf"^##[ \t]+{re.escape(heading)}[ \t]*\n.*?(?=^##[ \t]|\Z)"
    if re.search(pattern, text, re.M | re.S):
        return re.sub(pattern, lambda m: block + ("\n" if m.end() < len(text) else ""), text, count=1, flags=re.M | re.S)
    return text.rstrip() + "\n\n" + block


def upsert_contacts_section(text: str, bullets: list[str]) -> str:
    # Retain manual and previously discovered contacts; an incomplete public
    # sample is never an authoritative replacement for the existing directory.
    m = re.search(r"^##[ \t]+Contacts[ \t]*\n(.*?)(?=^##[ \t]|\Z)", text, re.M | re.S)
    body = m[1].strip() if m else ""
    for bullet in bullets:
        profile = next((linkedin_url(u, "in") for u in re.findall(r"https?://\S+", bullet) if linkedin_url(u, "in")), "")
        if bullet not in body.splitlines() and not (profile and profile in body):
            body = (body + "\n" + bullet).strip()
    return upsert_section(text, "Contacts", body)


def resolve_note(vault: Path, row: dict) -> Path | None:
    root = (vault / "Businesses").resolve()
    if not root.is_relative_to(vault.resolve()):
        return None
    supplied = row.get("path")
    slug = row.get("slug")
    if supplied:
        if not isinstance(supplied, str):
            return None
        path = Path(supplied)
        if not path.is_absolute():
            path = vault / path
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            return None
        if path.is_file() and path.suffix == ".md":
            if slug and slug.removesuffix(".md") != path.stem:
                return None
            return path.resolve()
    if isinstance(slug, str) and slug and Path(slug).name == slug:
        path = root / f"{slug.removesuffix('.md')}.md"
        if path.is_file() and not path.is_symlink():
            return path
    return None


def validate_row(row) -> None:
    if not isinstance(row, dict):
        raise ValueError("row must be an object")
    for key in ("path", "slug", "linkedin_company", "owner"):
        if row.get(key) is not None and not isinstance(row[key], str):
            raise ValueError(f"{key} must be a string")
    if row.get("linkedin_company") and not linkedin_url(row["linkedin_company"]):
        raise ValueError("invalid LinkedIn company URL")
    contacts = row.get("contacts") or []
    if not isinstance(contacts, list) or not all(isinstance(c, dict) for c in contacts):
        raise ValueError("contacts must be an array of objects")
    for key in ("people", "hiring"):
        if key in row and not isinstance(row[key], dict):
            raise ValueError(f"{key} must be an object")
    if "hiring" in row:
        jobs = row["hiring"].get("jobs") or []
        if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
            raise ValueError("hiring.jobs must be an array of objects")


def set_note_field(text: str, key: str, value: str) -> str:
    """Quote data, not regex replacement syntax; only modify frontmatter."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) != 3:
        return text
    line = key + ": " + json.dumps(value, ensure_ascii=False)
    pattern = rf"^{re.escape(key)}:[^\n]*$"
    front = re.sub(pattern, lambda _: line, parts[1], count=1, flags=re.M) if re.search(pattern, parts[1], re.M) else parts[1].rstrip("\n") + "\n" + line + "\n"
    return "---" + front + "---" + parts[2]


def apply_result_to_note(path: Path, row: dict, dry_run=False) -> dict:
    validate_row(row)
    text = path.read_text(encoding="utf-8")
    contacts = [_normalize_contact(c) for c in (row.get("contacts") or [])]
    contacts = [c for c in contacts if c.get("name") and c["name"].lower() not in MASKED]
    new = text
    company = linkedin_url(row.get("linkedin_company") or "")
    if company:
        new = set_note_field(new, "linkedin_company", company)
    owner = _pick_owner(contacts, row.get("owner"))
    if owner and (not fm_get(new, "owner") or row.get("owner")):
        new = set_note_field(new, "owner", owner)
    if contacts:
        new = upsert_contacts_section(new, [_format_contact_bullet(c) for c in contacts])
    if row.get("source") == "linkedin_public":
        people = row.get("people") or {}
        hiring = row.get("hiring") or {}
        body = [f"- Checked: {row.get('checked_at', '')}", f"- People: {people.get('status', 'unknown')}; {len(contacts)} observed; public sample only.", f"- Hiring: {hiring.get('status', 'unknown')}; {hiring.get('observed_job_count', 0)} company-matched public listings.", f"- Coverage: partial. No listings does not establish that the company is not hiring.", f"- Stop reason: {hiring.get('stop_reason', '')}"]
        for job in (hiring.get("jobs") or [])[:50]:
            body.append("- " + clean(str(job.get("title", ""))) + " — " + clean(str(job.get("location", ""))) + " — " + clean(str(job.get("url", ""))))
        new = upsert_section(new, "LinkedIn public check", "\n".join(body))
    if new != text and not dry_run:
        atomic_text(path, new)
    return {"path": str(path), "slug": path.stem, "status": ("would_write" if dry_run else "wrote") if new != text else "unchanged", "contacts_n": len(contacts), "linkedin_company": company or None, "owner": owner or None}


def merge_public_profile(vault: Path, row: dict, *, dry_run=False) -> bool:
    """Lossless contact merge; current unknown hiring never masquerades as fresh yes."""
    note = resolve_note(vault, row)
    if note is None:
        raise ValueError("result does not identify a note inside Businesses")
    directory = vault / "Research" / "firms"
    path = directory / f"{note.stem}.json"
    if path.is_symlink() or not path.resolve().is_relative_to(vault.resolve()):
        raise ValueError("unsafe profile path")
    profile = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    before = json.dumps(profile, sort_keys=True)
    note_data = note_row(note, note.read_text(encoding="utf-8"))
    for key, value in (("slug", note.stem), ("name", note_data["firm"]), ("website", note_data["website"])):
        profile.setdefault(key, value)
    li = dict(profile.get("linkedin") or {})
    company = linkedin_url(row.get("linkedin_company") or "")
    if company:
        li["company_url"] = company
    li.update(status=row.get("status", "unknown"), people=row.get("people", {}), checked_at=row.get("checked_at"), checks=row.get("checks", []), transport="public_html")
    if row.get("company_id"):
        li["company_id"] = row["company_id"]
    profile["linkedin"] = li
    existing = [dict(c) for c in profile.get("contacts", []) if isinstance(c, dict)]
    for raw in (row.get("contacts") or []):
        c = _normalize_contact(raw)
        if not c.get("name") or c["name"].lower() in MASKED:
            continue
        entry = {**raw, **c, "source": "linkedin", "kind": "person"}
        prior = next((p for p in existing if c["profile_url"] and p.get("profile_url") == c["profile_url"]), None)
        if prior is not None:
            prior.update({k: v for k, v in entry.items() if v not in (None, "")})
        else:
            existing.append(entry)
    for c in existing:
        if c.get("source") == "linkedin" and c.get("name"):
            c["kind"] = "person"
            c["poc_score"] = 5 + (4 if OWNER_TITLE_RE.search(c.get("title") or "") else 0) + (3 if c.get("email") and not c["email"].lower().startswith(("info@", "contact@", "office@", "hello@", "admin@", "team@")) else 0)
    profile["contacts"] = existing
    candidates = [c for c in existing if c.get("kind") == "person" and c.get("name") and c["name"].lower() not in MASKED]
    if candidates:
        best = max(candidates, key=lambda c: float(c.get("poc_score") or 0))
        previous = profile.get("best_poc") or {}
        if previous.get("kind") != "person" or float(best.get("poc_score") or 0) >= float(previous.get("poc_score") or 0):
            profile["best_poc"] = dict(best)
    if row.get("hiring"):
        prior = profile.get("hiring") or {}
        if row["hiring"].get("status") == "unknown" and prior.get("status") in {"hiring", "no_public_jobs_found"}:
            profile["linkedin_last_successful_hiring"] = prior
        profile["hiring"] = row["hiring"]
    changed = before != json.dumps(profile, sort_keys=True)
    if changed and not dry_run:
        atomic_text(path, json.dumps(profile, ensure_ascii=False, indent=2) + "\n")
    return changed


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                    validate_row(row)
                    yield number, row, None
                except (ValueError, TypeError) as error:
                    yield number, None, str(error)


def apply_results(vault: Path, results_path: Path, dry_run=False) -> dict:
    stats = {"applied": 0, "skipped": 0, "missing_note": 0, "errors": 0, "changed": 0}
    if not results_path.is_file():
        stats["errors"] = 1
        print(f"FAIL\tapply\tmissing {results_path}", file=sys.stderr)
        return stats
    for number, row, error in read_jsonl(results_path):
        try:
            if error:
                raise ValueError(error)
            note = resolve_note(vault, row)
            if note is None:
                stats["missing_note"] += 1
                raise ValueError("note missing or outside vault")
            # Validate/read the existing profile before mutating the note.
            profile_changed = merge_public_profile(vault, row, dry_run=dry_run) if row.get("source") == "linkedin_public" else False
            result = apply_result_to_note(note, row, dry_run)
            stats["applied"] += 1
            stats["changed"] += int(profile_changed or result["status"] in {"wrote", "would_write"})
            print(json.dumps(result, ensure_ascii=False))
        except (ValueError, TypeError, OSError) as error:
            stats["errors"] += 1
            print(f"FAIL\tline {number}\t{error}", file=sys.stderr)
    print("apply\t" + json.dumps(stats))
    return stats


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    for flag in ("category", "practice", "slug"):
        ap.add_argument("--" + flag)
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="queue-only: include existing contacts; fetch always refreshes hiring")
    ap.add_argument("--dry-run", action="store_true", help="no network and no writes")
    ap.add_argument("--no-push", action="store_true")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--apply", metavar="JSONL")
    group.add_argument("--queue", metavar="JSONL", help="fetch exactly these queued vault notes")
    ap.add_argument("--queue-only", action="store_true", help="legacy queue export without fetching")
    ap.add_argument("--output", help="results JSONL; default Sources/runs/linkedin-results-DATE.jsonl")
    ap.add_argument("--company-url", help="explicit company URL, requires --slug")
    ap.add_argument("--jobs-only", action="store_true")
    ap.add_argument("--max-jobs", type=int, default=50)
    ap.add_argument("--job-pages", type=int, default=3)
    ap.add_argument("--delay", type=float, default=2)
    args = ap.parse_args(argv)
    if args.limit < 0 or not 1 <= args.max_jobs <= 500 or not 1 <= args.job_pages <= 20 or not 1 <= args.delay <= 60:
        ap.error("limit >= 0; max-jobs 1..500; job-pages 1..20; delay 1..60")
    if args.company_url and (not args.slug or not linkedin_url(args.company_url)):
        ap.error("--company-url requires --slug and a valid public LinkedIn company URL")
    if args.queue_only and args.apply:
        ap.error("--queue-only cannot be combined with --apply")
    vault = Path(args.vault).expanduser().resolve()
    if not (vault / "Businesses").is_dir():
        ap.error("vault Businesses directory does not exist; run doctor --init first")
    if args.apply:
        stats = apply_results(vault, Path(args.apply), args.dry_run)
    else:
        if args.queue:
            rows = []
            for number, row, error in read_jsonl(Path(args.queue)):
                if error or resolve_note(vault, row) is None:
                    ap.error(f"queue line {number}: {error or 'note missing or outside vault'}")
                note = resolve_note(vault, row)
                # Read source-of-truth note fields, not arbitrary URLs in a queue.
                candidates = list_candidates(vault, slug=note.stem, force=True)
                if not candidates:
                    ap.error(f"queue line {number}: note not eligible")
                rows.append(candidates[0])
            if args.limit:
                rows = rows[:args.limit]
        else:
            rows = list_candidates(vault, category=args.category, practice=args.practice, limit=args.limit, slug=args.slug, force=(args.force or not args.queue_only))
        if args.company_url:
            rows = [row for row in rows if row["slug"] == args.slug.removesuffix(".md")]
            for row in rows:
                row["linkedin_company"] = linkedin_url(args.company_url)
        if args.slug and not rows:
            ap.error("no exact matching note found")
        if args.dry_run or args.queue_only:
            write_queue(vault, rows, args.dry_run)
            print("push-backup\tskip\t" + ("dry-run" if args.dry_run else "queue-only"))
            return 0
        write_queue(vault, rows)
        results = Path(args.output) if args.output else vault / "Sources" / "runs" / f"linkedin-results-{vault_day()}.jsonl"
        client = PublicHTTP(delay=args.delay)
        outputs = [fetch_company(row, client, max_jobs=args.max_jobs, max_pages=args.job_pages, jobs_only=args.jobs_only) for row in rows]
        atomic_text(results, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in outputs))
        print(f"results\t{results}\trows={len(outputs)}")
        stats = apply_results(vault, results)
    if not args.dry_run and not args.no_push:
        from push_backup import maybe_after_mutation
        maybe_after_mutation(mutated=stats["changed"] > 0, dry_run=False, no_push=False, vault=vault)
    return 2 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(run())

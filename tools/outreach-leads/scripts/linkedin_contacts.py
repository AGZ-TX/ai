#!/usr/bin/env python3
"""LinkedIn contacts enrich layer: queue vault notes for browser lookup; apply structured results.

Does NOT drive a browser. Coordinator/computerUse fills linkedin-results JSONL; this script
writes ## Contacts + optional linkedin_company:/owner: onto notes. Never writes linkedin.com
into website:.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from note_io import (
    DEFAULT_VAULT,
    category_has,
    fm_get,
    iter_notes,
    practice_is_pi,
    set_fm,
    website_value,
)

OWNER_TITLE_RE = re.compile(
    r"\b(owner|founder|co-?founder|ceo|president|managing\s+partner|principal)\b",
    re.I,
)
CONTACTS_HEADING = re.compile(r"^##\s+Contacts\s*$", re.M)


def vault_day() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")


def has_contacts_section(text: str) -> bool:
    """True when ## Contacts exists and has at least one non-empty bullet (not a bare stub)."""
    m = CONTACTS_HEADING.search(text)
    if not m:
        return False
    rest = text[m.end() :]
    next_h = re.search(r"^##\s+", rest, re.M)
    body = rest[: next_h.start()] if next_h else rest
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("-") and len(s) > 1:
            return True
    return False


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


def note_row(path: Path, text: str) -> dict:
    city = re.sub(r"[\[\]\"]", "", fm_get(text, "city")).strip()
    return {
        "slug": path.stem,
        "path": str(path),
        "firm": fm_get(text, "name") or path.stem,
        "website": website_value(text),
        "city": city,
        "phone": fm_get(text, "phone"),
    }


def list_candidates(
    vault: Path,
    *,
    category: str | None,
    practice: str | None,
    limit: int,
    slug: str | None,
    force: bool,
) -> list[dict]:
    rows: list[dict] = []
    for p in iter_notes(vault):
        text = p.read_text(encoding="utf-8", errors="replace")
        if not matches_filters(text, category, practice, slug, p):
            continue
        if not force and has_contacts_section(text):
            continue
        rows.append(note_row(p, text))
        if limit and len(rows) >= limit:
            break
    return rows


def write_queue(vault: Path, rows: list[dict], dry_run: bool = False) -> Path | None:
    day = vault_day()
    runs = vault / "Sources" / "runs"
    out = runs / f"linkedin-queue-{day}.jsonl"
    if dry_run:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        print(f"dry-run queue rows: {len(rows)} (would write {out})")
        return None
    runs.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(json.dumps(r, ensure_ascii=False))
    print(f"queue\t{out}\trows={len(rows)}")
    return out


def _normalize_contact(c: dict) -> dict:
    """Normalize contact dict to {name, title, email, location, photo_url, profile_url}.

    Aliases: pfp → photo_url; url → profile_url. Never invent emails.
    """
    if not isinstance(c, dict):
        return {}
    photo = (c.get("photo_url") or c.get("pfp") or "").strip()
    profile = (c.get("profile_url") or c.get("url") or "").strip()
    return {
        "name": (c.get("name") or "").strip(),
        "title": (c.get("title") or "").strip(),
        "email": (c.get("email") or "").strip(),
        "location": (c.get("location") or "").strip(),
        "photo_url": photo,
        "profile_url": profile,
    }


def _format_contact_bullet(c: dict) -> str:
    """Bullet: - Name — Title — location — email — photo_url — profile_url

    Omit empty fields (incl. trailing) cleanly — no dangling em-dashes.
    """
    n = _normalize_contact(c)
    name = n["name"] or "?"
    # Fixed order; skip empties so bullets stay readable
    parts = [name]
    for key in ("title", "location", "email", "photo_url", "profile_url"):
        val = n[key]
        if val:
            parts.append(val)
    return "- " + " — ".join(parts)


def _pick_owner(contacts: list[dict], explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    for c in contacts:
        title = (c.get("title") or "").strip()
        name = (c.get("name") or "").strip()
        if name and title and OWNER_TITLE_RE.search(title):
            return name
    return ""


def upsert_contacts_section(text: str, bullets: list[str]) -> str:
    block = "## Contacts\n" + "\n".join(bullets) + "\n"
    if CONTACTS_HEADING.search(text):
        # Replace existing section through next ## or EOF
        return re.sub(
            r"^##\s+Contacts\s*\n(?:.*?)(?=^##\s|\Z)",
            block + "\n",
            text,
            count=1,
            flags=re.M | re.S,
        )
    # Insert before ## Call log if present, else before ## Research, else append
    for marker in ("## Call log", "## Research", "## Angle"):
        if marker in text:
            # Prefer: after Research / before Call log
            if marker == "## Call log":
                return text.replace(marker, block + "\n" + marker, 1)
            if marker == "## Research":
                # place Contacts after Research section start? Prefer before Call log already handled.
                # Insert after Research heading block end is messy; put before Call log first.
                continue
            if marker == "## Angle":
                # put Contacts near end — after Angle is wrong; skip
                continue
    if "## Research" in text:
        # append Contacts after Research section: before next ## or EOF
        m = re.search(r"(^##\s+Research\s*\n(?:.*?)(?=^##\s|\Z))", text, re.M | re.S)
        if m:
            end = m.end(1)
            return text[:end].rstrip() + "\n\n" + block + "\n" + text[end:].lstrip("\n")
    return text.rstrip() + "\n\n" + block + "\n"


def resolve_note(vault: Path, row: dict) -> Path | None:
    path_s = (row.get("path") or "").strip()
    if path_s:
        p = Path(path_s)
        if p.is_file():
            return p
    slug = (row.get("slug") or "").strip().removesuffix(".md")
    if slug:
        cand = vault / "Businesses" / f"{slug}.md"
        if cand.is_file():
            return cand
        # fuzzy stem match
        for p in iter_notes(vault):
            if p.stem == slug:
                return p
    return None


def apply_result_to_note(path: Path, row: dict, dry_run: bool = False) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    contacts = row.get("contacts") or []
    if not isinstance(contacts, list):
        contacts = []
    contacts = [_normalize_contact(c) for c in contacts if isinstance(c, dict)]
    bullets = [_format_contact_bullet(c) for c in contacts]
    if not bullets and not row.get("linkedin_company") and not row.get("owner"):
        return {"path": str(path), "status": "skip_empty", "dry_run": dry_run}

    planned = {
        "path": str(path),
        "slug": path.stem,
        "contacts_n": len(bullets),
        "linkedin_company": (row.get("linkedin_company") or "").strip() or None,
        "owner": None,
        "dry_run": dry_run,
    }

    new_text = text
    li_co = (row.get("linkedin_company") or "").strip()
    if li_co:
        # never touch website:
        new_text = set_fm(new_text, "linkedin_company", li_co)

    owner = _pick_owner(contacts, row.get("owner"))
    if owner:
        existing = fm_get(new_text, "owner")
        if not existing or row.get("owner"):
            new_text = set_fm(new_text, "owner", owner)
            planned["owner"] = owner
        else:
            planned["owner"] = existing

    if bullets:
        new_text = upsert_contacts_section(new_text, bullets)

    stamp = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z")
    research_line = f"- {stamp}: contacts via enrich --layer contacts (LinkedIn lookup). linkedin.com not written to website."
    if "## Research" in new_text:
        if research_line not in new_text:
            new_text = new_text.replace("## Research", f"## Research\n{research_line}", 1)
    else:
        new_text = new_text.rstrip() + f"\n\n## Research\n{research_line}\n"

    planned["status"] = "would_write" if dry_run else "wrote"
    planned["bullets"] = bullets
    if dry_run:
        print(json.dumps(planned, ensure_ascii=False))
        return planned

    path.write_text(new_text, encoding="utf-8")
    print(json.dumps({k: planned[k] for k in ("path", "slug", "status", "contacts_n", "linkedin_company", "owner")}, ensure_ascii=False))
    return planned


def apply_results(vault: Path, results_path: Path, dry_run: bool = False) -> dict:
    if not results_path.is_file():
        print(f"FAIL\tapply\tmissing {results_path}", file=sys.stderr)
        return {"applied": 0, "skipped": 0, "missing_note": 0, "errors": 1}

    applied = skipped = missing = 0
    for line_no, line in enumerate(results_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"FAIL\tline {line_no}\t{e}", file=sys.stderr)
            continue
        note = resolve_note(vault, row)
        if not note:
            missing += 1
            print(f"missing_note\t{row.get('slug') or row.get('path')}")
            continue
        out = apply_result_to_note(note, row, dry_run=dry_run)
        if out.get("status") == "skip_empty":
            skipped += 1
        else:
            applied += 1
    stats = {"applied": applied, "skipped": skipped, "missing_note": missing, "errors": 0}
    print(f"apply\t{'dry-run' if dry_run else 'write'}\tapplied={applied}\tskipped={skipped}\tmissing_note={missing}")
    return stats


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="linkedin_contacts")
    ap.add_argument("--category", default=None)
    ap.add_argument("--practice", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--force", action="store_true", help="include notes that already have ## Contacts")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true", help="skip immediate GitHub backup (debug/tests only)")
    ap.add_argument("--apply", default=None, metavar="PATH", help="apply linkedin-results JSONL onto notes")
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    if args.apply:
        stats = apply_results(vault, Path(args.apply), dry_run=args.dry_run)
        try:
            from push_backup import maybe_after_mutation

            maybe_after_mutation(
                mutated=(not args.dry_run) and stats.get("applied", 0) > 0,
                dry_run=args.dry_run,
                no_push=args.no_push,
                vault=vault,
            )
        except Exception as e:
            print(f"push-backup_error\t{e}", file=sys.stderr)
        return 0

    rows = list_candidates(
        vault,
        category=args.category,
        practice=args.practice,
        limit=args.limit,
        slug=args.slug,
        force=args.force,
    )
    write_queue(vault, rows, dry_run=args.dry_run)
    # queue JSONL is vault Sources write; still not a Businesses mutation — skip auto-push
    if args.dry_run:
        print("push-backup\tskip\tdry-run")
    else:
        print("push-backup\tskip\tqueue-only")
    return 0


if __name__ == "__main__":
    sys.exit(run())

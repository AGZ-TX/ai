#!/usr/bin/env python3
"""Enrich vault notes. Layers: website (default), contacts (LinkedIn), emails (sitemap)."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from note_io import (
    DEFAULT_VAULT,
    category_has,
    fm_get,
    host_blocked,
    iter_notes,
    practice_is_pi,
    set_fm,
    website_value,
)

SCRIPT_DIR = Path(__file__).resolve().parent

LAYER_ALIASES = {
    "website": "website",
    "contacts": "contacts",
    "linkedin": "contacts",
    "linkedin-contacts": "contacts",
    "emails": "emails",
    "email": "emails",
    "sitemap-emails": "emails",
    "website-emails": "emails",
}


def extract_candidate_urls(text: str) -> list[str]:
    urls = []
    for m in re.finditer(r"candidate_website:\s*(https?://\S+)", text, re.I):
        urls.append(m.group(1).rstrip(").,;"))
    for m in re.finditer(r"(https?://[^\s<>\"']+)", text):
        u = m.group(1).rstrip(").,;")
        if host_blocked(u):
            continue
        if any(x in u.lower() for x in ("justia", "findlaw", "schema.org")):
            continue
        urls.append(u)
    # dedupe preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def verify(url: str, firm: str, city_hint: str = "") -> tuple[bool, str]:
    cmd = [sys.executable, str(SCRIPT_DIR / "verify_website.py"), url, "--firm", firm]
    if city_hint:
        cmd.extend(["--city-hint", city_hint])
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip().startswith("OK"):
        # OK\tcanon\t...
        parts = p.stdout.strip().split("\t")
        canon = parts[1] if len(parts) > 1 else url
        return True, canon
    err = (p.stderr or p.stdout or "reject").strip().splitlines()[-1] if (p.stderr or p.stdout) else "reject"
    return False, err


def layer_website(vault: Path, category: str, practice: str | None, limit: int) -> dict:
    filled = 0
    skipped = 0
    failed = 0
    no_candidate = 0
    samples = []
    rejects = []
    for p in iter_notes(vault):
        if limit and filled >= limit:
            break
        text = p.read_text(encoding="utf-8", errors="replace")
        if not category_has(text, category):
            continue
        if practice:
            if practice.lower() in {"pi", "personal-injury", "trial"}:
                if not practice_is_pi(text):
                    continue
            else:
                if practice.lower() not in fm_get(text, "practice").lower():
                    continue
        if website_value(text):
            skipped += 1
            continue
        firm = fm_get(text, "name")
        city = re.sub(r"[\[\]\"]", "", fm_get(text, "city")).strip()
        cands = extract_candidate_urls(text)
        # also scan Sources/runs jsonl for this firm
        if not cands:
            runs = vault / "Sources" / "runs"
            if runs.is_dir():
                import json

                for jl in sorted(runs.glob("specialty-*.jsonl"))[-5:]:
                    for line in jl.read_text(encoding="utf-8", errors="replace").splitlines():
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        if row.get("firm", "").lower() == firm.lower() and row.get("candidate_website"):
                            cands.append(row["candidate_website"])
        if not cands:
            no_candidate += 1
            continue
        ok = False
        last_err = ""
        for url in cands:
            if host_blocked(url):
                last_err = f"directory_host:{url}"
                continue
            good, detail = verify(url, firm, city)
            if good:
                new_text = set_fm(text, "website", detail.rstrip("/"))
                # Prefer canon with trailing slash stripped for consistency with existing notes
                from urllib.parse import urlparse

                pu = urlparse(detail if "://" in detail else "https://" + detail)
                canon = f"{pu.scheme}://{pu.netloc}"
                new_text = set_fm(text, "website", canon)
                # append research line
                stamp = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M %Z")
                if "## Research" in new_text:
                    new_text = new_text.replace(
                        "## Research",
                        f"## Research\n- {stamp}: website {canon} via enrich --layer website + verify_website. No Google Places.",
                        1,
                    )
                p.write_text(new_text, encoding="utf-8")
                filled += 1
                samples.append(f"{p.stem} → {canon}")
                ok = True
                break
            last_err = detail
        if not ok:
            failed += 1
            rejects.append(f"{p.stem}: {last_err}")
    return {
        "filled": filled,
        "skipped_have_site": skipped,
        "failed": failed,
        "no_candidate": no_candidate,
        "samples": samples,
        "rejects": rejects,
    }


def write_prove(vault: Path, layer: str, category: str, stats: dict) -> Path:
    day = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    path = vault / "Sources" / f"enrich-{layer}-{category.lower()}-{day}.md"
    body = f"""---
type: source
name: "enrich-{layer}-{category.lower()}-{day}"
---

# Prove log — enrich/{layer} — {day}

## Goal
Enrich {category} notes layer={layer}

## Mode
enrich

## Counts
| Metric | Value |
|---|---|
| filled | {stats.get('filled', 0)} |
| skipped (already had website) | {stats.get('skipped_have_site', 0)} |
| failed verify | {stats.get('failed', 0)} |
| no candidate URL | {stats.get('no_candidate', 0)} |
| queue rows | {stats.get('queue_rows', 0)} |
| applied | {stats.get('applied', 0)} |

## Sources / recipes used
- verify_website.py (website layer)
- linkedin_contacts.py (contacts layer; browser is external)
- website_emails.py (emails layer; sitemap crawl)
- UA: OutreachTools/1.0

## Sample
{chr(10).join(str(i+1)+'. '+s for i,s in enumerate(stats.get('samples', [])[:15]))}

## Rejects / skips
{chr(10).join('- '+r for r in stats.get('rejects', [])[:40])}

## Blind-spot check
- Directory hosts written: 0 (blocked by verify_website)
- linkedin.com never written to website:
- Layer: {layer}

## Artifacts
- `{path}`
"""
    path.write_text(body, encoding="utf-8")
    return path



def layer_emails_argv(args) -> list[str]:
    """Build argv for website_emails.run from enrich argparse namespace."""
    argv: list[str] = []
    if args.category:
        argv.extend(["--category", args.category])
    if args.practice:
        argv.extend(["--practice", args.practice])
    if args.limit:
        argv.extend(["--limit", str(args.limit)])
    if getattr(args, "slug", None):
        argv.extend(["--slug", args.slug])
    if getattr(args, "max_pages", None) is not None:
        argv.extend(["--max-pages", str(args.max_pages)])
    if args.vault:
        argv.extend(["--vault", args.vault])
    if args.dry_run:
        argv.append("--dry-run")
    if getattr(args, "no_push", False):
        argv.append("--no-push")
    return argv


def layer_contacts_argv(args) -> list[str]:
    """Build argv for linkedin_contacts.run from enrich argparse namespace."""
    argv: list[str] = []
    if args.category:
        argv.extend(["--category", args.category])
    if args.practice:
        argv.extend(["--practice", args.practice])
    if args.limit:
        argv.extend(["--limit", str(args.limit)])
    if getattr(args, "slug", None):
        argv.extend(["--slug", args.slug])
    if getattr(args, "force", False):
        argv.append("--force")
    if args.vault:
        argv.extend(["--vault", args.vault])
    if args.dry_run:
        argv.append("--dry-run")
    if getattr(args, "no_push", False):
        argv.append("--no-push")
    if getattr(args, "apply", None):
        argv.extend(["--apply", args.apply])
    return argv


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="enrich")
    ap.add_argument("--category", default="Law")
    ap.add_argument("--practice", default=None)
    ap.add_argument(
        "--layer",
        default="website",
        help="website (default) | contacts (alias: linkedin) | emails (alias: sitemap-emails)",
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slug", default=None, help="contacts/emails layer: single note stem")
    ap.add_argument("--force", action="store_true", help="contacts: re-queue notes that already have ## Contacts")
    ap.add_argument("--max-pages", type=int, default=40, help="emails layer: max pages to fetch per site")
    ap.add_argument(
        "--apply",
        default=None,
        metavar="PATH",
        help="contacts layer: apply linkedin-results JSONL onto notes",
    )
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--dry-run", action="store_true", help="list/plan only; no writes")
    ap.add_argument("--no-push", action="store_true", help="skip immediate GitHub backup (debug/tests only)")
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    layer = LAYER_ALIASES.get((args.layer or "").lower())
    if not layer:
        print(
            f"layer {args.layer!r} unknown; use --layer website|contacts|emails (aliases: linkedin, sitemap-emails)",
            file=sys.stderr,
        )
        return 1

    if layer == "contacts":
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import linkedin_contacts

        rc = linkedin_contacts.run(layer_contacts_argv(args))
        # lightweight prove for non-dry queue/apply
        if not args.dry_run:
            stats = {
                "filled": 0,
                "skipped_have_site": 0,
                "failed": 0,
                "no_candidate": 0,
                "queue_rows": 0,
                "applied": 0,
                "samples": [],
                "rejects": [],
            }
            day = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
            if args.apply:
                stats["applied"] = 1
                stats["samples"] = [f"apply {args.apply}"]
            else:
                q = vault / "Sources" / "runs" / f"linkedin-queue-{day}.jsonl"
                if q.is_file():
                    n = sum(1 for line in q.read_text(encoding="utf-8").splitlines() if line.strip())
                    stats["queue_rows"] = n
                    stats["samples"] = [f"queue {q}"]
            prove = write_prove(vault, "contacts", args.category or "all", stats)
            print(f"prove\t{prove}")
        return int(rc or 0)

    if layer == "emails":
        if args.apply:
            print("--apply is only valid with --layer contacts", file=sys.stderr)
            return 2
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import website_emails

        rc = website_emails.run(layer_emails_argv(args))
        return int(rc or 0)

    # --- website layer ---
    if args.apply:
        print("--apply is only valid with --layer contacts", file=sys.stderr)
        return 2

    if args.dry_run:
        n = 0
        for p in iter_notes(vault):
            text = p.read_text(encoding="utf-8", errors="replace")
            if not category_has(text, args.category):
                continue
            if args.practice and args.practice.lower() in {"pi", "personal-injury", "trial"}:
                if not practice_is_pi(text):
                    continue
            if website_value(text):
                continue
            cands = extract_candidate_urls(text)
            print(f"{p.stem}\tcandidates={len(cands)}\t{cands[:2]}")
            n += 1
            if args.limit and n >= args.limit:
                break
        print(f"dry-run missing-website notes: {n}")
        print("push-backup\tskip\tdry-run")
        return 0

    stats = layer_website(vault, args.category, args.practice, args.limit)
    prove = write_prove(vault, args.layer, args.category, stats)
    print(f"filled\t{stats['filled']}")
    print(f"skipped\t{stats['skipped_have_site']}")
    print(f"failed\t{stats['failed']}")
    print(f"no_candidate\t{stats['no_candidate']}")
    print(f"prove\t{prove}")
    try:
        from push_backup import maybe_after_mutation

        maybe_after_mutation(
            mutated=stats.get("filled", 0) > 0,
            dry_run=False,
            no_push=args.no_push,
            vault=vault,
        )
    except Exception as e:
        print(f"push-backup_error\t{e}", file=sys.stderr)
    try:
        from coverage_check import evaluate

        rc = evaluate(args.category, args.practice, vault)
        print(f"coverage-check_exit\t{rc}")
    except Exception as e:
        print(f"coverage-check_error\t{e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(run())

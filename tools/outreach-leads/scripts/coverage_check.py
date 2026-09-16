#!/usr/bin/env python3
"""Coverage-check a vault category against references/coverage.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from note_io import (
    DEFAULT_VAULT,
    category_has,
    fm_get,
    iter_notes,
    practice_is_pi,
    website_value,
)

PACK_COV = Path(__file__).resolve().parents[1] / "references" / "coverage.json"


def load_coverage(path: Path | None = None) -> dict:
    p = path or PACK_COV
    if not p.exists():
        raise SystemExit(f"missing coverage.json: {p}")
    return json.loads(p.read_text())


def find_row(cov: dict, category: str) -> dict | None:
    for row in cov.get("rows", []):
        if row.get("category", "").lower() == category.lower():
            return row
    return None


def score_category(vault: Path, category: str, practice: str | None = None) -> dict:
    notes = 0
    with_phone = 0
    with_site = 0
    with_email = 0
    practice_notes = 0
    practice_site = 0
    practice_phone = 0
    for p in iter_notes(vault):
        text = p.read_text(encoding="utf-8", errors="replace")
        if not category_has(text, category):
            continue
        notes += 1
        if fm_get(text, "phone"):
            with_phone += 1
        if website_value(text):
            with_site += 1
        if fm_get(text, "email"):
            with_email += 1
        in_prac = False
        if practice:
            prac = fm_get(text, "practice").lower()
            aliases = {"pi", "personal-injury", "trial"}
            if practice.lower() in aliases:
                in_prac = practice_is_pi(text)
            else:
                in_prac = practice.lower() in prac or prac == practice.lower()
        if in_prac:
            practice_notes += 1
            if website_value(text):
                practice_site += 1
            if fm_get(text, "phone"):
                practice_phone += 1
    site_fill = (with_site / notes) if notes else 0.0
    prac_fill = (practice_site / practice_notes) if practice_notes else 0.0
    return {
        "notes": notes,
        "with_phone": with_phone,
        "with_website": with_site,
        "with_email": with_email,
        "site_fill": site_fill,
        "practice_notes": practice_notes,
        "practice_with_website": practice_site,
        "practice_with_phone": practice_phone,
        "practice_site_fill": prac_fill,
    }


def print_scorecard(category: str, practice: str | None, stats: dict, row: dict | None, verdict: str, next_mode: str):
    print(f"## coverage-check scorecard — {category}" + (f" / {practice}" if practice else ""))
    print()
    print("| Metric | Value | Threshold |")
    print("|---|---|---|")
    min_notes = (row or {}).get("min_notes", "—")
    min_fill = (row or {}).get("min_site_fill", "—")
    print(f"| notes | {stats['notes']} | {min_notes} |")
    print(f"| with phone | {stats['with_phone']} | — |")
    print(f"| with website | {stats['with_website']} | — |")
    print(f"| site fill % | {100*stats['site_fill']:.1f}% | {100*float(min_fill):.0f}% |" if isinstance(min_fill, (int, float)) else f"| site fill % | {100*stats['site_fill']:.1f}% | {min_fill} |")
    print(f"| with email | {stats['with_email']} | — |")
    if practice:
        smin = (row or {}).get("specialty_min_notes", min_notes)
        sfill = (row or {}).get("specialty_min_site_fill", min_fill)
        print(f"| practice notes ({practice}) | {stats['practice_notes']} | {smin} |")
        print(f"| practice with website | {stats['practice_with_website']} | — |")
        print(f"| practice site fill % | {100*stats['practice_site_fill']:.1f}% | {100*float(sfill):.0f}% |" if isinstance(sfill, (int, float)) else f"| practice site fill % | {100*stats['practice_site_fill']:.1f}% | {sfill} |")
    print(f"| specialty_required | {(row or {}).get('specialty_required', False)} | — |")
    print(f"| matrix status | {(row or {}).get('status', '?')} | — |")
    print()
    print(f"VERDICT: {verdict}")
    if next_mode:
        print(f"NEXT: {next_mode}")


def evaluate(category: str, practice: str | None, vault: Path, cov_path: Path | None = None) -> int:
    cov = load_coverage(cov_path)
    row = find_row(cov, category)
    stats = score_category(vault, category, practice)
    specialty_required = bool((row or {}).get("specialty_required"))
    min_notes = int((row or {}).get("min_notes") or 0)
    min_fill = float((row or {}).get("min_site_fill") or 0)
    next_mode = ""
    covered = True
    reasons = []

    check_notes = stats["notes"]
    check_fill = stats["site_fill"]
    thresh_notes = min_notes
    thresh_fill = min_fill
    if practice and row:
        check_notes = stats["practice_notes"]
        check_fill = stats["practice_site_fill"]
        thresh_notes = int(row.get("specialty_min_notes") or min_notes)
        thresh_fill = float(row.get("specialty_min_site_fill") or min_fill)

    if row is None:
        covered = False
        reasons.append("no matrix row")
        next_mode = "coverage-check (add coverage.json row)"
    else:
        if check_notes < thresh_notes:
            covered = False
            reasons.append(f"notes {check_notes} < {thresh_notes}")
            if specialty_required:
                next_mode = row.get("specialty_mode") or "specialty-directory"
            else:
                next_mode = "bulk-dump"
        if check_fill < thresh_fill:
            covered = False
            reasons.append(f"site_fill {check_fill:.2f} < {thresh_fill:.2f}")
            next_mode = next_mode or "enrich"

    if covered:
        verdict = "COVERED"
        next_mode = ""
        print_scorecard(category, practice, stats, row, verdict, next_mode)
        return 0

    verdict = "NOT COVERED — " + "; ".join(reasons)
    if specialty_required and not next_mode:
        next_mode = row.get("specialty_mode") or "specialty-directory"
    print_scorecard(category, practice, stats, row, verdict, next_mode)
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="coverage-check")
    ap.add_argument("--category", required=True)
    ap.add_argument("--practice", default=None, help="e.g. pi / personal-injury / trial")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--coverage", default=str(PACK_COV))
    args = ap.parse_args(argv)
    return evaluate(args.category, args.practice, Path(args.vault), Path(args.coverage))


if __name__ == "__main__":
    sys.exit(main())

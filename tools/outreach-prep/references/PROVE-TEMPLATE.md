---
type: source
name: "PROVE TEMPLATE — outreach-prep"
---

# Prove log — outreach-prep — <category> — YYYY-MM-DD

## Goal
Prep one vertical week: cover → crawl → LinkedIn people → POC → shortlist (calls gated)

## Mode
outreach-prep | outreach-prep (dry-run)

## Counts
| Metric | Value |
|---|---|
| category / practice |  |
| coverage verdict | COVERED / NOT COVERED |
| specialty ran | Y/N |
| queue (with website) |  |
| profiles written |  |
| linkedin queue rows |  |
| linkedin results applied |  |
| linkedin blocked / names_masked |  |
| named best_poc |  |
| inbox_fallback only |  |
| shortlist count |  |

## Sources / recipes used
- `scripts/run_outreach_prep.py`
- outreach-leads: coverage-check, specialty-directory, enrich contacts/emails, build_poc, push-backup
- website-search: `research_website.py`
- LinkedIn: signed-in session; website-to-api recipe if present
- UA: OutreachTools/1.0

## Sample firms (5–10)
1.

## Rejects / skips / errors
-

## Blind-spot check
- Calls operator-gated? must be Y
- Named best_poc preferred over info@? must be Y
- LinkedIn attempted (or `--skip-linkedin`)? must be Y
- Blocked LI failed the whole week? must be N
- Directory host in `website:`? must be N
- Invented emails/people? must be N

## Artifacts
- `Research/firms/<slug>.json`
- `Research/firms/shortlists/<category-slug>-YYYY-MM-DD.json`
- `Sources/runs/outreach-queue-…jsonl`
- `Sources/runs/linkedin-queue-…jsonl` / `linkedin-results-…jsonl`
- `Sources/outreach-prep-<category-slug>-YYYY-MM-DD.md`

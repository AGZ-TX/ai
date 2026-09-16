---
type: source
name: "website-search prove template"
---

# Prove log. website-search. YYYY-MM-DD

## Goal
Multi-page site JSON via sitemap crawl, not a single-page WebFetch. Markdown is optional.

## Mode
website-search CLI

## Counts
| Metric | Value |
|---|---|
| start URL |  |
| pages discovered |  |
| pages fetched |  |
| outcome ok |  |
| outcome timeout |  |
| outcome http_error |  |
| outcome error |  |
| emails (same-domain) |  |
| llms.txt present | Y/N |
| schema @types distinct |  |
| ok pages with full_text |  |

## Sources and recipes used
- `scripts/research_website.py`
- UA: OutreachTools/1.0
- Same-domain email extract. Same rules as outreach ingest.

## Sample pages (5 to 10)
1.

## Rejects / skips / errors
-

## Blind-spot check
- Stopped at WebFetch of the landing page? must be N
- Cross-domain pages fetched? must be N
- Invented emails or rankings? must be N
- Sitemap over 500 URLs without `--all`. Priority filter applied?
- First timeout abandoned the rest of the URL list? must be N
- Failed pages missing `status`, `http_code`, or `attempts`? must be N
- Ok HTML page missing `full_text` without `--no-full-text`? must be N
- Default run wrote markdown? must be N unless `--out` or `--markdown`

## Artifacts
- `Sources/runs/research-<slug>-YYYY-MM-DD.json` (required)
- `Research/site-<slug>-YYYY-MM-DD.md` (only if requested)

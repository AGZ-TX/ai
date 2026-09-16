# prep (run_outreach_prep)

One-category outreach prep week. Driver orchestrates existing tools; does not invent CRM facts.

## Sub-features

- `cover` — coverage-check; specialty-directory when NOT COVERED (unless `--skip-specialty`)
- `queue` — notes with official websites → outreach-queue JSONL
- `crawl` — website-search → `Research/firms/<slug>.json`
- `linkedin` — enrich `--layer contacts` queue → coordinator results → apply + merge `source=linkedin` (default ON; `--skip-linkedin` opt-out)
- `emails` — enrich `--layer emails` when profiles lack addresses
- `poc` — build_poc / re-rank named best_poc over inbox_fallback
- `shortlist` — `Research/firms/shortlists/<cat>-YYYY-MM-DD.json`
- `push` — push-backup after real writes

## How to get to it (user POV)

Operator: "outreach-prep" / "prep trucking week" / "setup HVAC outreach" / "PI week" → resolve alias → run driver.

## Driving it

```bash
python3 scripts/run_outreach_prep.py --category Law --practice personal-injury --max-firms 2 --dry-run
python3 scripts/run_outreach_prep.py --category Logistics --max-firms 50
python3 scripts/run_outreach_prep.py --category Law --linkedin-results Sources/runs/linkedin-results-YYYY-MM-DD.jsonl
```

LinkedIn pass:

1. Driver writes `Sources/runs/linkedin-queue-YYYY-MM-DD.jsonl`
2. Coordinator: signed-in LinkedIn (or website-to-api LinkedIn recipe) → `linkedin-results-….jsonl`
3. Driver apply + merge into firm JSON; `linkedin.status=blocked|names_masked|linked`
4. Re-rank `best_poc`; rebuild shortlist

## Pass / fail

| Claim | Pass | Fail |
|---|---|---|
| Dry-run plans without vault mutation | stdout plan; no firm JSON / shortlist / push | Dry-run pushes or overwrites profiles |
| Alias resolve | `pi` → Law + personal-injury; trucking → Logistics | Wrong category |
| Crawl writes category field | `Research/firms/<slug>.json` has `"category"` | Missing category |
| LinkedIn default ON | Without `--skip-linkedin`, queue step runs (or dry-run plans it) | Silently skips LI |
| Blocked LI continues | `linkedin.status=blocked` / names_masked; exit 0 | Week aborts on one blocked profile |
| Merge source | LI people in `contacts[]` have `source=linkedin` | Unsourced or invented emails |
| Shortlist order | Named best_poc before inbox-only | info@ ranked above named person |
| Push gate | Real writes → push-backup; dry-run/`--no-push` → no push | Dry-run push |

## Gotchas

- `build_poc.py` missing → TODO in prove log; shortlist still writes from existing profile fields
- Free LinkedIn People tab often masks names — record `names_masked`, keep titles, never invent
- Prefer website-to-api LinkedIn recipe when captured; else UI path with signed-in session
- Calls remain operator-gated

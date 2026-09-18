---
name: outreach-prep
description: Use when prep a vertical week — "prep trucking week", "setup HVAC outreach", "PI week", or a cold-rerunnable cover→crawl→public LinkedIn people and hiring→POC→shortlist pipeline for one business category. Prep only.
---

# Outreach prep

Vault: `$VAULT_ROOT` or `./data`. Skill id: `outreach-prep`.

**Executable.** One category / practice for the week. Run the driver. Write prove log. Calls stay gated — this skill is prep only.

Depends on sibling skills [outreach-leads](../outreach-leads/SKILL.md) and [website-search](../website-search/SKILL.md). LinkedIn now uses anonymous public website HTML through outreach-leads; no signed-in session, browser replay, API key, or third-party provider is needed.

## Pipeline

```
cover → specialty (if thin) → queue websites → crawl → public LinkedIn people + hiring → emails (if thin) → POC rank → shortlist → push-backup
```

1. **Coverage-check** — `outreach-leads` coverage-check. If NOT COVERED and not `--skip-specialty`, run specialty-directory for the mapped vertical.
2. **Queue** — vault notes in category (optional practice) that already have official `website:` (no directory hosts).
3. **Crawl** — `website-search` per firm → `Research/firms/<slug>.json`. Preserve prior LinkedIn/hiring data during website recrawls.
4. **Public LinkedIn (default ON)** — run `linkedin_contacts.py` on **exactly this prep queue**, not an independently selected set. Fetch public employee cards and company-matched jobs; apply notes and merge firm profiles. Use `linkedin_company:`, saved company URL, or an unambiguous company link on the official homepage. Never guess identity from a similar name. Store timestamped, category/practice-scoped results under `Sources/runs/`. Stop on login/challenge/rate limits and **continue the prep with explicit unknown coverage**, not fabricated results. Opt out with `--skip-linkedin`.
5. **Emails** — optional sitemap enrich when profiles still lack addresses.
6. **POC rank** — `build_poc.py` when present; named decision-makers beat info@. Restore this run's LinkedIn evidence after ranking so its narrower metadata writer cannot erase people/hiring provenance.
7. **Shortlist** — `Research/firms/shortlists/<category-slug>-YYYY-MM-DD.json`, with public people coverage and hiring status, observed job count, checked time, and stop reason. Existing named-POC order stays unchanged.
8. **push-backup** — auto on real writes when `BACKUP_REPO` is set unless `--dry-run` / `--no-push`. The LinkedIn subprocess defers backup to this final step.

## CLI

```bash
cd tools/outreach-prep
python3 scripts/run_outreach_prep.py --category Law [--practice personal-injury] \
  [--max-firms 50] [--max-pages 35] [--concurrency 4] \
  [--skip-specialty] [--skip-poc] [--skip-emails] [--skip-linkedin] \
  [--linkedin-max-jobs 50] [--linkedin-job-pages 3] \
  [--linkedin-results PATH] [--dry-run] [--no-push]
```

One pass is now the default:

```bash
python3 scripts/run_outreach_prep.py --category Law --practice personal-injury \
  --max-firms 50 --linkedin-max-jobs 50 --linkedin-job-pages 3
```

To replay previously collected results explicitly instead of fetching LinkedIn:

```bash
python3 scripts/run_outreach_prep.py --category Law --practice personal-injury \
  --linkedin-results "$VAULT_ROOT/Sources/runs/linkedin-results-my-run.jsonl"
```

The driver does **not** silently apply a same-day file from another prep run. Missing explicit results paths fail before work begins. `--dry-run` does not call LinkedIn or write artifacts. Sibling tools resolve from `TOOLS_ROOT` or the `tools/` directory next to this skill.

## Aliases

| Say / flag | Category | Practice |
|---|---|---|
| pi, personal-injury, injury, trial | Law | personal-injury |
| family, family-law | Law | family |
| immigration | Law | immigration |
| law, lawyer(s), attorney(s) | Law | — |
| trucking, truck, freight, logistics | Logistics | — |
| oilfield, oil, oil-field | Oilfield | — |
| hvac, ac, a/c | HVAC | — |
| roofing, roof | Roofing | — |
| dentist, dental | Dentist | — |
| accounting, cpa | Accounting | — |
| insurance | Insurance | — |
| real-estate, realtor, realty | Real estate | — |
| medical, clinic, doctor | Medical | — |
| engineering, engineer | Engineering | — |

## Hard rules

1. Prep only. No cold calls from this skill.
2. Official firm domains only in `website:`. Never Justia/FindLaw/Avvo/Yelp/FB/LinkedIn.
3. LinkedIn company URLs → `linkedin_company:` / firm JSON `linkedin` — never `website:`.
4. Never invent people or emails. Public employee lists are **partial samples**, not all employees. Blocked requests do not delete saved contacts.
5. Hiring is `hiring` only with company-matched public job cards. `no_public_jobs_found` is an observed empty result, **not proof of no hiring**. Inaccessible or unrecognized pages produce `unknown`; never infer `is_hiring=false`.
6. Named `best_poc` first; general inboxes are `inbox_fallback` only.
7. HTTP UA: `OutreachTools/1.0`. No cookies, developer APIs, paid services, proxy rotation, or challenge bypass. Bound requests and stop on access refusal.
8. After real vault writes: push-backup when `BACKUP_REPO` is set.

## Done means

- Coverage scorecard printed
- Firm JSON profiles for queued sites (or dry-run plan)
- LinkedIn outcomes and evidence recorded for queued firms **or** explicit `--skip-linkedin`
- Shortlist written (real run) with best_poc ordering and hiring/people coverage
- Prove log under `Sources/`
- Report public/partial/blocked coverage honestly; do not claim a complete staff or vacancy census

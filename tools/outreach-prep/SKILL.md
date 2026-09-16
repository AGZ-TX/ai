---
name: outreach-prep
description: Use when prep a vertical week — "prep trucking week", "setup HVAC outreach", "PI week", or a cold-rerunnable cover→crawl→LinkedIn→POC→shortlist pipeline for one business category. Prep only.
---

# Outreach prep

Vault: `$VAULT_ROOT` or `./data`. Skill id: `outreach-prep`.

**Executable.** One category / practice for the week. Run the driver. Write prove log. Calls stay gated — this skill is prep only.

Depends on sibling skills [outreach-leads](../outreach-leads/SKILL.md), [website-search](../website-search/SKILL.md), and [website-to-api](../website-to-api/SKILL.md) (LinkedIn recipe replay when present).

## Pipeline

```
cover → specialty (if thin) → queue websites → crawl → LinkedIn people → emails (if thin) → POC rank → shortlist → push-backup
```

1. **Coverage-check** — `outreach-leads` coverage-check. If NOT COVERED and not `--skip-specialty`, run specialty-directory for the mapped vertical.
2. **Queue** — vault notes in category (optional practice) that already have official `website:` (no directory hosts).
3. **Crawl** — `website-search` per firm → `Research/firms/<slug>.json`.
4. **LinkedIn people (default ON)** — queue via `enrich --layer contacts`; coordinator fills results with a **signed-in LinkedIn session** (never commit cookies). Prefer **website-to-api** LinkedIn recipe replay if present; else UI company People tab. Apply JSONL → notes; merge into firm JSON `contacts[]` with `source=linkedin`; set `linkedin.status` (`linked` / `names_masked` / `blocked`). Blocked or masked → record and **continue**. Opt out only with `--skip-linkedin`.
5. **Emails** — optional sitemap enrich when profiles still lack addresses.
6. **POC rank** — `build_poc.py` when present; named decision-makers beat info@.
7. **Shortlist** — `Research/firms/shortlists/<category-slug>-YYYY-MM-DD.json`.
8. **push-backup** — auto on real writes when `BACKUP_REPO` is set unless `--dry-run` / `--no-push`.

## CLI

```bash
cd tools/outreach-prep
python3 scripts/run_outreach_prep.py --category Law [--practice personal-injury] \
  [--max-firms 50] [--max-pages 35] [--concurrency 4] \
  [--skip-specialty] [--skip-poc] [--skip-emails] [--skip-linkedin] \
  [--linkedin-results PATH] [--dry-run] [--no-push]
```

Two-pass LinkedIn (typical week):

```bash
python3 scripts/run_outreach_prep.py --category Law --practice personal-injury --max-firms 50

# Coordinator: signed-in LinkedIn or website-to-api recipe
# → $VAULT_ROOT/Sources/runs/linkedin-results-YYYY-MM-DD.jsonl

python3 scripts/run_outreach_prep.py --category Law --practice personal-injury \
  --linkedin-results "$VAULT_ROOT/Sources/runs/linkedin-results-YYYY-MM-DD.jsonl"
```

Sibling tools resolve from `TOOLS_ROOT` or the `tools/` directory next to this skill.

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
4. Never invent people or emails. Masked LI names stay `names_masked`; blocked → `linkedin.status=blocked`.
5. Named `best_poc` first; general inboxes are `inbox_fallback` only.
6. HTTP UA: `OutreachTools/1.0`.
7. After real vault writes: push-backup when `BACKUP_REPO` is set.

## Done means

- Coverage scorecard printed
- Firm JSON profiles for queued sites (or dry-run plan)
- LinkedIn attempted for queued firms **or** explicit `--skip-linkedin`
- Shortlist written (real run) with best_poc ordering
- Prove log under `Sources/`

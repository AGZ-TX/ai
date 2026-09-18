---
name: outreach-leads
description: Use when ingesting or enriching CRM vault leads. Covers coverage checks, specialty directories (Justia/FindLaw, Texas Comptroller, OSM, TREC, TDLR, NPPES, TSBDE), enrich layers (website, public LinkedIn people and hiring, emails/sitemap), POC ranking, recipe fetches, and prove logs. Agents pull cold via scripts.
---

# Outreach leads

Vault: `$VAULT_ROOT` or `./data`. Skill id: `outreach-leads`.

**Executable.** Read artifacts. Run CLI. Write prove log. If coverage matrix says specialty required and you only bulk-dumped, status is NOT COVERED.

## Install

```bash
export VAULT_ROOT="$(pwd)/data"
python3 tools/outreach-leads/scripts/outreach_leads.py doctor --init
```

Stdlib only. Optional backup needs `gh` plus `BACKUP_REPO`.

## Hard rules

1. Websites > phones. Official firm domain only.
2. No Google Places.
3. Never write directory hosts into `website:` (Justia, FindLaw, Avvo, Yelp, FB, LinkedIn, …). Enforced by `verify_website.py`. LinkedIn company URLs go in `linkedin_company:` / `## Contacts`, never `website:`.
4. Skip chains, out-of-country, unnamed junk, ticket mills, individual apprentices.
5. Keep churches/nonprofits when in scope.
6. Calls remain gated. This skill is CRM ingest/enrich only.
7. HTTP UA: `OutreachTools/1.0`.
8. Vault path is `$VAULT_ROOT` or `./data`. Texas public directories are examples, not a locked metro.
9. Contacts now fetch **public LinkedIn people and hiring in one pass**. No LinkedIn keys, cookies, signed-in session, Voyager, or third-party scraping service. Never invent names or emails. Public employee cards are a **partial sample**, not a staff directory. Stop on login walls, challenges, or rate limits; do not bypass them. Use `--layer emails` for website addresses.
10. After a successful vault write, `push-backup` runs when `BACKUP_REPO` is set (branch→PR→merge, never direct push to `main`). Dry-runs never open a PR.
11. Hiring requires company-matched job cards. Generic “Browse jobs” counts and keyword matches are not evidence. `is_hiring` is `true` for observed listings or `null` when unestablished, never inferred `false`. Retain timestamps and partial-coverage labels.

Frontmatter facts: `category: "[[Law]]"` (wikilink); empty site is often `website: ""` (missing); PI is `practice: personal-injury` (also `trial`).

## Preflight

1. `python3 scripts/outreach_leads.py doctor --init`
2. Read `references/coverage.json` / `references/COVERAGE-MATRIX.md`
3. Pick mode → run CLI → prove log under `$VAULT_ROOT/Sources/`
4. Verification map: `references/verification-map/`

## Modes

```bash
cd tools/outreach-leads
python3 scripts/outreach_leads.py doctor --init
python3 scripts/outreach_leads.py coverage-check --category Law [--practice pi]
python3 scripts/outreach_leads.py specialty-directory --vertical pi|accounting|dentist|insurance|real-estate|hvac|roofing|medical|engineering|family|immigration --geo texas [--dry-run] [--limit N] [--no-push]
python3 scripts/outreach_leads.py enrich --category Law [--practice pi] [--layer website] [--limit N] [--no-push]
python3 scripts/outreach_leads.py enrich --category Law --practice pi --layer contacts --limit 5 --dry-run
python3 scripts/outreach_leads.py enrich --category Law --practice pi --layer contacts --limit 5
python3 scripts/outreach_leads.py enrich --layer contacts --apply "$VAULT_ROOT/Sources/runs/linkedin-results-YYYY-MM-DD.jsonl" [--no-push]
python3 scripts/outreach_leads.py enrich --layer emails --category Law --practice pi --limit N [--dry-run] [--max-pages 40]
python3 scripts/outreach_leads.py enrich --layer emails --slug example-law
python3 scripts/outreach_leads.py fetch --recipe justia_pi_texas
python3 scripts/outreach_leads.py verify-website --url https://example.com --firm 'Example Law'
python3 scripts/outreach_leads.py bulk-dump --recipe comptroller_texas --limit 20
python3 scripts/outreach_leads.py build-poc --category Law [--slug SLUG] [--limit N] [--dry-run] [--skip-fetch] [--no-push]
python3 scripts/outreach_leads.py push-backup [--dry-run]
```

| Mode | Script | When |
|---|---|---|
| doctor | outreach_leads.py | Vault + recipes + imports healthy. `--init` creates `./data` |
| coverage-check | coverage_check.py | Before claiming a vertical; exit 2 = NOT COVERED |
| specialty-directory | specialty_directory.py | Matrix specialty_required verticals. `--geo texas` is the example; `GEO_CITIES` optionally filters cities |
| enrich | enrich.py | `--layer website` fills official domains; `--layer contacts` fetches public LinkedIn people + hiring and applies JSONL; `--layer emails` crawls firm sitemap for same-domain emails |
| fetch | fetch_recipe.py | Pull one recipe |
| verify-website | verify_website.py | Gate before writing `website:` |
| bulk-dump | fetch + path | Broad dump fill |
| build-poc | build_poc.py | Rank named POCs. Never invent emails |
| push-backup | push_backup.py | Optional vault → `BACKUP_REPO` `outreach/data` via branch→PR→merge |

Recipes are Texas statewide examples (Comptroller, TREC, TDLR, Justia `/texas`, OSM `US-TX`). Override with `--recipes` or edit `references/sources.json`.

## Public LinkedIn people + hiring playbook

1. `doctor --init`.
2. Set `linkedin_company:` to a known public company URL. Otherwise the fetcher reuses the saved profile URL or finds a unique company link on the official website homepage. It does **not** guess a company slug from the business name. Ambiguity remains `company_not_found`.
3. `enrich --layer contacts --category Law --practice pi --limit 5` fetches and applies. Existing contacts do not prevent a fresh hiring check.
4. Inspect `Sources/runs/linkedin-results-YYYY-MM-DD.jsonl`, `Research/firms/<slug>.json`, and the note's `## Contacts` / `## LinkedIn public check`. Each result includes source URLs, HTTP outcomes, timestamps, and stop reasons. Daily files are overwritten on a new direct run; use `--output` below to retain multiple runs.
5. Named people merge into contacts/POC ranking. Missing fields never erase known emails. A blocked/empty sample does not delete contacts. Current unknown hiring is kept separate from `linkedin_last_successful_hiring`.
6. Successful mutations trigger the existing optional backup unless `--no-push`.

Advanced flags belong to **`linkedin_contacts.py` directly**, not the generic enrich wrapper:

```bash
# Single firm; explicit identity when homepage discovery is unavailable.
python3 scripts/linkedin_contacts.py --slug example-law \
  --company-url https://www.linkedin.com/company/example-law/ --no-push

# Hiring only; no new people. Defaults: 50 observed jobs, 3 pages, 2s pacing.
python3 scripts/linkedin_contacts.py --category Law --jobs-only \
  --max-jobs 50 --job-pages 3 --delay 2 --no-push

# Legacy export/import remains available, without automated login.
python3 scripts/linkedin_contacts.py --category Law --queue-only --force
python3 scripts/linkedin_contacts.py --apply /path/to/results.jsonl --no-push

# Fetch exactly the notes in a queue and retain a named results artifact.
python3 scripts/linkedin_contacts.py --queue /path/to/queue.jsonl \
  --output "$VAULT_ROOT/Sources/runs/linkedin-results-my-run.jsonl" --no-push
```

`--dry-run` performs no HTTP calls and no writes, including apply mode. `--force` matters only for legacy queue-only contact filtering. Slug selection is exact. Imported paths must identify notes inside this vault's `Businesses/` directory.

Transport: anonymous company HTML `/company/<slug>/`; employer-filtered guest **HTML** at `/jobs-guest/jobs/api/seeMoreJobPostings?f_C=<id>&start=<offset>` when a single company ID is exposed in company job navigation; otherwise the public company `/jobs/` page. Despite `api` in the guest path, this is a public website HTML response, not a keyed LinkedIn developer API. Multiple affiliate IDs are not guessed. No full employee roster or complete vacancy count is promised.

Full evidence schema and checks: [contacts verification](references/verification-map/contacts.md).

## Emails playbook

1. Notes must already have official `website:`
2. `enrich --layer emails --slug <stem> --dry-run`
3. `enrich --layer emails --slug <stem>` → same-registrable-domain emails only
4. Prove: never invent emails; never write linkedin.com into `website:`

## POC ranking

```bash
python3 scripts/outreach_leads.py build-poc --category Law
python3 scripts/outreach_leads.py build-poc --category Logistics
python3 scripts/build_poc.py --category Law --slug example-law --dry-run
```

Named decision-makers beat info@. Same-domain emails only. Next crawl step is [website-search](../website-search/SKILL.md).

## Env

| Var | Default | What |
|---|---|---|
| `VAULT_ROOT` | `./data` | Vault root (`Businesses/`, `Sources/`, `Research/`) |
| `BACKUP_REPO` | unset | Optional git remote for push-backup |
| `GEO_CITIES` | unset (all) | Comma city allow-list for specialty parse |
| `DEFAULT_CITY` | `Texas` | City written when a row has none |
| `LOCAL_AREA_CODES` | unset | Prefer these NANP prefixes when ranking phones |
| `VAULT_TZ` | `America/Chicago` | Prove-log dates |

## Done means

- Scorecard printed
- Prove log written; LinkedIn conclusions checked against results JSONL, not queue size
- No directory hosts in `website:`
- After real writes: `push-backup` ran or printed `skip no BACKUP_REPO`
- Report: COVERED / NOT COVERED + next mode; report LinkedIn partial/blocked/unknown separately

# specialty-directory

specialty-directory fetches specialty recipes (Justia/FindLaw, Comptroller SODA, OSM Overpass, TREC, TDLR, NPPES, TSBDE), parses candidates, writes JSONL, dedupes firm+phone, and creates/updates Law or business notes without directory hosts in `website:`. Verticals: pi, accounting, dentist, insurance, real-estate, hvac, roofing, medical, engineering, family, immigration.

## Sub-features

- `sd-fetch-parse` pulls recipes and emits candidates.
- `sd-dry-run` writes JSONL only (`--dry-run`).
- `sd-dedupe` skips or updates existing firm+phone notes.
- `sd-prove` writes `Sources/specialty-<vertical>-YYYY-MM-DD.md`.
- `sd-push` after real creates/updates: auto `push-backup` (skipped on `--dry-run` / `--no-push`).

## How to get to it (user POV)

- `python3 scripts/outreach_leads.py specialty-directory --vertical pi --geo texas --dry-run`
- Add `--limit N` for a small proof
- Omit `--dry-run` only when vault writes are intended

## Driving it with outreach_leads

Preconditions:

- `doctor` PASS
- Network allowed for Justia/FindLaw
- Prefer `--dry-run` for first proof

- **Dry-run JSONL.** Run `python3 scripts/outreach_leads.py specialty-directory --vertical pi --geo texas --dry-run --limit 20`. Stdout has `candidates` and a JSONL path under `Sources/runs/` or `/tmp`.
- **Parse quality.** Open JSONL: rows have `firm` and usually `phone`; `candidate_website` is empty or non-directory.
- **No vault corruption (dry-run).** `git status` / mtime on Businesses unchanged for dry-run.
- **Prove.** `Sources/specialty-pi-YYYY-MM-DD.md` exists. Pass = JSONL nonempty + prove path printed; fail = fetch_fail for all recipes or directory URL stored as website on a write run.
- **Backup.** Dry-run prints `push-backup\tskip\tdry-run`. Real creates/updates print `push-backup\trequested` then commit URL (unless `--no-push`).

## Gotchas

- Directory listing pages may lack ProfileWebsite; candidate websites often need profile fetch (`--fetch-profiles`) or enrich later.
- National 800 numbers and chain names are skipped.
- Never pass a Justia profile URL into `website:` frontmatter.
- Do not batch GitHub backups; each successful write run pushes immediately.

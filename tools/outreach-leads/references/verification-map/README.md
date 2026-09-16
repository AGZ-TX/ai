# Outreach-leads verification map

Maintained source for proving outreach-leads CLI behavior. Agent is the user. Read this index, then the matching feature file.

## Baseline preconditions

- `VAULT=./data` with `Businesses/` present.
- Run from `tools/outreach-leads`.
- HTTP UA `OutreachTools/1.0` (scripts set this).
- `python3 scripts/outreach_leads.py doctor` → exit 0 and `PASS`.
- Prefer `--dry-run` / `--limit` when a proof must not mutate vault notes.
- After real vault writes, expect immediate `push-backup` (or prove `--no-push` was intentional). Dry-run never pushes.
- Evidence stays under `Sources/` prove logs, `Sources/runs/*.jsonl`, and `VERIFY.md`. Do not delete proof in cleanup.

## Driving conventions

- Entry points are CLI modes on `outreach_leads.py`.
- Treat commands as literal; keep flags and quoted firm names unchanged.
- Category matching uses wikilink `category: "[[Law]]"`. Empty `website: ""` counts as missing.
- After any write proof, read the note frontmatter back (second view).

## Proof and skip reporting

- Capture command, exit code, and stdout/stderr slices.
- Mutation proof: read back `website:` / `phone:` / `practice:` from the note file.
- Directory-host proof: `website:` must not contain Justia/FindLaw/Avvo/Yelp/FB/LinkedIn/etc.
- Report unreachable paths with the unmet precondition. Do not claim a skipped dry-run as a vault write proof.

## Features

- [coverage-check](./coverage-check.md) — blind-spot gate + scorecard
- [specialty-directory](./specialty-directory.md) — Justia/FindLaw parse, dedupe, JSONL
- [enrich](./enrich.md) — website + contacts + emails layers (`--layer website|contacts|emails`)
- [contacts](./contacts.md) — LinkedIn queue/apply (enrich contacts layer)
- [emails](./emails.md) — sitemap / website email crawl (enrich emails layer)
- [fetch-recipe](./fetch-recipe.md) — recipe pull
- [verify-website](./verify-website.md) — OK vs directory reject gate
- [push-backup](./push-backup.md) — immediate vault → GitHub `outreach/data` sync+push

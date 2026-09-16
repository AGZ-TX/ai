# outreach-prep verification map

Agent is the user. Prove the week prep pipeline before claiming a vertical is ready.

## Baseline

- Vault: `./data` with `Businesses/`
- Pack: `tools/outreach-prep` or install `./tools/outreach-prep`
- Depends on installed `outreach-leads` + `website-search` scripts
- UA: `OutreachTools/1.0`
- Entry: `python3 scripts/run_outreach_prep.py --category …`
- Prefer `--dry-run --max-firms 2` for non-mutating smoke
- LinkedIn browser work uses a signed-in session; queue/apply is CLI

## Features

- [prep](./prep.md) — full week driver: cover → crawl → LinkedIn → POC → shortlist

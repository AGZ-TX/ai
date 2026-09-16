# website-search verification map

Agent is the user. Prove a multi-page JSON crawl before claiming a site was researched.

## Baseline

- Pack: `tools/website-search` in this repo. Grok install: `tools/website-search` or `./tools/website-search`
- Vault output: `./data/Sources/runs/`. Markdown under `Research/` only if requested
- UA: `OutreachTools/1.0`
- Entry: `python3 scripts/research_website.py <url|domain> …`

## Features

- [research](./research.md). discover, fetch, retry, JSON

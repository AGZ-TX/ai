# ai

Public agent skills. Point Grok Bot, Claude, ChatGPT, or any skill host at this repo and install from `tools/`.

```bash
git clone https://github.com/AGZ-TX/ai.git
cd ai
./tools/install.sh
```

Or tell the host: **install from https://github.com/AGZ-TX/ai** — each skill is `tools/<id>/SKILL.md`.

No secrets in this repo. Vault data and Chrome cookies stay local.

## Skills

| id | What |
|---|---|
| [`outreach-leads`](tools/outreach-leads/SKILL.md) | Bulk ingest + enrich. Texas Comptroller / TREC / TDLR / Justia / OSM recipes. Thousands of leads. |
| [`outreach-prep`](tools/outreach-prep/SKILL.md) | One-vertical week: cover → crawl → LinkedIn → POC → shortlist. |
| [`website-search`](tools/website-search/SKILL.md) | Fast multi-page sitemap crawl with full visible copy. |
| [`website-to-api`](tools/website-to-api/SKILL.md) | Capture → distill → replay. Patterns only; never commit cookies. |
| [`search-x-profile`](tools/search-x-profile/SKILL.md) | One X profile. `UserOriginalsTimeline` archive + optional voice note. |
| [`search-x`](tools/search-x/SKILL.md) | X Search page (Top / Latest / People / Media). Same capture/replay spirit. |

## Compose

```
outreach-leads  →  outreach-prep  →  website-search  →  website-to-api
                                      search-x-profile
                                      search-x
```

1. `outreach-leads` fills the vault (specialty-directory, enrich).
2. `outreach-prep` runs a week: coverage, crawl via `website-search`, LinkedIn via signed-in session or `website-to-api` recipe, POC rank.
3. `website-search` is the full-site crawl. `website-to-api` is the expensive-path recipe.
4. `search-x-profile` is one handle. `search-x` is a Search query.

## Vault

```bash
export VAULT_ROOT="$(pwd)/data"   # default if unset
./tools/install.sh
```

## Prove

```bash
./tools/doctor.sh
```

See [tools/README.md](tools/README.md) for deps, env, and per-tool commands.

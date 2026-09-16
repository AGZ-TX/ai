---
name: website-search
description: Use when researching a site, URL, or website, or when an agent needs a sitemap plus page-crawl JSON with full visible copy and on-page signals instead of a single-page WebFetch. Runs scripts/research_website.py.
---

# Website search

When the job is research a website or URL, run this skill's CLI first. Do not stop at a WebFetch of the landing page.

Stdlib only. Fast multi-page crawl (default concurrency 4, cap 8).

## Run

```bash
python3 tools/website-search/scripts/research_website.py https://example.com \
  --json "$VAULT_ROOT/Sources/runs/research-<slug>-YYYY-MM-DD.json" \
  [--max-pages 80] [--priority-only] [--include-emails] [--all] \
  [--concurrency 4] [--no-full-text]

python3 tools/website-search/scripts/research_website.py --retry-failed path/to/prior.json \
  --json ...
```

A bare domain is accepted. `example.com` becomes `https://example.com`. The start URL may be a deep path. The crawl stays on that URL's origin and registrable domain.

Default `--max-pages` is 60. Default `--concurrency` is 4, cap 8. Full text is on. JSON runs land under `$VAULT_ROOT/Sources/runs/` (or `./data/Sources/runs/`). Markdown is off unless `--out PATH` or `--markdown` is passed.

Prove: `python3 tools/website-search/scripts/prove_full_text.py`

## What the CLI does

Encoded in `scripts/research_website.py`. Not optional prose.

1. Normalize the start URL. User-Agent is `OutreachTools/1.0`.
2. Read `robots.txt` Sitemap: lines plus `/sitemap.xml` and `/sitemap_index.xml`, including nested indexes.
3. Always include the homepage and common paths (contact, about, team, blog, pricing, services, and the rest in the script).
4. If the sitemap has more than 500 URLs, keep priority path keywords unless `--all`.
5. Fetch up to `--max-pages` with `--concurrency` (default 4, cap 8). Same registrable domain only.
6. Retry timeouts, connection errors, and HTTP 5xx two to three times with backoff 0.5s, 1.5s, 3s. Do not retry 404 or 403. Connect timeout is about 6s. Read timeout is about 22s. Finish the URL list and mark failed pages.
7. Per ok page: status, http_code, attempts, error, final URL, title, meta description, h1s at most 5, word_count, same-site link sample, emails if `--include-emails`, JSON-LD `@type`s, llms.txt link mentions, plus `full_text`, `full_text_truncated`, `headings` (h1–h3, cap 40), `image_alts` (cap 30), `list_items` (cap 40), and `cta_like` (cap 20). `word_count` matches `full_text.split()` when `full_text` is stored. `--no-full-text` stores an empty `full_text` and still counts words and extracts signals. Single-page `full_text` caps at 200k chars.
8. Site-level JSON holds `/llms.txt`, robots AI-bot signals for GPTBot, ClaudeBot, Google-Extended, and PerplexityBot, and `outcome_counts`.
9. Write the JSON run artifact (primary). Write markdown only if `--out` or `--markdown` is passed.
10. `--retry-failed prior.json` re-fetches non-ok pages only and merges them.
11. Exit 0 if any page is ok. Exit 2 if no page is ok and the start URL failed every retry.

HAR capture is out of scope. Use [website-to-api](../website-to-api/SKILL.md) for API recipes.

## Related

- [website-to-api](../website-to-api/SKILL.md) when a known UI path is token-expensive.
- [outreach-leads](../outreach-leads/SKILL.md). Reuse this JSON run before enrich. This skill does not write CRM notes.

## Done means

- JSON path printed
- `pages` has more than one URL when the site has more, each with an outcome
- Each ok page has `full_text` unless `--no-full-text`, plus headings, image_alts, list_items, and cta_like when the HTML has them
- `word_count` matches stored `full_text` word split when `full_text` is stored
- Failures marked and counted. The crawl does not stop after one timeout

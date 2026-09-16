# research (CLI)

Sitemap plus common-path crawl of one origin. Writes a JSON run artifact. Markdown is optional.

## Sub-features

- `rw-normalize`. https URL or bare domain. A deep start URL keeps its path. Crawl scope is the origin registrable domain.
- `rw-discover`. robots Sitemap: lines plus `/sitemap.xml` and `/sitemap_index.xml`. Nested indexes, cap 25. Homepage plus common paths.
- `rw-prioritize`. Priority keywords. `--max-pages` (default 60). Sitemap over 500 URLs is filtered unless `--all`. `--priority-only` keeps only priority paths.
- `rw-fetch`. Parallel fetches within one site (`--concurrency` default 4, cap 8). Per ok page: title, meta description, h1s at most 5, word_count, same-site link sample, optional same-domain emails, JSON-LD @types, llms.txt href mentions, `full_text`, `full_text_truncated`, headings (h1–h3, cap 40), image_alts (cap 30), list_items (cap 40), cta_like (cap 20). `--no-full-text` skips stored body text. `full_text` caps at 200k chars.
- `rw-retry`. Timeouts, connection errors, and HTTP 5xx retry two to three times with backoff 0.5s, 1.5s, 3s. No retry on 404 or 403. Connect about 6s. Read about 22s. Finish the URL list and mark failed pages.
- `rw-outcomes`. Page `status` is ok, timeout, http_error, or error, plus `http_code`, `attempts`, and `error`. JSON `outcome_counts` summarizes failures. `--retry-failed prior.json` re-fetches non-ok pages only and merges them.
- `rw-site`. Probe `/llms.txt`. Summarize GPTBot, ClaudeBot, Google-Extended, and PerplexityBot robots allow or disallow.
- `rw-write`. JSON run is the primary artifact. Markdown writes only when `--out` or `--markdown` is passed. Optional markdown is a short preview, not a `full_text` dump.
- `rw-exit`. Exit 0 if any page is ok. Exit 2 if no page is ok and the start URL failed every retry.

## CLI

```bash
python3 scripts/research_website.py https://example.com \
  --json ./data/Sources/runs/research-example-com-YYYY-MM-DD.json \
  --max-pages 40 --include-emails --concurrency 4
python3 scripts/research_website.py example.com --priority-only
python3 scripts/research_website.py --retry-failed prior.json --json new.json
```

## Pass / fail

| Claim | Pass | Fail |
|---|---|---|
| Multi-page | JSON `pages` has more than one URL when the site has more | Only homepage skim or a single WebFetch |
| Same domain | All fetched hosts share the registrable domain | Off-domain pages in JSON |
| Full text | Each ok page has `full_text` length > 0 unless `--no-full-text`. `word_count` matches `full_text.split()` when stored | Empty body on an HTML page with visible copy. word_count disagrees with stored text |
| Signals | headings, image_alts, list_items, cta_like present when the HTML has them | Fields missing on ok pages |
| Emails | Only with `--include-emails`. Same-domain. Never invented | Free-mail kept. Fabricated addresses |
| LLM-readiness | llms.txt, bots, and schema fields always present in JSON | Fields omitted |
| Theme hints | Tokens from titles and h1s with counts | Invented rankings or SEO claims |
| Huge sitemap | Over 500 filtered to priority unless `--all` | Blind fetch of the entire catalog |
| Reliability | Timeouts and 5xx retried. Failed pages marked. URL list finished | Skip on first timeout. Abandon the rest of the site |
| Outcomes | Each page has status, http_code, and attempts. JSON has outcome_counts | Missing outcome fields |
| Markdown | No `.md` unless `--out` or `--markdown` | Default run writes a digest |

## Gotchas

- Case-study URLs (agency success-story pages) start on the agency origin. Research the live client domain as a second run when needed.
- Contact-form-only sites with zero emails are valid.
- No HAR here. See `scripts/HAR-OPTIONAL.txt` and website-to-api.
- No screenshots.
- This skill does not write CRM notes. Hand the JSON run to `tools/outreach-leads` enrich when the next step is vault fill.
- Use `--retry-failed` on a prior JSON to re-queue only non-ok pages without discovering the site again.

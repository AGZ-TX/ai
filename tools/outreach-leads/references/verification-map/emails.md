# emails (enrich --layer emails)

Website / sitemap email enrichment for vault business notes. Crawls the official firm domain already on `website:`; extracts same-registrable-domain emails only.

See also [enrich.md](./enrich.md). LinkedIn contacts remain best-effort for people; **this layer is the reliable path for firm addresses**.

## Sub-features

- `em-select` — notes with http(s) `website:` (skip directory hosts)
- `em-discover` — `/sitemap.xml`, `/sitemap_index.xml`, `robots.txt` Sitemap:; nested indexes; homepage + common contact/about/team paths
- `em-prioritize` — prefer contact/about/team/people/attorney/staff/bio paths; `--max-pages` (default 40); if sitemap >500 URLs, filter hard to priority paths before fetch
- `em-extract` — mailto: + regex; same registrable domain only; never invent; no free-mail unless firm domain
- `em-write` — JSONL + `## Emails` bullets + best `email:` if empty + Research line
- `em-dry-run` — planned pages only; no vault mutation; no push
- `em-push` — auto `push-backup` after successful real writes (unless `--no-push`)

## CLI

```bash
python3 scripts/outreach_leads.py enrich --layer emails --category Law --practice pi --limit 5 --dry-run
python3 scripts/outreach_leads.py enrich --layer emails --slug example-law --no-push
python3 scripts/outreach_leads.py enrich --layer sitemap-emails --slug example-law
python3 scripts/website_emails.py --slug example-law --max-pages 40
```

## JSONL shape

`Sources/runs/website-emails-YYYY-MM-DD.jsonl`:

```json
{"slug":"example-law","website":"https://example.com","emails":[{"email":"info@example.com","pages":["https://example.com/contact/"]}],"pages_fetched":12,"errors":[]}
```

## Note writes

- Frontmatter `email:` set only when empty — prefer info@, contact@, office@, admin@, then first found.
- Body:

```
## Emails
- `info@example.com` — found on: https://example.com/contact/
```

- Research line notes sitemap enrich date / page count / email count.

## Pass / fail

| Claim | Pass | Fail |
|---|---|---|
| Selects official websites | http(s) `website:` notes; directory hosts skipped | Directory host crawled as firm site |
| Same-domain only | Emails match firm registrable domain | gmail/yahoo kept; invented addresses |
| Page attribution | Each email lists pages found on | Emails without source URLs when found |
| Dry-run | Planned pages printed; no note/JSONL mutation; no push | Vault mutated on dry-run |
| Real write + backup | `## Emails` / prove log; `push-backup` unless `--no-push` | Silent skip of push after mutation |
| Huge sitemap | >500 URLs filtered to contact/about/team before fetch | Blind fetch of entire product catalog |

## Playbook

doctor → dry-run slug → real run (`--no-push` ok for first smoke) → real run with auto push-backup → prove `Sources/enrich-emails-….md`.

## Gotchas

- Many firms use contact forms only — zero emails is a valid result (never invent).
- UA must be `OutreachTools/1.0`.
- Never write linkedin.com into `website:`.
- No Google Places.

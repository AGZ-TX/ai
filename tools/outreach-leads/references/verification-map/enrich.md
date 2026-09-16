# enrich

enrich fills missing fields on vault notes.

- Default layer `website`: candidate URL → `verify_website` → write `website:` on OK.
- Layer `contacts` (alias `linkedin`): queue notes missing real `## Contacts` bullets → JSONL for browser lookup; `--apply` writes contacts back. Never writes linkedin.com into `website:`.
- Layer `emails` (alias `sitemap-emails`): notes with official `website:` → sitemap/robots + contact/about/team crawl → same-domain emails → `## Emails` + optional `email:`.

## Sub-features

- `en-website` verifies and writes official domains (`--layer website`, default).
- `en-contacts` builds `Sources/runs/linkedin-queue-YYYY-MM-DD.jsonl` (`--layer contacts`).
- `en-contacts-apply` writes `## Contacts` + optional `linkedin_company:` / `owner:` from results JSONL (`--apply PATH`).
- `en-dry-run` lists candidates / planned writes without mutating notes.
- `en-limit` caps queue or website fills with `--limit N`.
- `en-prove` writes `Sources/enrich-<layer>-<category>-YYYY-MM-DD.md`.
- `en-emails` crawls firm site for same-domain emails (`--layer emails` / `sitemap-emails`).
- `en-push` after website fills, contacts apply, or emails writes: auto `push-backup` (skipped on dry-run / `--no-push`).

## How to get to it (user POV)

- `python3 scripts/outreach_leads.py enrich --category Law --practice pi --layer website --dry-run`
- `python3 scripts/outreach_leads.py enrich --category Law --practice pi --limit 5`
- `python3 scripts/outreach_leads.py enrich --layer contacts --category Law --practice pi --limit 5 --dry-run`
- `python3 scripts/outreach_leads.py enrich --layer contacts --apply Sources/runs/linkedin-results-YYYY-MM-DD.jsonl --dry-run`
- `python3 scripts/outreach_leads.py enrich --layer emails --slug example-law --dry-run`
- `python3 scripts/outreach_leads.py enrich --layer emails --category Law --practice pi --limit 5`

## Driving it with outreach_leads

### Website

Preconditions:

- `doctor` PASS
- Notes with missing `website: ""` and a candidate URL in body/JSONL when testing a fill
- `verify-website` feature PASS on a known good firm URL

- **Dry-run.** Run enrich with `--dry-run --limit 10`. Stdout lists stems and candidate counts. No frontmatter changes.
- **Fill one.** Pick a note with a known-good candidate (or use verify-website proof separately). Run enrich `--limit 1`. Exit 0; stdout `filled` ≥ 0.
- **Readback.** Open the note: `website:` is `https://firm-domain` and host is not on the directory blocklist.
- **Prove.** Prove log path printed. Pass = readback official domain; fail = directory host in `website:` or write without verify OK.
- **Backup.** `filled` > 0 → `push-backup\trequested` + commit URL. Dry-run → `push-backup\tskip\tdry-run`.

### Contacts

Preconditions:

- `doctor` PASS (imports include `linkedin_contacts`)
- Signed-in LinkedIn session available to the coordinator (this script does not open LinkedIn)

- **Queue dry-run.** `enrich --layer contacts --category Law --practice pi --limit 5 --dry-run` prints JSON queue rows (firm, path, website, city, phone). No file write.
- **Queue write.** Same without `--dry-run` → `Sources/runs/linkedin-queue-YYYY-MM-DD.jsonl`.
- **Browser lookup.** External. Produce `linkedin-results-….jsonl` with `{slug|path, linkedin_company, contacts:[{name,title,profile_url}], owner}`. Do not invent contacts.
- **Apply dry-run.** `--apply PATH --dry-run` prints planned writes.
- **Apply.** `--apply PATH` upserts `## Contacts` bullets, sets `linkedin_company:` and `owner:` when Owner/Founder/CEO/President is clear.
- **Readback.** Note has `## Contacts` lines; `website:` still empty or firm domain — never linkedin.com.
- **Prove.** Pass = contacts on note + no LinkedIn in `website:`; fail = invented people or directory host as website.
- **Backup.** Successful `--apply` writes auto-push; apply `--dry-run` never pushes.

### Emails (sitemap)

Preconditions:

- `doctor` PASS (imports include `website_emails`)
- Notes with nonempty official `website:` http(s) (not directory host)

- **Dry-run.** `enrich --layer emails --slug <stem> --dry-run` prints planned page list. No vault mutation; `push-backup\tskip\tdry-run`.
- **Fetch.** Without `--dry-run`: discover sitemap/robots + common paths; prioritize contact/about/team/people/attorney/staff; cap `--max-pages` (default 40). Huge sitemaps (>500 URLs) filter to priority paths first.
- **Extract.** mailto: + regex; keep **same registrable domain only**. Never invent emails. No free-mail (gmail/yahoo/…) unless that is the firm domain.
- **Write.** JSONL `Sources/runs/website-emails-YYYY-MM-DD.jsonl`; note `## Emails` bullets `` `a@b.com` — found on: url1, url2 ``; set `email:` to best inbox if empty (info@, contact@, office@, admin@, then first); Research line with enrich date.
- **Prove.** `Sources/enrich-emails-<category>-YYYY-MM-DD.md`.
- **Backup.** Successful mutations auto `push-backup` unless `--no-push`.

See also [emails.md](./emails.md).

## Gotchas

- Mode name is `enrich` only. There is no `site-enrich`.
- Skip website notes that already have a nonempty website.
- Candidate URLs from Justia ProfileWebsite must still pass verify_website before write.
- Empty `## Contacts` stub (heading only) still counts as missing for queue.
- Alias: `--layer linkedin` == `--layer contacts`.
- Alias: `--layer sitemap-emails` / `email` / `website-emails` == `--layer emails`.
- LinkedIn profile open is best-effort; website emails is the reliable address path.

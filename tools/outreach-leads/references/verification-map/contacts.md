# contacts (enrich --layer contacts)

LinkedIn contact enrichment for vault business notes. Queue generation + apply only; browser lookup is external (signed-in LinkedIn session).

See also [enrich.md](./enrich.md).

## Sub-features

- `li-queue` — notes missing real Contacts bullets → `Sources/runs/linkedin-queue-YYYY-MM-DD.jsonl`
- `li-apply` — `--apply` results JSONL → `## Contacts`, `linkedin_company:`, `owner:`
- `li-dry-run` — print queue rows or planned writes; no mutation
- `li-force` — re-queue notes that already have contact bullets

## CLI

```bash
python3 scripts/outreach_leads.py enrich --layer contacts --category Law --practice pi --limit 5 --dry-run
python3 scripts/outreach_leads.py enrich --layer contacts --category Law --practice pi --limit 5
python3 scripts/outreach_leads.py enrich --layer contacts --apply Sources/runs/linkedin-results-YYYY-MM-DD.jsonl --dry-run
python3 scripts/outreach_leads.py enrich --layer linkedin --slug some-firm --force   # alias
python3 scripts/linkedin_contacts.py --category Law --practice pi --limit 5 --dry-run
```

## Results JSONL shape

Contact object: `{name, title, email, location, photo_url, profile_url}` (alias `pfp` → `photo_url`).

Seek email, location, and profile photo when LinkedIn exposes them. **Never invent emails.** Free company People tab may mask names (limited visibility without Sales Nav / connection).

```json
{"slug":"example-law","linkedin_company":"https://www.linkedin.com/company/example","contacts":[{"name":"Jane Doe","title":"Owner","email":"jane@example.com","location":"Texas","photo_url":"https://media.licdn.com/dms/image/example","profile_url":"https://www.linkedin.com/in/jane-doe"}],"owner":"Jane Doe"}
```

## `## Contacts` bullet format

```
- Name — Title — location — email — photo_url — profile_url
```

Omit empty fields cleanly (no dangling ` — `). LinkedIn company URLs go in `linkedin_company:` / Contacts — never write `linkedin.com` into `website:`.

## Pass / fail

| Claim | Pass | Fail |
|---|---|---|
| Queue lists missing-contacts notes | JSON rows with firm/path/website/city/phone | Empty when notes exist and lack bullets |
| Apply writes Contacts | `## Contacts` bullets with name/title/email/location/photo_url/profile_url when provided | Invented names/emails; LinkedIn URL in `website:` |
| Apply triggers backup | `push-backup\trequested` + commit URL (unless dry-run/`--no-push`) | Dry-run apply pushes |
| Owner fill | `owner:` set for Owner/Founder/CEO/President | Overwrites unrelated filled owner without explicit `owner` in results |
| Extra fields | email/location/photo_url land when present in JSONL | Fabricated emails; empty trailing em-dashes |

## Playbook

doctor → queue → browser lookup → apply → auto push-backup → prove (readback + no linkedin.com in `website:`).

## Gotchas (2026-09-15)

- Free company People cards may mask names and refuse profile open ("You don’t have access to this profile").
- Default: only enrich profiles this session can open. Titles from the grid are OK with `names_masked: true`. Never invent emails.
- Prefer `enrich --layer emails` for firm inbox addresses when LinkedIn hides emails or blocks profile open.

# Outreach-leads VERIFY

Pack: `tools/outreach-leads`. Vault: `$VAULT_ROOT` or `./data`.

## Doctor

```bash
python3 scripts/outreach_leads.py doctor --init
```

Expect exit 0, `PASS`. Vault OK, sources.json recipes load, coverage.json rows load, imports OK.

## coverage-check

```bash
python3 scripts/outreach_leads.py coverage-check --category Law
python3 scripts/outreach_leads.py coverage-check --category Law --practice pi
```

Prints notes / site-fill against `references/coverage.json`. Empty vault → NOT COVERED (exit 2).

## verify-website

```bash
python3 scripts/outreach_leads.py verify-website --url https://example.com --firm 'Example Law'
python3 scripts/outreach_leads.py verify-website --url 'https://www.justia.com/lawyers/personal-injury/texas' --firm 'Example Law'
```

Directory hosts must reject. Official domains that mention the firm name pass.

## fetch (Texas examples)

```bash
python3 scripts/outreach_leads.py fetch --recipe comptroller_texas --limit 3 --out /tmp/comptroller_smoke.json
python3 scripts/outreach_leads.py fetch --recipe justia_pi_texas --out /tmp/justia_smoke.html
```

## specialty-directory dry-run

```bash
python3 scripts/outreach_leads.py specialty-directory --vertical pi --geo texas --dry-run --limit 15
```

JSONL under `$VAULT_ROOT/Sources/runs/`. created=0 updated=0 on dry-run.

## enrich dry-run

```bash
python3 scripts/outreach_leads.py enrich --category Law --practice pi --layer website --dry-run --limit 5
python3 scripts/outreach_leads.py enrich --layer emails --slug example-law --dry-run
```

## Rename guard

```bash
python3 scripts/outreach_leads.py site-enrich --category Law
```

Exit 2. Message: use `enrich`.

## push-backup smoke

```bash
python3 scripts/outreach_leads.py push-backup --vault /tmp/.../vault --repo /tmp/.../backup.git --dry-run
```

Without `BACKUP_REPO` / `--repo`, mutation hooks print `push-backup skip no BACKUP_REPO`.

See `references/verification-map/`.

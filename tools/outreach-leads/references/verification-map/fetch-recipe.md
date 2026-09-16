# fetch-recipe

fetch pulls one named recipe from `sources.json` (SODA / HTML / Overpass) and prints or writes the body.

## Sub-features

- `fr-html` fetches specialty HTML recipes.
- `fr-soda` fetches SODA JSON with optional `--limit`.
- `fr-out` writes `--out` path and prints byte length.

## How to get to it (user POV)

- `python3 scripts/outreach_leads.py fetch --recipe justia_pi_texas --out /tmp/justia_pi_texas.html`
- `python3 scripts/outreach_leads.py fetch --recipe comptroller_texas --limit 5 --out /tmp/comptroller_sample.json`
- `python3 scripts/outreach_leads.py bulk-dump --recipe comptroller_texas --limit 5`

## Driving it with outreach_leads

Preconditions:

- `doctor` PASS
- Network allowed

- **HTML recipe.** Fetch `justia_pi_texas` to `/tmp/...`. Exit 0; file size > 1000; body contains `LegalService` or `Person`.
- **SODA sample.** Fetch `comptroller_texas --limit 5`. File is JSON array length ≤ 5.
- **Proof.** Command + path + size in VERIFY.md. Pass = exit 0 and nonempty artifact; fail = HTTP error or unknown recipe.

## Gotchas

- Pack and vault `sources.json` should stay mirrored.
- Large SODA pulls need `--limit` for smoke tests.

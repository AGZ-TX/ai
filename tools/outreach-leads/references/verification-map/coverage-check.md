# coverage-check

coverage-check counts vault notes for a category (and optional practice), compares to `references/coverage.json`, prints a scorecard, and exits 0 when covered or 2 when NOT COVERED.

## Sub-features

- `cc-scorecard` prints notes / phone / website / site-fill table.
- `cc-blindspot` fails when specialty_required and counts or site fill are below threshold.
- `cc-practice` scopes to PI via `--practice pi` (`personal-injury` / `trial`).

## How to get to it (user POV)

- Run `python3 scripts/outreach_leads.py coverage-check --category Law`
- Run `python3 scripts/outreach_leads.py coverage-check --category Law --practice pi`
- Call after specialty-directory or enrich (those modes invoke it)

## Driving it with outreach_leads

Preconditions:

- `doctor` PASS
- `references/coverage.json` loads
- Vault has Law notes with `category: "[[Law]]"`

- **Scorecard.** Run `python3 scripts/outreach_leads.py coverage-check --category Law`. Stdout includes `| notes |` and `VERDICT:`. Exit 0 or 2.
- **PI scope.** Run with `--practice pi`. Stdout includes `practice notes (pi)`.
- **Blind-spot.** If notes or site fill under threshold, stdout contains `NOT COVERED` and `NEXT:`, exit 2.
- **Proof.** Save scorecard stdout into `VERIFY.md` or a prove log. Pass = table numbers match a manual count sample; fail = exit not in {0,2} or missing VERDICT.

## Gotchas

- Matching `Law` inside `[[Law]]` is required; bare substring must not count Lawn notes.
- `website: ""` is missing for site fill.
- Exit 2 is a successful gate signal, not a script crash.

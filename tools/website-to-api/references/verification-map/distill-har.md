# Distill HAR

Distill turns a HAR into a secret-free recipe of replay candidates.

## Sub-features

- `distill-run` writes `references/examples/<site>-recipe.json`.
- `distill-strip` drops cookie and auth values.

## How to get to it (user POV)

- A capture HAR exists locally.

## Driving it with har_summarize

Preconditions:

- HAR path known.

- **Summarize.** Run `python3 scripts/har_summarize.py <har> -o references/examples/<site>-recipe.json`.
- **Proof.** Recipe JSON has `top_paths` and `warning` about no secrets. No `cookie=` values appear in the file.

## Gotchas

- Large `variables=` blobs may need hand-trimming to templates.

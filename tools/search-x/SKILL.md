---
name: search-x
description: Use when capturing X Search (Top / Latest / People / Media), not a single profile. Capture SearchTimeline, distill a secret-free recipe, and paginate. Profile archives stay in search-x-profile. Generic site recipes stay in website-to-api. Runs scripts/capture_search.py.
---

# Search X

Capture an authorized X **Search** page, distill the GraphQL recipe, then paginate. This is the Search-page counterpart to [search-x-profile](../search-x-profile/SKILL.md). Generic site-to-API work stays in [website-to-api](../website-to-api/SKILL.md).

## Install

```bash
python3 -m pip install patchright
python3 -m patchright install chrome
python3 tools/search-x/scripts/capture_search.py --self-check
```

Signed-in Chrome is required. Never commit cookies. Dumps stay under `./data/search-x/<query-slug-tab>/`.

## When

- The job is X Search UI: Top, Latest, People, or Media
- You want capture → distill → replay of `SearchTimeline` (same spirit as website-to-api)
- Not a single profile timeline (that is search-x-profile)

## Hard rules

1. Operator-authorized sessions only. Stop on a login wall or challenge.
2. Recipes store URL templates, operationNames, queryIds, and header names only. Never write cookies, tokens, or `x-client-transaction-id` values.
3. Attach with `--cdp` when Chrome is already signed in. Otherwise launch headed Chrome with `channel=chrome`, `headless=False`, `no_viewport=True`.
4. Human scroll is bootstrap fingerprint only. Paginate GraphQL with the bottom cursor after the first search page.
5. Honor `x-rate-limit-remaining` and `x-rate-limit-reset`. Exit 3 on a soft block.
6. Write dumps under `./data/search-x/<slug>/`. Never commit posts JSON.
7. After each successful page, flush `posts.json` and `checkpoint.json`. Write a temp file, then rename it.
8. On startup, load those files and resume from `bottom_cursor` when present.

## CLI

```bash
python3 tools/search-x/scripts/capture_search.py \
  --query 'texas hvac' \
  --tab latest \
  --cdp http://127.0.0.1:9225

python3 tools/search-x/scripts/capture_search.py \
  --query 'from:example' \
  --tab people \
  --user-data-dir "$CHROME_PROFILE"
```

`--tab` is `top` | `latest` | `people` | `media`. Latest opens `https://x.com/search?q=…&src=typed_query&f=live`. People uses `f=user`. Media uses `f=image`. Top omits `f`.

`--max-posts 0` (default) paginates until the bottom cursor is gone. `--min-delay` / `--max-delay` set the 0.5 to 1.5 s gap between pages.

`--out` defaults to `./data/search-x`. Each run flushes `posts.json` and `checkpoint.json` so a later run can resume.

Optional distill:

```bash
python3 tools/search-x/scripts/digest_results.py \
  --posts ./data/search-x/texas-hvac-latest/posts.json
```

## Capture path

1. Listen for `api.x.com/graphql` and `/i/api/graphql`.
2. Open the Search URL for the query + tab.
3. Capture `SearchTimeline` when it appears. Fall back to `SearchAdaptive`.
4. Keep queryId, the variables/features template, and the bottom cursor in memory. Do not write the cookie jar.
5. One XCli-shaped wheel burst for fingerprint. Paginate the rest via GraphQL.

Exit 2 on a login wall. Replay needs the live session (website-to-api spirit). If GraphQL rejects a missing `x-client-transaction-id`, use [XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) for that header only.

When `x-rate-limit-remaining` is 0 after a 200, keep that page, sleep until `x-rate-limit-reset` plus jitter, then continue. Repeat for every later window. Exit 3 if still blocked.

## Exit codes

- 0. Archive wrote posts
- 1. Fail
- 2. Login wall
- 3. Rate limited or soft block

## Layout

```
search-x/
  SKILL.md
  references/stack.md
  references/rate-limits.md
  scripts/capture_search.py
  scripts/digest_results.py
```

## Done means

- `python3 scripts/capture_search.py --self-check` prints `self-check ok`
- Self-check covers per-page flush, resume from the checkpoint cursor, and an atomic write that keeps prior posts
- Live run names the posts path and an XCli-aligned exit code
- Recipe draft has header names and no cookie values

---
name: search-x-profile
description: Use when archiving one X profile or writing a voice/pattern digest from posts, or when capturing UserOriginalsTimeline. Do not use for generic site-to-API recipes (website-to-api) or X Search pages (search-x). Runs scripts/capture_profile.py.
---

# Search X profile

Archive one authorized X profile, then optionally write a short voice/pattern note. This is not [website-to-api](../website-to-api/SKILL.md). That leaf stays on generic site-capture recipes. For Search UI (Top / Latest / People) use [search-x](../search-x/SKILL.md).

## Install

```bash
python3 -m pip install patchright
python3 -m patchright install chrome
python3 tools/search-x-profile/scripts/capture_profile.py --self-check
```

Signed-in Chrome is required. Never commit cookies. Dumps stay under `./data/search-x-profile/<handle>/`.

## When

- Posts from `@handle` for a voice digest or archive
- The job is `UserOriginalsTimeline` pagination, not a one-off screenshot
- Not an X Search page (that is search-x)

## Hard rules

1. Operator-authorized sessions only. Stop on a login wall or challenge. Do not bypass paywalls or accounts the operator does not own.
2. Recipes store URL templates, operationNames, queryIds, and header names only. Never write cookies, tokens, or `x-client-transaction-id` values.
3. Attach with `--cdp` when Chrome is already signed in. Otherwise launch headed Chrome with `channel=chrome`, `headless=False`, `no_viewport=True`. No custom user-agent soup.
4. Human scroll is bootstrap fingerprint only. Paginate GraphQL with the bottom cursor after the first timeline page.
5. Honor `x-rate-limit-remaining` and `x-rate-limit-reset`. Exit 3 on a soft block. See [references/rate-limits.md](references/rate-limits.md).
6. Write dumps under `./data/search-x-profile/<handle>/`. That path stays off git. Never commit posts JSON.
7. After each successful timeline page, flush `posts.json` and `checkpoint.json`. Write a temp file, then rename it. Do not truncate the live file first.
8. On startup, load `posts.json` and `checkpoint.json` when they exist. Seed seen ids from posts. If `checkpoint.json` has a `bottom_cursor`, resume pagination from that cursor. Bootstrap from the top only when there is no cursor to resume. A page of already-seen posts is not the end. Stop when the bottom cursor is gone, the API page has no posts, or five pages in a row add no new ids.

## Recipe

Stack and header names live in [references/stack.md](references/stack.md). Rate windows live in [references/rate-limits.md](references/rate-limits.md).

### 1. Attach

Prefer a live Chrome DevTools port.

```bash
python3 tools/search-x-profile/scripts/capture_profile.py \
  --handle example \
  --cdp http://127.0.0.1:9225
```

If Chrome is not already open, omit `--cdp` and pass `--user-data-dir` for the signed-in profile. The script calls `launch_persistent_context(channel=chrome, headless=False, no_viewport=True)`.

`--max-posts 0` (default) paginates until the bottom cursor is gone. A positive N stops when unique posts on disk plus this run reach N. `--max-pages` is an optional page cap including pages already on disk. `--min-delay` / `--max-delay` set the 0.5 to 1.5 s gap between pages.

`--out` defaults to `./data/search-x-profile`. Posts land in `<out>/<handle>/posts.json`. After each successful page the script flushes `posts.json` and `checkpoint.json`. Each write goes to a temp file, then the script renames it. A later run loads those files and resumes from `bottom_cursor`.

### 2. Bootstrap

1. Listen for `api.x.com/graphql` and `/i/api/graphql`.
2. Open `https://x.com/<handle>`.
3. Capture `UserOriginalsTimeline` when it appears. Fall back to `UserTweets`.
4. Keep queryId, the variables/features template, and the bottom cursor in memory. Do not write the request cookie jar.
5. Run one XCli-shaped wheel burst so the first page does not look like a dead tab. Do not scroll the rest of the archive.

Exit 2 if the page is a login wall.

### 3. Paginate

Replay the same GraphQL operation on the same browser context with `page.request` (or in-page fetch). Update only the bottom cursor. Copy live request headers in memory. Do not persist them.

If GraphQL rejects the replay because `x-client-transaction-id` is missing, use [XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) for that header only. Do not use it for scrolling.

When `x-rate-limit-remaining` is 0 after a 200, keep that page, sleep until `x-rate-limit-reset` plus jitter, then continue. Repeat for every later window. When status is 429, sleep and retry that cursor once. Exit 3 if the 429 retry is still blocked, or if remaining is 0 with no usable reset.

### 4. Write

Each successful page writes `posts.json` and `checkpoint.json` under `<out>/<handle>/`. The write is a temp file, then the script renames it. A crash, kill, or rate wait must leave every page already fetched on disk.

`posts.json` holds posts, page sizes, and the last rate-limit header numbers. `checkpoint.json` holds handle, query_id, bottom_cursor, page_count, last_rate, and the post_ids count. `recipe-draft.json` holds the secret-free template.

A later run loads `posts.json` and `checkpoint.json`, keeps existing posts, and continues from `bottom_cursor`. It does not walk from the top and overwrite. `--max-posts` counts unique posts already on disk plus new ones.

### 5. Voice digest (optional)

```bash
python3 tools/search-x-profile/scripts/digest_voice.py \
  --posts ./data/search-x-profile/example/posts.json
```

The stub writes a short pattern note next to the posts file.

## Exit codes

Aligned with [XCli](https://github.com/vermarjun/XCli).

- 0. Archive wrote posts
- 1. Fail (bad args, no attach, no timeline op, other errors)
- 2. Login wall
- 3. Rate limited or soft block

## Layout

```
search-x-profile/
  SKILL.md
  references/stack.md
  references/rate-limits.md
  scripts/capture_profile.py
  scripts/digest_voice.py
```

Dumps stay under `./data/search-x-profile/<handle>/` and stay off git.

## Done means

- `python3 scripts/capture_profile.py --self-check` prints `self-check ok`
- Self-check covers per-page flush, resume from the checkpoint cursor, and an atomic write that keeps prior posts
- A live run names the posts path and an XCli-aligned exit code
- The recipe draft has header names and no cookie values

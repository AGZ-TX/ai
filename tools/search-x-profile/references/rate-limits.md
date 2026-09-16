# Rate limits

Live-observed on `UserOriginalsTimeline` during authorized capture. Not an official X document. Re-check headers on the next run if the window moves.

## Observed window

Fetched 2026-09-16 on a signed-in Chrome session.

| Header | Observed |
|--------|----------|
| `x-rate-limit-limit` | 250 |
| `x-rate-limit-remaining` | counts down on each GraphQL page |
| `x-rate-limit-reset` | Unix time of the next window |

Posts per GraphQL page landed in the 21 to 27 range. Requested `count` in the captured variables was 20. Extra cards (pinned, conversation) push the extracted post count above 20.

250 pages at about 24 posts is a large archive in one window. A busy handle can still exhaust remaining before the cursor ends.

## Honor the headers

1. Read `x-rate-limit-remaining` and `x-rate-limit-reset` on every timeline response.
2. When remaining is 0 after a 200, keep that page, then sleep until reset plus 1 to 5 s jitter before the next cursor call. Repeat for every later window.
3. When status is 429, sleep until reset plus jitter and retry that cursor once.
4. If the 429 retry is still blocked, keep the flushed files and exit 3.
5. If remaining is 0 and there is no usable reset, keep the flushed files and exit 3.

Missing reset on a 429 falls back to the XCli soft-block wait of 30 s plus jitter, then one retry.

## Flush and resume

Incremental flush and resume is mandatory for large archives. A 12k run that dies in a rate wait must already have every fetched page on disk.

1. After each 200 timeline page, flush `posts.json` and `checkpoint.json` under `./data/search-x-profile/<handle>/`. Write a temp file, then rename it. Do not truncate the live file first.
2. Flush before any rate wait. A kill during the wait must not lose that page.
3. The next run loads those files. It seeds seen ids from `posts.json`. If `checkpoint.json` has `bottom_cursor`, it resumes from that cursor. It does not walk from the top and overwrite.
4. `--max-posts` counts unique posts already on disk plus new ones.

## Soft block

XCli treats `/account/access` and a "Something went wrong" body with no `primaryColumn` as a rate limit. This skill does the same and exits 3. Do not open new profiles or rotate user-agents.

## Exit 3

XCli-aligned. `0` ok, `1` fail, `2` login wall, `3` rate limited. Agents back off. They do not hammer the handle.

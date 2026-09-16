---
name: website-to-api
description: Use when a site task is slow or token-heavy because agents click and screenshot a known path, or when turning a site into an API. Also use for authorized programmatic capture on heavy bot-detection sites such as X or LinkedIn. Capture GraphQL JSON with Patchright response hooks, then replay the same calls without the UI.
---

# Website to API

Goal: turn a familiar, correct click path into a small API recipe so later runs skip screenshots and UI.

## Install

```bash
python3 -m pip install patchright
python3 -m patchright install chrome
```

Camoufox is the named fallback in [references/human-input.md](references/human-input.md). Never commit cookies.

## When

- The path is known and has been done correctly at least once (or this run will establish it).
- UI automation burns tokens (many screenshots, tabs, waits).
- The site challenges headless browsers (X, LinkedIn) and this run is an authorized capture of a session the operator uses.

Do not use for one-off unfamiliar sites. Explore first, then apply this skill.

## Hard rules

1. Capture only after the success state is visible (data on screen, not the loading shell).
2. Attach `page.on('response')` or `waitForResponse` before the path. Write GraphQL JSON under `./data/<site>-capture/` (or `$WEBSITE_TO_API_OUT`). Distill from URLs and operationNames. Bootstrap-only captures fail. DevTools Save HAR is last fallback when Patchright cannot attach to the session.
3. Never commit cookies, tokens, capture JSON, or HARs with secrets to git. Recipes store URL patterns, methods, queryIds, operationNames, and header names only.
4. Prefer a signed-in Chrome session, including its cookies. Do not start a headless fresh profile on heavy bot-detection sites.
5. Prove replay matches the UI result before deleting the UI path from the playbook.
6. Authorized session capture of sites the operator uses. Do not use it to bypass paywalls or to access accounts they do not own.
7. If a challenge or login wall appears, stop and report. Do not brute force.

## Phases

### 1. Name the path

Write one line: site, start URL, end success state, auth needed.

Example: X profile posts. Start at `https://x.com/example`. Success is timeline posts in GraphQL. Auth is a signed-in X session.

Example: LinkedIn company People. Start at the company URL. Success is employee cards with titles. Auth is a signed-in LinkedIn session.

### 2. Capture (hooks default)

```bash
python3 scripts/capture_x_profile.py --handle example --cdp http://127.0.0.1:9225
# writes ./data/website-to-api-capture/example-posts.json
# writes ./data/website-to-api-capture/example-recipe-draft.json
```

1. Launch Patchright headed Chrome with a persistent context on the signed-in profile. Use `channel=chrome`. Do not set a custom user-agent.
2. Attach `page.on('response')` for `api.x.com/graphql` (and `x.com/i/api/graphql`).
3. Open the start URL. Exit non-zero on a login wall.
4. Human-scroll N bursts from [Heavy bot detection](#heavy-bot-detection-x-linkedin-etc) while the listener stays on.
5. Write JSON under `./data/<site>-capture/` (gitignored). Distill the recipe from captured URLs and operationNames.

Optional on the same Patchright context. Playwright `recordHar` writes a HAR the API owns. Later `context.routeFromHAR` may replay that file.

```bash
python3 scripts/har_summarize.py ./data/<site>-capture/*.har -o references/examples/<site>-recipe.json
```

### 3. Distill

Keep in the recipe:

- method + URL template (path + stable query keys such as `queryId`)
- operationNames that match the success state (`UserByScreenName`, `UserTweets`, `SearchTimeline`, and the like)
- required header names (not values)
- how to refresh auth (cookie jar path, not cookie values)

Drop noise: analytics, presence, badges, messaging pings.

### 4. Replay

Add `scripts/replay_<site>.py` (or a mode in the owning skill) that:

1. Loads session auth from the live browser profile or a local jar the operator approved
2. Calls only the distilled endpoints
3. Writes structured output the agent needs
4. Exits non-zero if auth dies or shape drifts

Wire the owning skill (for example [outreach-leads](../outreach-leads/SKILL.md) contacts layer, or [search-x](../search-x/SKILL.md) / [search-x-profile](../search-x-profile/SKILL.md)) to prefer replay when a recipe exists.

For X GraphQL that rejects requests without `x-client-transaction-id`, generate that header with [iSarabjitDhiman/XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction). Do not use it for scrolling.

### 5. Prove

- Same inputs produce replay output that matches the capture screenshot facts
- If names or fields stay masked in UI, record that limit in the recipe (do not invent data)

## Heavy bot detection (X, LinkedIn, etc.)

Human scroll is for the capture phase only. Replay uses distilled endpoints and the session jar.

### Session

Prefer a real signed-in Chrome session and its cookies. Use headed Chrome with a persistent context. Do not invent a custom user-agent or fingerprint soup.

### Canonical stack

1. Browser, primary. [Kaliiiiiiiiii-Vinyzu/patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) and [patchright-python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). Headed Chrome with `channel=chrome` and a persistent context.
2. Browser, fallback. [daijro/camoufox](https://github.com/daijro/camoufox) when Patchright still fails challenges.
3. Human mouse and scroll. [Xetera/ghost-cursor](https://github.com/Xetera/ghost-cursor). The Python X capture script encodes the same burst numbers on `page.mouse.wheel`.
4. X-shaped reference. [vermarjun/XCli](https://github.com/vermarjun/XCli). Study only. Do not vendor the CLI as the replay path.
5. Replay header helper. [iSarabjitDhiman/XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) only when X GraphQL replay needs `x-client-transaction-id`.

Defaults live in [references/human-input.md](references/human-input.md).

### Scroll recipe

1. Attach GraphQL response hooks, then open the start URL.
2. Scroll in bursts of 7 to 24 wheel events with variable deltas and intra-burst gaps.
3. About 10 percent of bursts include a reverse overshoot (150 to 400 px up), then a correction.
4. Pause between bursts: skim 0.4 to 1.0 s, browse 0.8 to 2.0 s, deep 1.5 to 3.5 s.
5. Stop when the success state is visible. Write the captured JSON.

### Fail closed

If a challenge or login wall appears, stop and report. Do not retry with new profiles or user-agent tricks.

## Layout

```
website-to-api/
  SKILL.md
  scripts/capture_x_profile.py
  scripts/har_summarize.py
  references/human-input.md
  references/verification-map/
  references/examples/
```

Per-site captures stay under `./data/<site>-capture/` and stay off git.

## Done means

- Recipe file exists without secrets
- Replay script or owning-skill mode can run cold
- A challenge or login wall stopped the run with a report, not a bypass attempt

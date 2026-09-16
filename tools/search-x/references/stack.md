# Stack

Locked capture stack for authorized X Search archives. Study notes only. These tools do not make a session undetectable.

Use this only for operator-authorized sessions. Do not use it to bypass paywalls or to access accounts they do not own.

## Browser

1. Primary. Patchright. [Kaliiiiiiiiii-Vinyzu/patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) and [patchright-python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). Drop-in Playwright.
2. Attach first with `--cdp` on a signed-in Chrome. Default is `http://127.0.0.1:9225`. Bare `--cdp` uses that URL.
3. If CDP is not available, `launch_persistent_context(channel=chrome, headless=False, no_viewport=True)` on the signed-in profile. Do not set a custom user-agent.
4. Fallback. Camoufox. [daijro/camoufox](https://github.com/daijro/camoufox) when Patchright still fails challenges.

## Human scroll

Bootstrap fingerprint only. Do not scroll the full search result set.

1. Primary mouse library. [Xetera/ghost-cursor](https://github.com/Xetera/ghost-cursor).
2. X-shaped reference. [vermarjun/XCli](https://github.com/vermarjun/XCli) `xcli/core/human.py`. Burst length 7 to 24 wheel events, about 10 percent reverse overshoot 150 to 400 px, skim pause 0.4 to 1.0 s. Study only.
3. The Python capture script encodes those burst numbers on `page.mouse.wheel` for the first page only.

## GraphQL replay

Paginate `SearchTimeline` (fallback `SearchAdaptive`) with the captured queryId, variables/features template, and bottom cursor on the same browser context.

[iSarabjitDhiman/XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) only when that replay needs `x-client-transaction-id`. Not for scrolling. Not a default import.

## Secrets

Recipes store URL templates, operationNames, queryIds, and header names. Cookie, authorization, csrf, and transaction-id values stay in the live context.

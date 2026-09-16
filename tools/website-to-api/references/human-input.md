# Human input

Lookup for authorized capture on heavy bot-detection sites. Human scroll belongs to the capture phase. Replay uses distilled endpoints and the session jar.

These tools reduce robotic fingerprints during authorized capture. They do not make a session undetectable.

Use this only for operator-authorized sessions of sites they use. Do not use it to bypass paywalls or to access accounts they do not own.

## Canonical stack

1. Browser, primary. [Kaliiiiiiiiii-Vinyzu/patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) and [patchright-python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). Drop-in Playwright. Headed Chrome, `channel=chrome`, persistent context. No custom user-agent or fingerprint soup.
2. Browser, fallback. [daijro/camoufox](https://github.com/daijro/camoufox) when Patchright still fails challenges. C++ Firefox. Heavier.
3. Human mouse and scroll, primary. [Xetera/ghost-cursor](https://github.com/Xetera/ghost-cursor). Canonical Bezier human input. For Playwright, a maintained ghost-cursor-playwright fork or [CloverLabsAI/human-cursor](https://github.com/CloverLabsAI/human-cursor) for momentum scroll. The Python X capture script uses the same burst numbers on `page.mouse.wheel`.
4. X-shaped reference. [vermarjun/XCli](https://github.com/vermarjun/XCli). Study Patchright, headed Chrome, humanized scroll bursts, and DOM-only profile pull. Do not vendor the CLI as the replay path.
5. Replay header helper. [iSarabjitDhiman/XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) only when X GraphQL replay needs `x-client-transaction-id`. Not for scrolling.

Do not recommend as primary: rebrowser-patches, HumaniPy or random scraper bots, stealth-x or x-crawlfox as required deps (optional Camoufox examples only), shy-mouse as equal to ghost-cursor.

## Scroll defaults

Source: [XCli `xcli/core/human.py`](https://github.com/vermarjun/XCli/blob/main/xcli/core/human.py).

- Burst length: 7 to 24 `page.mouse.wheel` events
- Deltas and intra-burst gaps vary. No fixed equal jumps
- About 10 percent of bursts reverse-overshoot 150 to 400 px up, then continue
- Read pause after a burst: skim 0.4 to 1.0 s, browse 0.8 to 2.0 s, deep 1.5 to 3.5 s
- Disabled human pace falls back to one wheel event. Do not use that for capture

## Capture hygiene

Hooks first. HAR UI last.

1. Launch Patchright headed Chrome with a persistent context on the signed-in profile. Use `channel=chrome`.
2. Attach `page.on('response')` or `waitForResponse` for GraphQL before the path. On X, listen for `api.x.com/graphql`.
3. Human-scroll with the defaults above until the success state is visible. Keep the listener on.
4. Write JSON under `./data/<site>-capture/`. Distill the recipe from URLs and operationNames. Do not open a DevTools Save File dialog.
5. Optional. Playwright `recordHar` on the same context. Use `context.routeFromHAR` only with a HAR that API wrote.
6. DevTools Save HAR only if Patchright cannot attach to the session.
7. Recipes store URL patterns, operationNames, and header names only. Do not commit capture files or cookies.

If a challenge or login wall appears, stop and report. Do not brute force.

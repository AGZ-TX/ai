# Capture path

Capture records one correct UI path as HAR plus a success screenshot.

## Sub-features

- `capture-clear` clears Network before the path.
- `capture-success` exports HAR only after success state is visible.
- `capture-auth` notes that a signed-in session is required (never commit cookies).

## How to get to it

- An operator names an expensive site or asks to turn a path into an API.
- Agent already knows the correct click path.

## Driving it

Preconditions:

- Signed into the required account.
- DevTools Network open with Preserve log, or Patchright hooks attached.

- **Clear.** Clear the Network log / attach hooks.
- **Walk.** Run the known path to the success state.
- **Export.** Save HAR under `./data/<site>-capture/`.
- **Proof.** Screenshot shows the success state. HAR entry count is non-trivial for the success calls, not feed-only bootstrap.

## Gotchas

- Exporting HAR on the feed or home shell fails the skill (LinkedIn lesson).
- Do not commit the HAR.

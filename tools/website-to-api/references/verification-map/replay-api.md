# Replay API

Replay hits distilled endpoints with live auth and skips the UI.

## Sub-features

- `replay-auth` loads session cookies/csrf from the live profile.
- `replay-call` requests only recipe candidates that carry the payload.
- `replay-prove` checks output against capture facts.

## How to get to it (user POV)

- Recipe exists and auth session is alive.

## Driving it with replay script

Preconditions:

- Recipe path and owning skill mode agreed.

- **Replay.** Run the site replay script or owning-skill mode.
- **Proof.** Structured output matches capture screenshot facts. Exit non-zero on auth death or shape drift.

## Gotchas

- Masked UI fields stay masked in replay unless a richer endpoint exists. Record the limit.

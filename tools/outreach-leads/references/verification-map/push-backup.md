# push-backup

push-backup syncs `./data/` → `BACKUP_REPO` `outreach/data/` via **branch → PR → merge** (never direct push to `main`). Runs automatically after successful vault-mutating specialty-directory / enrich write+apply. Manual mode available. Never force-pushes. Never uses a dirty checkout.

## Sub-features

- `pb-clean-worktree` fetches `origin/main` into a fresh worktree; refuses dirty checkouts as dest.
- `pb-sync` mirrors vault into `outreach/data/`, excluding `.obsidian`, `.trash`, caches/temp.
- `pb-commit` commits only `outreach/data`; no-op when unchanged.
- `pb-pr` commits on `backup/outreach-data-YYYYMMDD` (or `backup/outreach-YYYYMMDD-HHMM` on same-day collision), `gh pr create`, merge, short PR comment; prints PR URL + merge commit. Never `git push origin main`.
- `pb-dry-run` syncs into clean worktree and shows staged paths; no commit, no PR, no push.
- `pb-skip` dry-run / `--no-push` / no vault mutation → no PR / no push.

## How to get to it

- `python3 scripts/outreach_leads.py push-backup`
- `python3 scripts/outreach_leads.py push-backup --dry-run`
- Auto: after specialty-directory creates/updates notes; after enrich website fill; after contacts `--apply` writes.
- Debug only: `--no-push` on mutation modes.

## Driving it with outreach_leads

Preconditions:

- `doctor` PASS (imports include `push_backup`)
- Network + GitHub auth for real PR merge
- Prefer `--dry-run` or a temp bare repo for smoke (do not create extra production commits while another executor is pushing)

- **Dry-run.** `push-backup --dry-run`. Stdout: `checkout	clean-worktree`, `push-backup	dry-run	no commit	no pr	no push` (or `no-op`). Exit 0. No GitHub PR/commit.
- **Dirty refuse.** Point `--refuse-dirty` at a dirty worktree → exit 2, `dirty_checkout	refused`. Dest path is never that checkout.
- **Mutation request.** specialty-directory / enrich write path without `--dry-run` prints `push-backup	requested` after real creates/updates (or `skip` when none). Dry-run mutation modes print `push-backup	skip	dry-run`.
- **Proof.** Pass = dry-run never opens a PR; mutation path requests backup after real writes; dirty checkout unused; prove line has `pr=` + `merge=`. Fail = dry-run pushes/PRs, or backup uses a dirty checkout, or seat data is pushed straight to `main`.

## Gotchas

- Auto-backup only after successful real note writes. Prove/JSONL-only and queue-only do not push.
- `--no-push` is for tests/debug, not normal runs (commits locally; no PR).
- Commit scope is `outreach/data` only.
- Seat data always lands via PR merge — never `git push origin main`.

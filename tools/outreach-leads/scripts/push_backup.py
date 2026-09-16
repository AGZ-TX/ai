#!/usr/bin/env python3
"""Optional vault backup to a git remote via branch → PR → merge.

Always fetch origin/main into a clean worktree. Never reuse a dirty checkout.
Never push vault data directly to main.
Dry-run / no-op / --no-push never create a PR or push.
Commit only outreach/data. Requires BACKUP_REPO or --repo.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from note_io import DEFAULT_VAULT

DEFAULT_REPO = os.environ.get("BACKUP_REPO") or os.environ.get("OUTREACH_BACKUP_REPO") or ""
DEST_PREFIX = "outreach/data"
BRANCH = "main"
TZ = ZoneInfo(os.environ.get("VAULT_TZ", "America/Chicago"))

EXCLUDE_DIR_NAMES = {
    ".obsidian",
    ".trash",
    ".Trash",
    ".git",
    "__pycache__",
    ".cache",
    ".tmp",
    "node_modules",
}
EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmp", ".temp", ".swp", ".swo"}


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if check and p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise GitError(f"git {' '.join(args)} failed: {err}")
    return p


def run_cmd(
    args: list[str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    p = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if check and p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise GitError(f"{' '.join(args)} failed: {err}")
    return p


def resolve_repo(repo: str) -> str:
    raw = (repo or "").strip() or DEFAULT_REPO
    p = Path(raw)
    if p.exists():
        return str(p.resolve())
    return raw


def github_slug(repo: str) -> str | None:
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", repo.replace("git@", "github.com/"))
    return m.group(1) if m else None


def commit_url(repo: str, sha: str) -> str:
    slug = github_slug(repo)
    if slug:
        return f"https://github.com/{slug}/commit/{sha}"
    return f"{repo}#{sha}"


def is_github_remote(repo: str) -> bool:
    return github_slug(repo) is not None


def skip_rel(rel: Path) -> bool:
    if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
        return True
    name = rel.name
    if name in EXCLUDE_FILE_NAMES:
        return True
    if name.startswith("~$") or name.endswith("~"):
        return True
    if rel.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def is_work_tree(path: Path) -> bool:
    if not path.is_dir():
        return False
    p = git(["rev-parse", "--is-inside-work-tree"], cwd=path, check=False)
    return p.returncode == 0 and p.stdout.strip() == "true"


def is_dirty(path: Path) -> bool:
    if not is_work_tree(path):
        return False
    p = git(["status", "--porcelain"], cwd=path)
    return bool(p.stdout.strip())


def refuse_if_dirty(path: Path) -> None:
    if is_dirty(path):
        print(f"FAIL\tdirty-checkout\t{path}", file=sys.stderr)
        print("dirty_checkout\trefused")
        raise SystemExit(2)


def ensure_bare(repo: str, cache: Path) -> Path:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if (cache / "HEAD").exists() or (cache / "objects").exists():
        git(["remote", "set-url", "origin", repo], cwd=cache, check=False)
        # Force-update remote-tracking ref so stale refs/heads/main cannot win.
        r = git(
            ["fetch", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}"],
            cwd=cache,
            check=False,
        )
        if r.returncode != 0:
            r2 = git(["fetch", "origin", "--update-head-ok"], cwd=cache, check=False)
            if r2.returncode != 0:
                git(["show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}"], cwd=cache)
        return cache
    git(["clone", "--bare", repo, str(cache)])
    return cache


def origin_main_sha(cache: Path) -> str:
    # Prefer remotes/origin/main (and FETCH_HEAD) over local refs/heads/main — bare
    # caches often leave heads/main lagging after a failed push or local commit.
    for ref in (
        f"refs/remotes/origin/{BRANCH}",
        "FETCH_HEAD",
        f"origin/{BRANCH}",
        f"refs/heads/{BRANCH}",
        BRANCH,
    ):
        p = git(["rev-parse", "--verify", ref], cwd=cache, check=False)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip().split()[0]
    raise GitError(f"cannot resolve {BRANCH} in {cache}")


def add_clean_worktree(cache: Path, wt: Path, sha: str) -> None:
    if wt.exists():
        git(["worktree", "remove", "--force", str(wt)], cwd=cache, check=False)
        shutil.rmtree(wt, ignore_errors=True)
    git(["worktree", "add", "--detach", str(wt), sha], cwd=cache)
    if is_dirty(wt):
        raise GitError(f"worktree dirty immediately after checkout: {wt}")
    # local identity only — never touches global git config
    git(["config", "user.email", "vault-backup@localhost"], cwd=wt)
    git(["config", "user.name", "Outreach CRM Backup"], cwd=wt)


def sync_vault(src: Path, dest: Path) -> tuple[int, int, int]:
    dest.mkdir(parents=True, exist_ok=True)
    src_files: set[Path] = set()
    copied = 0
    for p in src.rglob("*"):
        if not p.is_file() and not p.is_symlink():
            continue
        rel = p.relative_to(src)
        if skip_rel(rel):
            continue
        src_files.add(rel)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink():
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(os.readlink(p))
            copied += 1
            continue
        if not target.exists() or target.read_bytes() != p.read_bytes():
            shutil.copy2(p, target)
            copied += 1
    removed = 0
    for p in dest.rglob("*"):
        if not p.is_file() and not p.is_symlink():
            continue
        rel = p.relative_to(dest)
        if skip_rel(rel) or rel not in src_files:
            p.unlink()
            removed += 1
    for d in sorted((x for x in dest.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        try:
            next(d.iterdir())
        except StopIteration:
            if d != dest:
                d.rmdir()
    return copied, removed, len(src_files)


def remove_worktree(cache: Path, wt: Path) -> None:
    git(["worktree", "remove", "--force", str(wt)], cwd=cache, check=False)
    shutil.rmtree(wt, ignore_errors=True)
    git(["worktree", "prune"], cwd=cache, check=False)


def remote_branch_exists(cache: Path, branch: str) -> bool:
    # Refresh backup refs opportunistically (best-effort).
    git(
        ["fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
        cwd=cache,
        check=False,
    )
    for ref in (f"refs/remotes/origin/{branch}", f"origin/{branch}"):
        p = git(["rev-parse", "--verify", ref], cwd=cache, check=False)
        if p.returncode == 0 and p.stdout.strip():
            return True
    # Also ask ls-remote when network available.
    ls = git(["ls-remote", "--heads", "origin", branch], cwd=cache, check=False)
    if ls.returncode == 0 and ls.stdout.strip():
        return True
    return False


def choose_backup_branch(cache: Path, now: datetime | None = None) -> str:
    """backup/outreach-data-YYYYMMDD, or backup/outreach-YYYYMMDD-HHMM on collision."""
    now = now or datetime.now(TZ)
    day = now.strftime("%Y%m%d")
    primary = f"backup/outreach-data-{day}"
    if not remote_branch_exists(cache, primary):
        return primary
    hhmm = now.strftime("%H%M")
    return f"backup/outreach-{day}-{hhmm}"


def pr_body(paths: list[str], branch: str) -> str:
    lines = [
        "## Outreach vault backup",
        "",
        "Immediate post-mutation sync of the local vault → `outreach/data`.",
        "",
        f"- Branch: `{branch}`",
        "- Why: vault write completed; seat data must land via PR (never direct push to main).",
        "",
        "### Paths synced",
        "",
    ]
    for p in paths[:80]:
        lines.append(f"- `{p}`")
    if len(paths) > 80:
        lines.append(f"- …and {len(paths) - 80} more")
    lines.append("")
    return "\n".join(lines)


def parse_pr_number(url_or_text: str) -> int | None:
    m = re.search(r"/pull/(\d+)", url_or_text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)\b", url_or_text.strip())
    if m:
        return int(m.group(1))
    return None


def push_via_pr(
    *,
    wt: Path,
    cache: Path,
    repo: str,
    sha: str,
    staged_paths: list[str],
) -> tuple[str, str, str]:
    """Push backup branch, open PR, merge, comment. Returns (pr_url, merge_sha, branch)."""
    branch = choose_backup_branch(cache)
    git(["checkout", "-B", branch], cwd=wt)

    remotes = git(["remote"], cwd=wt, check=False).stdout.split()
    if "origin" not in remotes:
        # Local bare smoke: push backup branch into cache only — never update main.
        push = git(["push", str(cache), f"HEAD:refs/heads/{branch}"], cwd=wt, check=False)
        if push.returncode != 0:
            raise GitError("local push to bare cache failed: " + (push.stderr or push.stdout).strip())
        print(f"pushed\tlocal-branch\t{branch}\t{sha}")
        print("push-backup\tlocal-bare\tno-pr")
        return ("", sha, branch)

    push = git(["push", "-u", "origin", f"HEAD:{branch}"], cwd=wt, check=False)
    if push.returncode != 0:
        raise GitError("push backup branch failed: " + (push.stderr or push.stdout).strip())
    print(f"pushed\tbranch\t{branch}\t{sha}")

    if not is_github_remote(repo):
        print("push-backup\tbranch-only\tnon-github-origin")
        return ("", sha, branch)

    title = f"backup: sync outreach/data ({branch.split('-')[-1] if '-' in branch else branch})"
    # Prefer day stamp in title when present.
    day_m = re.search(r"(\d{8})", branch)
    if day_m:
        title = f"backup: sync outreach/data ({day_m.group(1)})"
    body = pr_body(staged_paths, branch)

    created = run_cmd(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            github_slug(repo) or "",
            "--base",
            BRANCH,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=wt,
        check=False,
    )
    if created.returncode != 0:
        raise GitError("gh pr create failed: " + (created.stderr or created.stdout).strip())
    pr_url = (created.stdout or "").strip().splitlines()[-1].strip()
    pr_num = parse_pr_number(pr_url)
    if not pr_num:
        raise GitError(f"could not parse PR number from: {pr_url!r}")
    print(f"pr\t{pr_url}")

    merged = run_cmd(
        [
            "gh",
            "pr",
            "merge",
            str(pr_num),
            "--repo",
            github_slug(repo) or "",
            "--delete-branch",
        ],
        cwd=wt,
        check=False,
    )
    if merged.returncode != 0:
        raise GitError("gh pr merge failed: " + (merged.stderr or merged.stdout).strip())

    # Resolve merge / head commit on main after merge.
    git(
        ["fetch", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}"],
        cwd=cache,
        check=False,
    )
    merge_sha = origin_main_sha(cache)

    # Prefer the PR head SHA if merge commit not yet visible; fall back to our commit.
    view = run_cmd(
        [
            "gh",
            "pr",
            "view",
            str(pr_num),
            "--repo",
            github_slug(repo) or "",
            "--json",
            "mergeCommit,state,url",
            "--jq",
            ".mergeCommit.oid // .url",
        ],
        cwd=wt,
        check=False,
    )
    if view.returncode == 0 and view.stdout.strip():
        out = view.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{7,40}", out):
            merge_sha = out

    comment = (
        f"Merged outreach vault backup.\n"
        f"- Branch: `{branch}`\n"
        f"- Files: {len(staged_paths)} under `{DEST_PREFIX}`\n"
        f"- Merge commit: `{merge_sha}`\n"
    )
    run_cmd(
        [
            "gh",
            "pr",
            "comment",
            str(pr_num),
            "--repo",
            github_slug(repo) or "",
            "--body",
            comment,
        ],
        cwd=wt,
        check=False,
    )

    print(f"merged\t{merge_sha}")
    print(f"url\t{commit_url(repo, merge_sha)}")
    print(f"prove\tpr={pr_url}\tmerge={merge_sha}")
    return (pr_url, merge_sha, branch)


def maybe_after_mutation(
    *,
    mutated: bool,
    dry_run: bool = False,
    no_push: bool = False,
    vault: Path | str | None = None,
    extra_argv: list[str] | None = None,
) -> int:
    """Call after a real vault write. Dry-run / no writes never push or open a PR.

    --no-push (debug/tests): still report requested, then skip network push/PR.
    """
    if dry_run:
        print("push-backup\tskip\tdry-run")
        return 0
    if not mutated:
        print("push-backup\tskip\tno-vault-mutation")
        return 0
    print("push-backup\trequested")
    if no_push:
        print("push-backup\tskip\t--no-push")
        return 0
    repo = os.environ.get("BACKUP_REPO") or os.environ.get("OUTREACH_BACKUP_REPO") or DEFAULT_REPO
    extra = list(extra_argv or [])
    if not repo and not any(a in {"--repo"} or a.startswith("--repo=") for a in extra):
        if "--repo" not in extra:
            print("push-backup\tskip\tno BACKUP_REPO")
            return 0
    argv = ["--vault", str(vault or DEFAULT_VAULT)]
    if extra_argv:
        argv.extend(extra_argv)
    return int(run(argv) or 0)


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="push-backup")
    ap.add_argument("--vault", default=os.environ.get("OUTREACH_BACKUP_VAULT", str(DEFAULT_VAULT)))
    ap.add_argument("--repo", default=os.environ.get("OUTREACH_BACKUP_REPO", DEFAULT_REPO))
    ap.add_argument("--cache", default=os.environ.get("OUTREACH_BACKUP_CACHE", ""))
    ap.add_argument("--worktree", default=os.environ.get("OUTREACH_BACKUP_WORKTREE", ""))
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch + clean worktree + sync + status; no commit, no PR, no push",
    )
    ap.add_argument("--no-push", action="store_true", help="commit locally only (debug/test); no PR")
    ap.add_argument(
        "--refuse-dirty",
        default="",
        help="exit 2 if PATH is a dirty checkout (never used as dest)",
    )
    args = ap.parse_args(argv)

    vault = Path(args.vault)
    if not vault.is_dir():
        print(f"FAIL\tvault\t{vault}", file=sys.stderr)
        return 1

    if args.refuse_dirty:
        refuse_if_dirty(Path(args.refuse_dirty))
        print(f"dirty_checkout\trefused-check-clean\t{args.refuse_dirty}")

    if not (args.repo or "").strip() and not DEFAULT_REPO:
        print("FAIL\tno BACKUP_REPO / --repo", file=sys.stderr)
        return 1
    repo = resolve_repo(args.repo)
    repo_path = Path(repo)
    if args.cache:
        cache = Path(args.cache)
    elif repo_path.exists() and (repo_path / "HEAD").exists() and not (repo_path / ".git").exists():
        # local bare repo — use directly as cache (no nested clone)
        cache = repo_path
    else:
        cache = Path(tempfile.gettempdir()) / "outreach-leads-backup.git"
    wt = Path(args.worktree) if args.worktree else Path(tempfile.gettempdir()) / f"outreach-leads-backup-wt-{os.getpid()}-{int(time.time())}"

    # Never operate inside an existing dirty checkout.
    for candidate in (vault, wt if wt.exists() else None):
        if candidate is None:
            continue
        if is_work_tree(candidate) and is_dirty(candidate):
            print(f"dirty_checkout\tignored\t{candidate}")

    print(f"vault\t{vault}")
    print(f"repo\t{repo}")
    print(f"checkout\tclean-worktree")

    try:
        ensure_bare(repo, cache)
        sha0 = origin_main_sha(cache)
        print(f"origin/{BRANCH}\t{sha0}")
        add_clean_worktree(cache, wt, sha0)
        print(f"worktree\t{wt}")
        dest = wt / DEST_PREFIX
        copied, removed, nfiles = sync_vault(vault, dest)
        print(f"sync\tcopied={copied}\tremoved={removed}\tfiles={nfiles}\tdest={DEST_PREFIX}")

        git(["add", "-A", "--", DEST_PREFIX], cwd=wt)
        staged = git(["diff", "--cached", "--name-only", "--", DEST_PREFIX], cwd=wt).stdout.strip()
        other = git(["diff", "--cached", "--name-only"], cwd=wt).stdout.strip().splitlines()
        leaked = [p for p in other if p and not p.startswith(DEST_PREFIX + "/") and p != DEST_PREFIX]
        if leaked:
            print("FAIL\tstaged-outside-outreach/data\t" + ",".join(leaked), file=sys.stderr)
            return 1
        if not staged:
            print("push-backup\tno-op\tno changes")
            print(f"sha\t{sha0}")
            print(f"url\t{commit_url(repo, sha0)}")
            return 0

        staged_paths = [ln for ln in staged.splitlines() if ln]
        print("staged")
        for line in staged_paths[:40]:
            print(f"  {line}")

        if args.dry_run:
            print("push-backup\tdry-run\tno commit\tno pr\tno push")
            return 0

        msg = (
            "backup: sync vault to outreach/data\n\n"
            "Immediate post-mutation backup (outreach-leads) via branch→PR→merge."
        )
        git(["commit", "-m", msg, "--", DEST_PREFIX], cwd=wt)
        sha = git(["rev-parse", "HEAD"], cwd=wt).stdout.strip()
        print(f"commit\t{sha}")
        print(f"url\t{commit_url(repo, sha)}")

        if args.no_push:
            # Local-only: park commit on a backup branch ref in the bare cache.
            # Never update refs/heads/main — even for debug.
            branch = choose_backup_branch(cache)
            git(["--git-dir", str(cache), "update-ref", f"refs/heads/{branch}", sha], check=False)
            print(f"push-backup\tcommitted\tno-push\tbranch={branch}")
            print(f"sha\t{sha}")
            print(f"url\t{commit_url(repo, sha)}")
            return 0

        push_via_pr(
            wt=wt,
            cache=cache,
            repo=repo,
            sha=sha,
            staged_paths=staged_paths,
        )
        return 0
    except GitError as e:
        print(f"FAIL\t{e}", file=sys.stderr)
        return 1
    finally:
        if wt.exists():
            remove_worktree(cache, wt)


if __name__ == "__main__":
    sys.exit(run())

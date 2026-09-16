#!/usr/bin/env python3
"""Outreach-leads CLI — vault ingest/enrich modes."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PACK_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from note_io import DEFAULT_VAULT


def _run_module(name: str, argv: list[str]) -> int:
    path = SCRIPT_DIR / f"{name}.py"
    # Prefer importing run()/main when available; else subprocess for isolation
    spec = importlib.util.spec_from_file_location(name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        # ensure sibling imports resolve
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        spec.loader.exec_module(mod)
        if hasattr(mod, "run"):
            return int(mod.run(argv) or 0)
        if hasattr(mod, "main"):
            return int(mod.main(argv) or 0)
    return subprocess.call([sys.executable, str(path), *argv])


def cmd_doctor(args) -> int:
    vault = Path(args.vault)
    if getattr(args, "init", False):
        for rel in ("Businesses", "Sources/runs", "Sources/recipes", "Research/firms"):
            (vault / rel).mkdir(parents=True, exist_ok=True)
        print(f"init\tvault\t{vault}")
    ok = True
    print("## doctor")
    if vault.is_dir() and (vault / "Businesses").is_dir():
        print(f"OK\tvault\t{vault}")
    else:
        print(f"FAIL\tvault\t{vault}")
        ok = False
    sources = [
        vault / "Sources" / "recipes" / "sources.json",
        PACK_ROOT / "references" / "sources.json",
    ]
    loaded = None
    for s in sources:
        if s.exists():
            try:
                loaded = json.loads(s.read_text())
                print(f"OK\tsources.json\t{s}\trecipes={len(loaded.get('sources', {}))}")
                break
            except Exception as e:
                print(f"FAIL\tsources.json\t{s}\t{e}")
                ok = False
    if loaded is None:
        print("FAIL\tsources.json\tnot found")
        ok = False
    else:
        pack_blob = (PACK_ROOT / "references" / "sources.json").read_text() if (PACK_ROOT / "references" / "sources.json").exists() else ""
        if "3gg6-9t7n" in pack_blob:
            print("FAIL\tcomptroller\tmetro extract 3gg6-9t7n")
            ok = False
        elif "jrea-zgmq" not in pack_blob:
            print("FAIL\tcomptroller\tmissing statewide jrea-zgmq")
            ok = False
        else:
            print("OK\tcomptroller\tjrea-zgmq")
    cov = PACK_ROOT / "references" / "coverage.json"
    if cov.exists():
        try:
            c = json.loads(cov.read_text())
            print(f"OK\tcoverage.json\trows={len(c.get('rows', []))}")
        except Exception as e:
            print(f"FAIL\tcoverage.json\t{e}")
            ok = False
    else:
        print("FAIL\tcoverage.json\tmissing")
        ok = False
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    for mod in ("verify_website", "coverage_check", "note_io", "fetch_recipe", "enrich", "specialty_directory", "linkedin_contacts", "website_emails", "build_poc", "push_backup"):
        try:
            __import__(mod)
            print(f"OK\timport\t{mod}")
        except Exception as e:
            print(f"FAIL\timport\t{mod}\t{e}")
            ok = False
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_coverage_check(argv: list[str]) -> int:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import coverage_check

    return coverage_check.main(argv)


def cmd_fetch(argv: list[str]) -> int:
    return subprocess.call([sys.executable, str(SCRIPT_DIR / "fetch_recipe.py"), *argv])


def cmd_verify_website(argv: list[str]) -> int:
    return subprocess.call([sys.executable, str(SCRIPT_DIR / "verify_website.py"), *argv])


def cmd_bulk_dump(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="bulk-dump")
    ap.add_argument("--recipe", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0)
    args, rest = ap.parse_known_args(argv)
    out = args.out or f"/tmp/{args.recipe}.json"
    cmd = [sys.executable, str(SCRIPT_DIR / "fetch_recipe.py"), args.recipe, "--out", out]
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    rc = subprocess.call(cmd + rest)
    if rc == 0:
        print(f"bulk-dump\t{out}")
        phase1 = DEFAULT_VAULT / "scripts" / "phase1_dumps.py"
        if phase1.exists():
            print(f"note\tfull note write: integrate via {phase1} when desired")
    return rc


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(
            """usage: outreach_leads.py <mode> ...

modes:
  doctor
  coverage-check --category Law [--practice pi]
  specialty-directory --vertical pi --geo texas [--dry-run] [--no-push]
  enrich --category Law [--practice pi] [--layer website|contacts|emails] [--limit N] [--slug SLUG] [--max-pages N] [--apply PATH] [--no-push]
  fetch --recipe <id> | fetch <id>
  verify-website --url URL --firm NAME | verify-website URL --firm NAME
  bulk-dump --recipe <id> [--limit N]
  push-backup [--dry-run] [--no-push] [--vault PATH]
  build-poc [--category Law|Logistics|HVAC|…] [--slug SLUG] [--limit N] [--dry-run] [--no-push]
"""
        )
        return 0

    mode = argv[0]
    rest = argv[1:]

    # normalize flag-style fetch/verify wrappers
    if mode == "doctor":
        ap = argparse.ArgumentParser()
        ap.add_argument("--vault", default=str(DEFAULT_VAULT))
        ap.add_argument("--init", action="store_true")
        return cmd_doctor(ap.parse_args(rest))
    if mode == "coverage-check":
        return cmd_coverage_check(rest)
    if mode == "specialty-directory":
        return _run_module("specialty_directory", rest)
    if mode == "enrich":
        return _run_module("enrich", rest)
    if mode in {"site-enrich", "site_enrich"}:
        print("mode renamed: use `enrich` (e.g. enrich --layer website)", file=sys.stderr)
        return 2
    if mode == "fetch":
        # accept --recipe id or bare id
        if rest and rest[0] == "--recipe":
            rest = rest[1:]
        return cmd_fetch(rest)
    if mode == "verify-website":
        # accept --url X --firm Y or URL --firm Y
        if rest and rest[0] == "--url":
            rest = rest[1:]
        return cmd_verify_website(rest)
    if mode == "bulk-dump":
        return cmd_bulk_dump(rest)
    if mode == "push-backup":
        return _run_module("push_backup", rest)
    if mode in {"build-poc", "build_poc", "poc"}:
        return _run_module("build_poc", rest)

    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

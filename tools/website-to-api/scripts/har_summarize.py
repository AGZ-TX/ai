#!/usr/bin/env python3
"""Summarize a HAR into an API recipe draft. Strips cookies and auth header values."""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from collections import Counter
from pathlib import Path

SENSITIVE_HEADERS = {
    "cookie",
    "authorization",
    "csrf-token",
    "x-csrf-token",
    "set-cookie",
    "x-li-identity",
}


def load_har(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    return data.get("log", {}).get("entries", [])


def header_names(req: dict) -> list[str]:
    names = []
    for h in req.get("headers") or []:
        n = (h.get("name") or "").lower()
        if n and n not in SENSITIVE_HEADERS:
            names.append(n)
        elif n in SENSITIVE_HEADERS:
            names.append(n)  # name only; value never stored
    return sorted(set(names))


def summarize_entry(e: dict) -> dict | None:
    req = e.get("request") or {}
    res = e.get("response") or {}
    url = req.get("url") or ""
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    # keep only stable-looking query keys
    keep_q = {}
    for k, vals in qs.items():
        if k.lower() in {"queryid", "includewebmetadata", "variables"} or k.endswith("Id"):
            v = vals[0] if vals else ""
            if k.lower() == "variables" and len(v) > 180:
                v = v[:180] + "…"
            keep_q[k] = v
    text = (res.get("content") or {}).get("text") or ""
    return {
        "method": req.get("method"),
        "status": res.get("status"),
        "host": parsed.netloc,
        "path": parsed.path,
        "query_keep": keep_q,
        "header_names": [h for h in header_names(req) if h in SENSITIVE_HEADERS or h.startswith("x-") or h in {"accept", "content-type"}],
        "response_bytes": len(text),
        "url_template": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("har", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--host-filter", default="", help="only keep hosts containing this substring")
    args = ap.parse_args()

    rows = []
    for har in args.har:
        for e in load_har(har):
            row = summarize_entry(e)
            if not row:
                continue
            if args.host_filter and args.host_filter not in row["host"]:
                continue
            rows.append(row)

    # rank by response size (payload-ish first)
    rows.sort(key=lambda r: (-(r["response_bytes"] or 0), r["path"]))
    path_counts = Counter((r["method"], r["path"]) for r in rows)

    recipe = {
        "version": 1,
        "warning": "No secrets. Rehydrate cookies/csrf from the live session at replay time.",
        "entry_count": len(rows),
        "top_paths": [
            {"method": m, "path": p, "count": n} for (m, p), n in path_counts.most_common(40)
        ],
        "candidates": rows[:80],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    print(args.out, "entries", len(rows), "unique_paths", len(path_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

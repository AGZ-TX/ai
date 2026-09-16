#!/usr/bin/env python3
"""Fetch a named recipe from sources.json. Prints body or writes --out file."""
from __future__ import annotations

import argparse
import io
import json
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from note_io import DEFAULT_VAULT, UA

PACK_REG = Path(__file__).resolve().parents[1] / "references" / "sources.json"
VAULT_REG = DEFAULT_VAULT / "Sources" / "recipes" / "sources.json"


def load_reg(path: Path | None = None) -> dict:
    for p in ([path] if path else []) + [VAULT_REG, PACK_REG]:
        if p and p.exists():
            return json.loads(p.read_text()), p
    raise SystemExit("sources.json not found")


def fetch_recipe(recipe: str, reg: dict | None = None) -> bytes:
    if reg is None:
        reg, _ = load_reg()
    src = reg["sources"][recipe]
    ua = reg.get("user_agent", UA)
    if src["kind"] == "soda":
        q = urllib.parse.urlencode(src.get("params") or {})
        url = src["url"] + ("?" + q if q else "")
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        return urllib.request.urlopen(req, timeout=120).read()
    if src["kind"] == "overpass":
        body = urllib.parse.urlencode({"data": src["data"]}).encode()
        endpoints = [src["url"]] + [
            ep for ep in (
                "https://overpass-api.de/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter",
            )
            if ep != src["url"]
        ]
        last_err = None
        for ep in endpoints:
            try:
                req = urllib.request.Request(
                    ep,
                    data=body,
                    headers={"User-Agent": ua, "Content-Type": "application/x-www-form-urlencoded"},
                )
                return urllib.request.urlopen(req, timeout=120).read()
            except Exception as e:
                last_err = e
                continue
        raise last_err  # type: ignore[misc]
    if src["kind"] in {"html", "csv"}:
        accept = "text/csv,*/*" if src["kind"] == "csv" else "text/html"
        req = urllib.request.Request(src["url"], headers={"User-Agent": ua, "Accept": accept})
        return urllib.request.urlopen(req, timeout=120).read()
    if src["kind"] == "nppes":
        q = urllib.parse.urlencode(src.get("params") or {})
        url = src["url"] + ("?" + q if q else "")
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
        return urllib.request.urlopen(req, timeout=120).read()
    if src["kind"] == "zip_csv":
        req = urllib.request.Request(src["url"], headers={"User-Agent": ua})
        raw = urllib.request.urlopen(req, timeout=180).read()
        member = src.get("csv_member") or ""
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            name = member if member in zf.namelist() else next(
                (n for n in zf.namelist() if n.lower().endswith(".csv")), None
            )
            if not name:
                raise SystemExit(f"no csv in zip for {recipe}")
            return zf.read(name)
    raise SystemExit(f"unknown kind {src['kind']}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("recipe")
    ap.add_argument("--out", help="write raw bytes/text here")
    ap.add_argument("--sources", help="path to sources.json")
    ap.add_argument("--limit", type=int, default=0, help="for soda JSON arrays, truncate rows")
    args = ap.parse_args(argv)
    reg, used = load_reg(Path(args.sources) if args.sources else None)
    data = fetch_recipe(args.recipe, reg)
    if args.limit and reg["sources"][args.recipe]["kind"] == "soda":
        try:
            rows = json.loads(data)
            if isinstance(rows, list):
                data = json.dumps(rows[: args.limit]).encode()
        except Exception:
            pass
    if args.out:
        Path(args.out).write_bytes(data)
        print(args.out, len(data), f"(from {used})")
    else:
        print(data[:2000].decode("utf-8", "ignore"))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify a candidate firm website before writing to vault frontmatter.

Exit 0 + prints OK\turl if verified. Exit 1 + reason on stderr if rejected.
"""
from __future__ import annotations

import argparse
import re
import ssl
import sys
import urllib.request
from urllib.parse import urlparse

UA = "OutreachTools/1.0"
BLOCK = (
    "justia.com",
    "findlaw.com",
    "lawyers.com",
    "avvo.com",
    "facebook.com",
    "yelp.com",
    "linkedin.com",
    "superpages.com",
    "google.com",
    "maps.google",
    "yellowpages.com",
    "bbb.org",
    "wikipedia.org",
    "wixsite.com",
    "martindale.com",
    "usattorneys.com",
)


def host_blocked(host: str) -> bool:
    h = host.lower()
    return any(b in h for b in BLOCK)


def tokens(name: str) -> list[str]:
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", name).upper()
    stop = {
        "THE",
        "AND",
        "OF",
        "LAW",
        "FIRM",
        "OFFICE",
        "OFFICES",
        "ATTORNEY",
        "ATTORNEYS",
        "PLLC",
        "LLC",
        "PC",
        "PA",
        "INC",
        "CORP",
        "LLP",
        "LP",
    }
    return [t for t in s.split() if len(t) > 2 and t not in stop][:6]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--firm", required=True, help="Firm name that must appear on page")
    ap.add_argument("--city-hint", default="", help="Optional city/region string to prefer")
    args = ap.parse_args()
    url = args.url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    host = urlparse(url).netloc.lower()
    if host_blocked(host):
        print(f"reject\tdirectory_host\t{host}", file=sys.stderr)
        sys.exit(1)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            raw = r.read(200_000)
            final = r.geturl()
            code = r.status
    except Exception as e:
        print(f"reject\tfetch_error\t{e}", file=sys.stderr)
        sys.exit(1)
    if code >= 400:
        print(f"reject\thttp_{code}", file=sys.stderr)
        sys.exit(1)
    text = raw.decode("utf-8", "ignore")
    title_m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
    blob = (title + "\n" + text[:80_000]).upper()
    toks = tokens(args.firm)
    hits = [t for t in toks if t in blob]
    need = 1 if len(toks) <= 2 else min(2, len(toks))
    if len(hits) < need:
        print(
            f"reject\tname_mismatch\thits={hits}\tneed={need}\ttitle={title[:80]}",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.city_hint:
        ch = args.city_hint.upper()
        city_ok = any(p in blob for p in ch.replace("-", " ").split() if len(p) > 3)
        if not city_ok:
            print(f"warn\tcity_weak\t{args.city_hint}", file=sys.stderr)
    final_host = urlparse(final).netloc
    if host_blocked(final_host):
        print(f"reject\tredirect_to_directory\t{final_host}", file=sys.stderr)
        sys.exit(1)
    canon = f"{urlparse(final).scheme}://{urlparse(final).netloc}/"
    print(f"OK\t{canon}\thits={','.join(hits)}\ttitle={title[:60]}")
    sys.exit(0)


if __name__ == "__main__":
    main()

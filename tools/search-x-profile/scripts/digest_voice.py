#!/usr/bin/env python3
"""Write a short X voice/pattern note from posts JSON.

Short pattern note from captured posts. No cookies.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

EXIT_OK = 0
EXIT_FAIL = 1
WORD_RE = re.compile(r"[A-Za-z0-9']+")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def load_posts(path: Path) -> tuple[str, list[dict[str, str]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        posts = raw
        handle = "unknown"
    elif isinstance(raw, dict):
        posts = raw.get("posts") or []
        handle = str(raw.get("handle") or "unknown")
    else:
        raise ValueError("posts JSON must be an object or a list")
    if not isinstance(posts, list):
        raise ValueError("posts must be a list")
    cleaned: list[dict[str, str]] = []
    for row in posts:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        cleaned.append(
            {
                "id": str(row.get("id") or ""),
                "text": text,
                "created_at": str(row.get("created_at") or ""),
            }
        )
    return handle, cleaned


def opener(text: str, n: int = 4) -> str:
    words = WORD_RE.findall(text)
    if not words:
        return ""
    return " ".join(words[:n]).lower()


def digest_body(handle: str, posts: list[dict[str, str]]) -> str:
    lengths = [len(p["text"]) for p in posts]
    openers = Counter(opener(p["text"]) for p in posts if opener(p["text"]))
    common = [f"- {text} ({count})" for text, count in openers.most_common(8) if count > 1]
    samples = [p["text"].replace("\n", " ") for p in posts[:5]]
    median = int(statistics.median(lengths)) if lengths else 0
    mean = int(statistics.mean(lengths)) if lengths else 0
    lines = [
        f"# Voice note for @{handle}",
        "",
        "X voice / pattern note from captured posts.",
        "",
        f"Posts: {len(posts)}",
        f"Chars median: {median}",
        f"Chars mean: {mean}",
        "",
        "## Repeated openers",
    ]
    if common:
        lines.extend(common)
    else:
        lines.append("- none repeated at 4 words")
    lines.extend(["", "## Samples"])
    if samples:
        lines.extend(f"- {text}" for text in samples)
    else:
        lines.append("- no posts")
    lines.append("")
    return "\n".join(lines)


def write_digest(posts_path: Path, out_path: Path) -> Path:
    handle, posts = load_posts(posts_path)
    if handle != "unknown" and not HANDLE_RE.fullmatch(handle):
        handle = "unknown"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(digest_body(handle, posts), encoding="utf-8")
    return out_path


def self_check() -> int:
    errors: list[str] = []
    fixture = {
        "handle": "example",
        "posts": [
            {"id": "1", "text": "gm to the timeline crew", "created_at": ""},
            {"id": "2", "text": "gm to the other room", "created_at": ""},
            {"id": "3", "text": "one off line", "created_at": ""},
        ],
    }
    tmp = Path("/tmp/search-x-profile-voice/posts.json")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(fixture), encoding="utf-8")
    out = write_digest(tmp, tmp.parent / "voice.md")
    text = out.read_text(encoding="utf-8")
    if "pattern note" not in text:
        errors.append("voice note missing pattern header")
    if "gm to the" not in text:
        errors.append("repeated opener missing")
    if "Posts: 3" not in text:
        errors.append("count missing")
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        print("self-check failed")
        return EXIT_FAIL
    print("self-check ok")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--posts", default="", help="posts.json from capture_profile.py")
    ap.add_argument(
        "--out",
        default="",
        help="markdown path. Default is voice.md next to the posts file",
    )
    ap.add_argument("--self-check", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.posts:
        print("--posts is required unless --self-check", file=sys.stderr)
        return EXIT_FAIL
    posts_path = Path(args.posts)
    if not posts_path.is_file():
        print(f"missing posts file {posts_path}", file=sys.stderr)
        return EXIT_FAIL
    out_path = Path(args.out) if args.out else posts_path.with_name("voice.md")
    try:
        written = write_digest(posts_path, out_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAIL
    print(written)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

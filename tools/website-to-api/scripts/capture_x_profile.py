#!/usr/bin/env python3
"""Capture an authorized X profile via Patchright GraphQL hooks.

Signed-in operator session only. Not a bypass tool. Does not make a session undetectable.

Writes posts JSON and a secret-free recipe draft under ./data/website-to-api-capture/.
Exits 2 on a login wall. Exits 3 if Patchright cannot attach.
"""
from __future__ import annotations

import argparse
import os
import json
import random
import re
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GRAPHQL_MARKERS = ("api.x.com/graphql", "/i/api/graphql")
LOGIN_PATHS = ("/i/flow/login", "/login")
LOGIN_TITLES = ("log in to x", "sign in to x", "log in to twitter")
SENSITIVE_HEADERS = {
    "cookie",
    "authorization",
    "csrf-token",
    "x-csrf-token",
    "set-cookie",
    "x-guest-token",
}
BURST_WHEEL_MIN = 7
BURST_WHEEL_MAX = 24
OVERSHOOT_P = 0.10
OVERSHOOT_PX = (150, 400)
PAUSE = {"skim": (0.4, 1.0), "browse": (0.8, 2.0), "deep": (1.5, 3.5)}
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_LOGIN_WALL = 2
EXIT_NO_ATTACH = 3
RECIPE_WARNING = (
    "No secrets. Rehydrate cookies/csrf from the live session at replay time."
)
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


@dataclass(frozen=True)
class GraphQLHit:
    url: str
    method: str
    status: int
    operation_name: str
    query_id: str
    header_names: tuple[str, ...]
    body: Any
    response_bytes: int


@dataclass
class RecipeDraft:
    version: int
    warning: str
    capture: str
    handle: str
    operations: list[dict[str, Any]]
    candidates: list[dict[str, Any]]


@dataclass
class PostsFile:
    handle: str
    source: str
    posts: list[dict[str, str]]
    operations: list[str] = field(default_factory=list)


def normalize_handle(raw: str) -> str:
    handle = raw.strip().lstrip("@")
    if not HANDLE_RE.fullmatch(handle):
        raise ValueError(f"invalid handle {raw!r}")
    return handle


def is_x_graphql(url: str) -> bool:
    return any(marker in url for marker in GRAPHQL_MARKERS)


def parse_graphql_url(url: str) -> tuple[str, str]:
    path = urllib.parse.urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if "graphql" not in parts:
        return "", ""
    idx = parts.index("graphql")
    after = parts[idx + 1 :]
    if len(after) >= 2:
        return after[0], after[1]
    if len(after) == 1:
        return "", after[0]
    return "", ""


def login_wall_reason(url: str, title: str = "") -> str | None:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    for marker in LOGIN_PATHS:
        if path == marker or path.startswith(marker + "/"):
            return f"login wall url {url}"
    title_l = title.lower()
    if any(t in title_l for t in LOGIN_TITLES):
        return f"login wall title {title!r}"
    return None


def header_names_only(headers: dict[str, str] | None) -> tuple[str, ...]:
    names = []
    for key in (headers or {}):
        name = key.lower()
        if name:
            names.append(name)
    return tuple(sorted(set(names)))


def url_template(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def hit_public_dict(hit: GraphQLHit) -> dict[str, Any]:
    return {
        "url_template": url_template(hit.url),
        "method": hit.method,
        "status": hit.status,
        "operation_name": hit.operation_name,
        "query_id": hit.query_id,
        "header_names": list(hit.header_names),
        "response_bytes": hit.response_bytes,
    }


def distill_recipe(handle: str, hits: list[GraphQLHit]) -> RecipeDraft:
    counts: Counter[str] = Counter()
    by_op: dict[str, GraphQLHit] = {}
    for hit in hits:
        counts[hit.operation_name] += 1
        by_op.setdefault(hit.operation_name, hit)
    operations = []
    for name, count in counts.most_common():
        sample = by_op[name]
        operations.append(
            {
                "name": name,
                "method": sample.method,
                "url_template": url_template(sample.url),
                "query_id": sample.query_id,
                "count": count,
                "header_names": list(sample.header_names),
            }
        )
    return RecipeDraft(
        version=1,
        warning=RECIPE_WARNING,
        capture="network-hooks",
        handle=handle,
        operations=operations,
        candidates=[hit_public_dict(h) for h in hits[:80]],
    )


def _tweet_from_node(node: dict[str, Any]) -> dict[str, str] | None:
    rest_id = node.get("rest_id")
    legacy = node.get("legacy") if isinstance(node.get("legacy"), dict) else {}
    note = node.get("note_tweet") if isinstance(node.get("note_tweet"), dict) else {}
    note_text = ""
    if isinstance(note.get("note_tweet_results"), dict):
        result = note["note_tweet_results"].get("result")
        if isinstance(result, dict):
            note_text = str(result.get("text") or "")
    text = note_text or str(legacy.get("full_text") or "")
    if not rest_id or not text:
        return None
    return {
        "id": str(rest_id),
        "text": text,
        "created_at": str(legacy.get("created_at") or ""),
    }


def extract_posts(payload: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            tweet = _tweet_from_node(obj)
            if tweet and tweet["id"] not in seen:
                seen.add(tweet["id"])
                found.append(tweet)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(payload)
    return found


def posts_from_hits(handle: str, hits: list[GraphQLHit]) -> PostsFile:
    posts: list[dict[str, str]] = []
    ops: list[str] = []
    for hit in hits:
        if hit.operation_name in {"UserTweets", "UserTweetsAndReplies", "UserOriginalsTimeline"}:
            ops.append(hit.operation_name)
            posts.extend(extract_posts(hit.body))
    dedup: list[dict[str, str]] = []
    seen: set[str] = set()
    for post in posts:
        if post["id"] in seen:
            continue
        seen.add(post["id"])
        dedup.append(post)
    return PostsFile(
        handle=handle,
        source="UserTweets",
        posts=dedup,
        operations=sorted(set(ops)),
    )


def recipe_has_secret(recipe: RecipeDraft, forbidden: str) -> bool:
    blob = json.dumps(asdict(recipe), ensure_ascii=True)
    return forbidden in blob


def human_scroll_burst(page: Any, rng: random.Random) -> None:
    n = rng.randint(BURST_WHEEL_MIN, BURST_WHEEL_MAX)
    for i in range(n):
        page.mouse.wheel(0, rng.randint(40, 180))
        if i < n - 1:
            time.sleep(rng.uniform(0.012, 0.055))
    if rng.random() < OVERSHOOT_P:
        time.sleep(rng.uniform(0.08, 0.16))
        page.mouse.wheel(0, -rng.randint(*OVERSHOOT_PX))


def human_read_pause(rng: random.Random, intent: str) -> None:
    lo, hi = PAUSE[intent]
    time.sleep(rng.uniform(lo, hi))


def hit_from_response(response: Any) -> GraphQLHit | None:
    url = getattr(response, "url", "") or ""
    if not is_x_graphql(url):
        return None
    query_id, operation_name = parse_graphql_url(url)
    if not operation_name:
        return None
    request = getattr(response, "request", None)
    raw_headers = getattr(request, "headers", None) if request is not None else None
    try:
        body = response.json()
    except Exception:
        body = None
    text = ""
    if body is None:
        try:
            text = response.text()
        except Exception:
            text = ""
    return GraphQLHit(
        url=url,
        method=getattr(request, "method", None) or "GET",
        status=int(getattr(response, "status", 0) or 0),
        operation_name=operation_name,
        query_id=query_id,
        header_names=header_names_only(raw_headers if isinstance(raw_headers, dict) else {}),
        body=body,
        response_bytes=len(json.dumps(body)) if body is not None else len(text),
    )


def write_outputs(out_dir: Path, handle: str, hits: list[GraphQLHit]) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    hits_path = out_dir / f"{handle}-graphql.json"
    posts_path = out_dir / f"{handle}-posts.json"
    recipe_path = out_dir / f"{handle}-recipe-draft.json"
    public_hits = []
    for hit in hits:
        row = hit_public_dict(hit)
        row["body"] = hit.body
        public_hits.append(row)
    hits_path.write_text(json.dumps(public_hits, indent=2) + "\n", encoding="utf-8")
    posts = posts_from_hits(handle, hits)
    posts_path.write_text(json.dumps(asdict(posts), indent=2) + "\n", encoding="utf-8")
    recipe = distill_recipe(handle, hits)
    recipe_path.write_text(json.dumps(asdict(recipe), indent=2) + "\n", encoding="utf-8")
    return hits_path, posts_path, recipe_path


def load_hits(path: Path) -> list[GraphQLHit]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    hits: list[GraphQLHit] = []
    for row in raw:
        url = row.get("url") or row.get("url_template") or ""
        query_id = row.get("query_id") or ""
        operation_name = row.get("operation_name") or ""
        if url and (not query_id or not operation_name):
            parsed_id, parsed_name = parse_graphql_url(url)
            query_id = query_id or parsed_id
            operation_name = operation_name or parsed_name
        names = row.get("header_names") or []
        hits.append(
            GraphQLHit(
                url=url,
                method=row.get("method") or "GET",
                status=int(row.get("status") or 0),
                operation_name=operation_name,
                query_id=query_id,
                header_names=tuple(names),
                body=row.get("body"),
                response_bytes=int(row.get("response_bytes") or 0),
            )
        )
    return hits


def capture_live(args: argparse.Namespace) -> int:
    handle = normalize_handle(args.handle)
    out_dir = Path(args.out)
    user_data_dir = Path(args.user_data_dir)
    user_data_dir.mkdir(parents=True, exist_ok=True)
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        print(
            "Patchright is not installed. Install patchright-python, or use "
            "computerUse DevTools Save HAR only as last fallback.",
            file=sys.stderr,
        )
        return EXIT_NO_ATTACH

    hits: list[GraphQLHit] = []
    profile_url = f"https://x.com/{handle}"
    record_har = str(out_dir / f"{handle}.har") if args.record_har else None
    if record_har:
        out_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            launch_kwargs: dict[str, Any] = {
                "channel": "chrome",
                "headless": False,
                "no_viewport": True,
            }
            if record_har:
                launch_kwargs["record_har_path"] = record_har
            try:
                context = p.chromium.launch_persistent_context(
                    str(user_data_dir), **launch_kwargs
                )
            except Exception as exc:
                print(
                    f"Patchright could not attach ({exc}). "
                    "computerUse DevTools Save HAR is last fallback.",
                    file=sys.stderr,
                )
                return EXIT_NO_ATTACH
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response: Any) -> None:
                hit = hit_from_response(response)
                if hit:
                    hits.append(hit)

            page.on("response", on_response)
            try:
                with page.expect_response(
                    lambda r: is_x_graphql(r.url)
                    and parse_graphql_url(r.url)[1]
                    in {"UserByScreenName", "UserTweets"},
                    timeout=30000,
                ):
                    page.goto(profile_url, wait_until="domcontentloaded")
            except Exception:
                wall = login_wall_reason(page.url, page.title())
                if wall:
                    print(wall, file=sys.stderr)
                    context.close()
                    return EXIT_LOGIN_WALL
            wall = login_wall_reason(page.url, page.title())
            if wall:
                print(wall, file=sys.stderr)
                context.close()
                return EXIT_LOGIN_WALL
            rng = random.Random()
            for _ in range(args.bursts):
                human_scroll_burst(page, rng)
                human_read_pause(rng, args.intent)
            wall = login_wall_reason(page.url, page.title())
            if wall:
                print(wall, file=sys.stderr)
                context.close()
                return EXIT_LOGIN_WALL
            context.close()
    except Exception as exc:
        print(
            f"Patchright could not attach ({exc}). "
            "computerUse DevTools Save HAR is last fallback.",
            file=sys.stderr,
        )
        return EXIT_NO_ATTACH

    if not hits:
        print("no api.x.com/graphql responses captured", file=sys.stderr)
        return EXIT_FAIL
    hits_path, posts_path, recipe_path = write_outputs(out_dir, handle, hits)
    print(hits_path)
    print(posts_path)
    print(recipe_path)
    print("operations", [op["name"] for op in distill_recipe(handle, hits).operations])
    return EXIT_OK


def run_from_hits(args: argparse.Namespace) -> int:
    handle = normalize_handle(args.handle)
    hits = load_hits(Path(args.from_hits))
    hits_path, posts_path, recipe_path = write_outputs(Path(args.out), handle, hits)
    print(hits_path)
    print(posts_path)
    print(recipe_path)
    return EXIT_OK


def _sample_tweets_body() -> dict[str, Any]:
    return {
        "data": {
            "user": {
                "result": {
                    "timeline_v2": {
                        "timeline": {
                            "instructions": [
                                {
                                    "entries": [
                                        {
                                            "content": {
                                                "itemContent": {
                                                    "tweet_results": {
                                                        "result": {
                                                            "rest_id": "123",
                                                            "legacy": {
                                                                "full_text": "hello from the timeline",
                                                                "created_at": "Wed Sep 16 00:00:00 +0000 2026",
                                                            },
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                }
            }
        }
    }


def self_check() -> int:
    errors: list[str] = []
    handle = normalize_handle("example")
    user_url = (
        "https://api.x.com/graphql/QueryIdUser/UserByScreenName"
        "?variables=%7B%22screen_name%22%3A%22example%22%7D"
    )
    tweets_url = "https://api.x.com/graphql/QueryIdTweets/UserTweets?variables=%7B%7D"
    qid, op = parse_graphql_url(user_url)
    if (qid, op) != ("QueryIdUser", "UserByScreenName"):
        errors.append(f"parse UserByScreenName got {(qid, op)}")
    qid, op = parse_graphql_url(tweets_url)
    if (qid, op) != ("QueryIdTweets", "UserTweets"):
        errors.append(f"parse UserTweets got {(qid, op)}")
    if login_wall_reason("https://x.com/i/flow/login", "Home") is None:
        errors.append("login url not detected")
    if login_wall_reason("https://x.com/example", "Example") is not None:
        errors.append("profile url flagged as login wall")
    if login_wall_reason("https://x.com/example", "Log in to X") is None:
        errors.append("login title not detected")
    secret = "auth_cookie_value_DO_NOT_WRITE"
    raw_headers = {"Cookie": secret, "Authorization": "Bearer leaked-token"}
    names = header_names_only(raw_headers)
    if names != ("authorization", "cookie"):
        errors.append(f"header names {names}")
    if secret in names or "leaked-token" in names:
        errors.append("header values leaked into names")
    hits = [
        GraphQLHit(
            url=user_url,
            method="GET",
            status=200,
            operation_name="UserByScreenName",
            query_id="QueryIdUser",
            header_names=("authorization", "cookie", "x-csrf-token"),
            body={"data": {"user": {"result": {"rest_id": "1"}}}},
            response_bytes=40,
        ),
        GraphQLHit(
            url=tweets_url,
            method="GET",
            status=200,
            operation_name="UserTweets",
            query_id="QueryIdTweets",
            header_names=("authorization", "cookie"),
            body=_sample_tweets_body(),
            response_bytes=200,
        ),
    ]
    recipe = distill_recipe(handle, hits)
    if recipe_has_secret(recipe, secret) or recipe_has_secret(recipe, "leaked-token"):
        errors.append("recipe leaked fixture secret")
    names = {op["name"] for op in recipe.operations}
    if names != {"UserByScreenName", "UserTweets"}:
        errors.append(f"operations {names}")
    for op in recipe.operations:
        if "cookie" not in op["header_names"]:
            errors.append("header names dropped cookie key")
        if secret in json.dumps(op):
            errors.append("operation row has secret")
    posts = posts_from_hits(handle, hits)
    if [p["text"] for p in posts.posts] != ["hello from the timeline"]:
        errors.append(f"posts {posts.posts}")
    if posts.posts[0]["id"] != "123":
        errors.append(f"post id {posts.posts[0]['id']}")
    out = Path("/tmp/x-capture-self-check")
    _, posts_path, recipe_path = write_outputs(out, handle, hits)
    written = posts_path.read_text(encoding="utf-8") + recipe_path.read_text(encoding="utf-8")
    if secret in written:
        errors.append("wrote secret to disk")
    if "hello from the timeline" not in posts_path.read_text(encoding="utf-8"):
        errors.append("posts file missing text")
    skill = Path(__file__).resolve().parents[1] / "SKILL.md"
    human = Path(__file__).resolve().parents[1] / "references" / "human-input.md"
    skill_text = skill.read_text(encoding="utf-8")
    human_text = human.read_text(encoding="utf-8")
    if "page.on('response')" not in skill_text or "page.on('response')" not in human_text:
        errors.append("skill or human-input missing page.on('response')")
    if "Save HAR" not in skill_text or "fallback" not in skill_text.lower():
        errors.append("SKILL.md must name Save HAR as fallback")
    hygiene = human_text.split("## Capture hygiene", 1)[-1]
    if hygiene.find("page.on('response')") > hygiene.find("Save HAR"):
        errors.append("human-input hygiene must put hooks before Save HAR")
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        print("self-check failed")
        return EXIT_FAIL
    print("self-check ok")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--handle", default="", help="X handle, for example example")
    ap.add_argument("--bursts", type=int, default=4, help="human scroll bursts (default 4)")
    ap.add_argument(
        "--intent",
        choices=sorted(PAUSE),
        default="browse",
        help="XCli read-pause band between bursts",
    )
    ap.add_argument(
        "--user-data-dir",
        default=os.environ.get("CHROME_PROFILE", str(Path.cwd() / "data" / "website-to-api-capture" / "chrome-profile")),
        help="persistent Chrome profile for the signed-in session",
    )
    ap.add_argument("--out", default=os.environ.get("WEBSITE_TO_API_OUT", str(Path.cwd() / "data" / "website-to-api-capture")), help="gitignored capture directory")
    ap.add_argument(
        "--record-har",
        action="store_true",
        help="Playwright recordHar on this context only. Not a DevTools export.",
    )
    ap.add_argument(
        "--from-hits",
        default="",
        help="distill posts and recipe from saved GraphQL JSON. Skip the browser.",
    )
    ap.add_argument("--self-check", action="store_true", help="run fixture checks and exit")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.handle:
        print("--handle is required unless --self-check", file=sys.stderr)
        return EXIT_FAIL
    if args.from_hits:
        return run_from_hits(args)
    return capture_live(args)


if __name__ == "__main__":
    raise SystemExit(main())

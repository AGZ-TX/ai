#!/usr/bin/env python3
"""Archive an authorized X profile via Patchright GraphQL.

Signed-in operator session only. Not a bypass tool. Does not write cookies or tokens.

Attach with --cdp when Chrome is already open. Otherwise launch headed
Chrome with a persistent context. Bootstrap the profile page, capture
UserOriginalsTimeline (UserTweets fallback), then paginate with the
bottom cursor on the same browser context.

Writes posts.json and checkpoint.json under
./data/search-x-profile/<handle>/ after every successful page.
A later run loads those files and resumes from bottom_cursor.
Exit 0 ok, 1 fail, 2 login wall, 3 rate limited.
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

GRAPHQL_MARKERS = ("api.x.com/graphql", "/i/api/graphql")
TIMELINE_OPS = (
    "UserOriginalsTimeline",
    "UserTweets",
    "UserTweetsAndReplies",
)
LOGIN_PATHS = ("/i/flow/login", "/login", "/i/flow/signup")
LOGIN_TITLES = ("log in to x", "sign in to x", "log in to twitter")
SOFT_BLOCK_PATHS = ("/account/access",)
SOFT_BLOCK_MARKERS = (
    "Something went wrong",
    "Rate limit exceeded",
    "Try again later",
)
SENSITIVE_HEADER_NAMES = {
    "cookie",
    "authorization",
    "csrf-token",
    "x-csrf-token",
    "set-cookie",
    "x-guest-token",
    "x-client-transaction-id",
}
HOP_BY_HOP = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "accept-encoding",
}
BURST_WHEEL_MIN = 7
BURST_WHEEL_MAX = 24
OVERSHOOT_P = 0.10
OVERSHOOT_PX = (150, 400)
PAUSE = {"skim": (0.4, 1.0), "browse": (0.8, 2.0), "deep": (1.5, 3.5)}
XCLI_SOFT_BLOCK_WAIT = 30.0
JITTER_RANGE = (1.0, 5.0)
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_LOGIN_WALL = 2
EXIT_RATE_LIMITED = 3
RECIPE_WARNING = (
    "No secrets. Rehydrate cookies and csrf from the live session at replay time."
)
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
DEFAULT_OUT = os.environ.get("SEARCH_X_PROFILE_OUT", str(Path.cwd() / "data" / "search-x-profile"))
DEFAULT_PROFILE = os.environ.get("CHROME_PROFILE", str(Path.cwd() / "data" / "search-x-profile" / "chrome-profile"))
DEFAULT_CDP = "http://127.0.0.1:9225"
DEFAULT_MIN_DELAY = 0.5
DEFAULT_MAX_DELAY = 1.5
MAX_429_RETRIES = 1
DUP_PAGE_STREAK = 5


@dataclass(frozen=True)
class RateLimit:
    limit: int | None
    remaining: int | None
    reset_unix: int | None

    def remaining_exhausted(self) -> bool:
        return self.remaining is not None and self.remaining <= 0


@dataclass(frozen=True)
class GraphQLTemplate:
    operation_name: str
    query_id: str
    method: str
    url_template: str
    variables: dict[str, Any]
    features: dict[str, Any]
    field_toggles: dict[str, Any]
    header_names: tuple[str, ...]


@dataclass(frozen=True)
class TimelinePage:
    posts: list[dict[str, str]]
    bottom_cursor: str | None
    rate: RateLimit
    status: int
    operation_name: str


@dataclass
class ArchiveResult:
    posts: list[dict[str, str]]
    pages: int
    stop: str
    last_rate: RateLimit
    operation_name: str
    query_id: str
    page_sizes: list[int] = field(default_factory=list)
    bottom_cursor: str | None = None


@dataclass
class PostsFile:
    handle: str
    source: str
    query_id: str
    exhausted: bool
    stop: str
    page_count: int
    page_sizes: list[int]
    rate_limit: dict[str, int | None]
    posts: list[dict[str, str]]


@dataclass
class RecipeDraft:
    version: int
    warning: str
    capture: str
    handle: str
    operation_name: str
    query_id: str
    method: str
    url_template: str
    variable_keys: list[str]
    feature_keys: list[str]
    header_names: list[str]
    cursor: str


@dataclass(frozen=True)
class Checkpoint:
    handle: str
    query_id: str
    bottom_cursor: str | None
    page_count: int
    last_rate: dict[str, int | None]
    post_ids: int


@dataclass
class DiskArchive:
    posts: list[dict[str, str]]
    checkpoint: Checkpoint | None
    page_sizes: list[int]


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


def url_template(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def parse_query_object(url: str, key: str) -> dict[str, Any]:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    raw = (qs.get(key) or [""])[0]
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def lower_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key).lower()
        if name:
            out[name] = "" if value is None else str(value)
    return out


def header_names_only(headers: dict[str, Any] | None) -> tuple[str, ...]:
    return tuple(sorted(lower_headers(headers)))


def rate_limit_from_headers(headers: dict[str, Any] | None) -> RateLimit:
    mapped = lower_headers(headers)

    def maybe_int(name: str) -> int | None:
        raw = mapped.get(name)
        if raw is None or raw == "":
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None

    return RateLimit(
        limit=maybe_int("x-rate-limit-limit"),
        remaining=maybe_int("x-rate-limit-remaining"),
        reset_unix=maybe_int("x-rate-limit-reset"),
    )


def wait_seconds(rate: RateLimit, now: float, jitter: float) -> float:
    if rate.reset_unix is not None:
        return max(0.0, float(rate.reset_unix) - now) + jitter
    return XCLI_SOFT_BLOCK_WAIT + jitter


def should_retry_backoff(status: int) -> bool:
    return status == 429


def inter_page_delay(
    rate: RateLimit, min_s: float, max_s: float, rng: random.Random
) -> float:
    lo = min(min_s, max_s)
    hi = max(min_s, max_s)
    delay = rng.uniform(lo, hi) if hi > lo else lo
    if rate.remaining is not None and rate.remaining <= 10:
        delay = max(delay, hi)
    if rate.remaining is not None and rate.limit and rate.remaining <= max(1, rate.limit // 10):
        delay *= 1.5
    return delay


def login_wall_reason(url: str, title: str = "") -> str | None:
    path = urllib.parse.urlparse(url).path.lower()
    for marker in LOGIN_PATHS:
        if path == marker or path.startswith(marker + "/"):
            return f"login wall url {url}"
    title_l = title.lower()
    if any(t in title_l for t in LOGIN_TITLES):
        return f"login wall title {title!r}"
    return None


def page_soft_blocked(url: str, body_text: str, has_primary_column: bool) -> bool:
    path = urllib.parse.urlparse(url).path
    for marker in SOFT_BLOCK_PATHS:
        if path == marker or path.startswith(marker + "/"):
            return True
    if has_primary_column:
        return False
    return any(marker in body_text for marker in SOFT_BLOCK_MARKERS)


def _as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _tweet_from_node(node: dict[str, Any]) -> dict[str, Any] | None:
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
    views = node.get("views") if isinstance(node.get("views"), dict) else {}
    out: dict[str, Any] = {
        "id": str(rest_id),
        "text": text,
        "created_at": str(legacy.get("created_at") or ""),
        "favorite_count": _as_int(legacy.get("favorite_count")),
        "retweet_count": _as_int(legacy.get("retweet_count")),
        "reply_count": _as_int(legacy.get("reply_count")),
        "quote_count": _as_int(legacy.get("quote_count")),
        "bookmark_count": _as_int(legacy.get("bookmark_count")),
    }
    view_count = _as_int(views.get("count")) if views.get("count") not in (None, "") else None
    if view_count is not None:
        out["view_count"] = view_count
    return out


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


def extract_bottom_cursor(payload: Any) -> str | None:
    ranked: list[tuple[int, str]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            cursor_type = str(obj.get("cursorType") or "")
            value = obj.get("value")
            if isinstance(value, str) and value:
                if cursor_type == "Bottom":
                    ranked.append((2, value))
                elif cursor_type == "ShowMore":
                    ranked.append((1, value))
            for item in obj.values():
                walk(item)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(payload)
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0])
    return ranked[-1][1]


def template_from_request(
    url: str,
    method: str,
    header_names: tuple[str, ...],
    post_body: dict[str, Any] | None = None,
) -> GraphQLTemplate | None:
    query_id, operation_name = parse_graphql_url(url)
    if not operation_name:
        return None
    variables = parse_query_object(url, "variables")
    features = parse_query_object(url, "features")
    field_toggles = parse_query_object(url, "fieldToggles")
    if post_body:
        if isinstance(post_body.get("variables"), dict) and not variables:
            variables = post_body["variables"]
        if isinstance(post_body.get("features"), dict) and not features:
            features = post_body["features"]
        if isinstance(post_body.get("fieldToggles"), dict) and not field_toggles:
            field_toggles = post_body["fieldToggles"]
        if post_body.get("queryId") and not query_id:
            query_id = str(post_body["queryId"])
    variables = dict(variables)
    variables.pop("cursor", None)
    return GraphQLTemplate(
        operation_name=operation_name,
        query_id=query_id,
        method=(method or "GET").upper(),
        url_template=url_template(url),
        variables=variables,
        features=features,
        field_toggles=field_toggles,
        header_names=header_names,
    )


def next_graphql_request(
    template: GraphQLTemplate, cursor: str
) -> tuple[str, dict[str, Any] | None]:
    variables = dict(template.variables)
    variables["cursor"] = cursor
    if template.method == "POST":
        body: dict[str, Any] = {
            "variables": variables,
            "features": template.features,
            "queryId": template.query_id,
        }
        if template.field_toggles:
            body["fieldToggles"] = template.field_toggles
        return template.url_template, body
    query: dict[str, str] = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "features": json.dumps(template.features, separators=(",", ":")),
    }
    if template.field_toggles:
        query["fieldToggles"] = json.dumps(
            template.field_toggles, separators=(",", ":")
        )
    return template.url_template + "?" + urllib.parse.urlencode(query), None


def replay_headers(live_headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in lower_headers(live_headers).items():
        if key in HOP_BY_HOP:
            continue
        out[key] = value
    return out


def pick_timeline_page(
    hits: list[tuple[GraphQLTemplate, TimelinePage]],
) -> tuple[GraphQLTemplate, TimelinePage] | None:
    by_name: dict[str, tuple[GraphQLTemplate, TimelinePage]] = {}
    for template, page in hits:
        if template.operation_name in TIMELINE_OPS and page.status == 200:
            by_name[template.operation_name] = (template, page)
    for name in TIMELINE_OPS:
        if name in by_name:
            return by_name[name]
    return None


def page_from_payload(
    payload: Any,
    headers: dict[str, Any] | None,
    status: int,
    operation_name: str,
) -> TimelinePage:
    return TimelinePage(
        posts=extract_posts(payload),
        bottom_cursor=extract_bottom_cursor(payload),
        rate=rate_limit_from_headers(headers),
        status=status,
        operation_name=operation_name,
    )


METRIC_KEYS = (
    "favorite_count",
    "retweet_count",
    "reply_count",
    "quote_count",
    "bookmark_count",
    "view_count",
)


def merge_posts(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int]:
    """Merge posts. Returns (merged, added_count, metrics_updated_count)."""
    by_id = {post["id"]: dict(post) for post in existing}
    order = [post["id"] for post in existing]
    added = 0
    updated = 0
    for post in incoming:
        pid = post["id"]
        if pid in by_id:
            cur = by_id[pid]
            changed = False
            for key in METRIC_KEYS:
                if key not in post:
                    continue
                if cur.get(key) != post.get(key):
                    cur[key] = post[key]
                    changed = True
            if post.get("text") and not cur.get("text"):
                cur["text"] = post["text"]
                changed = True
            if post.get("created_at") and not cur.get("created_at"):
                cur["created_at"] = post["created_at"]
                changed = True
            if changed:
                updated += 1
            continue
        by_id[pid] = dict(post)
        order.append(pid)
        added += 1
    return [by_id[i] for i in order], added, updated


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    dumps: Callable[..., str] = json.dumps,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _normalize_post(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    return {
        "id": str(raw["id"]),
        "text": str(raw.get("text") or ""),
        "created_at": str(raw.get("created_at") or ""),
    }


def load_posts(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    posts: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        post = _normalize_post(row)
        if post is None or post["id"] in seen:
            continue
        seen.add(post["id"])
        posts.append(post)
    return posts


def load_checkpoint(path: Path) -> Checkpoint | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    cursor = data.get("bottom_cursor")
    if cursor is not None:
        cursor = str(cursor) or None
    rate = data.get("last_rate") if isinstance(data.get("last_rate"), dict) else {}
    return Checkpoint(
        handle=str(data.get("handle") or ""),
        query_id=str(data.get("query_id") or ""),
        bottom_cursor=cursor,
        page_count=int(data.get("page_count") or 0),
        last_rate={
            "limit": _maybe_int(rate.get("limit")),
            "remaining": _maybe_int(rate.get("remaining")),
            "reset": _maybe_int(rate.get("reset")),
        },
        post_ids=int(data.get("post_ids") or 0),
    )


def load_disk_archive(out_dir: Path) -> DiskArchive:
    posts_path = out_dir / "posts.json"
    page_sizes: list[int] = []
    if posts_path.is_file():
        try:
            data = json.loads(posts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("page_sizes"), list):
            for item in data["page_sizes"]:
                parsed = _maybe_int(item)
                if parsed is not None:
                    page_sizes.append(parsed)
    return DiskArchive(
        posts=load_posts(posts_path),
        checkpoint=load_checkpoint(out_dir / "checkpoint.json"),
        page_sizes=page_sizes,
    )


def checkpoint_from_archive(handle: str, archive: ArchiveResult) -> Checkpoint:
    return Checkpoint(
        handle=handle,
        query_id=archive.query_id,
        bottom_cursor=archive.bottom_cursor,
        page_count=archive.pages,
        last_rate={
            "limit": archive.last_rate.limit,
            "remaining": archive.last_rate.remaining,
            "reset": archive.last_rate.reset_unix,
        },
        post_ids=len(archive.posts),
    )


def run_archive(
    seed: TimelinePage,
    template: GraphQLTemplate,
    fetch: Callable[[GraphQLTemplate, str], tuple[int, dict[str, str], Any]],
    *,
    max_posts: int,
    now: Callable[[], float],
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
    max_pages: int = 0,
    max_429_retries: int = MAX_429_RETRIES,
    page_delay: Callable[[RateLimit], float] | None = None,
    on_page: Callable[[ArchiveResult], None] | None = None,
    existing_posts: list[dict[str, str]] | None = None,
    resume_cursor: str | None = None,
    resume_pages: int = 0,
    resume_page_sizes: list[int] | None = None,
) -> ArchiveResult:
    prior = list(existing_posts or [])
    prior_count = len(prior)
    posts, _seed_added, _seed_updated = merge_posts(prior, seed.posts)
    if resume_pages > 0:
        pages = resume_pages
        page_sizes = list(resume_page_sizes or [])
    else:
        pages = 1 if seed.status == 200 else 0
        page_sizes = [len(seed.posts)] if seed.posts else []
    cursor = resume_cursor or seed.bottom_cursor
    last_rate = seed.rate
    pending_window_wait = seed.rate.remaining_exhausted() and seed.status == 200
    retries_429 = 0
    dup_streak = 0

    def finish(stop: str) -> ArchiveResult:
        if max_posts > 0 and len(posts) > max_posts and prior_count < max_posts:
            clipped = posts[:max_posts]
        else:
            clipped = posts
        return ArchiveResult(
            posts=clipped,
            pages=pages,
            stop=stop,
            last_rate=last_rate,
            operation_name=template.operation_name,
            query_id=template.query_id,
            page_sizes=page_sizes,
            bottom_cursor=cursor,
        )

    def cap_posts() -> bool:
        return max_posts > 0 and len(posts) >= max_posts

    def cap_pages() -> bool:
        return max_pages > 0 and pages >= max_pages

    def flush(stop: str) -> None:
        if on_page is not None:
            on_page(finish(stop))

    if pages > 0 or posts:
        flush("page")
    if cap_posts():
        return finish("max_posts")
    if cap_pages():
        return finish("max_pages")
    if pending_window_wait and last_rate.reset_unix is None:
        return finish("rate_limited")

    while cursor:
        did_window_wait = False
        if pending_window_wait:
            if last_rate.reset_unix is None:
                return finish("rate_limited")
            sleep(wait_seconds(last_rate, now(), jitter()))
            pending_window_wait = False
            did_window_wait = True
        if page_delay is not None and not did_window_wait:
            sleep(page_delay(last_rate))
        status, headers, body = fetch(template, cursor)
        last_rate = rate_limit_from_headers(headers)
        if should_retry_backoff(status):
            if retries_429 >= max_429_retries:
                return finish("rate_limited")
            sleep(wait_seconds(last_rate, now(), jitter()))
            retries_429 += 1
            continue
        if status != 200:
            return finish("fail")
        retries_429 = 0
        page = page_from_payload(body, headers, status, template.operation_name)
        posts, added, metrics_updated = merge_posts(posts, page.posts)
        pages += 1
        page_sizes.append(len(page.posts))
        cursor = page.bottom_cursor
        flush("page")
        if cap_posts():
            return finish("max_posts")
        if cap_pages():
            return finish("max_pages")
        # Metric enrich counts as progress so a metrics re-walk does not false-exhaust.
        if added == 0 and metrics_updated == 0:
            dup_streak += 1
        else:
            dup_streak = 0
        if (
            not cursor
            or (added == 0 and metrics_updated == 0 and len(page.posts) == 0)
            or dup_streak >= DUP_PAGE_STREAK
        ):
            return finish("exhausted")
        if last_rate.remaining_exhausted() and last_rate.reset_unix is None:
            return finish("rate_limited")
        pending_window_wait = last_rate.remaining_exhausted()

    return finish("exhausted")


def recipe_from_template(handle: str, template: GraphQLTemplate) -> RecipeDraft:
    return RecipeDraft(
        version=1,
        warning=RECIPE_WARNING,
        capture="graphql-cursor",
        handle=handle,
        operation_name=template.operation_name,
        query_id=template.query_id,
        method=template.method,
        url_template=template.url_template,
        variable_keys=sorted(template.variables),
        feature_keys=sorted(template.features),
        header_names=list(template.header_names),
        cursor="bottom",
    )


def recipe_has_secret(recipe: RecipeDraft, forbidden: str) -> bool:
    return forbidden in json.dumps(asdict(recipe), ensure_ascii=True)


def posts_file(handle: str, archive: ArchiveResult) -> PostsFile:
    return PostsFile(
        handle=handle,
        source=archive.operation_name,
        query_id=archive.query_id,
        exhausted=archive.stop == "exhausted",
        stop=archive.stop,
        page_count=archive.pages,
        page_sizes=archive.page_sizes,
        rate_limit={
            "limit": archive.last_rate.limit,
            "remaining": archive.last_rate.remaining,
            "reset": archive.last_rate.reset_unix,
        },
        posts=archive.posts,
    )


def write_outputs(
    out_dir: Path, handle: str, archive: ArchiveResult, template: GraphQLTemplate
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    posts_path = out_dir / "posts.json"
    recipe_path = out_dir / "recipe-draft.json"
    checkpoint_path = out_dir / "checkpoint.json"
    atomic_write_json(posts_path, asdict(posts_file(handle, archive)))
    atomic_write_json(checkpoint_path, asdict(checkpoint_from_archive(handle, archive)))
    atomic_write_json(
        recipe_path, asdict(recipe_from_template(handle, template))
    )
    return posts_path, recipe_path


def human_scroll_burst(page: Any, rng: random.Random) -> None:
    n = rng.randint(BURST_WHEEL_MIN, BURST_WHEEL_MAX)
    for i in range(n):
        page.mouse.wheel(0, rng.randint(40, 180))
        if i < n - 1:
            time.sleep(rng.uniform(0.012, 0.055))
    if rng.random() < OVERSHOOT_P:
        time.sleep(rng.uniform(0.08, 0.16))
        page.mouse.wheel(0, -rng.randint(*OVERSHOOT_PX))


def human_read_pause(rng: random.Random, intent: str = "skim") -> None:
    lo, hi = PAUSE[intent]
    time.sleep(rng.uniform(lo, hi))


def handle_out_dir(out: str, handle: str) -> Path:
    root = Path(out)
    if root.name.lower() == handle.lower():
        return root
    return root / handle


def attach_context(playwright: Any, args: argparse.Namespace) -> tuple[Any, Any, bool]:
    if args.cdp:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        if not browser.contexts:
            raise RuntimeError(f"CDP {args.cdp} has no browser context")
        context = browser.contexts[0]
        return context, browser, False
    user_data_dir = Path(args.user_data_dir)
    user_data_dir.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        str(user_data_dir),
        channel="chrome",
        headless=False,
        no_viewport=True,
    )
    return context, None, True


def live_fetch(
    page: Any, live_headers: dict[str, str]
) -> Callable[[GraphQLTemplate, str], tuple[int, dict[str, str], Any]]:
    headers = replay_headers(live_headers)

    def fetch_in_page(
        url: str, method: str, body: dict[str, Any] | None
    ) -> tuple[int, dict[str, str], Any]:
        raw = json.dumps(body, separators=(",", ":")) if body is not None else None
        payload = page.evaluate(
            """async ({url, method, headerPairs, rawBody}) => {
                const headers = Object.fromEntries(headerPairs);
                const init = {method, credentials: 'include', headers};
                if (rawBody) init.body = rawBody;
                const res = await fetch(url, init);
                const text = await res.text();
                const rh = {};
                res.headers.forEach((v, k) => { rh[k] = v; });
                return {status: res.status, text, headers: rh};
            }""",
            {
                "url": url,
                "method": method,
                "headerPairs": [[k, v] for k, v in headers.items()],
                "rawBody": raw,
            },
        )
        text = (payload or {}).get("text") or ""
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        return (
            int((payload or {}).get("status") or 0),
            lower_headers((payload or {}).get("headers") or {}),
            parsed,
        )

    def fetch(
        template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        url, body = next_graphql_request(template, cursor)
        try:
            if body is None:
                response = page.request.get(url, headers=headers, timeout=60000)
            else:
                response = page.request.post(
                    url,
                    headers={**headers, "content-type": "application/json"},
                    data=json.dumps(body, separators=(",", ":")),
                    timeout=60000,
                )
            try:
                payload = response.json()
            except Exception:
                payload = None
            return int(response.status), lower_headers(response.headers), payload
        except Exception:
            return fetch_in_page(url, "GET" if body is None else "POST", body)

    return fetch


def capture_live(args: argparse.Namespace) -> int:
    handle = normalize_handle(args.handle)
    out_dir = handle_out_dir(args.out, handle)
    disk = load_disk_archive(out_dir)
    if args.max_posts > 0 and len(disk.posts) >= args.max_posts:
        print(out_dir / "posts.json")
        pages = disk.checkpoint.page_count if disk.checkpoint else 0
        print(
            f"stop=max_posts posts={len(disk.posts)} pages={pages}",
            file=sys.stderr,
        )
        return EXIT_OK
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        print(
            "Patchright is not installed. Install patchright-python. "
            "Camoufox is the fallback named in references/stack.md.",
            file=sys.stderr,
        )
        return EXIT_FAIL

    timeline_hits: list[tuple[GraphQLTemplate, TimelinePage]] = []
    live_headers: dict[str, str] = {}
    profile_url = f"https://x.com/{handle}"

    try:
        with sync_playwright() as playwright:
            archive: ArchiveResult | None = None
            template: GraphQLTemplate | None = None
            try:
                context, _, owned = attach_context(playwright, args)
            except Exception as exc:
                print(f"could not attach ({exc})", file=sys.stderr)
                return EXIT_FAIL
            page = context.pages[0] if context.pages else context.new_page()

            def on_response(response: Any) -> None:
                url = getattr(response, "url", "") or ""
                if not is_x_graphql(url):
                    return
                request = getattr(response, "request", None)
                method = getattr(request, "method", None) or "GET"
                raw_headers = getattr(request, "headers", None) if request else None
                names = header_names_only(
                    raw_headers if isinstance(raw_headers, dict) else {}
                )
                post_body = None
                if method.upper() == "POST" and request is not None:
                    try:
                        parsed = request.post_data_json
                        if isinstance(parsed, dict):
                            post_body = parsed
                    except Exception:
                        post_body = None
                template = template_from_request(url, method, names, post_body)
                if template is None or template.operation_name not in TIMELINE_OPS:
                    return
                try:
                    payload = response.json()
                except Exception:
                    payload = None
                page = page_from_payload(
                    payload,
                    getattr(response, "headers", None),
                    int(getattr(response, "status", 0) or 0),
                    template.operation_name,
                )
                timeline_hits.append((template, page))
                if isinstance(raw_headers, dict) and raw_headers:
                    live_headers.clear()
                    live_headers.update(lower_headers(raw_headers))

            page.on("response", on_response)
            try:
                with page.expect_response(
                    lambda r: is_x_graphql(r.url)
                    and parse_graphql_url(r.url)[1] in TIMELINE_OPS,
                    timeout=30000,
                ):
                    page.goto(profile_url, wait_until="domcontentloaded")
            except Exception:
                wall = login_wall_reason(page.url, page.title())
                if wall:
                    print(wall, file=sys.stderr)
                    if owned:
                        context.close()
                    return EXIT_LOGIN_WALL
            wall = login_wall_reason(page.url, page.title())
            if wall:
                print(wall, file=sys.stderr)
                if owned:
                    context.close()
                return EXIT_LOGIN_WALL
            try:
                body_text = page.inner_text("body", timeout=2000)
            except Exception:
                body_text = ""
            try:
                has_primary = page.locator('[data-testid="primaryColumn"]').count() > 0
            except Exception:
                has_primary = False
            if page_soft_blocked(page.url, body_text, has_primary):
                print(f"soft block at {page.url}", file=sys.stderr)
                if owned:
                    context.close()
                return EXIT_RATE_LIMITED
            rng = random.Random()
            human_scroll_burst(page, rng)
            human_read_pause(rng, "skim")
            wall = login_wall_reason(page.url, page.title())
            if wall:
                print(wall, file=sys.stderr)
                if owned:
                    context.close()
                return EXIT_LOGIN_WALL
            chosen = pick_timeline_page(timeline_hits)
            if chosen is None:
                print(
                    "no UserOriginalsTimeline or UserTweets response captured",
                    file=sys.stderr,
                )
                if owned:
                    context.close()
                return EXIT_FAIL
            template, seed = chosen
            sleeps: list[float] = []

            def sleep_live(seconds: float) -> None:
                print(f"rate window wait {seconds:.1f}s", file=sys.stderr)
                sleeps.append(seconds)
                time.sleep(seconds)

            resume_cursor = None
            resume_pages = 0
            if (
                disk.posts
                and disk.checkpoint is not None
                and disk.checkpoint.bottom_cursor
            ):
                resume_cursor = disk.checkpoint.bottom_cursor
                resume_pages = disk.checkpoint.page_count
                print(
                    f"resume cursor={resume_cursor} "
                    f"posts={len(disk.posts)} pages={resume_pages}",
                    file=sys.stderr,
                )

            def on_page(partial: ArchiveResult) -> None:
                write_outputs(out_dir, handle, partial, template)
                print(
                    f"flush posts={len(partial.posts)} "
                    f"pages={partial.pages} "
                    f"cursor={partial.bottom_cursor or '-'}",
                    file=sys.stderr,
                )

            archive = run_archive(
                seed,
                template,
                live_fetch(page, live_headers),
                max_posts=args.max_posts,
                max_pages=args.max_pages,
                now=time.time,
                sleep=sleep_live,
                jitter=lambda: random.uniform(*JITTER_RANGE),
                page_delay=lambda rate: inter_page_delay(
                    rate, args.min_delay, args.max_delay, rng
                ),
                on_page=on_page,
                existing_posts=disk.posts,
                resume_cursor=resume_cursor,
                resume_pages=resume_pages,
                resume_page_sizes=disk.page_sizes,
            )
            if owned:
                context.close()
    except Exception as exc:
        print(f"capture failed ({exc})", file=sys.stderr)
        return EXIT_FAIL

    if archive is None or template is None:
        return EXIT_FAIL
    posts_path, recipe_path = write_outputs(out_dir, handle, archive, template)
    print(posts_path)
    print(recipe_path)
    print(
        f"stop={archive.stop} posts={len(archive.posts)} pages={archive.pages}",
        file=sys.stderr,
    )
    if archive.stop == "rate_limited":
        return EXIT_RATE_LIMITED
    if archive.stop == "fail":
        return EXIT_FAIL
    return EXIT_OK


def _timeline_entry(rest_id: str, text: str, created_at: str) -> dict[str, Any]:
    return {
        "content": {
            "itemContent": {
                "tweet_results": {
                    "result": {
                        "rest_id": rest_id,
                        "legacy": {"full_text": text, "created_at": created_at},
                    }
                }
            }
        }
    }


def _cursor_entry(cursor_type: str, value: str) -> dict[str, Any]:
    return {
        "entryId": f"cursor-{cursor_type.lower()}-1",
        "content": {
            "entryType": "TimelineTimelineCursor",
            "cursorType": cursor_type,
            "value": value,
        },
    }


def _originals_body(posts: list[tuple[str, str]], cursor: str | None) -> dict[str, Any]:
    entries = [
        _timeline_entry(post_id, text, "Wed Sep 16 00:00:00 +0000 2026")
        for post_id, text in posts
    ]
    if cursor:
        entries.append(_cursor_entry("Bottom", cursor))
    return {
        "data": {
            "user": {
                "result": {
                    "timeline": {
                        "timeline": {
                            "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
                        }
                    }
                }
            }
        }
    }


def self_check() -> int:
    errors: list[str] = []
    handle = normalize_handle("@example")
    if handle != "example":
        errors.append(f"handle {handle}")
    originals_url = (
        "https://api.x.com/graphql/QueryIdOrig/UserOriginalsTimeline"
        "?variables=%7B%22userId%22%3A%221%22%2C%22count%22%3A20%7D"
        "&features=%7B%22longform%22%3Atrue%7D"
    )
    tweets_url = "https://api.x.com/graphql/QueryIdTweets/UserTweets?variables=%7B%7D"
    qid, op = parse_graphql_url(originals_url)
    if (qid, op) != ("QueryIdOrig", "UserOriginalsTimeline"):
        errors.append(f"parse UserOriginalsTimeline got {(qid, op)}")
    qid, op = parse_graphql_url(tweets_url)
    if (qid, op) != ("QueryIdTweets", "UserTweets"):
        errors.append(f"parse UserTweets got {(qid, op)}")
    if login_wall_reason("https://x.com/i/flow/login", "Home") is None:
        errors.append("login url not detected")
    if login_wall_reason("https://x.com/example", "Example") is not None:
        errors.append("profile url flagged as login wall")
    if not page_soft_blocked("https://x.com/account/access", "", True):
        errors.append("account access should soft-block")
    if not page_soft_blocked("https://x.com/home", "Something went wrong", False):
        errors.append("soft-block body missed")
    if page_soft_blocked("https://x.com/example", "Something went wrong", True):
        errors.append("primary column should not soft-block")

    secret = "auth_cookie_value_DO_NOT_WRITE"
    names = header_names_only({"Cookie": secret, "Authorization": "Bearer leaked-token"})
    if names != ("authorization", "cookie"):
        errors.append(f"header names {names}")
    if secret in names or "leaked-token" in names:
        errors.append("header values leaked into names")

    rate = rate_limit_from_headers(
        {
            "x-rate-limit-limit": "250",
            "x-rate-limit-remaining": "0",
            "x-rate-limit-reset": "1000",
        }
    )
    if rate != RateLimit(limit=250, remaining=0, reset_unix=1000):
        errors.append(f"rate parse {rate}")
    if not rate.remaining_exhausted():
        errors.append("remaining=0 should be exhausted")
    waited = wait_seconds(rate, now=990.0, jitter=1.5)
    if waited != 11.5:
        errors.append(f"wait_seconds {waited}")
    if wait_seconds(RateLimit(None, None, None), 0.0, 2.0) != 32.0:
        errors.append("missing reset should use XCli 30s + jitter")

    body = _originals_body(
        [("101", "first originals post"), ("102", "second originals post")],
        "cursor-bottom-aaa",
    )
    extracted = extract_posts(body)
    if [p["id"] for p in extracted] != ["101", "102"]:
        errors.append(f"originals posts {extracted}")
    if extract_bottom_cursor(body) != "cursor-bottom-aaa":
        errors.append(f"cursor {extract_bottom_cursor(body)}")

    template = template_from_request(
        originals_url,
        "GET",
        ("authorization", "cookie", "x-csrf-token"),
    )
    if template is None or template.operation_name != "UserOriginalsTimeline":
        errors.append("template missing")
        return EXIT_FAIL
    if "cursor" in template.variables:
        errors.append("template kept cursor")
    if template.variables.get("userId") != "1":
        errors.append(f"userId {template.variables}")
    next_url, next_body = next_graphql_request(template, "cursor-bottom-bbb")
    if next_body is not None:
        errors.append("GET should not post a body")
    if "cursor-bottom-bbb" not in next_url:
        errors.append("next url missing cursor")
    if secret in next_url:
        errors.append("next url contains secret")

    recipe = recipe_from_template(handle, template)
    if recipe_has_secret(recipe, secret) or recipe_has_secret(recipe, "leaked-token"):
        errors.append("recipe leaked fixture secret")
    if recipe.header_names != ["authorization", "cookie", "x-csrf-token"]:
        errors.append(f"recipe headers {recipe.header_names}")

    seed = page_from_payload(
        body,
        {
            "x-rate-limit-limit": "250",
            "x-rate-limit-remaining": "5",
            "x-rate-limit-reset": "2000",
        },
        200,
        "UserOriginalsTimeline",
    )
    if len(seed.posts) != 2 or seed.bottom_cursor != "cursor-bottom-aaa":
        errors.append(f"seed page {seed}")

    fetches: list[str] = []
    sleeps: list[float] = []

    def fake_fetch(
        _template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        fetches.append(cursor)
        if len(fetches) == 1:
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "0",
                    "x-rate-limit-reset": "50",
                },
                _originals_body(
                    [("201", "page two post")],
                    "cursor-bottom-ccc",
                ),
            )
        return (
            429,
            {
                "x-rate-limit-limit": "250",
                "x-rate-limit-remaining": "0",
                "x-rate-limit-reset": "50",
            },
            {},
        )

    archive = run_archive(
        seed,
        template,
        fake_fetch,
        max_posts=0,
        now=lambda: 40.0,
        sleep=sleeps.append,
        jitter=lambda: 2.0,
    )
    if [p["id"] for p in archive.posts] != ["101", "102", "201"]:
        errors.append(f"paginated posts {archive.posts}")
    if archive.stop != "rate_limited":
        errors.append(f"stop {archive.stop}")
    if archive.page_sizes != [2, 1]:
        errors.append(f"page sizes {archive.page_sizes}")
    if sleeps != [12.0, 12.0]:
        errors.append(f"sleeps {sleeps}")
    if fetches != ["cursor-bottom-aaa", "cursor-bottom-ccc", "cursor-bottom-ccc"]:
        errors.append(f"fetches {fetches}")

    window_fetches: list[str] = []
    window_sleeps: list[float] = []
    flushed: list[int] = []

    def two_windows(
        _template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        window_fetches.append(cursor)
        if cursor == "cursor-bottom-aaa":
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "0",
                    "x-rate-limit-reset": "50",
                },
                _originals_body([("201", "page two post")], "cursor-window-2"),
            )
        if cursor == "cursor-window-2":
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "0",
                    "x-rate-limit-reset": "80",
                },
                _originals_body([("301", "page three after reset")], "cursor-window-3"),
            )
        return (
            200,
            {
                "x-rate-limit-limit": "250",
                "x-rate-limit-remaining": "249",
                "x-rate-limit-reset": "80",
            },
            _originals_body([("401", "last page")], None),
        )

    windows = run_archive(
        seed,
        template,
        two_windows,
        max_posts=0,
        now=lambda: 40.0,
        sleep=window_sleeps.append,
        jitter=lambda: 2.0,
        on_page=lambda partial: flushed.append(partial.pages),
    )
    if [p["id"] for p in windows.posts] != ["101", "102", "201", "301", "401"]:
        errors.append(f"multi-window posts {windows.posts}")
    if windows.stop != "exhausted":
        errors.append(f"multi-window stop {windows.stop}")
    if window_sleeps != [12.0, 42.0]:
        errors.append(f"multi-window sleeps {window_sleeps}")
    if flushed != [1, 2, 3, 4]:
        errors.append(f"flush pages {flushed}")

    paged = run_archive(
        seed,
        template,
        two_windows,
        max_posts=0,
        max_pages=2,
        now=lambda: 40.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    if paged.stop != "max_pages" or paged.pages != 2:
        errors.append(f"max-pages {paged.stop} {paged.pages}")

    no_reset = run_archive(
        page_from_payload(
            _originals_body([("501", "seed")], "cursor-none"),
            {
                "x-rate-limit-limit": "250",
                "x-rate-limit-remaining": "0",
            },
            200,
            "UserOriginalsTimeline",
        ),
        template,
        lambda *_: (_ for _ in ()).throw(AssertionError("no reset must not fetch")),
        max_posts=0,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    if no_reset.stop != "rate_limited" or [p["id"] for p in no_reset.posts] != ["501"]:
        errors.append(f"no-reset {no_reset}")

    delay = inter_page_delay(RateLimit(250, 5, 1000), 0.5, 1.5, random.Random(0))
    if delay < 1.5:
        errors.append(f"volume delay {delay}")

    capped = run_archive(
        seed,
        template,
        lambda *_: (_ for _ in ()).throw(AssertionError("should not fetch")),
        max_posts=1,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    if [p["id"] for p in capped.posts] != ["101"] or capped.stop != "max_posts":
        errors.append(f"max-posts {capped}")

    exhausted_seed = page_from_payload(
        _originals_body([("301", "only page")], None),
        {"x-rate-limit-limit": "250", "x-rate-limit-remaining": "249"},
        200,
        "UserOriginalsTimeline",
    )
    exhausted = run_archive(
        exhausted_seed,
        template,
        lambda *_: (_ for _ in ()).throw(AssertionError("no page 2")),
        max_posts=0,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    if exhausted.stop != "exhausted" or [p["id"] for p in exhausted.posts] != ["301"]:
        errors.append(f"exhausted {exhausted}")

    fallback = pick_timeline_page(
        [
            (
                GraphQLTemplate(
                    "UserTweets",
                    "QueryIdTweets",
                    "GET",
                    "https://api.x.com/graphql/QueryIdTweets/UserTweets",
                    {},
                    {},
                    {},
                    ("authorization",),
                ),
                page_from_payload(
                    _originals_body([("401", "tweets fallback")], None),
                    {},
                    200,
                    "UserTweets",
                ),
            )
        ]
    )
    if fallback is None or fallback[0].operation_name != "UserTweets":
        errors.append("UserTweets fallback missing")

    out = Path("/tmp/search-x-profile-self-check/example")
    posts_path, recipe_path = write_outputs(out, handle, archive, template)
    checkpoint_path = out / "checkpoint.json"
    written = (
        posts_path.read_text(encoding="utf-8")
        + recipe_path.read_text(encoding="utf-8")
        + checkpoint_path.read_text(encoding="utf-8")
    )
    if secret in written or "leaked-token" in written:
        errors.append("wrote secret to disk")
    if "first originals post" not in posts_path.read_text(encoding="utf-8"):
        errors.append("posts file missing text")
    if '"limit": 250' not in posts_path.read_text(encoding="utf-8"):
        errors.append("posts file missing live-observed limit field")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("handle") != handle:
        errors.append(f"checkpoint handle {checkpoint}")
    if checkpoint.get("query_id") != template.query_id:
        errors.append(f"checkpoint query_id {checkpoint}")
    if checkpoint.get("bottom_cursor") != archive.bottom_cursor:
        errors.append(f"checkpoint cursor {checkpoint}")
    if checkpoint.get("page_count") != archive.pages:
        errors.append(f"checkpoint page_count {checkpoint}")
    if checkpoint.get("post_ids") != len(archive.posts):
        errors.append(f"checkpoint post_ids {checkpoint}")
    if "last_rate" not in checkpoint:
        errors.append("checkpoint missing last_rate")

    prior_dir = Path("/tmp/search-x-profile-self-check/atomic")
    prior_path = prior_dir / "posts.json"
    atomic_write_json(
        prior_path, {"posts": [{"id": "keep", "text": "prior keep"}]}
    )

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("mid-write")

    try:
        atomic_write_json(prior_path, {"posts": [{"id": "new"}]}, dumps=boom)
        errors.append("atomic write should fail when dumps raises")
    except RuntimeError:
        pass
    kept = prior_path.read_text(encoding="utf-8")
    if "prior keep" not in kept:
        errors.append("atomic write dropped prior posts")

    resume_dir = Path("/tmp/search-x-profile-self-check/resume")
    resume_seed_archive = ArchiveResult(
        posts=[
            {
                "id": "101",
                "text": "first originals post",
                "created_at": "Wed Sep 16 00:00:00 +0000 2026",
            },
            {
                "id": "102",
                "text": "second originals post",
                "created_at": "Wed Sep 16 00:00:00 +0000 2026",
            },
        ],
        pages=4,
        stop="page",
        last_rate=RateLimit(250, 0, 50),
        operation_name="UserOriginalsTimeline",
        query_id=template.query_id,
        page_sizes=[2, 1, 1, 1],
        bottom_cursor="cursor-resume-zzz",
    )
    write_outputs(resume_dir, handle, resume_seed_archive, template)
    loaded = load_disk_archive(resume_dir)
    if loaded.checkpoint is None:
        errors.append("resume checkpoint missing")
    elif loaded.checkpoint.bottom_cursor != "cursor-resume-zzz":
        errors.append(f"loaded cursor {loaded.checkpoint.bottom_cursor}")
    if [p["id"] for p in loaded.posts] != ["101", "102"]:
        errors.append(f"loaded posts {loaded.posts}")

    resume_fetches: list[str] = []

    def resume_fetch(
        _template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        resume_fetches.append(cursor)
        return (
            200,
            {
                "x-rate-limit-limit": "250",
                "x-rate-limit-remaining": "249",
                "x-rate-limit-reset": "80",
            },
            _originals_body(
                [("102", "dup from disk"), ("901", "resumed page")],
                None,
            ),
        )

    resume_flushed: list[int] = []
    resumed = run_archive(
        seed,
        template,
        resume_fetch,
        max_posts=0,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        existing_posts=loaded.posts,
        resume_cursor=(
            loaded.checkpoint.bottom_cursor if loaded.checkpoint else None
        ),
        resume_pages=loaded.checkpoint.page_count if loaded.checkpoint else 0,
        resume_page_sizes=loaded.page_sizes,
        on_page=lambda partial: resume_flushed.append(partial.pages),
    )
    if resume_fetches != ["cursor-resume-zzz"]:
        errors.append(f"resume fetched {resume_fetches}")
    if [p["id"] for p in resumed.posts] != ["101", "102", "901"]:
        errors.append(f"resume posts {resumed.posts}")
    if resumed.pages != 5:
        errors.append(f"resume pages {resumed.pages}")
    if 4 not in resume_flushed or 5 not in resume_flushed:
        errors.append(f"resume flush {resume_flushed}")

    rewalk_existing = [
        {
            "id": "101",
            "text": "first originals post",
            "created_at": "",
        },
        {
            "id": "102",
            "text": "second originals post",
            "created_at": "",
        },
        {
            "id": "201",
            "text": "already on disk page two",
            "created_at": "",
        },
        {
            "id": "202",
            "text": "already on disk page two b",
            "created_at": "",
        },
    ]
    rewalk_fetches: list[str] = []

    def rewalk_fetch(
        _template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        rewalk_fetches.append(cursor)
        if cursor == "cursor-bottom-aaa":
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "248",
                    "x-rate-limit-reset": "80",
                },
                _originals_body(
                    [
                        ("201", "already on disk page two"),
                        ("202", "already on disk page two b"),
                    ],
                    "cursor-rewalk-2",
                ),
            )
        if cursor == "cursor-rewalk-2":
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "247",
                    "x-rate-limit-reset": "80",
                },
                _originals_body([("901", "new after rewalk")], "cursor-rewalk-3"),
            )
        if cursor == "cursor-rewalk-3":
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "246",
                    "x-rate-limit-reset": "80",
                },
                _originals_body([("902", "last after rewalk")], None),
            )
        raise AssertionError(f"unexpected rewalk cursor {cursor}")

    rewalked = run_archive(
        seed,
        template,
        rewalk_fetch,
        max_posts=0,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        existing_posts=rewalk_existing,
    )
    if rewalk_fetches != [
        "cursor-bottom-aaa",
        "cursor-rewalk-2",
        "cursor-rewalk-3",
    ]:
        errors.append(f"rewalk fetched {rewalk_fetches}")
    if [p["id"] for p in rewalked.posts] != [
        "101",
        "102",
        "201",
        "202",
        "901",
        "902",
    ]:
        errors.append(f"rewalk posts {[p['id'] for p in rewalked.posts]}")
    if rewalked.stop != "exhausted":
        errors.append(f"rewalk stop {rewalked.stop}")
    if rewalked.pages != 4:
        errors.append(f"rewalk pages {rewalked.pages}")

    empty_fetches: list[str] = []

    def empty_page_fetch(
        _template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        empty_fetches.append(cursor)
        if cursor == "cursor-bottom-aaa":
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "249",
                    "x-rate-limit-reset": "80",
                },
                _originals_body([], "cursor-empty-next"),
            )
        raise AssertionError(f"empty page must not follow {cursor}")

    empty_page = run_archive(
        seed,
        template,
        empty_page_fetch,
        max_posts=0,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        existing_posts=rewalk_existing,
    )
    if empty_fetches != ["cursor-bottom-aaa"]:
        errors.append(f"empty page fetched {empty_fetches}")
    if empty_page.stop != "exhausted":
        errors.append(f"empty page stop {empty_page.stop}")
    if [p["id"] for p in empty_page.posts] != ["101", "102", "201", "202"]:
        errors.append(f"empty page posts {[p['id'] for p in empty_page.posts]}")

    dup_streak_fetches: list[str] = []

    def dup_streak_fetch(
        _template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        dup_streak_fetches.append(cursor)
        n = len(dup_streak_fetches)
        nxt: str | None = f"cursor-dup-{n}"
        if n > 12:
            nxt = None
        return (
            200,
            {
                "x-rate-limit-limit": "250",
                "x-rate-limit-remaining": "240",
                "x-rate-limit-reset": "80",
            },
            _originals_body(
                [("101", "first originals post"), ("102", "second originals post")],
                nxt,
            ),
        )

    dup_streaked = run_archive(
        seed,
        template,
        dup_streak_fetch,
        max_posts=0,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        existing_posts=rewalk_existing[:2],
    )
    if len(dup_streak_fetches) != 5:
        errors.append(f"dup streak fetched {dup_streak_fetches}")
    if [p["id"] for p in dup_streaked.posts] != ["101", "102"]:
        errors.append(f"dup streak posts {[p['id'] for p in dup_streaked.posts]}")
    if dup_streaked.stop != "exhausted":
        errors.append(f"dup streak stop {dup_streaked.stop}")
    if dup_streaked.pages != 6:
        errors.append(f"dup streak pages {dup_streaked.pages}")

    four_fetches: list[str] = []

    def four_then_new_fetch(
        _template: GraphQLTemplate, cursor: str
    ) -> tuple[int, dict[str, str], Any]:
        four_fetches.append(cursor)
        n = len(four_fetches)
        if n <= 4:
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "245",
                    "x-rate-limit-reset": "80",
                },
                _originals_body(
                    [("101", "first originals post")],
                    f"cursor-four-{n}",
                ),
            )
        if n == 5:
            return (
                200,
                {
                    "x-rate-limit-limit": "250",
                    "x-rate-limit-remaining": "244",
                    "x-rate-limit-reset": "80",
                },
                _originals_body([("801", "new after four dups")], None),
            )
        raise AssertionError(f"four-then-new extra fetch {cursor}")

    four_then = run_archive(
        seed,
        template,
        four_then_new_fetch,
        max_posts=0,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        existing_posts=rewalk_existing[:2],
    )
    if four_fetches != [
        "cursor-bottom-aaa",
        "cursor-four-1",
        "cursor-four-2",
        "cursor-four-3",
        "cursor-four-4",
    ]:
        errors.append(f"four-then-new fetched {four_fetches}")
    if [p["id"] for p in four_then.posts] != ["101", "102", "801"]:
        errors.append(f"four-then-new posts {[p['id'] for p in four_then.posts]}")
    if four_then.stop != "exhausted":
        errors.append(f"four-then-new stop {four_then.stop}")
    if four_then.pages != 6:
        errors.append(f"four-then-new pages {four_then.pages}")

    disk_cap = run_archive(
        seed,
        template,
        lambda *_: (_ for _ in ()).throw(AssertionError("disk cap must not fetch")),
        max_posts=2,
        now=lambda: 0.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        existing_posts=[
            {
                "id": "101",
                "text": "first originals post",
                "created_at": "",
            },
            {
                "id": "102",
                "text": "second originals post",
                "created_at": "",
            },
            {
                "id": "103",
                "text": "already on disk",
                "created_at": "",
            },
        ],
    )
    if [p["id"] for p in disk_cap.posts] != ["101", "102", "103"]:
        errors.append(f"max-posts dropped disk posts {disk_cap.posts}")
    if disk_cap.stop != "max_posts":
        errors.append(f"disk cap stop {disk_cap.stop}")

    flush_dir = Path("/tmp/search-x-profile-self-check/flush-pages")
    flush_counts: list[int] = []

    def flush_to_disk(partial: ArchiveResult) -> None:
        write_outputs(flush_dir, handle, partial, template)
        payload = json.loads((flush_dir / "posts.json").read_text(encoding="utf-8"))
        flush_counts.append(len(payload.get("posts") or []))
        ck = json.loads((flush_dir / "checkpoint.json").read_text(encoding="utf-8"))
        if ck.get("post_ids") != flush_counts[-1]:
            errors.append(f"flush checkpoint post_ids {ck}")

    flushed_run = run_archive(
        seed,
        template,
        two_windows,
        max_posts=0,
        now=lambda: 40.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
        on_page=flush_to_disk,
    )
    if flush_counts != [2, 3, 4, 5]:
        errors.append(f"per-page flush counts {flush_counts}")
    if [p["id"] for p in flushed_run.posts] != ["101", "102", "201", "301", "401"]:
        errors.append(f"per-page flush posts {flushed_run.posts}")

    copied = replay_headers({"Cookie": secret, "Host": "api.x.com", "x-csrf-token": "t"})
    if "host" in copied or copied.get("cookie") != secret:
        errors.append(f"replay headers {copied}")

    skill = Path(__file__).resolve().parents[1] / "SKILL.md"
    stack = Path(__file__).resolve().parents[1] / "references" / "stack.md"
    rates = Path(__file__).resolve().parents[1] / "references" / "rate-limits.md"
    skill_text = skill.read_text(encoding="utf-8")
    stack_text = stack.read_text(encoding="utf-8")
    rates_text = rates.read_text(encoding="utf-8")
    if "name: search-x-profile" not in skill_text:
        errors.append("SKILL.md missing frontmatter name")
    if "website-to-api" not in skill_text:
        errors.append("SKILL.md must point at website-to-api")
    if "Patchright" not in stack_text or "Camoufox" not in stack_text:
        errors.append("stack.md missing browser shortlist")
    if "ghost-cursor" not in stack_text or "XCli" not in stack_text:
        errors.append("stack.md missing human-scroll shortlist")
    if "XClientTransaction" not in stack_text:
        errors.append("stack.md missing XClientTransaction fence")
    if "250" not in rates_text or "x-rate-limit-remaining" not in rates_text:
        errors.append("rate-limits.md missing live-observed headers")
    if "21" not in rates_text or "27" not in rates_text:
        errors.append("rate-limits.md missing page-size range")
    if "exit 3" not in rates_text.lower() and "exit code 3" not in rates_text.lower():
        errors.append("rate-limits.md must name exit 3")
    if "every later window" not in rates_text and "each later window" not in rates_text:
        errors.append("rate-limits.md must say remaining=0 waits continue across windows")
    if "9225" not in skill_text:
        errors.append("SKILL.md must document CDP port 9225")
    if "--min-delay" not in skill_text:
        errors.append("SKILL.md must name --min-delay")
    if "./data/search-x-profile/" not in skill_text:
        errors.append("SKILL.md must name the gitignored dump path")
    if "checkpoint.json" not in skill_text:
        errors.append("SKILL.md must name checkpoint.json")
    if "rename" not in skill_text.lower():
        errors.append("SKILL.md must name temp plus rename")
    if "resume" not in skill_text.lower():
        errors.append("SKILL.md must name resume")
    if "checkpoint.json" not in rates_text:
        errors.append("rate-limits.md must name checkpoint.json")
    if "resume" not in rates_text.lower():
        errors.append("rate-limits.md must name resume")
    if "rename" not in rates_text.lower():
        errors.append("rate-limits.md must name temp plus rename")

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
    ap.add_argument(
        "--max-posts",
        type=int,
        default=0,
        help="stop after N posts. 0 means paginate until the cursor is gone",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="stop after N timeline pages including bootstrap. 0 means no page cap",
    )
    ap.add_argument(
        "--cdp",
        nargs="?",
        const=DEFAULT_CDP,
        default="",
        help=f"attach box Chrome over CDP (default {DEFAULT_CDP} when flag is bare)",
    )
    ap.add_argument(
        "--user-data-dir",
        default=DEFAULT_PROFILE,
        help="persistent Chrome profile when --cdp is omitted",
    )
    ap.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="parent directory. Posts land in <out>/<handle>/",
    )
    ap.add_argument(
        "--min-delay",
        type=float,
        default=DEFAULT_MIN_DELAY,
        help="minimum seconds between GraphQL pages (default 0.5)",
    )
    ap.add_argument(
        "--max-delay",
        type=float,
        default=DEFAULT_MAX_DELAY,
        help="maximum seconds between GraphQL pages (default 1.5)",
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
    try:
        normalize_handle(args.handle)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAIL
    if args.max_posts < 0:
        print("--max-posts must be 0 or greater", file=sys.stderr)
        return EXIT_FAIL
    if args.max_pages < 0:
        print("--max-pages must be 0 or greater", file=sys.stderr)
        return EXIT_FAIL
    return capture_live(args)


if __name__ == "__main__":
    raise SystemExit(main())

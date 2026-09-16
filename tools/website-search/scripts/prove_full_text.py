#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import py_compile
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "research_website.py"

FIXTURE = """<!doctype html>
<html>
<head>
  <title>Fixture Firm</title>
  <meta name="description" content="A test page with visible copy.">
  <script>var hide = "do not keep";</script>
  <style>body { color: red; }</style>
</head>
<body>
  <h1>Welcome to Fixture Firm</h1>
  <h2>Injury practice</h2>
  <p>We help people after a crash. Call the office today.</p>
  <noscript>hidden noscript copy</noscript>
  <ul>
    <li>Free consult</li>
    <li>Same-day call back</li>
  </ul>
  <img src="/hero.jpg" alt="Attorney at the courthouse">
  <button>Book a consult</button>
  <a href="/contact">Contact us</a>
</body>
</html>
"""


def _load():
    spec = importlib.util.spec_from_file_location("research_website", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load research_website.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    try:
        py_compile.compile(str(SCRIPT), doraise=True)
        rw = _load()
        rec = rw.extract_page(
            FIXTURE, "https://fixture.test/", "fixture.test", False, True
        )
        if len(rec.get("full_text") or "") <= 40:
            raise AssertionError("full_text length >> 0")
        if "We help people after a crash" not in rec["full_text"]:
            raise AssertionError("paragraph missing from full_text")
        if "do not keep" in rec["full_text"] or "hidden noscript copy" in rec["full_text"]:
            raise AssertionError("noise tags leaked into full_text")
        if rec.get("title") != "Fixture Firm":
            raise AssertionError(f"title: {rec.get('title')}")
        if rec.get("headings") != ["Welcome to Fixture Firm", "Injury practice"]:
            raise AssertionError(f"headings: {rec.get('headings')}")
        if rec.get("image_alts") != ["Attorney at the courthouse"]:
            raise AssertionError(f"image_alts: {rec.get('image_alts')}")
        if "Free consult" not in (rec.get("list_items") or []):
            raise AssertionError(f"list_items: {rec.get('list_items')}")
        ctas = rec.get("cta_like") or []
        if "Book a consult" not in ctas:
            raise AssertionError(f"cta_like missing button: {ctas}")
        if "Contact us" not in ctas:
            raise AssertionError(f"cta_like missing link: {ctas}")
        if rec.get("word_count") != len(rec["full_text"].split()):
            raise AssertionError(
                f"word_count {rec.get('word_count')} != {len(rec['full_text'].split())}"
            )
        if rec.get("full_text_truncated") is not False:
            raise AssertionError("short page marked truncated")

        off = rw.extract_page(
            FIXTURE, "https://fixture.test/", "fixture.test", False, False
        )
        if off.get("full_text") != "":
            raise AssertionError("no-full-text still stored body")
        if off.get("full_text_truncated") is not False:
            raise AssertionError("no-full-text marked truncated")
        if not (off.get("word_count") or 0) > 0:
            raise AssertionError("no-full-text word_count is 0")
        if off.get("headings") != rec.get("headings"):
            raise AssertionError("signals dropped when full_text off")

        hard = "<p>" + ("q" * (rw.FULL_TEXT_CHAR_CAP + 80)) + "</p>"
        cut = rw.extract_page(
            hard, "https://fixture.test/", "fixture.test", False, True
        )
        if cut.get("full_text_truncated") is not True:
            raise AssertionError("hard cut did not set truncated")
        if cut.get("full_text") != "q" * rw.FULL_TEXT_CHAR_CAP:
            raise AssertionError("hard cut missed the cap")
        if cut.get("word_count") != len(cut["full_text"].split()):
            raise AssertionError("truncated word_count mismatch")

        blob = ("hello " * 80_000).strip()
        spaced = rw.extract_page(
            f"<p>{blob}</p>", "https://fixture.test/", "fixture.test", False, True
        )
        if spaced.get("full_text_truncated") is not True:
            raise AssertionError("whitespace cut did not set truncated")
        if len(spaced.get("full_text") or "") > rw.FULL_TEXT_CHAR_CAP:
            raise AssertionError("whitespace cut exceeded cap")
        if not (spaced.get("full_text") or "").endswith("hello"):
            raise AssertionError("whitespace cut tore a word")
        if spaced.get("word_count") != len(spaced["full_text"].split()):
            raise AssertionError("whitespace-cut word_count mismatch")

        long_words = " ".join(f"w{i}" for i in range(500))
        run = {
            "site": {
                "reg": "fixture.test",
                "host": "fixture.test",
                "origin": "https://fixture.test",
            },
            "start_url": "https://fixture.test/",
            "pages": [
                {
                    "url": "https://fixture.test/",
                    "final_url": "https://fixture.test/",
                    "status": "ok",
                    "http_code": 200,
                    "attempts": 1,
                    "title": "Fixture Firm",
                    "meta_description": "A test page with visible copy.",
                    "h1s": ["Welcome to Fixture Firm"],
                    "word_count": 500,
                    "full_text": long_words,
                    "full_text_truncated": False,
                    "headings": ["Welcome to Fixture Firm", "Injury practice"],
                    "image_alts": ["Attorney at the courthouse"],
                    "list_items": ["Free consult"],
                    "cta_like": ["Book a consult", "Contact us"],
                    "outbound_same_site_sample": [],
                    "emails": [],
                    "schema_types": [],
                    "llms_txt_mentions": [],
                }
            ],
            "flags": {"full_text": True, "concurrency": 1},
            "theme_hints": [],
            "emails_map": {},
            "llms_txt": {},
            "ai_bots": {},
            "errors": [],
        }
        with tempfile.TemporaryDirectory() as td:
            md_path = Path(td) / "digest.md"
            rw.write_markdown(md_path, run)
            md = md_path.read_text(encoding="utf-8")
            preview = " ".join(long_words.split()[: rw.DIGEST_PREVIEW_WORDS])
            if "## Page copy" not in md:
                raise AssertionError("Page copy section missing")
            if "full_text in JSON" not in md:
                raise AssertionError("JSON note missing")
            if preview not in md:
                raise AssertionError("400-word preview missing")
            if long_words in md:
                raise AssertionError("digest dumped entire full_text")
            run["flags"]["full_text"] = False
            off_path = Path(td) / "digest-off.md"
            rw.write_markdown(off_path, run)
            off_md = off_path.read_text(encoding="utf-8")
            if "`--no-full-text`" not in off_md:
                raise AssertionError("disabled flag not named")
            if preview in off_md:
                raise AssertionError("preview written when full text disabled")
        return 0
    except Exception as e:
        print(f"FAIL\t{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

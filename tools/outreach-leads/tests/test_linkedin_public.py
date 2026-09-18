"""Offline regression tests. Fixtures are synthetic; no live LinkedIn calls."""
import contextlib
import importlib.util
import io
import json
import socket
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "outreach-leads" / "scripts"))
import linkedin_public as public
import linkedin_contacts as contacts
from company_identity import fetch_verified
from test_indeed_public import (COMPANY as INDEED_COMPANY, listing_page as indeed_listing,
                                detail_page as indeed_detail, FakeHTTP as IndeedFake)

spec = importlib.util.spec_from_file_location("outreach_prep_test", TOOLS / "outreach-prep" / "scripts" / "run_outreach_prep.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)
COMPANY = public.BASE + "/company/example-law/"
PERSON = public.BASE + "/in/example-person/"


def company_page(name="Example Person", company=COMPANY, ids="101"):
    return f'''<html><head><title>Example Law | LinkedIn</title>
    <link rel="canonical" href="{company}"></head><body>
    <h1>Example Law</h1><dl><dt>Website</dt><dd><a href="https://example.test">Website</a></dd></dl>
    <a href="/jobs/example-jobs-worldwide?f_C={ids}">See jobs</a>
    <section data-test-id="employees-at"><h2>Employees at Example Law</h2>
    <ul><li class="base-main-card"><a href="{PERSON}?trk=sample">
    <h3 class="base-main-card__title">{name}</h3></a>
    <h4 class="base-main-card__subtitle">Managing Partner</h4>
    <img data-delayed-url="https://media.example.test/person.jpg"></li></ul></section>
    <section><h2>People also viewed</h2><li><a href="/in/unrelated/">Unrelated Person</a></li></section>
    <a href="/jobs/fake-jobs">Browse jobs: 800,000 open jobs</a>
    <a href="/login">Sign in</a></body></html>'''


def job(jid="501", employer=COMPANY, host="www.linkedin.com", expired=False):
    return f'''<li><div class="base-search-card" data-entity-urn="urn:li:jobPosting:{jid}">
    <a class="base-card__full-link" href="https://{host}/jobs/view/paralegal-{jid}?trk=test">View</a>
    <h3 class="base-search-card__title">PI Paralegal</h3>
    <h4><a class="hidden-nested-link" href="{employer}?trk=test">Example Law</a></h4>
    <span class="job-search-card__location">El Paso, TX</span>
    <time datetime="2026-09-18">1 day ago</time>
    {'No longer accepting applications' if expired else ''}</div></li>'''


def job_detail():
    return f'<section class="top-card-layout"><h1 class="top-card-layout__title">PI Paralegal</h1><a class="topcard__org-name-link" href="{COMPANY}">Example Law</a></section><section class="description"><div class="show-more-less-html__markup"><p>Full public description: prepare filings and review case records.</p></div></section>'


class FakeHTTP:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.pages:
            raise AssertionError("unexpected extra network call: " + url)
        result = self.pages.pop(0)
        if isinstance(result, public.Page):
            return public.Page(url, result.status, result.html, result.http_status, result.reason)
        return public.Page(url, "ok", result, 200)


class ParsingTests(unittest.TestCase):
    def test_company_url_canonicalizes_region_and_tracking(self):
        self.assertEqual(public.linkedin_url("https://uk.linkedin.com/company/Example-Law/jobs/?trk=x"), COMPANY)

    def test_unsafe_identities_rejected(self):
        for url in ("http://www.linkedin.com/company/example-law", "https://linkedin.com.evil.test/company/example-law", "https://user:secret@linkedin.com/company/example-law", "https://www.linkedin.com:444/company/example-law", "https://www.linkedin.com/company/example-law/../other", "https://www.linkedin.com/company/example-law/people/"):
            with self.subTest(url=url):
                self.assertEqual(public.linkedin_url(url), "")

    def test_people_are_employee_section_only(self):
        result = public.parse_company(company_page(), COMPANY)
        self.assertEqual(result["company_id"], "101")
        self.assertEqual(len(result["contacts"]), 1)
        person = result["contacts"][0]
        self.assertEqual(person["name"], "Example Person")
        self.assertEqual(person["title"], "Managing Partner")
        self.assertEqual(person["email"], "")
        self.assertEqual(person["source_url"], COMPANY)
        self.assertEqual(person["profile_url"], PERSON)

    def test_masked_names_not_promoted_to_people(self):
        result = public.parse_company(company_page("LinkedIn Member"), COMPANY)
        self.assertEqual(result["status"], "names_masked")
        self.assertEqual(result["contacts"], [])

    def test_influencer_accessibility_label_not_duplicated(self):
        result = public.parse_company(company_page("Example Person Example Person is an Influencer"), COMPANY)
        self.assertEqual(result["contacts"][0]["name"], "Example Person")

    def test_repeated_real_name_not_shortened(self):
        result = public.parse_company(company_page("John John"), COMPANY)
        self.assertEqual(result["contacts"][0]["name"], "John John")

    def test_unknown_company_markup_fails_closed(self):
        self.assertEqual(public.parse_company("<html><h1>Try again</h1></html>", COMPANY)["status"], "parse_error")

    def test_different_company_rejected(self):
        result = public.parse_company(company_page(company=public.BASE + "/company/other/"), COMPANY)
        self.assertEqual(result["status"], "company_mismatch")

    def test_affiliate_company_ids_not_guessed(self):
        self.assertIsNone(public.parse_company(company_page(ids="101%2C202"), COMPANY)["company_id"])

    def test_official_site_must_have_unique_company_link(self):
        self.assertEqual(public.discover_company(f'<a href="{COMPANY}">LinkedIn</a>'), COMPANY)
        self.assertEqual(public.discover_company(f'<a href="{COMPANY}">A</a><a href="/company/other/">B</a>'), "")

    def test_jobs_require_exact_employer_and_exclude_expired(self):
        html = job() + job("502", public.BASE + "/company/other/") + job("503", expired=True)
        result, count = public.parse_jobs(html, COMPANY, "101")
        self.assertEqual(count, 3)
        self.assertEqual([j["job_id"] for j in result], ["501"])
        self.assertEqual(result[0]["date_posted"], "2026-09-18")
        self.assertEqual(result[0]["location"], "El Paso, TX")

    def test_regional_job_links_are_supported(self):
        for host in ("ca.linkedin.com", "uk.linkedin.com", "www.linkedin.com"):
            with self.subTest(host=host):
                result, _ = public.parse_jobs(job(host=host), COMPANY, "101")
                self.assertEqual(result[0]["url"], public.BASE + "/jobs/view/501/")

    def test_deceptive_job_hosts_rejected(self):
        self.assertEqual(public.job_identity("https://linkedin.com.evil.test/jobs/view/123"), ("", ""))

    def test_nested_and_duplicate_cards_deduplicated(self):
        result, _ = public.parse_jobs(job() + job(), COMPANY, "101")
        self.assertEqual(len(result), 1)

    def test_generic_browse_jobs_not_counted(self):
        result, count = public.parse_jobs('<footer>Browse jobs: 800,000 open jobs</footer>', COMPANY, "101")
        self.assertEqual((result, count), ([], 0))

    def test_signup_wall_but_not_signin_cta(self):
        self.assertTrue(public.wall('<title>Sign Up | LinkedIn</title>', COMPANY))
        self.assertTrue(public.wall('', public.BASE + '/signup?foo=bar'))
        self.assertFalse(public.wall(company_page(), COMPANY))


class FetchTests(unittest.TestCase):
    def test_pagination_uses_card_count_and_deduplicates(self):
        client = FakeHTTP(job(), job() + job("502"), "")
        result, checks = public.fetch_hiring(client, COMPANY, "101")
        self.assertEqual(result["observed_job_count"], 2)
        self.assertTrue(result["is_hiring"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["pagination_exhausted"])
        self.assertEqual([parse_qs(urlsplit(u).query)["start"][0] for u, _ in client.calls], ["0", "1", "3"])
        self.assertEqual(len(checks), 3)

    def test_repeated_pages_stop(self):
        client = FakeHTTP(job(), job())
        result, _ = public.fetch_hiring(client, COMPANY, "101")
        self.assertEqual(result["stop_reason"], "repeated_page")
        self.assertEqual(result["observed_job_count"], 1)

    def test_job_and_page_caps(self):
        result, _ = public.fetch_hiring(FakeHTTP(job() + job("502")), COMPANY, "101", max_jobs=1)
        self.assertEqual(result["stop_reason"], "job_limit")
        self.assertEqual(result["observed_job_count"], 1)
        result, _ = public.fetch_hiring(FakeHTTP(job()), COMPANY, "101", max_pages=1)
        self.assertEqual(result["stop_reason"], "page_limit")

    def test_blocked_and_empty_are_not_not_hiring(self):
        for page, status in ((public.Page("", "blocked"), "unknown"), ("", "unknown"), ("<p>No jobs found</p>", "no_public_jobs_found"), ("<p>Something went wrong</p>", "unknown")):
            with self.subTest(page=page):
                result, _ = public.fetch_hiring(FakeHTTP(page), COMPANY, "101")
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["is_hiring"])

    def test_positive_evidence_survives_later_rate_limit(self):
        result, _ = public.fetch_hiring(FakeHTTP(job(), public.Page("", "rate_limited")), COMPANY, "101")
        self.assertEqual(result["status"], "hiring")
        self.assertEqual(result["stop_reason"], "rate_limited")

    def test_without_numeric_id_uses_public_company_jobs_page(self):
        client = FakeHTTP(job())
        result, _ = public.fetch_hiring(client, COMPANY, None)
        self.assertEqual(client.calls[0][0], COMPANY + "jobs/")
        self.assertEqual(result["stop_reason"], "company_page_only")

    def test_complete_company_fetch(self):
        client = FakeHTTP(company_page(), job(), job_detail())
        result = public.fetch_company({"slug": "example-law", "linkedin_company": COMPANY}, client, max_pages=1)
        self.assertEqual(result["source"], "linkedin_public")
        self.assertEqual(result["people"]["observed_count"], 1)
        self.assertEqual(result["hiring"]["observed_job_count"], 1)
        self.assertFalse(result["people"]["complete"])

    def test_discovery_and_jobs_only(self):
        client = FakeHTTP(f'<a href="{COMPANY}">LinkedIn</a>', company_page(), job(), job_detail())
        result = public.fetch_company({"website": "https://example.test"}, client, jobs_only=True, max_pages=1)
        self.assertEqual(result["contacts"], [])
        self.assertEqual(result["people"]["status"], "skipped")
        self.assertEqual(client.calls[0][1], {"website": True})
        self.assertEqual(result["linkedin_company"], COMPANY)

    def test_company_mismatch_does_not_fetch_jobs(self):
        client = FakeHTTP(company_page(company=public.BASE + "/company/other/"))
        result = public.fetch_company({"linkedin_company": COMPANY}, client)
        self.assertEqual(result["status"], "company_mismatch")
        self.assertEqual(result["hiring"]["status"], "unknown")
        self.assertEqual(len(client.calls), 1)


class TransportTests(unittest.TestCase):
    def test_private_dns_rejected(self):
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                public.public_host(COMPANY)

    def test_authenticated_endpoints_not_allowed(self):
        for path in ("/voyager/api/search", "/rest/people", "/in/example-person/", "/checkpoint/challenge"):
            self.assertFalse(public.linkedin_request_allowed(public.BASE + path))
        self.assertTrue(public.linkedin_request_allowed(public.GUEST_JOBS + "?f_C=101&start=0"))

    def test_429_stops_batch_no_retries(self):
        client = public.PublicHTTP()
        headers = Message()
        headers["Retry-After"] = "90"
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError(COMPANY, 429, "limited", headers, None)
        with patch.object(public, "public_host"), patch.object(public.time, "sleep"):
            first = client.get(COMPANY)
            second = client.get(COMPANY + "jobs/")
        self.assertEqual(first.status, "rate_limited")
        self.assertEqual(second.status, "rate_limited")
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(first.reason, "retry_after=90")
        request = client.opener.open.call_args.args[0]
        self.assertNotIn("Cookie", request.headers)
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.headers["User-agent"], public.UA)

    def test_redirect_to_login_not_followed(self):
        client = public.PublicHTTP()
        headers = Message()
        headers["Location"] = public.BASE + "/signup"
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError(COMPANY, 302, "redirect", headers, None)
        with patch.object(public, "public_host"), patch.object(public.time, "sleep"):
            self.assertEqual(client.get(COMPANY).status, "blocked")
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertEqual(client.stopped, "blocked")

    def test_malformed_url_does_not_crash(self):
        self.assertEqual(public.PublicHTTP().get("https://[invalid").status, "unsafe_url")


class VaultTests(unittest.TestCase):
    def setUp(self):
        indeed_patch = patch.object(contacts, "IndeedHTTP", side_effect=lambda **_: IndeedFake(company_page(company=INDEED_COMPANY), indeed_listing(), indeed_detail()))
        indeed_patch.start()
        self.addCleanup(indeed_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        (self.vault / "Businesses").mkdir()
        self.note = self.vault / "Businesses" / "example-law.md"
        self.note.write_text(f'---\nname: "Example Law"\ncategory: "[[Law]]"\npractice: personal-injury\nwebsite: "https://example.test"\nlinkedin_company: "{COMPANY}"\nindeed_company: "{INDEED_COMPANY}"\nowner: ""\n---\n\n## Contacts\n- Manual Contact — manual@example.test\n\n## Call log\nKeep this unchanged.\n')
        self.profile = self.vault / "Research" / "firms" / "example-law.json"
        self.profile.parent.mkdir(parents=True)
        self.result = fetch_verified(contacts.note_row(self.note, self.note.read_text()), "linkedin", FakeHTTP(company_page(), job(), job_detail()), public.fetch_company, max_pages=1)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def import_row(self, row=None):
        source = self.vault / "input.jsonl"
        source.write_text(json.dumps(row or self.result) + "\n")
        return contacts.apply_results(self.vault, source)

    def test_applies_people_hiring_without_replacing_website(self):
        stats = self.import_row()
        self.assertEqual(stats["errors"], 0)
        text = self.note.read_text()
        self.assertIn('website: "https://example.test"', text)
        self.assertIn("Manual Contact", text)
        self.assertIn("Keep this unchanged.", text)
        self.assertIn("Example Person", text)
        self.assertIn("PI Paralegal", text)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["hiring"]["status"], "hiring")
        self.assertEqual(profile["best_poc"]["name"], "Example Person")
        self.assertIn("review case records.", profile["hiring"]["jobs"][0]["description"])
        self.assertEqual(profile["hiring"]["jobs"][0]["job_url"], public.BASE + "/jobs/view/501/")

    def test_repeat_import_is_idempotent(self):
        self.import_row()
        before = (self.note.read_bytes(), self.profile.read_bytes())
        result = self.import_row()
        self.assertEqual(result["changed"], 0)
        self.assertEqual(before, (self.note.read_bytes(), self.profile.read_bytes()))

    def test_failed_lookup_preserves_people_but_not_fresh_hiring_yes(self):
        self.import_row()
        profile = json.loads(self.profile.read_text())
        profile["contacts"][0]["email"] = "person@example.test"
        self.profile.write_text(json.dumps(profile))
        row = public.fetch_company({"slug": self.note.stem, "linkedin_company": COMPANY}, FakeHTTP(public.Page("", "blocked")))
        self.import_row(row)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["contacts"], [])
        self.assertEqual(profile["company_identity"]["quarantine"]["previous_linkedin_contacts"][0]["email"], "person@example.test")
        self.assertEqual(profile["hiring"]["status"], "unknown")
        self.assertEqual(profile["linkedin_last_successful_hiring"]["status"], "hiring")
        self.assertIn("Example Person", self.note.read_text())

    def test_blank_public_email_does_not_erase_existing_email(self):
        self.import_row()
        profile = json.loads(self.profile.read_text())
        profile["contacts"][0]["email"] = "person@example.test"
        self.profile.write_text(json.dumps(profile))
        self.import_row()
        self.assertEqual(json.loads(self.profile.read_text())["contacts"][0]["email"], "person@example.test")

    def test_malformed_profile_does_not_overwrite_note(self):
        self.profile.write_text("{bad")
        before = self.note.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            stats = self.import_row()
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(before, self.note.read_bytes())
        self.assertEqual(self.profile.read_text(), "{bad")

    def test_empty_company_field_does_not_consume_next_field(self):
        text = self.note.read_text().replace(f'linkedin_company: "{COMPANY}"', 'linkedin_company:')
        self.assertEqual(contacts.note_row(self.note, text)["linkedin_company"], "")

    def test_owner_text_cannot_be_regex_replacement(self):
        value = r'Jordan \1 "Example" Doe'
        text = contacts.set_note_field(self.note.read_text(), "owner", value)
        self.assertIn('owner: ' + json.dumps(value), text)
        self.assertIn('website: "https://example.test"', text)

    def test_path_traversal_and_slug_mismatch_rejected(self):
        for row in ({"path": "/etc/passwd", "slug": "example-law"}, {"slug": "../example-law"}, {"path": str(self.note), "slug": "other"}):
            self.assertIsNone(contacts.resolve_note(self.vault, row))

    def test_dry_run_has_no_network_or_writes(self):
        before = {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        with patch.object(contacts, "PublicHTTP", side_effect=AssertionError("network forbidden")):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--dry-run"]), 0)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()})

    def test_default_refreshes_hiring_even_with_existing_contacts(self):
        fake = FakeHTTP(company_page(), job(), "", job_detail())
        with patch.object(contacts, "PublicHTTP", return_value=fake):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--no-push"]), 0)
        self.assertEqual(json.loads(self.profile.read_text())["hiring"]["status"], "hiring")

    def test_queue_only_never_fetches(self):
        with patch.object(contacts, "PublicHTTP", side_effect=AssertionError("no network")):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--queue-only", "--force"]), 0)
        self.assertTrue(list((self.vault / "Sources" / "runs").glob("linkedin-queue-*.jsonl")))
        self.assertFalse(self.profile.exists())

    def test_malformed_jsonl_returns_nonzero_and_continues(self):
        source = self.vault / "input.jsonl"
        source.write_text("{bad\n[]\n" + json.dumps(self.result) + "\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--apply", str(source), "--no-push"]), 2)
        self.assertTrue(self.profile.exists())

    def test_prep_scopes_to_its_queue_and_restores_poc_metadata(self):
        other = self.note.with_name("other.md")
        other.write_text(self.note.read_text().replace('[[Law]]', '[[HVAC]]'))
        calls = []
        def fake_run(cmd, *, dry_run, label):
            calls.append(cmd)
            if Path(cmd[1]).name == "linkedin_contacts.py":
                return contacts.run(cmd[2:]), "", ""
            if Path(cmd[1]).name == "build_poc.py":
                profile = json.loads(self.profile.read_text())
                profile["linkedin"] = {"company_url": COMPANY, "status": "linked"}
                self.profile.write_text(json.dumps(profile))
            return 0, "", ""
        def scripts(root, name):
            return TOOLS / "outreach-leads" / "scripts" / name if name in {"linkedin_contacts.py", "build_poc.py"} else None
        with patch.object(prep, "script_path", side_effect=scripts), patch.object(prep, "run_cmd", side_effect=fake_run), patch.object(contacts, "PublicHTTP", return_value=FakeHTTP(company_page(), job(), "", job_detail())):
            rc = prep.main(["--vault", str(self.vault), "--category", "Law", "--skip-specialty", "--skip-emails", "--no-push"])
        self.assertEqual(rc, 0)
        self.assertFalse(self.profile.with_name("other.json").exists())
        li_command = next(c for c in calls if Path(c[1]).name == "linkedin_contacts.py")
        self.assertIn("--queue", li_command)
        self.assertIn("--no-push", li_command)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["linkedin"]["people"]["observed_count"], 1)
        self.assertIn("Full first paragraph.", profile["hiring_sources"]["indeed"]["jobs"][0]["description"])
        self.assertEqual(profile["hiring_sources"]["indeed"]["jobs"][0]["base_salary"]["currency"], "USD")
        self.assertEqual(profile["hiring"]["checked_sources"], ["linkedin", "indeed"])
        self.assertIn("review case records.", profile["hiring"]["jobs"][0]["description"])
        shortlist = next((self.vault / "Research" / "firms" / "shortlists").glob("law-*.json"))
        self.assertEqual(json.loads(shortlist.read_text())["firms"][0]["hiring"]["status"], "hiring")

    def test_indeed_only_leaves_linkedin_untouched(self):
        self.import_row()
        prior = json.loads(self.profile.read_text())["linkedin"]
        with patch.object(contacts, "PublicHTTP", side_effect=AssertionError("LinkedIn forbidden")):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--indeed-only", "--no-push"]), 0)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["linkedin"], prior)
        self.assertEqual(profile["hiring"]["checked_sources"], ["indeed"])
        self.assertIn("Full first paragraph.", profile["hiring"]["jobs"][0]["description"])
        self.assertIn("## Indeed public check", self.note.read_text())

    def test_linkedin_refusal_does_not_prevent_indeed(self):
        with patch.object(contacts, "PublicHTTP", return_value=FakeHTTP(public.Page("", "blocked"))):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--no-push"]), 0)
        profile = json.loads(self.profile.read_text())
        self.assertEqual(profile["hiring_sources"]["linkedin"]["status"], "unknown")
        self.assertTrue(profile["hiring_sources"]["indeed"]["is_hiring"])
        self.assertTrue(profile["hiring"]["is_hiring"])

    def test_skip_indeed_never_calls_indeed(self):
        fake = FakeHTTP(company_page(), job(), "", job_detail())
        with patch.object(contacts, "PublicHTTP", return_value=fake), patch.object(contacts, "IndeedHTTP", side_effect=AssertionError("Indeed forbidden")):
            self.assertEqual(contacts.run(["--vault", str(self.vault), "--skip-indeed", "--no-push"]), 0)
        self.assertEqual(json.loads(self.profile.read_text())["hiring"]["checked_sources"], ["linkedin"])

    def test_malformed_indeed_import_is_rejected(self):
        row = dict(self.result)
        row["indeed"] = {"hiring": {"jobs": [{"job_id": "bad", "url": "https://evil.test"}]}}
        before = self.note.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertGreater(self.import_row(row)["errors"], 0)
        self.assertEqual(self.note.read_bytes(), before)

    def test_prep_dry_run_does_not_fetch_or_write(self):
        before = {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}
        with patch.object(prep, "script_path", return_value=None), patch.object(contacts, "PublicHTTP", side_effect=AssertionError("no network")):
            self.assertEqual(prep.main(["--vault", str(self.vault), "--category", "Law", "--dry-run"]), 0)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.vault.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()

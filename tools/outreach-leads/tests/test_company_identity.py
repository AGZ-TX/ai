"""Adversarial identity tests. Synthetic public HTML only; never live HTTP."""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import company_identity as identity
from linkedin_public import Page

LI = "https://www.linkedin.com/company/acme/"
ID = "https://www.indeed.com/cmp/acme"
ROW = {"slug": "acme", "firm": "Acme", "website": "https://acme.example", "linkedin_company": LI, "indeed_company": ID}


def profile(company=LI, name="Acme", website="https://acme.example", extra=""):
    return (f'<link rel="canonical" href="{company}"><h1>{name}</h1>'
            + (f'<dl><dt>Website</dt><dd><a href="{website}">{website}</a></dd></dl>' if website else "") + extra)


def official(company=LI):
    return f'<footer><a href="{company}">Company profile</a></footer>'


class HTTP:
    def __init__(self, mapping):
        self.mapping, self.calls, self.stopped = mapping, [], ""

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        page = self.mapping.get(url, Page(url, "not_found", http_status=404))
        return page if isinstance(page, Page) else Page(url, "ok", page, 200)


def verified_record(source="linkedin", row=None):
    row = row or ROW
    ref = row[source + "_company"]
    return identity.evaluate(identity.target_for(row), identity.profile_facts(profile(ref), ref, source), source)


def result(source="linkedin", row=None):
    row = row or ROW
    ref = row[source + "_company"]
    ident = verified_record(source, row)
    return {"source": source + "_public", "slug": row["slug"], "checked_at": "2026-09-18T00:00:00Z", "identity": ident,
            "linkedin_company": ref, "company_url": ref, "contacts": [{"name": "Jane Smith", "profile_url": "https://www.linkedin.com/in/jane/", "source": "linkedin"}],
            "hiring": {"status": "hiring", "is_hiring": True, "jobs": [{"job_id": "1", "url": "job", "company_url": ref, "description": "Full description."}], "observed_job_count": 1}}


class IdentityParsingTests(unittest.TestCase):
    def test_name_only_never_verifies(self):
        facts = identity.profile_facts(profile(website=""), LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["reason"], "name_only_not_identity")

    def test_wrong_domain_same_name_conflict(self):
        facts = identity.profile_facts(profile(website="https://other.example"), LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["status"], "conflict")

    def test_wrong_domain_beats_official_outbound_link(self):
        facts = identity.profile_facts(profile(website="https://other.example"), LI, "linkedin")
        proof = {"kind": "official_link", "source_url": ROW["website"], "target_url": LI}
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin", proof)["status"], "conflict")

    def test_website_and_name_verify_both_boards(self):
        for source, ref in (("linkedin", LI), ("indeed", ID)):
            with self.subTest(source=source):
                rec = identity.evaluate(identity.target_for(ROW), identity.profile_facts(profile(ref), ref, source), source)
                self.assertEqual(rec["status"], "verified")
                self.assertTrue(identity.trusted(rec, ROW, source))

    def test_www_and_http_are_domain_equivalent(self):
        facts = identity.profile_facts(profile(website="http://www.acme.example/about"), LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["status"], "verified")

    def test_subdomain_and_suffix_not_guessed(self):
        for url in ("https://acme.example.evil.test", "https://jobs.acme.example", "https://other.example.co.uk"):
            facts = identity.profile_facts(profile(website=url), LI, "linkedin")
            self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["status"], "conflict")

    def test_shared_domains_never_verify(self):
        for value in ("https://linkedin.com/company/acme", "https://acme.github.io", "https://acme.wixsite.com", "https://facebook.com/acme", "https://127.0.0.1"):
            self.assertEqual(identity.official_host(value), "")

    def test_domain_with_credentials_rejected(self):
        self.assertEqual(identity.official_host("https://user@acme.example"), "")
        self.assertEqual(identity.official_host("https://acme.example:9999"), "")

    def test_different_legal_entity_on_same_domain_is_unverified(self):
        facts = identity.profile_facts(profile(name="Acme Subsidiary"), LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["status"], "unverified")

    def test_explicit_alias_on_same_domain_is_accepted(self):
        row = {**ROW, "company_aliases": ["Acme Subsidiary"]}
        facts = identity.profile_facts(profile(name="Acme Subsidiary"), LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(row), facts, "linkedin")["status"], "verified")

    def test_name_and_city_without_domain_is_not_enough(self):
        row = {**ROW, "city": "El Paso"}
        facts = identity.profile_facts(profile(website="", extra="<dl><dt>Headquarters</dt><dd>El Paso</dd></dl>"), LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(row), facts, "linkedin")["status"], "unverified")

    def test_headquarters_mismatch_does_not_confuse_remote_office(self):
        facts = identity.profile_facts(profile(extra="<dl><dt>Headquarters</dt><dd>New York</dd></dl>"), LI, "linkedin")
        rec = identity.evaluate(identity.target_for({**ROW, "city": "El Paso"}), facts, "linkedin")
        self.assertEqual(rec["status"], "verified")
        self.assertEqual(rec["locations"], ["New York"])

    def test_scoped_schema_records_company_identity(self):
        obj = {"@type": "Organization", "url": LI, "name": "Acme", "sameAs": ROW["website"], "legalName": "Acme LLC"}
        html = f'<link rel="canonical" href="{LI}"><script type="application/ld+json">{json.dumps(obj)}</script>'
        facts = identity.profile_facts(html, LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["status"], "verified")

    def test_unrelated_schema_org_does_not_verify(self):
        obj = {"@type": "Organization", "url": "https://www.linkedin.com/company/other/", "name": "Acme", "sameAs": ROW["website"]}
        html = profile(website="", extra=f'<script type="application/ld+json">{json.dumps(obj)}</script>')
        self.assertEqual(identity.profile_facts(html, LI, "linkedin")["websites"], [])

    def test_website_in_job_description_not_identity(self):
        html = profile(website="", extra=f'<div id="jobDescriptionText"><a href="{ROW["website"]}">Website</a></div>')
        self.assertEqual(identity.profile_facts(html, LI, "linkedin")["websites"], [])

    def test_website_of_recommended_company_ignored(self):
        html = profile(website="", extra=f'<aside><a data-test-id="about-us__website" href="{ROW["website"]}">Website</a></aside>')
        self.assertEqual(identity.profile_facts(html, LI, "linkedin")["websites"], [])

    def test_linkedin_outbound_website_redirect_is_decoded_not_followed(self):
        html = profile(website="https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Facme.example")
        self.assertEqual(identity.profile_facts(html, LI, "linkedin")["websites"], [ROW["website"]])

    def test_profile_canonical_conflict(self):
        html = profile("https://www.linkedin.com/company/other/")
        facts = identity.profile_facts(html, LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["status"], "conflict")

    def test_numeric_linkedin_id_can_resolve_to_slug(self):
        requested = "https://www.linkedin.com/company/101/"
        html = profile(extra='<a href="/jobs/search?f_C=101">See jobs</a>')
        facts = identity.profile_facts(html, requested, "linkedin")
        self.assertEqual(facts["company_id"], "101")
        self.assertEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin")["status"], "verified")

    def test_loading_shell_not_verified_by_outbound_link(self):
        facts = identity.profile_facts(f'<link rel="canonical" href="{LI}">Loading', LI, "linkedin")
        proof = {"kind": "official_link", "source_url": ROW["website"], "target_url": LI}
        self.assertNotEqual(identity.evaluate(identity.target_for(ROW), facts, "linkedin", proof)["status"], "verified")

    def test_unrelated_business_words_not_stripped(self):
        self.assertNotEqual(identity.normalize_name("Acme Law"), identity.normalize_name("Acme Construction"))

    def test_unicode_legal_name_supported(self):
        row = {**ROW, "firm": "Eléctrica & Hijos"}
        facts = identity.profile_facts(profile(name="Eléctrica and Hijos"), LI, "linkedin")
        self.assertEqual(identity.evaluate(identity.target_for(row), facts, "linkedin")["status"], "verified")

    def test_regional_indeed_profiles_not_conflated(self):
        self.assertNotEqual(identity.company_ref("https://ca.indeed.com/cmp/Acme", "indeed"), ID)

    def test_deceptive_company_hosts_rejected(self):
        for value in ("https://www.linkedin.com.evil.test/company/acme", "https://www.indeed.com.evil.test/cmp/acme", "https://user@www.indeed.com/cmp/acme"):
            self.assertFalse(any(identity.company_ref(value, b) for b in identity.BOARDS))


class DiscoveryTests(unittest.TestCase):
    def test_stored_url_not_trusted_without_evidence(self):
        http = HTTP({LI: profile(website="")})
        rec, _ = identity.resolve_company(ROW, "linkedin", http)
        self.assertNotEqual(rec["status"], "verified")

    def test_resolved_profile_is_cached_for_enrichment(self):
        http = HTTP({LI: profile()})
        rec, cache = identity.resolve_company(ROW, "linkedin", http)
        self.assertEqual(rec["status"], "verified")
        cache.get(LI)
        self.assertEqual(len(http.calls), 1)

    def test_official_link_supports_trade_name(self):
        http = HTTP({LI: profile(name="Acme Trade", website=""), ROW["website"]: official()})
        rec, _ = identity.resolve_company(ROW, "linkedin", http)
        self.assertEqual(rec["status"], "verified")
        self.assertEqual(rec["reason"], "official_site_identity_link")

    def test_corrects_bad_saved_profile_from_official_site(self):
        wrong = "https://www.linkedin.com/company/wrong/"
        row = {**ROW, "linkedin_company": wrong}
        http = HTTP({wrong: profile(wrong, website="https://other.example"), ROW["website"]: official(), LI: profile()})
        rec, _ = identity.resolve_company(row, "linkedin", http)
        self.assertEqual(rec["company_url"], LI)
        self.assertEqual(rec["status"], "verified")
        self.assertEqual(rec["candidates"][0]["status"], "conflict")

    def test_follows_exposed_careers_link_not_guessed_paths(self):
        row = {**ROW, "linkedin_company": ""}
        http = HTTP({ROW["website"]: '<a href="/careers">Careers</a>', ROW["website"] + '/careers': official(), LI: profile(website="")})
        rec, _ = identity.resolve_company(row, "linkedin", http)
        self.assertEqual(rec["status"], "verified")
        self.assertEqual([u for u, _ in http.calls], [ROW["website"], ROW["website"] + '/careers', LI])

    def test_company_name_search_only_discovers_candidates(self):
        row = {**ROW, "indeed_company": ""}
        from urllib.parse import urlencode
        search = "https://www.indeed.com/jobs?" + urlencode({"q": 'company:"Acme"', "l": ""})
        http = HTTP({search: f'<h4><a href="{ID}">Acme</a></h4>', ID: profile(ID)})
        rec, _ = identity.resolve_company(row, "indeed", http)
        self.assertEqual(rec["status"], "verified")
        self.assertIn(ID, [u for u, _ in http.calls])

    def test_name_search_with_no_website_stays_unverified(self):
        row = {**ROW, "indeed_company": ""}
        from urllib.parse import urlencode
        search = "https://www.indeed.com/jobs?" + urlencode({"q": 'company:"Acme"', "l": ""})
        http = HTTP({search: f'<a href="{ID}">Acme</a>', ID: profile(ID, website="")})
        rec, _ = identity.resolve_company(row, "indeed", http)
        self.assertNotEqual(rec["status"], "verified")

    def test_multiple_verified_profiles_are_ambiguous(self):
        second = "https://www.linkedin.com/company/acme-2/"
        row = {**ROW, "linkedin_company": ""}
        http = HTTP({ROW["website"]: official() + official(second), LI: profile(), second: profile(second)})
        rec, _ = identity.resolve_company(row, "linkedin", http)
        self.assertEqual(rec["status"], "ambiguous")

    def test_partner_links_do_not_become_our_company(self):
        html = f'<section class="partners"><a href="{LI}">LinkedIn</a></section>'
        links, _ = identity.official_candidates(html, ROW["website"], "linkedin", identity.target_for(ROW))
        self.assertEqual(links, {})

    def test_official_schema_same_as_accepted(self):
        obj = {"@type": "Organization", "url": ROW["website"], "name": "Acme", "sameAs": [LI, ID]}
        html = '<script type="application/ld+json">' + json.dumps(obj) + '</script>'
        links, _ = identity.official_candidates(html, ROW["website"], "linkedin", identity.target_for(ROW))
        self.assertEqual(links[LI]["kind"], "official_same_as")

    def test_nested_review_author_does_not_supply_identity(self):
        obj = {"@type": "Review", "author": {"@type": "Organization", "name": "Acme", "url": ROW["website"], "sameAs": LI}}
        html = '<script type="application/ld+json">' + json.dumps(obj) + '</script>'
        self.assertEqual(identity.official_candidates(html, ROW["website"], "linkedin", identity.target_for(ROW))[0], {})

    def test_block_stops_no_search_or_alternate_endpoint(self):
        for status in ("blocked", "rate_limited"):
            http = HTTP({LI: Page(LI, status)})
            rec, _ = identity.resolve_company(ROW, "linkedin", http)
            self.assertEqual(rec["status"], status)
            self.assertEqual(len(http.calls), 1)

    def test_html_challenge_stops(self):
        http = HTTP({LI: '<title>Just a moment...</title>'})
        rec, _ = identity.resolve_company(ROW, "linkedin", http)
        self.assertEqual(rec["status"], "blocked")
        self.assertEqual(len(http.calls), 1)

    def test_missing_official_domain_performs_no_network(self):
        http = HTTP({})
        rec, _ = identity.resolve_company({**ROW, "website": ""}, "linkedin", http)
        self.assertEqual(rec["reason"], "missing_official_domain")
        self.assertEqual(http.calls, [])

    def test_unverified_company_never_fetches_people_or_job_details(self):
        fetcher = Mock(side_effect=AssertionError("wrong company should not be enriched"))
        data = identity.fetch_verified(ROW, "linkedin", HTTP({LI: profile(website="https://other.example")}), fetcher)
        self.assertIsNone(data["hiring"]["is_hiring"])
        self.assertEqual(data["contacts"], [])
        fetcher.assert_not_called()

    def test_fetcher_receives_resolved_url_and_platform_name(self):
        fetcher = Mock(return_value={"hiring": {"jobs": []}})
        data = identity.fetch_verified(ROW, "linkedin", HTTP({LI: profile()}), fetcher, max_jobs=12)
        self.assertEqual(fetcher.call_args.args[0]["linkedin_company"], LI)
        self.assertEqual(fetcher.call_args.kwargs["max_jobs"], 12)
        self.assertEqual(data["identity"]["status"], "verified")


class PersistenceTests(unittest.TestCase):
    def test_guard_accepts_bound_evidence(self):
        self.assertEqual(len(identity.guard_row(result(), ROW)["hiring"]["jobs"]), 1)

    def test_guard_rejects_name_only_legacy_import(self):
        data = result()
        data.pop("identity")
        guarded = identity.guard_row(data, ROW)
        self.assertEqual(guarded["contacts"], [])
        self.assertEqual(guarded["hiring"]["jobs"], [])
        self.assertEqual(len(guarded["identity_quarantine"]["hiring"]["jobs"]), 1)

    def test_guard_rejects_result_bound_to_another_domain(self):
        guarded = identity.guard_row(result(), {**ROW, "website": "https://other.example"})
        self.assertEqual(guarded["hiring"]["status"], "unknown")
        self.assertEqual(guarded["contacts"], [])

    def test_guard_rejects_result_bound_to_another_firm(self):
        self.assertEqual(identity.guard_row(result(), {**ROW, "firm": "Other Firm"})["contacts"], [])

    def test_status_verified_without_proof_is_not_sufficient(self):
        data = result()
        data["identity"]["evidence"] = []
        self.assertEqual(identity.guard_row(data, ROW)["contacts"], [])

    def test_wrong_employer_job_is_quarantined(self):
        data = result()
        data["hiring"]["jobs"][0]["company_url"] = "https://www.linkedin.com/company/other/"
        guarded = identity.guard_row(data, ROW)
        self.assertEqual(guarded["hiring"]["jobs"], [])
        self.assertIsNone(guarded["hiring"]["is_hiring"])

    def test_company_changed_after_verification_rejected(self):
        data = result()
        data["linkedin_company"] = "https://www.linkedin.com/company/other/"
        self.assertEqual(identity.guard_row(data, ROW)["contacts"], [])

    def test_old_report_replay_is_idempotent_and_does_not_mutate_input(self):
        data = result()
        data.pop("identity")
        before = deepcopy(data)
        a = identity.guard_row(data, ROW)
        self.assertEqual(data, before)
        self.assertEqual(identity.guard_row(a, ROW), a)

    def test_verified_import_is_idempotent(self):
        a = identity.guard_row(result(), ROW)
        self.assertEqual(identity.guard_row(a, ROW), a)

    def test_boards_are_independent(self):
        data = result()
        data["indeed"] = result("indeed")
        data["indeed"]["identity"]["evidence"] = []
        guarded = identity.guard_row(data, ROW)
        self.assertEqual(len(guarded["hiring"]["jobs"]), 1)
        self.assertEqual(guarded["indeed"]["hiring"]["jobs"], [])

    def test_identity_snapshot_and_target_persist(self):
        profile_data = {}
        data = identity.guard_row(result(), ROW)
        identity.merge_identity(profile_data, data, ROW)
        self.assertEqual(profile_data["company_identity"]["sources"]["linkedin"]["status"], "verified")
        self.assertEqual(profile_data["company_identity"]["target"], identity.target_for(ROW))

    def test_unknown_refresh_archives_unverified_automatic_contacts(self):
        data = identity.guard_row(result(), ROW)
        profile_data = {"contacts": data["contacts"] + [{"name": "Manual", "source": "website"}], "best_poc": data["contacts"][0]}
        identity.merge_identity(profile_data, {"source": "linkedin_public", "identity": {}, "linkedin_skipped": False}, ROW)
        self.assertEqual(profile_data["contacts"], [{"name": "Manual", "source": "website"}])
        self.assertIsNone(profile_data["best_poc"])
        self.assertIn("previous_linkedin_contacts", profile_data["company_identity"]["quarantine"])

    def test_reverified_same_company_preserves_known_contacts(self):
        data = identity.guard_row(result(), ROW)
        profile_data = {"company_identity": {"sources": {"linkedin": data["identity"]}}, "contacts": data["contacts"]}
        profile_data["contacts"][0]["email"] = "jane@acme.example"
        identity.merge_identity(profile_data, data, ROW)
        self.assertEqual(profile_data["contacts"][0]["email"], "jane@acme.example")

    def test_indeed_only_does_not_retire_linkedin_contacts(self):
        data = {"source": "linkedin_public", "linkedin_skipped": True, "indeed": result("indeed")}
        profile_data = {"contacts": result()["contacts"]}
        before = deepcopy(profile_data["contacts"])
        identity.merge_identity(profile_data, data, ROW)
        self.assertEqual(profile_data["contacts"], before)

    def test_scope_fingerprint_changes_with_explicit_aliases(self):
        self.assertNotEqual(identity.target_for(ROW), identity.target_for({**ROW, "company_aliases": ["Acme LLC"]}))


if __name__ == "__main__":
    unittest.main()

"""Synthetic public HTML and mocked HTTP only; no live data or credentials."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import linkedin_public as public
import linkedin_job_details as details

COMPANY = public.BASE + "/company/example-law/"
URL = public.BASE + "/jobs/view/501/"
JOB = {"job_id": "501", "url": URL, "title": "Paralegal", "company": "Example Law", "company_url": COMPANY, "location": "El Paso, TX"}


def detail_page(description=None, *, jid="501", company=COMPANY, schema=None, closed=False):
    description = description if description is not None else '<h2>Responsibilities</h2><p>Support <strong>clients</strong> &amp; counsel.</p><ul><li>Review records.</li><li>Prepare filings.</li></ul><p>Final paragraph: benefits and equal opportunity.</p>'
    structured = '<script type="application/ld+json">' + json.dumps(schema) + '</script>' if schema is not None else ''
    return f'''<html><head><title>Paralegal at Example Law | LinkedIn</title>
    <link rel="canonical" href="https://uk.linkedin.com/jobs/view/paralegal-{jid}?trackingId=test">{structured}</head><body>
    <section class="top-card-layout" data-entity-urn="urn:li:jobPosting:{jid}"><h1 class="top-card-layout__title">Paralegal</h1>
    <a class="topcard__org-name-link" href="{company}">Example Law</a><span class="topcard__flavor--bullet">El Paso, TX</span>
    <span class="posted-time-ago__text">2 days ago</span><span class="num-applicants__caption">Over 200 applicants</span>
    {'<span>No longer accepting applications</span>' if closed else ''}
    <a class="apply-button apply-button--offsite" href="https://careers.example.test/apply/501?source=linkedin">Apply</a></section>
    <section class="description"><div class="show-more-less-html__markup">{description}</div>
    <button>Show more</button><button>Show less</button>
    <ul><li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Seniority level</h3><span class="description__job-criteria-text">Associate</span></li>
    <li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Employment type</h3><span class="description__job-criteria-text">Full-time</span></li>
    <li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Job function</h3><span class="description__job-criteria-text">Legal</span></li>
    <li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Industries</h3><span class="description__job-criteria-text">Law Practice</span></li></ul></section>
    <section class="compensation"><div class="salary">$55,000 - $75,000 per year</div></section>
    <section class="message-the-recruiter"><a href="/in/example-recruiter/"><h3 class="base-main-card__title">Example Recruiter</h3></a><h4 class="base-main-card__subtitle">Talent Partner</h4></section>
    <section class="similar-jobs"><h2>Similar jobs</h2><div class="show-more-less-html__markup">UNRELATED JOB DESCRIPTION</div></section>
    <a href="/login">Sign in</a></body></html>'''


def posting(**extra):
    return {"@type": "JobPosting", "url": URL, "title": "Paralegal", "hiringOrganization": {"name": "Example Law", "sameAs": COMPANY}, **extra}


def card(jid="501"):
    return f'<li><div class="base-search-card"><a href="/jobs/view/{jid}/">View</a><h3>Paralegal</h3><a href="{COMPANY}">Example Law</a></div></li>'


class FakeHTTP:
    def __init__(self, *pages):
        self.pages, self.calls, self.stopped = list(pages), [], ""

    def get(self, url, **kwargs):
        self.calls.append(url)
        if not self.pages:
            raise AssertionError("unexpected HTTP call: " + url)
        page = self.pages.pop(0)
        return page if isinstance(page, public.Page) else public.Page(url, "ok", page, 200)


class ParseDetailTests(unittest.TestCase):
    def test_full_description_preserves_end_and_lists(self):
        row = details.parse_job_detail(detail_page(), JOB)
        self.assertEqual(row["detail_status"], "ok")
        self.assertIn("Support clients & counsel.", row["description"])
        self.assertRegex(row["description"], r"- Review records\.\n+- Prepare filings\.")
        self.assertTrue(row["description"].endswith("Final paragraph: benefits and equal opportunity."))
        self.assertIn("<strong>clients</strong>", row["description_html"])
        self.assertNotIn("UNRELATED", row["description"])
        self.assertNotIn("Show more", row["description"])
        self.assertEqual(row["job_url"], URL)

    def test_long_description_never_snippet_truncated(self):
        text = "Complete responsibility. " * 5000 + "TAIL_MARKER"
        row = details.parse_job_detail(detail_page('<p>' + text + '</p>'), JOB)
        self.assertEqual(row["description"], text)
        self.assertFalse(row["description_truncated"])

    def test_public_metadata(self):
        row = details.parse_job_detail(detail_page(), JOB)
        self.assertEqual(row["seniority_level"], "Associate")
        self.assertEqual(row["employment_type"], "Full-time")
        self.assertEqual(row["job_function"], "Legal")
        self.assertEqual(row["industries"], "Law Practice")
        self.assertEqual(row["salary_text"], "$55,000 - $75,000 per year")
        self.assertEqual(row["applicant_count_text"], "Over 200 applicants")
        self.assertNotIn("applicant_count", row)  # Never an invented exact count.
        self.assertEqual(row["apply_url"], "https://careers.example.test/apply/501?source=linkedin")
        self.assertEqual(row["job_poster"]["profile_url"], public.BASE + "/in/example-recruiter/")

    def test_json_ld_fallback_graph_and_structured_fields(self):
        data = posting(description="<p>All requirements</p><ul><li>Python</li></ul>", datePosted="2026-09-17", validThrough="2026-10-17", employmentType="FULL_TIME", jobLocationType="TELECOMMUTE", baseSalary={"currency": "USD", "value": {"minValue": 50000, "maxValue": 70000, "unitText": "YEAR"}}, skills=["Python", "Writing"], jobBenefits="PTO", directApply=False)
        html = '<script type="application/ld+json">' + json.dumps({"@graph": [{"@type": "Organization"}, data]}) + '</script>'
        row = details.parse_job_detail(html, JOB)
        self.assertEqual(row["detail_status"], "ok")
        self.assertIn("- Python", row["description"])
        self.assertEqual(row["description_source"], "json_ld")
        self.assertEqual(row["valid_through"], "2026-10-17")
        self.assertEqual(row["base_salary"]["value"]["unitText"], "YEAR")
        self.assertEqual(row["skills"], ["Python", "Writing"])
        self.assertFalse(row["direct_apply"])
        self.assertEqual(row["job_location_type"], "TELECOMMUTE")

    def test_matching_schema_selected_not_recommendation(self):
        data = [posting(url=public.BASE + "/jobs/view/777/", description="WRONG"), posting(description="<p>Correct</p>")]
        row = details.parse_job_detail('<script type="application/ld+json">' + json.dumps(data) + '</script>', JOB)
        self.assertEqual(row["description"], "Correct")

    def test_ambiguous_schema_is_not_guessed(self):
        data = [posting(url=None, description="A"), posting(url=None, description="B")]
        self.assertEqual(details.parse_job_detail('<script type="application/ld+json">' + json.dumps(data) + '</script>', JOB)["detail_status"], "job_mismatch")

    def test_bad_json_does_not_break_html_description(self):
        html = detail_page().replace('</head>', '<script type="application/ld+json">{invalid</script></head>')
        self.assertEqual(details.parse_job_detail(html, JOB)["detail_status"], "ok")

    def test_html_and_structured_employment_values_preserved(self):
        row = details.parse_job_detail(detail_page(schema=posting(employmentType="FULL_TIME")), JOB)
        self.assertEqual(row["employment_type"], "Full-time")
        self.assertEqual(row["structured_metadata"]["employment_type"], "FULL_TIME")

    def test_different_job_canonical_rejected(self):
        self.assertEqual(details.parse_job_detail(detail_page(jid="999"), JOB)["detail_status"], "job_mismatch")

    def test_conflicting_open_graph_identity_rejected(self):
        html = detail_page().replace('</head>', '<meta property="og:url" content="https://www.linkedin.com/jobs/view/999/"></head>')
        self.assertEqual(details.parse_job_detail(html, JOB)["detail_status"], "job_mismatch")

    def test_different_top_card_urn_rejected(self):
        html = detail_page().replace('urn:li:jobPosting:501', 'urn:li:jobPosting:999')
        self.assertEqual(details.parse_job_detail(html, JOB)["detail_status"], "job_mismatch")

    def test_employer_mismatch_rejected(self):
        self.assertEqual(details.parse_job_detail(detail_page(company=public.BASE + "/company/other/"), JOB)["detail_status"], "company_mismatch")

    def test_schema_employer_conflict_rejected(self):
        data = posting(hiringOrganization={"sameAs": public.BASE + "/company/other/"})
        self.assertEqual(details.parse_job_detail(detail_page(schema=data), JOB)["detail_status"], "company_mismatch")

    def test_numeric_employer_identity_supported(self):
        row = details.parse_job_detail(detail_page(company=public.BASE + "/company/101/"), JOB, "101")
        self.assertEqual(row["detail_status"], "ok")

    def test_scripts_handlers_and_unsafe_links_removed(self):
        html = '<p onclick="evil()">Safe <b>text</b></p><script>evil()</script><svg>secret</svg><iframe>secret</iframe><a href="javascript:evil()">Bad link</a><a href="https://example.test/info">Info</a><img src=x onerror="evil()">'
        row = details.parse_job_detail(detail_page(html), JOB)
        for bad in ("onclick", "onerror", "javascript:", "<script", "<svg", "<iframe", "secret", "evil()"):
            self.assertNotIn(bad, row["description_html"])
        self.assertIn('href="https://example.test/info"', row["description_html"])
        self.assertIn("Safe text", row["description"])

    def test_missing_description_not_replaced_by_meta_teaser(self):
        html = detail_page("").replace('</head>', '<meta name="description" content="TEASER"></head>')
        row = details.parse_job_detail(html, JOB)
        self.assertEqual(row["detail_status"], "partial")
        self.assertEqual(row["description"], "")

    def test_closed_listing_keeps_description(self):
        row = details.parse_job_detail(detail_page(closed=True), JOB)
        self.assertEqual(row["listing_status"], "closed")
        self.assertTrue(row["description"])

    def test_closure_words_in_description_not_status(self):
        row = details.parse_job_detail(detail_page("<p>Tell clients when a job has expired.</p>"), JOB)
        self.assertEqual(row["listing_status"], "observed")

    def test_login_wall_is_not_description(self):
        self.assertEqual(details.parse_job_detail('<title>Sign in | LinkedIn</title>', JOB)["detail_status"], "blocked")

    def test_offsite_apply_comment(self):
        target = "https://careers.example.test/apply/501?source=linkedin&ref=public"
        redirect = public.BASE + '/jobs/view/externalApply?jobId=501&url=' + quote(target, safe='')
        html = detail_page().replace('class="apply-button apply-button--offsite"', 'class="ignored"') + '<code id="applyUrl"><!--' + json.dumps(redirect) + '--></code>'
        self.assertEqual(details.parse_job_detail(html, JOB)["apply_url"], target)

    def test_unsafe_application_urls_rejected(self):
        for url in ("javascript:alert(1)", "http://127.0.0.1/apply", "http://localhost/apply", "http://company.internal/apply", "https://user:password@example.test/apply", "https://example.test:8080/apply", "https://www.linkedin.com/login", "https://www.linkedin.com/voyager/api/test"):
            with self.subTest(url=url):
                self.assertEqual(details.application_url(url, "501"), "")
        self.assertEqual(details.application_url(public.BASE + '/jobs/view/externalApply?jobId=999&url=https%3A%2F%2Fexample.test%2Fapply', '501'), '')

    def test_no_invented_optional_fields(self):
        html = '<script type="application/ld+json">' + json.dumps(posting(description='Text')) + '</script>'
        row = details.parse_job_detail(html, JOB)
        for key in ('apply_url', 'salary_text', 'base_salary', 'seniority_level', 'skills', 'job_poster'):
            self.assertNotIn(key, row)

    def test_empty_html_description_uses_full_json_ld(self):
        row = details.parse_job_detail(detail_page("", schema=posting(description="<p>Complete structured description.</p>")), JOB)
        self.assertEqual(row["description"], "Complete structured description.")
        self.assertEqual(row["description_source"], "json_ld")
        self.assertEqual(row["detail_status"], "ok")

    def test_response_size_bound(self):
        with self.assertRaises(ValueError):
            details.parse_job_detail('x' * (public.MAX_BYTES + 1), JOB)


class FetchDetailTests(unittest.TestCase):
    def test_fetches_full_public_page_only_once(self):
        client = FakeHTTP(detail_page())
        row = details.fetch_job_detail(JOB, client)
        self.assertEqual(client.calls, [URL])
        self.assertEqual(row["detail_status"], "ok")
        self.assertEqual(row["url"], URL)
        self.assertTrue(row["detail_checked_at"])
        self.assertEqual(row["detail_checks"][0]["http_status"], 200)

    def test_public_guest_fallback_for_missing_markup(self):
        client = FakeHTTP('<html><p>Unrecognized shell</p></html>', detail_page())
        row = details.fetch_job_detail(JOB, client)
        self.assertEqual(client.calls, [URL, details.GUEST_DETAIL + "501"])
        self.assertEqual(row["detail_status"], "ok")
        self.assertEqual(row["detail_source_url"], details.GUEST_DETAIL + '501')

    def test_block_timeout_404_do_not_try_other_endpoint(self):
        for status in ('blocked', 'rate_limited', 'not_found', 'network_error', 'too_large'):
            client = FakeHTTP(public.Page(URL, status))
            row = details.fetch_job_detail(JOB, client)
            self.assertEqual(row['detail_status'], status)
            self.assertIsNone(row['description'])
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(row['title'], JOB['title'])

    def test_page_identity_mismatch_no_fallback(self):
        client = FakeHTTP(public.Page(public.BASE + '/jobs/view/999/', 'ok', detail_page(), 200))
        self.assertEqual(details.fetch_job_detail(JOB, client)['detail_status'], 'job_mismatch')
        self.assertEqual(len(client.calls), 1)

    def test_wrong_company_does_not_become_hiring_details(self):
        client = FakeHTTP(detail_page(company=public.BASE + '/company/other/'))
        row = details.fetch_job_detail(JOB, client)
        self.assertEqual(row['detail_status'], 'company_mismatch')
        self.assertIsNone(row['description'])
        self.assertEqual(len(client.calls), 1)

    def test_partial_metadata_survives_failed_guest_fetch(self):
        client = FakeHTTP(detail_page(""), public.Page(details.GUEST_DETAIL + "501", "blocked"))
        row = details.fetch_job_detail(JOB, client)
        self.assertEqual(row["detail_status"], "blocked")
        self.assertEqual(row["seniority_level"], "Associate")
        self.assertFalse(row["description"])
        self.assertEqual(len(client.calls), 2)

    def test_one_unavailable_job_does_not_discard_other_details(self):
        client = FakeHTTP(public.Page(URL, "not_found", http_status=404), detail_page(jid="502"))
        hiring = {"jobs": [dict(JOB), {**JOB, "job_id": "502", "url": public.BASE + "/jobs/view/502/"}]}
        details.enrich_job_details(hiring, client)
        self.assertEqual(hiring["jobs"][0]["detail_status"], "not_found")
        self.assertEqual(hiring["jobs"][1]["detail_status"], "ok")
        self.assertEqual(hiring["job_details"]["descriptions_fetched"], 1)
        self.assertEqual(len(client.calls), 2)

    def test_invalid_id_never_fetches(self):
        client = FakeHTTP()
        row = details.fetch_job_detail({**JOB, 'job_id': '999'}, client)
        self.assertEqual(row['detail_status'], 'unsafe_url')
        self.assertEqual(client.calls, [])

    def test_job_details_stop_batch_on_rate_limit(self):
        client = FakeHTTP(public.Page(URL, 'rate_limited', http_status=429))
        hiring = {'jobs': [dict(JOB), {**JOB, 'job_id': '502', 'url': public.BASE + '/jobs/view/502/'}], 'status': 'hiring', 'is_hiring': True}
        details.enrich_job_details(hiring, client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(hiring['jobs'][1]['detail_status'], 'not_attempted')
        self.assertEqual(hiring['job_details']['stop_reason'], 'rate_limited')
        self.assertTrue(hiring['is_hiring'])  # Listing evidence survives a blocked detail fetch.

    def test_listing_rate_limit_prevents_detail_fetches(self):
        client = FakeHTTP()
        hiring = {'jobs': [dict(JOB)], 'stop_reason': 'rate_limited'}
        details.enrich_job_details(hiring, client)
        self.assertEqual(client.calls, [])
        self.assertEqual(hiring['jobs'][0]['detail_status'], 'not_attempted')

    def test_deduplicates_detail_requests(self):
        client = FakeHTTP(detail_page())
        hiring = {'jobs': [dict(JOB), dict(JOB)]}
        details.enrich_job_details(hiring, client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(hiring['jobs']), 2)
        self.assertEqual(hiring['job_details']['descriptions_fetched'], 2)

    def test_closed_details_cannot_assert_current_hiring(self):
        hiring = {'jobs': [dict(JOB)], 'status': 'hiring', 'is_hiring': True}
        details.enrich_job_details(hiring, FakeHTTP(detail_page(closed=True)))
        self.assertIsNone(hiring['is_hiring'])
        self.assertEqual(hiring['closed_job_count'], 1)
        self.assertTrue(hiring['jobs'][0]['description'])

    def test_complete_company_pipeline_includes_details(self):
        company_html = f'<link rel="canonical" href="{COMPANY}"><a href="/jobs/search/?f_C=101">See jobs</a>'
        client = FakeHTTP(company_html, card(), detail_page())
        row = public.fetch_company({'slug': 'example-law', 'linkedin_company': COMPANY}, client, max_pages=1)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(row['hiring']['jobs'][0]['detail_status'], 'ok')
        self.assertIn('Final paragraph', row['hiring']['jobs'][0]['description'])
        self.assertEqual(row['hiring']['job_details']['descriptions_fetched'], 1)
        self.assertEqual(len(row['checks']), 3)
        self.assertEqual(json.loads(json.dumps(row))['hiring']['jobs'][0]['description'], row['hiring']['jobs'][0]['description'])

    def test_job_cap_also_caps_detail_requests(self):
        company_html = f'<link rel="canonical" href="{COMPANY}"><a href="/jobs/search/?f_C=101">See jobs</a>'
        client = FakeHTTP(company_html, card() + card('502'), detail_page())
        row = public.fetch_company({'linkedin_company': COMPANY}, client, max_jobs=1)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(len(row['hiring']['jobs']), 1)

    def test_only_exact_public_detail_routes_allowed(self):
        for url in (URL, details.GUEST_DETAIL + '501', 'https://uk.linkedin.com/jobs/view/paralegal-501'):
            self.assertTrue(public.linkedin_request_allowed(url))
        for url in (details.GUEST_DETAIL + '501/other', details.GUEST_DETAIL + '../501', public.BASE + '/voyager/api/jobs/501', 'https://linkedin.com.evil.test/jobs/view/501', 'http://www.linkedin.com/jobs/view/501'):
            self.assertFalse(public.linkedin_request_allowed(url))

    def test_client_is_anonymous_and_application_is_never_fetched(self):
        client = public.PublicHTTP()
        response = Mock()
        response.status = 200
        response.headers.get.return_value = 'text/html'
        response.headers.get_content_charset.return_value = 'utf-8'
        response.read.return_value = detail_page().encode()
        client.opener = MagicMock()
        client.opener.open.return_value.__enter__.return_value = response
        with patch.object(public, 'public_host'), patch.object(public.time, 'sleep'):
            row = details.fetch_job_detail(JOB, client)
        self.assertEqual(client.opener.open.call_count, 1)
        request = client.opener.open.call_args.args[0]
        self.assertNotIn('Cookie', request.headers)
        self.assertNotIn('Authorization', request.headers)
        self.assertEqual(row['detail_status'], 'ok')


if __name__ == '__main__':
    unittest.main()

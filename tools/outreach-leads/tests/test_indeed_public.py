"""Offline, synthetic Indeed fixtures; these tests do not establish live access."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import indeed_public as indeed
from hiring_sources import merge_hiring

COMPANY = indeed.BASE + "/cmp/example-law"
JID = "0123456789abcdef"
JID2 = "fedcba9876543210"
URL = indeed.BASE + "/viewjob?jk=" + JID


def listing_card(jid=JID, company=COMPANY, name="Example Law", title="Paralegal"):
    employer = f'<span data-testid="company-name"><a href="{company}">{name}</a></span>' if name or company else ""
    return f'''<li data-jk="{jid}"><h3><a href="/viewjob?jk={jid}&from=cmp">{title}</a></h3>{employer}
    <span data-testid="text-location">El Paso, TX</span><div data-testid="salary-snippet">$25 - $30 an hour</div></li>'''


def listing_page(cards=None, next_page=False):
    return f'''<html><head><title>Example Law Jobs and Careers | Indeed</title><link rel="canonical" href="{COMPANY}/jobs"></head>
    <h1>Example Law Jobs and Careers</h1><ul>{listing_card() if cards is None else cards}</ul>
    {'<a rel="next" href="?clearPrefilter=1&start=20">Next</a>' if next_page else ''}<a href="https://secure.indeed.com">Sign in</a></html>'''


def posting(jid=JID, company=COMPANY, name="Example Law"):
    return {"@type": "JobPosting", "url": indeed.BASE + "/viewjob?jk=" + jid, "title": "Paralegal", "hiringOrganization": {"name": name, "sameAs": company},
            "description": "<p>Full first paragraph.</p><ul><li>Research cases.</li><li>Prepare documents.</li></ul>",
            "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"minValue": 25, "maxValue": 30, "unitText": "HOUR"}},
            "employmentType": "FULL_TIME", "jobBenefits": ["Health insurance", "Paid time off"], "skills": "Legal research",
            "qualifications": "Three years experience", "datePosted": "2026-09-18", "validThrough": "2099-12-31", "workHours": "40 hours"}


def detail_page(jid=JID, data=None, markup=None):
    data = posting(jid) if data is None else data
    return f'''<html><head><title>Paralegal - Example Law</title><link rel="canonical" href="/viewjob?jk={jid}"></head>
    <h1 class="jobsearch-JobInfoHeader-title">Paralegal</h1><div data-testid="inlineHeader-companyName"><a href="{COMPANY}">Example Law</a></div>
    <div id="salaryInfoAndJobType">$25 - $30 an hour; Full-time</div><div id="jobDetailsSection">8 hour shift</div>
    <div id="benefits">Paid leave</div><div id="jobDescriptionText">{data.get('description', '') if markup is None else markup}</div>
    <a id="applyButtonLink" href="https://careers.example.test/apply/123">Apply on company site</a>
    <script type="application/ld+json">{json.dumps(data)}</script></html>'''


class FakeHTTP:
    def __init__(self, *pages):
        self.pages, self.calls, self.stopped = list(pages), [], ""

    def get(self, url, **kwargs):
        if self.stopped:
            return indeed.Page(url, self.stopped, reason="stopped")
        self.calls.append((url, kwargs))
        if not self.pages:
            raise AssertionError("Unexpected network call: " + url)
        value = self.pages.pop(0)
        if isinstance(value, indeed.Page):
            return indeed.Page(value.url or url, value.status, value.html, value.http_status, value.reason)
        return indeed.Page(url, "ok", value, 200)


def job():
    return {"job_id": JID, "url": URL, "company": "Example Law", "company_url": COMPANY}


class IdentityTests(unittest.TestCase):
    def test_company_canonicalization(self):
        self.assertEqual(indeed.company_url("https://www.indeed.com/cmp/Example-Law/jobs?clearPrefilter=1"), COMPANY)

    def test_regional_domain_preserved(self):
        self.assertEqual(indeed.company_url("https://ca.indeed.com/cmp/Example-Law"), "https://ca.indeed.com/cmp/example-law")

    def test_disallowed_urls(self):
        for url in ("https://indeed.com.evil.test/cmp/foo", "https://evil.indeed.com/cmp/foo", "https://u:p@www.indeed.com/cmp/foo", "http://www.indeed.com/cmp/foo", "https://www.indeed.com:444/cmp/foo", "https://www.indeed.com/cmp/%2e%2e", "https://www.indeed.com/cmp/foo/../bar", "https://www.indeed.com/cmp/a\\b", "https://[invalid"):
            with self.subTest(url=url):
                self.assertFalse(indeed.company_url(url))

    def test_tracking_link_becomes_canonical_detail(self):
        self.assertEqual(indeed.job_identity('/rc/clk?jk=' + JID + '&other=1'), (JID, URL))
        self.assertFalse(indeed.request_allowed(indeed.BASE + '/rc/clk?jk=' + JID))

    def test_duplicate_or_invalid_jk_rejected(self):
        for query in ("jk=" + JID + "&jk=" + JID2, "jk=bad", "vjk=" + JID):
            self.assertEqual(indeed.job_identity('/viewjob?' + query), ("", ""))

    def test_discovery_requires_unique_official_link(self):
        self.assertEqual(indeed.discover_company(f'<a href="{COMPANY}">Indeed</a>', "https://example.test"), COMPANY)
        self.assertEqual(indeed.discover_company(f'<a href="{COMPANY}">A</a><a href="https://www.indeed.com/cmp/other">B</a>', "https://example.test"), "")

    def test_no_api_or_account_routes(self):
        for path in ('/graphql', '/api/jobs', '/account/login', '/pagead/clk?jk=' + JID):
            self.assertFalse(indeed.request_allowed(indeed.BASE + path))

    def test_normal_signin_link_is_not_wall(self):
        self.assertFalse(indeed.wall(listing_page(), COMPANY))
        self.assertTrue(indeed.wall('<title>Just a moment...</title>', COMPANY))


class ListingTests(unittest.TestCase):
    def parse(self, html, company=COMPANY, url=None):
        return indeed.parse_listings(html, url or COMPANY + '/jobs?clearPrefilter=1', 'Example Law', company)

    def test_fields_and_pay(self):
        jobs, _, _ = self.parse(listing_page())
        self.assertEqual(jobs[0]['url'], URL)
        self.assertEqual(jobs[0]['pay_text'], '$25 - $30 an hour')
        self.assertEqual(jobs[0]['location'], 'El Paso, TX')

    def test_other_employers_not_matched(self):
        jobs, _, _ = self.parse(listing_page(listing_card(company=indeed.BASE + '/cmp/other')))
        self.assertEqual(jobs, [])

    def test_confirmed_company_cards_can_omit_employer(self):
        jobs, _, _ = self.parse(listing_page(listing_card(company='', name='')))
        self.assertEqual(len(jobs), 1)

    def test_recommendations_excluded(self):
        jobs, _, _ = self.parse(listing_page('<aside>' + listing_card() + '</aside>'))
        self.assertEqual(jobs, [])

    def test_duplicate_cards(self):
        jobs, _, _ = self.parse(listing_page(listing_card() + listing_card()))
        self.assertEqual(len(jobs), 1)

    def test_search_requires_exact_name(self):
        for name in ('Example Law Services', 'Other Law', ''):
            jobs, _, _ = self.parse(listing_page(listing_card(company='', name=name)), company='', url=indeed.BASE + '/jobs?q=company%3AExample')
            self.assertEqual(jobs, [])

    def test_search_ambiguous_same_name(self):
        html = listing_page(listing_card() + listing_card(JID2, indeed.BASE + '/cmp/other', 'Example Law'))
        self.assertEqual(self.parse(html, company='')[2], 'ambiguous_company')

    def test_pagination_keeps_company_and_filters(self):
        _, nxt, _ = self.parse(listing_page(next_page=True))
        self.assertEqual(nxt, COMPANY + '/jobs?clearPrefilter=1&start=20')
        for link in ('https://evil.test/jobs', '?clearPrefilter=1&l=El+Paso&start=20', '/cmp/other/jobs?clearPrefilter=1'):
            html = listing_page() + f'<a rel="next" href="{link}">Next</a>'
            self.assertEqual(self.parse(html)[1], '')

    def test_json_ld_listing(self):
        html = '<script type="application/ld+json">' + json.dumps({'@graph': [posting()]}) + '</script>'
        self.assertEqual(self.parse(html)[0][0]['job_id'], JID)

    def test_embedded_json_is_not_evaluated(self):
        record = {'jobkey': JID, 'title': 'Paralegal', 'company': 'Example Law', 'salarySnippet': {'text': '$25/hr'}}
        html = '<script>window._initialData = ' + json.dumps({'results': [record]}) + ';throw new Error("DO NOT RUN");</script>'
        jobs, _, _ = self.parse(html)
        self.assertEqual(jobs[0]['listing_metadata']['salarySnippet']['text'], '$25/hr')
        self.assertNotIn('throw', json.dumps(jobs))

    def test_malformed_javascript_is_not_parsed_as_json(self):
        self.assertEqual(self.parse('<script>window._initialData = {jobkey: "123"};</script>')[0], [])

    def test_empty_vs_shape_drift(self):
        self.assertEqual(self.parse('<p>No jobs available</p>')[2], 'no_public_jobs_found')
        self.assertEqual(self.parse('<p>Loading</p>')[2], 'unrecognized_html')


class DetailTests(unittest.TestCase):
    def test_full_description_and_structured_pay(self):
        data = indeed.parse_detail(detail_page(), job())
        self.assertIn('- Prepare documents.', data['description'])
        self.assertEqual(data['base_salary']['value']['unitText'], 'HOUR')
        self.assertEqual(data['apply_url'], 'https://careers.example.test/apply/123')
        self.assertEqual(data['skills'], 'Legal research')
        self.assertEqual(data['benefits'], ['Health insurance', 'Paid time off'])
        self.assertEqual(data['employment_type'], 'FULL_TIME')

    def test_no_truncation(self):
        raw = '<p>' + 'Long description. ' * 10000 + 'LAST SENTENCE.</p>'
        result = indeed.parse_detail(detail_page(markup=raw), job())
        self.assertTrue(result['description'].endswith('LAST SENTENCE.'))
        self.assertFalse(result['description_truncated'])

    def test_html_sanitized_and_relative_links_use_indeed(self):
        raw = '<p onclick="bad()">Real</p><script>secret()</script><a href="/help">help</a><a href="javascript:bad()">label</a><iframe src="x"></iframe>'
        result = indeed.parse_detail(detail_page(markup=raw), job())
        for token in ('onclick', 'secret()', 'javascript:', 'iframe'):
            self.assertNotIn(token, result['description_html'])
        self.assertIn('https://www.indeed.com/help', result['description_html'])
        self.assertNotIn('linkedin.com', result['description_html'])

    def test_job_mismatch(self):
        self.assertEqual(indeed.parse_detail(detail_page(JID2), job())['detail_status'], 'job_mismatch')

    def test_employer_mismatch_even_same_name(self):
        result = indeed.parse_detail(detail_page(data=posting(company=indeed.BASE + '/cmp/other')), job())
        self.assertEqual(result['detail_status'], 'company_mismatch')

    def test_recommended_schema_not_used(self):
        result = indeed.parse_detail(detail_page(data=posting(JID2)), job())
        self.assertEqual(result['detail_status'], 'job_mismatch')

    def test_missing_description_is_partial_not_teaser(self):
        data = posting(); data.pop('description')
        result = indeed.parse_detail(detail_page(data=data, markup=''), job())
        self.assertEqual(result['detail_status'], 'partial')
        self.assertIsNone(result['description'])

    def test_estimated_salary_not_employer_pay(self):
        data = posting(); data['estimatedSalary'] = data.pop('baseSalary')
        result = indeed.parse_detail(detail_page(data=data), job())
        self.assertEqual(result['pay_basis'], 'estimated')
        self.assertNotIn('base_salary', result)

    def test_expired_keeps_full_text(self):
        data = posting(); data['validThrough'] = '2001-01-01'
        result = indeed.parse_detail(detail_page(data=data), job())
        self.assertEqual(result['listing_status'], 'closed')
        self.assertTrue(result['description'])

    def test_closed_words_in_description_not_job_status(self):
        result = indeed.parse_detail(detail_page(markup='<p>We work on closed cases.</p>'), job())
        self.assertEqual(result['listing_status'], 'observed')

    def test_json_ld_description_fallback(self):
        html = detail_page().replace('id="jobDescriptionText"', 'id="unrelated"')
        result = indeed.parse_detail(html, job())
        self.assertEqual(result['description_source'], 'json_ld')
        self.assertIn('Full first paragraph.', result['description'])

    def test_recommended_description_not_used(self):
        html = '<aside><div id="jobDescriptionText">WRONG JOB</div></aside>' + detail_page()
        self.assertNotIn('WRONG JOB', indeed.parse_detail(html, job())['description'])

    def test_generic_metadata_not_mislabelled_pay(self):
        data = posting(); data.pop('baseSalary')
        html = detail_page(data=data).replace('id="salaryInfoAndJobType"', 'id="other"')
        html += '<div data-testid="jobsearch-JobMetadataHeader-item">Remote</div>'
        self.assertNotIn('pay_text', indeed.parse_detail(html, job()))

    def test_application_link_never_fetched(self):
        client = FakeHTTP(detail_page())
        result = indeed.fetch_detail(job(), client)
        self.assertTrue(result['apply_url'])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], URL)

    def test_refusals_not_retried(self):
        for state in ('blocked', 'rate_limited', 'not_found', 'network_error'):
            client = FakeHTTP(indeed.Page('', state))
            result = indeed.fetch_detail(job(), client)
            self.assertEqual(result['detail_status'], state)
            self.assertEqual(len(client.calls), 1)


class FetchTests(unittest.TestCase):
    def fetch(self, client, **kwargs):
        return indeed.fetch_company({'slug': 'example-law', 'firm': 'Example Law', 'indeed_company': COMPANY}, client, **kwargs)

    def test_company_fetch_and_descriptions(self):
        client = FakeHTTP(listing_page(), detail_page())
        result = self.fetch(client)
        self.assertTrue(result['hiring']['is_hiring'])
        self.assertIn('clearPrefilter=1', client.calls[0][0])
        self.assertTrue(result['hiring']['jobs'][0]['description'])
        self.assertFalse(result['hiring']['complete'])

    def test_pagination_and_deduplication(self):
        client = FakeHTTP(listing_page(next_page=True), listing_page(listing_card() + listing_card(JID2)), detail_page(), detail_page(JID2))
        result = self.fetch(client)
        self.assertEqual(result['hiring']['observed_job_count'], 2)
        self.assertEqual(len(client.calls), 4)

    def test_caps(self):
        client = FakeHTTP(listing_page(listing_card() + listing_card(JID2), next_page=True), detail_page())
        result = self.fetch(client, max_jobs=1)
        self.assertEqual(result['hiring']['stop_reason'], 'job_limit')
        self.assertEqual(len(client.calls), 2)

    def test_repeated_page_stops(self):
        client = FakeHTTP(listing_page(next_page=True), listing_page(next_page=True), detail_page())
        result = self.fetch(client)
        self.assertEqual(result['hiring']['stop_reason'], 'repeated_page')

    def test_404_details_do_not_imply_hiring(self):
        result = self.fetch(FakeHTTP(listing_page(), indeed.Page('', 'not_found')))
        self.assertIsNone(result['hiring']['is_hiring'])

    def test_block_stops_remaining_jobs(self):
        client = FakeHTTP(listing_page(listing_card() + listing_card(JID2)), indeed.Page('', 'blocked'))
        result = self.fetch(client)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result['hiring']['jobs'][1]['detail_status'], 'not_attempted')

    def test_block_during_listing_stops_detail_requests(self):
        client = FakeHTTP(listing_page(next_page=True), indeed.Page('', 'rate_limited'))
        result = self.fetch(client)
        self.assertEqual(result['hiring']['jobs'][0]['detail_status'], 'not_attempted')
        self.assertEqual(len(client.calls), 2)

    def test_empty_or_blocked_never_false_hiring(self):
        for page in ('<p>No jobs available</p>', indeed.Page('', 'blocked'), '<p>Loading</p>'):
            result = self.fetch(FakeHTTP(page))
            self.assertIsNone(result['hiring']['is_hiring'])

    def test_search_without_company_url(self):
        client = FakeHTTP(listing_card(), detail_page())
        result = indeed.fetch_company({'firm': 'Example Law'}, client)
        self.assertEqual(result['discovery'], 'exact_employer_search')
        self.assertTrue(result['hiring']['is_hiring'])
        self.assertIn('company%3A%22Example+Law%22', client.calls[0][0])

    def test_homepage_discovery(self):
        client = FakeHTTP(f'<a href="{COMPANY}">Jobs</a>', listing_page(), detail_page())
        result = indeed.fetch_company({'firm': 'Example Law', 'website': 'https://example.test'}, client)
        self.assertEqual(result['company_url'], COMPANY)
        self.assertEqual(client.calls[0][1], {'website': True})

    def test_invalid_company_not_guessed(self):
        client = FakeHTTP()
        result = indeed.fetch_company({'firm': 'Example Law', 'indeed_company': 'https://evil.test/cmp/foo'}, client)
        self.assertEqual(client.calls, [])
        self.assertEqual(result['hiring']['stop_reason'], 'invalid_company_url')


class TransportTests(unittest.TestCase):
    def test_rate_limit_stops_batch(self):
        client = indeed.IndeedHTTP()
        headers = Message(); headers['Retry-After'] = '60'
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError(URL, 429, 'rate limited', headers, None)
        with patch.object(indeed, 'public_host'), patch.object(indeed.time, 'sleep'):
            self.assertEqual(client.get(URL).status, 'rate_limited')
            self.assertEqual(client.get(URL).status, 'rate_limited')
        self.assertEqual(client.opener.open.call_count, 1)
        request = client.opener.open.call_args.args[0]
        self.assertNotIn('Cookie', request.headers)
        self.assertNotIn('Authorization', request.headers)

    def test_login_redirect_not_followed(self):
        client = indeed.IndeedHTTP()
        headers = Message(); headers['Location'] = 'https://secure.indeed.com/account/login'
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError(URL, 302, 'redirect', headers, None)
        with patch.object(indeed, 'public_host'), patch.object(indeed.time, 'sleep'):
            self.assertEqual(client.get(URL).status, 'blocked')
        self.assertEqual(client.opener.open.call_count, 1)

    def test_private_destination_rejected(self):
        client = indeed.IndeedHTTP()
        with patch.object(indeed, 'public_host', side_effect=ValueError('private')):
            self.assertEqual(client.get(URL).status, 'unsafe_url')


class MergeTests(unittest.TestCase):
    def state(self, board='linkedin', status='hiring'):
        return {'status': status, 'is_hiring': True if status == 'hiring' else None, 'jobs': [{'job_id': JID, 'url': URL, 'source': board, 'description': board + ' full text'}] if status == 'hiring' else [], 'checked_at': '2026-09-18', 'observed_job_count': 1 if status == 'hiring' else 0, 'stop_reason': 'done'}

    def test_sources_never_overwrite(self):
        profile = {}
        row = {'hiring': self.state(), 'indeed': {'hiring': self.state('indeed')}, 'checked_at': '2026-09-18'}
        merge_hiring(profile, row)
        self.assertEqual(len(profile['hiring']['jobs']), 2)
        self.assertEqual(profile['hiring_sources']['linkedin']['jobs'][0]['description'], 'linkedin full text')
        self.assertEqual(profile['hiring_sources']['indeed']['jobs'][0]['description'], 'indeed full text')
        self.assertIsNone(profile['hiring']['distinct_vacancy_count'])

    def test_unknown_preserves_history_not_fresh_yes(self):
        profile = {}
        merge_hiring(profile, {'hiring': self.state(), 'indeed': {'hiring': self.state('indeed')}})
        merge_hiring(profile, {'hiring': self.state(status='unknown'), 'indeed': {'hiring': self.state('indeed', 'unknown')}})
        self.assertIsNone(profile['hiring']['is_hiring'])
        self.assertTrue(profile['hiring_last_successful']['indeed']['jobs'])

    def test_one_blocked_board_does_not_hide_other(self):
        profile = {}
        merge_hiring(profile, {'hiring': self.state(status='unknown'), 'indeed': {'hiring': self.state('indeed')}})
        self.assertTrue(profile['hiring']['is_hiring'])

    def test_not_checked_source_is_not_current(self):
        profile = {}
        merge_hiring(profile, {'hiring': self.state(), 'indeed': {'hiring': self.state('indeed')}})
        merge_hiring(profile, {'hiring': self.state(status='unknown')})
        self.assertIsNone(profile['hiring']['is_hiring'])
        self.assertEqual(profile['hiring']['not_checked_sources'], ['indeed'])

    def test_idempotent_and_does_not_mutate_input(self):
        row = {'hiring': self.state(), 'indeed': {'hiring': self.state('indeed')}}
        original = deepcopy(row); profile = {}
        merge_hiring(profile, row); snapshot = deepcopy(profile)
        merge_hiring(profile, row)
        self.assertEqual(snapshot, profile)
        self.assertEqual(original, row)

    def test_indeed_only_keeps_linkedin_snapshot(self):
        profile = {'hiring': self.state()}
        merge_hiring(profile, {'linkedin_skipped': True, 'indeed': {'hiring': self.state('indeed')}})
        self.assertEqual(profile['hiring']['checked_sources'], ['indeed'])
        self.assertTrue(profile['hiring_sources']['linkedin']['jobs'])

    def test_recrawl_preservation_recovers_correct_source_history(self):
        profile = {}
        merge_hiring(profile, {'hiring': self.state(), 'indeed': {'hiring': self.state('indeed')}})
        recrawled = {'hiring': deepcopy(profile['hiring'])}
        merge_hiring(recrawled, {'hiring': self.state(status='unknown'), 'indeed': {'hiring': self.state('indeed', 'unknown')}})
        self.assertEqual(recrawled['hiring_last_successful']['indeed']['jobs'][0]['description'], 'indeed full text')
        self.assertEqual(recrawled['hiring_last_successful']['linkedin']['jobs'][0]['description'], 'linkedin full text')
        self.assertIsNone(recrawled['hiring']['is_hiring'])

    def test_failed_description_preserves_history_without_filling_fresh_text(self):
        profile = {}
        merge_hiring(profile, {'indeed': {'hiring': self.state('indeed')}})
        partial = self.state('indeed'); partial['jobs'][0]['description'] = None
        merge_hiring(profile, {'indeed': {'hiring': partial}})
        row = profile['hiring']['jobs'][0]
        self.assertIsNone(row['description'])
        self.assertEqual(row['last_successful_details']['description'], 'indeed full text')


if __name__ == '__main__':
    unittest.main()

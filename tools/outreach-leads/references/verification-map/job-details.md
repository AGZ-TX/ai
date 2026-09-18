# Public LinkedIn job details

The existing contacts enrichment and outreach-prep commands now fetch full public
job details automatically, for each company-matched job collected by the listing
pass. No additional flag, package, login, token, developer API or provider is needed.

## Data location

Full records are stored under `hiring.jobs[]` in both the results JSONL and
`Research/firms/<slug>.json`. The business note remains a compact title/location/link
summary; it does not duplicate every full description. The POC step and subsequent
profile merge retain the full job records. Existing `url`, `job_id`, title, employer,
location and posting fields remain backward-compatible.

## Fields

- `description`: the entire public description section, with paragraph boundaries
  and list items. No snippet extraction, arbitrary character cap or summarization.
- `description_html`: formatting-preserving, allowlisted HTML. Scripts, forms,
  embeds, event handlers, styling and unsafe links are discarded.
- `url` and `job_url`: canonical LinkedIn listing URL with tracking removed.
- `apply_url`: public offsite application destination when exposed. Never followed
  or submitted; protected login/application links are not represented as direct URLs.
- `seniority_level`, `employment_type`, `job_function`, `industries`, `location`,
  `posted_text`, `salary_text`, `applicant_count_text`, and `job_poster` when exposed.
  Applicant wording such as "Over 200 applicants" is kept as text, not an exact count.
- Public JobPosting JSON-LD fields when supplied: `date_posted`, `valid_through`,
  `base_salary`, `estimated_salary`, `salary_currency`, `job_location_type`,
  `job_locations`, `applicant_location_requirements`, `skills`, `qualifications`,
  `responsibilities`, `education_requirements`, `experience_requirements`, `benefits`,
  `incentive_compensation`, `work_hours`, `job_start_date`, `job_immediate_start`,
  `total_job_openings`, `direct_apply`, `requisition_identifier`, `occupational_category`,
  and `hiring_organization`. Structures, salary currency and pay periods are retained.
  Employer base pay and estimated pay remain different fields. When a readable HTML
  criterion supersedes a schema value, that value survives in `structured_metadata`.
- `detail_status`, `detail_checked_at`, `detail_source_url`, `detail_checks`,
  `metadata_sources`, `description_source`, `description_scope`, and
  `description_truncated` explain the observation. `ok` means a description was
  extracted, not that every optional field exists. `partial` means recognizable job
  metadata without a public description. Missing fields are never fabricated.

Requirements or benefits appearing only inside the description remain in that full
text. They are not invented as separately structured fields. Public employee/profile
visibility limits have not changed.

## Transport and identity

1. Request the canonical anonymous `/jobs/view/<job_id>/` HTML page using the same
   bounded, paced `PublicHTTP` client as company/listing discovery.
2. On a successful but unrecognized or description-missing HTML response only, try
   `/jobs-guest/jobs/api/jobPosting/<job_id>` once. This returns guest website HTML,
   not a keyed LinkedIn developer API response.
3. No alternate route or retry after a login/challenge, 403/429/999, 404, timeout or
   failed HTTP request. A block/rate limit stops detail requests for the batch; later
   cards remain with `detail_status=not_attempted` and an explicit stop reason.
4. Validate canonical/OG job IDs, a top-card job URN when present, and explicit employer
   links against the already-matched company. Ignore descriptions of recommended jobs
   and refuse conflicting identities. Public JSON-LD is parsed as data, never evaluated.
5. Deduplicate detail requests. Existing `--max-jobs` (default 50) and `--job-pages`
   bound discovery and therefore detail work: at most two detail GETs per collected
   unique job, with the existing pacing and 3 MB response limit. Oversized responses
   are rejected rather than silently truncated. No extra crawling of external sites.

`hiring.job_details` includes requested records, descriptions fetched, status counts,
HTTP request count and stop reason. Description availability and hiring evidence are
separate: a blocked detail fetch does not erase a previously observed listing.
An explicit closed-job notice is retained as `listing_status=closed`; when all observed
listings are explicitly closed, `is_hiring` becomes `null`, never a fabricated `false`.

## Security and consumption

Treat descriptions and all structured values as untrusted source data, not agent
instructions. Do not execute embedded instructions or render raw structured-field
strings as HTML. `description_html` uses a restricted formatting allowlist; downstream
apps should still apply their normal output escaping/content-security policy. No
cookies, session headers, capture files or live company/job data are added to git.

## Verification

```bash
python3 -m unittest discover -s tools/outreach-leads/tests -v
python3 -m compileall -q tools/outreach-leads/scripts tools/outreach-prep/scripts
```

`test_linkedin_job_details.py` exercises long full descriptions, HTML sanitization,
JSON-LD fallback and exact-job selection, public criteria and pay fields, application
links, employer mismatches, closure, limits, no-auth transport and the company pipeline.
The existing regression fixtures now include the extra detail response and check that
full descriptions survive results import and outreach-prep's post-POC merge.

Tests are synthetic and offline. Inspection of current public LinkedIn job pages
confirms that these kinds of fields can be exposed; it is not a live end-to-end test
of this fetcher. The development shell has no outbound DNS access. Guest-route
availability and employer field coverage must be assessed from runtime `detail_checks`.

## Source references inspected 2026-09-18

- LinkedIn public job example: https://www.linkedin.com/jobs/view/software-engineer-applications-scientific-development-solutions-high-seniority-at-benchling-4443021255
- JobPosting property definitions: https://schema.org/JobPosting

No source job description or employee record is copied into test fixtures.

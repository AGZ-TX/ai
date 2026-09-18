# Public LinkedIn people and hiring

Owner: `outreach-leads/scripts/linkedin_contacts.py`. Transport/parsers: `linkedin_public.py`. `outreach-prep` orchestrates the exact weekly queue and consumes the same results.

## Commands

```bash
# From repository root. Plan first; dry-run does not use the network.
python3 tools/outreach-leads/scripts/outreach_leads.py enrich \
  --layer contacts --category Law --practice pi --limit 5 --dry-run
python3 tools/outreach-leads/scripts/outreach_leads.py enrich \
  --layer contacts --category Law --practice pi --limit 5 --no-push

# Advanced flags use the owning script directly.
python3 tools/outreach-leads/scripts/linkedin_contacts.py \
  --slug example-law --company-url https://www.linkedin.com/company/example-law/ \
  --max-jobs 50 --job-pages 3 --no-push

# Legacy queue/apply is still supported without automated login.
python3 tools/outreach-leads/scripts/linkedin_contacts.py --category Law --queue-only --force
python3 tools/outreach-leads/scripts/linkedin_contacts.py --apply /path/to/results.jsonl --no-push

# Offline tests: synthetic HTML, mocked HTTP, temporary vaults.
python3 -m unittest discover -s tools/outreach-leads/tests -v
```

## Transport contract

Anonymous public company HTML supplies employee cards and, when unambiguous, a numeric company ID from company job navigation. Numeric IDs enable the public guest HTML endpoint `/jobs-guest/jobs/api/seeMoreJobPostings` with `f_C` and `start`. Without a verified single ID, use the company's public `/jobs/` page; do not choose an affiliate ID from a multi-company filter.

No keys, cookies, Voyager/authenticated endpoints, paid scraping services, or browser profiles. The `api` segment in the guest URL is LinkedIn's public website route, not a developer-API integration. Only allowlisted LinkedIn website paths are fetched. Official website homepage discovery is limited to same-host/www redirects and rejects private/local destinations.

Default limits: 50 jobs, 3 pages, 2 seconds between LinkedIn requests, 15-second per-request timeout, 3 MB response, 3 redirects. Limits are caps, not promised result counts. HTTP 401/403/429/999, login redirects and verification walls stop LinkedIn requests for the rest of the batch. No retries with rotated proxies/accounts/user agents. Unknown HTML is not an empty business result.

## Evidence model

One JSONL object per attempted company, including unsuccessful attempts:

```json
{
  "schema_version": 1,
  "source": "linkedin_public",
  "slug": "example-law",
  "linkedin_company": "https://www.linkedin.com/company/example-law/",
  "status": "linked",
  "checked_at": "2026-09-18T12:00:00+00:00",
  "contacts": [],
  "people": {
    "status": "not_public",
    "observed_count": 0,
    "coverage": "public_sample",
    "complete": false
  },
  "hiring": {
    "status": "unknown",
    "is_hiring": null,
    "observed_job_count": 0,
    "jobs": [],
    "coverage": "public_listings",
    "complete": false,
    "stop_reason": "blocked",
    "source_urls": []
  },
  "checks": []
}
```

This is an illustrative shape, not a captured company result. Actual `checks` contain URL, HTTP status, outcome and reason. Each contact includes its public profile URL, company source URL, evidence type and observed time. Names/titles/photos are copied only when visible. Public mode does not infer emails or individual locations. Job entries contain ID, title, canonical URL, matched employer URL, location and posted date/text when exposed.

Hiring states:

- `hiring`: at least one company-matched listing was observed (`is_hiring=true`). A listing is not a guarantee that the vacancy is still unfilled.
- `no_public_jobs_found`: explicit empty public response. `is_hiring=null`, not false.
- `unknown`: blocked, missing company identity, unrecognized markup, ambiguous blank first response, or no verifiable employer match. `is_hiring=null`.

Pagination deduplicates by job ID, advances by raw card count and stops on repeated results, configured caps, explicit exhaustion or access refusal. Generic footer counts, affiliate listings and mere company-name keywords are excluded. Counts always mean **observed matching listings**, not company-wide totals. Employee cards are always **a public sample**, never a full roster.

## Persistence and reruns

Keep `website:` unchanged. Company URLs go in `linkedin_company:` / profile `linkedin.company_url`. Merge contacts rather than replacing the directory; missing fields and blocked/partial samples do not erase prior people or emails. Reapplying the same result is idempotent. Preserve manual contact bullets and unrelated note sections.

Write current uncertainty to `hiring`; preserve a prior successful snapshot separately under `linkedin_last_successful_hiring`. Never display that historical snapshot as a fresh positive result. The prep driver restores its same-run evidence after the existing POC ranker rewrites LinkedIn metadata, and includes hiring evidence in its shortlist.

`--apply` rejects malformed rows and note paths outside the selected vault, reports line errors, and returns nonzero while still processing valid rows. Dry-run reads and validates but does not fetch, mutate, or back up. Real writes retain existing optional backup behavior. Daily direct-run artifacts overwrite that day's previous artifact; specify `--output` to retain a named run. Prep generates unique scoped filenames and never silently imports an unrelated same-day file.

## Verification limits

Offline tests validate parsing, identity matching, pagination, block handling, HTTP safety, imports, idempotence, field preservation, dry-runs, and prep integration. They do **not** prove LinkedIn will serve every company from every IP. Current public company/jobs pages were inspected during development; a live guest-endpoint HTTP fetch could not be completed in the development environment. A first deployment run should inspect actual `checks` and sample job/profile links before relying on the output.

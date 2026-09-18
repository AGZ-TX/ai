# Company identity: LinkedIn and Indeed

The owning contacts CLI and outreach-prep must resolve the **actual company's
public profile before collecting its people or job details**. An exact employer
name is a discovery hint, not verification. This gate supersedes the earlier
name-only Indeed fallback and automatically trusted saved company URLs.

## Inputs and automatic resolution

The source of truth is the vault note's official `website:` and business `name:`.
A saved `linkedin_company:` / `indeed_company:` or CLI company URL is a candidate,
not an override. Revalidate it on each network run. Results imported explicitly
are checked offline against their retained evidence and the current note inputs;
imports do not make network calls or become newly observed evidence.

The resolver checks a candidate's public company page for the employer's
**Website** field or a company-scoped Organization JSON-LD record. If that website
matches the official domain and the displayed/legal name matches an explicit
name, the mapping is verified. Ignore unrelated organization, employee, partner,
recommendation, testimonial, review-author and job-description links.

When the saved mapping cannot be verified, read the official homepage and up to
two exposed about/contact/careers pages. A self-identifying social/profile link
or scoped Organization `sameAs` can establish the connection, including trade
names that differ from the note. Do not guess company slugs or site paths. An
explicit conflicting Website field still rejects the candidate.

A public job-search page may discover additional named company-profile URLs.
Open those company pages and verify them using the same domain/link rules. The
search result itself never establishes identity or hiring. Inspect at most five
company candidates and one search page per source. Multiple verified profiles,
conflicting identities, missing evidence, or the candidate cap produce an
explicit unresolved outcome instead of picking the first result.

## Matching rules

- Compare exact IDNA hostnames, ignoring only `www.` and HTTP versus HTTPS.
  Do not compare substrings, automatically merge sibling/subdomains, or guess
  registrable domains by taking the last two labels. Lookalikes stay distinct.
- Social/directory/shared-hosting roots, IP addresses, credentials and unsafe
  URLs are not official company-domain evidence.
- Preserve business words when normalizing Unicode, case and punctuation. A
  shared name, city, address fragment or job location does not prove identity.
- Retain observed employer locations and industry for review. Do not reject a
  valid domain-linked company merely because its headquarters differ from the
  prospect's branch office or the job's remote location.
- LinkedIn numeric company navigation IDs may resolve to a canonical slug when
  that numeric identity is exposed on the same page. Unexplained canonical or
  redirect changes are conflicts. Indeed regional company URLs stay distinct.
- Parent, subsidiary, franchise and DBA identities are not fuzzy-merged. Explicit
  aliases can help match a displayed name, but still require domain evidence.
- A saved URL with insufficient evidence can be repaired automatically when the
  official site establishes another, verified profile. Preserve the rejected
  candidate and its reason in the audit record.

Optional explicit names in the **local frontmatter**, not imported job text:

```yaml
name: "Acme Holdings LLC"
website: "https://acme.example"
legal_name: "Acme Holdings LLC"
company_aliases: ["Acme Manufacturing", "Acme"]
linkedin_company: "https://www.linkedin.com/company/acme/"
indeed_company: "https://www.indeed.com/cmp/acme"
```

Aliases are a JSON-style array of strings. They are not inferred from similar
names, and they are not a bypass for a conflicting domain.

## Persistence and enforcement

`company_identity.sources.linkedin` and `.indeed` in the firm profile retain:

- Verification policy, source, status, reason and observation time.
- Bound target: note slug, normalized official host, explicit normalized names,
  and a deterministic fingerprint of those inputs.
- Canonical company URL, exposed LinkedIn ID, displayed/observed names, website
  URLs, locations and industry when present.
- Evidence URLs and relationship type (`profile_website`, `official_link`, or
  `official_same_as`), candidate evaluations and HTTP outcomes.

Each raw source result carries `identity`; each source's hiring snapshot also
carries `company_identity`. Nested provenance survives the existing prep
recrawl/POC restoration even when a recrawl omits new top-level fields. Notes get
a compact `## Company identity` section with statuses and source evidence.

Only verified, target-bound source results enter current automatic contacts and
hiring. Jobs must also carry the resolved employer URL (or its verified LinkedIn
numeric alias). Conflicting jobs are quarantined and excluded from current
hiring counts. LinkedIn and Indeed remain independent: one board's refusal or
identity failure does not establish anything about the other board.

Old public JSONL without identity evidence, results for a changed company/domain,
and rejected jobs/contacts are stored under `company_identity.quarantine`, not
promoted as current verified records. Previously automatic LinkedIn contacts and
POCs are retired from active JSON on an unverified or changed identity; prior
observations remain archived. Manual Contacts notes are retained as historical
notes and are explicitly **not re-certified**. Explicit legacy manual-contact
imports are not automatic platform verification.

A fingerprint binds evidence to inputs; it is **not a signature**. Only import
trusted local research artifacts. Offline imports validate their structure and
relationships; they do not independently authenticate the original collector or
re-fetch the cited pages. Historical timestamps remain historical.

## Transport and limits

Reuse the existing public-only HTTP clients: no API keys, login, cookies,
private endpoints, proxies, fingerprint spoofing or CAPTCHA solving. A refusal
stops that source for the batch. Do not try an alternate route after refusal.
Successful identity pages are reused within the firm/source pass to avoid
redundant requests; there is no permanent unverified mapping cache. Existing job
pagination/detail limits and pacing remain in force.

The gate improves attribution; it does not guarantee complete public visibility,
a complete employee roster, every vacancy, or a company's legal registration.
`unverified`, `conflict`, `ambiguous`, blocked and rate-limited identities produce
unknown hiring, never a guessed company or `is_hiring=false`.

## Verification

```bash
python3 -m compileall -q tools/outreach-leads/scripts tools/outreach-prep/scripts
python3 -m unittest discover -s tools/outreach-leads/tests -v
```

Tests use synthetic HTML, mocked HTTP and temporary vaults. They exercise both
providers, lookalike/same-name firms, wrong saved mappings, explicit aliases,
JSON-LD scope, redirect conflicts, malformed evidence, refusal handling,
identity-bound imports, historical quarantine and the actual contacts/prep
pipeline. CI/offline success is not live LinkedIn/Indeed end-to-end validation.

Primary references inspected September 18, 2026:

- Schema.org Organization: https://schema.org/Organization
- Schema.org sameAs: https://schema.org/sameAs
- Public LinkedIn employer page and its Website field:
  https://www.linkedin.com/company/ibm
- Public Indeed employer page and company website link:
  https://www.indeed.com/cmp/IBM

These are observations of public representations, not a stable vendor API or
an assertion that the sources authenticate every employer claim.

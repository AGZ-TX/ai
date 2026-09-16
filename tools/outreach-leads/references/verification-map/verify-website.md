# verify-website

verify-website is the gate used by enrich before writing `website:`. Exit 0 + `OK\turl` on pass; exit 1 + reject reason on fail.

## Sub-features

- `vw-ok` accepts official firm domains when firm-name tokens match.
- `vw-reject-directory` rejects Justia/FindLaw/Avvo and other blocklist hosts.
- `vw-reject-mismatch` rejects pages that do not carry firm name tokens.

## How to get to it (user POV)

- `python3 scripts/outreach_leads.py verify-website --url https://example.com --firm 'Example Law'`
- `python3 scripts/outreach_leads.py verify-website --url 'https://www.justia.com/lawyers/...' --firm 'Anyone'`
- Direct: `python3 scripts/verify_website.py URL --firm NAME`

## Driving it with outreach_leads

Preconditions:

- Network allowed
- Known good: `https://example.com` / firm Example Law

- **OK path.** Run verify on a known official firm domain. Exit 0; stdout starts with `OK`.
- **Directory reject.** Run verify on a justia.com URL. Exit 1; stderr contains `directory_host`.
- **Proof.** Both outcomes recorded in VERIFY.md. Pass = OK + reject as above; fail = Justia exits 0 or the official domain rejects as directory.

## Gotchas

- Redirects to a directory host must reject.
- Firm token stopwords drop LAW/FIRM/LLC — use distinctive name tokens.

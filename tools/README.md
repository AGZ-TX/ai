# tools

Agent skills for this repo. Each folder is a skill id. Frontmatter `name:` matches the folder.

Install from GitHub: **https://github.com/AGZ-TX/ai** → `tools/<id>/SKILL.md`.

```bash
git clone https://github.com/AGZ-TX/ai.git && cd ai && ./tools/install.sh
```

## One-liner per host

- Clone this repo and add `tools/` as the skills root.
- Or copy one folder (`tools/website-search`, …) into the host’s skills directory.
- Set `VAULT_ROOT` to a writable `./data` (created by `install.sh`).
- Do not commit `data/`, Chrome profiles, HARs, cookies, or `posts.json`.

## Compose

```
leads → prep → website-search → website-to-api
                 search-x-profile   search-x
```

`outreach-prep` calls `website-search` and prefers `website-to-api` LinkedIn replay when a recipe exists. `search-x` is Search-page; `search-x-profile` is one profile.

## Per tool

### outreach-leads

- **Deps:** Python 3 stdlib. Optional: `gh` + `BACKUP_REPO` for push-backup.
- **Env:** `VAULT_ROOT` (default `./data`), `BACKUP_REPO`, `GEO_CITIES`, `DEFAULT_CITY`, `LOCAL_AREA_CODES`, `VAULT_TZ`.
- **Doctor / prove:**
  ```bash
  python3 tools/outreach-leads/scripts/outreach_leads.py doctor --init
  python3 tools/outreach-leads/scripts/outreach_leads.py coverage-check --category Law
  python3 tools/outreach-leads/scripts/outreach_leads.py specialty-directory --vertical pi --geo texas --dry-run --limit 5
  ```

### outreach-prep

- **Deps:** same as outreach-leads + website-search on `PATH` as sibling folders.
- **Env:** `VAULT_ROOT`, `TOOLS_ROOT` (defaults to this `tools/` directory).
- **Doctor / prove:**
  ```bash
  python3 tools/outreach-prep/scripts/run_outreach_prep.py --category Law --max-firms 2 --dry-run
  ```

### website-search

- **Deps:** Python 3 stdlib.
- **Env:** `VAULT_ROOT` for default JSON paths.
- **Doctor / prove:**
  ```bash
  python3 tools/website-search/scripts/prove_full_text.py
  python3 tools/website-search/scripts/research_website.py example.com --max-pages 8 --concurrency 4
  ```

### website-to-api

- **Deps:** `pip install patchright` then `python3 -m patchright install chrome`. Camoufox is the named fallback.
- **Env:** `CHROME_PROFILE`, `WEBSITE_TO_API_OUT` (default `./data/website-to-api-capture`).
- **Doctor / prove:** signed-in session required. Never commit cookies.
  ```bash
  python3 tools/website-to-api/scripts/capture_x_profile.py --help
  ```

### search-x-profile

- **Deps:** patchright + headed Chrome. Signed-in session required. Never commit cookies.
- **Env:** `CHROME_PROFILE`, `SEARCH_X_PROFILE_OUT` (default `./data/search-x-profile`).
- **Doctor / prove:**
  ```bash
  python3 tools/search-x-profile/scripts/capture_profile.py --self-check
  python3 tools/search-x-profile/scripts/digest_voice.py --self-check
  ```

### search-x

- **Deps:** same as search-x-profile.
- **Env:** `CHROME_PROFILE`, `SEARCH_X_OUT` (default `./data/search-x`).
- **Doctor / prove:**
  ```bash
  python3 tools/search-x/scripts/capture_search.py --self-check
  python3 tools/search-x/scripts/digest_results.py --self-check
  ```

Live Search capture:

```bash
python3 tools/search-x/scripts/capture_search.py --query 'texas hvac' --tab latest --cdp http://127.0.0.1:9225
```

## Secrets

Never commit cookies, tokens, HARs with auth, `posts.json`, or vault notes with real POCs. Recipes keep URL patterns, operationNames, queryIds, and header **names** only.

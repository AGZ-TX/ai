#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export VAULT_ROOT="${VAULT_ROOT:-$ROOT/data}"
cd "$ROOT"
python3 tools/outreach-leads/scripts/outreach_leads.py doctor --init --vault "$VAULT_ROOT"
python3 tools/website-search/scripts/prove_full_text.py
python3 tools/search-x-profile/scripts/capture_profile.py --self-check
python3 tools/search-x-profile/scripts/digest_voice.py --self-check
python3 tools/search-x/scripts/capture_search.py --self-check
python3 tools/search-x/scripts/digest_results.py --self-check
python3 tools/outreach-prep/scripts/run_outreach_prep.py --help >/dev/null
echo "doctor ok"

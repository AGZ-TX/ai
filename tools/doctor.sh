#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export VAULT_ROOT="${VAULT_ROOT:-$ROOT/data}"
cd "$ROOT"
if [[ -e tools/search-x-profiles ]]; then
  echo "legacy folder tools/search-x-profiles must be search-x-profile" >&2
  exit 1
fi
for skill_dir in tools/*/; do
  id="$(basename "$skill_dir")"
  [[ -f "${skill_dir}SKILL.md" ]] || continue
  if ! grep -q "^name: ${id}$" "${skill_dir}SKILL.md"; then
    echo "SKILL.md name: must match folder ${id}" >&2
    exit 1
  fi
done
python3 tools/outreach-leads/scripts/outreach_leads.py doctor --init --vault "$VAULT_ROOT"
python3 tools/website-search/scripts/prove_full_text.py
python3 tools/search-x-profile/scripts/capture_profile.py --self-check
python3 tools/search-x-profile/scripts/digest_voice.py --self-check
python3 tools/search-x/scripts/capture_search.py --self-check
python3 tools/search-x/scripts/digest_results.py --self-check
python3 tools/outreach-prep/scripts/run_outreach_prep.py --help >/dev/null
echo "doctor ok"

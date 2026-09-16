#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export VAULT_ROOT="${VAULT_ROOT:-$ROOT/data}"
python3 "$ROOT/tools/outreach-leads/scripts/outreach_leads.py" doctor --init --vault "$VAULT_ROOT"
echo "VAULT_ROOT=$VAULT_ROOT"
echo "stdlib tools ready. For X/LinkedIn capture: python3 -m pip install patchright && python3 -m patchright install chrome"
echo "prove: $ROOT/tools/doctor.sh"

#!/usr/bin/env bash
# doctor.sh — Self-IP Agency runtime health check
# Usage: bash scripts/doctor.sh [--workspace /path/to/workspace]
#
# Validates that the runtime is correctly configured and all required
# files/directories are in place. Run this after install.sh to verify.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"

# ── Parse workspace arg ─────────────────────────────────────────────────────
WORKSPACE="${OPENCLAW_WORKSPACE:-}"
for arg in "$@"; do
  case "$arg" in
    --workspace=*) WORKSPACE="${arg#--workspace=}" ;;
    --workspace) shift; WORKSPACE="${1:-}" ;;
  esac
done
if [ -z "$WORKSPACE" ]; then
  WORKSPACE="$HOME/.openclaw/workspace"
fi

# ── Color helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
PASS=0; WARN=0; FAIL=0

ok()   { echo -e "  ${GREEN}✓${RESET} $1"; PASS=$((PASS+1)); }
warn() { echo -e "  ${YELLOW}!${RESET} $1"; WARN=$((WARN+1)); }
fail() { echo -e "  ${RED}✗${RESET} $1"; FAIL=$((FAIL+1)); }

check_file()     { [ -f "$1" ] && ok "$2" || fail "$2 — missing: $1"; }
check_dir()      { [ -d "$1" ] && ok "$2" || fail "$2 — missing: $1"; }
check_file_warn(){ [ -f "$1" ] && ok "$2" || warn "$2 — missing: $1"; }

echo ""
echo "  Self-IP Agency Doctor"
echo "  Workspace: $WORKSPACE"
echo "  ────────────────────────────────────"
echo ""

# ── 1. System dependencies ──────────────────────────────────────────────────
echo "1. System dependencies"

if command -v python3 &>/dev/null; then
  PY_VER="$(python3 --version 2>&1)"
  ok "Python: $PY_VER"
else
  fail "Python 3 not found — required for all scripts"
fi

if command -v curl &>/dev/null; then
  ok "curl available"
else
  fail "curl not found — required for API calls and install"
fi

if python3 -c "import fastapi" 2>/dev/null; then
  ok "FastAPI installed (dashboard ready)"
else
  warn "FastAPI not installed — run: pip3 install -r dashboard/requirements.txt"
fi

if python3 -c "import requests" 2>/dev/null; then
  ok "requests installed"
else
  warn "requests not installed — run: pip3 install requests"
fi

echo ""

# ── 2. OpenClaw workspace ───────────────────────────────────────────────────
echo "2. OpenClaw workspace"
check_dir "$WORKSPACE" "workspace root exists"
check_dir "$WORKSPACE/runtime" "runtime/ directory"
check_dir "$WORKSPACE/runtime/main" "runtime/main/"
check_dir "$WORKSPACE/runtime/bookmarker" "runtime/bookmarker/"
check_dir "$WORKSPACE/runtime/trader" "runtime/trader/"
check_dir "$WORKSPACE/runtime/shared" "runtime/shared/"
check_dir "$WORKSPACE/wiki" "wiki/ directory"
check_dir "$WORKSPACE/scripts" "scripts/ directory"

echo ""

# ── 3. Credentials ──────────────────────────────────────────────────────────
echo "3. Credentials"
CREDS_FILE="$HOME/.config/tagclaw/credentials.json"
if [ -f "$CREDS_FILE" ]; then
  # Check that it has required keys but don't print values
  if python3 -c "
import json, sys
d = json.load(open('$CREDS_FILE'))
missing = [k for k in ('api_key', 'apiKey', 'privateKey', 'private_key') if d.get(k) is None]
keys = list(d.keys())
print('Keys: ' + ', '.join(keys))
has_api = bool(d.get('api_key') or d.get('apiKey'))
has_pk = bool(d.get('privateKey') or d.get('private_key'))
if not has_api:
    print('MISSING_API_KEY')
if not has_pk:
    print('MISSING_PRIVATE_KEY')
" 2>/dev/null | grep -q "MISSING_API_KEY"; then
    warn "credentials.json found but missing api_key"
  elif python3 -c "import json; d = json.load(open('$CREDS_FILE')); print('ok')" 2>/dev/null | grep -q "ok"; then
    ok "credentials.json exists and is valid JSON"
  fi
else
  fail "credentials.json not found — first read https://tagclaw.com/SKILL.md and join TagClaw, then run: cp $AGENCY_DIR/config/credentials.example.json $CREDS_FILE && edit it"
fi

echo ""

# ── 4. Runtime files (populated after first agent cycle) ────────────────────
echo "4. Runtime files (populated after first agent cycle)"
check_file_warn "$WORKSPACE/runtime/main/latest.json" "runtime/main/latest.json"
check_file_warn "$WORKSPACE/runtime/bookmarker/latest.json" "runtime/bookmarker/latest.json"
check_file_warn "$WORKSPACE/runtime/trader/latest.json" "runtime/trader/latest.json"
check_file_warn "$WORKSPACE/runtime/shared/wiki-lint-status.json" "runtime/shared/wiki-lint-status.json"
check_file_warn "$WORKSPACE/runtime/shared/community-heat.json" "runtime/shared/community-heat.json"

echo ""

# ── 4b. Dashboard-required artifacts (bootstrap vs missing) ─────────────────
echo "4b. Dashboard-required artifacts"

check_bootstrap_or_real() {
  local filepath="$1" label="$2"
  if [ ! -f "$filepath" ]; then
    warn "$label — missing (run install.sh to bootstrap)"
    return
  fi
  # Check if file has bootstrap marker
  if python3 -c "import json; d=json.load(open('$filepath')); exit(0 if d.get('bootstrap') else 1)" 2>/dev/null; then
    ok "$label (bootstrap — awaiting first cycle)"
  else
    ok "$label (populated)"
  fi
}

DASHBOARD_ARTIFACTS=(
  "shared/runtime-status.json"
  "main/runtime-health.json"
  "main/tas-latest.json"
  "main/last-decision.json"
  "main/social-intent.json"
  "bookmarker/topic-brief.json"
  "bookmarker/source-health.json"
  "bookmarker/content-candidates.json"
  "trader/wallet-snapshot.json"
  "trader/tas-trade.json"
  "trader/risk-status.json"
)

for art in "${DASHBOARD_ARTIFACTS[@]}"; do
  check_bootstrap_or_real "$WORKSPACE/runtime/$art" "runtime/$art"
done

echo ""

# ── 5. Wiki system ──────────────────────────────────────────────────────────
echo "5. Wiki system"
check_dir "$WORKSPACE/wiki/concepts" "wiki/concepts/ directory"
check_dir "$WORKSPACE/wiki/identity" "wiki/identity/ directory"
check_file_warn "$WORKSPACE/wiki/identity/persona.md" "wiki/identity/persona.md"
check_file_warn "$WORKSPACE/wiki/identity/key-positions.md" "wiki/identity/key-positions.md"
check_file_warn "$WORKSPACE/wiki/INDEX.md" "wiki/INDEX.md"

echo ""

# ── 6. Agency config ────────────────────────────────────────────────────────
echo "6. Agency config"
check_file "$AGENCY_DIR/config/agency-identity.json" "agency-identity.json"
check_file "$AGENCY_DIR/config/agency.config.yaml" "agency.config.yaml"
check_file "$AGENCY_DIR/config/wiki_topic_registry.json" "wiki_topic_registry.json"

echo ""

# ── 7. Cycle entrypoints ─────────────────────────────────────────────────────
echo "7. Cycle entrypoints"
check_file "$AGENCY_DIR/scripts/main-heartbeat.sh" "repo scripts/main-heartbeat.sh"
check_file "$AGENCY_DIR/scripts/bookmarker-cycle.sh" "repo scripts/bookmarker-cycle.sh"
check_file "$AGENCY_DIR/scripts/trader-cycle.sh" "repo scripts/trader-cycle.sh"
check_file "$AGENCY_DIR/HEARTBEAT.md" "repo HEARTBEAT.md (contract)"
check_file "$AGENCY_DIR/docs/main-heartbeat-contract.md" "docs/main-heartbeat-contract.md"

for cycle_script in main-heartbeat.sh bookmarker-cycle.sh trader-cycle.sh; do
  if [ -f "$AGENCY_DIR/scripts/$cycle_script" ]; then
    if [ -x "$AGENCY_DIR/scripts/$cycle_script" ]; then
      ok "repo $cycle_script is executable"
    else
      warn "repo $cycle_script is not executable — run: chmod +x scripts/$cycle_script"
    fi
  fi
  if [ -f "$WORKSPACE/scripts/$cycle_script" ]; then
    ok "deployed $cycle_script exists"
    if [ -x "$WORKSPACE/scripts/$cycle_script" ]; then
      ok "deployed $cycle_script is executable"
    else
      warn "deployed $cycle_script is not executable — rerun install or chmod +x"
    fi
  else
    warn "deployed $cycle_script not found — rerun install.sh"
  fi
done

check_file_warn "$WORKSPACE/HEARTBEAT.md" "workspace HEARTBEAT.md (deployed contract)"

echo ""

# ── 8. Deployment contract consistency ───────────────────────────────────────
echo "8. Deployment contract consistency"

# Check for stale task.json references in cron-jobs.json
if [ -f "$AGENCY_DIR/config/cron-jobs.json" ]; then
  if grep -q "runtime/bookmarker/task.json\|runtime/trader/task.json" "$AGENCY_DIR/config/cron-jobs.json"; then
    fail "cron-jobs.json still references runtime/*/task.json — these are not primary entrypoints"
  else
    ok "cron-jobs.json uses dedicated entrypoint scripts (no stale task.json refs)"
  fi
fi

# Check for stale references in openclaw-agents.yaml
if [ -f "$AGENCY_DIR/config/openclaw-agents.yaml" ]; then
  if grep -q "dev-claude.sh" "$AGENCY_DIR/config/openclaw-agents.yaml"; then
    warn "openclaw-agents.yaml still references dev-claude.sh — should use dedicated cycle scripts"
  else
    ok "openclaw-agents.yaml uses dedicated cycle scripts"
  fi
fi

# Check cron-jobs.json and openclaw-agents.yaml reference the same entrypoints
if [ -f "$AGENCY_DIR/config/cron-jobs.json" ]; then
  for agent_script in main-heartbeat.sh bookmarker-cycle.sh trader-cycle.sh; do
    if grep -q "$agent_script" "$AGENCY_DIR/config/cron-jobs.json"; then
      ok "cron-jobs.json references $agent_script"
    else
      warn "cron-jobs.json missing reference to $agent_script"
    fi
  done
fi

echo ""

# ── 9. Key scripts ──────────────────────────────────────────────────────────
echo "9. Key scripts"
for s in run_main_runtime_v2.py wiki_lint_v1.py select_strategy_v1.py \
          compute_tas_social_v2.py build_main_input_packet_v2.py \
          build_wiki_query_index_v1.py runtime_utils_v2.py; do
  check_file "$AGENCY_DIR/scripts/$s" "$s"
done

echo ""

# ── Summary ─────────────────────────────────────────────────────────────────
echo "  ────────────────────────────────────"
echo -e "  ${GREEN}PASS: $PASS${RESET}   ${YELLOW}WARN: $WARN${RESET}   ${RED}FAIL: $FAIL${RESET}"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "  Action required: fix the items above marked ✗ before running agents."
  if [ -f "$AGENCY_DIR/.install-next-steps.json" ]; then
    echo "  See also: .install-next-steps.json for machine-readable next steps"
    echo "            .install-next-steps.md  for human-readable next steps"
  fi
  exit 1
elif [ "$WARN" -gt 0 ]; then
  echo "  Warnings present — runtime files are populated after the first agent cycle."
  if [ -f "$AGENCY_DIR/.install-next-steps.json" ]; then
    echo "  See also: .install-next-steps.json / .install-next-steps.md for follow-up steps"
  fi
  exit 0
else
  echo "  All checks passed. Your runtime is ready."
  exit 0
fi

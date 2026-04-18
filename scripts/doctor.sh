#!/usr/bin/env bash
# doctor.sh — Self-IP Agency runtime health check
# Usage: bash scripts/doctor.sh [--workspace /path/to/workspace]
#
# Validates that the runtime is correctly configured and all required
# files/directories are in place. Run this after install.sh to verify.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

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

# ── 3. Credentials + TagClaw onboarding ────────────────────────────────────
echo "3. Credentials + TagClaw onboarding"
SKILL_ENV="$WORKSPACE/skills/tagclaw/.env"
WALLET_DIR="$WORKSPACE/skills/tagclaw-wallet"
WALLET_ENV="$WALLET_DIR/.env"

if [ -f "$WORKSPACE/scripts/tagclaw-onboard.sh" ] || [ -f "$AGENCY_DIR/scripts/tagclaw-onboard.sh" ]; then
  ok "tagclaw-onboard.sh available"
else
  warn "tagclaw-onboard.sh missing"
fi

if [ -f "$SKILL_ENV" ]; then
  if python3 -c "
import pathlib
path = pathlib.Path('$SKILL_ENV')
data = {}
for line in path.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s:
        continue
    k, v = s.split('=', 1)
    data[k.strip()] = v.strip().strip('\"').strip(\"'\")
print('ok' if data.get('TAGCLAW_API_KEY') else 'missing')
" 2>/dev/null | grep -q "ok"; then
    ok "skills/tagclaw/.env exists and contains TAGCLAW_API_KEY"
  else
    warn "skills/tagclaw/.env exists but TAGCLAW_API_KEY is missing"
  fi
else
  warn "skills/tagclaw/.env missing — run: bash $WORKSPACE/scripts/tagclaw-onboard.sh full --workspace $WORKSPACE"
fi

if [ -d "$WALLET_DIR/.git" ]; then
  ok "skills/tagclaw-wallet repo exists"
else
  warn "skills/tagclaw-wallet repo missing — run: bash $WORKSPACE/scripts/tagclaw-onboard.sh wallet-install --workspace $WORKSPACE"
fi

if [ -f "$WALLET_ENV" ]; then
  if python3 -c "
import pathlib
path = pathlib.Path('$WALLET_ENV')
required = ['TAGCLAW_ETH_ADDR','TAGCLAW_STEEM_POSTING_PUB','TAGCLAW_STEEM_POSTING_PRI','TAGCLAW_STEEM_OWNER','TAGCLAW_STEEM_ACTIVE','TAGCLAW_STEEM_MEMO']
data = {}
for line in path.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s:
        continue
    k, v = s.split('=', 1)
    data[k.strip()] = v.strip().strip('\"').strip(\"'\")
missing = [k for k in required if not data.get(k)]
print('ok' if not missing else 'missing')
" 2>/dev/null | grep -q "ok"; then
    ok "skills/tagclaw-wallet/.env exists and wallet prerequisites are initialized"
  else
    warn "skills/tagclaw-wallet/.env exists but wallet initialization is incomplete"
  fi
else
  warn "skills/tagclaw-wallet/.env missing — run: bash $WORKSPACE/scripts/tagclaw-onboard.sh wallet-init --workspace $WORKSPACE"
fi

if [ -f "$SKILL_ENV" ]; then
  ok "TagClaw API credentials are sourced from skills/tagclaw/.env only"
else
  warn "TagClaw API credentials missing — complete TagClaw onboarding first"
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

# ── 4c. Dashboard service (local + opt-in public) ───────────────────────────
echo "4c. Dashboard service"

DASHBOARD_SERVICE_SCRIPT="$AGENCY_DIR/scripts/dashboard-service.sh"
DASHBOARD_SERVICE_STATE="$WORKSPACE/runtime/shared/dashboard-service.json"

if [ -f "$DASHBOARD_SERVICE_SCRIPT" ]; then
  ok "scripts/dashboard-service.sh present (canonical owner)"
else
  fail "scripts/dashboard-service.sh missing — install.sh cannot delegate dashboard lifecycle"
fi

# Read dashboard.public.{suggest_in_install,auto_start} from agency.config.yaml.
# Both default to the safer value when the key is absent:
#   - suggest_in_install: true  (always suggest — default ON for new operators)
#   - auto_start:         false (never expose without explicit opt-in)
#   - enabled is a legacy alias for auto_start
PUBLIC_SUGGEST="true"
PUBLIC_AUTO_START="false"
if [ -f "$AGENCY_DIR/config/agency.config.yaml" ]; then
  read -r PUBLIC_SUGGEST PUBLIC_AUTO_START <<<"$(AGENCY_CONFIG="$AGENCY_DIR/config/agency.config.yaml" python3 -c "
import os, sys
try:
    import yaml
except ImportError:
    print('true false'); sys.exit(0)
try:
    with open(os.environ['AGENCY_CONFIG']) as f:
        d = yaml.safe_load(f) or {}
    pub = (d.get('dashboard') or {}).get('public') or {}
    suggest = pub.get('suggest_in_install', True)
    auto = pub.get('auto_start', pub.get('enabled', False))
    print(('true' if bool(suggest) else 'false') + ' ' + ('true' if bool(auto) else 'false'))
except Exception:
    print('true false')
" 2>/dev/null || echo "true false")"
fi

# Tri-state classification uses the guide-public subcommand as the source of truth
# when available; otherwise fall back to the raw state file.
if [ ! -f "$DASHBOARD_SERVICE_STATE" ]; then
  warn "runtime/shared/dashboard-service.json missing — run: bash scripts/dashboard-service.sh start-local"
else
  DASH_LOCAL_STATUS="$(python3 -c "import json; d=json.load(open('$DASHBOARD_SERVICE_STATE')); print((d.get('local') or {}).get('status',''))" 2>/dev/null || echo "")"
  DASH_LOCAL_PID="$(python3 -c "import json; d=json.load(open('$DASHBOARD_SERVICE_STATE')); print((d.get('local') or {}).get('pid','') or '')" 2>/dev/null || echo "")"
  DASH_PUBLIC_STATUS="$(python3 -c "import json; d=json.load(open('$DASHBOARD_SERVICE_STATE')); print((d.get('public') or {}).get('status',''))" 2>/dev/null || echo "")"
  DASH_PUBLIC_URL="$(python3 -c "import json; d=json.load(open('$DASHBOARD_SERVICE_STATE')); print((d.get('public') or {}).get('url','') or '')" 2>/dev/null || echo "")"
  DASH_PUBLIC_PID="$(python3 -c "import json; d=json.load(open('$DASHBOARD_SERVICE_STATE')); print((d.get('public') or {}).get('pid','') or '')" 2>/dev/null || echo "")"

  # Tri-state A: local not healthy → fix local first (don't distract with
  # public-exposure guidance until the underlying dashboard is up).
  case "$DASH_LOCAL_STATUS" in
    running)
      if [ -n "$DASH_LOCAL_PID" ] && kill -0 "$DASH_LOCAL_PID" 2>/dev/null; then
        ok "local dashboard running (pid $DASH_LOCAL_PID)"
      else
        warn "local dashboard state=running but PID $DASH_LOCAL_PID is not alive — run: bash scripts/dashboard-service.sh start-local"
      fi
      ;;
    stopped|"")
      warn "local dashboard stopped — run: bash scripts/dashboard-service.sh start-local"
      ;;
    failed|started_unverified|deps_missing)
      fail "local dashboard status=$DASH_LOCAL_STATUS — check logs/dashboard.log"
      ;;
    *)
      warn "local dashboard status=$DASH_LOCAL_STATUS"
      ;;
  esac

  if [ "$DASH_LOCAL_STATUS" != "running" ]; then
    # Don't escalate public-exposure guidance while the local dashboard
    # itself is broken — that's the first thing to fix.
    :
  elif [ "$DASH_PUBLIC_STATUS" = "running" ]; then
    # Tri-state C: public running. Verify the PID + surface the URL.
    if [ -n "$DASH_PUBLIC_PID" ] && kill -0 "$DASH_PUBLIC_PID" 2>/dev/null; then
      if [ -n "$DASH_PUBLIC_URL" ]; then
        ok "public dashboard tunnel running: $DASH_PUBLIC_URL"
      else
        warn "public dashboard tunnel running but URL not captured — check logs/dashboard-tunnel.log"
      fi
    else
      fail "public dashboard state=running but PID $DASH_PUBLIC_PID is not alive — run: bash scripts/dashboard-service.sh start-public"
    fi
  elif [ "$DASH_PUBLIC_STATUS" = "failed" ]; then
    fail "public dashboard tunnel failed — check logs/dashboard-tunnel.log (is cloudflared installed? $(cloudflared_install_hint))"
  else
    # Tri-state B: local healthy, public not started. Decision depends on
    # operator intent (auto_start vs suggest_in_install). In all opt-in cases
    # we print a concrete next-step command so the operator can act without
    # reading docs.
    GUIDE_START="bash $WORKSPACE/scripts/dashboard-service.sh start-public --workspace $WORKSPACE"
    if command -v cloudflared >/dev/null 2>&1; then
      CF_HINT=""
    else
      CF_HINT=" (install cloudflared first: $(cloudflared_install_hint))"
    fi

    if [ "$PUBLIC_AUTO_START" = "true" ]; then
      # Operator asked for auto-start but it's not running — treat as drift.
      fail "public dashboard auto_start=true but tunnel is not running — run: $GUIDE_START${CF_HINT}"
    elif [ "$PUBLIC_SUGGEST" = "true" ]; then
      info "public dashboard not started (optional). To expose a public URL, run: $GUIDE_START${CF_HINT}"
    else
      ok "public dashboard disabled (suggest_in_install=false in config/agency.config.yaml)"
    fi
  fi
fi

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

# ── 6b. Identity freshness (install-first / onboard-second stale-shadow check) ─
# If skills/tagclaw/.env holds real identity but agency-identity.json is still
# null-filled, the operator installed before onboarding and the identity JSON
# was never refreshed. Runtime reads workspace/config/agency-identity.json
# first, so a stale workspace copy shadows a correct repo copy and vice versa.
echo ""
echo "6b. Identity freshness"

check_identity_fresh() {
  local label="$1" identity_path="$2" env_path="$3"
  if [ ! -f "$identity_path" ]; then
    warn "$label: identity JSON missing ($identity_path)"
    return
  fi
  if [ ! -f "$env_path" ]; then
    # Without onboarded .env there's no real identity to check against.
    return
  fi
  local state
  state="$(IDENTITY_PATH="$identity_path" ENV_PATH="$env_path" python3 - <<'PY'
import json, os, pathlib

identity_path = pathlib.Path(os.environ["IDENTITY_PATH"])
env_path = pathlib.Path(os.environ["ENV_PATH"])

def parse_env(path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip(); v = v.strip().strip('"').strip("'")
        data[k] = v
    return data

env = parse_env(env_path)
env_username = env.get("TAGCLAW_AGENT_USERNAME") or ""
env_eth = env.get("TAGCLAW_ETH_ADDR") or ""

try:
    d = json.loads(identity_path.read_text())
except Exception:
    print("unreadable")
    raise SystemExit(0)

id_username = ((d.get("agent") or {}).get("username")) or ""
id_eth = ((d.get("agent") or {}).get("eth_addr")) or ""
id_wallet = ((d.get("wallet") or {}).get("address")) or ""

has_env_identity = bool(env_username) and bool(env_eth)
has_id_identity = bool(id_username) and bool(id_eth or id_wallet)

if has_env_identity and not has_id_identity:
    print("stale")
elif has_env_identity and has_id_identity:
    mismatch = (
        (env_username and env_username != id_username)
        or (env_eth and env_eth not in (id_eth, id_wallet))
    )
    print("mismatch" if mismatch else "fresh")
else:
    print("not-onboarded")
PY
  )"
  case "$state" in
    fresh)
      ok "$label is in sync with skills/tagclaw/.env"
      ;;
    stale)
      fail "$label is STALE (.env has real identity but JSON is null-filled) — run: bash scripts/refresh-agency-identity.sh --workspace $WORKSPACE"
      ;;
    mismatch)
      warn "$label disagrees with skills/tagclaw/.env — run: bash scripts/refresh-agency-identity.sh --workspace $WORKSPACE"
      ;;
    not-onboarded)
      # Nothing to verify yet — TagClaw onboarding still pending.
      :
      ;;
    unreadable)
      fail "$label exists but cannot be parsed as JSON"
      ;;
  esac
}

SKILL_ENV_FOR_ID="$WORKSPACE/skills/tagclaw/.env"
check_identity_fresh "repo config/agency-identity.json" "$AGENCY_DIR/config/agency-identity.json" "$SKILL_ENV_FOR_ID"
check_identity_fresh "workspace config/agency-identity.json" "$WORKSPACE/config/agency-identity.json" "$SKILL_ENV_FOR_ID"

if [ -f "$AGENCY_DIR/scripts/refresh-agency-identity.sh" ] || [ -f "$WORKSPACE/scripts/refresh-agency-identity.sh" ]; then
  ok "refresh-agency-identity.sh helper present"
else
  warn "refresh-agency-identity.sh helper missing — rerun install.sh"
fi

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

# ── 8. Execution backend availability ────────────────────────────────────────
echo "8. Execution backend availability"

# Main heartbeat uses Python scripts directly — always available if python3 is present
if command -v python3 &>/dev/null; then
  ok "main-heartbeat: Python runtime available"
else
  fail "main-heartbeat: Python 3 not found — heartbeat cannot run"
fi

# Bookmarker execution backend
BOOKMARKER_BACKEND="none"
if [ -f "$WORKSPACE/scripts/run_bookmarker_runtime_v1.py" ]; then
  ok "bookmarker: native runtime (run_bookmarker_runtime_v1.py)"
  BOOKMARKER_BACKEND="native"
elif [ -f "$AGENCY_DIR/scripts/run_bookmarker_runtime_v1.py" ]; then
  ok "bookmarker: native runtime in repo (run install.sh to deploy)"
  BOOKMARKER_BACKEND="native-repo"
elif [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
  ok "bookmarker: dev-claude.sh (LLM execution path)"
  BOOKMARKER_BACKEND="dev-claude"
elif command -v claude &>/dev/null; then
  ok "bookmarker: claude CLI (LLM execution path)"
  BOOKMARKER_BACKEND="claude-cli"
else
  fail "bookmarker: NO execution backend — bookmarker-cycle.sh will fail"
fi

# Trader execution backend
TRADER_BACKEND="none"
if [ -f "$WORKSPACE/scripts/run_trader_runtime_v1.py" ]; then
  ok "trader: native runtime (run_trader_runtime_v1.py)"
  TRADER_BACKEND="native"
elif [ -f "$AGENCY_DIR/scripts/run_trader_runtime_v1.py" ]; then
  ok "trader: native runtime in repo (run install.sh to deploy)"
  TRADER_BACKEND="native-repo"
elif [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
  ok "trader: dev-claude.sh (LLM execution path)"
  TRADER_BACKEND="dev-claude"
elif command -v claude &>/dev/null; then
  ok "trader: claude CLI (LLM execution path)"
  TRADER_BACKEND="claude-cli"
else
  fail "trader: NO execution backend — trader-cycle.sh will fail"
fi

# Workspace-local assets check (deployed scripts need these)
if [ -f "$WORKSPACE/.agency-meta.json" ]; then
  ok "workspace .agency-meta.json present"
else
  warn "workspace .agency-meta.json missing — run install.sh to deploy"
fi

if [ -f "$WORKSPACE/.agency-installed" ]; then
  ok "workspace .agency-installed marker present"
else
  warn "workspace .agency-installed marker missing — deployed scripts may fail install check"
fi

for agent_file in main bookmarker trader; do
  if [ -f "$WORKSPACE/agents/${agent_file}.md" ]; then
    ok "workspace agents/${agent_file}.md deployed"
  else
    warn "workspace agents/${agent_file}.md missing — run install.sh"
  fi
done

echo ""

# ── 9. Deployment contract consistency ───────────────────────────────────────
echo "9. Deployment contract consistency"

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

# ── 10. Key scripts ─────────────────────────────────────────────────────────
echo "10. Key scripts"
for s in run_main_runtime_v2.py wiki_lint_v1.py select_strategy_v1.py \
          compute_tas_social_v2.py build_main_input_packet_v2.py \
          build_wiki_query_index_v1.py runtime_utils_v2.py \
          run_bookmarker_runtime_v1.py run_trader_runtime_v1.py; do
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
  echo "  Warnings present — runtime may still be awaiting first cycle, or TagClaw onboarding may be incomplete."
  if [ -f "$AGENCY_DIR/.install-next-steps.json" ]; then
    echo "  See also: .install-next-steps.json / .install-next-steps.md for follow-up steps"
  fi
  exit 0
else
  echo "  All checks passed. Your runtime is ready."
  exit 0
fi

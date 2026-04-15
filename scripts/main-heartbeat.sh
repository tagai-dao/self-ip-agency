#!/usr/bin/env bash
# main-heartbeat.sh — Dedicated main-heartbeat entrypoint for self-ip-agency
#
# This is the RECOMMENDED first-run self-check and recurring heartbeat script.
# It replaces the legacy pattern of "Read runtime/main/task.json" which does not
# exist and causes external agents to fall back to generic/idle behavior.
#
# What this script does:
#   1. Validates the runtime environment (installed, identity, credentials)
#   2. Builds the main input packet (TAS scores, strategy, wiki status)
#   3. Runs the main runtime orchestrator (dispatch bookmarker/trader if needed)
#   4. Writes heartbeat.json and status.json to runtime/main/
#
# Usage:
#   bash scripts/main-heartbeat.sh                    # normal heartbeat
#   bash scripts/main-heartbeat.sh --self-check       # first-run validation only
#   bash scripts/main-heartbeat.sh --dry-run          # show what would run
#
# Source of truth: See HEARTBEAT.md (repo root) and docs/main-heartbeat-contract.md
# NOTE: runtime/main/task.json is NOT the primary task queue. See contract docs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
AGENCY_VERSION="$(cat "$AGENCY_DIR/VERSION" 2>/dev/null || echo "unknown")"
WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
RUNTIME_MAIN="$WORKSPACE/runtime/main"

# ── Color helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
log_ok()   { echo -e "${GREEN}[OK]${RESET} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; }
log_fail() { echo -e "${RED}[FAIL]${RESET} $1"; }
log_info() { echo -e "[INFO] $1"; }

# ── Parse args ───────────────────────────────────────────────────────────────
MODE="heartbeat"
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --self-check) MODE="self-check" ;;
    --dry-run)    DRY_RUN=true ;;
    *)            log_warn "Unknown argument: $arg" ;;
  esac
done

# ── Phase 1: Environment validation (always runs) ───────────────────────────

validate_environment() {
  local errors=0

  log_info "Validating main-heartbeat environment (v$AGENCY_VERSION)..."

  # 1. Check .installed marker
  if [ -f "$AGENCY_DIR/.installed" ]; then
    log_ok "Agency installed"
  else
    log_fail "Agency not installed — run: bash scripts/install.sh"
    errors=$((errors + 1))
  fi

  # 2. Check runtime/main directory
  if [ -d "$RUNTIME_MAIN" ]; then
    log_ok "runtime/main/ exists at $RUNTIME_MAIN"
  else
    log_fail "runtime/main/ not found at $RUNTIME_MAIN"
    errors=$((errors + 1))
  fi

  # 3. Check identity
  local identity_file="$AGENCY_DIR/config/agency-identity.json"
  if [ -f "$identity_file" ]; then
    local username
    username="$(python3 -c "import json; d=json.load(open('$identity_file')); print(d.get('agent',{}).get('username',''))" 2>/dev/null || echo "")"
    if [ -n "$username" ]; then
      log_ok "Identity resolved: $username"
    else
      log_warn "Identity file exists but username is empty"
    fi
  else
    log_warn "Identity file not found — run install.sh with TagClaw API access"
  fi

  # 4. Check credentials
  if [ -f "$HOME/.config/tagclaw/credentials.json" ]; then
    log_ok "Credentials file exists"
  else
    log_warn "Credentials not configured — see docs/deployment-guide.md"
  fi

  # 5. Check heartbeat template
  if [ -f "$RUNTIME_MAIN/heartbeat.json" ]; then
    log_ok "runtime/main/heartbeat.json exists"
  else
    log_warn "runtime/main/heartbeat.json not found (will be created on first cycle)"
  fi

  return $errors
}

# ── Phase 2: Build input packet ─────────────────────────────────────────────

build_input_packet() {
  log_info "Building main input packet..."

  if [ -f "$AGENCY_DIR/scripts/build_main_input_packet_v2.py" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      log_info "[DRY RUN] Would run: python3 scripts/build_main_input_packet_v2.py"
    else
      python3 "$AGENCY_DIR/scripts/build_main_input_packet_v2.py" 2>&1 || {
        log_warn "Input packet build failed — continuing with stale data"
      }
    fi
  else
    log_warn "build_main_input_packet_v2.py not found — skipping input packet"
  fi
}

# ── Phase 3: Run main runtime ───────────────────────────────────────────────

run_main_runtime() {
  log_info "Running main runtime orchestrator..."

  if [ -f "$AGENCY_DIR/scripts/run_main_runtime_v2.py" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      log_info "[DRY RUN] Would run: python3 scripts/run_main_runtime_v2.py"
    else
      python3 "$AGENCY_DIR/scripts/run_main_runtime_v2.py" 2>&1 || {
        log_fail "Main runtime failed"
        return 1
      }
    fi
  else
    log_warn "run_main_runtime_v2.py not found — writing minimal heartbeat"
    write_minimal_heartbeat
  fi
}

# ── Fallback: write minimal heartbeat ────────────────────────────────────────

write_minimal_heartbeat() {
  mkdir -p "$RUNTIME_MAIN"
  local RUNTIME_SHARED="$WORKSPACE/runtime/shared"
  mkdir -p "$RUNTIME_SHARED"
  python3 -c "
import json
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
ts = now.isoformat()

hb = {
    'heartbeat_id': 'hb-' + now.strftime('%Y%m%d%H%M%S'),
    'timestamp': ts,
    'mode': 'self-check',
    'source': 'main-heartbeat.sh',
    'tas_score': 0.0,
    'tas_social': 0.0,
    'tas_trade': 0.0,
    'bookmarker_status': 'unknown',
    'trader_status': 'unknown',
    'alerts': [],
    'schema': 'main.heartbeat.v1'
}
with open('$RUNTIME_MAIN/heartbeat.json', 'w') as f:
    json.dump(hb, f, indent=2)
print('Wrote heartbeat.json')

# Also write latest.json so dashboard agent pill shows a timestamp
latest = {
    'schema': 'main.latest.v1',
    'generated_at': ts,
    'status': 'self-check',
    'source': 'main-heartbeat.sh'
}
with open('$RUNTIME_MAIN/latest.json', 'w') as f:
    json.dump(latest, f, indent=2)
print('Wrote latest.json')

# Update shared runtime-status with main heartbeat timestamp
import os
rs_path = '$RUNTIME_SHARED/runtime-status.json'
try:
    rs = json.load(open(rs_path))
except Exception:
    rs = {}
rs.setdefault('schema', 'runtime-status.v1')
rs['main'] = {'status': 'self-check', 'updated_at': ts, 'last_heartbeat': ts}
rs.pop('bootstrap', None)
with open(rs_path, 'w') as f:
    json.dump(rs, f, indent=2)
print('Updated runtime-status.json')
" 2>&1
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
  echo ""
  echo "  ┌──────────────────────────────────────┐"
  echo "  │  Main Heartbeat — v$AGENCY_VERSION"
  echo "  │  Mode: $MODE"
  echo "  └──────────────────────────────────────┘"
  echo ""

  # Always validate
  validate_environment || {
    if [ "$MODE" = "self-check" ]; then
      log_fail "Self-check failed — fix errors above before running heartbeat"
      exit 1
    fi
    log_warn "Environment issues detected — attempting heartbeat anyway"
  }

  # Self-check mode stops after validation
  if [ "$MODE" = "self-check" ]; then
    log_ok "Self-check complete"
    echo ""
    echo "### BEGIN HEARTBEAT CONTRACT ###"
    echo "HEARTBEAT_MODE=\"self-check\""
    echo "HEARTBEAT_SOURCE=\"scripts/main-heartbeat.sh\""
    echo "HEARTBEAT_STATUS=\"validated\""
    echo "TASK_JSON_IS_PRIMARY=\"false\""
    echo "### END HEARTBEAT CONTRACT ###"
    exit 0
  fi

  # Full heartbeat cycle
  build_input_packet
  run_main_runtime

  log_ok "Main heartbeat cycle complete"

  echo ""
  echo "### BEGIN HEARTBEAT CONTRACT ###"
  echo "HEARTBEAT_MODE=\"heartbeat\""
  echo "HEARTBEAT_SOURCE=\"scripts/main-heartbeat.sh\""
  echo "HEARTBEAT_STATUS=\"completed\""
  echo "TASK_JSON_IS_PRIMARY=\"false\""
  echo "### END HEARTBEAT CONTRACT ###"
}

main "$@"

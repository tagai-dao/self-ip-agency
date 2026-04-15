#!/usr/bin/env bash
# trader-cycle.sh — Dedicated trader cycle entrypoint for self-ip-agency
#
# This is the RECOMMENDED entrypoint for the trader on-chain operations cycle.
# It replaces the legacy pattern of "Read runtime/trader/task.json" which
# does not exist in runtime-template and causes broken deployment contracts.
#
# What this script does:
#   1. Validates the runtime environment
#   2. Runs the trader on-chain operations cycle via dev-claude.sh
#   3. Writes cycle status to runtime/trader/
#
# Usage:
#   bash scripts/trader-cycle.sh                # run trader cycle
#   bash scripts/trader-cycle.sh --self-check   # validate environment only
#   bash scripts/trader-cycle.sh --dry-run      # show what would run
#
# NOTE: runtime/trader/task.json is NOT the primary entrypoint. See docs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared library (works from both repo and deployed workspace)
if [ -f "$SCRIPT_DIR/lib/common.sh" ]; then
  source "$SCRIPT_DIR/lib/common.sh"
else
  echo "[FATAL] lib/common.sh not found at $SCRIPT_DIR/lib/" >&2
  exit 1
fi

# Resolve REPO_DIR, WORKSPACE, AGENCY_VERSION from context
resolve_agency_paths "$SCRIPT_DIR"
RUNTIME_TRADER="$WORKSPACE/runtime/trader"

# ── Color helpers (override common.sh log format for cycle output) ───────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
log_ok()   { echo -e "${GREEN}[OK]${RESET} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; }
log_fail() { echo -e "${RED}[FAIL]${RESET} $1"; }
log_info() { echo -e "[INFO] $1"; }

# ── Parse args ───────────────────────────────────────────────────────────────
MODE="cycle"
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --self-check) MODE="self-check" ;;
    --dry-run)    DRY_RUN=true ;;
    *)            log_warn "Unknown argument: $arg" ;;
  esac
done

# ── Phase 1: Environment validation ─────────────────────────────────────────

validate_environment() {
  local errors=0

  log_info "Validating trader-cycle environment (v$AGENCY_VERSION)..."

  # 1. Check .installed marker (workspace or repo)
  if check_agency_installed; then
    log_ok "Agency installed"
  else
    log_fail "Agency not installed — run: bash scripts/install.sh"
    errors=$((errors + 1))
  fi

  # 2. Check runtime/trader directory
  if [ -d "$RUNTIME_TRADER" ]; then
    log_ok "runtime/trader/ exists at $RUNTIME_TRADER"
  else
    log_fail "runtime/trader/ not found at $RUNTIME_TRADER"
    errors=$((errors + 1))
  fi

  # 3. Check execution backend (native runtime preferred, claude optional)
  if [ -f "$SCRIPT_DIR/run_trader_runtime_v1.py" ]; then
    log_ok "Native trader runtime available"
  elif [ -f "$WORKSPACE/scripts/run_trader_runtime_v1.py" ]; then
    log_ok "Native trader runtime available (workspace)"
  elif [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    log_ok "dev-claude.sh available (LLM execution path)"
  elif command -v claude &>/dev/null; then
    log_ok "Claude CLI available (LLM execution path)"
  else
    log_fail "No execution backend found — need run_trader_runtime_v1.py, dev-claude.sh, or claude CLI"
    errors=$((errors + 1))
  fi

  # 4. Check credentials (trader needs wallet access)
  if [ -f "$HOME/.config/tagclaw/credentials.json" ]; then
    log_ok "Credentials file exists (wallet access)"
  else
    log_warn "Credentials not configured — on-chain operations will fail"
  fi

  # 5. Check behavior file (workspace or repo)
  local behavior_file
  behavior_file="$(resolve_agent_file "trader" 2>/dev/null || echo "")"
  if [ -n "$behavior_file" ]; then
    log_ok "Trader behavior file: $behavior_file"
  else
    log_warn "Trader behavior file not found"
  fi

  return $errors
}

# ── Phase 2: Run trader cycle ────────────────────────────────────────────────

run_trader_cycle() {
  log_info "Running trader on-chain operations cycle..."

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would run trader cycle"
    return 0
  fi

  # Priority 1: Native Python runtime (no LLM dependency)
  local native_runtime=""
  if [ -f "$SCRIPT_DIR/run_trader_runtime_v1.py" ]; then
    native_runtime="$SCRIPT_DIR/run_trader_runtime_v1.py"
  elif [ -f "$WORKSPACE/scripts/run_trader_runtime_v1.py" ]; then
    native_runtime="$WORKSPACE/scripts/run_trader_runtime_v1.py"
  elif [ -n "${REPO_DIR:-}" ] && [ -f "$REPO_DIR/scripts/run_trader_runtime_v1.py" ]; then
    native_runtime="$REPO_DIR/scripts/run_trader_runtime_v1.py"
  fi

  if [ -n "$native_runtime" ]; then
    log_info "Using native runtime: $native_runtime"
    cd "$WORKSPACE" && python3 "$native_runtime" 2>&1 || {
      log_fail "Trader cycle failed via native runtime"
      return 1
    }
    return 0
  fi

  # Priority 2: LLM execution (dev-claude.sh or claude CLI)
  local prompt="Execute trade cycle for the trader agent. Read agents/trader.md for behavior rules. Evaluate signals, execute trades if warranted. Write results to runtime/trader/result.json and update runtime/trader/latest.json."

  if [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    log_info "Using dev-claude.sh execution path"
    cd "$WORKSPACE" && ./scripts/dev-claude.sh "$prompt" 2>&1 || {
      log_fail "Trader cycle failed via dev-claude.sh"
      return 1
    }
  elif command -v claude &>/dev/null; then
    log_info "Using claude CLI execution path"
    cd "$WORKSPACE" && claude --print "$prompt" 2>&1 || {
      log_fail "Trader cycle failed via claude CLI"
      return 1
    }
  else
    log_fail "No execution backend available — install run_trader_runtime_v1.py or ensure claude CLI is available"
    return 1
  fi
}

# ── Runtime status update ─────────────────────────────────────────────────────

_update_runtime_status() {
  local agent="$1" status="$2"
  local rs_path="$WORKSPACE/runtime/shared/runtime-status.json"
  mkdir -p "$(dirname "$rs_path")"
  python3 -c "
import json
from datetime import datetime, timezone
ts = datetime.now(timezone.utc).isoformat()
try:
    rs = json.load(open('$rs_path'))
except Exception:
    rs = {}
rs.setdefault('schema', 'runtime-status.v1')
rs['$agent'] = {'status': '$status', 'updated_at': ts}
rs.pop('bootstrap', None)
with open('$rs_path', 'w') as f:
    json.dump(rs, f, indent=2)
" 2>/dev/null || true
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
  echo ""
  echo "  ┌──────────────────────────────────────┐"
  echo "  │  Trader Cycle — v$AGENCY_VERSION"
  echo "  │  Mode: $MODE"
  echo "  └──────────────────────────────────────┘"
  echo ""

  # Always validate
  validate_environment || {
    if [ "$MODE" = "self-check" ]; then
      log_fail "Self-check failed — fix errors above before running trader cycle"
      exit 1
    fi
    log_warn "Environment issues detected — attempting cycle anyway"
  }

  # Self-check mode stops after validation
  if [ "$MODE" = "self-check" ]; then
    log_ok "Self-check complete"
    echo ""
    echo "### BEGIN CYCLE CONTRACT ###"
    echo "CYCLE_AGENT=\"trader\""
    echo "CYCLE_MODE=\"self-check\""
    echo "CYCLE_SOURCE=\"scripts/trader-cycle.sh\""
    echo "CYCLE_STATUS=\"validated\""
    echo "TASK_JSON_IS_PRIMARY=\"false\""
    echo "### END CYCLE CONTRACT ###"
    exit 0
  fi

  # Full trader cycle
  run_trader_cycle

  # Update shared runtime-status with trader timestamp
  _update_runtime_status "trader" "completed"

  log_ok "Trader cycle complete"

  echo ""
  echo "### BEGIN CYCLE CONTRACT ###"
  echo "CYCLE_AGENT=\"trader\""
  echo "CYCLE_MODE=\"cycle\""
  echo "CYCLE_SOURCE=\"scripts/trader-cycle.sh\""
  echo "CYCLE_STATUS=\"completed\""
  echo "TASK_JSON_IS_PRIMARY=\"false\""
  echo "### END CYCLE CONTRACT ###"
}

main "$@"

#!/usr/bin/env bash
# bookmarker-cycle.sh — Dedicated bookmarker cycle entrypoint for self-ip-agency
#
# This is the RECOMMENDED entrypoint for the bookmarker social curation cycle.
# It replaces the legacy pattern of "Read runtime/bookmarker/task.json" which
# does not exist in runtime-template and causes broken deployment contracts.
#
# What this script does:
#   1. Validates the runtime environment
#   2. Runs the bookmarker social curation cycle via dev-claude.sh
#   3. Writes cycle status to runtime/bookmarker/
#
# Usage:
#   bash scripts/bookmarker-cycle.sh                # run bookmarker cycle
#   bash scripts/bookmarker-cycle.sh --self-check   # validate environment only
#   bash scripts/bookmarker-cycle.sh --dry-run      # show what would run
#
# NOTE: runtime/bookmarker/task.json is NOT the primary entrypoint. See docs.

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
RUNTIME_BOOKMARKER="$WORKSPACE/runtime/bookmarker"

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

  log_info "Validating bookmarker-cycle environment (v$AGENCY_VERSION)..."

  # 1. Check .installed marker (workspace or repo)
  if check_agency_installed; then
    log_ok "Agency installed"
  else
    log_fail "Agency not installed — run: bash scripts/install.sh"
    errors=$((errors + 1))
  fi

  # 2. Check runtime/bookmarker directory
  if [ -d "$RUNTIME_BOOKMARKER" ]; then
    log_ok "runtime/bookmarker/ exists at $RUNTIME_BOOKMARKER"
  else
    log_fail "runtime/bookmarker/ not found at $RUNTIME_BOOKMARKER"
    errors=$((errors + 1))
  fi

  # 3. Check execution backend (native runtime preferred, claude optional)
  if [ -f "$SCRIPT_DIR/run_bookmarker_runtime.py" ]; then
    log_ok "Native bookmarker runtime available"
  elif [ -f "$WORKSPACE/scripts/run_bookmarker_runtime.py" ]; then
    log_ok "Native bookmarker runtime available (workspace)"
  elif [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    log_ok "dev-claude.sh available (LLM execution path)"
  elif command -v claude &>/dev/null; then
    log_ok "Claude CLI available (LLM execution path)"
  else
    log_fail "No execution backend found — need run_bookmarker_runtime.py, dev-claude.sh, or claude CLI"
    errors=$((errors + 1))
  fi

  # 4. Check behavior file (workspace or repo)
  local behavior_file
  behavior_file="$(resolve_agent_file "bookmarker" 2>/dev/null || echo "")"
  if [ -n "$behavior_file" ]; then
    log_ok "Bookmarker behavior file: $behavior_file"
  else
    log_warn "Bookmarker behavior file not found"
  fi

  return $errors
}

# ── Phase 2: Run bookmarker cycle ────────────────────────────────────────────

run_bookmarker_cycle() {
  log_info "Running bookmarker social curation cycle..."

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would run bookmarker cycle"
    return 0
  fi

  # Priority 1: Native Python runtime (no LLM dependency)
  local native_runtime=""
  if [ -f "$SCRIPT_DIR/run_bookmarker_runtime.py" ]; then
    native_runtime="$SCRIPT_DIR/run_bookmarker_runtime.py"
  elif [ -f "$WORKSPACE/scripts/run_bookmarker_runtime.py" ]; then
    native_runtime="$WORKSPACE/scripts/run_bookmarker_runtime.py"
  elif [ -n "${REPO_DIR:-}" ] && [ -f "$REPO_DIR/scripts/run_bookmarker_runtime.py" ]; then
    native_runtime="$REPO_DIR/scripts/run_bookmarker_runtime.py"
  fi

  if [ -n "$native_runtime" ]; then
    log_info "Using native runtime: $native_runtime"
    cd "$WORKSPACE" && python3 "$native_runtime" 2>&1 || {
      log_fail "Bookmarker cycle failed via native runtime"
      return 1
    }
    return 0
  fi

  # Priority 2: LLM execution (dev-claude.sh or claude CLI)
  local prompt="Execute social curation cycle for the bookmarker agent. Read agents/bookmarker.md for behavior rules. Write results to runtime/bookmarker/result.json and update runtime/bookmarker/latest.json."

  if [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    log_info "Using dev-claude.sh execution path"
    cd "$WORKSPACE" && ./scripts/dev-claude.sh "$prompt" 2>&1 || {
      log_fail "Bookmarker cycle failed via dev-claude.sh"
      return 1
    }
  elif command -v claude &>/dev/null; then
    log_info "Using claude CLI execution path"
    cd "$WORKSPACE" && claude --print "$prompt" 2>&1 || {
      log_fail "Bookmarker cycle failed via claude CLI"
      return 1
    }
  else
    log_fail "No execution backend available — install run_bookmarker_runtime.py or ensure claude CLI is available"
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
  echo "  │  Bookmarker Cycle — v$AGENCY_VERSION"
  echo "  │  Mode: $MODE"
  echo "  └──────────────────────────────────────┘"
  echo ""

  # Always validate
  validate_environment || {
    if [ "$MODE" = "self-check" ]; then
      log_fail "Self-check failed — fix errors above before running bookmarker cycle"
      exit 1
    fi
    log_warn "Environment issues detected — attempting cycle anyway"
  }

  # Self-check mode stops after validation
  if [ "$MODE" = "self-check" ]; then
    log_ok "Self-check complete"
    echo ""
    echo "### BEGIN CYCLE CONTRACT ###"
    echo "CYCLE_AGENT=\"bookmarker\""
    echo "CYCLE_MODE=\"self-check\""
    echo "CYCLE_SOURCE=\"scripts/bookmarker-cycle.sh\""
    echo "CYCLE_STATUS=\"validated\""
    echo "TASK_JSON_IS_PRIMARY=\"false\""
    echo "### END CYCLE CONTRACT ###"
    exit 0
  fi

  # Full bookmarker cycle
  run_bookmarker_cycle

  # Update shared runtime-status with bookmarker timestamp
  _update_runtime_status "bookmarker" "completed"

  log_ok "Bookmarker cycle complete"

  echo ""
  echo "### BEGIN CYCLE CONTRACT ###"
  echo "CYCLE_AGENT=\"bookmarker\""
  echo "CYCLE_MODE=\"cycle\""
  echo "CYCLE_SOURCE=\"scripts/bookmarker-cycle.sh\""
  echo "CYCLE_STATUS=\"completed\""
  echo "TASK_JSON_IS_PRIMARY=\"false\""
  echo "### END CYCLE CONTRACT ###"
}

main "$@"

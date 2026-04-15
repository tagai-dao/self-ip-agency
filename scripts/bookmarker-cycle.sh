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
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
AGENCY_VERSION="$(cat "$AGENCY_DIR/VERSION" 2>/dev/null || echo "unknown")"
WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
RUNTIME_BOOKMARKER="$WORKSPACE/runtime/bookmarker"

# ── Color helpers ────────────────────────────────────────────────────────────
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

  # 1. Check .installed marker
  if [ -f "$AGENCY_DIR/.installed" ]; then
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

  # 3. Check dev-claude.sh availability
  if [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    log_ok "dev-claude.sh available in workspace"
  elif command -v claude &>/dev/null; then
    log_ok "Claude CLI available (will use directly)"
  else
    log_fail "Neither dev-claude.sh nor claude CLI found — cannot run bookmarker cycle"
    errors=$((errors + 1))
  fi

  # 4. Check behavior file
  if [ -f "$AGENCY_DIR/agents/bookmarker.md" ] || [ -f "$AGENCY_DIR/agents/bookmarker.md.tmpl" ]; then
    log_ok "Bookmarker behavior file exists"
  else
    log_warn "Bookmarker behavior file not found"
  fi

  return $errors
}

# ── Phase 2: Run bookmarker cycle ────────────────────────────────────────────

run_bookmarker_cycle() {
  log_info "Running bookmarker social curation cycle..."

  local prompt="Execute social curation cycle for the bookmarker agent. Read agents/bookmarker.md for behavior rules. Write results to runtime/bookmarker/result.json and update runtime/bookmarker/latest.json."

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would run bookmarker cycle with prompt:"
    log_info "  $prompt"
    return 0
  fi

  # Prefer dev-claude.sh if available, fall back to claude CLI
  if [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    cd "$WORKSPACE" && ./scripts/dev-claude.sh "$prompt" 2>&1 || {
      log_fail "Bookmarker cycle failed via dev-claude.sh"
      return 1
    }
  elif command -v claude &>/dev/null; then
    cd "$WORKSPACE" && claude --print "$prompt" 2>&1 || {
      log_fail "Bookmarker cycle failed via claude CLI"
      return 1
    }
  else
    log_fail "No execution method available for bookmarker cycle"
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

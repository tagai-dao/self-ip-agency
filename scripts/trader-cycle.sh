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
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
AGENCY_VERSION="$(cat "$AGENCY_DIR/VERSION" 2>/dev/null || echo "unknown")"
WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
RUNTIME_TRADER="$WORKSPACE/runtime/trader"

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

  log_info "Validating trader-cycle environment (v$AGENCY_VERSION)..."

  # 1. Check .installed marker
  if [ -f "$AGENCY_DIR/.installed" ]; then
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

  # 3. Check dev-claude.sh availability
  if [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    log_ok "dev-claude.sh available in workspace"
  elif command -v claude &>/dev/null; then
    log_ok "Claude CLI available (will use directly)"
  else
    log_fail "Neither dev-claude.sh nor claude CLI found — cannot run trader cycle"
    errors=$((errors + 1))
  fi

  # 4. Check credentials (trader needs wallet access)
  if [ -f "$HOME/.config/tagclaw/credentials.json" ]; then
    log_ok "Credentials file exists (wallet access)"
  else
    log_warn "Credentials not configured — on-chain operations will fail"
  fi

  # 5. Check behavior file
  if [ -f "$AGENCY_DIR/agents/trader.md" ] || [ -f "$AGENCY_DIR/agents/trader.md.tmpl" ]; then
    log_ok "Trader behavior file exists"
  else
    log_warn "Trader behavior file not found"
  fi

  return $errors
}

# ── Phase 2: Run trader cycle ────────────────────────────────────────────────

run_trader_cycle() {
  log_info "Running trader on-chain operations cycle..."

  local prompt="Execute trade cycle for the trader agent. Read agents/trader.md for behavior rules. Evaluate signals, execute trades if warranted. Write results to runtime/trader/result.json and update runtime/trader/latest.json."

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would run trader cycle with prompt:"
    log_info "  $prompt"
    return 0
  fi

  # Prefer dev-claude.sh if available, fall back to claude CLI
  if [ -f "$WORKSPACE/scripts/dev-claude.sh" ]; then
    cd "$WORKSPACE" && ./scripts/dev-claude.sh "$prompt" 2>&1 || {
      log_fail "Trader cycle failed via dev-claude.sh"
      return 1
    }
  elif command -v claude &>/dev/null; then
    cd "$WORKSPACE" && claude --print "$prompt" 2>&1 || {
      log_fail "Trader cycle failed via claude CLI"
      return 1
    }
  else
    log_fail "No execution method available for trader cycle"
    return 1
  fi
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

#!/usr/bin/env bash
# uninstall.sh — Self-IP Agency removal script
# Usage: bash scripts/uninstall.sh [--force]
#
# Removes:
#   - Registered cron jobs (main-heartbeat, bookmarker-cycle, trader-cycle)
#   - Runtime directory placeholder files (NOT your live runtime data by default)
#   - Dashboard installation from workspace/tools/self-ip-dashboard
#   - .installed marker
#
# Does NOT remove:
#   - Generated agent .md files (agents/main.md etc.) — those are your config
#   - config/agency-identity.json — preserves your identity
#   - Any runtime data in workspace/runtime/ — preserves your data
#   - Unless --force is passed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

FORCE=false

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    *) log_warn "Unknown argument: $arg" ;;
  esac
done

# ── Detect workspace ──────────────────────────────────────────────────────────

WORKSPACE="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
log_info "OpenClaw workspace: $WORKSPACE"

# ── Remove cron jobs ──────────────────────────────────────────────────────────

log_info "Removing cron jobs..."

for job_name in main-heartbeat bookmarker-cycle trader-cycle; do
  if command -v openclaw &>/dev/null; then
    if openclaw cron remove "$job_name" 2>/dev/null; then
      log_ok "Removed cron: $job_name"
    else
      log_warn "Cron not found or already removed: $job_name"
    fi
  else
    log_warn "openclaw CLI not found — remove crons manually:"
    echo "  openclaw cron remove $job_name"
  fi
done

# ── Remove dashboard ──────────────────────────────────────────────────────────

DASHBOARD_DIR="$WORKSPACE/tools/self-ip-dashboard"
if [ -d "$DASHBOARD_DIR" ]; then
  # Stop running dashboard if any
  local_pid="$(pgrep -f "server.py.*8765" 2>/dev/null || echo "")"
  if [ -n "$local_pid" ]; then
    kill "$local_pid" 2>/dev/null && log_ok "Stopped dashboard server (PID: $local_pid)"
  fi
  rm -rf "$DASHBOARD_DIR"
  log_ok "Removed dashboard: $DASHBOARD_DIR"
else
  log_info "Dashboard not installed at $DASHBOARD_DIR — skipping"
fi

# ── Remove runtime template files (not live data) ────────────────────────────

if [ "$FORCE" = "true" ]; then
  log_warn "--force: removing runtime directories..."
  for subdir in main bookmarker trader shared; do
    rm -rf "$WORKSPACE/runtime/$subdir"
    log_ok "Removed $WORKSPACE/runtime/$subdir"
  done
else
  log_info "Runtime data preserved (use --force to remove)"
fi

# ── Remove .installed marker ──────────────────────────────────────────────────

INSTALLED_FILE="$AGENCY_DIR/.installed"
if [ -f "$INSTALLED_FILE" ]; then
  rm -f "$INSTALLED_FILE"
  log_ok "Removed .installed marker"
fi

echo ""
log_ok "Uninstall complete."
echo ""
if [ "$FORCE" = "false" ]; then
  echo "  Note: Your identity and runtime data were preserved."
  echo "  Use --force to remove all runtime directories."
fi
echo ""

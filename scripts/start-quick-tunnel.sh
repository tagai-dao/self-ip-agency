#!/usr/bin/env bash
# start-quick-tunnel.sh — Thin wrapper around dashboard-service.sh start-public
#
# NOTE: This script exists only for backward compatibility. The canonical owner
# of dashboard lifecycle (local + public tunnel) is:
#
#   scripts/dashboard-service.sh
#
# All new call sites and docs should use dashboard-service.sh directly.
#
# Usage:
#   ./start-quick-tunnel.sh [port]
#
# Behavior:
#   - Delegates to `dashboard-service.sh start-public` with the given port
#     (default 7890 to match the canonical dashboard port).
#   - State + URL captured in <workspace>/runtime/shared/dashboard-service.json
#     (schema: dashboard.service.v1).
#   - Logs: <workspace>/logs/dashboard-tunnel.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_SERVICE="$SCRIPT_DIR/dashboard-service.sh"

PORT="${1:-7890}"

if [ ! -x "$DASHBOARD_SERVICE" ]; then
  if [ -f "$DASHBOARD_SERVICE" ]; then
    chmod +x "$DASHBOARD_SERVICE" 2>/dev/null || true
  else
    echo "[ERROR] dashboard-service.sh not found at: $DASHBOARD_SERVICE" >&2
    exit 2
  fi
fi

echo "[INFO] start-quick-tunnel.sh is a thin wrapper — delegating to dashboard-service.sh start-public"
exec "$DASHBOARD_SERVICE" start-public --port "$PORT"

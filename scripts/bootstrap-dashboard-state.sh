#!/usr/bin/env bash
# bootstrap-dashboard-state.sh — Seed dashboard-required runtime artifacts
# with bootstrap/pending semantics so a fresh install shows neutral state
# instead of a wall of red/critical indicators.
#
# Usage: bash scripts/bootstrap-dashboard-state.sh [--workspace /path/to/workspace]
#
# Safe to re-run: only writes files that do not already exist (no-clobber).
# Does NOT produce fake healthy data — all artifacts carry explicit
# "bootstrap" or "pending_first_run" markers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse workspace arg ─────────────────────────────────────────────────────
WORKSPACE="${OPENCLAW_WORKSPACE:-}"
for arg in "$@"; do
  case "$arg" in
    --workspace=*) WORKSPACE="${arg#--workspace=}" ;;
  esac
done
if [ -z "$WORKSPACE" ]; then
  WORKSPACE="$HOME/.openclaw/workspace"
fi

RUNTIME="$WORKSPACE/runtime"

# ── Helpers ─────────────────────────────────────────────────────────────────

write_if_missing() {
  local filepath="$1"
  local content="$2"
  if [ ! -f "$filepath" ]; then
    mkdir -p "$(dirname "$filepath")"
    printf '%s\n' "$content" > "$filepath"
  fi
}

NOW_UTC=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── Shared runtime artifacts ───────────────────────────────────────────────

write_if_missing "$RUNTIME/shared/runtime-status.json" '{
  "main": {"status": "bootstrap", "updated_at": "'"$NOW_UTC"'"},
  "bookmarker": {"status": "bootstrap", "updated_at": "'"$NOW_UTC"'"},
  "trader": {"status": "bootstrap", "updated_at": "'"$NOW_UTC"'"},
  "schema": "runtime-status.v1",
  "bootstrap": true,
  "bootstrap_at": "'"$NOW_UTC"'"
}'

write_if_missing "$RUNTIME/shared/budget-allocation.json" '{
  "schema": "budget-allocation.v1",
  "bootstrap": true,
  "allocated_at": null,
  "social_budget": null,
  "trade_budget": null
}'

write_if_missing "$RUNTIME/shared/latest-attribution.json" '{
  "schema": "latest-attribution.v1",
  "bootstrap": true,
  "attribution": null
}'

write_if_missing "$RUNTIME/shared/social-history.json" '{
  "schema": "social-history.v1",
  "bootstrap": true,
  "items": []
}'

write_if_missing "$RUNTIME/shared/social-write-state.json" '{
  "schema": "social-write-state.v1",
  "bootstrap": true,
  "state": "pending_first_run"
}'

write_if_missing "$RUNTIME/shared/wiki-contract-alert.json" '{
  "schema": "wiki-contract-alert.v1",
  "bootstrap": true,
  "severity": "none",
  "action": null,
  "message": "Pending first wiki contract check"
}'

write_if_missing "$RUNTIME/shared/wiki-maintenance-alert.json" '{
  "schema": "wiki-maintenance-alert.v1",
  "bootstrap": true,
  "severity": "none",
  "action": null,
  "message": "Pending first maintenance cycle"
}'

write_if_missing "$RUNTIME/shared/tas-history.jsonl" ''

# ── Main agent artifacts ────────────────────────────────────────────────────

write_if_missing "$RUNTIME/main/runtime-health.json" '{
  "schema": "main.runtime-health.v1",
  "bootstrap": true,
  "status": "bootstrap",
  "checked_at": "'"$NOW_UTC"'"
}'

write_if_missing "$RUNTIME/main/input-packet.json" '{
  "schema": "main.input-packet.v1",
  "bootstrap": true,
  "generated_at": null
}'

write_if_missing "$RUNTIME/main/tas-latest.json" '{
  "schema": "main.tas-latest.v1",
  "bootstrap": true,
  "tas_total": null,
  "tas_social": null,
  "tas_trade": null,
  "updated_at": null
}'

write_if_missing "$RUNTIME/main/last-decision.json" '{
  "schema": "main.last-decision.v1",
  "bootstrap": true,
  "decision": null,
  "updated_at": null
}'

write_if_missing "$RUNTIME/main/strategy-plan.json" '{
  "schema": "main.strategy-plan.v1",
  "bootstrap": true,
  "strategy_action": "pending_first_run"
}'

write_if_missing "$RUNTIME/main/social-intent.json" '{
  "schema": "main.social-intent.v1",
  "bootstrap": true,
  "intent": null,
  "issued_at": null
}'

write_if_missing "$RUNTIME/main/tas-social.json" '{
  "schema": "main.tas-social.v1",
  "bootstrap": true,
  "value": null,
  "inputs": {}
}'

write_if_missing "$RUNTIME/main/latest.json" '{
  "schema": "main.latest.v1",
  "bootstrap": true,
  "generated_at": "'"$NOW_UTC"'",
  "status": "bootstrap"
}'

# ── Bookmarker agent artifacts ──────────────────────────────────────────────

write_if_missing "$RUNTIME/bookmarker/topic-brief.json" '{
  "schema": "bookmarker.topic-brief.v1",
  "bootstrap": true,
  "generated_at": null,
  "topics": []
}'

write_if_missing "$RUNTIME/bookmarker/source-health.json" '{
  "schema": "bookmarker.source-health.v1",
  "bootstrap": true,
  "status": "bootstrap",
  "sources": []
}'

write_if_missing "$RUNTIME/bookmarker/content-candidates.json" '{
  "schema": "bookmarker.content-candidates.v1",
  "bootstrap": true,
  "candidates": []
}'

write_if_missing "$RUNTIME/bookmarker/topic-performance.json" '{
  "schema": "bookmarker.topic-performance.v1",
  "bootstrap": true,
  "topics": []
}'

write_if_missing "$RUNTIME/bookmarker/autonomy-intent.json" '{
  "schema": "bookmarker.autonomy-intent.v1",
  "bootstrap": true,
  "intent": null
}'

write_if_missing "$RUNTIME/bookmarker/social-drafts.json" '{
  "schema": "bookmarker.social-drafts.v1",
  "bootstrap": true,
  "drafts": []
}'

write_if_missing "$RUNTIME/bookmarker/social-execution.json" '{
  "schema": "bookmarker.social-execution.v1",
  "bootstrap": true,
  "executions": []
}'

write_if_missing "$RUNTIME/bookmarker/latest.json" '{
  "schema": "bookmarker.latest.v1",
  "bootstrap": true,
  "generated_at": "'"$NOW_UTC"'",
  "status": "bootstrap"
}'

write_if_missing "$RUNTIME/bookmarker/topic-heatmap.json" '{
  "schema": "bookmarker.topic-heatmap.v1",
  "bootstrap": true,
  "heatmap": {}
}'

# ── Trader agent artifacts ──────────────────────────────────────────────────

write_if_missing "$RUNTIME/trader/wallet-snapshot.json" '{
  "schema": "trader.wallet-snapshot.v1",
  "bootstrap": true,
  "balances": {}
}'

write_if_missing "$RUNTIME/trader/reward-status.json" '{
  "schema": "trader.reward-status.v1",
  "bootstrap": true,
  "claimable": null
}'

write_if_missing "$RUNTIME/trader/risk-status.json" '{
  "schema": "trader.risk-status.v1",
  "bootstrap": true,
  "flags": [],
  "status": "bootstrap"
}'

write_if_missing "$RUNTIME/trader/onchain-positions.json" '{
  "schema": "trader.onchain-positions.v1",
  "bootstrap": true,
  "positions": []
}'

write_if_missing "$RUNTIME/trader/portfolio-baseline.json" '{
  "schema": "trader.portfolio-baseline.v1",
  "bootstrap": true,
  "baseline": null
}'

write_if_missing "$RUNTIME/trader/portfolio-delta.json" '{
  "schema": "trader.portfolio-delta.v1",
  "bootstrap": true,
  "delta": null
}'

write_if_missing "$RUNTIME/trader/measurement-quality.json" '{
  "schema": "trader.measurement-quality.v1",
  "bootstrap": true,
  "quality": null
}'

write_if_missing "$RUNTIME/trader/latest.json" '{
  "schema": "trader.latest.v1",
  "bootstrap": true,
  "generated_at": "'"$NOW_UTC"'",
  "status": "bootstrap"
}'

# ── Dev / Claude Dispatch artifacts ─────────────────────────────────────────

write_if_missing "$RUNTIME/dev/stage-status.json" '{
  "schema": "dev.stage-status.v1",
  "bootstrap": true,
  "stage": null
}'

write_if_missing "$RUNTIME/dev/backlog.json" '{
  "schema": "dev.backlog.v1",
  "bootstrap": true,
  "items": []
}'

write_if_missing "$RUNTIME/dev/dispatch-roi.json" '{
  "schema": "dev.dispatch-roi.v1",
  "bootstrap": true,
  "roi": null
}'

echo "Dashboard bootstrap state seeded at: $RUNTIME"

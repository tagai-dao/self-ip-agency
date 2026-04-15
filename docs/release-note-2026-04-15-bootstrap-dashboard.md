# Release Note — 2026-04-15 — Bootstrap Dashboard First-Run Semantics

## Summary

This release fixes the first-run dashboard experience for fresh Self-IP Agency installs.

Before this change, a newly installed environment could look broken even when nothing had actually failed:
- dashboard panels rendered as missing / degraded because required runtime artifacts did not exist yet,
- installer output could fail at summary time due to an unbound `workspace` variable,
- clean-room acceptance showed that the installer's dashboard verification path expected `/api/health` even though the dashboard server did not expose that endpoint,
- and bootstrap seeding initially missed `runtime/trader/tas-trade.json`, leaving one avoidable doctor warning on fresh install.

## Shipped changes

### 1) Bootstrap runtime seeding

Added `scripts/bootstrap-dashboard-state.sh`.

It seeds the dashboard-required runtime artifacts with explicit bootstrap / pending-first-run markers so a fresh install has neutral, truthful placeholders instead of missing-file red states.

### 2) Installer integration

`install.sh` now runs bootstrap seeding after runtime installation.

It also no longer crashes in the summary / install-contract section due to an unbound `workspace` variable.

### 3) Runtime-output contract hardening

`main-heartbeat.sh`, `bookmarker-cycle.sh`, and `trader-cycle.sh` now update `runtime/shared/runtime-status.json` so the dashboard can distinguish:
- bootstrap,
- initializing,
- and post-cycle populated states.

`main-heartbeat.sh` also emits `runtime/main/latest.json` during minimal heartbeat output so the dashboard shows a real timestamp for the main agent pill.

### 4) Dashboard bootstrap semantics

The dashboard now treats first-run state as bootstrap instead of degraded/critical:
- bootstrap banner added,
- bootstrap color/status classes added,
- missing timestamps map to `bootstrap` instead of `critical`,
- control tower and agent health endpoints distinguish bootstrap / initializing from actual failure,
- and the status API keeps the bootstrap banner visible until the core main/bookmarker/trader outputs have each transitioned out of bootstrap.

### 5) Doctor output alignment

`doctor.sh` now reports dashboard-required artifacts as:
- `bootstrap — awaiting first cycle`, or
- `populated`

instead of collapsing first-run state into generic missing/degraded output.

### 6) Dashboard health endpoint

Added `GET /api/health` to the dashboard server so installer verification and operator checks match the documented contract.

## Validation performed

Validated in a clean temporary workspace on macOS:
- fresh install completes and emits install-contract markers,
- bootstrap runtime artifacts are seeded,
- `doctor.sh` reports bootstrap artifacts as awaiting first cycle,
- dashboard loads against the clean workspace,
- bootstrap banner is visible in the UI,
- agent state shows bootstrap / pending-first-run rather than false critical failure,
- and `/api/health` now responds successfully.

## Operator impact

After a fresh install, seeing blue bootstrap / pending state is now expected behavior.

Operators should treat this as:
- installation completed,
- runtime skeleton exists,
- first real agent cycles have not yet populated live data.

Once main heartbeat, bookmarker, and trader each run, the dashboard should transition from bootstrap to normal live status automatically.

## Follow-up recommendation

If additional installer verification is added later, keep the contract aligned across all three places:
- dashboard server endpoints,
- installer health checks,
- and operator-facing docs.

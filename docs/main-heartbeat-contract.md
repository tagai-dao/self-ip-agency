# Main Heartbeat Contract (Machine-Readable)

> Schema: `main-heartbeat-contract.v1`
> Last updated: 2026-04-15

## Contract Summary

This document is the authoritative contract for the main heartbeat system.
External agents, cron schedulers, and CI systems should reference this document
to understand what the main heartbeat expects and produces.

## Source of Truth

| Property | Value |
|----------|-------|
| Primary entrypoint | `scripts/main-heartbeat.sh` |
| Self-check command | `scripts/main-heartbeat.sh --self-check` |
| Heartbeat command | `scripts/main-heartbeat.sh` |
| Dry-run command | `scripts/main-heartbeat.sh --dry-run` |
| Cron schedule | `*/10 * * * *` (every 10 minutes) |

## Critical Clarification: runtime/main/task.json

```
TASK_JSON_IS_PRIMARY_HEARTBEAT_SOURCE: false
```

**`runtime/main/task.json` is NOT the primary task queue for the main heartbeat.**

- It does not exist by default in the runtime template.
- If present, it is a compatibility placeholder that redirects agents to `scripts/main-heartbeat.sh`.
- A missing `runtime/main/task.json` is NOT a failure condition.
- External agents MUST NOT interpret its absence as "no work to do" or "heartbeat idle".

The primary heartbeat source is always `scripts/main-heartbeat.sh`.

## Input Dependencies

The heartbeat reads from these files (all optional — graceful degradation on missing):

| File | Purpose | Required? |
|------|---------|-----------|
| `.installed` | Install status marker | Yes (self-check fails without it) |
| `config/agency-identity.json` | Agent identity | Warn if missing |
| `~/.config/tagclaw/credentials.json` | API credentials | Warn if missing |
| `runtime/main/heartbeat.json` | Previous heartbeat state | No (created on first run) |
| `runtime/shared/tas_snapshot.json` | TAS scores | No |
| `runtime/shared/identity-strategy.json` | Strategy state | No |
| `runtime/shared/wiki-lint-status.json` | Wiki health | No |

## Output Artifacts

A successful heartbeat cycle produces:

### 1. runtime/main/heartbeat.json

```json
{
  "heartbeat_id": "hb-20260415120000",
  "timestamp": "2026-04-15T12:00:00+00:00",
  "mode": "active",
  "source": "main-heartbeat.sh",
  "tas_score": 42.5,
  "tas_social": 30.0,
  "tas_trade": 12.5,
  "bookmarker_status": "idle",
  "trader_status": "idle",
  "alerts": [],
  "schema": "main.heartbeat.v1"
}
```

### 2. Stdout Contract Block

```
### BEGIN HEARTBEAT CONTRACT ###
HEARTBEAT_MODE="heartbeat"
HEARTBEAT_SOURCE="scripts/main-heartbeat.sh"
HEARTBEAT_STATUS="completed"
TASK_JSON_IS_PRIMARY="false"
### END HEARTBEAT CONTRACT ###
```

### 3. Self-Check Stdout (--self-check mode)

```
### BEGIN HEARTBEAT CONTRACT ###
HEARTBEAT_MODE="self-check"
HEARTBEAT_SOURCE="scripts/main-heartbeat.sh"
HEARTBEAT_STATUS="validated"
TASK_JSON_IS_PRIMARY="false"
### END HEARTBEAT CONTRACT ###
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (heartbeat completed or self-check passed) |
| 1 | Failure (self-check failed or runtime error) |

## Anti-Patterns (What NOT to Do)

1. **Do NOT** read `runtime/main/task.json` as the heartbeat trigger
2. **Do NOT** report HEARTBEAT_OK / idle if `task.json` is missing
3. **Do NOT** generate a generic heartbeat summary without running the actual script
4. **Do NOT** skip `scripts/main-heartbeat.sh` in favor of prompt-only phrasing
5. **Do NOT** treat a missing `runtime/main/task.json` as a deployment failure

## Correct First-Run Sequence

For an external agent running the first main heartbeat:

```bash
# Step 1: Validate environment
bash scripts/main-heartbeat.sh --self-check

# Step 2: If self-check passes, run the heartbeat
bash scripts/main-heartbeat.sh

# Step 3: Verify output
cat runtime/main/heartbeat.json
```

## Integration with Cron

The cron configuration in `config/cron-jobs.json` uses `scripts/main-heartbeat.sh`
as the command for the `main-heartbeat` job. Register with:

```bash
openclaw cron add main-heartbeat '*/10 * * * *' '/path/to/agency/scripts/main-heartbeat.sh'
```

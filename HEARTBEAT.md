# Main Heartbeat Contract

> **For agents and humans**: This file defines what the main heartbeat does,
> how to run it, and what success looks like.

## Quick Start

```bash
# First-run self-check (validates environment, no side effects)
bash scripts/main-heartbeat.sh --self-check

# Normal heartbeat cycle (build input, run orchestrator, write outputs)
bash scripts/main-heartbeat.sh

# Dry run (show what would execute without running it)
bash scripts/main-heartbeat.sh --dry-run
```

## What Is the Main Heartbeat?

The main heartbeat is the recurring orchestration loop for the self-IP agency.
Every cycle it:

1. **Validates** the runtime environment (install status, identity, credentials)
2. **Builds** a main input packet (TAS scores, strategy state, wiki health)
3. **Runs** the main runtime orchestrator (evaluates mode, dispatches sub-agents)
4. **Writes** `runtime/main/heartbeat.json` and `runtime/main/status.json`

## Entrypoint

The **sole recommended entrypoint** is:

```
scripts/main-heartbeat.sh
```

This script is the source of truth for main heartbeat execution.

## What runtime/main/task.json Is NOT

**`runtime/main/task.json` is NOT the primary task queue or heartbeat trigger.**

It does not exist by default. If present, it is a compatibility placeholder that
redirects to `scripts/main-heartbeat.sh`. External agents must NOT treat a
missing `runtime/main/task.json` as a heartbeat failure.

See `docs/main-heartbeat-contract.md` for the full machine-readable contract.

## Success Criteria

A successful heartbeat produces:

| Artifact | Location | Schema |
|----------|----------|--------|
| Heartbeat status | `runtime/main/heartbeat.json` | `main.heartbeat.v1` |
| Agent status | `runtime/main/status.json` | `main.status.v1` |
| Stdout contract | stdout markers | `### BEGIN HEARTBEAT CONTRACT ###` block |

The stdout contract block contains:
```
HEARTBEAT_MODE="heartbeat"
HEARTBEAT_SOURCE="scripts/main-heartbeat.sh"
HEARTBEAT_STATUS="completed"
TASK_JSON_IS_PRIMARY="false"
```

## Self-Check Mode

For first-run validation, use `--self-check`:

```bash
bash scripts/main-heartbeat.sh --self-check
```

This validates the environment without executing the runtime. It checks:
- `.installed` marker exists
- `runtime/main/` directory exists
- Identity file is present and has a username
- Credentials file exists
- Heartbeat template is in place

Exit code 0 = environment is ready. Exit code 1 = fix errors first.

## Cron Registration

Register the main heartbeat with OpenClaw:

```bash
openclaw cron add \
  --name "main-heartbeat" \
  --cron "*/10 * * * *" \
  --session isolated \
  --message "Run the main heartbeat cycle: bash /path/to/self-ip-agency/scripts/main-heartbeat.sh"
```

## Further Reading

- `docs/main-heartbeat-contract.md` — full machine-readable contract
- `docs/deployment-guide.md` — deployment walkthrough
- `docs/wiki-runtime-contract-v1.md` — runtime contract specification

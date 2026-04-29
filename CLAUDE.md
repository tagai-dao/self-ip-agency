# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`self-ip-agency` is an **installer + template pack**, not a standalone runtime. `scripts/install.sh` scaffolds a 3-agent IP operations stack (`main` / `bookmarker` / `trader`) into an OpenClaw workspace. The repo itself is the source of truth; the workspace is where cycles actually run.

Two roots matter — keep them straight:

| Root | What it holds |
|------|---------------|
| `AGENCY_DIR` (this repo) | Installer, templates (`agents/*.md.tmpl`, `runtime-template/`, `wiki-template/`), schema, docs, Python scripts |
| `OPENCLAW_WORKSPACE` (default `~/.openclaw/workspace`) | Materialized `runtime/`, `wiki/`, `skills/tagclaw/`, `skills/tagclaw-wallet/`, and the deployed copies of `scripts/*-cycle.sh` + `main-heartbeat.sh` |

Most Python scripts resolve paths via the `OPENCLAW_WORKSPACE` env var with a fallback to `~/.openclaw/workspace`. When debugging behaviour that looks wrong in the repo, check whether the workspace copy is the one actually running.

## Common commands

```bash
# Install / re-install (idempotent). Also handles TagClaw onboarding + wallet scaffold.
bash scripts/install.sh
bash scripts/install.sh --tagclaw-name YourAgt1 --tagclaw-description "..."
bash scripts/install.sh --dry-run
bash scripts/install.sh --skip-tagclaw-onboarding

# Full runtime health check (defaults to $OPENCLAW_WORKSPACE or ~/.openclaw/workspace)
bash scripts/doctor.sh
bash scripts/doctor.sh --workspace /custom/path

# Cycle entrypoints — run from the workspace after install. Each supports --self-check and --dry-run.
bash ~/.openclaw/workspace/scripts/main-heartbeat.sh --self-check
bash ~/.openclaw/workspace/scripts/bookmarker-cycle.sh --self-check
bash ~/.openclaw/workspace/scripts/trader-cycle.sh --self-check

# Wiki / strategy tooling (all respect OPENCLAW_WORKSPACE)
python3 scripts/wiki_lint.py
python3 scripts/verify_wiki_contract.py
python3 scripts/select_strategy.py [--stats] [--apply]
python3 scripts/build_wiki_query_index.py [--force]

# Dashboard — canonical owner is dashboard-service.sh. Do not start server.py directly in prod flows.
bash scripts/dashboard-service.sh start-local
bash scripts/dashboard-service.sh status --json
bash scripts/dashboard-service.sh stop
bash scripts/dashboard-service.sh start-public   # opt-in Cloudflare Quick Tunnel
pip3 install -r dashboard/requirements.txt       # first-time deps

# Tests — ad-hoc, no framework. Run the file directly.
python3 scripts/test_bookmarker_runtime.py

# Uninstall
bash scripts/uninstall.sh
```

There is no lint/build pipeline and no `pytest`/`make` harness. Python is stdlib-only in adapters; dashboard needs `fastapi` + `uvicorn` + `requests`.

## Architecture you need to know

### Three-layer knowledge pipeline (Wiki)

`raw/` (immutable sources) → `wiki/` (compiled knowledge with YAML frontmatter) → `runtime/shared/*.json` (derived artifacts agents consume).

The compilation rules live in `schema/` — treat these as the operational constitution, not prose docs. `schema/resolver-map.yaml` binds tasks to the context they must read; `schema/identity-safety.md` declares that `wiki/identity/persona.md` and `wiki/identity/key-positions.md` are **manually managed only** — no script, strategy loop, or feedback pipeline may touch them. Feedback flows into strategy, never identity.

The topic registry (`config/wiki_topic_registry.json` resolved via `scripts/wiki_registry.py`) is the single source of truth for concept/tick naming. Never introduce a local alias map in a new script.

### AutoResearch

Epsilon-greedy hill-climbing over two independent tracks (bookmarker + trader). Modes cycle `BASELINE → EXPLORE → EXPLOIT`. Cycles append to `memory/main-strategy-log.jsonl` (workspace). When changing strategy search spaces, edit `scripts/select_strategy.py`.

### Main heartbeat contract

`runtime/main/task.json` is **NOT** the heartbeat trigger. The sole entrypoint is `scripts/main-heartbeat.sh`. A missing `task.json` is not a failure. Every run emits a `### BEGIN HEARTBEAT CONTRACT ###` stdout block with `HEARTBEAT_STATUS` — downstream tooling parses that, not free text. See `docs/main-heartbeat-contract.md` for the full machine-readable schema.

### Install contract

`install.sh` writes three parallel outputs:
- `.install-next-steps.json` (schema `install-next-steps.v2`, with structured `next_steps` + flat `next_steps_text` fallback)
- `.install-next-steps.md`
- stdout `### BEGIN INSTALL CONTRACT ###` block

Status is `partial` until identity, credentials, and dashboard are all confirmed; only then `verified`. The X verification tweet is surfaced atomically across all three channels plus a dedicated `<workspace>/tagclaw-verification-tweet.txt` handoff file — do not split it back up.

### Platform adapter boundary

`adapters/base.py` defines the abstract interface; `adapters/tagclaw.py` is the only concrete implementation. Agent code and runtime scripts must call adapter methods, never embed TagClaw-specific API logic inline. Stdlib-only — do not add third-party HTTP libs to adapters.

## Conventions & hazards

- **Generated files are gitignored and must not be committed**: `agents/main.md`, `agents/bookmarker.md`, `agents/trader.md` (rendered from `.md.tmpl`), `runtime/`, `.installed`, `.install-next-steps.{json,md}`, `.cache/`, anything matching `.env`.
- **Credentials**: TagClaw API state lives only in `<workspace>/skills/tagclaw/.env`; wallet secrets only in `<workspace>/skills/tagclaw-wallet/.env`. `config/credentials.example.json` is a deprecated migration note, not a runtime source. Never pre-create the skill `.env` before onboarding returns real values.
- **Versioning**: `VERSION` is semver; `install.sh` reads it and writes it into `.installed`. Bump when touching install/runtime contracts. Cross-check `SKILL.md`'s own `version:` field, which currently lags `VERSION`.
- **Install is idempotent**: re-running `install.sh` after `git pull` is the supported upgrade path. Prefer fixing installer logic over manual workspace surgery.
- **Entrypoint discipline**: the three cycle scripts (`main-heartbeat.sh`, `bookmarker-cycle.sh`, `trader-cycle.sh`) are the only supported run surface. Do not invent new ones or call `run_*_runtime_v*.py` as a top-level entry.
- **Versioned script names**: runtime entrypoints still use explicit version suffixes where old deployed names may exist. Current unversioned entrypoints include `build_main_input_packet.py`, `wiki_lint.py`, and `select_strategy.py`.
- **Dashboard ownership**: `scripts/dashboard-service.sh` is the canonical lifecycle owner. `install.sh` delegates to it; don't launch `dashboard/server.py` directly except for local dev.
- **Public dashboard exposure** is opt-in (`dashboard.public.enabled` / `auto_start` in `config/agency.config.yaml`). The MVP is Cloudflare Quick Tunnel only — no access control, URL is ephemeral. Do not wire a default-on public URL.

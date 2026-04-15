# Wiki-Runtime Contract v1

## Overview

The self-IP LLM Wiki system has a chain of **canonical sources** and **derived artifacts**. This document defines what is source-of-truth, what is derived, what refreshes what, and how the canonical topic/tick registry fits in.

## Architecture

```
config/wiki_topic_registry.json    ← CANONICAL: topic/tick/concept naming & aliases
    ↓ (imported by scripts via wiki_registry.py)
wiki/concepts/*.md                 ← CANONICAL: concept knowledge base
wiki/onchain-ticks/*.md            ← CANONICAL: tick profiles
wiki/synthesis/tweets/*.md         ← CANONICAL: tweet theme annotations
raw/x-interactions/*.json          ← CANONICAL: raw X interaction data
wiki/tagclaw-platform/trending-ticks.md  ← CANONICAL: trending tick rankings
    ↓ (compiled by pipeline scripts)
runtime/bookmarker/topic-heatmap.json    ← DERIVED: heat scores per topic
runtime/shared/wiki-execution-brief.json ← DERIVED: weekly execution brief
runtime/shared/community-heat.json       ← DERIVED: community heat for tracked ticks
runtime/shared/wiki-lint-status.json     ← DERIVED: wiki quality lint results
wiki/onchain-ticks/INDEX.json            ← DERIVED: compiled tick index
```

## Canonical Topic/Tick Registry

**File**: `config/wiki_topic_registry.json`
**Resolver**: `scripts/wiki_registry.py`

The registry centralizes:
- **Concepts**: canonical names, aliases, categories, wiki file paths
- **Ticks**: canonical names, tracked status, wiki file paths

### Why a Registry?

Previously, alias logic was scattered across scripts:
- `build_wiki_execution_brief_v1.py` had a local `CONCEPT_ALIAS` dict
- `refresh_wiki_community_heat_v1.py` had a local `TRACKED_TICKS` list
- Other scripts used ad-hoc name matching

This caused naming drift where the same concept could appear under different names in different artifacts.

### Registry Schema

```json
{
  "schema": "wiki-topic-registry-v1",
  "concepts": {
    "ATOC": {
      "canonical_name": "ATOC",
      "display_name": "ATOC",
      "aliases": ["AgentInfrastructure", "AgentSwarm", "atoc-agent"],
      "category": "agent-infra",
      "wiki_file": "wiki/concepts/ATOC.md"
    }
  },
  "ticks": {
    "TagClaw": {
      "canonical_name": "TagClaw",
      "display_name": "TagClaw",
      "tracked": true,
      "wiki_file": "wiki/onchain-ticks/TagClaw.md"
    }
  }
}
```

### Resolver API

```python
from wiki_registry import resolve_concept, get_tracked_ticks

resolve_concept("AgentInfrastructure")  # → "ATOC"
resolve_concept("desoc-agent")          # → "DeSoc"
resolve_concept("UnknownTopic")         # → "UnknownTopic" (passthrough)
get_tracked_ticks()                     # → ["TagClaw", "BUIDL", ...]
```

## Derived Artifact Contracts

| Derived Artifact | Source(s) | Refresh Script | Staleness Limit |
|---|---|---|---|
| `runtime/bookmarker/topic-heatmap.json` | raw/x-interactions/ + wiki/synthesis/tweets/ | `build_wiki_topic_heatmap_v1.py` | None (on-demand) |
| `runtime/shared/wiki-execution-brief.json` | wiki/concepts/ + topic-heatmap.json | `build_wiki_execution_brief_v1.py` | 7 days |
| `runtime/shared/community-heat.json` | wiki/tagclaw-platform/trending-ticks.md | `refresh_wiki_community_heat_v1.py` | 48 hours |
| `runtime/shared/wiki-lint-status.json` | wiki/ | `wiki_lint_v1.py` | None |
| `wiki/onchain-ticks/INDEX.json` | raw/onchain-token-transation/ | `build_onchain_ticks_wiki_v1.py` | None |

## Contract Verifier

**Script**: `scripts/verify_wiki_runtime_contract_v1.py`

Checks:
1. **Source presence**: all canonical source directories/files exist
2. **Derived presence**: all derived artifacts exist
3. **Freshness**: derived artifacts are within staleness limits
4. **Schema**: derived JSON files have expected top-level keys
5. **Registry consistency**: aliases don't conflict, wiki files exist

Output: `runtime/shared/wiki-contract-verify.json`

```bash
python3 scripts/verify_wiki_runtime_contract_v1.py        # human-readable
python3 scripts/verify_wiki_runtime_contract_v1.py --json  # machine-readable
```

## Rules for Future Work

1. **New concepts/ticks**: add to `config/wiki_topic_registry.json`, never to script-local maps
2. **Alias resolution**: always use `wiki_registry.resolve_concept()`, never local dicts
3. **Tracked ticks**: always use `wiki_registry.get_tracked_ticks()`, never hardcoded lists
4. **New derived artifacts**: add an entry to the CONTRACT list in `verify_wiki_runtime_contract_v1.py`
5. **Staleness**: if a derived artifact has a freshness requirement, document it in the contract

## Runtime Integration (P2)

The contract verifier now runs automatically as part of both runtime refresh paths:

- **`refresh-runtime-v2.sh`** (Step 8): runs after status summary, ensures fresh verifier output on every refresh cycle
- **`run-full-cycle-v2.sh`** (Step 9): runs after post-status hooks, with incident recording on failure

This means `runtime/shared/wiki-contract-verify.json` stays current without manual intervention.

## Dashboard Visibility (P2)

The dashboard API (`/api/status` and `/api/wiki`) exposes a `contract_health` object:

```json
{
  "status": "ok",
  "pass": 32,
  "fail": 0,
  "verified_at": "2026-04-11T12:30:00+00:00",
  "age_hours": 0.5,
  "top_failures": []
}
```

The dashboard UI renders this in the Wiki System section under "Contract Health", showing status, pass/fail counts, verification timestamp, and the top 5 failing checks when degraded.

## Cross-Artifact Consistency Checks (P2)

Three cross-artifact checks ensure derived artifacts stay consistent with the registry:

1. **`cross:brief-themes-resolvable`**: Every `top_themes` entry in the execution brief must resolve to a known concept via the registry. Catches naming drift between the brief compiler and the registry.

2. **`cross:heat-ticks-match-registry`**: The set of ticks in `community-heat.json` must exactly match `get_tracked_ticks()` from the registry. Catches cases where a tick is added/removed from the registry but the heat refresh hasn't caught up.

3. **`cross:heatmap-themes-resolvable`**: Theme names in the topic heatmap must resolve via the registry. Catches unregistered themes appearing in the heatmap.

## Standalone Verification & Degraded Alerting (P4)

### Standalone Verifier Path

The verifier can now run independently on a recurring schedule via a cron wrapper:

```bash
# Manual run
python3 scripts/verify_wiki_runtime_contract_v1.py

# Cron wrapper (with logging)
scripts/cron-wiki-contract-verify.sh

# Crontab example (every 30 minutes):
*/30 * * * * /Users/buidlclawdbot/.openclaw/workspace/scripts/cron-wiki-contract-verify.sh
```

This decouples contract monitoring from the runtime refresh cycle, so degraded states are caught even when no refresh is running.

### Degraded Alert Artifact

Every verifier run now emits `runtime/shared/wiki-contract-alert.json` — a deterministic, machine-readable alert signal:

```json
{
  "schema": "wiki-contract-alert-v1",
  "status": "degraded",
  "severity": "warning",
  "pass": 30,
  "fail": 2,
  "verified_at": "2026-04-11T13:00:00+00:00",
  "failing_checks": ["community-heat:freshness", "wiki-execution-brief:freshness"],
  "message": "Wiki contract degraded: 30 pass, 2 fail — top failures: community-heat:freshness, wiki-execution-brief:freshness"
}
```

**Severity levels** (derived deterministically from fail count):
| Severity | Condition |
|----------|-----------|
| `clear` | status == ok (0 failures) |
| `warning` | 1-3 failures |
| `critical` | 4+ failures |

### Dashboard Integration

The dashboard API (`/api/wiki`) now includes `alert_severity` and `alert_message` in the `contract_health` object. When severity is `warning` or `critical`, the dashboard UI renders an alert row in the Contract Health section.

### Consumption Patterns

- **Cron/main agent**: read `runtime/shared/wiki-contract-alert.json`, check `severity` field
- **Dashboard**: auto-rendered in Contract Health section via API
- **Scripts**: `jq .severity runtime/shared/wiki-contract-alert.json` for quick shell checks

## Wiki Events Ledger (P5)

### Overview

An append-only JSONL ledger at `runtime/shared/wiki-events.jsonl` records structured events from wiki pipeline runs. This provides durable event history for provenance, timeline debugging, and future fact-sidecar work.

### Event Schema

Each line is a JSON object:

```json
{
  "ts": "2026-04-11T13:30:00+08:00",
  "event_type": "contract_verify",
  "producer": "verify_wiki_runtime_contract_v1",
  "entity": "optional — topic/tick/concept name",
  "artifact": "runtime/shared/wiki-contract-verify.json",
  "status": "ok",
  "summary": "pass=32 fail=0 severity=clear",
  "detail": {"pass": 32, "fail": 0, "severity": "clear"}
}
```

**Required fields**: `ts`, `event_type`, `producer`, `status`
**Optional fields**: `entity`, `artifact`, `summary`, `detail`

### Event Types & Producers

| event_type | Producer Script | Artifact |
|---|---|---|
| `contract_verify` | `verify_wiki_runtime_contract_v1.py` | `runtime/shared/wiki-contract-verify.json` |
| `execution_brief_build` | `build_wiki_execution_brief_v1.py` | `runtime/shared/wiki-execution-brief.json` |
| `topic_heatmap_build` | `build_wiki_topic_heatmap_v1.py` | `runtime/bookmarker/topic-heatmap.json` |
| `community_heat_refresh` | `refresh_wiki_community_heat_v1.py` | `runtime/shared/community-heat.json` |

### Shared Helper

All producers use `append_wiki_event()` from `scripts/runtime_utils_v2.py`:

```python
from runtime_utils_v2 import append_wiki_event

append_wiki_event(
    event_type='contract_verify',
    producer='verify_wiki_runtime_contract_v1',
    artifact='runtime/shared/wiki-contract-verify.json',
    status='ok',
    summary='pass=32 fail=0',
    detail={'pass': 32, 'fail': 0},
)
```

The helper is fail-safe: exceptions are swallowed to avoid disrupting the calling pipeline. Events are appended via file-append mode (no locking needed for single-writer).

### Design Decisions

- **JSONL over JSON array**: append-safe without read-modify-write; compatible with `jq`, `grep`, `tail -f`
- **Fail-safe emission**: event write failures never propagate to the calling pipeline
- **Compact vocabulary**: 4 event types initially; new producers add new types without schema migration
- **No backfill**: only forward events from integration point onward

### Consumption Patterns

```bash
# Latest 10 events
tail -10 runtime/shared/wiki-events.jsonl | jq .

# Filter by event type
grep '"contract_verify"' runtime/shared/wiki-events.jsonl | jq .

# Count events by type
jq -r .event_type runtime/shared/wiki-events.jsonl | sort | uniq -c
```

## Provenance Sidecar / Fact Layer (P6)

### Overview

Key derived artifacts now emit compact machine-readable **provenance sidecars** that explain what artifact was derived from which inputs, by which producer, and with what intermediate facts. Sidecars are file-based (no database), deterministic, and written atomically.

### Sidecar Schema

Each sidecar is written to `<artifact>.provenance.json` (adjacent to the artifact):

```json
{
  "schema": "provenance-sidecar-v1",
  "artifact_ref": "runtime/shared/wiki-execution-brief.json",
  "generated_at": "2026-04-11T14:00:00+08:00",
  "producer": "build_wiki_execution_brief_v1",
  "artifact_schema": "wiki-execution-brief-v1",
  "source_refs": [
    "wiki/concepts/",
    "runtime/bookmarker/topic-heatmap.json",
    "wiki/identity/persona.md"
  ],
  "facts": {
    "theme_count": 5,
    "top_theme": "DeSoc",
    "valid_until": "2026-04-18T06:00:00Z"
  }
}
```

**Required fields**: `schema`, `artifact_ref`, `generated_at`, `producer`
**Optional fields**: `artifact_schema`, `source_refs`, `facts`

### Producer Coverage

| Artifact | Sidecar Path | Producer |
|---|---|---|
| `runtime/shared/wiki-execution-brief.json` | `wiki-execution-brief.json.provenance.json` | `build_wiki_execution_brief_v1` |
| `runtime/bookmarker/topic-heatmap.json` | `topic-heatmap.json.provenance.json` | `build_wiki_topic_heatmap_v1` |
| `runtime/shared/community-heat.json` | `community-heat.json.provenance.json` | `refresh_wiki_community_heat_v1` |

### Shared Helper

All producers use `write_provenance_sidecar()` from `scripts/runtime_utils_v2.py`:

```python
from runtime_utils_v2 import write_provenance_sidecar

write_provenance_sidecar(
    artifact_path=OUTPUT_JSON,
    producer='build_wiki_execution_brief_v1',
    source_refs=['wiki/concepts/', 'runtime/bookmarker/topic-heatmap.json'],
    schema_version='wiki-execution-brief-v1',
    facts={'theme_count': 5, 'top_theme': 'DeSoc'},
)
```

The helper is atomic (tempfile + os.replace) and uses workspace-relative paths for `artifact_ref`.

### Contract Verifier Integration

The verifier (`verify_wiki_runtime_contract_v1.py`) now checks provenance sidecars:
- Sidecar file exists adjacent to each covered artifact
- Valid JSON with required keys (`schema`, `artifact_ref`, `generated_at`, `producer`)
- Schema version is `provenance-sidecar-v1`

### Design Decisions

- **Adjacent file pattern** (`<name>.provenance.json`): co-located with the artifact; no separate directory or registry needed
- **No opaque IDs**: `artifact_ref` and `source_refs` use workspace-relative paths that humans and scripts can follow
- **`facts` is free-form**: each producer stores the most operationally useful intermediate values without a rigid schema
- **Incremental**: only 3 high-value artifacts covered initially; new producers can adopt the pattern with one function call

## Nightly Maintenance / Dream Cycle (P7)

### Overview

A lightweight recurring maintenance loop that checks system health, refreshes key derived artifacts, and emits a concise maintenance report. Designed for cron scheduling — no database or workflow engine required.

### Entry Point

```bash
# Direct execution
python3 scripts/wiki_nightly_maintenance_v1.py

# Cron wrapper (with logging)
scripts/cron-wiki-nightly-maintenance.sh
```

**Crontab example** (daily at 03:00 UTC):
```
0 3 * * * /Users/buidlclawdbot/.openclaw/workspace/scripts/cron-wiki-nightly-maintenance.sh
```

### What the Cycle Runs

| Step | Action | Reuses |
|------|--------|--------|
| 1. Contract Verify | Full contract verification (freshness, schema, cross-artifact) | `verify_wiki_runtime_contract_v1.py` |
| 2. Artifact Freshness | Independent freshness check on 7 key artifacts | File mtime comparison |
| 3. Wiki Lint | Content health check (broken links, stale concepts, orphans) | `wiki_lint_v1.py` |
| 4. Provenance Coverage | Check sidecar existence for covered artifacts | File existence |
| 5. Events Ledger Health | Validate ledger integrity (valid JSONL, required fields) | Ledger parsing |

### Report Artifact

**Path**: `runtime/shared/wiki-maintenance-report.json`

```json
{
  "schema": "wiki-maintenance-report-v1",
  "generated_at": "2026-04-11T03:00:00+00:00",
  "started_at": "2026-04-11T03:00:00+00:00",
  "overall_status": "ok",
  "degraded_signals": [],
  "steps": {
    "contract_verify": { "status": "ok", "pass": 42, "fail": 0, "severity": "clear" },
    "artifact_freshness": [ { "artifact": "...", "exists": true, "age_hours": 1.2, "fresh": true } ],
    "wiki_lint": { "status": "ok" },
    "provenance_coverage": { "covered": ["..."], "missing": [], "coverage_pct": 100 },
    "events_ledger": { "status": "ok", "event_count": 16, "invalid_lines": 0 }
  }
}
```

**Overall status**:
- `ok` — all checks pass, no stale/missing artifacts
- `degraded` — one or more `degraded_signals` detected (contract warning, stale artifacts, missing provenance)

### Events Ledger Integration

The cycle appends a `nightly_maintenance` event to `runtime/shared/wiki-events.jsonl`:

| event_type | Producer Script | Artifact |
|---|---|---|
| `nightly_maintenance` | `wiki_nightly_maintenance_v1.py` | `runtime/shared/wiki-maintenance-report.json` |

### Consumption

```bash
# Quick status check
jq .overall_status runtime/shared/wiki-maintenance-report.json

# Degraded signals
jq '.degraded_signals' runtime/shared/wiki-maintenance-report.json

# Maintenance history from events ledger
grep '"nightly_maintenance"' runtime/shared/wiki-events.jsonl | jq .
```

## P8: Controlled Auto-Repair & Active Alerting

### Overview

The nightly maintenance cycle now includes two new capabilities:
1. **Controlled auto-repair**: safe, deterministic refresh actions for stale/degraded artifacts
2. **Active alerting chain**: a machine-readable alert artifact designed for consumption by notifiers, reminder scripts, or the main agent

### Auto-Repair Policy

Repairs are controlled by an explicit **allowlist** in `wiki_nightly_maintenance_v1.py`. Only deterministic, non-destructive refresh scripts are eligible:

| Artifact | Repair Script | Risk |
|----------|--------------|------|
| `wiki-contract-verify` | `verify_wiki_runtime_contract_v1.py` | None — read-only verification |
| `wiki-lint-status` | `wiki_lint_v1.py` | None — read-only lint check |
| `community-heat` | `refresh_wiki_community_heat_v1.py` | Low — re-derives from canonical source |

**What is NOT auto-repaired** (alert-only):
- `wiki-execution-brief` — requires LLM synthesis, not deterministic
- `topic-heatmap` — depends on external X interaction data
- Provenance sidecars — should be regenerated by their owning pipeline
- Any artifact requiring judgment, external API calls, or financial actions

### Repair Flow

```
Phase 1: Observe
  → contract verify, freshness check, lint, provenance, ledger

Phase 2: Auto-repair (if degraded)
  → attempt allowlisted repairs only
  → record outcome: repaired | failed | skipped

Phase 3: Re-verify (if repairs attempted)
  → re-run freshness + contract checks
  → determine post-repair status
```

### Repair Outcomes

Each repair attempt produces a structured result:

```json
{
  "artifact": "community-heat",
  "outcome": "repaired",
  "script": "scripts/refresh_wiki_community_heat_v1.py",
  "description": "Re-derive community heat from trending-ticks source"
}
```

Possible `outcome` values:
- `repaired` — script ran successfully, artifact refreshed
- `failed` — script exited non-zero or threw an exception
- `skipped` — script not found or precondition not met

### Active Alert Artifact

**Path**: `runtime/shared/wiki-maintenance-alert.json`

```json
{
  "schema": "wiki-maintenance-alert-v1",
  "generated_at": "2026-04-11T03:00:00+00:00",
  "pre_repair_status": "degraded",
  "post_repair_status": "ok",
  "severity": "clear",
  "action": "none",
  "remaining_degraded_signals": [],
  "repairs_attempted": 2,
  "repairs_succeeded": 2,
  "repairs_failed": 0,
  "repairs_skipped": 0,
  "repair_details": [...],
  "message": "Wiki maintenance: severity=clear action=none | repaired=[...]"
}
```

**Severity levels**:
| Severity | Condition |
|----------|-----------|
| `clear` | Post-repair status is ok |
| `warning` | Degraded with 1-2 failed repairs or unresolved signals |
| `critical` | 3+ failed repairs |

**Action levels**:
| Action | Meaning |
|--------|---------|
| `none` | All clear, no notification needed |
| `notify` | Degraded but no failed repairs — informational alert |
| `escalate` | Failed repairs — requires operator attention |

### Consumption Patterns

```bash
# Quick alert check
jq '.severity, .action' runtime/shared/wiki-maintenance-alert.json

# Check if escalation needed
jq 'select(.action == "escalate")' runtime/shared/wiki-maintenance-alert.json

# Repair history from events ledger
grep '"nightly_maintenance"' runtime/shared/wiki-events.jsonl | jq '.detail.repairs_succeeded'
```

**Integration paths**:
- **Main agent**: read `wiki-maintenance-alert.json`, escalate to operator if `action == "escalate"`
- **Cron notifier**: wrap `cron-wiki-nightly-maintenance.sh` with a post-check that reads the alert artifact
- **Dashboard**: expose alert severity in the Wiki System section

### Report Schema Change

The maintenance report schema is now `wiki-maintenance-report-v2`:
- Added `pre_repair_status` field (status before repairs)
- Added `repair_results` array (detailed repair outcomes)
- `overall_status` now reflects post-repair state
- Backward-compatible: old consumers can still read `overall_status` and `degraded_signals`

## P9: File-Based Query / Index Layer

A thin query surface over all structured wiki artifacts, implemented without a database.

### Query Entry Point: `scripts/query_wiki_facts_v1.py`

CLI and library. Five query modes:

| Mode | CLI Example | Description |
|------|-------------|-------------|
| `canonical` | `python3 scripts/query_wiki_facts_v1.py canonical AgentInfrastructure` | Resolve alias → canonical concept/tick with metadata |
| `artifact` | `python3 scripts/query_wiki_facts_v1.py artifact wiki-execution-brief` | Look up artifact existence, schema, timestamp, and provenance |
| `events` | `python3 scripts/query_wiki_facts_v1.py events --limit 5 --type contract_verify` | Recent events from wiki-events.jsonl (most recent first) |
| `health` | `python3 scripts/query_wiki_facts_v1.py health` | Aggregated health: contract + lint + maintenance |
| `maintenance` | `python3 scripts/query_wiki_facts_v1.py maintenance` | Latest maintenance report summary with step breakdown |

All outputs are JSON with stable field names.

Library usage:
```python
from query_wiki_facts_v1 import query_canonical, query_artifact, query_events, query_health, query_maintenance
```

### Aggregated Index: `runtime/shared/wiki-query-index.json`

Built by `scripts/build_wiki_query_index_v1.py`. Schema: `wiki-query-index-v1`.

Sections:
- `registry` — concept count, tick count, tracked ticks, alias count
- `artifacts` — catalog of 8 wiki artifacts with existence, age, schema, provenance status
- `health` — quick snapshot: contract status/severity, maintenance severity, lint score, overall ok/degraded
- `recent_events` — last 10 events (compact: ts, type, status, summary)

Refresh: `python3 scripts/build_wiki_query_index_v1.py` (deterministic, idempotent).

### Artifact Sources Feeding the Query Layer

| Artifact | Fed by |
|----------|--------|
| `wiki-contract-verify.json` | `verify_wiki_runtime_contract_v1.py` |
| `wiki-contract-alert.json` | `verify_wiki_runtime_contract_v1.py` |
| `wiki-execution-brief.json` | `build_wiki_execution_brief_v1.py` |
| `wiki-lint-status.json` | `wiki_lint_v1.py` |
| `wiki-maintenance-report.json` | `wiki_nightly_maintenance_v1.py` |
| `wiki-maintenance-alert.json` | `wiki_nightly_maintenance_v1.py` |
| `community-heat.json` | `refresh_wiki_community_heat_v1.py` |
| `config/wiki_topic_registry.json` | Manual edits (canonical source) |

## P10: Dashboard Explainability UI v1

Added a focused operator-facing explainability surface to the dashboard, exposed via `/api/explainability`.

### API: `/api/explainability`

Returns a JSON payload with three sections:

| Field | Type | Description |
|-------|------|-------------|
| `artifacts` | `list` | Catalog of 7 key wiki artifacts with existence, age, metadata, and provenance summary |
| `recent_events` | `list` | Last 15 events from `wiki-events.jsonl` (ts, event_type, producer, artifact, status, summary) |
| `health` | `dict` | Aggregated health context: overall + contract + maintenance + lint |

Each artifact entry includes:
- `filename`, `label`, `exists` — identity
- `meta` — key metadata fields from the artifact (schema, timestamps, status)
- `age_hours`, `timestamp` — freshness
- `provenance` — producer, generated_at, source_refs (from `.provenance.json` sidecar if present)

### UI: Explainability Panel

Located in the Wiki Intelligence section, the panel is collapsible and open by default. Two-column layout:

- **Left column**: Artifact State & Provenance — one card per artifact showing label, freshness badge, key metadata, and provenance chain
- **Right column**: Health Context (3-pill grid: contract, maintenance, lint) + Recent Wiki Events (scrollable timeline of last 15 events)

### Artifacts Covered (v1)

| Artifact | Label |
|----------|-------|
| `wiki-execution-brief.json` | Execution Brief |
| `community-heat.json` | Community Heat |
| `wiki-contract-verify.json` | Contract Verify |
| `wiki-maintenance-report.json` | Maintenance Report |
| `wiki-lint-status.json` | Wiki Lint |
| `wiki-contract-alert.json` | Contract Alert |
| `wiki-maintenance-alert.json` | Maintenance Alert |

### Test Coverage

Three new checks added to `test_runtime_v2.py`:
- `explainability-server-syntax-ok` — server.py compiles cleanly
- `explainability-html-panel-exists` — index.html contains the panel elements
- `explainability-js-render-exists` — app.js contains render function and API fetch

## P11: Retrieval-Oriented Index v1

### Overview

The retrieval-oriented index (retrieval pack) synthesizes compact, self-contained text+metadata documents from existing wiki structured assets. Each document is suitable for agent consumption, semantic search, or future indexing — without requiring a DB or vector backend.

### Builder: `scripts/build_wiki_retrieval_pack_v1.py`

Deterministic builder that reads existing artifacts and produces a single output file.

```bash
python3 scripts/build_wiki_retrieval_pack_v1.py
```

**Output**: `runtime/shared/wiki-retrieval-pack.json` (schema: `wiki-retrieval-pack-v1`)

### Retrieval Pack Schema

```json
{
  "schema": "wiki-retrieval-pack-v1",
  "generated_at": "ISO8601",
  "doc_count": 43,
  "doc_type_counts": { "entity": 33, "artifact": 8, "event_window": 1, "health_digest": 1 },
  "docs": [
    {
      "doc_type": "entity|artifact|event_window|health_digest",
      "doc_id": "entity:concept:TagClaw",
      "text": "compact retrieval text with key signals merged",
      "source_refs": ["config/wiki_topic_registry.json", "..."]
    }
  ]
}
```

### Document Types

| Type | Count | Sources | Description |
|------|-------|---------|-------------|
| `entity` | 1 per concept + 1 per tracked tick | registry, brief, heatmap, community heat | Merged entity profile: name, aliases, category, stance, heat, trend |
| `artifact` | 1 per wiki artifact | artifact files + provenance sidecars | Artifact metadata, status, schema, provenance facts |
| `event_window` | 1 | wiki-events.jsonl | Last 20 events grouped by type with ok/fail counts |
| `health_digest` | 1 | contract alert, lint, maintenance alert/report | Full health summary: contract, lint, maintenance, overall |

### Per-Document Fields

Every document has:
- `doc_type`: one of the four types above
- `doc_id`: stable, unique ID (e.g. `entity:concept:TagClaw`, `artifact:wiki-lint-status.json`)
- `text`: compact human/LLM-readable text merging key signals from sources
- `source_refs`: list of canonical/derived artifact paths that fed this doc

Entity docs additionally have: `entity_kind`, `canonical_name`, `category`.
Artifact docs additionally have: `artifact_name`, `schema`, `timestamp`, `age_hours`, `has_provenance`.
Event window docs additionally have: `event_count`, `event_types`, `ts_range`.
Health digest docs additionally have: `overall`.

### Query Surface: `retrieve` Mode

Added to `scripts/query_wiki_facts_v1.py`:

```bash
# All docs
python3 scripts/query_wiki_facts_v1.py retrieve

# Filter by keyword
python3 scripts/query_wiki_facts_v1.py retrieve --query TagClaw

# Filter by doc type
python3 scripts/query_wiki_facts_v1.py retrieve --doc-type artifact

# Combined
python3 scripts/query_wiki_facts_v1.py retrieve --query reward --doc-type entity --limit 5
```

Library API:
```python
from query_wiki_facts_v1 import query_retrieve
result = query_retrieve(query="TagClaw", doc_type="entity", limit=5)
# result["docs"] → list of matching retrieval documents
```

### Artifact Sources Feeding the Retrieval Pack

| Source Artifact | Feeds Doc Type |
|-----------------|---------------|
| `config/wiki_topic_registry.json` | entity |
| `runtime/shared/wiki-execution-brief.json` | entity |
| `runtime/bookmarker/topic-heatmap.json` | entity |
| `runtime/shared/community-heat.json` | entity |
| All 8 wiki artifacts in `runtime/shared/` | artifact |
| `*.provenance.json` sidecars | artifact |
| `runtime/shared/wiki-events.jsonl` | event_window |
| `runtime/shared/wiki-contract-alert.json` | health_digest |
| `runtime/shared/wiki-maintenance-alert.json` | health_digest |
| `runtime/shared/wiki-maintenance-report.json` | health_digest |
| `runtime/shared/wiki-lint-status.json` | health_digest |

### Test Coverage

`test_wiki_retrieval_pack()` in `scripts/test_runtime_v2.py`:
- Builder exists and compiles
- Pack artifact exists with schema `wiki-retrieval-pack-v1`
- Pack has `generated_at`, `docs` (≥5), `doc_type_counts` (≥3 types)
- Every doc has `doc_type`, `doc_id`, `text`, `source_refs`
- All four doc types present
- `doc_count` consistent with actual docs length
- `query_retrieve()` keyword search returns results
- `query_retrieve()` doc_type filter works correctly

### Design Decisions

- **File-based, no DB**: The pack is a single JSON file, rebuilt deterministically. No vector DB, no embeddings — just structured text documents.
- **Text field is the retrieval surface**: Each doc's `text` is a compact, human/LLM-readable summary merging signals from multiple source artifacts. Future semantic search can index this field.
- **Stable doc_id**: Enables diffing between pack versions and targeted retrieval.
- **Source refs for provenance**: Every doc traces back to the canonical/derived artifacts that produced it.
- **Incremental by design**: Re-running the builder produces a fresh pack. Future: incremental updates via event-driven triggers.

## P13: Local Retrieval Quality Upgrade

### Overview

P13 improves local retrieval recall quality without introducing a DB/vector backend. Three enhancements:

1. **CJK tokenization**: Chinese characters are now extracted as individual tokens (unigram). Previously, the tokenizer stripped all non-ASCII, making Chinese queries return zero results.
2. **CamelCase splitting**: `AgentEconomy` → `[agent, economy]`, improving sub-word recall for compound names.
3. **Alias/synonym expansion**: Query tokens are expanded using the registry's alias map. Querying "AgentInfrastructure" automatically expands to include "atoc" tokens, boosting the canonical ATOC entity.

### Tokenizer Changes (`_tokenize`)

Before:
```python
re.split(r'[^a-z0-9]+', text.lower())  # drops all CJK, no CamelCase split
```

After:
- CamelCase split via regex before lowering
- Latin alphanumeric tokens extracted (min length 2)
- CJK characters (U+4E00–U+9FFF, U+3400–U+4DBF) extracted as individual tokens

### Alias Expansion

`_build_alias_map()` builds a `{alias_lower: [canonical_tokens]}` dict from the registry. `_expand_query_tokens()` appends canonical tokens for any matched aliases while preserving original tokens.

Example: query "BTC" → tokens `["btc"]` → expanded `["btc", "bitcoin"]`

### Explainability

When alias expansion occurs, the retrieve result includes an `expanded_tokens` field showing what tokens were added. When no expansion occurs, the field is `null`.

### Limitations

- CJK tokenization is per-character (unigram), not phrase-based. A segmenter like jieba could improve precision but adds a dependency.
- Alias map is rebuilt per query (acceptable at current scale).
- No English stemming (e.g., "trading" vs "trade").
- No fuzzy/edit-distance matching.

### Test Coverage

`test_wiki_retrieval_quality_p13()` in `test_runtime_v2.py`:
- CJK token extraction from mixed Chinese+Latin text
- CamelCase splitting (AgentEconomy → agent, economy)
- Alias map is non-empty and expands AgentInfrastructure → atoc
- BTC alias expands to bitcoin
- Chinese query "生态" returns retrieval results
- Alias query "AgentInfrastructure" finds ATOC entity
- `expanded_tokens` field present when expansion occurs

## P14: Final Optimization Pack (v1.x Complete)

The final optimization pack addresses the remaining backlog and marks the local-first/file-based self-IP Wiki roadmap as **complete for v1.x**.

### Completed Optimizations

| # | Item | Implementation |
|---|------|----------------|
| H1 | Chinese phrase tokenization | CJK bigram generation from consecutive runs in `_tokenize()` — multi-char phrases like "生态系统" now produce both unigrams and bigrams for better recall |
| H2 | Alias/synonym coverage expansion | 15+ concepts now have aliases including Chinese terms, kebab-case variants, and abbreviations in `wiki_topic_registry.json` |
| H3 | Explainability UI refinement | Artifact cards now have expandable detail panels showing all top-level fields, plus raw file path links for operator drill-down |
| H4 | Alerting chain refinement | Signal classification (critical/actionable/informational), suppression of repaired signals, tiered escalation logic in maintenance alerts |
| M1 | Retrieval ranking enhancement | Field-weight BM25 boosting — matches in `canonical_name`/`doc_id` get 2x weight vs body text |
| M2 | Maintenance policy maturation | Retrieval pack and query index added to REPAIR_ALLOWLIST and FRESHNESS_CHECKS (48h) |
| M3 | Query/index rebuild cadence | Staleness-aware skip logic in both builders — only rebuild when upstream sources have changed, `--force` flag to override |

### Roadmap Status: Complete for v1.x

The local-first/file-based self-IP Wiki architecture is now effectively landed:
- Registry + resolver as single source of truth
- Contract verification + alerting chain
- Events ledger + provenance sidecars
- Nightly maintenance with auto-repair
- Query/index layer with BM25 ranking
- Explainability UI with detail expansion
- CJK + alias-aware retrieval quality
- Staleness-aware rebuild cadence

### Intentionally Deferred (Optional Future Work)

These items are **not required** for v1.x and are left as optional future enhancements:
- Extend registry with tick contract addresses (from onchain-ticks INDEX.json)
- Registry versioning for backward-compatible schema evolution
- Telegram notification integration when alert severity is `critical`
- Lightweight English stemming (suffix stripping) for improved recall
- Cache alias map across queries for higher throughput
- Integrate retrieval pack into agent prompt context for richer self-IP awareness

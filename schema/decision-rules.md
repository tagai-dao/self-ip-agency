---
title: Decision Rules
type: schema
scope: compile-decisions
updated: 2026-06-05
---

# Decision Rules

Rules for the decision-memory layer (`wiki/synthesis/decisions/`) — Tier-3 of the
LLM-Wiki ladder, alongside theses/claims. Decisions are an **append-only,
auto-compiled** record of what the three agents (main / trader / bookmarker)
actually decided, why, and how it turned out.

## Source of truth

Decision records are **derived, never hand-authored**. `scripts/build_decisions_synthesis_v1.py`
reads the agents' existing decision trails (read-only) and normalizes them:

| Source | Agent | Decision kind |
|--------|-------|---------------|
| `runtime/shared/strategy-ledger.jsonl` | main | strategy-shift |
| `runtime/main/tas-decisions-*.jsonl` | main | strategy-eval |
| `runtime/main/last-decision.json` | main | social-intent / treasury-policy |
| `runtime/trader/executions-*.json` | trader | trade-execution |
| `runtime/bookmarker/planned-action-log.jsonl` | bookmarker | curation |

No agent changes behaviour to feed this layer; it observes existing outputs.

## Two surfaces

1. **Ledger** — `runtime/shared/decision-index.json` (schema `self-ip-decision-index-v1`):
   the full machine-readable record (rolling 90-day window, capped). This is what
   retrieval/agents consume.
2. **Records** — `wiki/synthesis/decisions/<YYYY-MM>/<date>-<agent>-<kind>-<id>.md`:
   human-readable pages for **page-worthy decisions only** (executed trades +
   main authorization changes), recent window. High-frequency streams
   (strategy churn, per-cycle TAS evals, individual curations) stay ledger-only.

### Required record frontmatter

```yaml
id: decision-{agent}-{YYYYMMDD}-{kind}-{hash}
agent: main|trader|bookmarker
decided_at: ISO timestamp
kind: strategy-shift|strategy-eval|social-intent|treasury-policy|trade-execution|curation
action: what was decided (one line)
outcome: pending|ok|skipped|failed
status: active
linked_concepts: []   # resolved via wiki_registry — never a local alias map
```

## Update rules

- **Idempotent**: an existing `.md` record is never clobbered; re-runs only add new ones.
- **Append-only intent**: decisions are historical facts; do not rewrite past records.
- **Concept linking** goes through `scripts/wiki_registry.py` (`resolve_concept`).
- Every compile appends a `decisions_compiled` event to `runtime/shared/wiki-events.jsonl`.

## Prohibitions

- **Identity primacy**: this layer MUST NOT read from or write to `wiki/identity/`
  (`persona.md`, `key-positions.md`, `README.md`). Feedback flows to strategy, never identity.
- No hand-authored decision pages (they would be overwritten by the compiler's model).
- No mass `.md` generation: page-worthy kinds + recent window only.

# wiki/synthesis/decisions/

Decision-memory: an **auto-compiled, append-only** record of what the three agents
(main / trader / bookmarker) decided, why, and how it turned out. Tier-3 of the
LLM-Wiki ladder. See `schema/decision-rules.md` for the constitution.

## How it works

`scripts/build_decisions_synthesis_v1.py` normalizes the agents' existing decision
trails (read-only) into two surfaces:

- **Ledger** — `runtime/shared/decision-index.json` (schema `self-ip-decision-index-v1`):
  the full machine-readable record (rolling 90-day window). Consumed by the
  retrieval pack, the INDEX "Recent Decisions" table, and the agents themselves.
- **Records** (the files under this directory) — `<YYYY-MM>/<date>-<agent>-<kind>-<id>.md`:
  human-readable pages for page-worthy decisions only (executed trades + main
  authorization changes), recent window. Strategy churn / per-cycle evals /
  individual curations stay ledger-only (queryable, not one page each).

## Do not hand-edit

These pages are generated. Edit the compiler or its sources, not the records.
The compiler never touches `wiki/identity/`.

## Rebuild

```bash
python3 scripts/build_decisions_synthesis_v1.py            # full
python3 scripts/build_decisions_synthesis_v1.py --dry-run  # preview counts
```
Runs daily via the `wiki-decisions-compile` cron (with failure alerts).

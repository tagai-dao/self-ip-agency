---
title: Artifact Routing
type: schema
scope: runtime-artifacts
updated: 2026-04-13
---

# Artifact Routing

Rules for where derived artifacts are written and how they are consumed.

## Artifact locations

All derived artifacts follow the existing v1 convention:

| Category | Base path | Examples |
|----------|-----------|---------|
| Shared runtime | `runtime/shared/` | resolver-pack.json, wiki-lint-status.json |
| Bookmarker runtime | `runtime/bookmarker/` | topic-heatmap.json |
| Wiki content | `wiki/` | concepts/, synthesis/, queries/ |
| Wiki lint output | `wiki/lint/` | latest-report.md |

## Naming conventions

- Runtime JSON: `{artifact-name}.json` with 2-space indent
- Provenance sidecars: `{artifact-name}.json.provenance.json`
- All runtime JSON must include:
  - `"schema"` field identifying the artifact type and version
  - `"generated_at"` ISO timestamp

## Write rules

- Runtime artifacts are always overwritten atomically (tempfile + os.replace)
- Wiki pages are merged, not overwritten blindly
- Log entries (`wiki/log.md`) are append-only
- Raw sources (`raw/`) are immutable — never modified after creation

## Staleness model

Follows `docs/wiki-runtime-contract-v1.md`:

| Artifact | Staleness limit |
|----------|-----------------|
| wiki-execution-brief.json | 7 days |
| community-heat.json | 48 hours |
| wiki-lint-status.json | none (on-demand) |
| resolver-pack.json | none (on-demand) |

## Contract verification

New runtime artifacts should be registered in `scripts/verify_wiki_runtime_contract_v1.py` to participate in automated health checks.

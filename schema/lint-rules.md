---
title: Lint Rules
type: schema
scope: lint-wiki
updated: 2026-04-13
---

# Lint Rules

Rules for wiki health checks. Three bands of linting.

## Band 1: Structural lint

Deterministic checks that require no LLM judgment.

- **Broken links**: internal wiki links that point to non-existent pages
- **Missing frontmatter**: pages without required YAML frontmatter
- **Orphan pages**: pages not linked from INDEX.md or any other page
- **Duplicate pages**: multiple pages covering the same canonical concept
- **Stale summaries**: pages not updated in >30 days with active raw sources
- **Empty pages**: pages with frontmatter but no meaningful content

## Band 2: Semantic lint (Phase 2+)

Requires claim/thesis layer to be operational.

- Repeated claims with divergent wording
- Unsupported assertions (claims without evidence links)
- Contradiction clusters
- Concept drift (terminology shifting without explicit update)
- Outdated thesis support

## Band 3: Identity lint (Phase 5+)

Requires identity drift checker.

- Tone drift from persona.md
- Position drift from key-positions.md
- Content angle overfitting
- Recent outputs inconsistent with identity anchors

## Output artifacts

| Artifact | Path |
|----------|------|
| Human-readable report | `wiki/lint/latest-report.md` |
| Machine-readable status | `runtime/shared/wiki-lint-status.json` |

## Current lint script

`scripts/wiki_lint_v1.py` covers Band 1 structural checks. Bands 2 and 3 will be added in later phases.

---
title: Thesis Rules
type: schema
scope: update-thesis
updated: 2026-04-13
---

# Thesis Rules

Rules for claim and thesis lifecycle management (Phase 2+).

## Claim lifecycle

Claims are explicit, trackable judgments stored in `wiki/claims/`.

### Status model

| Status | Meaning |
|--------|---------|
| `active` | Believed and supported by evidence |
| `contested` | Challenged by new evidence or counterarguments |
| `stale` | Not reinforced in >90 days |
| `deprecated` | Explicitly abandoned or superseded |

### Required frontmatter

```yaml
id: claim-{domain}-{topic}-{nnn}
statement: One-sentence claim
status: active|contested|stale|deprecated
confidence: high|medium|low
first_seen: ISO date
last_reinforced: ISO date
domain: concept domain
linked_theses: []
```

## Thesis lifecycle

Theses are recurring viewpoints or arguments stored in `wiki/theses/`.

### Revision states

| State | Meaning |
|-------|---------|
| `reinforced` | New evidence strengthens the thesis |
| `revised` | Modified based on new information |
| `split` | Thesis divided into sub-theses |
| `deprecated` | Thesis abandoned |

### Required frontmatter

```yaml
id: thesis-{topic}
title: Thesis statement
status: active|contested|stale|deprecated
confidence: high|medium|low
scope: concept domain
first_compiled_at: ISO date
last_revised_at: ISO date
revision_state: reinforced|revised|split|deprecated
```

## Update rules

- Claims and theses may be updated when new evidence arrives
- Status changes must be logged in `wiki/log.md`
- Contradictions must be surfaced, not silently resolved
- Identity-core positions (from `key-positions.md`) inform but do not override evidence-based thesis updates

## What thesis operations must NOT do

- Auto-generate hundreds of claims without review
- Silently deprecate claims without logging
- Modify `wiki/identity/*` files

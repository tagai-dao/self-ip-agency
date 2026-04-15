---
title: Identity Safety
type: schema
scope: identity-governance
updated: 2026-04-13
---

# Identity Safety

Defines the boundary between what may auto-update and what requires human approval.

## Protected identity anchors

These files are **manual-only** or **manual-or-quarterly**. No automated process may modify them:

| File | Policy | Owner |
|------|--------|-------|
| `wiki/identity/persona.md` | manual-only | 0xNought |
| `wiki/identity/key-positions.md` | manual-or-quarterly | 0xNought |
| `wiki/identity/README.md` | manual-only | 0xNought |

Any future files added to `wiki/identity/` inherit the `manual-only` policy by default.

## What may auto-update

Adaptive fields that respond to feedback without changing core identity:

- **Topic weights**: which topics to emphasize in content
- **Format preferences**: thread vs single post, length, structure
- **Platform heuristics**: timing, hashtags, engagement patterns
- **Source priority**: which raw source types yield highest-quality content
- **Citation depth**: how deeply to cite supporting evidence

These are stored in `runtime/shared/identity-strategy.json` and are always derived, never canonical.

## What may NOT auto-update

Core identity elements that define who TagClawX is:

- Persona definition (name, role, mission, tone)
- Key positions on core topics
- Worldview constraints and philosophical stances
- Forbidden framings and taboos (e.g., no "to the moon", no unconditional praise)
- Protected vocabulary and metaphors

## How feedback influences strategy

```
raw/feedback/ → feedback-summary.json → identity-strategy.json
                                            ↓
                                    adaptive fields ONLY
                                            ↓
                              consumed by draft-content, prepare-briefing
```

Feedback may change **how** content is distributed (topic emphasis, format, timing).
Feedback must NOT change **what** the system believes (positions, worldview, identity).

## Phase 3 implementation reference

The feedback compilation layer (Phase 3) enforces this boundary via two scripts:

| Script | Reads | Writes | Identity access |
|--------|-------|--------|-----------------|
| `scripts/build_feedback_summary.py` | `raw/feedback/*.json` | `runtime/shared/feedback-summary.json` | None — never reads or writes `wiki/identity/` |
| `scripts/build_identity_strategy.py` | `runtime/shared/feedback-summary.json` | `runtime/shared/identity-strategy.json` | None — never reads or writes `wiki/identity/` |

**Allowlist** (adaptive fields in `identity-strategy.json`):
- `topic_weights`
- `format_preferences`
- `platform_heuristics`
- `source_priority`

**Denylist** (declared as `protected_identity_fields` in the output):
- `persona`
- `key_positions`
- `worldview_constraints`
- `forbidden_framings`
- `protected_vocabulary`

The denylist is enforced structurally: the identity strategy script derives only from the feedback summary and never imports or references any file under `wiki/identity/`.

## Drift detection (Phase 5)

When implemented, identity drift detection will:

1. Compare recent outputs against identity anchors
2. Classify drift severity: none / watch / warning / critical
3. Emit a report — never auto-correct identity files
4. Flag cases requiring human review

## Escalation path

If any automated process detects it needs to modify a protected file:

1. Log the proposed change
2. Emit a structured proposal (not an edit)
3. Wait for human approval
4. Only apply after explicit confirmation from 0xNought

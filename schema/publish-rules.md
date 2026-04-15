---
title: Publish Rules
type: schema
scope: draft-content
updated: 2026-04-13
---

# Publish Rules

Rules for content drafting and publication.

## Required context loading

Before drafting any public content, the system must load:

1. `wiki/identity/persona.md` — tone, style, behavioral constraints
2. `wiki/identity/key-positions.md` — positions on core topics, taboos
3. Relevant concept pages from `wiki/concepts/`
4. Recent queries from `wiki/queries/` for novelty check
5. `runtime/shared/feedback-summary.json` (when available) — topic/format resonance
6. `runtime/shared/identity-strategy.json` (when available) — adaptive preferences

## Drafting constraints

- Content must be consistent with persona tone (中英混用, builder perspective)
- Content must not contradict key-positions taboos
- Content must not repeat the same angle as the last 3 posts on the same topic
- Content must cite or reference existing wiki knowledge, not fabricate

## Output requirements

- Draft text
- List of cited supporting wiki pages
- Novelty / repetition assessment
- Thesis alignment note (Phase 2+)

## Quality gates

- No half-finished content to public platforms
- No exaggerated vocabulary ("revolutionary", "disruptive")
- No unconditional project praise
- Data-backed arguments preferred over pure opinion

## What drafting must NOT do

- Modify `wiki/identity/*` files
- Publish without explicit approval for external platforms
- Use slogans ("to the moon", "LFG")
- Ignore topic fatigue signals from feedback summary

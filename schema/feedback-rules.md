---
title: Feedback Rules
type: schema
scope: process-feedback
updated: 2026-04-13
---

# Feedback Rules

Rules for processing feedback into strategy artifacts.

## Feedback sources

| Source | Type | Storage |
|--------|------|---------|
| Owner recognition (0xNought) | direct | `raw/feedback/` |
| External engagement metrics | derived | platform API snapshots |
| Repeated topic resonance | computed | from raw interaction patterns |
| Format wins/losses | computed | from engagement data |

## Processing flow

1. Ingest feedback into `raw/feedback/` (immutable)
2. Classify feedback type (recognition, engagement, resonance, format)
3. Link to touched content, topic, or thesis
4. Update `runtime/shared/feedback-summary.json`
5. Propose strategy changes to `runtime/shared/identity-strategy.json`
6. **Never** directly edit identity anchor files

## Allowed adaptive targets

These fields may be updated automatically based on feedback:

- Topic weights (which topics to emphasize)
- Format preferences (thread vs single post, length)
- Platform heuristics (timing, hashtags, engagement patterns)
- Source priority hints (which source types yield best content)
- Citation depth preference

## Disallowed adaptive targets

These fields require human approval and must NEVER be auto-updated:

- Core identity / persona text
- Key positions and worldview
- Forbidden framings / taboos
- Protected identity wording

## Output artifacts

| Artifact | Path | Content |
|----------|------|---------|
| Feedback summary | `runtime/shared/feedback-summary.json` | Aggregated topic/format/platform signals |
| Identity strategy | `runtime/shared/identity-strategy.json` | Adaptive fields only |

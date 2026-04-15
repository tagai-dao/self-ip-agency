---
title: Query Rules
type: schema
scope: answer-query
updated: 2026-04-13
---

# Query Rules

Rules for answering queries against the compiled wiki.

## Resolution order

1. Check `wiki/INDEX.md` for relevant concept pages
2. Load resolver pack for the `answer-query` task
3. Search concept pages, then synthesis pages, then raw sources
4. Cite durable artifacts — prefer wiki pages over raw files

## Output classification

Not every answer should be saved. Classify outputs:

| Category | Action |
|----------|--------|
| trivial | No file — answer in session only |
| useful | Append to session/query log |
| durable | Write to `wiki/queries/` with frontmatter |
| identity-shaping | Propose update to thesis/claim layer (Phase 2+) |

## Citation rules

- Always cite the wiki page or raw source that supports a claim
- Use workspace-relative paths: `wiki/concepts/TagClaw.md`, not absolute paths
- If a claim cannot be grounded in existing wiki content, state "needs verification"

## Identity alignment

- Answers must be consistent with `wiki/identity/persona.md` tone
- Answers must not contradict `wiki/identity/key-positions.md` without flagging
- When uncertainty exists, prefer the identity-anchored position

## What queries must NOT do

- Modify `wiki/identity/*` files
- Fabricate citations to non-existent pages
- Answer with generic AI filler — always ground in wiki content

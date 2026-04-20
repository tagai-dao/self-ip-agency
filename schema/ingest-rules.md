---
title: Ingest Rules
type: schema
scope: ingest-source
updated: 2026-04-13
---

# Ingest Rules

Rules for compiling raw sources into wiki pages.

## Source classification

Raw sources are classified by directory:

| Directory | Type | Ingest behavior |
|-----------|------|-----------------|
| `raw/x-tweets/` | owned tweet / guided bootstrap | Extract themes, link to concepts, update synthesis; replies may enter via guided bootstrap |
| `raw/x-bookmarks/` | curated external | Extract creator, themes, file to BookmarkCuration |
| `raw/x-likes/` | signal | Lightweight — update topic heat only |
| `raw/x-interactions/` | engagement | Update CommunityEcosystem, people nodes |
| `raw/tagclaw-posts/` | platform post | Update tagclaw-platform pages |
| `raw/onchain-token-transation/` | onchain | Update onchain-ticks pages |
| `raw/external-docs/` | reference doc | Extract concepts, file to relevant pages |

## Deduplication

- Every raw file has a unique filename or embedded ID
- Before writing, check if the source ID already exists in the wiki log
- Duplicate sources are skipped with a log note, never silently dropped

## Concept resolution

- Use `wiki_registry.resolve_concept()` for canonical naming
- Never create ad-hoc alias maps in scripts — all aliases live in `config/wiki_topic_registry.json`

## Write targets

- Concept pages: `wiki/concepts/`
- Synthesis pages: `wiki/synthesis/`
- Log entries: `wiki/log.md` (append-only)
- Index updates: `wiki/INDEX.md`

## What ingest must NOT do

- Modify `wiki/identity/*` files
- Delete existing raw sources
- Overwrite existing concept pages without merging
- Skip malformed sources silently — always log warnings

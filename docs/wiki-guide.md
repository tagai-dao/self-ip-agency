# Self-IP LLM Wiki — Operation Guide

The self-IP LLM Wiki is your agent's knowledge backbone. It follows a three-layer architecture:
**Raw Sources → Wiki (compiled knowledge) → Runtime (derived artifacts)**.

## Architecture

```
raw/                        ← Immutable raw sources
├── x-tweets/               ← Your X/Twitter posts
├── x-bookmarks/            ← Bookmarked content
├── x-interactions/         ← Engagement data
├── tagclaw-posts/          ← TagClaw platform posts
├── onchain-token-transation/ ← Blockchain data
└── external-docs/          ← External documentation

wiki/                       ← Compiled knowledge base
├── concepts/               ← Concept pages (your domain knowledge)
├── identity/               ← Protected: persona + key positions
├── synthesis/              ← Compiled synthesis from raw sources
├── queries/                ← Durable query results
├── execution/              ← Execution briefs
├── onchain-ticks/          ← Token/tick profiles
├── lint/                   ← Health check reports
└── INDEX.md                ← Master index

schema/                     ← Operational constitution
├── resolver-map.yaml       ← Task-to-context contracts
├── ingest-rules.md         ← How to compile raw → wiki
├── query-rules.md          ← How to answer queries
├── publish-rules.md        ← Content drafting rules
├── lint-rules.md           ← Health check rules
├── identity-safety.md      ← Protected boundaries
└── ...

runtime/shared/             ← Derived artifacts (auto-refreshed)
├── wiki-lint-status.json   ← Wiki health score
├── wiki-execution-brief.json ← Weekly execution summary
├── community-heat.json     ← Trending topic scores
├── wiki-contract-verify.json ← Contract verification
└── resolver-pack.json      ← Compiled resolver
```

## Getting Started

### 1. Create Your First Concept Page

Create `wiki/concepts/YourTopic.md`:

```markdown
---
title: Your Topic
type: wiki-concept
aliases: [alias1, alias2]
tags: [relevant-tag]
updated: 2026-04-15
---

# Your Topic

## Core Position
Your agent's stance on this topic.

## Key Facts
- Evidence-backed facts

## Agent Insights
How this concept drives agent decisions.
```

### 2. Set Up Your Identity

Edit `wiki/identity/persona.md` and `wiki/identity/key-positions.md`.
These are **protected files** — only the owner may edit them.

### 3. Register Topics

Add your concepts to `config/wiki_topic_registry.json`:

```json
{
  "concepts": {
    "YourTopic": {
      "canonical_name": "YourTopic",
      "display_name": "Your Topic",
      "aliases": ["alias1"],
      "category": "your-category",
      "wiki_file": "wiki/concepts/YourTopic.md"
    }
  }
}
```

### 4. Run Health Checks

```bash
python3 scripts/wiki_lint.py
# Outputs: wiki/lint/latest-report.md + runtime/shared/wiki-lint-status.json
```

### 5. Verify Contracts

```bash
python3 scripts/verify_wiki_contract.py
# Checks source presence, derived freshness, schema validity
```

## Topic Registry

The topic registry (`config/wiki_topic_registry.json`) is the single source of truth for naming:

```python
from wiki_registry import resolve_concept, get_tracked_ticks

canonical = resolve_concept("AgentInfrastructure")  # → "ATOC"
ticks = get_tracked_ticks()  # → ["TagClaw", "BUIDL", ...]
```

All scripts use `wiki_registry.py` — never maintain local alias maps.

## Identity Safety

Files under `wiki/identity/` are **manually managed**:
- `persona.md` — manual-only, defines who your agent IS
- `key-positions.md` — manual-or-quarterly, core positions

No automated process may modify these files. See `schema/identity-safety.md` for details.

Feedback influences **strategy** (topic weights, format preferences) but never **identity** (persona, positions, worldview).

## Health Score

The wiki lint computes a health score:

```
health_score = 100 - broken_links_pct×30 - stale_pct×20 - orphan_pct×10 - empty_pct×10
```

Score < 80 triggers `needs_attention: true` in the status output.

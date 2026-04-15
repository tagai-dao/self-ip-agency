# Obsidian Setup — Self-IP LLM Wiki

The wiki is designed to work with [Obsidian](https://obsidian.md/) as a visual knowledge graph editor.

## Installation

1. Download Obsidian from https://obsidian.md/
2. Open Obsidian → "Open folder as vault"
3. Select your workspace's `wiki/` directory

## Recommended Plugins

| Plugin | Purpose |
|--------|---------|
| Dataview | Query concept frontmatter (aliases, tags, updated dates) |
| Graph View (built-in) | Visualize concept connections via [[wikilinks]] |
| Templates | Create new concept pages from the `_example.md` template |
| Linter | Auto-format frontmatter on save |

## Vault Configuration

### Graph View Settings
- Enable: Tags, Attachments
- Color by: `type` frontmatter field
- Group: `wiki-concept` (blue), `identity-anchor` (red)

### File & Link Settings
- Use [[Wikilinks]]: ON
- Default location for new notes: `concepts/`

## Concept Frontmatter

Every concept page should have:

```yaml
---
title: Concept Name
type: wiki-concept
aliases: [alias1, alias2]
tags: [tag1, tag2]
graph_class: C
graph_weight: 1-5
updated: YYYY-MM-DD
---
```

### Graph Classes
- **A**: Core identity concepts (highest weight)
- **B**: Primary domain concepts
- **C**: Secondary/supporting concepts
- **D**: Reference/peripheral concepts

## Workflow

1. **Create concepts** in `wiki/concepts/` with proper frontmatter
2. **Link concepts** using [[wikilinks]] to build the knowledge graph
3. **Run lint** periodically: `python3 scripts/wiki_lint.py`
4. **Check the graph** in Obsidian's Graph View for orphans and clusters
5. **Update stale pages** flagged by the linter

## Identity Protection

The `wiki/identity/` folder contains protected files:
- Do NOT auto-edit these files
- Mark them as "pinned" or "starred" in Obsidian for easy access
- Review quarterly with the owner

## Adding Obsidian Aliases

The `aliases` field in frontmatter enables Obsidian to recognize alternative names:

```yaml
aliases: [TagClaw, tagclaw, TagClawX]
```

This lets you link with `[[TagClaw]]` or `[[tagclaw]]` and both resolve correctly.

# Operator Guide

## Daily Operations

### Morning Check
1. Verify dashboard is running: `curl -s localhost:8765/api/health`
2. Check wiki health: `python3 scripts/wiki_lint.py`
3. Review strategy stats: `python3 scripts/select_strategy.py --stats`
4. Check contract status: `python3 scripts/verify_wiki_contract.py`

### Monitoring
- Dashboard at `http://localhost:8765` shows real-time TAS, agent status, wiki health
- `runtime/shared/wiki-lint-status.json` — `needs_attention: true` means health < 80
- `runtime/shared/wiki-contract-verify.json` — `status: degraded` means checks failing

### Cycle Cadence

| Agent | Cycle | What Happens |
|-------|-------|-------------|
| Main (heartbeat) | Every 10 min | TAS check, mode evaluation, dispatch |
| Bookmarker | Every 30 min | Feed scan, curate, post, reply |
| Trader | Every 60 min | Signal check, trade execution, claim |
| Wiki lint | Weekly | Health check, report generation |
| Contract verify | Every refresh | Source/derived consistency check |
| Strategy select | Every heartbeat | Guidance generation for next cycle |

## Agent Modes

| Mode | TAS Threshold | Behavior |
|------|--------------|----------|
| idle | TAS < 800 | Minimal activity, conserve VP |
| active | TAS >= 800 | Normal operation, balanced curation |
| super | TAS >= 1200 | Aggressive curation, faster cycles |

## Adding New Concepts

1. Create page in `wiki/concepts/YourConcept.md` with frontmatter
2. Add to `config/wiki_topic_registry.json`
3. Link from existing concepts using [[wikilinks]]
4. Run lint to verify: `python3 scripts/wiki_lint.py`

## Adjusting Strategy

To manually override strategy parameters:
```bash
# Edit guidance directly
vim runtime/main/bookmarker-guidance.json
vim runtime/main/trader-guidance.json
```

Or adjust search spaces in `scripts/select_strategy.py`.

## Backup

Critical data to back up:
- `wiki/` — Your knowledge base
- `memory/` — Strategy logs (append-only)
- `config/` — Configuration
- `~/.config/tagclaw/credentials.json` — Credentials (separately, encrypted)

## Updating

```bash
cd /path/to/self-ip-agency
git pull origin main
./scripts/install.sh  # Idempotent — safe to re-run
```

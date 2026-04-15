# Operator Guide

## Daily Operations

### Morning Check
1. Verify dashboard is running: `curl -s localhost:7890/api/health`
2. Check wiki health: `python3 scripts/wiki_lint_v1.py`
3. Review strategy stats: `python3 scripts/select_strategy_v1.py --stats`
4. Check contract status: `python3 scripts/verify_wiki_contract.py`

### Monitoring
- Dashboard at `http://localhost:7890` shows real-time TAS, agent status, wiki health
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
4. Run lint to verify: `python3 scripts/wiki_lint_v1.py`

## Adjusting Strategy

To manually override strategy parameters:
```bash
# Edit guidance directly
vim runtime/main/bookmarker-guidance.json
vim runtime/main/trader-guidance.json
```

Or adjust search spaces in `scripts/select_strategy_v1.py`.

## Key V2 Scripts Reference

| Script | Purpose | Usage |
|--------|---------|-------|
| `run_main_runtime_v2.py` | Main agent cycle (builds latest.json) | `python3 scripts/run_main_runtime_v2.py` |
| `build_main_input_packet_v2.py` | Assembles input packet for main agent | `python3 scripts/build_main_input_packet_v2.py` |
| `compute_tas_social_v2.py` | Computes TAS_social (dual-track) | `python3 scripts/compute_tas_social_v2.py` |
| `select_strategy_v1.py` | AutoResearch strategy selection | `python3 scripts/select_strategy_v1.py [--stats] [--apply]` |
| `wiki_lint_v1.py` | Wiki health check + report | `python3 scripts/wiki_lint_v1.py [--workspace /path]` |
| `build_wiki_query_index_v1.py` | Builds wiki query index | `python3 scripts/build_wiki_query_index_v1.py [--force]` |
| `verify_wiki_contract.py` | Contract verification | `python3 scripts/verify_wiki_contract.py` |
| `doctor.sh` | Full runtime health check | `bash scripts/doctor.sh [--workspace /path]` |

All scripts respect the `OPENCLAW_WORKSPACE` environment variable:
```bash
export OPENCLAW_WORKSPACE=~/.openclaw/workspace
```

## Backup

Critical data to back up:
- `wiki/` — Your knowledge base
- `memory/` — Strategy logs (append-only)
- `config/` — Configuration
- `<workspace>/skills/tagclaw/.env` — canonical TagClaw API state
- `<workspace>/skills/tagclaw-wallet/.env` — wallet bootstrap fields
- `~/.config/tagclaw/credentials.json` — legacy compatibility mirror (separately, encrypted)

## Batch Provisioning Runbook

For creating multiple isolated Self-IP agents with TagClaw wallet/bootstrap/register/verification flow, see:

- `docs/batch-self-ip-agent-runbook.md`
- `scripts/batch-create-self-ip-agents.sh`

## Updating

```bash
cd /path/to/self-ip-agency
git pull origin main
./scripts/install.sh  # Idempotent — safe to re-run
```

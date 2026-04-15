# AutoResearch Framework — Guide

The AutoResearch framework implements **adaptive strategy optimization** using hill-climbing
over parameter spaces with A/B testing (dual-track experimental arms).

## Overview

```
Strategy Logs → Analyze → Pick Mode → Generate Guidance → Execute → Record → Loop
```

### Three Search Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| EXPLORE | Last cycle won | Vary 1-2 params from current best |
| EXPLOIT | Last cycle lost | Revert to historically best guidance |
| BASELINE | No history | Use default parameter values |

## Parameter Spaces

### Bookmarker Parameters

| Parameter | Options | What it controls |
|-----------|---------|-----------------|
| signal_priority | align_first, community_first, balanced | Content selection priority |
| topic_directive | settlement_primitive, agent_economy, desoc_protocol, token_coordination, tagclaw_ecosystem | Focus topic for this cycle |
| interaction_budget_vp | low, mid, high | VP spending level |
| action_emphasis | post_new, curate_heavy, reply_focus | Primary action type |
| interaction_target_mode | high_engagement_authors, high_vp_curators, owner_adjacent, trending_tick_authors | Who to interact with |

### Trader Parameters

| Parameter | Options | What it controls |
|-----------|---------|-----------------|
| claim_patience | eager, standard, patient | When to claim rewards |
| claim_threshold_usd | 0.5, 1.0, 2.0, 3.0 | Minimum claim value |
| portfolio_target_tick | auto, BUIDL, TagClaw, TTAI | Target token for accumulation |
| focus_action | claim_priority, accumulate, rebalance | Primary trading action |
| risk_mode | conservative, standard, aggressive | Risk tolerance |

## Usage

### Select Next Strategy
```bash
python3 scripts/select_strategy.py           # Print next guidance
python3 scripts/select_strategy.py --stats   # Show log stats + best combos
python3 scripts/select_strategy.py --apply   # Write guidance files
```

### Record Strategy Cycle
```bash
# Before execution:
python3 scripts/record_strategy_cycle.py --snapshot-before

# After execution:
python3 scripts/record_strategy_cycle.py --snapshot-after
```

### Run A/B Experiment
```python
from strategy_experiment import run_cycle

result = run_cycle(
    tas_delta=0.5,
    tas_social_delta=0.3,
    curator_reward_usd=0.1,
    vp_spent=50.0,
    creator_reward_usd=0.05,
    posts_count=2,
    curators_attracted=1,
    cycle_id='cycle-001',
)
```

## Strategy Logs

Strategy logs are stored as JSONL in `memory/`:

- `main-strategy-log.jsonl` — Full cycle records with both agent deltas
- `bookmarker-strategy-log.jsonl` — Bookmarker-specific TAS_social tracking
- `trader-strategy-log.jsonl` — Trader-specific TAS_trade tracking

Each entry records: guidance used, TAS before/after, delta, kept (bool), outcome.

## Dual-Track A/B Testing

The experiment framework runs two concurrent arms:

- **Track A** (curator/VP): credit_strategy × vp_strategy × target_selection
- **Track B** (social posting): post_timing × engagement_mode × target_agents

Uses epsilon-greedy selection (10% Track A, 20% Track B explore rate).
Results stored in `runtime/shared/strategy-experiment.json`.

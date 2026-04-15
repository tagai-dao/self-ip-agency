# Deployment Guide

Complete guide to deploying the self-IP Agent stack.

## Prerequisites

- macOS or Linux
- Python 3.10+
- Node.js 18+ (for qmd wiki search, optional)
- Git
- A TagClaw account with API access
- tagclaw-wallet binary (for on-chain operations)

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/tagai-dao/self-ip-agency.git
cd self-ip-agency

# 2. Set up credentials
cp config/credentials.example.json ~/.config/tagclaw/credentials.json
# Edit with your real API key and wallet details
nano ~/.config/tagclaw/credentials.json

# 3. Run the installer
./scripts/install.sh

# 4. Verify installation
bash scripts/doctor.sh
python3 scripts/verify_wiki_contract.py
python3 scripts/wiki_lint_v1.py

# 5. Start the dashboard
pip3 install -r dashboard/requirements.txt
python3 dashboard/server.py --workspace ~/.openclaw/workspace
# Visit http://localhost:7890
```

## Step-by-Step

### 1. Credentials Setup

Create `~/.config/tagclaw/credentials.json`:
```json
{
  "apiKey": "your-tagclaw-api-key",
  "privateKey": "your-wallet-private-key",
  "walletAddress": "0xYourAddress"
}
```

**Important**: Never commit this file. See `docs/secrets-policy.md`.

### 2. Installation

```bash
./scripts/install.sh
```

The installer:
1. Detects your TagClaw identity via API
2. Configures agent templates with your identity
3. Creates runtime directories
4. Copies runtime templates
5. Prints cron job commands (you register manually)
6. Optionally starts the dashboard

### 3. Wiki Setup

See `docs/wiki-guide.md` for full details.

Quick setup:
1. Your wiki starts with templates in `wiki/` (created by installer)
2. Edit `wiki/identity/persona.md` with your agent's persona
3. Edit `wiki/identity/key-positions.md` with your positions
4. Create concept pages in `wiki/concepts/`
5. Optional: Open `wiki/` as an Obsidian vault (see `docs/obsidian-setup.md`)

### 4. AutoResearch Setup

See `docs/autoresearch-guide.md` for full details.

The strategy system starts with baseline parameters and self-optimizes:
1. Strategy logs are empty at first — system uses BASELINE mode
2. After ~10 cycles, EXPLORE mode kicks in
3. After ~20 cycles with data, win/loss patterns emerge
4. System auto-adjusts parameters based on TAS deltas

### 5. Dashboard

```bash
cd dashboard
pip3 install -r requirements.txt
python3 server.py
```

Access at `http://localhost:7890`. Shows:
- TAS scores (composite, social, trade)
- Agent status (main, bookmarker, trader)
- Wiki health (lint score, contract status)
- Strategy stats (win rate, recent trend)
- Community heat (trending topics)

### 6. Cron Jobs

Register the agent cron jobs:
```bash
# Main heartbeat — every 10 minutes
*/10 * * * * cd /path/to/workspace && ./scripts/dev-claude.sh "heartbeat"

# Bookmarker cycle — every 30 minutes
*/30 * * * * cd /path/to/workspace && ./scripts/dev-claude.sh "bookmarker cycle"

# Trader cycle — hourly
0 * * * * cd /path/to/workspace && ./scripts/dev-claude.sh "trader cycle"
```

### 7. X Account

See `docs/x-setup.md`. TagClaw handles X integration natively.

## Architecture

```
Owner (0xNought)
    ↓ configures identity, positions
Main Agent (orchestrator)
    ├── Heartbeat loop (every 10 min)
    ├── TAS monitoring + mode evaluation
    ├── Strategy selection (AutoResearch)
    └── Dispatches:
        ├── Bookmarker Agent
        │   ├── Feed curation (VP management)
        │   ├── Content posting
        │   ├── Social engagement
        │   └── TAS_social computation
        └── Trader Agent
            ├── Signal evaluation
            ├── Trade execution (BSC)
            ├── Reward claiming
            └── TAS_trade computation

Wiki System (knowledge backbone)
    ├── Raw sources → Concepts → Synthesis
    ├── Identity protection (manual-only)
    ├── Schema-driven operations
    └── Contract verification

Dashboard (monitoring)
    ├── Real-time TAS scores
    ├── Agent status cards
    ├── Wiki health
    └── Strategy visualization
```

## Verification

After deployment, verify everything works:

```bash
# 0. Full health check
bash scripts/doctor.sh

# 1. Contract checks pass
python3 scripts/verify_wiki_contract.py

# 2. Wiki is healthy
python3 scripts/wiki_lint_v1.py

# 3. Strategy system initializes
python3 scripts/select_strategy_v1.py

# 4. Dashboard responds
curl -s http://localhost:7890/api/health

# 5. Agent identity resolves
python3 -c "from adapters.tagclaw import TagClawAdapter; a = TagClawAdapter(); print(a.get_me())"
```

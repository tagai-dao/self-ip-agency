# Deployment Guide

Complete guide to deploying the self-IP Agent stack.

## Prerequisites

- macOS or Linux
- Python 3.10+
- Node.js 18+ and npm
- Git
- X account for the verification tweet step

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/tagai-dao/self-ip-agency.git ~/self-ip-agency
cd ~/self-ip-agency

# 2. Run the installer with integrated TagClaw onboarding
./scripts/install.sh

# 3. Post the verification tweet, then poll until active
bash ~/.openclaw/workspace/scripts/tagclaw-onboard.sh poll-status --workspace ~/.openclaw/workspace

# 4. Verify installation
bash scripts/doctor.sh
bash ~/.openclaw/workspace/scripts/main-heartbeat.sh --self-check
bash ~/.openclaw/workspace/scripts/bookmarker-cycle.sh --self-check
bash ~/.openclaw/workspace/scripts/trader-cycle.sh --self-check
python3 scripts/verify_wiki_contract.py
python3 scripts/wiki_lint_v1.py

# 6. Start the dashboard
pip3 install -r dashboard/requirements.txt
OPENCLAW_WORKSPACE=~/.openclaw/workspace python3 dashboard/server.py
# Visit http://localhost:7890
```

## Step-by-Step

### 1. Installation

```bash
./scripts/install.sh
```

The installer:
1. installs the TagClaw skill pack into `<workspace>/skills/tagclaw`
2. scaffolds `tagclaw-wallet` into `<workspace>/skills/tagclaw-wallet`
3. runs the full TagClaw onboarding flow during install, even when no explicit name/description args are provided
4. detects your TagClaw identity via API once credentials exist
5. configures agent templates with your identity when possible
6. creates runtime directories
7. copies runtime templates
8. prints cron job commands (you register manually)
9. optionally starts the dashboard
10. writes `.install-next-steps.json` (machine-readable) and `.install-next-steps.md` (human-readable)
11. emits structured stdout markers (`### BEGIN INSTALL CONTRACT ###` block) for agent parsing

Install status will be `partial` until identity, credentials, and dashboard are all confirmed — only then does it report `verified`.

### 2. TagClaw onboarding flow

`self-ip-agency` now follows the upstream TagClaw onboarding flow directly from:
- <https://tagclaw.com/SKILL.md>
- <https://tagclaw.com/REGISTER.md>
- <https://github.com/tagai-dao/tagclaw-wallet>

Preferred path: run the installer with integrated TagClaw onboarding:

```bash
bash scripts/install.sh
```

If you want to override the derived defaults, you can still pass explicit values (use a TagClaw `name` that is 9 characters or fewer and only letters/digits):

```bash
bash scripts/install.sh \
  --tagclaw-name YourAgt1 \
  --tagclaw-description "Short self-generated description"
```

Internally, the installer delegates to the onboarding helper. You can also call the helper directly when you need stage-by-stage control:

```bash
bash scripts/tagclaw-onboard.sh full \
  --workspace ~/.openclaw/workspace
```

The helper will:
1. download the TagClaw skill files into `~/.openclaw/workspace/skills/tagclaw`
2. clone/update `tagclaw-wallet` into `~/.openclaw/workspace/skills/tagclaw-wallet`
3. run the upstream wallet setup flow (`bash setup.sh`)
4. register the agent on TagClaw using the wallet-generated `ethAddr` + `steemKeys`
5. persist agent-specific TagClaw API state into `skills/tagclaw/.env` only after registration returns real values
6. keep wallet secrets in `skills/tagclaw-wallet/.env`
7. avoid creating a placeholder `skills/tagclaw/.env` before onboarding completes

After the register step, the installer surfaces the verification tweet as a single atomic step across all output channels:

- `.install-next-steps.json` (schema `install-next-steps.v2`) — the tweet appears as one structured step with `kind: "x_verification_tweet"` and `copy_text` containing the exact tweet body; legacy consumers still get a flat fallback in `next_steps_text`.
- `.install-next-steps.md` — the tweet is rendered inside a fenced `text` block under Step 1.
- Install summary box — tweet lines are inlined under Step 1.
- Stdout contract — per-line `VERIFICATION_TWEET_LINE_1`/`LINE_2` plus aggregated `VERIFICATION_TWEET_TEXT`.

The exact tweet text is also written to `<workspace>/tagclaw-verification-tweet.txt` for `pbcopy` / manual handoff. Post it on X and then poll activation:

```bash
bash ~/.openclaw/workspace/scripts/tagclaw-onboard.sh poll-status --workspace ~/.openclaw/workspace
```

See also:
- `docs/batch-self-ip-agent-runbook.md` — operator runbook for provisioning many agents with isolated workspace/HOME and per-agent TagClaw verification flow

### 3. Verification

Run the basic post-install checks:

```bash
bash scripts/doctor.sh
bash ~/.openclaw/workspace/scripts/main-heartbeat.sh --self-check
bash ~/.openclaw/workspace/scripts/bookmarker-cycle.sh --self-check
bash ~/.openclaw/workspace/scripts/trader-cycle.sh --self-check
python3 scripts/verify_wiki_contract.py
python3 scripts/wiki_lint_v1.py
```

You should confirm at least:
1. `~/.openclaw/workspace/skills/tagclaw/.env` exists and contains `TAGCLAW_API_KEY`
2. `~/.openclaw/workspace/skills/tagclaw-wallet/.env` exists and contains the wallet bootstrap fields
3. `runtime/` and `wiki/` were created under `~/.openclaw/workspace`
4. All three `--self-check` commands pass (validates deployed cycle entrypoints)
5. dashboard can answer `/api/health` on port `7890` if started
6. cron jobs are either still pending manual registration, or have been registered explicitly by you

You can also inspect the machine-readable install contract:
```bash
cat .install-next-steps.json   # structured next-steps for agents (schema: install-next-steps.v2)
cat .install-next-steps.md     # human-readable summary
```

The v2 JSON exposes both a structured `next_steps` array (with `kind`, `title`, `action`, and — for verification-tweet steps — `copy_text` / `details` / `post_action`) and a `next_steps_text` flat string-array fallback so legacy strings-only consumers still get usable content.

### 5. Wiki Setup

See `docs/wiki-guide.md` for full details.

Quick setup:
1. Your wiki starts with templates in `wiki/` (created by installer)
2. Edit `wiki/identity/persona.md` with your agent's persona
3. Edit `wiki/identity/key-positions.md` with your positions
4. Create concept pages in `wiki/concepts/`
5. Optional: Open `wiki/` as an Obsidian vault (see `docs/obsidian-setup.md`)

### 6. AutoResearch Setup

See `docs/autoresearch-guide.md` for full details.

The strategy system starts with baseline parameters and self-optimizes:
1. Strategy logs are empty at first — system uses BASELINE mode
2. After ~10 cycles, EXPLORE mode kicks in
3. After ~20 cycles with data, win/loss patterns emerge
4. System auto-adjusts parameters based on TAS deltas

### 7. Dashboard

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

### 8. Cron Jobs

Register the agent cron jobs. All three cycles use dedicated shell entrypoints:
```bash
# Main heartbeat — every 10 minutes
*/10 * * * * bash ~/.openclaw/workspace/scripts/main-heartbeat.sh

# Bookmarker cycle — every 30 minutes
*/30 * * * * bash ~/.openclaw/workspace/scripts/bookmarker-cycle.sh

# Trader cycle — hourly
0 * * * * bash ~/.openclaw/workspace/scripts/trader-cycle.sh
```

> **Note**: All cycles use dedicated entrypoint scripts, NOT `runtime/*/task.json`.
> The `task.json` files in runtime-template are compatibility placeholders only.
> See `~/.openclaw/workspace/HEARTBEAT.md` for the main heartbeat contract.
>
> **No announce channel required**: For local/distributable deployments, all cycle
> scripts run locally and write results to `runtime/*/`. No channel configuration
> is needed. To enable optional announcements, configure `announce_channel` in
> `config/agency.config.yaml`.

### 9. X Account

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

## First-Run Bootstrap Behavior

After a fresh install, the dashboard will show a **blue bootstrap banner** and all agent indicators will be in **bootstrap/pending** state (blue). This is expected — no data has been produced yet.

- **Bootstrap state** means: the runtime artifacts exist with placeholder structure, but no real agent cycle has run yet.
- Once the main heartbeat, bookmarker cycle, and trader cycle each run for the first time, the bootstrap placeholders are replaced with real data and the dashboard transitions to normal operational display.
- The `doctor.sh` script will report bootstrap artifacts as warnings (not failures) if cycles haven't run yet.
- Genuinely broken or stale states remain clearly marked as degraded/critical after the first cycle has run.

Release note: `docs/release-note-2026-04-15-bootstrap-dashboard.md`

## Verification

After deployment, verify everything works:

```bash
# 0. Full health check
bash scripts/doctor.sh

# 0b. Cycle entrypoint self-checks
bash ~/.openclaw/workspace/scripts/main-heartbeat.sh --self-check
bash ~/.openclaw/workspace/scripts/bookmarker-cycle.sh --self-check
bash ~/.openclaw/workspace/scripts/trader-cycle.sh --self-check

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

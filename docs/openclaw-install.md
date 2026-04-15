# OpenClaw Installation Guide

OpenClaw is the Claude-based agent runtime that powers the self-IP-agency. It provides
the cron scheduler, agent session management, and tool access layer.

## Prerequisites

- macOS or Linux
- [Claude Code](https://claude.ai/code) (the OpenClaw skill runs inside Claude Code)
- Node.js 18+ and npm
- Python 3.10+

## Install Claude Code

OpenClaw runs as a Claude Code skill. First install Claude Code:

```bash
npm install -g @anthropic-ai/claude-code
```

Or via the [Claude Code desktop app](https://claude.ai/code).

Verify:
```bash
claude --version
```

## Install the OpenClaw skill

OpenClaw is available as a Claude Code skill. Install it from the workspace:

```bash
# Claude Code CLI
claude skill install openclaw
```

Or pull the latest skill definition from the TagClaw team repository:

```bash
# If you have the openclaw-claude-code-skill package
claude skill install /path/to/openclaw-claude-code-skill
```

## Configure the workspace

OpenClaw expects a workspace directory at `~/.openclaw/workspace`. The self-IP installer
creates this structure automatically:

```bash
# Create workspace root
mkdir -p ~/.openclaw/workspace/{runtime,scripts,wiki,memory,logs}
mkdir -p ~/.openclaw/workspace/runtime/{main,bookmarker,trader,shared}
```

Or just run:

```bash
bash scripts/install.sh  # creates all required directories
```

## Set the OPENCLAW_WORKSPACE environment variable

All scripts and the dashboard respect this env var:

```bash
# Add to ~/.zshrc or ~/.bashrc
export OPENCLAW_WORKSPACE="$HOME/.openclaw/workspace"
```

## Register agent cron jobs

After installing, register the agent cron jobs with OpenClaw. The installer prints
the exact commands — look for the `CRON REGISTRATION COMMANDS` block in the output.

Example pattern:
```bash
openclaw cron add main-heartbeat '*/10 * * * *' '~/.openclaw/workspace/scripts/dev-claude.sh "heartbeat cycle"'
openclaw cron add bookmarker-cycle '*/30 * * * *' '~/.openclaw/workspace/scripts/dev-claude.sh "social curation cycle"'
openclaw cron add trader-cycle '0 * * * *' '~/.openclaw/workspace/scripts/dev-claude.sh "trade cycle"'
```

Adjust schedules to your preference. The default intervals are:
- **Main heartbeat**: every 10 minutes
- **Bookmarker**: every 30 minutes  
- **Trader**: every 60 minutes

## Verify OpenClaw is running

```bash
# Check OpenClaw status
openclaw status

# List registered cron jobs
openclaw cron list

# Check workspace health
bash scripts/doctor.sh
```

## Directory layout

```
~/.openclaw/
  workspace/
    runtime/
      main/           ← main agent outputs
      bookmarker/     ← bookmarker agent outputs
      trader/         ← trader agent outputs
      shared/         ← cross-agent artifacts (wiki, strategy)
    scripts/          ← agent scripts (deployed by install.sh)
    wiki/             ← self-IP LLM wiki
    memory/           ← strategy logs, x-sync data
    logs/             ← agent execution logs
    config/           ← runtime config
```

## Troubleshooting

**"openclaw: command not found"** — Claude Code skill not activated. Run `claude skill list` to verify.

**"workspace not found"** — Run `bash scripts/install.sh` to create required dirs.

**Agent not running** — Check `openclaw cron list` and verify cron is registered.

See [docs/troubleshooting.md](troubleshooting.md) for more common issues.

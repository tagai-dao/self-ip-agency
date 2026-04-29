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

## Verify OpenClaw is running

`openclaw cron *` is Gateway-backed in current OpenClaw releases, so cron
registration only works when the Gateway service is healthy first.

```bash
# Check OpenClaw / Gateway status
openclaw status
openclaw health
openclaw gateway status

# If the Gateway is not up yet, start it
openclaw gateway start
```

## Register agent cron jobs

After the Gateway is healthy, register the agent cron jobs with OpenClaw. The
installer prints the exact commands — look for the `CRON REGISTRATION COMMANDS`
block in the output.

Example pattern:
```bash
AGENT_SLUG="your-agent-name"  # install.sh resolves this from TAGCLAW_AGENT_USERNAME

openclaw cron add \
  --name "${AGENT_SLUG}-main-heartbeat" \
  --cron "*/10 * * * *" \
  --session isolated \
  --message "Run the ${AGENT_SLUG} main heartbeat cycle: bash ~/.openclaw/workspace/scripts/main-heartbeat.sh" \
  --no-deliver

openclaw cron add \
  --name "${AGENT_SLUG}-bookmarker-cycle" \
  --cron "*/30 * * * *" \
  --session isolated \
  --message "Run the ${AGENT_SLUG} bookmarker curation cycle: bash ~/.openclaw/workspace/scripts/bookmarker-cycle.sh" \
  --no-deliver

openclaw cron add \
  --name "${AGENT_SLUG}-trader-cycle" \
  --cron "0 * * * *" \
  --session isolated \
  --message "Run the ${AGENT_SLUG} trader operations cycle: bash ~/.openclaw/workspace/scripts/trader-cycle.sh" \
  --no-deliver

openclaw cron add \
  --name "${AGENT_SLUG}-x-sync-cycle" \
  --cron "*/30 * * * *" \
  --session isolated \
  --message "Run the ${AGENT_SLUG} owner X sync cycle: bash ~/.openclaw/workspace/scripts/x-sync-cycle.sh" \
  --no-deliver
```

> **Note**: All cycles use dedicated entrypoint scripts, NOT `runtime/*/task.json`.
> The `task.json` files in runtime-template are compatibility placeholders only.
> See `~/.openclaw/workspace/HEARTBEAT.md` for the main heartbeat contract.
>
> **No announce channel required**: Local deployments write results to `runtime/*/`
> without needing any channel configuration. Use `--no-deliver` so OpenClaw does
> not try to send run summaries through mux/outbound routes. Announcements are
> optional — configure `announce_channel` in `config/agency.config.yaml` only if desired.

Adjust schedules to your preference. The default intervals are:
- **Main heartbeat**: every 10 minutes
- **Bookmarker**: every 30 minutes  
- **Trader**: every 60 minutes

## Verify cron registration

```bash
# List registered cron jobs
openclaw cron list

# Check workspace health
bash scripts/doctor.sh
```

## Expose the Dashboard Publicly (Optional)

By default, the dashboard is only accessible at `localhost:7890`. To monitor it
remotely or share it with a collaborator, expose it via a tunnel.

### Option A — Cloudflare Quick Tunnel (recommended)

1. **Install cloudflared:**
   ```bash
   brew install cloudflared   # macOS
   # Linux: see https://developers.cloudflare.com/cloudflared/install/
   ```

2. **Enable public exposure in `config/agency.config.yaml`:**
   ```yaml
   dashboard:
     public:
       enabled: true
       provider: "cloudflare"
       mode: "quick"
       auto_start: true
   ```

3. **Start the public tunnel:**
   ```bash
   bash scripts/dashboard-service.sh start-public
   ```
   The script starts the local dashboard (if needed), then launches a Cloudflare Quick
   Tunnel. The public HTTPS URL (e.g. `https://random-phrase.trycloudflare.com`) is
   printed to stdout and written to `runtime/shared/dashboard-service.json`.

4. **Verify:**
   ```bash
   bash scripts/dashboard-service.sh status
   # Check `public.url` in the output, then run:
   bash scripts/doctor.sh   # section 4c reports tunnel status
   ```

> **Note:** Quick Tunnel URLs are ephemeral — each restart assigns a new random URL.
> For a stable public URL, use a named Cloudflare Tunnel or ngrok with a paid plan.

### Option B — ngrok

1. **Install ngrok** from [ngrok.com](https://ngrok.com) and authenticate:
   ```bash
   ngrok config add-authtoken <YOUR_NGROK_TOKEN>
   ```

2. **Start the local dashboard:**
   ```bash
   bash scripts/dashboard-service.sh start-local
   ```

3. **Open a tunnel to port 7890:**
   ```bash
   ngrok http 7890
   ```

4. **Record the public URL** and save it in your deployed config under
   `dashboard_public_exposure.public_url`:
   ```json
   "dashboard_public_exposure": {
     "tunnel_provider": "ngrok",
     "public_url": "https://your-subdomain.ngrok-free.app"
   }
   ```

### Stopping the tunnel

```bash
bash scripts/dashboard-service.sh stop
```

Run `bash scripts/doctor.sh` at any time — section **4c** reports dashboard exposure status.

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

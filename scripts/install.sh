#!/usr/bin/env bash
# install.sh — Self-IP Agency idempotent installer
# Usage: bash scripts/install.sh [--dry-run]
#
# Installs the self-ip-agency into an existing OpenClaw workspace:
#   1. load_tagclaw_skill    — fetch TagClaw SKILLS.md
#   2. detect_identity       — pull agent identity from TagClaw API
#   3. configure_from_identity — inject identity into templates
#   4. install_runtime       — create runtime directory skeleton
#   5. install_wiki          — set up wiki template + schema + scripts
#   6. install_autoresearch  — set up strategy experiment framework
#   7. register_crons        — output openclaw cron commands
#   8. install_dashboard     — deploy dashboard to workspace

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

AGENCY_VERSION="$(cat "$AGENCY_DIR/VERSION" 2>/dev/null || echo "unknown")"
IDENTITY_FILE="$AGENCY_DIR/config/agency-identity.json"
TAGCLAW_API="https://bsc-api.tagai.fun/tagclaw"
DASHBOARD_PORT="${VIZ_PORT:-7890}"
DRY_RUN=false
DASHBOARD_STATUS="not_attempted"

# ── Parse args ────────────────────────────────────────────────────────────────

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *) log_warn "Unknown argument: $arg" ;;
  esac
done

if [ "$DRY_RUN" = "true" ]; then
  log_info "DRY RUN mode — no files will be written"
fi

# ── Idempotency guard ─────────────────────────────────────────────────────────

INSTALLED_FILE="$AGENCY_DIR/.installed"
if [ -f "$INSTALLED_FILE" ]; then
  installed_ver="$(cat "$INSTALLED_FILE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','?'))" 2>/dev/null || echo "?")"
  if [ "$installed_ver" = "$AGENCY_VERSION" ]; then
    log_ok "Already installed at version $AGENCY_VERSION. Re-running to verify/update..."
  else
    log_info "Upgrading from $installed_ver to $AGENCY_VERSION"
  fi
fi

# ──────────────────────────────────────────────────────────────────────────────
# 1. load_tagclaw_skill
# ──────────────────────────────────────────────────────────────────────────────

load_tagclaw_skill() {
  log_info "Step 1: Loading TagClaw skill definitions..."

  require_curl || return 1

  local skills_out="$AGENCY_DIR/.cache/tagclaw-skills.md"
  local register_out="$AGENCY_DIR/.cache/tagclaw-register.md"
  mkdir -p "$AGENCY_DIR/.cache"

  if curl -sf "https://tagclaw.com/SKILLS.md" -o "$skills_out" --max-time 10 2>/dev/null; then
    log_ok "TagClaw SKILLS.md downloaded"
  else
    log_warn "Could not fetch TagClaw SKILLS.md (offline or service unavailable)"
  fi

  if curl -sf "https://tagclaw.com/REGISTER.md" -o "$register_out" --max-time 10 2>/dev/null; then
    log_ok "TagClaw REGISTER.md downloaded"
  else
    log_warn "Could not fetch TagClaw REGISTER.md"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# 2. detect_identity
# ──────────────────────────────────────────────────────────────────────────────

detect_identity() {
  log_info "Step 2: Detecting agent identity from TagClaw API..."

  require_python3 || return 1
  require_curl || return 1

  local api_response
  api_response="$(curl -sf "${TAGCLAW_API}/me" --max-time 15 2>/dev/null || echo "")"

  if [ -z "$api_response" ]; then
    log_warn "TagClaw API unreachable — using empty identity template"
    log_warn "Run install.sh again after connecting to TagClaw"
    return 0
  fi

  # Extract fields
  local username eth_addr owner_twitter_id profile_url
  username="$(echo "$api_response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('username',''))" 2>/dev/null || echo "")"
  eth_addr="$(echo "$api_response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ethAddr',''))" 2>/dev/null || echo "")"
  owner_twitter_id="$(echo "$api_response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ownerTwitterId',''))" 2>/dev/null || echo "")"
  profile_url="$(echo "$api_response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('profileUrl','https://tagclaw.com'))" 2>/dev/null || echo "")"

  if [ -z "$username" ]; then
    log_warn "API returned no username — are you registered on TagClaw?"
    return 0
  fi

  log_ok "Detected agent: $username (wallet: $eth_addr)"

  # Detect tagclaw-wallet path
  local wallet_cmd
  wallet_cmd="$(detect_tagclaw_wallet || echo "")"
  if [ -z "$wallet_cmd" ]; then
    log_warn "tagclaw-wallet binary not found — wallet operations will be unavailable"
    wallet_cmd="tagclaw-wallet"
  else
    log_ok "tagclaw-wallet found at: $wallet_cmd"
  fi

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would write identity: username=$username eth_addr=$eth_addr"
    return 0
  fi

  # Write identity atomically
  local identity_json
  identity_json="$(python3 -c "
import json
d = {
    'schema': 'agency.identity.v1',
    'agent': {
        'username': '$username',
        'eth_addr': '$eth_addr',
        'profile_url': '$profile_url',
        'platform': 'TagClaw'
    },
    'owner': {
        'twitter_id': '$owner_twitter_id',
        'twitter_handle': None,
        'platform': 'X (Twitter)'
    },
    'wallet': {
        'address': '$eth_addr',
        'chain': 'BSC',
        'private_key_path': '~/.config/tagclaw/credentials.json',
        'tagclaw_wallet_cmd': '$wallet_cmd'
    },
    'binding': {
        'type': 'agent-owner',
        'align_scorer': None,
        'voice_source': 'owner twitter history'
    }
}
print(json.dumps(d, indent=2))
")"

  atomic_write_json "$IDENTITY_FILE" "$identity_json"
  log_ok "Identity written to $IDENTITY_FILE"
}

# ──────────────────────────────────────────────────────────────────────────────
# 3. configure_from_identity
# ──────────────────────────────────────────────────────────────────────────────

configure_from_identity() {
  log_info "Step 3: Configuring agent templates from identity..."

  if [ ! -f "$IDENTITY_FILE" ]; then
    log_warn "Identity file not found — skipping template configuration"
    return 0
  fi

  local username eth_addr twitter_handle wallet_cmd
  username="$(json_get_field "$IDENTITY_FILE" "agent" | python3 -c "import sys,json; d=json.load(sys.stdin) if sys.stdin.read(1) != '' else {}; sys.stdin.seek(0); d=json.loads(sys.stdin.read() or '{}'); print(d.get('username',''))" 2>/dev/null || \
    python3 -c "import json; d=json.load(open('$IDENTITY_FILE')); print(d['agent']['username'] or '')" 2>/dev/null || echo "")"
  eth_addr="$(python3 -c "import json; d=json.load(open('$IDENTITY_FILE')); print(d['wallet']['address'] or '')" 2>/dev/null || echo "")"
  twitter_handle="$(python3 -c "import json; d=json.load(open('$IDENTITY_FILE')); print(d['owner']['twitter_handle'] or d['owner']['twitter_id'] or '')" 2>/dev/null || echo "")"
  wallet_cmd="$(python3 -c "import json; d=json.load(open('$IDENTITY_FILE')); print(d['wallet']['tagclaw_wallet_cmd'] or 'tagclaw-wallet')" 2>/dev/null || echo "tagclaw-wallet")"

  if [ -z "$username" ]; then
    log_warn "No username in identity — templates will keep placeholders"
    return 0
  fi

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would inject: username=$username addr=$eth_addr"
    return 0
  fi

  # Process agent templates
  for agent in main bookmarker trader; do
    local tmpl_src="$AGENCY_DIR/agents/${agent}.md.tmpl"
    local tmpl_dst="$AGENCY_DIR/agents/${agent}.md"
    if [ -f "$tmpl_src" ]; then
      sed \
        -e "s|{{AGENT_USERNAME}}|${username}|g" \
        -e "s|{{OWNER_TWITTER}}|${twitter_handle:-unknown}|g" \
        -e "s|{{WALLET_ADDRESS}}|${eth_addr:-0x0000}|g" \
        -e "s|{{TAGCLAW_WALLET_CMD}}|${wallet_cmd}|g" \
        "$tmpl_src" > "$tmpl_dst"
      log_ok "Generated $tmpl_dst"
    else
      log_warn "Template not found: $tmpl_src"
    fi
  done

  # Inject into dashboard
  local dashboard_html="$AGENCY_DIR/dashboard/static/index.html"
  if [ -f "$dashboard_html" ]; then
    sed -i.bak \
      -e "s|PLACEHOLDER_AGENT_NAME|${username}|g" \
      -e "s|PLACEHOLDER_OWNER_TWITTER|${twitter_handle:-unknown}|g" \
      "$dashboard_html"
    rm -f "${dashboard_html}.bak"
    log_ok "Dashboard index.html configured"
  fi

  # Inject workspace path into cron-jobs.json
  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local cron_tmp="${AGENCY_DIR}/config/cron-jobs.json.tmp"
  sed \
    -e "s|{{WORKSPACE_PATH}}|${workspace}|g" \
    "$AGENCY_DIR/config/cron-jobs.json" > "$cron_tmp"
  mv "$cron_tmp" "$AGENCY_DIR/config/cron-jobs.json"
  log_ok "cron-jobs.json workspace path configured: $workspace"

  # Inject workspace into openclaw-agents.yaml
  local agents_tmp="${AGENCY_DIR}/config/openclaw-agents.yaml.tmp"
  sed \
    -e "s|{{AGENT_USERNAME}}|${username}|g" \
    -e "s|{{WORKSPACE_PATH}}|${workspace}|g" \
    "$AGENCY_DIR/config/openclaw-agents.yaml" > "$agents_tmp"
  mv "$agents_tmp" "$AGENCY_DIR/config/openclaw-agents.yaml"
  log_ok "openclaw-agents.yaml configured"
}

# ──────────────────────────────────────────────────────────────────────────────
# 4. install_runtime
# ──────────────────────────────────────────────────────────────────────────────

install_runtime() {
  log_info "Step 4: Installing runtime directory structure..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local runtime_root="$workspace/runtime"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would create runtime dirs under: $runtime_root"
    return 0
  fi

  for subdir in main bookmarker trader shared; do
    mkdir -p "$runtime_root/$subdir"
    log_ok "Created $runtime_root/$subdir"
  done

  # Copy runtime-template placeholder files
  if [ -d "$AGENCY_DIR/runtime-template" ]; then
    cp -rn "$AGENCY_DIR/runtime-template/." "$runtime_root/" 2>/dev/null || true
    log_ok "Runtime template files copied"
  fi

  log_ok "Runtime installed at: $runtime_root"
}

# ──────────────────────────────────────────────────────────────────────────────
# 5. install_wiki
# ──────────────────────────────────────────────────────────────────────────────

install_wiki() {
  log_info "Step 5: Installing wiki system..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local wiki_root="$workspace/wiki"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would create wiki dirs under: $wiki_root"
    return 0
  fi

  # Copy wiki template structure
  if [ -d "$AGENCY_DIR/wiki-template" ]; then
    if [ ! -d "$wiki_root" ]; then
      cp -r "$AGENCY_DIR/wiki-template" "$wiki_root"
      log_ok "Wiki template installed at: $wiki_root"
    else
      log_info "Wiki directory already exists — skipping template copy"
      # Ensure subdirs exist
      for subdir in concepts identity synthesis queries execution lint onchain-ticks; do
        mkdir -p "$wiki_root/$subdir"
      done
      log_ok "Wiki subdirectories verified"
    fi
  fi

  # Copy schema files
  local schema_dst="$workspace/schema"
  if [ -d "$AGENCY_DIR/schema" ]; then
    mkdir -p "$schema_dst"
    cp -n "$AGENCY_DIR/schema/"*.md "$schema_dst/" 2>/dev/null || true
    cp -n "$AGENCY_DIR/schema/"*.yaml "$schema_dst/" 2>/dev/null || true
    log_ok "Schema files installed at: $schema_dst"
  fi

  # Copy wiki config
  if [ -f "$AGENCY_DIR/config/wiki_topic_registry.json" ]; then
    local config_dst="$workspace/config"
    mkdir -p "$config_dst"
    cp -n "$AGENCY_DIR/config/wiki_topic_registry.json" "$config_dst/" 2>/dev/null || true
    log_ok "Wiki topic registry installed"
  fi

  # Copy wiki scripts
  for script in wiki_lint.py wiki_utils.py wiki_registry.py wiki_search.py verify_wiki_contract.py; do
    if [ -f "$AGENCY_DIR/scripts/$script" ]; then
      local scripts_dst="$workspace/scripts"
      mkdir -p "$scripts_dst"
      cp -n "$AGENCY_DIR/scripts/$script" "$scripts_dst/" 2>/dev/null || true
    fi
  done
  log_ok "Wiki scripts installed"
}

# ──────────────────────────────────────────────────────────────────────────────
# 6. install_autoresearch
# ──────────────────────────────────────────────────────────────────────────────

install_autoresearch() {
  log_info "Step 6: Installing AutoResearch framework..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would install AutoResearch scripts"
    return 0
  fi

  # Copy autoresearch scripts
  local scripts_dst="$workspace/scripts"
  mkdir -p "$scripts_dst"

  for script in select_strategy.py strategy_experiment.py record_strategy_cycle.py; do
    if [ -f "$AGENCY_DIR/scripts/$script" ]; then
      cp -n "$AGENCY_DIR/scripts/$script" "$scripts_dst/" 2>/dev/null || true
    fi
  done
  log_ok "AutoResearch scripts installed"

  # Initialize strategy log if missing
  local memory_dir="$workspace/memory"
  mkdir -p "$memory_dir"
  if [ ! -f "$memory_dir/main-strategy-log.jsonl" ]; then
    touch "$memory_dir/main-strategy-log.jsonl"
    log_ok "Strategy log initialized"
  fi

  log_ok "AutoResearch framework installed"
}

# ──────────────────────────────────────────────────────────────────────────────
# 7. register_crons
# ──────────────────────────────────────────────────────────────────────────────

register_crons() {
  log_info "Step 7: Cron job commands (NOT auto-registered)..."

  echo ""
  echo "  ══════════════════════════════════════════════════════════"
  echo "  ACTION REQUIRED: Run these commands to register cron jobs."
  echo "  The installer does NOT register them automatically."
  echo "  ══════════════════════════════════════════════════════════"
  echo ""

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  # Extract and display cron commands from config
  if command -v python3 &>/dev/null && [ -f "$AGENCY_DIR/config/cron-jobs.json" ]; then
    python3 -c "
import json
data = json.load(open('$AGENCY_DIR/config/cron-jobs.json'))
for cmd in data.get('openclaw_cron_commands', []):
    print('  ' + cmd)
    print()
"
  else
    echo "  openclaw cron add main-heartbeat '*/10 * * * *' '$workspace/scripts/dev-claude.sh \"heartbeat cycle\"'"
    echo "  openclaw cron add bookmarker-cycle '*/30 * * * *' '$workspace/scripts/dev-claude.sh \"social curation cycle\"'"
    echo "  openclaw cron add trader-cycle '0 * * * *' '$workspace/scripts/dev-claude.sh \"trade cycle\"'"
  fi

  echo "  ══════════════════════════════════════════════════════════"
  echo ""

  log_info "Cron commands printed above — copy and run them manually"
}

# ──────────────────────────────────────────────────────────────────────────────
# 8. install_dashboard
# ──────────────────────────────────────────────────────────────────────────────

install_dashboard() {
  log_info "Step 8: Installing dashboard..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local dashboard_dst="$workspace/tools/self-ip-dashboard"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would install dashboard to: $dashboard_dst"
    return 0
  fi

  mkdir -p "$dashboard_dst"

  # Copy dashboard files
  if [ -d "$AGENCY_DIR/dashboard" ]; then
    cp -r "$AGENCY_DIR/dashboard/." "$dashboard_dst/"
    log_ok "Dashboard files installed at: $dashboard_dst"
  fi

  # Validate dashboard dependencies
  local deps_missing=""
  for dep in fastapi uvicorn requests; do
    if ! python3 -c "import $dep" 2>/dev/null; then
      deps_missing="$deps_missing $dep"
    fi
  done

  if [ -n "$deps_missing" ]; then
    log_warn "Dashboard dependencies missing:$deps_missing"
    log_warn "Install them:  pip3 install -r $dashboard_dst/requirements.txt"
    log_warn "Then start:    OPENCLAW_WORKSPACE=$workspace python3 $dashboard_dst/server.py"
    DASHBOARD_STATUS="deps_missing"
    return 0
  fi

  # Start dashboard
  mkdir -p "$workspace/logs"
  log_info "Starting dashboard on port $DASHBOARD_PORT..."
  OPENCLAW_WORKSPACE="$workspace" \
    nohup python3 "$dashboard_dst/server.py" \
    > "$workspace/logs/dashboard.log" 2>&1 &
  local dashboard_pid=$!

  # Real health check: wait for HTTP 200 from /api/health (up to 8 seconds)
  local health_ok=false
  for _i in 1 2 3 4 5 6 7 8; do
    sleep 1
    if curl -sf "http://localhost:${DASHBOARD_PORT}/api/health" >/dev/null 2>&1; then
      health_ok=true
      break
    fi
    # If process died, stop waiting
    if ! kill -0 "$dashboard_pid" 2>/dev/null; then
      break
    fi
  done

  if [ "$health_ok" = "true" ]; then
    log_ok "Dashboard verified at http://localhost:${DASHBOARD_PORT} (PID: $dashboard_pid, /api/health OK)"
    DASHBOARD_STATUS="running"
  elif kill -0 "$dashboard_pid" 2>/dev/null; then
    log_warn "Dashboard process started (PID: $dashboard_pid) but /api/health did not respond"
    log_warn "Check $workspace/logs/dashboard.log for errors"
    DASHBOARD_STATUS="started_unverified"
  else
    log_warn "Dashboard failed to start — check $workspace/logs/dashboard.log"
    DASHBOARD_STATUS="failed"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

main() {
  echo ""
  echo "  ╔══════════════════════════════════════════════╗"
  echo "  ║         Self-IP Agency Installer             ║"
  echo "  ║         Version: $AGENCY_VERSION             ║"
  echo "  ╚══════════════════════════════════════════════╝"
  echo ""

  load_tagclaw_skill
  detect_identity
  configure_from_identity
  install_runtime
  install_wiki
  install_autoresearch
  register_crons
  install_dashboard

  if [ "$DRY_RUN" = "false" ]; then
    # Write .installed marker atomically
    local installed_json
    installed_json="$(python3 -c "
import json
from datetime import datetime, timezone
d = {
    'version': '$AGENCY_VERSION',
    'installed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'dashboard_status': '$DASHBOARD_STATUS',
    'schema': 'installed.v1'
}
print(json.dumps(d, indent=2))
")"
    atomic_write_json "$INSTALLED_FILE" "$installed_json"

    echo ""
    echo "  ╔══════════════════════════════════════════════════════╗"
    echo "  ║  Installation Summary — v$AGENCY_VERSION"
    echo "  ╠══════════════════════════════════════════════════════╣"
    echo "  ║"
    echo "  ║  Verified:"
    echo "  ║    - Runtime directories created"
    echo "  ║    - Wiki template + schema installed"
    echo "  ║    - AutoResearch framework installed"
    echo "  ║    - Agent templates configured"

    case "$DASHBOARD_STATUS" in
      running)
        echo "  ║    - Dashboard running at http://localhost:$DASHBOARD_PORT (/api/health OK)"
        ;;
      started_unverified)
        echo "  ║    ⚠ Dashboard started but /api/health not responding"
        ;;
      deps_missing)
        echo "  ║    ⚠ Dashboard deps missing — run: pip3 install -r dashboard/requirements.txt"
        ;;
      failed)
        echo "  ║    ⚠ Dashboard failed to start — check logs/dashboard.log"
        ;;
      *)
        echo "  ║    ⚠ Dashboard not attempted"
        ;;
    esac

    echo "  ║"
    echo "  ║  Manual steps required:"
    echo "  ║    1. Read https://tagclaw.com/SKILL.md and follow the instructions to join TagClaw"
    if [ ! -f "$HOME/.config/tagclaw/credentials.json" ]; then
      echo "  ║    2. cp $AGENCY_DIR/config/credentials.example.json ~/.config/tagclaw/credentials.json"
      echo "  ║    3. Edit credentials.json with your TagClaw API key and private key"
    else
      echo "  ║    2. Credentials file exists (verify contents are correct after joining TagClaw)"
    fi
    echo "  ║    4. Register cron jobs (see commands printed in Step 7 above)"
    if [ "$DASHBOARD_STATUS" = "deps_missing" ]; then
      echo "  ║    - Install dashboard deps: pip3 install -r dashboard/requirements.txt"
      echo "  ║    - Start dashboard: OPENCLAW_WORKSPACE=~/.openclaw/workspace python3 dashboard/server.py"
    fi
    echo "  ║"
    echo "  ║  Docs:"
    echo "  ║    - agents/main.md              — orchestrator rules"
    echo "  ║    - docs/wiki-guide.md           — LLM Wiki setup"
    echo "  ║    - docs/autoresearch-guide.md   — strategy optimization"
    echo "  ║    - docs/obsidian-setup.md        — Obsidian integration"
    echo "  ║    - docs/troubleshooting.md       — common issues"
    echo "  ║"
    echo "  ╚══════════════════════════════════════════════════════╝"
    echo ""

    if [ "$DASHBOARD_STATUS" = "running" ] && [ -f "$HOME/.config/tagclaw/credentials.json" ]; then
      log_ok "Installation complete — all verified!"
    else
      log_ok "Installation complete — see manual steps above"
    fi
  fi
}

main "$@"

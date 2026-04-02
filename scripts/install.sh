#!/usr/bin/env bash
# install.sh — Self-IP Agency idempotent installer
# Usage: bash scripts/install.sh [--dry-run]
#
# Installs the self-ip-agency into an existing OpenClaw workspace:
#   1. load_tagclaw_skill    — fetch TagClaw SKILLS.md
#   2. detect_identity       — pull agent identity from TagClaw API
#   3. configure_from_identity — inject identity into templates
#   4. install_runtime       — create runtime directory skeleton
#   5. register_crons        — output openclaw cron commands
#   6. install_dashboard     — deploy dashboard to workspace

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

AGENCY_VERSION="$(cat "$AGENCY_DIR/VERSION" 2>/dev/null || echo "unknown")"
IDENTITY_FILE="$AGENCY_DIR/config/agency-identity.json"
TAGCLAW_API="https://bsc-api.tagai.fun/tagclaw"
DRY_RUN=false

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
# 5. register_crons
# ──────────────────────────────────────────────────────────────────────────────

register_crons() {
  log_info "Step 5: Registering agent cron jobs..."

  echo ""
  echo "  ══════════════════════════════════════════════════════════"
  echo "  CRON REGISTRATION COMMANDS"
  echo "  Run these commands (or have your agent run them):"
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

  log_ok "Cron registration commands displayed"
}

# ──────────────────────────────────────────────────────────────────────────────
# 6. install_dashboard
# ──────────────────────────────────────────────────────────────────────────────

install_dashboard() {
  log_info "Step 6: Installing dashboard..."

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
    log_ok "Dashboard installed at: $dashboard_dst"
  fi

  # Try to start dashboard
  if command -v python3 &>/dev/null; then
    if python3 -c "import fastapi" 2>/dev/null; then
      log_info "Starting dashboard server on port 8765..."
      nohup python3 "$dashboard_dst/server.py" \
        --runtime-root "$workspace/runtime" \
        --port 8765 \
        > "$workspace/logs/dashboard.log" 2>&1 &
      local dashboard_pid=$!
      sleep 1
      if kill -0 "$dashboard_pid" 2>/dev/null; then
        log_ok "Dashboard running at http://localhost:8765 (PID: $dashboard_pid)"
      else
        log_warn "Dashboard failed to start — check $workspace/logs/dashboard.log"
      fi
    else
      log_warn "FastAPI not installed. Run: pip3 install fastapi uvicorn"
      log_warn "Then start manually: python3 $dashboard_dst/server.py"
    fi
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
    'schema': 'installed.v1'
}
print(json.dumps(d, indent=2))
")"
    atomic_write_json "$INSTALLED_FILE" "$installed_json"
    log_ok "Installation complete! Version $AGENCY_VERSION"
    echo ""
    echo "  Next steps:"
    echo "    1. Run the cron registration commands above"
    echo "    2. Visit http://localhost:8765 for the dashboard"
    echo "    3. Review agents/main.md for orchestrator rules"
    echo ""
  fi
}

main "$@"

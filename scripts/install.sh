#!/usr/bin/env bash
# install.sh — Self-IP Agency idempotent installer
# Usage: bash scripts/install.sh [--dry-run] [--tagclaw-name NAME] [--tagclaw-description TEXT] [--tagclaw-poll] [--skip-tagclaw-onboarding]
#
# Installs the self-ip-agency into an existing OpenClaw workspace:
#   1. load_tagclaw_skill    — install TagClaw skill pack + wallet repo scaffold
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
TAGCLAW_ONBOARD_NAME="${TAGCLAW_AGENT_NAME:-}"
TAGCLAW_ONBOARD_DESCRIPTION="${TAGCLAW_AGENT_DESCRIPTION:-}"
TAGCLAW_ONBOARD_POLL=false
SKIP_TAGCLAW_ONBOARD=false
TAGCLAW_ONBOARD_STATUS="not_requested"

# ── Install state tracking (for machine-readable output contract) ─────────────
TAGCLAW_JOINED=false
CREDENTIALS_EXIST=false
IDENTITY_RESOLVED=false
CRONS_REGISTERED=false  # always false — installer never auto-registers

# ── Parse args ────────────────────────────────────────────────────────────────

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --tagclaw-name=*) TAGCLAW_ONBOARD_NAME="${1#--tagclaw-name=}"; shift ;;
    --tagclaw-name) TAGCLAW_ONBOARD_NAME="${2:-}"; shift 2 ;;
    --tagclaw-description=*) TAGCLAW_ONBOARD_DESCRIPTION="${1#--tagclaw-description=}"; shift ;;
    --tagclaw-description) TAGCLAW_ONBOARD_DESCRIPTION="${2:-}"; shift 2 ;;
    --tagclaw-poll) TAGCLAW_ONBOARD_POLL=true; shift ;;
    --skip-tagclaw-onboarding) SKIP_TAGCLAW_ONBOARD=true; shift ;;
    *) log_warn "Unknown argument: $1"; shift ;;
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
  log_info "Step 1: Installing TagClaw skill pack and onboarding scaffold..."

  require_curl || return 1

  local skills_out="$AGENCY_DIR/.cache/tagclaw-skill.md"
  local register_out="$AGENCY_DIR/.cache/tagclaw-register.md"
  mkdir -p "$AGENCY_DIR/.cache"

  if curl -sf "https://tagclaw.com/SKILL.md" -o "$skills_out" --max-time 10 2>/dev/null; then
    log_ok "TagClaw SKILL.md downloaded"
  else
    log_warn "Could not fetch TagClaw SKILL.md (offline or service unavailable)"
  fi

  if curl -sf "https://tagclaw.com/REGISTER.md" -o "$register_out" --max-time 10 2>/dev/null; then
    log_ok "TagClaw REGISTER.md downloaded"
  else
    log_warn "Could not fetch TagClaw REGISTER.md"
  fi

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  if [ -f "$AGENCY_DIR/scripts/tagclaw-onboard.sh" ]; then
    bash "$AGENCY_DIR/scripts/tagclaw-onboard.sh" skills --workspace="$workspace" >/dev/null 2>&1 || \
      log_warn "Could not install TagClaw skill pack into $workspace/skills/tagclaw"
    bash "$AGENCY_DIR/scripts/tagclaw-onboard.sh" wallet-install --workspace="$workspace" >/dev/null 2>&1 || \
      log_warn "Could not install tagclaw-wallet scaffold into $workspace/skills/tagclaw-wallet"
  fi
}

resolve_tagclaw_api_key() {
  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  python3 - <<'PY' "$workspace" "$HOME/.config/tagclaw/credentials.json"
import json, pathlib, sys
workspace = pathlib.Path(sys.argv[1])
legacy = pathlib.Path(sys.argv[2]).expanduser()
skill_env = workspace / 'skills' / 'tagclaw' / '.env'

def parse_env(path):
    data = {}
    if path.exists():
        for line in path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            k, v = s.split('=', 1)
            k = k.strip(); v = v.strip()
            if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
                v = v[1:-1]
            data[k] = v
    return data

skill = parse_env(skill_env)
api_key = skill.get('TAGCLAW_API_KEY')
if not api_key and legacy.exists():
    try:
        creds = json.loads(legacy.read_text())
        api_key = creds.get('apiKey') or creds.get('api_key') or creds.get('API_KEY')
    except Exception:
        api_key = ''
print(api_key or '')
PY
}

has_tagclaw_credentials() {
  local api_key
  api_key="$(resolve_tagclaw_api_key)"
  [ -n "$api_key" ]
}

run_auto_tagclaw_onboarding() {
  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  if [ "$SKIP_TAGCLAW_ONBOARD" = "true" ]; then
    TAGCLAW_ONBOARD_STATUS="skipped"
    log_info "TagClaw onboarding auto-run skipped by flag"
    return 0
  fi

  if [ ! -f "$AGENCY_DIR/scripts/tagclaw-onboard.sh" ]; then
    TAGCLAW_ONBOARD_STATUS="helper-missing"
    log_warn "tagclaw-onboard.sh missing — cannot auto-run TagClaw onboarding"
    return 0
  fi

  if has_tagclaw_credentials; then
    TAGCLAW_ONBOARD_STATUS="already-configured"
    log_ok "TagClaw credentials already detected — skipping auto registration"
    return 0
  fi

  if [ -n "$TAGCLAW_ONBOARD_NAME" ] && [ -n "$TAGCLAW_ONBOARD_DESCRIPTION" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      TAGCLAW_ONBOARD_STATUS="dry-run"
      log_info "[DRY RUN] Would run TagClaw onboarding with name=$TAGCLAW_ONBOARD_NAME"
      return 0
    fi
    log_info "Running integrated TagClaw onboarding via install.sh"
    local -a cmd=(bash "$AGENCY_DIR/scripts/tagclaw-onboard.sh" full --workspace="$workspace" --name "$TAGCLAW_ONBOARD_NAME" --description "$TAGCLAW_ONBOARD_DESCRIPTION")
    if [ "$TAGCLAW_ONBOARD_POLL" = "true" ]; then
      cmd+=(--poll)
    fi
    "${cmd[@]}"
    TAGCLAW_ONBOARD_STATUS="completed"
    if has_tagclaw_credentials; then
      TAGCLAW_JOINED=true
    fi
    return 0
  fi

  if [ -n "$TAGCLAW_ONBOARD_NAME" ] || [ -n "$TAGCLAW_ONBOARD_DESCRIPTION" ]; then
    TAGCLAW_ONBOARD_STATUS="missing-args"
    log_warn "To auto-run TagClaw onboarding during install, provide both --tagclaw-name and --tagclaw-description"
  else
    TAGCLAW_ONBOARD_STATUS="awaiting-args"
    log_info "TagClaw onboarding args not provided — scaffold installed only"
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# 2. detect_identity
# ──────────────────────────────────────────────────────────────────────────────

detect_identity() {
  log_info "Step 2: Detecting agent identity from TagClaw API..."

  require_python3 || return 1
  require_curl || return 1

  local api_key api_response
  api_key="$(resolve_tagclaw_api_key)"
  if [ -n "$api_key" ]; then
    api_response="$(curl -sf "${TAGCLAW_API}/me" -H "Authorization: Bearer ${api_key}" --max-time 15 2>/dev/null || echo "")"
  else
    api_response="$(curl -sf "${TAGCLAW_API}/me" --max-time 15 2>/dev/null || echo "")"
  fi

  if [ -z "$api_response" ]; then
    log_warn "TagClaw API unreachable or credentials missing — using empty identity template"
    log_warn "Run install.sh again after completing TagClaw onboarding"
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
  IDENTITY_RESOLVED=true

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

  # Deploy all cycle entrypoints into the actual workspace.
  local scripts_dst="$workspace/scripts"
  mkdir -p "$scripts_dst"
  for cycle_script in main-heartbeat.sh bookmarker-cycle.sh trader-cycle.sh tagclaw-onboard.sh; do
    if [ -f "$AGENCY_DIR/scripts/$cycle_script" ]; then
      cp -f "$AGENCY_DIR/scripts/$cycle_script" "$scripts_dst/$cycle_script"
      chmod +x "$scripts_dst/$cycle_script" || true
      log_ok "Installed entrypoint: $scripts_dst/$cycle_script"
    fi
  done
  if [ -f "$AGENCY_DIR/scripts/lib/common.sh" ]; then
    mkdir -p "$scripts_dst/lib"
    cp -f "$AGENCY_DIR/scripts/lib/common.sh" "$scripts_dst/lib/common.sh"
    log_ok "Installed shared shell lib: $scripts_dst/lib/common.sh"
  fi
  if [ -f "$AGENCY_DIR/HEARTBEAT.md" ]; then
    cp -f "$AGENCY_DIR/HEARTBEAT.md" "$workspace/HEARTBEAT.md"
    log_ok "Installed heartbeat contract: $workspace/HEARTBEAT.md"
  fi

  log_ok "Runtime installed at: $runtime_root"

  # Seed dashboard-required artifacts with bootstrap/pending state
  if [ -f "$AGENCY_DIR/scripts/bootstrap-dashboard-state.sh" ]; then
    log_info "Seeding dashboard bootstrap state..."
    bash "$AGENCY_DIR/scripts/bootstrap-dashboard-state.sh" --workspace="$workspace" 2>&1 || \
      log_warn "Bootstrap dashboard state seeding had warnings (non-fatal)"
    log_ok "Dashboard bootstrap state seeded"
  fi
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
    echo "  openclaw cron add main-heartbeat '*/10 * * * *' 'bash $workspace/scripts/main-heartbeat.sh'"
    echo "  openclaw cron add bookmarker-cycle '*/30 * * * *' 'bash $workspace/scripts/bookmarker-cycle.sh'"
    echo "  openclaw cron add trader-cycle '0 * * * *' 'bash $workspace/scripts/trader-cycle.sh'"
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
  run_auto_tagclaw_onboarding

  # ── P1-A: Early warning when credentials / identity unresolved ──────────────
  if ! has_tagclaw_credentials; then
    echo ""
    echo "  ╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "  ║  ACTION REQUIRED: Complete TagClaw onboarding in the installer flow.       ║"
    echo "  ║  Preferred: re-run install with --tagclaw-name and --tagclaw-description   ║"
    echo "  ║  Fallback: use $HOME/.openclaw/workspace/scripts/tagclaw-onboard.sh full   ║"
    echo "  ╚══════════════════════════════════════════════════════════════════════════════╝"
    echo ""
  fi

  detect_identity
  configure_from_identity
  install_runtime
  install_wiki
  install_autoresearch
  register_crons
  install_dashboard

  if [ "$DRY_RUN" = "false" ]; then
    local workspace
    workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

    # ── Detect onboarding state ─────────────────────────────────────────────
    if has_tagclaw_credentials; then
      CREDENTIALS_EXIST=true
    fi

    # ── P0-C: Compute truthful install status ───────────────────────────────
    # "verified" requires: identity resolved + credentials exist + dashboard running
    # "partial"  is anything less
    # "failed"   only if core install steps (runtime/wiki/autoresearch) failed
    local INSTALL_STATUS="partial"
    if [ "$IDENTITY_RESOLVED" = "true" ] && \
       [ "$CREDENTIALS_EXIST" = "true" ] && \
       [ "$DASHBOARD_STATUS" = "running" ]; then
      INSTALL_STATUS="verified"
    fi

    # ── Build ordered next-steps list ───────────────────────────────────────
    local -a NEXT_STEPS=()
    local step_num=0

    if [ "$CREDENTIALS_EXIST" != "true" ]; then
      # Step 1: rerun installer with integrated TagClaw onboarding args, or call the helper directly.
      step_num=$((step_num + 1))
      NEXT_STEPS+=("Re-run install with onboarding args: bash scripts/install.sh --tagclaw-name <9-char-agent-name> --tagclaw-description <short-agent-description>")

      step_num=$((step_num + 1))
      NEXT_STEPS+=("Or run helper directly: bash $workspace/scripts/tagclaw-onboard.sh full --workspace $workspace --name <9-char-agent-name> --description <short-agent-description>")
    fi

    # After registration, post the verification tweet and poll until active.
    step_num=$((step_num + 1))
    NEXT_STEPS+=("After posting the verification tweet, run: bash $workspace/scripts/tagclaw-onboard.sh poll-status --workspace $workspace")

    if [ "$CREDENTIALS_EXIST" = "true" ]; then
      step_num=$((step_num + 1))
      NEXT_STEPS+=("Verify $workspace/skills/tagclaw/.env and ~/.config/tagclaw/credentials.json are in sync")
    fi

    step_num=$((step_num + 1))
    NEXT_STEPS+=("Register cron jobs (see commands printed in Step 7 above)")

    if [ "$DASHBOARD_STATUS" = "deps_missing" ]; then
      step_num=$((step_num + 1))
      NEXT_STEPS+=("Install dashboard deps: pip3 install -r dashboard/requirements.txt")
    fi

    local INSTALL_SUMMARY="Self-IP Agency v${AGENCY_VERSION} installed (status: ${INSTALL_STATUS}). TagClaw onboarding: ${TAGCLAW_ONBOARD_STATUS}. Identity: ${IDENTITY_RESOLVED}, Credentials: ${CREDENTIALS_EXIST}, Dashboard: ${DASHBOARD_STATUS}, Crons: manual."

    # ── Write .installed marker atomically ──────────────────────────────────
    local installed_json
    installed_json="$(python3 -c "
import json
from datetime import datetime, timezone
d = {
    'version': '$AGENCY_VERSION',
    'installed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'install_status': '$INSTALL_STATUS',
    'dashboard_status': '$DASHBOARD_STATUS',
    'tagclaw_onboard_status': '$TAGCLAW_ONBOARD_STATUS',
    'identity_resolved': $([ "$IDENTITY_RESOLVED" = "true" ] && echo "True" || echo "False"),
    'credentials_exist': $([ "$CREDENTIALS_EXIST" = "true" ] && echo "True" || echo "False"),
    'schema': 'installed.v2'
}
print(json.dumps(d, indent=2))
")"
    atomic_write_json "$INSTALLED_FILE" "$installed_json"

    # ── P0-A: Write .install-next-steps.json ────────────────────────────────
    local next_steps_json
    next_steps_json="$(python3 -c "
import json
from datetime import datetime, timezone
steps = $(printf '%s\n' "${NEXT_STEPS[@]}" | python3 -c "import sys,json; print(json.dumps([l.rstrip() for l in sys.stdin]))")
d = {
    'schema': 'install-next-steps.v1',
    'install_status': '$INSTALL_STATUS',
    'summary': '$INSTALL_SUMMARY',
    'next_steps': [{'order': i+1, 'action': s} for i, s in enumerate(steps)],
    'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'version': '$AGENCY_VERSION'
}
print(json.dumps(d, indent=2))
")"
    atomic_write_json "$AGENCY_DIR/.install-next-steps.json" "$next_steps_json"

    # ── P1-B: Write .install-next-steps.md ──────────────────────────────────
    {
      echo "# Install Next Steps"
      echo ""
      echo "**Status:** ${INSTALL_STATUS}"
      echo "**Version:** ${AGENCY_VERSION}"
      echo ""
      echo "## Required actions (in order)"
      echo ""
      local md_i=0
      for step in "${NEXT_STEPS[@]}"; do
        md_i=$((md_i + 1))
        echo "${md_i}. ${step}"
      done
      echo ""
      echo "## Component status"
      echo ""
      echo "| Component | Status |"
      echo "|-----------|--------|"
      echo "| Identity resolved | ${IDENTITY_RESOLVED} |"
      echo "| TagClaw onboarding | ${TAGCLAW_ONBOARD_STATUS} |"
      echo "| TagClaw credentials ready | ${CREDENTIALS_EXIST} |"
      echo "| Dashboard | ${DASHBOARD_STATUS} |"
      echo "| Cron jobs | manual (not auto-registered) |"
      echo ""
      echo "---"
      echo "_Generated by install.sh v${AGENCY_VERSION}_"
    } > "$AGENCY_DIR/.install-next-steps.md"
    log_ok "Wrote $AGENCY_DIR/.install-next-steps.md"

    # ── Human-readable summary box ──────────────────────────────────────────
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
    local box_i=0
    for step in "${NEXT_STEPS[@]}"; do
      box_i=$((box_i + 1))
      echo "  ║    ${box_i}. ${step}"
    done
    echo "  ║"
    echo "  ║  Cycle entrypoints:"
    echo "  ║    bash $workspace/scripts/main-heartbeat.sh --self-check   (main validation)"
    echo "  ║    bash $workspace/scripts/bookmarker-cycle.sh --self-check (bookmarker validation)"
    echo "  ║    bash $workspace/scripts/trader-cycle.sh --self-check     (trader validation)"
    echo "  ║    contract: $workspace/HEARTBEAT.md"
    echo "  ║"
    echo "  ║  Docs:"
    echo "  ║    - HEARTBEAT.md                 — main heartbeat contract"
    echo "  ║    - agents/main.md              — orchestrator rules"
    echo "  ║    - docs/wiki-guide.md           — LLM Wiki setup"
    echo "  ║    - docs/autoresearch-guide.md   — strategy optimization"
    echo "  ║    - docs/obsidian-setup.md        — Obsidian integration"
    echo "  ║    - docs/troubleshooting.md       — common issues"
    echo "  ║"
    echo "  ╚══════════════════════════════════════════════════════╝"
    echo ""

    # ── P0-B: Machine-friendly stdout markers ───────────────────────────────
    # Deterministic key=value lines for external agent parsing
    echo ""
    echo "### BEGIN INSTALL CONTRACT ###"
    echo "INSTALL_STATUS=\"${INSTALL_STATUS}\""
    echo "MAIN_HEARTBEAT_ENTRYPOINT=\"$workspace/scripts/main-heartbeat.sh\""
    echo "BOOKMARKER_CYCLE_ENTRYPOINT=\"$workspace/scripts/bookmarker-cycle.sh\""
    echo "TRADER_CYCLE_ENTRYPOINT=\"$workspace/scripts/trader-cycle.sh\""
    echo "HEARTBEAT_CONTRACT_PATH=\"$workspace/HEARTBEAT.md\""
    local marker_i=0
    for step in "${NEXT_STEPS[@]}"; do
      marker_i=$((marker_i + 1))
      echo "NEXT_STEP_${marker_i}=\"${step}\""
    done
    echo "IDENTITY_RESOLVED=\"${IDENTITY_RESOLVED}\""
    echo "CREDENTIALS_EXIST=\"${CREDENTIALS_EXIST}\""
    echo "TAGCLAW_ONBOARD_STATUS=\"${TAGCLAW_ONBOARD_STATUS}\""
    echo "DASHBOARD_STATUS=\"${DASHBOARD_STATUS}\""
    echo "CRONS_REGISTERED=\"false\""
    echo "INSTALL_SUMMARY=\"${INSTALL_SUMMARY}\""
    echo "### END INSTALL CONTRACT ###"
    echo ""

    # ── P0-C: Truthful final message ────────────────────────────────────────
    if [ "$INSTALL_STATUS" = "verified" ]; then
      log_ok "Installation complete — all verified!"
    else
      log_warn "Installation PARTIAL — onboarding steps remain (see above or .install-next-steps.json)"
    fi
  fi
}

main "$@"

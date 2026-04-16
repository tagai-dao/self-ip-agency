#!/usr/bin/env bash
# install.sh — Self-IP Agency idempotent installer
# Usage: bash scripts/install.sh [--dry-run] [--tagclaw-name NAME] [--tagclaw-description TEXT] [--tagclaw-poll] [--skip-tagclaw-onboarding]
# Quick install: bash scripts/install.sh
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
DASHBOARD_PUBLIC_STATUS="disabled"
DASHBOARD_PUBLIC_URL=""
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
  python3 - <<'PY' "$workspace"
import pathlib, sys
workspace = pathlib.Path(sys.argv[1])
skill_env = workspace / 'skills' / 'tagclaw' / '.env'
api_key = ''
if skill_env.exists():
    for line in skill_env.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        k = k.strip(); v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        if k == 'TAGCLAW_API_KEY' and v:
            api_key = v
            break
print(api_key)
PY
}

has_tagclaw_credentials() {
  local api_key
  api_key="$(resolve_tagclaw_api_key)"
  [ -n "$api_key" ]
}

resolve_tagclaw_skill_env_field() {
  local field="$1"
  local workspace="${2:-$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")}"
  python3 - <<'PY' "$workspace" "$field"
import pathlib, sys
workspace = pathlib.Path(sys.argv[1])
field = sys.argv[2]
path = workspace / 'skills' / 'tagclaw' / '.env'
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
print(data.get(field, ''))
PY
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

  if [ "$DRY_RUN" = "true" ]; then
    TAGCLAW_ONBOARD_STATUS="dry-run"
    log_info "[DRY RUN] Would run integrated TagClaw onboarding"
    if [ -z "$TAGCLAW_ONBOARD_NAME" ]; then
      log_info "[DRY RUN] No --tagclaw-name supplied; helper will derive a default name"
    fi
    if [ -z "$TAGCLAW_ONBOARD_DESCRIPTION" ]; then
      log_info "[DRY RUN] No --tagclaw-description supplied; helper will use a default description"
    fi
    return 0
  fi

  log_info "Running integrated TagClaw onboarding via install.sh"
  if [ -z "$TAGCLAW_ONBOARD_NAME" ]; then
    log_warn "No --tagclaw-name supplied. Using helper-derived default name."
  fi
  if [ -z "$TAGCLAW_ONBOARD_DESCRIPTION" ]; then
    log_warn "No --tagclaw-description supplied. Using helper default description."
  fi

  local -a cmd=(bash "$AGENCY_DIR/scripts/tagclaw-onboard.sh" full --workspace="$workspace")
  if [ -n "$TAGCLAW_ONBOARD_NAME" ]; then
    cmd+=(--name "$TAGCLAW_ONBOARD_NAME")
  fi
  if [ -n "$TAGCLAW_ONBOARD_DESCRIPTION" ]; then
    cmd+=(--description "$TAGCLAW_ONBOARD_DESCRIPTION")
  fi
  if [ "$TAGCLAW_ONBOARD_POLL" = "true" ]; then
    cmd+=(--poll)
  fi
  "${cmd[@]}"
  TAGCLAW_ONBOARD_STATUS="completed"
  if has_tagclaw_credentials; then
    TAGCLAW_JOINED=true
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
  local workspace identity_json
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
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
        'private_key_path': '$workspace/skills/tagclaw-wallet/.env',
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

  # Deploy native runtime scripts (Phase 2: bookmarker/trader no longer require claude CLI)
  for runtime_script in run_bookmarker_runtime_v1.py run_trader_runtime_v1.py runtime_utils_v2.py; do
    if [ -f "$AGENCY_DIR/scripts/$runtime_script" ]; then
      cp -f "$AGENCY_DIR/scripts/$runtime_script" "$scripts_dst/$runtime_script"
      log_ok "Installed native runtime: $scripts_dst/$runtime_script"
    fi
  done

  # Deploy Python scripts needed by main-heartbeat
  for py_script in build_main_input_packet_v2.py run_main_runtime_v2.py compute_tas_social_v2.py select_strategy_v1.py; do
    if [ -f "$AGENCY_DIR/scripts/$py_script" ]; then
      cp -f "$AGENCY_DIR/scripts/$py_script" "$scripts_dst/$py_script"
    fi
  done
  log_ok "Installed Python runtime scripts"

  if [ -f "$AGENCY_DIR/scripts/lib/common.sh" ]; then
    mkdir -p "$scripts_dst/lib"
    cp -f "$AGENCY_DIR/scripts/lib/common.sh" "$scripts_dst/lib/common.sh"
    log_ok "Installed shared shell lib: $scripts_dst/lib/common.sh"
  fi
  if [ -f "$AGENCY_DIR/HEARTBEAT.md" ]; then
    cp -f "$AGENCY_DIR/HEARTBEAT.md" "$workspace/HEARTBEAT.md"
    log_ok "Installed heartbeat contract: $workspace/HEARTBEAT.md"
  fi

  # Deploy agent behavior files to workspace (so deployed scripts don't need repo)
  local agents_dst="$workspace/agents"
  mkdir -p "$agents_dst"
  for agent in main bookmarker trader; do
    if [ -f "$AGENCY_DIR/agents/${agent}.md" ]; then
      cp -f "$AGENCY_DIR/agents/${agent}.md" "$agents_dst/${agent}.md"
      log_ok "Installed behavior file: $agents_dst/${agent}.md"
    elif [ -f "$AGENCY_DIR/agents/${agent}.md.tmpl" ]; then
      cp -f "$AGENCY_DIR/agents/${agent}.md.tmpl" "$agents_dst/${agent}.md.tmpl"
      log_ok "Installed behavior template: $agents_dst/${agent}.md.tmpl"
    fi
  done

  # Deploy config to workspace (identity, agency config)
  local config_dst="$workspace/config"
  mkdir -p "$config_dst"
  if [ -f "$AGENCY_DIR/config/agency-identity.json" ]; then
    cp -f "$AGENCY_DIR/config/agency-identity.json" "$config_dst/agency-identity.json"
    log_ok "Installed identity config to workspace"
  fi
  if [ -f "$AGENCY_DIR/config/agency.config.yaml" ]; then
    cp -f "$AGENCY_DIR/config/agency.config.yaml" "$config_dst/agency.config.yaml"
  fi

  # Write .agency-meta.json — allows deployed scripts to find repo and version
  local meta_json
  meta_json="$(python3 -c "
import json
from datetime import datetime, timezone
d = {
    'schema': 'agency-meta.v1',
    'repo_dir': '$AGENCY_DIR',
    'version': '$AGENCY_VERSION',
    'installed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
}
print(json.dumps(d, indent=2))
")"
  atomic_write_json "$workspace/.agency-meta.json" "$meta_json"
  log_ok "Wrote workspace meta: $workspace/.agency-meta.json"

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
  echo "  NOTE: Only register crons for agents that pass --self-check."
  echo "  ══════════════════════════════════════════════════════════"
  echo ""

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  echo "  # Always safe to register (native Python runtime):"
  echo "  openclaw cron add main-heartbeat '*/10 * * * *' 'bash $workspace/scripts/main-heartbeat.sh'"
  echo ""
  echo "  # Register only after --self-check passes:"
  echo "  openclaw cron add bookmarker-cycle '*/30 * * * *' 'bash $workspace/scripts/bookmarker-cycle.sh'"
  echo "  openclaw cron add trader-cycle '0 * * * *' 'bash $workspace/scripts/trader-cycle.sh'"

  echo ""
  echo "  ══════════════════════════════════════════════════════════"
  echo ""

  log_info "Cron commands printed above — copy and run them manually"
}

# ──────────────────────────────────────────────────────────────────────────────
# 8. install_dashboard
# ──────────────────────────────────────────────────────────────────────────────

# Read `dashboard.public.enabled` from config/agency.config.yaml.
# Prints "true" or "false". Default is "false" (opt-in gate is safe-by-default).
# Requires PyYAML (yaml.safe_load) which is used elsewhere in the runtime.
_read_dashboard_public_enabled() {
  local yaml_path="$AGENCY_DIR/config/agency.config.yaml"
  if [ ! -f "$yaml_path" ]; then
    echo "false"
    return 0
  fi
  python3 - "$yaml_path" <<'PY' 2>/dev/null || echo "false"
import sys
try:
    import yaml
except ImportError:
    print("false")
    sys.exit(0)
try:
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f) or {}
except Exception:
    print("false")
    sys.exit(0)
dash = (data.get("dashboard") or {})
pub = (dash.get("public") or {})
print("true" if bool(pub.get("enabled")) else "false")
PY
}

install_dashboard() {
  log_info "Step 8: Installing dashboard..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local dashboard_dst="$workspace/tools/self-ip-dashboard"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would install dashboard to: $dashboard_dst"
    log_info "[DRY RUN] Would delegate lifecycle to scripts/dashboard-service.sh"
    return 0
  fi

  # 1. Install phase: ensure dashboard files are deployed. The detailed
  #    lifecycle (deps validation, local start, health check, public tunnel)
  #    is owned by scripts/dashboard-service.sh — this function only preps
  #    the filesystem and delegates.
  mkdir -p "$dashboard_dst"
  if [ -d "$AGENCY_DIR/dashboard" ]; then
    cp -r "$AGENCY_DIR/dashboard/." "$dashboard_dst/"
    log_ok "Dashboard files installed at: $dashboard_dst"
  fi

  # 2. Lifecycle phase: delegate to the canonical owner.
  local svc="$AGENCY_DIR/scripts/dashboard-service.sh"
  if [ ! -x "$svc" ]; then
    log_warn "scripts/dashboard-service.sh not found or not executable — cannot manage dashboard lifecycle"
    DASHBOARD_STATUS="failed"
    return 0
  fi

  VIZ_PORT="$DASHBOARD_PORT" "$svc" start-local \
    --port "$DASHBOARD_PORT" --workspace "$workspace" || true

  # 3. Read back status from the state file (the service wrote it atomically).
  local state_file="$workspace/runtime/shared/dashboard-service.json"
  if [ -f "$state_file" ]; then
    DASHBOARD_STATUS="$(python3 -c "
import json
try:
    d = json.load(open('$state_file'))
    print(d.get('local', {}).get('status') or 'unknown')
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")"
  else
    log_warn "Expected state file missing: $state_file"
    DASHBOARD_STATUS="unknown"
  fi

  # 4. Opt-in public exposure. Default is false (safe-by-default).
  local public_enabled
  public_enabled="$(_read_dashboard_public_enabled)"
  if [ "$public_enabled" = "true" ] && [ "$DASHBOARD_STATUS" = "running" ]; then
    log_info "dashboard.public.enabled=true → starting public tunnel..."
    "$svc" start-public --port "$DASHBOARD_PORT" --workspace "$workspace" || true

    if [ -f "$state_file" ]; then
      DASHBOARD_PUBLIC_STATUS="$(python3 -c "
import json
try:
    d = json.load(open('$state_file'))
    print(d.get('public', {}).get('status') or 'unknown')
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")"
      DASHBOARD_PUBLIC_URL="$(python3 -c "
import json
try:
    d = json.load(open('$state_file'))
    print(d.get('public', {}).get('url') or '')
except Exception:
    print('')
" 2>/dev/null || echo "")"
    fi
  elif [ "$public_enabled" = "true" ]; then
    log_warn "dashboard.public.enabled=true but local dashboard is not running — skipping public tunnel"
    DASHBOARD_PUBLIC_STATUS="failed"
  else
    DASHBOARD_PUBLIC_STATUS="disabled"
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
    local workspace_hint
    workspace_hint="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
    echo ""
    case "$TAGCLAW_ONBOARD_STATUS" in
      dry-run)
        echo "  ╔══════════════════════════════════════════════════════════════════════════════╗"
        echo "  ║  DRY RUN: install would execute integrated TagClaw onboarding next.        ║"
        echo "  ║  Workspace: $workspace_hint"
        echo "  ╚══════════════════════════════════════════════════════════════════════════════╝"
        ;;
      skipped|helper-missing)
        echo "  ╔══════════════════════════════════════════════════════════════════════════════╗"
        echo "  ║  ACTION REQUIRED: Complete TagClaw onboarding in the installer flow.       ║"
        echo "  ║  Fallback after install: use $workspace_hint/scripts/tagclaw-onboard.sh    ║"
        echo "  ╚══════════════════════════════════════════════════════════════════════════════╝"
        ;;
    esac
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
    # Schema v2: structured steps (one object per atomic step) with a parallel
    # flat-text fallback for legacy consumers. The verification tweet is modeled
    # as ONE atomic step (not 3 split strings) so operator UIs that only render
    # the first entry still see the full tweet body inline.
    #
    # Parallel arrays indexed by step position:
    #   NEXT_STEPS_TEXT  — flat string per step (may contain embedded newlines)
    #   STEP_KINDS       — kind tag (e.g. "x_verification_tweet") for custom render
    #   STEP_PAYLOADS    — JSON string: the full structured step object
    local -a NEXT_STEPS_TEXT=() STEP_KINDS=() STEP_PAYLOADS=()
    local TAGCLAW_STATUS TAGCLAW_AGENT_USERNAME TAGCLAW_VERIFICATION_CODE TAGCLAW_PROFILE_URL
    TAGCLAW_STATUS="$(resolve_tagclaw_skill_env_field "TAGCLAW_STATUS" "$workspace")"
    TAGCLAW_AGENT_USERNAME="$(resolve_tagclaw_skill_env_field "TAGCLAW_AGENT_USERNAME" "$workspace")"
    TAGCLAW_VERIFICATION_CODE="$(resolve_tagclaw_skill_env_field "TAGCLAW_VERIFICATION_CODE" "$workspace")"
    TAGCLAW_PROFILE_URL="$(resolve_tagclaw_skill_env_field "TAGCLAW_PROFILE_URL" "$workspace")"

    # Verification-tweet artifact path (used by both the structured step and
    # the dedicated file handoff written further down).
    local VERIFICATION_TWEET_FILE="$workspace/tagclaw-verification-tweet.txt"

    # Helper: emit a "simple" structured step (kind + action only)
    _emit_step_simple() {
      local _kind="$1" _text="$2"
      NEXT_STEPS_TEXT+=("$_text")
      STEP_KINDS+=("$_kind")
      STEP_PAYLOADS+=("$(python3 -c '
import json, sys
print(json.dumps({"kind": sys.argv[1], "title": sys.argv[2], "action": sys.argv[2]}))
' "$_kind" "$_text")")
    }

    # Helper: emit the x_verification_tweet step — the `action` field inlines
    # the full tweet body (matching the flat-text fallback) so naive consumers
    # that render only top-level common fields (title/action/file) still see
    # the exact tweet text under Step 1 and do not have to open the
    # verification-tweet file. `copy_text` / `details` remain the canonical
    # clipboard/per-line fields for kind-aware consumers.
    _emit_step_verification_tweet() {
      local _agent="$1" _code="$2" _vfile="$3" _poll="$4"
      local _line1 _line2 _copy _flat
      _line1="I'm claiming my AI agent \"$_agent\" on @TagClaw"
      _line2="Verification: \"$_code\""
      _copy="$(printf '%s\n%s' "$_line1" "$_line2")"
      _flat="$(printf 'Post this verification tweet on X:\n%s\n%s' "$_line1" "$_line2")"
      NEXT_STEPS_TEXT+=("$_flat")
      STEP_KINDS+=("x_verification_tweet")
      STEP_PAYLOADS+=("$(python3 -c '
import json, sys
agent, code, vfile, poll = sys.argv[1:]
line1 = f"I\u0027m claiming my AI agent \"{agent}\" on @TagClaw"
line2 = f"Verification: \"{code}\""
body = f"{line1}\n{line2}"
print(json.dumps({
    "kind": "x_verification_tweet",
    "title": "Post verification tweet on X",
    "action": f"Post this verification tweet on X:\n{line1}\n{line2}",
    "copy_text": body,
    "details": [line1, line2],
    "file": vfile,
    "post_action": f"After the tweet is live, run: {poll}",
}))
' "$_agent" "$_code" "$_vfile" "$_poll")")
    }

    if [ "$CREDENTIALS_EXIST" != "true" ]; then
      _emit_step_simple "rerun_install" \
        "Re-run install to complete TagClaw onboarding: bash scripts/install.sh"
      _emit_step_simple "run_onboard_helper" \
        "Or run helper directly: bash $workspace/scripts/tagclaw-onboard.sh full --workspace $workspace"
    fi

    if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
      local _POLL_CMD="bash $workspace/scripts/tagclaw-onboard.sh poll-status --workspace $workspace"
      _emit_step_verification_tweet \
        "$TAGCLAW_AGENT_USERNAME" \
        "$TAGCLAW_VERIFICATION_CODE" \
        "$VERIFICATION_TWEET_FILE" \
        "$_POLL_CMD"
      _emit_step_simple "poll_tagclaw_status" \
        "After the tweet is live, run: $_POLL_CMD"
    elif [ "$TAGCLAW_STATUS" != "active" ]; then
      _emit_step_simple "poll_tagclaw_status" \
        "After posting the verification tweet, run: bash $workspace/scripts/tagclaw-onboard.sh poll-status --workspace $workspace"
    fi

    if [ "$CREDENTIALS_EXIST" = "true" ]; then
      _emit_step_simple "verify_env_files" \
        "Verify $workspace/skills/tagclaw/.env contains TAGCLAW_API_KEY and $workspace/skills/tagclaw-wallet/.env contains the wallet bootstrap fields"
    fi

    _emit_step_simple "register_crons" \
      "Register cron jobs (see commands printed in Step 7 above)"

    if [ "$DASHBOARD_STATUS" = "deps_missing" ]; then
      _emit_step_simple "install_dashboard_deps" \
        "Install dashboard deps: pip3 install -r dashboard/requirements.txt"
    fi

    if [ "$DASHBOARD_PUBLIC_STATUS" = "failed" ]; then
      _emit_step_simple "install_cloudflared" \
        "Public dashboard tunnel failed to start. Install cloudflared (brew install cloudflared) then run: bash $workspace/scripts/dashboard-service.sh start-public"
    fi

    # ── Write .installed marker atomically ──────────────────────────────────
    # IMPORTANT: Write BEFORE self-checks so that cycle --self-check sees
    # the .agency-installed marker and does not false-negative on readiness.
    local installed_json
    installed_json="$(python3 -c "
import json
from datetime import datetime, timezone
d = {
    'version': '$AGENCY_VERSION',
    'installed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'install_status': '$INSTALL_STATUS',
    'dashboard_status': '$DASHBOARD_STATUS',
    'dashboard_local_status': '$DASHBOARD_STATUS',
    'dashboard_public_status': '$DASHBOARD_PUBLIC_STATUS',
    'dashboard_public_url': '$DASHBOARD_PUBLIC_URL',
    'tagclaw_onboard_status': '$TAGCLAW_ONBOARD_STATUS',
    'identity_resolved': $([ "$IDENTITY_RESOLVED" = "true" ] && echo "True" || echo "False"),
    'credentials_exist': $([ "$CREDENTIALS_EXIST" = "true" ] && echo "True" || echo "False"),
    'schema': 'installed.v2'
}
print(json.dumps(d, indent=2))
")"
    atomic_write_json "$INSTALLED_FILE" "$installed_json"

    # Also write workspace-local installed marker (deployed scripts check this)
    atomic_write_json "$workspace/.agency-installed" "$installed_json"

    # ── Post-install self-checks: run each cycle's --self-check ─────────
    local MAIN_READY=false BOOKMARKER_READY=false TRADER_READY=false
    log_info "Running post-install self-checks..."

    if bash "$workspace/scripts/main-heartbeat.sh" --self-check >/dev/null 2>&1; then
      MAIN_READY=true
      log_ok "main-heartbeat --self-check PASSED"
    else
      log_warn "main-heartbeat --self-check FAILED"
    fi

    if bash "$workspace/scripts/bookmarker-cycle.sh" --self-check >/dev/null 2>&1; then
      BOOKMARKER_READY=true
      log_ok "bookmarker-cycle --self-check PASSED"
    else
      log_warn "bookmarker-cycle --self-check FAILED — bookmarker not yet runnable"
    fi

    if bash "$workspace/scripts/trader-cycle.sh" --self-check >/dev/null 2>&1; then
      TRADER_READY=true
      log_ok "trader-cycle --self-check PASSED"
    else
      log_warn "trader-cycle --self-check FAILED — trader not yet runnable"
    fi

    local _public_summary="${DASHBOARD_PUBLIC_STATUS}"
    if [ -n "$DASHBOARD_PUBLIC_URL" ]; then
      _public_summary="${DASHBOARD_PUBLIC_STATUS} (${DASHBOARD_PUBLIC_URL})"
    fi
    local INSTALL_SUMMARY="Self-IP Agency v${AGENCY_VERSION} installed (status: ${INSTALL_STATUS}). TagClaw onboarding: ${TAGCLAW_ONBOARD_STATUS}. Identity: ${IDENTITY_RESOLVED}, Credentials: ${CREDENTIALS_EXIST}, Dashboard: ${DASHBOARD_STATUS}, Public dashboard: ${_public_summary}, Crons: manual. Readiness: main=${MAIN_READY} bookmarker=${BOOKMARKER_READY} trader=${TRADER_READY}."

    # ── Dedicated verification tweet handoff artifact ──────────────────────
    # VERIFICATION_TWEET_FILE was declared earlier (before the structured
    # next-steps builder) so the x_verification_tweet step could reference it.
    if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
      cat > "$VERIFICATION_TWEET_FILE" <<EOF
I'm claiming my AI agent "$TAGCLAW_AGENT_USERNAME" on @TagClaw
Verification: "$TAGCLAW_VERIFICATION_CODE"
EOF
      log_ok "Wrote verification tweet template: $VERIFICATION_TWEET_FILE"
    else
      rm -f "$VERIFICATION_TWEET_FILE" 2>/dev/null || true
    fi

    # ── P0-A: Write .install-next-steps.json (schema v2) ────────────────────
    # v2 changes (2026-04-17):
    #   - `next_steps` is now a structured array (one object per atomic step).
    #   - `next_steps_text` is the flat-string fallback for legacy consumers.
    #   - The verification tweet ships as ONE step (kind=x_verification_tweet)
    #     carrying `copy_text`, `details`, `file`, and `post_action` so UIs can
    #     render the full tweet inline instead of splitting it across steps.
    #
    # Assemble the structured + flat arrays into a single JSON blob in an
    # intermediate python3 step. We pass STEP_PAYLOADS (already JSON-encoded
    # objects) and NEXT_STEPS_TEXT (flat strings, may contain embedded
    # newlines) through argv so neither bash word-splitting nor double-quoted
    # interpolation can lose data.
    local _arrays_json _struct_count="${#STEP_PAYLOADS[@]}"
    _arrays_json="$(python3 -c '
import json, sys
n = int(sys.argv[1])
structured = [json.loads(x) for x in sys.argv[2:2 + n]]
flat = list(sys.argv[2 + n:])
for i, s in enumerate(structured, 1):
    s["order"] = i
print(json.dumps({"next_steps": structured, "next_steps_text": flat}))
' "$_struct_count" "${STEP_PAYLOADS[@]}" "${NEXT_STEPS_TEXT[@]}")"

    local next_steps_json
    # The arrays JSON is piped via stdin (NOT interpolated into the Python
    # source) so Python's string-literal escape processing cannot mangle
    # embedded newlines or quote characters in the step content.
    next_steps_json="$(printf '%s' "$_arrays_json" | python3 -c "
import json, sys
from datetime import datetime, timezone
_arrays = json.load(sys.stdin)
_tw_active = '$TAGCLAW_STATUS' == 'pending_verification' and bool('$TAGCLAW_AGENT_USERNAME') and bool('$TAGCLAW_VERIFICATION_CODE')
_tw_line1 = 'I\\'m claiming my AI agent \"$TAGCLAW_AGENT_USERNAME\" on @TagClaw' if _tw_active else ''
_tw_line2 = 'Verification: \"$TAGCLAW_VERIFICATION_CODE\"' if _tw_active else ''
d = {
    'schema': 'install-next-steps.v2',
    'install_status': '$INSTALL_STATUS',
    'summary': '$INSTALL_SUMMARY',
    'dashboard_local_status': '$DASHBOARD_STATUS',
    'dashboard_public_status': '$DASHBOARD_PUBLIC_STATUS',
    'dashboard_public_url': '$DASHBOARD_PUBLIC_URL',
    'next_steps': _arrays['next_steps'],
    'next_steps_text': _arrays['next_steps_text'],
    'tagclaw': {
        'onboard_status': '$TAGCLAW_ONBOARD_STATUS',
        'status': '$TAGCLAW_STATUS',
        'agent_username': '$TAGCLAW_AGENT_USERNAME',
        'verification_code': '$TAGCLAW_VERIFICATION_CODE',
        'profile_url': '$TAGCLAW_PROFILE_URL',
        'verification_tweet_file': '$VERIFICATION_TWEET_FILE',
        'verification_tweet': [_tw_line1, _tw_line2] if _tw_active else [],
        'verification_tweet_text': (_tw_line1 + '\\n' + _tw_line2) if _tw_active else ''
    },
    'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'version': '$AGENCY_VERSION'
}
print(json.dumps(d, indent=2))
")"
    atomic_write_json "$AGENCY_DIR/.install-next-steps.json" "$next_steps_json"

    # ── P1-B: Write .install-next-steps.md ──────────────────────────────────
    # Kind-based rendering: the x_verification_tweet step renders with the
    # tweet body inline in a fenced text block so Step 1 is atomic.
    {
      echo "# Install Next Steps"
      echo ""
      echo "**Status:** ${INSTALL_STATUS}"
      echo "**Version:** ${AGENCY_VERSION}"
      echo ""
      echo "## Required actions (in order)"
      echo ""
      local md_i=0 _k _tw_line1 _tw_line2
      _tw_line1="I'm claiming my AI agent \"$TAGCLAW_AGENT_USERNAME\" on @TagClaw"
      _tw_line2="Verification: \"$TAGCLAW_VERIFICATION_CODE\""
      for md_i in "${!NEXT_STEPS_TEXT[@]}"; do
        _k="${STEP_KINDS[$md_i]}"
        if [ "$_k" = "x_verification_tweet" ]; then
          echo "$((md_i + 1)). **Post verification tweet on X** — post this exact tweet:"
          echo ""
          echo '   ```text'
          echo "   ${_tw_line1}"
          echo "   ${_tw_line2}"
          echo '   ```'
          echo ""
          echo "   File (for convenience): \`$VERIFICATION_TWEET_FILE\`"
          echo "   After the tweet is live, run: \`bash $workspace/scripts/tagclaw-onboard.sh poll-status --workspace $workspace\`"
        else
          echo "$((md_i + 1)). ${NEXT_STEPS_TEXT[$md_i]}"
        fi
      done
      echo ""
      if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
        # Keep the dedicated section as a redundant convenience reference
        # (the primary inline copy is above in Step 1).
        echo "## Verification tweet (reference copy)"
        echo ""
        echo "File: $VERIFICATION_TWEET_FILE"
        echo ""
        echo '```text'
        echo "${_tw_line1}"
        echo "${_tw_line2}"
        echo '```'
        echo ""
      fi
      echo "## Component status"
      echo ""
      echo "| Component | Status |"
      echo "|-----------|--------|"
      echo "| Identity resolved | ${IDENTITY_RESOLVED} |"
      echo "| TagClaw onboarding | ${TAGCLAW_ONBOARD_STATUS} |"
      echo "| TagClaw credentials ready | ${CREDENTIALS_EXIST} |"
      echo "| Dashboard (local) | ${DASHBOARD_STATUS} |"
      if [ -n "$DASHBOARD_PUBLIC_URL" ]; then
        echo "| Dashboard (public) | ${DASHBOARD_PUBLIC_STATUS} — ${DASHBOARD_PUBLIC_URL} |"
      else
        echo "| Dashboard (public) | ${DASHBOARD_PUBLIC_STATUS} |"
      fi
      echo "| Cron jobs | manual (not auto-registered) |"
      echo ""
      echo "---"
      echo "_Generated by install.sh v${AGENCY_VERSION} (schema: install-next-steps.v2)_"
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

    case "$DASHBOARD_PUBLIC_STATUS" in
      running)
        if [ -n "$DASHBOARD_PUBLIC_URL" ]; then
          echo "  ║    - Public dashboard URL: $DASHBOARD_PUBLIC_URL"
        else
          echo "  ║    - Public dashboard tunnel running (URL pending in logs/dashboard-tunnel.log)"
        fi
        ;;
      failed)
        echo "  ║    ⚠ Public dashboard tunnel failed — check logs/dashboard-tunnel.log (cloudflared required)"
        ;;
      disabled|"")
        # Quiet: opt-in feature, default off
        :
        ;;
      *)
        echo "  ║    ⚠ Public dashboard status: $DASHBOARD_PUBLIC_STATUS"
        ;;
    esac

    echo "  ║"
    if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
      echo "  ║  Verification tweet (post this exact text):"
      echo "  ║    File: $VERIFICATION_TWEET_FILE"
      echo "  ║    I'm claiming my AI agent \"$TAGCLAW_AGENT_USERNAME\" on @TagClaw"
      echo "  ║    Verification: \"$TAGCLAW_VERIFICATION_CODE\""
      if [ -n "$TAGCLAW_PROFILE_URL" ]; then
        echo "  ║    Profile: $TAGCLAW_PROFILE_URL"
      fi
      echo "  ║"
    elif [ "$TAGCLAW_STATUS" = "active" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ]; then
      echo "  ║  TagClaw account active: $TAGCLAW_AGENT_USERNAME"
      if [ -n "$TAGCLAW_PROFILE_URL" ]; then
        echo "  ║    Profile: $TAGCLAW_PROFILE_URL"
      fi
      echo "  ║"
    fi
    echo "  ║  Manual steps required:"
    # Kind-based render: x_verification_tweet inlines the full tweet body so
    # operators reading only the summary box still see the exact text to post.
    local box_i _bk
    for box_i in "${!NEXT_STEPS_TEXT[@]}"; do
      _bk="${STEP_KINDS[$box_i]}"
      if [ "$_bk" = "x_verification_tweet" ]; then
        echo "  ║    $((box_i + 1)). Post this verification tweet on X:"
        echo "  ║         I'm claiming my AI agent \"$TAGCLAW_AGENT_USERNAME\" on @TagClaw"
        echo "  ║         Verification: \"$TAGCLAW_VERIFICATION_CODE\""
        echo "  ║         (file: $VERIFICATION_TWEET_FILE)"
      else
        # Flat strings may contain embedded newlines (e.g. the consolidated
        # fallback): render each physical line with the box gutter preserved.
        local _first=1 _line
        while IFS= read -r _line; do
          if [ "$_first" = "1" ]; then
            echo "  ║    $((box_i + 1)). ${_line}"
            _first=0
          else
            echo "  ║       ${_line}"
          fi
        done <<< "${NEXT_STEPS_TEXT[$box_i]}"
      fi
    done
    echo "  ║"
    echo "  ║  Cycle readiness (post-install self-check):"
    if [ "$MAIN_READY" = "true" ]; then
      echo "  ║    ✓ main-heartbeat       — READY"
    else
      echo "  ║    ✗ main-heartbeat       — NOT READY (run --self-check for details)"
    fi
    if [ "$BOOKMARKER_READY" = "true" ]; then
      echo "  ║    ✓ bookmarker-cycle     — READY"
    else
      echo "  ║    ✗ bookmarker-cycle     — NOT READY (run --self-check for details)"
    fi
    if [ "$TRADER_READY" = "true" ]; then
      echo "  ║    ✓ trader-cycle         — READY"
    else
      echo "  ║    ✗ trader-cycle         — NOT READY (run --self-check for details)"
    fi
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
    echo "INSTALL_STEPS_SCHEMA=\"install-next-steps.v2\""
    echo "INSTALL_STATUS=\"${INSTALL_STATUS}\""
    echo "MAIN_HEARTBEAT_ENTRYPOINT=\"$workspace/scripts/main-heartbeat.sh\""
    echo "BOOKMARKER_CYCLE_ENTRYPOINT=\"$workspace/scripts/bookmarker-cycle.sh\""
    echo "TRADER_CYCLE_ENTRYPOINT=\"$workspace/scripts/trader-cycle.sh\""
    echo "HEARTBEAT_CONTRACT_PATH=\"$workspace/HEARTBEAT.md\""
    # NEXT_STEP_N markers carry the flat fallback text. For the consolidated
    # x_verification_tweet step the value spans multiple logical lines; we
    # escape embedded newlines as the two-character sequence \n so naive
    # line-based parsers still see one NEXT_STEP_N per physical output line.
    local marker_i=0
    local step_escaped
    for step in "${NEXT_STEPS_TEXT[@]}"; do
      marker_i=$((marker_i + 1))
      step_escaped="${step//\\/\\\\}"
      step_escaped="${step_escaped//\"/\\\"}"
      # Convert real newlines → literal \n for single-line key=value output.
      step_escaped="${step_escaped//$'\n'/\\n}"
      echo "NEXT_STEP_${marker_i}=\"${step_escaped}\""
      # Emit a kind tag so new parsers can detect the verification step.
      echo "NEXT_STEP_${marker_i}_KIND=\"${STEP_KINDS[$((marker_i - 1))]}\""
    done
    echo "IDENTITY_RESOLVED=\"${IDENTITY_RESOLVED}\""
    echo "CREDENTIALS_EXIST=\"${CREDENTIALS_EXIST}\""
    echo "TAGCLAW_ONBOARD_STATUS=\"${TAGCLAW_ONBOARD_STATUS}\""
    echo "TAGCLAW_STATUS=\"${TAGCLAW_STATUS}\""
    echo "TAGCLAW_AGENT_USERNAME=\"${TAGCLAW_AGENT_USERNAME}\""
    echo "TAGCLAW_VERIFICATION_CODE=\"${TAGCLAW_VERIFICATION_CODE}\""
    echo "TAGCLAW_PROFILE_URL=\"${TAGCLAW_PROFILE_URL}\""
    if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
      echo "VERIFICATION_TWEET_FILE=\"${VERIFICATION_TWEET_FILE}\""
      # Per-line fields — kept for backward compatibility.
      echo "VERIFICATION_TWEET_LINE_1=\"I\'m claiming my AI agent \\\"${TAGCLAW_AGENT_USERNAME}\\\" on @TagClaw\""
      echo "VERIFICATION_TWEET_LINE_2=\"Verification: \\\"${TAGCLAW_VERIFICATION_CODE}\\\"\""
      # Aggregated field — newlines escaped as \n so parsers do not need to
      # reconstruct the tweet from line 1 + line 2.
      echo "VERIFICATION_TWEET_TEXT=\"I\'m claiming my AI agent \\\"${TAGCLAW_AGENT_USERNAME}\\\" on @TagClaw\\nVerification: \\\"${TAGCLAW_VERIFICATION_CODE}\\\"\""
    fi
    echo "MAIN_READY=\"${MAIN_READY}\""
    echo "BOOKMARKER_READY=\"${BOOKMARKER_READY}\""
    echo "TRADER_READY=\"${TRADER_READY}\""
    echo "DASHBOARD_STATUS=\"${DASHBOARD_STATUS}\""
    echo "DASHBOARD_LOCAL_STATUS=\"${DASHBOARD_STATUS}\""
    echo "DASHBOARD_PUBLIC_STATUS=\"${DASHBOARD_PUBLIC_STATUS}\""
    echo "DASHBOARD_PUBLIC_URL=\"${DASHBOARD_PUBLIC_URL}\""
    echo "CRONS_REGISTERED=\"false\""
    echo "INSTALL_SUMMARY=\"${INSTALL_SUMMARY}\""
    echo "### END INSTALL CONTRACT ###"
    echo ""

    if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
      echo "### BEGIN VERIFICATION TWEET ###"
      echo "VERIFICATION_TWEET_FILE=$VERIFICATION_TWEET_FILE"
      echo "I'm claiming my AI agent \"$TAGCLAW_AGENT_USERNAME\" on @TagClaw"
      echo "Verification: \"$TAGCLAW_VERIFICATION_CODE\""
      echo "### END VERIFICATION TWEET ###"
      echo ""
    fi

    # ── P0-C: Truthful final message ────────────────────────────────────────
    if [ "$INSTALL_STATUS" = "verified" ]; then
      log_ok "Installation complete — all verified!"
    else
      log_warn "Installation PARTIAL — onboarding steps remain (see above or .install-next-steps.json)"
    fi
  fi
}

main "$@"

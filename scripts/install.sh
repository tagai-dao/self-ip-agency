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
DASHBOARD_PUBLIC_GUIDE_AVAILABLE="false"
DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED="false"
DASHBOARD_PUBLIC_INSTALL_COMMAND=""
DASHBOARD_PUBLIC_START_COMMAND=""
DASHBOARD_PUBLIC_STATE_FILE=""
TAGCLAW_ONBOARD_NAME="${TAGCLAW_AGENT_NAME:-}"
TAGCLAW_ONBOARD_DESCRIPTION="${TAGCLAW_AGENT_DESCRIPTION:-}"
TAGCLAW_ONBOARD_POLL=false
SKIP_TAGCLAW_ONBOARD=false
TAGCLAW_ONBOARD_STATUS="not_requested"

# ── Owner twitter binding inputs (PR-B: cloud-headless non-interactive first) ──
# Priority: --owner-twitter-handle > OWNER_TWITTER_HANDLE env > config/owner.local.json
# (TTY prompt is the weakest fallback, gated on [ -t 0 ] && ! FORCE_NON_INTERACTIVE)
# Any non-empty source lands in skill .env as TAGCLAW_EXPECTED_TWITTER_HANDLE,
# which refresh-agency-identity.sh picks up as binding_source=operator.declared.
OWNER_TWITTER_HANDLE_ARG=""
OWNER_TWITTER_ID_ARG=""
OWNER_TWITTER_HANDLE_SOURCE=""   # flag | env | file | tty | none
# See docs/design/x-sync-twitter-binding-fix.md §4.2 for the full fallback chain.

# ── Install state tracking (for machine-readable output contract) ─────────────
TAGCLAW_JOINED=false
CREDENTIALS_EXIST=false
IDENTITY_RESOLVED=false
CRONS_REGISTERED=false
CRON_REGISTRATION_MODE=""        # local-cli | deferred-tool | blocked
CRON_INTENT_PATH=""              # path to .install-cron-jobs.json when deferred
# `--no-deliver` (when the CLI supports it) tells OpenClaw's scheduler NOT to
# attempt announcing run summaries over the outbound mux. Our three cron jobs
# write results to runtime/ JSONs — announce delivery is overhead that causes
# false `error` run states on deployments where the outbound route isn't
# bound (`mux outbound failed (403): route not bound`). Detected lazily at
# first cron-add site so the probe only runs when needed.
CRON_ADD_EXTRA_FLAGS=""
CRON_ADD_EXTRA_FLAGS_PROBED=false
_detect_cron_add_flags() {
  [ "$CRON_ADD_EXTRA_FLAGS_PROBED" = "true" ] && return 0
  CRON_ADD_EXTRA_FLAGS_PROBED=true
  if command -v openclaw >/dev/null 2>&1 && openclaw cron add --help 2>&1 | grep -q -- '--no-deliver'; then
    CRON_ADD_EXTRA_FLAGS="--no-deliver"
  else
    log_warn "openclaw CLI does NOT support --no-deliver. Cron run status may show 'error' on delivery failures even when script succeeds. Consider upgrading: pnpm up -g openclaw@latest"
  fi
}
RAW_SEED_STATUS="not_attempted"  # ok | partial | failed | not_attempted
X_TWEETS_SEED_STATUS="not_attempted"   # ok | partial | deferred | blocked | failed | not_attempted | dry-run
X_TWEETS_COMPILE_STATUS="not_attempted" # ok | deferred | failed | not_attempted | dry-run
X_TWEETS_COMPILED_COUNT="0"
X_TWEETS_BLOCKERS="[]"
INTRO_POST_STATUS="not_attempted" # published | published_but_marker_failed | already_published | skipped | failed | not_attempted
INTRO_POST_TICK=""                # resolved tick value
INTRO_POST_TICK_STATUS=""         # resolved | fallback | unresolved | not_attempted
INTRO_POST_TICK_SOURCE=""         # explicit | raw_trending | raw_inference | validated_fallback
INTRO_POST_TICK_CANDIDATES=""     # JSON array of top candidates (for diagnostics)

# ── Owner binding state (PR-B: populated after install, used for next-steps/UX) ──
OWNER_BINDING_STATUS="unknown"       # verified | declared | unresolved | unknown
OWNER_BINDING_REASON=""              # awaiting_tagclaw_me_or_post_verify | verified_via_me | declared_pending_verify | empty
OWNER_BINDING_SELF_HEAL="heartbeat"  # heartbeat | disabled

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
    --owner-twitter-handle=*) OWNER_TWITTER_HANDLE_ARG="${1#--owner-twitter-handle=}"; shift ;;
    --owner-twitter-handle) OWNER_TWITTER_HANDLE_ARG="${2:-}"; shift 2 ;;
    --owner-twitter-id=*) OWNER_TWITTER_ID_ARG="${1#--owner-twitter-id=}"; shift ;;
    --owner-twitter-id) OWNER_TWITTER_ID_ARG="${2:-}"; shift 2 ;;
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
# 0.5 collect_owner_twitter_binding — resolve owner twitter handle from the
#     non-interactive priority chain and persist to skill .env as
#     TAGCLAW_EXPECTED_TWITTER_HANDLE (operator-declared fallback).
#
#     Priority (see docs/design/x-sync-twitter-binding-fix.md §4.2):
#       1. --owner-twitter-handle flag (OWNER_TWITTER_HANDLE_ARG)
#       2. OWNER_TWITTER_HANDLE env var
#       3. config/owner.local.json (gitignored, IaC-friendly)
#       4. TTY prompt (only if [ -t 0 ] && [ -z "$FORCE_NON_INTERACTIVE" ])
#
#     This only writes the *expected* handle. The actual verified binding comes
#     from TagClaw /me via refresh-agency-identity.sh --verify-api (triggered
#     by tagclaw-onboard.sh and by the heartbeat self-heal in PR-B).
# ──────────────────────────────────────────────────────────────────────────────

_sanitize_twitter_handle() {
  python3 - <<'PY' "$1"
import re, sys
raw = sys.argv[1].strip().lstrip("@")
# Strip URL prefixes if operator pasted a profile link.
for prefix in ("https://x.com/", "https://twitter.com/", "http://x.com/", "http://twitter.com/", "x.com/", "twitter.com/"):
    if raw.lower().startswith(prefix.lower()):
        raw = raw[len(prefix):]
        break
# Twitter handle: 1-15 chars, alphanumeric + underscore.
if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", raw):
    print("")
else:
    print(raw)
PY
}

_sanitize_twitter_id() {
  python3 - <<'PY' "$1"
import re, sys
raw = sys.argv[1].strip()
# Twitter user IDs are numeric; permit 1-25 digits defensively.
if not re.fullmatch(r"[0-9]{1,25}", raw):
    print("")
else:
    print(raw)
PY
}

collect_owner_twitter_binding() {
  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local skill_env="$workspace/skills/tagclaw/.env"
  mkdir -p "$workspace/skills/tagclaw" 2>/dev/null || true

  local raw_handle="" raw_id="" source=""

  # 1. flag
  if [ -n "$OWNER_TWITTER_HANDLE_ARG" ]; then
    raw_handle="$OWNER_TWITTER_HANDLE_ARG"
    source="flag"
  fi
  if [ -n "$OWNER_TWITTER_ID_ARG" ]; then
    raw_id="$OWNER_TWITTER_ID_ARG"
  fi

  # 2. env
  if [ -z "$raw_handle" ] && [ -n "${OWNER_TWITTER_HANDLE:-}" ]; then
    raw_handle="$OWNER_TWITTER_HANDLE"
    source="env"
  fi
  if [ -z "$raw_id" ] && [ -n "${OWNER_TWITTER_ID:-}" ]; then
    raw_id="$OWNER_TWITTER_ID"
  fi

  # 3. config/owner.local.json (gitignored file; IaC-friendly)
  local owner_local="$AGENCY_DIR/config/owner.local.json"
  if [ -z "$raw_handle" ] && [ -f "$owner_local" ]; then
    local file_handle file_id
    file_handle="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); o=(d.get('owner') or {}); print((o.get('twitter_handle') or '').strip())" "$owner_local" 2>/dev/null || echo "")"
    file_id="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); o=(d.get('owner') or {}); print((o.get('twitter_id') or '').strip())" "$owner_local" 2>/dev/null || echo "")"
    if [ -n "$file_handle" ]; then
      raw_handle="$file_handle"
      source="file"
    fi
    if [ -z "$raw_id" ] && [ -n "$file_id" ]; then
      raw_id="$file_id"
    fi
  fi

  # 4. TTY prompt — weakest fallback, gated hard on interactive-only.
  if [ -z "$raw_handle" ] && [ -t 0 ] && [ -z "${FORCE_NON_INTERACTIVE:-}" ]; then
    printf "[install] Owner X/Twitter handle (for binding; leave blank to defer): " >&2
    IFS= read -r raw_handle || raw_handle=""
    if [ -n "$raw_handle" ]; then
      source="tty"
    fi
  fi

  if [ -z "$raw_handle" ]; then
    OWNER_TWITTER_HANDLE_SOURCE="none"
    log_info "Owner X binding: not supplied at install; will resolve via /me or heartbeat self-heal"
    return 0
  fi

  local clean_handle clean_id
  clean_handle="$(_sanitize_twitter_handle "$raw_handle")"
  if [ -z "$clean_handle" ]; then
    log_warn "Owner X handle '$raw_handle' failed format validation (expected 1-15 chars [A-Za-z0-9_]); ignored"
    OWNER_TWITTER_HANDLE_SOURCE="none"
    return 0
  fi
  clean_id=""
  if [ -n "$raw_id" ]; then
    clean_id="$(_sanitize_twitter_id "$raw_id")"
    if [ -z "$clean_id" ]; then
      log_warn "Owner X id '$raw_id' failed format validation (expected numeric); ignoring id, keeping handle"
    fi
  fi

  OWNER_TWITTER_HANDLE_SOURCE="$source"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would write TAGCLAW_EXPECTED_TWITTER_HANDLE=$clean_handle (source=$source) to $skill_env"
    [ -n "$clean_id" ] && log_info "[DRY RUN] Would write TAGCLAW_EXPECTED_TWITTER_ID=$clean_id"
    return 0
  fi

  python3 - <<'PY' "$skill_env" "$clean_handle" "$clean_id"
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
handle = sys.argv[2]
tid = sys.argv[3]
data = {}
if path.exists():
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        data[k] = v
if handle:
    data['TAGCLAW_EXPECTED_TWITTER_HANDLE'] = handle
if tid:
    data['TAGCLAW_EXPECTED_TWITTER_ID'] = tid
def fmt(v):
    if re.fullmatch(r'[A-Za-z0-9_./:@+\-]+', v):
        return v
    import json
    return json.dumps(v)
path.parent.mkdir(parents=True, exist_ok=True)
text = ''.join('{}={}\n'.format(k, fmt(v)) for k, v in sorted(data.items()))
path.write_text(text)
PY
  log_ok "Owner X binding declared: handle=$clean_handle (source=$source); will be upgraded to verified when /me confirms"
}

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
  log_info "Step 2: Detecting agent identity..."

  require_python3 || return 1
  require_curl || return 1

  local workspace refresh_helper
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  refresh_helper="$AGENCY_DIR/scripts/refresh-agency-identity.sh"

  if [ ! -f "$refresh_helper" ]; then
    log_warn "refresh-agency-identity.sh missing — leaving identity template untouched"
    return 0
  fi

  # Delegate identity reconstruction to the canonical refresh helper. It reads
  # .env sources (skill + wallet), optionally enriches from TagClaw /me, and
  # atomically writes BOTH the repo and workspace copies of agency-identity.json
  # so they stay in sync. Centralizing the write in one place is what fixes
  # the install-first / onboard-second stale shadow problem.
  local -a cmd=(bash "$refresh_helper" --workspace "$workspace" --repo-dir "$AGENCY_DIR" --verify-api)
  if [ "$DRY_RUN" = "true" ]; then
    cmd+=(--dry-run)
  fi

  local rc=0
  "${cmd[@]}" || rc=$?

  case "$rc" in
    0)
      IDENTITY_RESOLVED=true
      log_ok "Identity resolved via refresh-agency-identity.sh"
      ;;
    2)
      log_warn "Identity sources incomplete — complete TagClaw onboarding, then rerun install.sh"
      log_warn "Or invoke the refresh helper directly: bash scripts/refresh-agency-identity.sh --workspace $workspace"
      ;;
    *)
      log_warn "refresh-agency-identity.sh exited with code $rc — identity may be stale"
      ;;
  esac
  return 0
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
  for cycle_script in main-heartbeat.sh bookmarker-cycle.sh trader-cycle.sh tagclaw-onboard.sh refresh-agency-identity.sh dashboard-service.sh start-quick-tunnel.sh publish-intro-post.sh seed-raw-docs.sh; do
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

# ── Cloud / environment detection for cron registration ────────────────────────
# Returns via CRON_REGISTRATION_MODE:
#   local-cli     — CLI + gateway available, register immediately
#   deferred-tool — cloud/clawdi environment, defer to agent/tool path
#   blocked       — truly broken (no CLI, no cloud, no path forward)
_detect_cron_registration_mode() {
  local workspace="$1"

  # Explicit override for environments where the shell CLI has narrower
  # pairing scopes than the agent runtime that will finalize cron jobs.
  case "${SELF_IP_AGENCY_CRON_REGISTRATION_MODE:-}" in
    local-cli|deferred-tool|blocked)
      CRON_REGISTRATION_MODE="$SELF_IP_AGENCY_CRON_REGISTRATION_MODE"
      return 0
      ;;
    "")
      ;;
    *)
      log_warn "Ignoring invalid SELF_IP_AGENCY_CRON_REGISTRATION_MODE=$SELF_IP_AGENCY_CRON_REGISTRATION_MODE"
      ;;
  esac

  # Signal 1: explicit cloud env vars (clawdi, cloud-run, etc.)
  if [ -n "${CLAWDI_CLOUD_ENV:-}" ] || [ -n "${CLAWDI_SESSION_ID:-}" ] || \
     [ -n "${CLOUD_RUN_JOB:-}" ] || [ -n "${K_SERVICE:-}" ] || \
     [ "${OPENCLAW_ENV:-}" = "cloud" ]; then
    CRON_REGISTRATION_MODE="deferred-tool"
    return 0
  fi

  # Signal 2: running inside a clawdi workspace (path heuristic)
  if [[ "$workspace" == */clawdi/* ]] || [[ "$HOME" == */clawdi/* ]]; then
    CRON_REGISTRATION_MODE="deferred-tool"
    return 0
  fi

  # Signal 3: no user service manager (systemctl/launchctl) — cloud container
  if ! command -v systemctl >/dev/null 2>&1 && ! command -v launchctl >/dev/null 2>&1; then
    # Additional check: if openclaw CLI exists but gateway is structurally unavailable
    if command -v openclaw >/dev/null 2>&1; then
      # CLI exists — use multi-signal probe (cron list + health --json fallback)
      # to correctly detect reachable-but-empty scheduler
      if probe_scheduler_reachable "detect-mode"; then
        CRON_REGISTRATION_MODE="local-cli"
        return 0
      fi
    fi
    CRON_REGISTRATION_MODE="deferred-tool"
    return 0
  fi

  # Default: try local CLI path
  CRON_REGISTRATION_MODE="local-cli"
  return 0
}

# Write structured cron intent artifact for deferred registration
_write_cron_intent_artifact() {
  local workspace="$1"
  local intent_path="$AGENCY_DIR/.install-cron-jobs.json"

  local intent_json
  intent_json="$(python3 -c "
import json
from datetime import datetime, timezone
d = {
    'schema': 'install-crons.v1',
    'mode': 'pending_finalization',
    'deferred_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'workspace': '$workspace',
    'jobs': [
        {
            'name': 'main-heartbeat',
            'schedule': '*/10 * * * *',
            'session': 'isolated',
            'command': 'bash $workspace/scripts/main-heartbeat.sh',
            'message': 'Run the main heartbeat cycle: bash $workspace/scripts/main-heartbeat.sh',
            'timeout_seconds': 120,
        },
        {
            'name': 'bookmarker-cycle',
            'schedule': '*/30 * * * *',
            'session': 'isolated',
            'command': 'bash $workspace/scripts/bookmarker-cycle.sh',
            'message': 'Run the bookmarker curation cycle: bash $workspace/scripts/bookmarker-cycle.sh',
            'timeout_seconds': 180,
        },
        {
            'name': 'trader-cycle',
            'schedule': '0 * * * *',
            'session': 'isolated',
            'command': 'bash $workspace/scripts/trader-cycle.sh',
            'message': 'Run the trader operations cycle: bash $workspace/scripts/trader-cycle.sh',
            'timeout_seconds': 300,
        },
    ],
    'approval': {
        'required': True,
        'reply_text': '同意创建 cron',
        'prompt': 'Reply "同意创建 cron" to authorize the agent/tool path to create these three OpenClaw cron jobs.',
        'tg_channel_supported_if': 'The Telegram channel is connected to an already-approved OpenClaw agent/operator that can execute native cron tools. If it is only a shell CLI device with operator.read, the user must approve the pending OpenClaw device scope upgrade first.',
    },
    'finalize_command': 'openclaw cron add --name \"{name}\" --cron \"{schedule}\" --session {session} --message \"{message}\" --no-deliver',
    'finalize_script': 'bash scripts/finalize-crons.sh --workspace $workspace',
    'notes': 'Installer-side CLI cron auto-registration was not available in this environment. Prefer the agent/tool path after user approval; use finalize_script only as a CLI fallback after any required OpenClaw device scope-upgrade approval.',
}
print(json.dumps(d, indent=2))
")"
  atomic_write_json "$intent_path" "$intent_json"

  # Also write to workspace for agent visibility
  atomic_write_json "$workspace/.install-cron-jobs.json" "$intent_json"

  CRON_INTENT_PATH="$intent_path"
  log_ok "Wrote cron intent artifact: $intent_path"
  log_ok "Wrote cron intent artifact: $workspace/.install-cron-jobs.json"
}

# Attempt to finalize deferred cron registration via CLI best-effort.
# Called after writing the intent artifact. If the openclaw CLI is actually
# reachable (common when detection was conservative, e.g. cloud env var set
# but CLI+gateway still work), register all 3 jobs and promote state to
# "registered". If anything fails, leave the deferred artifact intact.
_attempt_deferred_cron_finalization() {
  local workspace="$1"
  local intent_path="$AGENCY_DIR/.install-cron-jobs.json"
  local cron_log_dir="$workspace/logs"
  local cron_log="$cron_log_dir/openclaw-cron-finalize.log"

  mkdir -p "$cron_log_dir"

  # Gate: CLI must exist and execute
  if ! command -v openclaw >/dev/null 2>&1; then
    log_info "Deferred finalization: openclaw CLI not in PATH — skipping auto-finalization"
    return 1
  fi
  if ! openclaw --version >/dev/null 2>&1; then
    log_info "Deferred finalization: openclaw CLI broken — skipping auto-finalization"
    return 1
  fi

  # Gate: scheduler must be reachable (multi-signal probe)
  if ! probe_scheduler_reachable "deferred-finalize"; then
    log_info "Deferred finalization: scheduler not reachable ($_PROBE_RESULT) — skipping auto-finalization"
    return 1
  fi

  log_info "Deferred finalization: scheduler reachable — attempting auto-registration"

  # Read jobs from the intent artifact
  local job_count
  job_count="$(python3 -c "
import json, sys
with open('$intent_path') as f:
    d = json.load(f)
print(len(d.get('jobs', [])))
" 2>/dev/null || echo "0")"

  if [ "${job_count:-0}" -eq 0 ]; then
    log_warn "Deferred finalization: no jobs found in intent artifact"
    return 1
  fi

  # Remove existing jobs from the intent artifact (dynamic, not hardcoded)
  local remove_names
  remove_names="$(python3 -c "
import json
with open('$intent_path') as f:
    d = json.load(f)
for j in d.get('jobs', []):
    print(j['name'])
" 2>/dev/null)"
  while IFS= read -r job_name; do
    [ -n "$job_name" ] && openclaw cron rm "$job_name" >>"$cron_log" 2>&1 || true
  done <<< "$remove_names"

  # Register each job from the intent artifact
  # Use tab delimiter to avoid collision with cron schedules and message text
  local finalize_ok=true
  local registered_count=0
  local failed_jobs=""

  _detect_cron_add_flags
  while IFS=$'\t' read -r name schedule session message; do
    [ -z "$name" ] && continue
    log_info "Deferred finalization: registering $name ($schedule)..."
    # shellcheck disable=SC2086  # CRON_ADD_EXTRA_FLAGS is intentionally word-split
    if openclaw cron add \
      --name "$name" \
      --cron "$schedule" \
      --session "$session" \
      --message "$message" \
      $CRON_ADD_EXTRA_FLAGS >>"$cron_log" 2>&1; then
      log_ok "Deferred finalization: registered $name"
      registered_count=$((registered_count + 1))
    else
      log_warn "Deferred finalization: failed to register $name"
      finalize_ok=false
      failed_jobs="${failed_jobs:+$failed_jobs, }$name"
    fi
  done < <(python3 -c "
import json
with open('$intent_path') as f:
    d = json.load(f)
for j in d.get('jobs', []):
    print(f\"{j['name']}\t{j['schedule']}\t{j['session']}\t{j['message']}\")
" 2>/dev/null)

  if [ "$finalize_ok" = "true" ] && [ "$registered_count" -gt 0 ]; then
    CRONS_REGISTERED=true
    CRON_REGISTRATION_MODE="local-cli"
    log_ok "Deferred finalization: all $registered_count cron jobs registered successfully"

    # Write a finalization receipt to the intent artifact (preserves provenance)
    python3 -c "
import json, os, tempfile
from datetime import datetime, timezone
with open('$intent_path') as f:
    d = json.load(f)
d['finalized_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
d['finalized_by'] = 'installer-deferred-auto'
d['mode'] = 'finalized'
p = '$intent_path'
with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(p), suffix='.tmp', delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, p)
# Also update workspace copy
wp = '$workspace/.install-cron-jobs.json'
with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(wp), suffix='.tmp', delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, wp)
" 2>/dev/null || true

    return 0
  else
    log_warn "Deferred finalization: $registered_count/$job_count jobs registered ($failed_jobs failed)"
    log_info "Deferred finalization: intent artifact preserved for manual/agent retry"
    return 1
  fi
}

_print_manual_cron_commands() {
  local ws="$1"
  echo ""
  echo "  ══════════════════════════════════════════════════════════"
  echo "  ACTION REQUIRED: Run these commands to register cron jobs."
  echo "  ══════════════════════════════════════════════════════════"
  echo ""
  echo "  openclaw cron add \\"
  echo "    --name \"main-heartbeat\" \\"
  echo "    --cron \"*/10 * * * *\" \\"
  echo "    --session isolated \\"
  echo "    --message \"Run the main heartbeat cycle: bash $ws/scripts/main-heartbeat.sh\" \\"
  echo "    --no-deliver"
  echo ""
  echo "  openclaw cron add \\"
  echo "    --name \"bookmarker-cycle\" \\"
  echo "    --cron \"*/30 * * * *\" \\"
  echo "    --session isolated \\"
  echo "    --message \"Run the bookmarker curation cycle: bash $ws/scripts/bookmarker-cycle.sh\" \\"
  echo "    --no-deliver"
  echo ""
  echo "  openclaw cron add \\"
  echo "    --name \"trader-cycle\" \\"
  echo "    --cron \"0 * * * *\" \\"
  echo "    --session isolated \\"
  echo "    --message \"Run the trader operations cycle: bash $ws/scripts/trader-cycle.sh\" \\"
  echo "    --no-deliver"
  echo ""
  echo "  (The --no-deliver flag disables announce delivery, which avoids false"
  echo "  'error' run states when the outbound mux route is not bound. Required for"
  echo "  correct cron status reporting — see VERSION 2.5.5+ release notes.)"
  echo ""
  echo "  ══════════════════════════════════════════════════════════"
  echo ""
}

register_crons() {
  log_info "Step 7: Registering cron jobs..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local cron_log_dir="$workspace/logs"
  local gateway_log="$cron_log_dir/openclaw-gateway-bootstrap.log"
  local cron_log="$cron_log_dir/openclaw-cron-registration.log"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would register 3 cron jobs with openclaw"
    return 0
  fi

  mkdir -p "$cron_log_dir"
  : > "$gateway_log"
  : > "$cron_log"

  # ── Detect cron registration mode ──────────────────────────────────────
  _detect_cron_registration_mode "$workspace"
  log_info "Cron registration mode: $CRON_REGISTRATION_MODE"

  if [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ]; then
    log_info "Cloud/clawdi environment detected — deferring cron registration to agent/tool path"
    _write_cron_intent_artifact "$workspace"
    log_info "Intent artifact written to: $CRON_INTENT_PATH"

    # Best-effort auto-finalization: the detection was conservative (env vars or
    # path heuristic), but the CLI+gateway might still be reachable. Try it now
    # so the user doesn't have to manually request cron completion after install.
    if _attempt_deferred_cron_finalization "$workspace"; then
      log_ok "Deferred cron registration auto-finalized successfully"
      return 0
    fi

    log_info "Cron registration deferred: auto-finalization did not complete"
    log_info "Finalize later with: bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace"
    # Deferred is an acceptable install state — finalize-crons.sh provides the completion path
    return 0
  fi

  # ── local-cli mode: proceed with CLI-based registration ────────────────

  # ── CLI presence check ──────────────────────────────────────────────────
  if ! command -v openclaw >/dev/null 2>&1; then
    log_warn "openclaw CLI not found in PATH"
    CRON_REGISTRATION_MODE="blocked"
    _write_cron_intent_artifact "$workspace"
    _print_manual_cron_commands "$workspace"
    return 1
  fi

  # ── CLI health probe — verify the binary actually executes ─────────────
  # A broken pnpm shim or dangling symlink passes `command -v` but crashes
  # on invocation. Catch that *before* blaming the gateway.
  local cli_path cli_real_path cli_version_out
  cli_path="$(command -v openclaw)"
  cli_real_path="$(readlink -f "$cli_path" 2>/dev/null || echo "$cli_path")"

  if ! cli_version_out="$(openclaw --version 2>&1)"; then
    log_warn "openclaw CLI found at $cli_path but it is broken or unexecutable"
    if [ "$cli_path" != "$cli_real_path" ]; then
      log_warn "Symlink target: $cli_real_path"
    fi
    log_warn "Error output: $cli_version_out"
    echo ""
    echo "  ══════════════════════════════════════════════════════════"
    echo "  DIAGNOSIS: The 'openclaw' command exists but fails to run."
    echo ""
    echo "  This typically means a broken pnpm global shim or a"
    echo "  dangling symlink after a version upgrade."
    echo ""
    echo "  Recommended fixes (try in order):"
    echo "    1. Reinstall the CLI:  pnpm add -g openclaw@latest"
    echo "    2. Or remove the stale shim and reinstall:"
    echo "       rm \"$cli_path\""
    echo "       pnpm add -g openclaw@latest"
    echo "    3. Then re-run:  bash $AGENCY_DIR/scripts/install.sh"
    echo "  ══════════════════════════════════════════════════════════"
    echo ""
    CRON_REGISTRATION_MODE="blocked"
    _write_cron_intent_artifact "$workspace"
    _print_manual_cron_commands "$workspace"
    return 1
  fi

  log_ok "openclaw CLI healthy — version: $cli_version_out"

  # ── Gateway reachability ───────────────────────────────────────────────
  # Use multi-signal probe that correctly handles reachable-but-empty scheduler
  local gw_ready=false
  if probe_scheduler_reachable "register-crons"; then
    gw_ready=true
  fi

  if [ "$gw_ready" != "true" ]; then
    log_warn "OpenClaw scheduler not immediately reachable — attempting gateway start"
    log_info "Attempting to start the OpenClaw Gateway service..."
    if openclaw gateway start >>"$gateway_log" 2>&1; then
      log_ok "Requested OpenClaw Gateway service start"
    else
      log_warn "openclaw gateway start returned non-zero"
    fi

    local _gw_try
    for _gw_try in 1 2 3 4 5 6 7 8 9 10; do
      sleep 1
      if probe_scheduler_reachable "register-crons-retry-$_gw_try"; then
        gw_ready=true
        break
      fi
    done
  fi

  if [ "$gw_ready" != "true" ]; then
    log_warn "Installer-side CLI cron auto-registration did not complete — scheduler not reachable after retries"
    log_info "Cron registration deferred to agent/tool path or manual retry"
    log_warn "See gateway bootstrap log: $gateway_log"
    CRON_REGISTRATION_MODE="deferred-tool"
    _write_cron_intent_artifact "$workspace"
    log_info "Recovery steps:"
    echo "  1. Run: openclaw gateway status"
    echo "  2. If needed, run: openclaw gateway start"
    echo "  3. Re-run: bash $AGENCY_DIR/scripts/install.sh"
    echo "  Or: use the agent/tool cron registration path (see .install-cron-jobs.json)"
    return 1
  fi
  log_ok "OpenClaw scheduler reachable — proceeding with cron registration"

  # Auto-register cron jobs, using verify_registered (from lib/common.sh) to
  # confirm each add actually persisted. The CLI has been observed to exit 0
  # on adds that silently get dropped by the scheduler (plugin config
  # mismatch, gateway flap mid-persist), which previously made the installer
  # report CRONS_REGISTERED=true while `openclaw cron list` stayed empty.
  _detect_cron_add_flags

  # Staging dir for per-job stderr (required by register_one_with_retry).
  _STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/install-crons.XXXXXX")"
  # shellcheck disable=SC2064  # intentional expansion at trap-install time
  trap "rm -rf \"$_STAGE_DIR\"" EXIT

  # Remove existing jobs first (idempotent)
  for job_name in main-heartbeat bookmarker-cycle trader-cycle; do
    openclaw cron rm "$job_name" >>"$cron_log" 2>&1 || true
  done
  sleep 1

  # Post-rm residual check — if rm silently failed, proceeding would allow a
  # stale job to false-positive a subsequent failed add.
  local residual=""
  for job_name in main-heartbeat bookmarker-cycle trader-cycle; do
    if verify_registered "$job_name"; then
      residual="${residual:+$residual, }$job_name"
    fi
  done
  if [ -n "$residual" ]; then
    log_warn "cron rm did not clear: $residual — scheduler flap; deferring to finalize-crons.sh"
    CRON_REGISTRATION_MODE="deferred-tool"
    _write_cron_intent_artifact "$workspace"
    log_info "Finalize later with: bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace"
    return 1
  fi

  # spec: name|schedule|session|message
  local specs=(
    "main-heartbeat|*/10 * * * *|isolated|Run the main heartbeat cycle: bash $workspace/scripts/main-heartbeat.sh"
    "bookmarker-cycle|*/30 * * * *|isolated|Run the bookmarker curation cycle: bash $workspace/scripts/bookmarker-cycle.sh"
    "trader-cycle|0 * * * *|isolated|Run the trader operations cycle: bash $workspace/scripts/trader-cycle.sh"
  )

  local registered=()
  local failed=()
  local spec name sched sess msg
  for spec in "${specs[@]}"; do
    IFS='|' read -r name sched sess msg <<< "$spec"
    log_info "Registering $name ($sched)..."
    if register_one_with_retry "$name" "$sched" "$sess" "$msg"; then
      sleep 1  # propagation delay before cron list reflects the add
      if verify_registered "$name"; then
        registered+=("$name")
        log_ok "Registered + verified cron: $name"
      else
        failed+=("$name:add-zero-but-invisible")
        log_warn "cron add $name returned 0 but job not visible in openclaw cron list — treating as failed"
      fi
    else
      # register_one_with_retry may still have succeeded on a final attempt
      # that the CLI reported as failed; verify before giving up.
      if verify_registered "$name"; then
        registered+=("$name")
        log_ok "Registered cron: $name (CLI reported error but job is visible)"
      else
        failed+=("$name:$LAST_REGISTER_ERR_KIND")
        log_warn "Failed to register cron: $name ($LAST_REGISTER_ERR_KIND)"
        [ -n "$LAST_REGISTER_ERR_TAIL" ] && log_warn "  stderr tail: $LAST_REGISTER_ERR_TAIL"
      fi
    fi
  done

  if [ "${#failed[@]}" -eq 0 ]; then
    CRONS_REGISTERED=true
    CRON_REGISTRATION_MODE="local-cli"
    log_ok "All 3 cron jobs registered and verified in scheduler"
    return 0
  fi

  log_warn "Cron registration partial: succeeded=${#registered[@]}/3, failed=${failed[*]}"
  log_warn "Detailed cron registration log: $cron_log"
  # Drop to deferred-tool so the install contract reflects reality and the
  # operator gets a concrete finalize command.
  CRON_REGISTRATION_MODE="deferred-tool"
  _write_cron_intent_artifact "$workspace"
  log_info "Finalize later with: bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace"
  return 1
}

# ──────────────────────────────────────────────────────────────────────────────
# 8. install_dashboard
# ──────────────────────────────────────────────────────────────────────────────

# Read a `dashboard.public.*` boolean from config/agency.config.yaml.
# Usage: _read_dashboard_public_flag <field> <default_when_missing>
# Prints "true" or "false".
# `auto_start` falls back to legacy `enabled`; `suggest_in_install` defaults ON
# (so new operators always get a pointer toward a public URL). All other fields
# default to the supplied default. Requires PyYAML.
_read_dashboard_public_flag() {
  local field="$1"
  local default_val="${2:-false}"
  local yaml_path="$AGENCY_DIR/config/agency.config.yaml"
  if [ ! -f "$yaml_path" ]; then
    echo "$default_val"
    return 0
  fi
  python3 - "$yaml_path" "$field" "$default_val" <<'PY' 2>/dev/null || echo "$default_val"
import sys
try:
    import yaml
except ImportError:
    print(sys.argv[3])
    sys.exit(0)
try:
    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f) or {}
except Exception:
    print(sys.argv[3])
    sys.exit(0)
field = sys.argv[2]
default_val = sys.argv[3]
dash = (data.get("dashboard") or {})
pub = (dash.get("public") or {})
val = pub.get(field)
if val is None:
    if field == "auto_start" and "enabled" in pub:
        val = pub.get("enabled")
    else:
        val = default_val == "true"
print("true" if bool(val) else "false")
PY
}

# Backwards-compat shim: older call sites ask "is public enabled?" — map that
# to the new `auto_start` semantic (does the installer launch the tunnel).
_read_dashboard_public_enabled() {
  _read_dashboard_public_flag auto_start false
}

install_dashboard() {
  log_info "Step 8: Installing dashboard..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
  local dashboard_dst="$workspace/tools/self-ip-dashboard"
  local dashboard_deps_log="$workspace/logs/dashboard-deps-install.log"

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

  # 3b. If the dashboard owner reported missing Python deps, install them
  # automatically with the same python3 interpreter that dashboard-service.sh
  # uses, then retry local startup once before proceeding.
  if [ "$DASHBOARD_STATUS" = "deps_missing" ] && [ -f "$dashboard_dst/requirements.txt" ]; then
    mkdir -p "$(dirname "$dashboard_deps_log")"
    : > "$dashboard_deps_log"
    log_info "Dashboard deps missing — attempting automatic install with python3 -m pip"

    local pip_ready=false
    if python3 -m pip --version >>"$dashboard_deps_log" 2>&1; then
      pip_ready=true
    elif python3 -m ensurepip --upgrade >>"$dashboard_deps_log" 2>&1 && python3 -m pip --version >>"$dashboard_deps_log" 2>&1; then
      pip_ready=true
    fi

    if [ "$pip_ready" = "true" ] && python3 -m pip install -r "$dashboard_dst/requirements.txt" >>"$dashboard_deps_log" 2>&1; then
      log_ok "Installed dashboard Python dependencies"
      VIZ_PORT="$DASHBOARD_PORT" "$svc" start-local \
        --port "$DASHBOARD_PORT" --workspace "$workspace" || true

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
        DASHBOARD_STATUS="unknown"
      fi
    else
      log_warn "Automatic dashboard dependency install failed — see $dashboard_deps_log"
    fi
  fi

  # 4. Public exposure. Two distinct knobs:
  #     - auto_start: actually launch the tunnel during install (default OFF)
  #     - suggest_in_install: emit guidance toward a public URL (default ON)
  #    This function updates two globals consumed by the install-contract code:
  #     - DASHBOARD_PUBLIC_STATUS: disabled | running | failed | not-started
  #     - DASHBOARD_PUBLIC_GUIDE_AVAILABLE: true | false
  local public_auto_start public_suggest
  public_auto_start="$(_read_dashboard_public_flag auto_start false)"
  public_suggest="$(_read_dashboard_public_flag suggest_in_install true)"

  DASHBOARD_PUBLIC_STATUS="disabled"
  DASHBOARD_PUBLIC_GUIDE_AVAILABLE="false"
  DASHBOARD_PUBLIC_STATE_FILE="$state_file"
  DASHBOARD_PUBLIC_START_COMMAND="bash $workspace/scripts/dashboard-service.sh start-public --workspace $workspace"
  if command -v cloudflared >/dev/null 2>&1; then
    DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED="true"
    DASHBOARD_PUBLIC_INSTALL_COMMAND=""
  else
    DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED="false"
    DASHBOARD_PUBLIC_INSTALL_COMMAND="$(cloudflared_install_hint)"

    # Auto-install cloudflared when auto_start is enabled
    if [ "$public_auto_start" = "true" ]; then
      log_info "cloudflared not found — attempting automatic installation..."
      local _os_type
      _os_type="$(uname -s)"
      if [ "$_os_type" = "Linux" ]; then
        # Linux: try apt-get first, then direct binary download
        local _cf_installed=false
        if command -v apt-get >/dev/null 2>&1; then
          log_info "Installing cloudflared via apt-get..."
          if sudo apt-get install -y cloudflared 2>&1; then
            _cf_installed=true
            log_ok "cloudflared installed successfully via apt-get"
          else
            log_warn "Failed to install cloudflared via apt-get — trying direct download..."
          fi
        fi
        if [ "$_cf_installed" = "false" ]; then
          local arch="amd64"
          [ "$(uname -m)" = "aarch64" ] && arch="arm64"
          log_info "Downloading cloudflared binary for linux-${arch}..."
          if curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}" -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared; then
            _cf_installed=true
            log_ok "cloudflared installed via direct download"
          else
            log_warn "Failed to download cloudflared binary"
          fi
        fi
        if [ "$_cf_installed" = "true" ]; then
          DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED="true"
          DASHBOARD_PUBLIC_INSTALL_COMMAND=""
        fi
      elif command -v brew >/dev/null 2>&1; then
        # macOS with Homebrew
        log_info "Installing cloudflared via Homebrew..."
        if brew install cloudflared 2>&1; then
          DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED="true"
          DASHBOARD_PUBLIC_INSTALL_COMMAND=""
          log_ok "cloudflared installed successfully"
        else
          log_warn "Failed to install cloudflared via brew"
        fi
      else
        log_warn "Cannot auto-install cloudflared — install manually: $DASHBOARD_PUBLIC_INSTALL_COMMAND"
      fi
    fi
  fi

  if [ "$public_auto_start" = "true" ] && [ "$DASHBOARD_STATUS" = "running" ] && [ "$DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED" = "true" ]; then
    log_info "dashboard.public.auto_start=true → starting public tunnel..."
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
  elif [ "$public_auto_start" = "true" ]; then
    if [ "$DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED" != "true" ]; then
      log_warn "dashboard.public.auto_start=true but cloudflared could not be installed — skipping public tunnel"
    else
      log_warn "dashboard.public.auto_start=true but local dashboard is not running — skipping public tunnel"
    fi
    DASHBOARD_PUBLIC_STATUS="failed"
  else
    # auto_start is OFF — decide whether to surface guidance.
    if [ "$public_suggest" = "true" ] && [ "$DASHBOARD_STATUS" = "running" ]; then
      # Don't overwrite a tunnel that's already running from a previous invocation.
      local existing_public_status="disabled"
      if [ -f "$state_file" ]; then
        existing_public_status="$(python3 -c "
import json
try:
    d = json.load(open('$state_file'))
    print(d.get('public', {}).get('status') or 'disabled')
except Exception:
    print('disabled')
" 2>/dev/null || echo "disabled")"
      fi
      if [ "$existing_public_status" = "running" ]; then
        DASHBOARD_PUBLIC_STATUS="running"
        DASHBOARD_PUBLIC_URL="$(python3 -c "
import json
try:
    d = json.load(open('$state_file'))
    print(d.get('public', {}).get('url') or '')
except Exception:
    print('')
" 2>/dev/null || echo "")"
      else
        DASHBOARD_PUBLIC_STATUS="not-started"
        DASHBOARD_PUBLIC_GUIDE_AVAILABLE="true"
      fi
    fi
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# 9. seed_raw_docs
# ──────────────────────────────────────────────────────────────────────────────

seed_raw_docs() {
  log_info "Step 9: Seeding raw knowledge base..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would seed raw docs under: $workspace/raw"
    RAW_SEED_STATUS="dry-run"
    return 0
  fi

  local seed_script="$AGENCY_DIR/scripts/seed-raw-docs.sh"
  if [ ! -f "$seed_script" ]; then
    log_warn "seed-raw-docs.sh not found — skipping raw seeding"
    RAW_SEED_STATUS="failed"
    return 0
  fi

  if bash "$seed_script" --workspace "$workspace" 2>&1; then
    RAW_SEED_STATUS="ok"
    log_ok "Raw knowledge base seeded"
  else
    RAW_SEED_STATUS="partial"
    log_warn "Raw seeding had issues (non-fatal) — some sources may be missing"
  fi

  # Check if the summary exists to confirm at least partial success
  if [ -f "$workspace/raw/_seed-summary.json" ]; then
    local _fetched _total
    _fetched="$(python3 -c "import json; d=json.load(open('$workspace/raw/_seed-summary.json')); print(d.get('sources_fetched', 0))" 2>/dev/null || echo "0")"
    _total="$(python3 -c "import json; d=json.load(open('$workspace/raw/_seed-summary.json')); print(d.get('sources_total', 0))" 2>/dev/null || echo "0")"
    if [ "${_fetched:-0}" -eq "${_total:-0}" ] && [ "${_total:-0}" -gt 0 ]; then
      RAW_SEED_STATUS="ok"
    elif [ "${_fetched:-0}" -gt 0 ]; then
      RAW_SEED_STATUS="partial"
    else
      RAW_SEED_STATUS="failed"
    fi
    log_info "Raw seeding result: ${_fetched}/${_total} sources fetched"
  fi
  return 0
}

# ──────────────────────────────────────────────────────────────────────────────
# 10. sync_guided_x_tweets
# ──────────────────────────────────────────────────────────────────────────────

sync_guided_x_tweets() {
  log_info "Step 10: Bootstrapping guided X tweets into raw/..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would sync guided X tweets into: $workspace/raw/x-tweets"
    X_TWEETS_SEED_STATUS="dry-run"
    return 0
  fi

  local sync_script="$AGENCY_DIR/scripts/sync_guided_x_tweets.py"
  if [ ! -f "$sync_script" ]; then
    log_warn "sync_guided_x_tweets.py not found — skipping guided X bootstrap"
    X_TWEETS_SEED_STATUS="failed"
    X_TWEETS_BLOCKERS='["sync_script_missing"]'
    return 0
  fi

  local _tmp_json
  _tmp_json="$(mktemp)"
  if python3 "$sync_script" --workspace "$workspace" --lookback-days 3 --include-replies --json >"$_tmp_json" 2>/dev/null; then
    X_TWEETS_SEED_STATUS="$(python3 -c "import json; d=json.load(open('$_tmp_json')); print(d.get('status','failed'))" 2>/dev/null || echo "failed")"
    X_TWEETS_BLOCKERS="$(python3 -c "import json; d=json.load(open('$_tmp_json')); import json as _j; print(_j.dumps(d.get('blockers', []), ensure_ascii=False))" 2>/dev/null || echo '[]')"
    local _written _skipped _found
    _written="$(python3 -c "import json; d=json.load(open('$_tmp_json')); print(d.get('items_written', 0))" 2>/dev/null || echo "0")"
    _skipped="$(python3 -c "import json; d=json.load(open('$_tmp_json')); print(d.get('items_skipped_existing', 0))" 2>/dev/null || echo "0")"
    _found="$(python3 -c "import json; d=json.load(open('$_tmp_json')); print(d.get('tweet_urls_found', 0))" 2>/dev/null || echo "0")"
    log_info "Guided X sync result: status=${X_TWEETS_SEED_STATUS}, found=${_found}, written=${_written}, skipped=${_skipped}"
  else
    X_TWEETS_SEED_STATUS="failed"
    X_TWEETS_BLOCKERS='["sync_command_failed"]'
    log_warn "Guided X sync command failed"
  fi
  rm -f "$_tmp_json" 2>/dev/null || true
  return 0
}

# ──────────────────────────────────────────────────────────────────────────────
# 11. compile_x_tweets_wiki
# ──────────────────────────────────────────────────────────────────────────────

compile_x_tweets_wiki() {
  log_info "Step 11: Compiling guided X tweets into wiki synthesis..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would compile raw/x-tweets into wiki/synthesis/tweets"
    X_TWEETS_COMPILE_STATUS="dry-run"
    return 0
  fi

  local compile_script="$AGENCY_DIR/scripts/build_x_tweets_wiki_v1.py"
  if [ ! -f "$compile_script" ]; then
    log_warn "build_x_tweets_wiki_v1.py not found — skipping X tweet wiki compile"
    X_TWEETS_COMPILE_STATUS="failed"
    return 0
  fi

  local _tmp_json
  _tmp_json="$(mktemp)"
  if python3 "$compile_script" --workspace "$workspace" --json >"$_tmp_json" 2>/dev/null; then
    X_TWEETS_COMPILE_STATUS="$(python3 -c "import json; d=json.load(open('$_tmp_json')); print(d.get('status','failed'))" 2>/dev/null || echo "failed")"
    X_TWEETS_COMPILED_COUNT="$(python3 -c "import json; d=json.load(open('$_tmp_json')); print(d.get('compiled_items', 0))" 2>/dev/null || echo "0")"
    log_info "Guided X wiki compile result: status=${X_TWEETS_COMPILE_STATUS}, compiled=${X_TWEETS_COMPILED_COUNT}"
  else
    X_TWEETS_COMPILE_STATUS="failed"
    log_warn "Guided X wiki compile command failed"
  fi
  rm -f "$_tmp_json" 2>/dev/null || true
  return 0
}

# ──────────────────────────────────────────────────────────────────────────────
# 12. publish_intro_post
# ──────────────────────────────────────────────────────────────────────────────

publish_intro_post() {
  log_info "Step 10: Self-introduction post..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  # Duplicate guard: marker file
  if [ -f "$workspace/.intro-post-published" ]; then
    INTRO_POST_STATUS="already_published"
    log_info "Intro post already published — skipping"
    return 0
  fi

  if [ "$DRY_RUN" = "true" ]; then
    INTRO_POST_STATUS="dry-run"
    log_info "[DRY RUN] Would publish self-introduction post"
    return 0
  fi

  # ── Strict ready gating ──────────────────────────────────────────────────
  # Auto-post requires ALL of:
  #   1. TagClaw credentials present
  #   2. Agent identity resolved
  #   3. Cron registration finalized or acceptably deferred
  #   4. Dashboard running (public exposure confirmed)
  #   5. .intro-post-published marker absent (checked above)
  #
  # If any condition is unmet, defer as a structured next-step.
  local _gate_reasons=()

  if ! has_tagclaw_credentials; then
    _gate_reasons+=("credentials_missing")
  fi

  if [ "$IDENTITY_RESOLVED" != "true" ]; then
    _gate_reasons+=("identity_not_resolved")
  fi

  # Cron readiness: registered OR acceptably deferred
  local _cron_ready=false
  if [ "$CRONS_REGISTERED" = "true" ]; then
    _cron_ready=true
  elif [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ]; then
    _cron_ready=true
  fi
  if [ "$_cron_ready" != "true" ]; then
    _gate_reasons+=("cron_not_ready")
  fi

  # Dashboard readiness: must be running
  if [ "$DASHBOARD_STATUS" != "running" ]; then
    _gate_reasons+=("dashboard_not_ready:${DASHBOARD_STATUS}")
  fi

  if [ "${#_gate_reasons[@]}" -gt 0 ]; then
    INTRO_POST_STATUS="skipped"
    local _reasons_str
    _reasons_str="$(printf '%s, ' "${_gate_reasons[@]}")"
    _reasons_str="${_reasons_str%, }"
    log_info "Intro post deferred — gating unmet: ${_reasons_str}"
    return 0
  fi

  local publish_script="$AGENCY_DIR/scripts/publish-intro-post.sh"
  if [ ! -f "$publish_script" ]; then
    INTRO_POST_STATUS="failed"
    log_warn "publish-intro-post.sh not found — skipping intro post"
    return 0
  fi

  # ── Resolve tick before publishing ──────────────────────────────────────
  local tick_resolver="$AGENCY_DIR/scripts/resolve-intro-post-tick.py"
  local _tick_args=()
  if [ -f "$tick_resolver" ]; then
    local tick_json
    tick_json="$(python3 "$tick_resolver" --workspace "$workspace" 2>/dev/null)" || true
    if [ -n "$tick_json" ]; then
      INTRO_POST_TICK="$(echo "$tick_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('resolved_tick',''))" 2>/dev/null)" || INTRO_POST_TICK=""
      INTRO_POST_TICK_STATUS="$(echo "$tick_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)" || INTRO_POST_TICK_STATUS=""
      INTRO_POST_TICK_SOURCE="$(echo "$tick_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('source',''))" 2>/dev/null)" || INTRO_POST_TICK_SOURCE=""
      INTRO_POST_TICK_CANDIDATES="$(echo "$tick_json" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('candidates',[])))" 2>/dev/null)" || INTRO_POST_TICK_CANDIDATES="[]"
      log_info "Tick resolved: ${INTRO_POST_TICK} (source: ${INTRO_POST_TICK_SOURCE}, status: ${INTRO_POST_TICK_STATUS})"
    fi
    if [ -n "$INTRO_POST_TICK" ]; then
      _tick_args=("--tick" "$INTRO_POST_TICK")
    fi
  fi

  local publish_output
  publish_output="$(bash "$publish_script" --workspace "$workspace" "${_tick_args[@]}" 2>&1)" || true
  local rc=$?

  # Parse the outcome from the script's stdout (machine-readable lines)
  local outcome
  outcome="$(echo "$publish_output" | grep -oP '(?<=^outcome=).+' | tail -1)" || outcome=""

  if [ "$rc" -eq 0 ]; then
    if [ "$outcome" = "published_but_marker_failed" ]; then
      INTRO_POST_STATUS="published_but_marker_failed"
      log_warn "Intro post published but marker write failed — duplicate guard not set"
    else
      INTRO_POST_STATUS="published"
      log_ok "Self-introduction post published"
    fi
  elif [ "$rc" -eq 1 ]; then
    INTRO_POST_STATUS="skipped"
    log_info "Intro post prerequisites not met — deferred"
  else
    INTRO_POST_STATUS="failed"
    log_warn "Failed to publish intro post (non-fatal)"
  fi
  return 0
}

# ──────────────────────────────────────────────────────────────────────────────
# 7b. wait_for_tagclaw_activation
# ──────────────────────────────────────────────────────────────────────────────

wait_for_tagclaw_activation() {
  log_info "Checking TagClaw activation status before registering crons..."

  local workspace
  workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

  # If no credentials at all, skip — earlier steps will have warned about this
  if ! has_tagclaw_credentials; then
    log_warn "No TagClaw credentials found — skipping activation check"
    return 1
  fi

  local tagclaw_status
  tagclaw_status="$(resolve_tagclaw_skill_env_field "TAGCLAW_STATUS" "$workspace")"

  if [ "$tagclaw_status" = "active" ]; then
    log_ok "TagClaw account is already active — proceeding"
    return 0
  fi

  if [ "$tagclaw_status" != "pending_verification" ]; then
    log_warn "TagClaw status is '$tagclaw_status' (expected 'active' or 'pending_verification') — skipping activation wait"
    return 1
  fi

  # Status is pending_verification — print the tweet, write the handoff
  # file, and EXIT. Auto-polling here was useless whenever install was
  # driven by an agent that captured stdout without surfacing the ACTION
  # REQUIRED block: the operator never saw the prompt, never posted the
  # tweet, and the loop burned an hour hitting /status. The two-phase
  # flow is explicit now:
  #   phase 1 (this install.sh run) — surface the tweet + exit
  #   phase 2 (operator runs post-verify-finalize after posting)
  #     — poll for activation, re-invoke install.sh which reaches active
  #       branch above and proceeds with crons + dashboard
  local agent_username verification_code profile_url
  agent_username="$(resolve_tagclaw_skill_env_field "TAGCLAW_AGENT_USERNAME" "$workspace")"
  verification_code="$(resolve_tagclaw_skill_env_field "TAGCLAW_VERIFICATION_CODE" "$workspace")"
  profile_url="$(resolve_tagclaw_skill_env_field "TAGCLAW_PROFILE_URL" "$workspace")"

  # Write the handoff file FIRST so the artifact exists even if an outer
  # harness truncates or filters the stdout box below.
  local tweet_file="$workspace/tagclaw-verification-tweet.txt"
  cat > "$tweet_file" <<EOF
I'm claiming my AI agent "$agent_username" on @TagClaw
Verification: "$verification_code"
EOF

  echo ""
  echo "  ╔══════════════════════════════════════════════════════════════════════╗"
  echo "  ║  ACTION REQUIRED: Post verification tweet on X (Twitter)             ║"
  echo "  ╠══════════════════════════════════════════════════════════════════════╣"
  echo "  ║"
  echo "  ║  Post this exact tweet:"
  echo "  ║"
  echo "  ║    I'm claiming my AI agent \"$agent_username\" on @TagClaw"
  echo "  ║    Verification: \"$verification_code\""
  echo "  ║"
  echo "  ║  Tweet text also saved to:"
  echo "  ║    $tweet_file"
  if [ -n "$profile_url" ]; then
    echo "  ║"
    echo "  ║  Profile after activation: $profile_url"
  fi
  echo "  ║"
  echo "  ║  AFTER posting the tweet, finish install by running:"
  echo "  ║"
  echo "  ║    bash $workspace/scripts/tagclaw-onboard.sh \\"
  echo "  ║         post-verify-finalize --workspace $workspace"
  echo "  ║"
  echo "  ║  That will poll for activation, register cron jobs, and start the"
  echo "  ║  dashboard. No need to re-run install.sh manually."
  echo "  ╚══════════════════════════════════════════════════════════════════════╝"
  echo ""
  log_info "Install paused — awaiting X verification tweet. See box above for the finalize command."

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would exit install here pending tweet post"
  fi
  return 1
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
  # Non-interactive owner twitter binding collection runs BEFORE onboarding so
  # TAGCLAW_EXPECTED_TWITTER_HANDLE is already in skill .env when
  # refresh-agency-identity.sh executes later. See §4.2 of the design doc.
  collect_owner_twitter_binding
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
  seed_raw_docs
  sync_guided_x_tweets

  # ── Gate: wait for TagClaw verification before registering crons / dashboard ──
  local TAGCLAW_ACTIVATED=false
  if wait_for_tagclaw_activation; then
    TAGCLAW_ACTIVATED=true
    register_crons || true
    install_dashboard
    sync_guided_x_tweets
    compile_x_tweets_wiki
  else
    log_warn "Skipping cron registration and dashboard setup — TagClaw not yet activated"
    local _ws_hint
    _ws_hint="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
    log_info "After posting the verification tweet, run: bash $_ws_hint/scripts/tagclaw-onboard.sh post-verify-finalize --workspace $_ws_hint"
  fi

  if [ "$DRY_RUN" = "false" ]; then
    local workspace
    workspace="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"

    # ── Detect onboarding state ─────────────────────────────────────────────
    if has_tagclaw_credentials; then
      CREDENTIALS_EXIST=true
    fi

    # ── Resolve owner_binding status from workspace identity JSON ───────────
    # Reads owner.verified / owner.twitter_handle / owner.binding_source written
    # by refresh-agency-identity.sh and maps to a compact status the next-steps
    # builder, summary box, and dashboard can all consume. See §4.5 of the
    # design doc for the UX contract: cloud install NEVER fails on unresolved
    # binding alone — heartbeat self-heal picks it up.
    _resolve_owner_binding() {
      local ws_ident="$workspace/config/agency-identity.json"
      if [ ! -f "$ws_ident" ]; then
        OWNER_BINDING_STATUS="unresolved"
        OWNER_BINDING_REASON="identity_not_written"
        return 0
      fi
      local _summary
      _summary="$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print('error|' + str(e))
    raise SystemExit(0)
o = d.get('owner') or {}
handle = (o.get('twitter_handle') or '').strip()
verified = bool(o.get('verified'))
src = (o.get('binding_source') or '').strip()
print('ok|' + str(verified) + '|' + handle + '|' + src)
" "$ws_ident" 2>/dev/null)"
      local _ok="${_summary%%|*}"
      if [ "$_ok" != "ok" ]; then
        OWNER_BINDING_STATUS="unknown"
        OWNER_BINDING_REASON="identity_parse_error"
        return 0
      fi
      local _rest="${_summary#ok|}"
      local _verified="${_rest%%|*}"; _rest="${_rest#*|}"
      local _handle="${_rest%%|*}"; _rest="${_rest#*|}"
      local _src="$_rest"
      if [ "$_verified" = "True" ]; then
        OWNER_BINDING_STATUS="verified"
        OWNER_BINDING_REASON="verified_via_me"
      elif [ -n "$_handle" ]; then
        OWNER_BINDING_STATUS="declared"
        OWNER_BINDING_REASON="declared_pending_verify"
      else
        OWNER_BINDING_STATUS="unresolved"
        OWNER_BINDING_REASON="awaiting_tagclaw_me_or_post_verify"
      fi
    }
    _resolve_owner_binding

    # ── P0-C: Compute truthful install status ───────────────────────────────
    # "verified" requires: identity resolved + credentials exist + dashboard running
    #            + crons registered OR acceptably deferred
    # "partial"  is anything less
    # "failed"   only if core install steps (runtime/wiki/autoresearch) failed
    #
    # IMPORTANT: OWNER_BINDING_STATUS is deliberately NOT a gating factor.
    # Per design §4.5 + §7 #4, cloud headless installs must exit 0 even when
    # owner binding is still unresolved — heartbeat self-heal will upgrade
    # identity JSON once TagClaw /me returns the binding. Blocking install on
    # this would regress every deployment where the operator posts the
    # verification tweet asynchronously.
    local INSTALL_STATUS="partial"
    local _crons_acceptable=false
    if [ "$CRONS_REGISTERED" = "true" ]; then
      _crons_acceptable=true
    elif [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ]; then
      _crons_acceptable=true
    fi
    if [ "$IDENTITY_RESOLVED" = "true" ] && \
       [ "$CREDENTIALS_EXIST" = "true" ] && \
       [ "$_crons_acceptable" = "true" ] && \
       [ "$DASHBOARD_STATUS" = "running" ]; then
      INSTALL_STATUS="verified"
    fi

    # Dashboard readiness: independent of cron state
    local DASHBOARD_READY=false
    if [ "$DASHBOARD_STATUS" = "running" ]; then
      DASHBOARD_READY=true
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
    # clipboard/per-line fields for kind-aware consumers. Human-facing
    # `post_action` stays intentionally simple; automation should use the
    # explicit `agent_command`.
    _emit_step_verification_tweet() {
      local _agent="$1" _code="$2" _vfile="$3" _agent_cmd="$4"
      local _line1 _line2 _copy _flat
      _line1="I'm claiming my AI agent \"$_agent\" on @TagClaw"
      _line2="Verification: \"$_code\""
      _copy="$(printf '%s\n%s' "$_line1" "$_line2")"
      _flat="$(printf 'Post this verification tweet on X:\n%s\n%s' "$_line1" "$_line2")"
      NEXT_STEPS_TEXT+=("$_flat")
      STEP_KINDS+=("x_verification_tweet")
      STEP_PAYLOADS+=("$(python3 -c '
import json, sys
agent, code, vfile, agent_cmd = sys.argv[1:]
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
    "post_action": "Tell me once the tweet is live.",
    "agent_command": agent_cmd,
    "agent_behavior": "When the user confirms the tweet is live, run agent_command silently and continue until a usable public dashboard URL is available.",
}))
' "$_agent" "$_code" "$_vfile" "$_agent_cmd")")
    }

    if [ "$CREDENTIALS_EXIST" != "true" ]; then
      _emit_step_simple "rerun_install" \
        "Re-run install to complete TagClaw onboarding: bash scripts/install.sh"
      _emit_step_simple "run_onboard_helper" \
        "Or run helper directly: bash $workspace/scripts/tagclaw-onboard.sh full --workspace $workspace"
    fi

    if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
      local _FINALIZE_CMD="bash $workspace/scripts/tagclaw-onboard.sh post-verify-finalize --workspace $workspace"
      _emit_step_verification_tweet \
        "$TAGCLAW_AGENT_USERNAME" \
        "$TAGCLAW_VERIFICATION_CODE" \
        "$VERIFICATION_TWEET_FILE" \
        "$_FINALIZE_CMD"
      _emit_step_simple "await_user_confirmation" \
        "Tell me once the verification tweet is live."
    elif [ "$TAGCLAW_STATUS" != "active" ]; then
      _emit_step_simple "await_user_confirmation" \
        "Tell me once the verification tweet is live."
    elif [ "$CRONS_REGISTERED" != "true" ] || [ "$DASHBOARD_STATUS" = "not_attempted" ]; then
      _emit_step_simple "finalize_post_verification" \
        "Finish cron registration and dashboard setup: bash $AGENCY_DIR/scripts/install.sh"
    fi

    if [ "$CREDENTIALS_EXIST" = "true" ]; then
      _emit_step_simple "verify_env_files" \
        "Verify $workspace/skills/tagclaw/.env contains TAGCLAW_API_KEY; confirm $workspace/skills/tagclaw-wallet/.env exists with mode 600 (do not cat its contents — it holds Steem private keys)"
    fi

    # Guided X sync next-step text depends on whether the blocker is an
    # unresolved owner binding (auto-heals via heartbeat — no operator action
    # required) or something downstream. See design §4.5.
    if [ "$X_TWEETS_SEED_STATUS" = "deferred" ] || [ "$X_TWEETS_SEED_STATUS" = "blocked" ] || [ "$X_TWEETS_SEED_STATUS" = "failed" ]; then
      if [ "$OWNER_BINDING_STATUS" = "unresolved" ]; then
        _emit_step_simple "owner_binding_pending" \
          "Owner X binding not yet resolved. Will auto-heal on next heartbeat once TagClaw /me returns the handle. No operator action required; optional manual path: rerun install.sh with --owner-twitter-handle, or bash $workspace/scripts/tagclaw-onboard.sh post-verify-finalize --workspace $workspace"
      elif [ "$OWNER_BINDING_STATUS" = "declared" ]; then
        _emit_step_simple "owner_binding_declared_pending_verify" \
          "Owner X handle declared (source=$OWNER_TWITTER_HANDLE_SOURCE) but not yet verified by TagClaw /me. Heartbeat self-heal will upgrade to verified once /me confirms. No action required."
      else
        _emit_step_simple "guided_x_sync" \
          "Complete guided X sync bootstrap: optionally create runtime/shared/guided-x-urls.json from a browser-guided session, then run: python3 $AGENCY_DIR/scripts/sync_guided_x_tweets.py --workspace $workspace --lookback-days 3 --include-replies --json"
      fi
    fi

    if [ "$CRONS_REGISTERED" != "true" ] && [ "$TAGCLAW_STATUS" = "active" ]; then
      if [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ]; then
        local _finalize_cmd="bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace"
        NEXT_STEPS_TEXT+=("Reply \"同意创建 cron\" to authorize the agent to create the deferred OpenClaw cron jobs from $CRON_INTENT_PATH. CLI fallback after device approval: ${_finalize_cmd}")
        STEP_KINDS+=("finalize_crons")
        STEP_PAYLOADS+=("$(python3 -c '
import json, sys
cmd = sys.argv[1]
intent = sys.argv[2]
print(json.dumps({
    "kind": "finalize_crons",
    "title": "Approve deferred cron registration",
    "action": "Reply \"同意创建 cron\" to authorize the agent/tool path to create the deferred OpenClaw cron jobs.",
    "command": cmd,
    "intent_artifact": intent,
    "approval_required": True,
    "approval_reply": "同意创建 cron",
    "auto_dispatchable": True,
    "preferred_agent_behavior": "Wait for the user to reply exactly \"同意创建 cron\" in this conversation or a connected Telegram channel. Then read intent_artifact and create each jobs[] entry with the native OpenClaw cron tool. Use command only as a CLI fallback after any required OpenClaw device scope-upgrade approval.",
    "preferred_install_mode": "SELF_IP_AGENCY_CRON_REGISTRATION_MODE=deferred-tool",
    "tg_channel_behavior": "Supported only when the Telegram channel routes to an already-approved OpenClaw agent/operator with native cron tool access. A read-only CLI device still needs explicit OpenClaw device approval.",
    "exit_codes": {"0": "success", "1": "precondition_failure", "2": "scheduler_unreachable", "3": "partial_registration"},
}))
' "$_finalize_cmd" "$CRON_INTENT_PATH")")
      else
        _emit_step_simple "register_crons" \
          "Register cron jobs manually (openclaw CLI was not available during install)"
      fi
    fi

    if [ "$DASHBOARD_STATUS" = "not_attempted" ] && [ "$TAGCLAW_STATUS" = "active" ]; then
      _emit_step_simple "start_dashboard" \
        "Or start the local dashboard directly: bash $workspace/scripts/dashboard-service.sh start-local --workspace $workspace"
    fi

    if [ "$DASHBOARD_STATUS" = "deps_missing" ]; then
      _emit_step_simple "install_dashboard_deps" \
        "Install dashboard deps: pip3 install -r dashboard/requirements.txt"
    fi

    if [ "$DASHBOARD_PUBLIC_STATUS" = "failed" ]; then
      _emit_step_simple "install_cloudflared" \
        "Public dashboard tunnel failed to start. Install cloudflared ($(cloudflared_install_hint)) then run: bash $workspace/scripts/dashboard-service.sh start-public"
    fi

    # Public dashboard guidance: emitted when the local dashboard is running,
    # auto_start=false (default), and no tunnel is already up. The step is
    # structured so operator UIs can surface install_command + run_command
    # separately and mark the whole thing `recommended: true, required: false`.
    if [ "$DASHBOARD_PUBLIC_GUIDE_AVAILABLE" = "true" ] && [ "$DASHBOARD_PUBLIC_STATUS" = "not-started" ]; then
      local _guide_install_cmd="$DASHBOARD_PUBLIC_INSTALL_COMMAND"
      local _guide_start_cmd="$DASHBOARD_PUBLIC_START_COMMAND"
      local _guide_state_file="$DASHBOARD_PUBLIC_STATE_FILE"
      local _guide_title="Enable a public dashboard URL (optional, recommended)"
      local _guide_action
      if [ "$DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED" = "true" ]; then
        _guide_action="Expose your dashboard publicly via Cloudflare Quick Tunnel. Run: ${_guide_start_cmd}"
      else
        _guide_action="Expose your dashboard publicly via Cloudflare Quick Tunnel. First install cloudflared (${_guide_install_cmd}), then run: ${_guide_start_cmd}"
      fi
      NEXT_STEPS_TEXT+=("$_guide_action")
      STEP_KINDS+=("dashboard_public_exposure")
      STEP_PAYLOADS+=("$(python3 -c '
import json, sys
(title, action, install_cmd, start_cmd, state_file, cloudflared_installed) = sys.argv[1:]
payload = {
    "kind": "dashboard_public_exposure",
    "title": title,
    "action": action,
    "recommended": True,
    "required": False,
    "prerequisites": ["local_dashboard_running", "cloudflared_installed"],
    "cloudflared_installed": cloudflared_installed == "true",
    "install_command": install_cmd,
    "run_command": start_cmd,
    "state_file": state_file,
    "result_field": "public.url",
}
print(json.dumps(payload))
' "$_guide_title" "$_guide_action" "$_guide_install_cmd" "$_guide_start_cmd" "$_guide_state_file" "$DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED")")
    fi

    # Intro post deferred step: if intro post was not published during this run
    # and TagClaw is active, emit a structured next-step so the agent can retry.
    # The auto-post path requires: cron_ready + dashboard_running + credentials + identity.
    if [ "$INTRO_POST_STATUS" = "skipped" ] || [ "$INTRO_POST_STATUS" = "failed" ] || [ "$INTRO_POST_STATUS" = "not_attempted" ]; then
      if [ "$TAGCLAW_STATUS" = "active" ] && [ "$CREDENTIALS_EXIST" = "true" ]; then
        _emit_step_simple "publish_intro_post" \
          "Publish self-introduction post (requires crons registered + dashboard running): bash $AGENCY_DIR/scripts/publish-intro-post.sh --workspace $workspace"
      fi
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
    'crons_registered': '$CRONS_REGISTERED' == 'true',
    'cron_registration_mode': '$CRON_REGISTRATION_MODE',
    'cron_registration_status': 'registered' if '$CRONS_REGISTERED' == 'true' else ('pending_finalization' if '$CRON_REGISTRATION_MODE' == 'deferred-tool' else ('blocked' if '$CRON_REGISTRATION_MODE' == 'blocked' else 'pending')),
    'cron_finalize_command': 'bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace' if '$CRON_REGISTRATION_MODE' == 'deferred-tool' and '$CRONS_REGISTERED' != 'true' else '',
    'cron_intent_path': '$CRON_INTENT_PATH',
    'dashboard_ready': '$DASHBOARD_READY' == 'true',
    'dashboard_status': '$DASHBOARD_STATUS',
    'dashboard_local_status': '$DASHBOARD_STATUS',
    'dashboard_public_status': '$DASHBOARD_PUBLIC_STATUS',
    'dashboard_public_url': '$DASHBOARD_PUBLIC_URL',
    'dashboard_public_guide_available': '$DASHBOARD_PUBLIC_GUIDE_AVAILABLE' == 'true',
    'tagclaw_onboard_status': '$TAGCLAW_ONBOARD_STATUS',
    'identity_resolved': $([ "$IDENTITY_RESOLVED" = "true" ] && echo "True" || echo "False"),
    'credentials_exist': $([ "$CREDENTIALS_EXIST" = "true" ] && echo "True" || echo "False"),
    'raw_seed_status': '$RAW_SEED_STATUS',
    'x_tweets_seed_status': '$X_TWEETS_SEED_STATUS',
    'x_tweets_compile_status': '$X_TWEETS_COMPILE_STATUS',
    'x_tweets_compiled_count': int('$X_TWEETS_COMPILED_COUNT' or '0'),
    'x_tweets_blockers': json.loads('''$X_TWEETS_BLOCKERS'''),
    'intro_post_status': '$INTRO_POST_STATUS',
    'intro_post_tick': '$INTRO_POST_TICK',
    'intro_post_tick_status': '$INTRO_POST_TICK_STATUS',
    'intro_post_tick_source': '$INTRO_POST_TICK_SOURCE',
    'schema': 'installed.v7'
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

    # ── Phase 1 Bootstrap: run first cycles synchronously ───────────────
    # Instead of leaving the system in "bootstrap" state until cron triggers
    # the first cycle (up to 60 min), run each ready agent's first cycle now.
    # This transitions runtime-status.json from bootstrap → completed and
    # gives the dashboard real artifacts to display immediately.
    local MAIN_BOOTSTRAPPED=false BOOKMARKER_BOOTSTRAPPED=false TRADER_BOOTSTRAPPED=false
    local BOOTSTRAP_ATTEMPTED=false

    # Resolve timeout command (GNU coreutils: timeout or gtimeout; fallback: none)
    local _TIMEOUT_CMD=""
    if command -v timeout &>/dev/null; then
      _TIMEOUT_CMD="timeout"
    elif command -v gtimeout &>/dev/null; then
      _TIMEOUT_CMD="gtimeout"
    fi

    if [ "$TAGCLAW_ACTIVATED" = "true" ]; then
      local _any_ready=false
      [ "$MAIN_READY" = "true" ] || [ "$BOOKMARKER_READY" = "true" ] || [ "$TRADER_READY" = "true" ] && _any_ready=true

      if [ "$_any_ready" = "true" ]; then
        BOOTSTRAP_ATTEMPTED=true
        echo ""
        echo "  ┌──────────────────────────────────────────────────────┐"
        echo "  │  Phase 1 Bootstrap — running first agent cycles...  │"
        echo "  └──────────────────────────────────────────────────────┘"
        echo ""

        # Helper: run a command with optional timeout
        _run_with_timeout() {
          local secs="$1"; shift
          if [ -n "$_TIMEOUT_CMD" ]; then
            "$_TIMEOUT_CMD" "$secs" "$@"
          else
            "$@"
          fi
        }

        # 1. Main heartbeat (timeout 120s — builds input packet + orchestrator)
        if [ "$MAIN_READY" = "true" ]; then
          log_info "Running first main heartbeat cycle..."
          if _run_with_timeout 120 bash "$workspace/scripts/main-heartbeat.sh" 2>&1; then
            MAIN_BOOTSTRAPPED=true
            log_ok "Main heartbeat bootstrap cycle completed"
          else
            log_warn "Main heartbeat bootstrap cycle failed (non-fatal, cron will retry)"
          fi
        fi

        # 2. Bookmarker cycle (timeout 180s — social curation)
        if [ "$BOOKMARKER_READY" = "true" ]; then
          log_info "Running first bookmarker cycle..."
          if _run_with_timeout 180 bash "$workspace/scripts/bookmarker-cycle.sh" 2>&1; then
            BOOKMARKER_BOOTSTRAPPED=true
            log_ok "Bookmarker bootstrap cycle completed"
          else
            log_warn "Bookmarker bootstrap cycle failed (non-fatal, cron will retry)"
          fi
        fi

        # 3. Trader cycle (timeout 300s — on-chain ops)
        if [ "$TRADER_READY" = "true" ]; then
          log_info "Running first trader cycle..."
          if _run_with_timeout 300 bash "$workspace/scripts/trader-cycle.sh" 2>&1; then
            TRADER_BOOTSTRAPPED=true
            log_ok "Trader bootstrap cycle completed"
          else
            log_warn "Trader bootstrap cycle failed (non-fatal, cron will retry)"
          fi
        fi

        # Summary
        local _bs_ok=0 _bs_total=0
        [ "$MAIN_READY" = "true" ] && _bs_total=$((_bs_total + 1))
        [ "$BOOKMARKER_READY" = "true" ] && _bs_total=$((_bs_total + 1))
        [ "$TRADER_READY" = "true" ] && _bs_total=$((_bs_total + 1))
        [ "$MAIN_BOOTSTRAPPED" = "true" ] && _bs_ok=$((_bs_ok + 1))
        [ "$BOOKMARKER_BOOTSTRAPPED" = "true" ] && _bs_ok=$((_bs_ok + 1))
        [ "$TRADER_BOOTSTRAPPED" = "true" ] && _bs_ok=$((_bs_ok + 1))

        if [ "$_bs_ok" -eq "$_bs_total" ] && [ "$_bs_total" -gt 0 ]; then
          log_ok "Bootstrap complete: $_bs_ok/$_bs_total agent cycles succeeded"
        elif [ "$_bs_ok" -gt 0 ]; then
          log_warn "Bootstrap partial: $_bs_ok/$_bs_total agent cycles succeeded"
        else
          log_warn "Bootstrap cycles all failed — dashboard will show bootstrap state until cron succeeds"
        fi

        # Refresh dashboard if it's running so it picks up new artifacts
        if [ "$DASHBOARD_STATUS" = "running" ]; then
          log_info "Refreshing dashboard to reflect bootstrap artifacts..."
          curl -s "http://localhost:${DASHBOARD_PORT:-8765}/api/health" >/dev/null 2>&1 || true
        fi
        echo ""
      fi

      # ── Intro post: publish after bootstrap when fully operational ──────
      publish_intro_post
    fi

    local _public_summary="${DASHBOARD_PUBLIC_STATUS}"
    if [ -n "$DASHBOARD_PUBLIC_URL" ]; then
      _public_summary="${DASHBOARD_PUBLIC_STATUS} (${DASHBOARD_PUBLIC_URL})"
    fi
    local _cron_summary="manual"
    if [ "$CRONS_REGISTERED" = "true" ]; then
      _cron_summary="auto-registered"
    elif [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ]; then
      _cron_summary="pending-finalization (run: bash scripts/finalize-crons.sh)"
    elif [ "$CRON_REGISTRATION_MODE" = "blocked" ]; then
      _cron_summary="blocked"
    fi
    local _bootstrap_summary="not-attempted"
    if [ "$BOOTSTRAP_ATTEMPTED" = "true" ]; then
      local _bs_count=0
      [ "$MAIN_BOOTSTRAPPED" = "true" ] && _bs_count=$((_bs_count + 1))
      [ "$BOOKMARKER_BOOTSTRAPPED" = "true" ] && _bs_count=$((_bs_count + 1))
      [ "$TRADER_BOOTSTRAPPED" = "true" ] && _bs_count=$((_bs_count + 1))
      if [ "$_bs_count" -eq 3 ]; then
        _bootstrap_summary="complete"
      elif [ "$_bs_count" -gt 0 ]; then
        _bootstrap_summary="partial (${_bs_count}/3)"
      else
        _bootstrap_summary="failed"
      fi
    fi
    local INSTALL_SUMMARY="Self-IP Agency v${AGENCY_VERSION} installed (status: ${INSTALL_STATUS}). TagClaw onboarding: ${TAGCLAW_ONBOARD_STATUS}. Identity: ${IDENTITY_RESOLVED}, Credentials: ${CREDENTIALS_EXIST}, Dashboard: ${DASHBOARD_STATUS}, Public dashboard: ${_public_summary}, Crons: ${_cron_summary}, Owner binding: ${OWNER_BINDING_STATUS}. Readiness: main=${MAIN_READY} bookmarker=${BOOKMARKER_READY} trader=${TRADER_READY}. Bootstrap: ${_bootstrap_summary}. Raw seed: ${RAW_SEED_STATUS}. Guided X sync: ${X_TWEETS_SEED_STATUS}. Guided X wiki compile: ${X_TWEETS_COMPILE_STATUS} (${X_TWEETS_COMPILED_COUNT}). Intro post: ${INTRO_POST_STATUS}."

    # ── Update .installed marker with bootstrap results ───────────────────
    # The initial write (above) ran before self-checks so cycle scripts see
    # the marker. Now re-write with final bootstrap state included.
    if [ "$BOOTSTRAP_ATTEMPTED" = "true" ]; then
      local installed_json_final
      installed_json_final="$(python3 -c "
import json
from datetime import datetime, timezone
d = {
    'version': '$AGENCY_VERSION',
    'installed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'install_status': '$INSTALL_STATUS',
    'crons_registered': '$CRONS_REGISTERED' == 'true',
    'cron_registration_mode': '$CRON_REGISTRATION_MODE',
    'cron_registration_status': 'registered' if '$CRONS_REGISTERED' == 'true' else ('pending_finalization' if '$CRON_REGISTRATION_MODE' == 'deferred-tool' else ('blocked' if '$CRON_REGISTRATION_MODE' == 'blocked' else 'pending')),
    'cron_finalize_command': 'bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace' if '$CRON_REGISTRATION_MODE' == 'deferred-tool' and '$CRONS_REGISTERED' != 'true' else '',
    'cron_intent_path': '$CRON_INTENT_PATH',
    'dashboard_ready': '$DASHBOARD_READY' == 'true',
    'dashboard_status': '$DASHBOARD_STATUS',
    'dashboard_local_status': '$DASHBOARD_STATUS',
    'dashboard_public_status': '$DASHBOARD_PUBLIC_STATUS',
    'dashboard_public_url': '$DASHBOARD_PUBLIC_URL',
    'dashboard_public_guide_available': '$DASHBOARD_PUBLIC_GUIDE_AVAILABLE' == 'true',
    'tagclaw_onboard_status': '$TAGCLAW_ONBOARD_STATUS',
    'identity_resolved': $([ "$IDENTITY_RESOLVED" = "true" ] && echo "True" || echo "False"),
    'credentials_exist': $([ "$CREDENTIALS_EXIST" = "true" ] && echo "True" || echo "False"),
    'bootstrap_attempted': True,
    'main_bootstrapped': $([ "$MAIN_BOOTSTRAPPED" = "true" ] && echo "True" || echo "False"),
    'bookmarker_bootstrapped': $([ "$BOOKMARKER_BOOTSTRAPPED" = "true" ] && echo "True" || echo "False"),
    'trader_bootstrapped': $([ "$TRADER_BOOTSTRAPPED" = "true" ] && echo "True" || echo "False"),
    'bootstrap_status': '$_bootstrap_summary',
    'raw_seed_status': '$RAW_SEED_STATUS',
    'x_tweets_seed_status': '$X_TWEETS_SEED_STATUS',
    'x_tweets_compile_status': '$X_TWEETS_COMPILE_STATUS',
    'x_tweets_compiled_count': int('$X_TWEETS_COMPILED_COUNT' or '0'),
    'x_tweets_blockers': json.loads('''$X_TWEETS_BLOCKERS'''),
    'intro_post_status': '$INTRO_POST_STATUS',
    'intro_post_tick': '$INTRO_POST_TICK',
    'intro_post_tick_status': '$INTRO_POST_TICK_STATUS',
    'intro_post_tick_source': '$INTRO_POST_TICK_SOURCE',
    'schema': 'installed.v7'
}
print(json.dumps(d, indent=2))
")"
      atomic_write_json "$INSTALLED_FILE" "$installed_json_final"
      atomic_write_json "$workspace/.agency-installed" "$installed_json_final"
    fi

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
_post_verify_command = 'bash $workspace/scripts/tagclaw-onboard.sh post-verify-finalize --workspace $workspace'
d = {
    'schema': 'install-next-steps.v2',
    'install_status': '$INSTALL_STATUS',
    'summary': '$INSTALL_SUMMARY',
    'dashboard_ready': '$DASHBOARD_READY' == 'true',
    'dashboard_local_status': '$DASHBOARD_STATUS',
    'dashboard_public_status': '$DASHBOARD_PUBLIC_STATUS',
    'dashboard_public_url': '$DASHBOARD_PUBLIC_URL',
    'dashboard_public_guide_available': '$DASHBOARD_PUBLIC_GUIDE_AVAILABLE' == 'true',
    'dashboard_public_guide': {
        'available': '$DASHBOARD_PUBLIC_GUIDE_AVAILABLE' == 'true',
        'cloudflared_installed': '$DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED' == 'true',
        'install_command': '$DASHBOARD_PUBLIC_INSTALL_COMMAND',
        'run_command': '$DASHBOARD_PUBLIC_START_COMMAND',
        'state_file': '$DASHBOARD_PUBLIC_STATE_FILE',
    },
    'crons_registered': '$CRONS_REGISTERED' == 'true',
    'cron_registration_status': 'registered' if '$CRONS_REGISTERED' == 'true' else ('pending_finalization' if '$CRON_REGISTRATION_MODE' == 'deferred-tool' else ('blocked' if '$CRON_REGISTRATION_MODE' == 'blocked' else 'pending')),
    'cron_finalize_command': 'bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace' if '$CRON_REGISTRATION_MODE' == 'deferred-tool' and '$CRONS_REGISTERED' != 'true' else '',
    'cron_registration_mode': '$CRON_REGISTRATION_MODE',
    'cron_intent_path': '$CRON_INTENT_PATH',
    'x_tweets_seed_status': '$X_TWEETS_SEED_STATUS',
    'next_steps': _arrays['next_steps'],
    'next_steps_text': _arrays['next_steps_text'],
    'tagclaw': {
        'onboard_status': '$TAGCLAW_ONBOARD_STATUS',
        'status': '$TAGCLAW_STATUS',
        'agent_username': '$TAGCLAW_AGENT_USERNAME',
        'verification_code': '$TAGCLAW_VERIFICATION_CODE',
        'profile_url': '$TAGCLAW_PROFILE_URL',
        'post_verification_command': _post_verify_command if _tw_active else '',
        'verification_tweet_file': '$VERIFICATION_TWEET_FILE',
        'verification_tweet': [_tw_line1, _tw_line2] if _tw_active else [],
        'verification_tweet_text': (_tw_line1 + '\\n' + _tw_line2) if _tw_active else ''
    },
    'raw_seed_status': '$RAW_SEED_STATUS',
    'x_tweets_seed_status': '$X_TWEETS_SEED_STATUS',
    'x_tweets_compile_status': '$X_TWEETS_COMPILE_STATUS',
    'x_tweets_compiled_count': int('$X_TWEETS_COMPILED_COUNT' or '0'),
    'x_tweets_blockers': json.loads('''$X_TWEETS_BLOCKERS'''),
    'intro_post_status': '$INTRO_POST_STATUS',
    'intro_post_tick': '$INTRO_POST_TICK',
    'intro_post_tick_status': '$INTRO_POST_TICK_STATUS',
    'intro_post_tick_source': '$INTRO_POST_TICK_SOURCE',
    'owner_binding': {
        'status': '$OWNER_BINDING_STATUS',
        'reason': '$OWNER_BINDING_REASON',
        'self_heal': '$OWNER_BINDING_SELF_HEAL',
        'declared_source': '$OWNER_TWITTER_HANDLE_SOURCE',
    },
    'install_report_url': None,
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
          echo "   After the tweet is live, tell me and I will finish the activation automatically."
        elif [ "$_k" = "dashboard_public_exposure" ]; then
          echo "$((md_i + 1)). **Enable a public dashboard URL (optional, recommended)**"
          echo ""
          if [ "$DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED" != "true" ]; then
            echo "   Install cloudflared:"
            echo ""
            echo '   ```bash'
            echo "   $DASHBOARD_PUBLIC_INSTALL_COMMAND"
            echo '   ```'
            echo ""
          fi
          echo "   Start the tunnel:"
          echo ""
          echo '   ```bash'
          echo "   $DASHBOARD_PUBLIC_START_COMMAND"
          echo '   ```'
          echo ""
          echo "   The public URL will be written to \`$DASHBOARD_PUBLIC_STATE_FILE\` under \`public.url\`."
        elif [ "$_k" = "finalize_crons" ]; then
          echo "$((md_i + 1)). **Approve deferred cron registration**"
          echo ""
          echo "   Reply \`同意创建 cron\` to let the agent create the three OpenClaw cron jobs from:"
          echo ""
          echo "   \`$CRON_INTENT_PATH\`"
          echo ""
          echo "   If only the shell CLI path is available and OpenClaw reports \`pairing_required\`, approve the pending device scope upgrade first, then run:"
          echo ""
          echo '   ```bash'
          echo "   bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace"
          echo '   ```'
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
      if [ "$CRONS_REGISTERED" = "true" ]; then
        echo "| Cron jobs | auto-registered ✓ |"
      elif [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ]; then
        echo "| Cron jobs | deferred to agent/tool path (see .install-cron-jobs.json) |"
      elif [ "$CRON_REGISTRATION_MODE" = "blocked" ]; then
        echo "| Cron jobs | blocked (openclaw CLI not available) |"
      else
        echo "| Cron jobs | manual (openclaw CLI not available) |"
      fi
      echo "| Raw knowledge base | ${RAW_SEED_STATUS} |"
      echo "| Guided X sync | ${X_TWEETS_SEED_STATUS} |"
      echo "| Guided X wiki compile | ${X_TWEETS_COMPILE_STATUS} (${X_TWEETS_COMPILED_COUNT}) |"
      echo "| Self-introduction post | ${INTRO_POST_STATUS} |"
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
      not-started)
        echo "  ║    - Public dashboard: not started (optional)"
        if [ "$DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED" = "true" ]; then
          echo "  ║         run: $DASHBOARD_PUBLIC_START_COMMAND"
        else
          echo "  ║         install cloudflared: $DASHBOARD_PUBLIC_INSTALL_COMMAND"
          echo "  ║         then run: $DASHBOARD_PUBLIC_START_COMMAND"
        fi
        ;;
      disabled|"")
        # Quiet: opt-in feature, default off (suggest_in_install=false case)
        :
        ;;
      *)
        echo "  ║    ⚠ Public dashboard status: $DASHBOARD_PUBLIC_STATUS"
        ;;
    esac

    if [ "$CRONS_REGISTERED" = "true" ]; then
      echo "  ║    - Cron jobs registered (3/3)"
    elif [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ]; then
      echo "  ║    ⚠ Cron registration deferred — finalize: bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace"
    elif [ "$CRON_REGISTRATION_MODE" = "blocked" ]; then
      echo "  ║    ✗ Cron registration blocked (openclaw CLI not available)"
    fi

    echo "  ║"
    if [ "$TAGCLAW_STATUS" = "pending_verification" ] && [ -n "$TAGCLAW_AGENT_USERNAME" ] && [ -n "$TAGCLAW_VERIFICATION_CODE" ]; then
      echo "  ║  Verification tweet (post this exact text):"
      echo "  ║    File: $VERIFICATION_TWEET_FILE"
      echo "  ║    I'm claiming my AI agent \"$TAGCLAW_AGENT_USERNAME\" on @TagClaw"
      echo "  ║    Verification: \"$TAGCLAW_VERIFICATION_CODE\""
      if [ -n "$TAGCLAW_PROFILE_URL" ]; then
        echo "  ║    Profile: $TAGCLAW_PROFILE_URL"
      fi
      echo "  ║    After the tweet is live, tell me and I will finish activation automatically."
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
        echo "  ║         Then tell me once the tweet is live."
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
    echo "  ║"
    echo "  ║  Bootstrap cycles (first-run):"
    if [ "$BOOTSTRAP_ATTEMPTED" = "true" ]; then
      if [ "$MAIN_BOOTSTRAPPED" = "true" ]; then
        echo "  ║    ✓ main-heartbeat       — BOOTSTRAPPED"
      elif [ "$MAIN_READY" = "true" ]; then
        echo "  ║    ✗ main-heartbeat       — FAILED (cron will retry)"
      else
        echo "  ║    - main-heartbeat       — SKIPPED (not ready)"
      fi
      if [ "$BOOKMARKER_BOOTSTRAPPED" = "true" ]; then
        echo "  ║    ✓ bookmarker-cycle     — BOOTSTRAPPED"
      elif [ "$BOOKMARKER_READY" = "true" ]; then
        echo "  ║    ✗ bookmarker-cycle     — FAILED (cron will retry)"
      else
        echo "  ║    - bookmarker-cycle     — SKIPPED (not ready)"
      fi
      if [ "$TRADER_BOOTSTRAPPED" = "true" ]; then
        echo "  ║    ✓ trader-cycle         — BOOTSTRAPPED"
      elif [ "$TRADER_READY" = "true" ]; then
        echo "  ║    ✗ trader-cycle         — FAILED (cron will retry)"
      else
        echo "  ║    - trader-cycle         — SKIPPED (not ready)"
      fi
    else
      echo "  ║    - not attempted (TagClaw not yet activated)"
    fi
    echo "  ║    contract: $workspace/HEARTBEAT.md"
    echo "  ║"
    echo "  ║  Raw knowledge base:"
    case "$RAW_SEED_STATUS" in
      ok)      echo "  ║    ✓ Seeded ($workspace/raw)" ;;
      partial) echo "  ║    ⚠ Partially seeded (some sources unavailable)" ;;
      failed)  echo "  ║    ✗ Seeding failed (non-fatal)" ;;
      *)       echo "  ║    - Not attempted" ;;
    esac
    echo "  ║"
    echo "  ║  Owner X binding:"
    case "$OWNER_BINDING_STATUS" in
      verified)   echo "  ║    ✓ Verified via TagClaw /me" ;;
      declared)   echo "  ║    ⚠ Declared (source: ${OWNER_TWITTER_HANDLE_SOURCE:-?}) — heartbeat will verify" ;;
      unresolved) echo "  ║    - Unresolved (will auto-heal on next heartbeat; pass --owner-twitter-handle on rerun to declare)" ;;
      *)          echo "  ║    - Unknown" ;;
    esac
    echo "  ║"
    echo "  ║  Guided X sync:"
    case "$X_TWEETS_SEED_STATUS" in
      ok)       echo "  ║    ✓ Synced owner X raw artifacts" ;;
      partial)  echo "  ║    ⚠ Partial sync (some tweet fetches failed)" ;;
      deferred) echo "  ║    - Deferred (guided session or URLs not yet available; heartbeat self-heal active if awaiting owner binding)" ;;
      blocked)  echo "  ║    ✗ Blocked (unrecoverable — see blockers array)" ;;
      failed)   echo "  ║    ✗ Sync failed (non-fatal)" ;;
      *)        echo "  ║    - Not attempted" ;;
    esac
    echo "  ║  Guided X wiki compile: ${X_TWEETS_COMPILE_STATUS} (${X_TWEETS_COMPILED_COUNT})"
    echo "  ║"
    echo "  ║  Self-introduction post:"
    case "$INTRO_POST_STATUS" in
      published)                    echo "  ║    ✓ Published on TagClaw (tick: ${INTRO_POST_TICK:-?}, source: ${INTRO_POST_TICK_SOURCE:-?})" ;;
      published_but_marker_failed)  echo "  ║    ⚠ Published but marker write failed (tick: ${INTRO_POST_TICK:-?})" ;;
      already_published)            echo "  ║    ✓ Already published (skipped duplicate)" ;;
      skipped)                      echo "  ║    - Deferred (prerequisites not met)" ;;
      failed)                       echo "  ║    ⚠ Failed to publish (non-fatal)" ;;
      *)                            echo "  ║    - Not attempted" ;;
    esac
    if [ -n "$INTRO_POST_TICK" ]; then
      echo "  ║    tick: ${INTRO_POST_TICK} (${INTRO_POST_TICK_STATUS:-?} via ${INTRO_POST_TICK_SOURCE:-?})"
    fi
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
    echo "DASHBOARD_READY=\"${DASHBOARD_READY}\""
    echo "DASHBOARD_STATUS=\"${DASHBOARD_STATUS}\""
    echo "DASHBOARD_LOCAL_STATUS=\"${DASHBOARD_STATUS}\""
    echo "DASHBOARD_PUBLIC_STATUS=\"${DASHBOARD_PUBLIC_STATUS}\""
    echo "DASHBOARD_PUBLIC_URL=\"${DASHBOARD_PUBLIC_URL}\""
    echo "DASHBOARD_PUBLIC_GUIDE_AVAILABLE=\"${DASHBOARD_PUBLIC_GUIDE_AVAILABLE:-false}\""
    echo "DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED=\"${DASHBOARD_PUBLIC_CLOUDFLARED_INSTALLED:-false}\""
    echo "DASHBOARD_PUBLIC_INSTALL_COMMAND=\"${DASHBOARD_PUBLIC_INSTALL_COMMAND:-}\""
    echo "DASHBOARD_PUBLIC_START_COMMAND=\"${DASHBOARD_PUBLIC_START_COMMAND:-}\""
    echo "DASHBOARD_PUBLIC_STATE_FILE=\"${DASHBOARD_PUBLIC_STATE_FILE:-}\""
    echo "CRONS_REGISTERED=\"${CRONS_REGISTERED}\""
    echo "CRON_REGISTRATION_MODE=\"${CRON_REGISTRATION_MODE}\""
    echo "CRON_REGISTRATION_STATUS=\"${_cron_summary}\""
    echo "CRON_INTENT_PATH=\"${CRON_INTENT_PATH}\""
    if [ "$CRON_REGISTRATION_MODE" = "deferred-tool" ] && [ "$CRONS_REGISTERED" != "true" ]; then
      echo "CRON_FINALIZE_COMMAND=\"bash $AGENCY_DIR/scripts/finalize-crons.sh --workspace $workspace\""
    fi
    echo "BOOTSTRAP_ATTEMPTED=\"${BOOTSTRAP_ATTEMPTED}\""
    echo "MAIN_BOOTSTRAPPED=\"${MAIN_BOOTSTRAPPED}\""
    echo "BOOKMARKER_BOOTSTRAPPED=\"${BOOKMARKER_BOOTSTRAPPED}\""
    echo "TRADER_BOOTSTRAPPED=\"${TRADER_BOOTSTRAPPED}\""
    echo "BOOTSTRAP_STATUS=\"${_bootstrap_summary}\""
    echo "RAW_SEED_STATUS=\"${RAW_SEED_STATUS}\""
    echo "X_TWEETS_SEED_STATUS=\"${X_TWEETS_SEED_STATUS}\""
    echo "X_TWEETS_COMPILE_STATUS=\"${X_TWEETS_COMPILE_STATUS}\""
    echo "X_TWEETS_COMPILED_COUNT=\"${X_TWEETS_COMPILED_COUNT}\""
    echo "X_TWEETS_BLOCKERS=\"${X_TWEETS_BLOCKERS}\""
    echo "INTRO_POST_STATUS=\"${INTRO_POST_STATUS}\""
    echo "INTRO_POST_TICK=\"${INTRO_POST_TICK}\""
    echo "INTRO_POST_TICK_STATUS=\"${INTRO_POST_TICK_STATUS}\""
    echo "INTRO_POST_TICK_SOURCE=\"${INTRO_POST_TICK_SOURCE}\""
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

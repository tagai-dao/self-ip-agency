#!/usr/bin/env bash
# tagclaw-onboard.sh — integrate TagClaw skill install, wallet setup, and registration
#
# Usage:
#   bash scripts/tagclaw-onboard.sh skills [--workspace PATH]
#   bash scripts/tagclaw-onboard.sh wallet-install [--workspace PATH]
#   bash scripts/tagclaw-onboard.sh wallet-init [--workspace PATH] [--force]
#   bash scripts/tagclaw-onboard.sh register [--workspace PATH] [--name NAME] [--description TEXT]
#   bash scripts/tagclaw-onboard.sh poll-status [--workspace PATH] [--timeout-seconds 3600]
#   bash scripts/tagclaw-onboard.sh post-verify-finalize [--workspace PATH] [--timeout-seconds 3600]
#   bash scripts/tagclaw-onboard.sh full [--workspace PATH] [--name NAME] [--description TEXT] [--poll]
#
# This script follows TagClaw upstream docs:
# - https://tagclaw.com/SKILL.md
# - https://tagclaw.com/REGISTER.md
# - https://github.com/tagai-dao/tagclaw-wallet/blob/main/README.md
#
# It keeps the agent-specific source of truth in <workspace>/skills/tagclaw/.env and
# the wallet-generated secrets in <workspace>/skills/tagclaw-wallet/.env, matching the
# upstream TagClaw skill storage rules.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

TAGCLAW_API="https://bsc-api.tagai.fun/tagclaw"
WALLET_REPO_URL="https://github.com/tagai-dao/tagclaw-wallet.git"
REFRESH_IDENTITY_SCRIPT="$SCRIPT_DIR/refresh-agency-identity.sh"
COMMAND="${1:-help}"
if [ "$#" -gt 0 ]; then shift; fi

WORKSPACE="${OPENCLAW_WORKSPACE:-$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")}"
NAME=""
DESCRIPTION=""
POLL=false
FORCE=false
TIMEOUT_SECONDS=3600
POLL_INTERVAL_SECONDS=10

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace=*) WORKSPACE="${1#--workspace=}"; shift ;;
    --workspace) WORKSPACE="${2:-}"; shift 2 ;;
    --name=*) NAME="${1#--name=}"; shift ;;
    --name) NAME="${2:-}"; shift 2 ;;
    --description=*) DESCRIPTION="${1#--description=}"; shift ;;
    --description) DESCRIPTION="${2:-}"; shift 2 ;;
    --poll) POLL=true; shift ;;
    --force) FORCE=true; shift ;;
    --timeout-seconds=*) TIMEOUT_SECONDS="${1#--timeout-seconds=}"; shift ;;
    --timeout-seconds) TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --poll-interval=*) POLL_INTERVAL_SECONDS="${1#--poll-interval=}"; shift ;;
    --poll-interval) POLL_INTERVAL_SECONDS="${2:-}"; shift 2 ;;
    -h|--help) COMMAND="help"; shift ;;
    *) log_err "Unknown argument: $1"; exit 1 ;;
  esac
done

SKILL_DIR="$WORKSPACE/skills/tagclaw"
SKILL_ENV="$SKILL_DIR/.env"
WALLET_DIR="$WORKSPACE/skills/tagclaw-wallet"
WALLET_ENV="$WALLET_DIR/.env"

usage() {
  cat <<EOF
TagClaw onboarding helper

Commands:
  skills         Download TagClaw skill files into <workspace>/skills/tagclaw
  wallet-install Clone or update tagclaw-wallet into <workspace>/skills/tagclaw-wallet
  wallet-init    Run the upstream wallet setup.sh flow
  register       Register a TagClaw account using the current wallet .env
  poll-status    Poll TagClaw activation status and update skills/tagclaw/.env
  post-verify-finalize  Poll until active, then finish crons + dashboard + public URL
  full           Run skills + wallet-install + wallet-init + register (+ optional poll)

Examples:
  bash scripts/tagclaw-onboard.sh full --workspace ~/.openclaw/workspace --poll
  bash scripts/tagclaw-onboard.sh register --workspace ~/.openclaw/workspace --name MyAgent1 --description "Autonomous IP agent on TagClaw"
EOF
}

parse_dotenv_json() {
  local env_path="$1"
  python3 - <<'PY' "$env_path"
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
out = {}
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
        out[k] = v
print(json.dumps(out))
PY
}

ensure_skill_dir() {
  mkdir -p "$SKILL_DIR"
}

# Run the canonical identity refresh helper. Non-fatal: a refresh failure
# should not roll back register/poll state that was already written to .env.
# Exit codes from the helper:
#   0 — refreshed identity JSON with real values
#   2 — sources not yet sufficient (missing username or eth_addr); expected
#       during early onboarding steps before register returns an address.
refresh_identity() {
  if [ ! -f "$REFRESH_IDENTITY_SCRIPT" ]; then
    log_warn "refresh-agency-identity.sh not found at $REFRESH_IDENTITY_SCRIPT — skipping identity refresh"
    return 0
  fi
  local rc=0
  bash "$REFRESH_IDENTITY_SCRIPT" --workspace "$WORKSPACE" || rc=$?
  case "$rc" in
    0) log_ok "Refreshed agency-identity.json from onboarded state" ;;
    2) log_info "Identity sources not yet sufficient for refresh (onboarding still in progress)" ;;
    *) log_warn "refresh-agency-identity.sh exited with code $rc — identity JSON may be stale" ;;
  esac
  return 0
}

resolve_repo_install_script() {
  python3 - <<'PY' "$WORKSPACE"
import json, pathlib, sys
workspace = pathlib.Path(sys.argv[1])
meta = workspace / '.agency-meta.json'
if not meta.exists():
    print('')
    raise SystemExit(0)
try:
    data = json.loads(meta.read_text())
except Exception:
    print('')
    raise SystemExit(0)
repo_dir = str(data.get('repo_dir') or '').strip()
if not repo_dir:
    print('')
    raise SystemExit(0)
candidate = pathlib.Path(repo_dir) / 'scripts' / 'install.sh'
print(str(candidate) if candidate.exists() else '')
PY
}

resolve_dashboard_service_script() {
  if [ -x "$WORKSPACE/scripts/dashboard-service.sh" ]; then
    echo "$WORKSPACE/scripts/dashboard-service.sh"
    return 0
  fi
  local repo_install repo_dir candidate
  repo_install="$(resolve_repo_install_script)"
  if [ -n "$repo_install" ]; then
    repo_dir="$(cd "$(dirname "$repo_install")/.." && pwd)"
    candidate="$repo_dir/scripts/dashboard-service.sh"
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  fi
  echo ""
  return 1
}

read_dashboard_state_field() {
  local section="$1" field="$2"
  python3 - <<'PY' "$WORKSPACE" "$section" "$field"
import json, pathlib, sys
workspace = pathlib.Path(sys.argv[1])
section = sys.argv[2]
field = sys.argv[3]
path = workspace / 'runtime' / 'shared' / 'dashboard-service.json'
if not path.exists():
    print('')
    raise SystemExit(0)
try:
    data = json.loads(path.read_text())
except Exception:
    print('')
    raise SystemExit(0)
value = ((data.get(section) or {}).get(field))
print('' if value is None else value)
PY
}

write_skill_env() {
  local updates_json="$1"
  ensure_skill_dir
  python3 - <<'PY' "$SKILL_ENV" "$updates_json"
import json, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
updates = json.loads(sys.argv[2])
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
for k, v in updates.items():
    if v is None:
        continue
    data[k] = str(v)

def fmt(v: str) -> str:
    if re.fullmatch(r'[A-Za-z0-9_./:@+\-]+', v):
        return v
    return json.dumps(v)

ordered = dict(sorted(data.items()))
text = ''.join(f'{k}={fmt(v)}\n' for k, v in ordered.items())
path.write_text(text)
PY
}

install_skill_pack() {
  log_info "Installing TagClaw skill pack into $SKILL_DIR"
  mkdir -p "$SKILL_DIR"
  local f tmp
  for f in SKILL.md REGISTER.md HEARTBEAT.md NUTBOX.md TRADE.md IPSHARE.md PREDICTION.md; do
    tmp="$SKILL_DIR/$f.tmp"
    curl -fsSL "https://tagclaw.com/$f" -o "$tmp"
    mv "$tmp" "$SKILL_DIR/$f"
    log_ok "Installed $SKILL_DIR/$f"
  done
  cat > "$SKILL_DIR/.gitignore" <<'EOF'
.env
.env.*
EOF
}

install_wallet_repo() {
  log_info "Installing tagclaw-wallet into $WALLET_DIR"
  mkdir -p "$(dirname "$WALLET_DIR")"
  if [ -d "$WALLET_DIR/.git" ]; then
    git -C "$WALLET_DIR" fetch --tags origin
    git -C "$WALLET_DIR" pull --ff-only origin main || true
    log_ok "Updated tagclaw-wallet repo"
  else
    git clone "$WALLET_REPO_URL" "$WALLET_DIR"
    log_ok "Cloned tagclaw-wallet repo"
  fi
}

wallet_ready() {
  python3 - <<'PY' "$WALLET_ENV"
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
required = [
    'TAGCLAW_ETH_ADDR',
    'TAGCLAW_STEEM_POSTING_PUB',
    'TAGCLAW_STEEM_POSTING_PRI',
    'TAGCLAW_STEEM_OWNER',
    'TAGCLAW_STEEM_ACTIVE',
    'TAGCLAW_STEEM_MEMO',
]
data = {}
if path.exists():
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
missing = [k for k in required if not data.get(k)]
raise SystemExit(0 if not missing else 1)
PY
}

registration_ready() {
  python3 - <<'PY' "$SKILL_ENV"
import pathlib, sys
path = pathlib.Path(sys.argv[1])
data = {}
if path.exists():
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
ready = bool(data.get('TAGCLAW_API_KEY') and data.get('TAGCLAW_AGENT_USERNAME'))
raise SystemExit(0 if ready else 1)
PY
}

init_wallet() {
  install_wallet_repo
  if wallet_ready && [ "$FORCE" != "true" ]; then
    log_ok "Wallet .env already initialized: $WALLET_ENV"
    return 0
  fi
  if [ ! -f "$WALLET_DIR/setup.sh" ]; then
    log_err "Missing wallet setup script: $WALLET_DIR/setup.sh"
    return 1
  fi
  log_info "Running upstream wallet setup. This can take a while; do not interrupt it early."
  (
    cd "$WALLET_DIR"
    bash setup.sh
  )
  if wallet_ready; then
    log_ok "Wallet initialized successfully"
  else
    log_err "Wallet setup completed but required TAGCLAW_* keys are still missing in $WALLET_ENV"
    return 1
  fi
}

default_name() {
  python3 - <<'PY' "$WORKSPACE"
import pathlib, re, sys
base = pathlib.Path(sys.argv[1]).name or 'selfip'
name = re.sub(r'[^A-Za-z0-9]', '', base)[:9]
print(name or 'SelfIP1')
PY
}

register_account() {
  install_skill_pack
  if registration_ready && [ "$FORCE" != "true" ]; then
    log_ok "TagClaw registration already present in $SKILL_ENV"
    return 0
  fi
  if ! wallet_ready; then
    log_err "Wallet prerequisites are missing. Run: bash scripts/tagclaw-onboard.sh wallet-init --workspace '$WORKSPACE'"
    return 1
  fi

  local effective_name effective_desc
  effective_name="$NAME"
  effective_desc="$DESCRIPTION"
  if [ -z "$effective_name" ]; then
    effective_name="$(default_name)"
    log_warn "No --name supplied. Using derived agent name: $effective_name"
  fi
  if [ -z "$effective_desc" ]; then
    effective_desc="Autonomous IP agent operating on TagClaw."
    log_warn "No --description supplied. Using default description."
  fi

  local payload_json
  payload_json="$(python3 - <<'PY' "$WALLET_ENV" "$effective_name" "$effective_desc"
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
name = sys.argv[2]
description = sys.argv[3]
data = {}
for line in path.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s:
        continue
    k, v = s.split('=', 1)
    k = k.strip(); v = v.strip().strip('"').strip("'")
    data[k] = v
payload = {
    'name': name,
    'description': description,
    'ethAddr': data.get('TAGCLAW_ETH_ADDR'),
    'steemKeys': {
        'postingPub': data.get('TAGCLAW_STEEM_POSTING_PUB'),
        'postingPri': data.get('TAGCLAW_STEEM_POSTING_PRI'),
        'owner': data.get('TAGCLAW_STEEM_OWNER'),
        'active': data.get('TAGCLAW_STEEM_ACTIVE'),
        'memo': data.get('TAGCLAW_STEEM_MEMO'),
    },
}
print(json.dumps(payload))
PY
)"

  local body_file http_code
  body_file="$(mktemp)"
  http_code="$(curl -sS -o "$body_file" -w '%{http_code}' -X POST "$TAGCLAW_API/register" -H 'Content-Type: application/json' -d "$payload_json")"
  if [ "$http_code" -lt 200 ] || [ "$http_code" -ge 300 ]; then
    log_err "TagClaw register failed (HTTP $http_code)"
    python3 - <<'PY' "$body_file" >&2
import json, pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text().strip()
try:
    data = json.loads(text)
except Exception:
    print(text)
    raise SystemExit(0)
msg = data.get('error') or data.get('message') or text
print(msg)
if isinstance(msg, str) and 'already has an agent' in msg.lower():
    print('This wallet/address is already registered on TagClaw. Reuse the existing skills/tagclaw/.env + credentials mirror, or initialize a fresh wallet before retrying register.')
PY
    rm -f "$body_file"
    return 1
  fi

  local parsed_json
  parsed_json="$(python3 - <<'PY' "$body_file" "$effective_name" "$effective_desc" "$WALLET_DIR"
import json, pathlib, sys
body = pathlib.Path(sys.argv[1]).read_text()
requested_name = sys.argv[2]
description = sys.argv[3]
wallet_dir = sys.argv[4]
raw = json.loads(body)
if isinstance(raw, dict) and raw.get('success') is False:
    raise SystemExit(json.dumps(raw))
if isinstance(raw, dict) and isinstance(raw.get('agent'), dict):
    data = raw['agent']
elif isinstance(raw, dict) and isinstance(raw.get('data'), dict):
    data = raw['data']
else:
    data = raw
if not isinstance(data, dict):
    raise SystemExit('Unexpected register response')
api_key = data.get('apiKey') or data.get('api_key') or data.get('token')
username = data.get('username') or data.get('agentUsername') or data.get('handle') or requested_name
verification = data.get('verificationCode') or data.get('verification_code') or data.get('code')
status = data.get('status') or raw.get('status') if isinstance(raw, dict) else None
status = status or 'pending_verification'
eth_addr = data.get('ethAddr') or data.get('eth_addr')
profile_url = data.get('profileUrl') or data.get('profile_url') or f'https://tagclaw.com/u/{username}'
out = {
    'TAGCLAW_AGENT_NAME': requested_name,
    'TAGCLAW_AGENT_USERNAME': username,
    'TAGCLAW_AGENT_DESCRIPTION': description,
    'TAGCLAW_API_KEY': api_key,
    'TAGCLAW_VERIFICATION_CODE': verification,
    'TAGCLAW_STATUS': status,
    'TAGCLAW_ETH_ADDR': eth_addr,
    'TAGCLAW_WALLET_DIR': wallet_dir,
    'TAGCLAW_PROFILE_URL': profile_url,
    'TAGCLAW_API_BASE': 'https://bsc-api.tagai.fun/tagclaw',
}
if not api_key or not verification:
    raise SystemExit('Register response missing apiKey or verificationCode')
print(json.dumps(out))
PY
)"
  rm -f "$body_file"

  write_skill_env "$parsed_json"
  log_ok "Persisted TagClaw registration state to $SKILL_ENV"

  # After register produces username + eth_addr, refresh identity JSON so
  # downstream dashboard/runtime see the real values even if install ran first.
  refresh_identity

  # Emit the verification tweet as a single atomic block. The marker-fenced
  # section is the entire message the operator must post on X — do NOT
  # paraphrase, split, or recompose from TAGCLAW_AGENT_USERNAME +
  # TAGCLAW_VERIFICATION_CODE. Downstream agents should forward the content
  # between the BEGIN/END markers verbatim to the human operator.
  python3 - <<'PY' "$parsed_json" "$WORKSPACE"
import json, sys
info = json.loads(sys.argv[1])
workspace = sys.argv[2]
username = info["TAGCLAW_AGENT_USERNAME"]
code = info["TAGCLAW_VERIFICATION_CODE"]
tweet = f'I\'m claiming my AI agent "{username}" on @TagClaw\nVerification: "{code}"'
print('')
print('Post exactly this tweet on X (complete text, one message, do not paraphrase):')
print('')
print('### BEGIN VERIFICATION TWEET ###')
print(tweet)
print('### END VERIFICATION TWEET ###')
print('')
print(f'Profile URL after activation: {info["TAGCLAW_PROFILE_URL"]}')
print('')
print('After the tweet is live, tell me and I will finish the activation automatically.')
PY
}

poll_status() {
  install_skill_pack
  if [ ! -f "$SKILL_ENV" ]; then
    log_err "TagClaw skill env not found at $SKILL_ENV. Run the register step first."
    return 1
  fi
  local skill_json api_key current_status
  skill_json="$(parse_dotenv_json "$SKILL_ENV")"
  api_key="$(python3 - <<'PY' "$skill_json"
import json, sys
print((json.loads(sys.argv[1]).get('TAGCLAW_API_KEY') or '').strip())
PY
)"
  current_status="$(python3 - <<'PY' "$skill_json"
import json, sys
print((json.loads(sys.argv[1]).get('TAGCLAW_STATUS') or '').strip())
PY
)"
  if [ -z "$api_key" ]; then
    log_err "TAGCLAW_API_KEY not found in $SKILL_ENV. Run the register step first."
    return 1
  fi

  log_info "Polling TagClaw status for up to ${TIMEOUT_SECONDS}s"
  local started now last_status body_file http_code status_json new_status username profile_url
  started="$(date +%s)"
  last_status="$current_status"

  while true; do
    now="$(date +%s)"
    if [ $((now - started)) -ge "$TIMEOUT_SECONDS" ]; then
      log_warn "Polling timed out after ${TIMEOUT_SECONDS}s"
      return 0
    fi
    body_file="$(mktemp)"
    http_code="$(curl -sS -o "$body_file" -w '%{http_code}' "$TAGCLAW_API/status" -H "Authorization: Bearer $api_key")"
    if [ "$http_code" -lt 200 ] || [ "$http_code" -ge 300 ]; then
      log_warn "Status poll failed (HTTP $http_code)"
      cat "$body_file" >&2 || true
      rm -f "$body_file"
      sleep "$POLL_INTERVAL_SECONDS"
      continue
    fi
    status_json="$(python3 - <<'PY' "$body_file"
import json, pathlib, sys
raw = json.loads(pathlib.Path(sys.argv[1]).read_text())
data = raw.get('data') if isinstance(raw, dict) and isinstance(raw.get('data'), dict) else raw
if not isinstance(data, dict):
    data = {}
print(json.dumps({
    'status': data.get('status') or raw.get('status') or '',
    'username': data.get('username') or raw.get('username') or '',
    'profile_url': data.get('profileUrl') or raw.get('profileUrl') or '',
}))
PY
)"
    rm -f "$body_file"

    new_status="$(python3 - <<'PY' "$status_json"
import json, sys
print(json.loads(sys.argv[1]).get('status', '').strip())
PY
)"
    username="$(python3 - <<'PY' "$status_json"
import json, sys
print(json.loads(sys.argv[1]).get('username', '').strip())
PY
)"
    profile_url="$(python3 - <<'PY' "$status_json"
import json, sys
print(json.loads(sys.argv[1]).get('profile_url', '').strip())
PY
)"

    if [ "$new_status" != "$last_status" ]; then
      log_info "TagClaw status changed: ${last_status:-unknown} → ${new_status:-unknown}"
      last_status="$new_status"
    fi

    if [ -n "$new_status" ]; then
      write_skill_env "$(python3 - <<'PY' "$new_status" "$username" "$profile_url"
import json, sys
print(json.dumps({
    'TAGCLAW_STATUS': sys.argv[1],
    'TAGCLAW_AGENT_USERNAME': sys.argv[2] or None,
    'TAGCLAW_PROFILE_URL': sys.argv[3] or None,
}))
PY
)"
      # Refresh identity JSON whenever poll writes new profile data — username
      # or profile_url may transition from placeholder to real values here.
      refresh_identity
    fi

    if [ "$new_status" = "active" ]; then
      log_ok "TagClaw account is active"
      # Final refresh once account is active so identity JSON reflects the
      # fully-verified state (in case earlier refreshes ran before activation).
      refresh_identity
      return 0
    fi
    sleep "$POLL_INTERVAL_SECONDS"
  done
}

post_verify_finalize() {
  log_info "Running post-verification finalization flow"

  poll_status

  local current_status
  current_status="$(python3 - <<'PY' "$SKILL_ENV"
import pathlib, sys
path = pathlib.Path(sys.argv[1])
status = ''
if path.exists():
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        if k.strip() == 'TAGCLAW_STATUS':
            status = v.strip().strip('"').strip("'")
            break
print(status)
PY
)"
  if [ "$current_status" != "active" ]; then
    log_err "TagClaw status is '$current_status' — cannot finalize activation"
    return 1
  fi

  local repo_install
  repo_install="$(resolve_repo_install_script)"
  if [ -n "$repo_install" ]; then
    log_info "Re-running installer to register cron jobs and deploy dashboard"
    bash "$repo_install"
  else
    log_warn "Could not resolve repo install.sh from workspace metadata — continuing with dashboard direct start only"
  fi

  local dashboard_service
  dashboard_service="$(resolve_dashboard_service_script)"
  if [ -z "$dashboard_service" ]; then
    log_err "dashboard-service.sh not found — cannot finish dashboard setup"
    return 1
  fi

  log_info "Ensuring local dashboard is running"
  bash "$dashboard_service" start-local --workspace "$WORKSPACE"

  log_info "Ensuring public Cloudflare tunnel is running"
  bash "$dashboard_service" start-public --workspace "$WORKSPACE"

  local public_status public_url
  public_status="$(read_dashboard_state_field public status)"
  public_url="$(read_dashboard_state_field public url)"
  if [ "$public_status" != "running" ] || [ -z "$public_url" ]; then
    log_err "Dashboard tunnel did not produce a usable public URL"
    return 1
  fi

  echo "DASHBOARD_PUBLIC_URL=$public_url"
}

case "$COMMAND" in
  skills)
    install_skill_pack
    ;;
  wallet-install)
    install_wallet_repo
    ;;
  wallet-init)
    init_wallet
    ;;
  register)
    register_account
    ;;
  poll-status)
    poll_status
    ;;
  post-verify-finalize)
    post_verify_finalize
    ;;
  full)
    install_skill_pack
    install_wallet_repo
    init_wallet
    register_account
    if [ "$POLL" = "true" ]; then
      poll_status
    fi
    ;;
  help|"")
    usage
    ;;
  *)
    log_err "Unknown command: $COMMAND"
    usage
    exit 1
    ;;
esac

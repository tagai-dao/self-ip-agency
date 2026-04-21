#!/usr/bin/env bash
# refresh-agency-identity.sh — Canonical owner of agency-identity.json
#
# Reconstructs config/agency-identity.json from the best-available sources
# after TagClaw onboarding has produced real values. This is the durable
# refresh path that fixes the install-first / onboard-second race where
# install runs before onboarding and leaves the identity JSON null-filled.
#
# Usage:
#   bash scripts/refresh-agency-identity.sh [--workspace PATH] [--repo-dir PATH]
#                                           [--dry-run] [--verify-api]
#                                           [--quiet]
#
# Input sources (in priority order):
#   1. <workspace>/skills/tagclaw/.env
#        TAGCLAW_AGENT_USERNAME, TAGCLAW_ETH_ADDR, TAGCLAW_PROFILE_URL,
#        TAGCLAW_API_KEY, TAGCLAW_OWNER_TWITTER_ID (if set)
#   2. <workspace>/skills/tagclaw-wallet/.env
#        TAGCLAW_ETH_ADDR (fallback when skill .env doesn't carry it)
#   3. TagClaw /me API (only when --verify-api is passed and an API key exists)
#        Enriches ownerTwitterId/profileUrl when .env is missing them.
#
# Write targets:
#   - <repo_dir>/config/agency-identity.json    (when repo dir is discoverable)
#   - <workspace>/config/agency-identity.json   (when workspace is discoverable)
#
# Both copies are kept in sync because the deployed runtime reads from
# workspace/config while install seeds from repo/config. Writing only one
# leaves a stale shadow that breaks downstream dashboard + runtime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

TAGCLAW_API_DEFAULT="https://bsc-api.tagai.fun/tagclaw"

WORKSPACE="${OPENCLAW_WORKSPACE:-}"
REPO_DIR_ARG=""
DRY_RUN=false
VERIFY_API=false
QUIET=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace=*) WORKSPACE="${1#--workspace=}"; shift ;;
    --workspace) WORKSPACE="${2:-}"; shift 2 ;;
    --repo-dir=*) REPO_DIR_ARG="${1#--repo-dir=}"; shift ;;
    --repo-dir) REPO_DIR_ARG="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --verify-api) VERIFY_API=true; shift ;;
    --quiet) QUIET=true; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | sed '/^set -euo/d'
      exit 0
      ;;
    *) log_err "Unknown argument: $1"; exit 1 ;;
  esac
done

info() { [ "$QUIET" = "true" ] || log_info "$*"; }
okmsg() { [ "$QUIET" = "true" ] || log_ok "$*"; }

# ── Resolve workspace + repo directory ───────────────────────────────────────
if [ -z "$WORKSPACE" ]; then
  WORKSPACE="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
fi

REPO_DIR=""
if [ -n "$REPO_DIR_ARG" ]; then
  REPO_DIR="$REPO_DIR_ARG"
else
  # Detect repo: script lives at REPO_DIR/scripts/ when invoked from a checkout
  parent_of_script="$(dirname "$SCRIPT_DIR")"
  if [ -f "$parent_of_script/VERSION" ] && [ -d "$parent_of_script/runtime-template" ]; then
    REPO_DIR="$parent_of_script"
  elif [ -f "$WORKSPACE/.agency-meta.json" ]; then
    meta_repo="$(python3 -c "import json; print(json.load(open('$WORKSPACE/.agency-meta.json')).get('repo_dir',''))" 2>/dev/null || echo "")"
    if [ -n "$meta_repo" ] && [ -d "$meta_repo" ]; then
      REPO_DIR="$meta_repo"
    fi
  fi
fi

info "refresh-agency-identity: workspace=$WORKSPACE repo_dir=${REPO_DIR:-<none>}"

# ── Build refreshed identity JSON in Python ──────────────────────────────────
# Python owns the actual identity reconstruction because:
#   - .env parsing needs to handle quoting consistently
#   - JSON writing must be canonical + atomic
#   - We merge three input sources with precedence rules
# The shell script only orchestrates path resolution, dry-run, and atomic move.

identity_payload="$(WORKSPACE="$WORKSPACE" \
  REPO_DIR="$REPO_DIR" \
  TAGCLAW_API_BASE="$TAGCLAW_API_DEFAULT" \
  VERIFY_API="$VERIFY_API" \
  python3 - <<'PY'
import json
import os
import pathlib
import subprocess
import sys

workspace = pathlib.Path(os.environ["WORKSPACE"])
repo_dir = os.environ.get("REPO_DIR") or ""
verify_api = os.environ.get("VERIFY_API", "false").lower() == "true"
api_base = os.environ.get("TAGCLAW_API_BASE", "https://bsc-api.tagai.fun/tagclaw")


def parse_dotenv(path: pathlib.Path) -> dict:
    data = {}
    if not path.exists():
        return data
    try:
        text = path.read_text()
    except OSError:
        return data
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        data[k] = v
    return data


def detect_wallet_cmd() -> str:
    candidates = [
        workspace / "skills/tagclaw-wallet/bin/wallet.js",
        pathlib.Path.home() / ".local/bin/tagclaw-wallet",
        pathlib.Path("/usr/local/bin/tagclaw-wallet"),
        pathlib.Path.home() / "tagclaw-wallet/tagclaw-wallet",
        pathlib.Path.home() / "tagclaw-wallet/dist/tagclaw-wallet",
    ]
    for c in candidates:
        if c.exists():
            if str(c).endswith(".js"):
                return f"node {c}"
            if os.access(str(c), os.X_OK):
                return str(c)
    return "tagclaw-wallet"


def maybe_fetch_me(api_key: str) -> dict:
    if not api_key or not verify_api:
        return {}
    try:
        # curl keeps us dependency-free in shell-land.
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "--max-time", "15",
                "-H", f"Authorization: Bearer {api_key}",
                f"{api_base}/me",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        body = json.loads(result.stdout)
    except Exception:
        return {}
    # Normalize /me response shape. The server has shipped four variants over
    # time; callers must all unwrap to the inner agent dict, otherwise fields
    # like ownerTwitterHandle go silently missing:
    #   - {"success": true, "agent": {...}}            (current, 2026-04)
    #   - {"success": true, "data": {"agent": {...}}}  (older nested wrapper)
    #   - {"success": true, "data": {...flat...}}      (earlier flat-wrapped)
    #   - {...flat...}                                  (legacy bare)
    if not isinstance(body, dict):
        return {}
    agent = body.get("agent")
    if isinstance(agent, dict):
        return agent
    data = body.get("data")
    if isinstance(data, dict):
        nested = data.get("agent")
        if isinstance(nested, dict):
            return nested
        return data
    return body


skill_env = parse_dotenv(workspace / "skills/tagclaw/.env")
wallet_env = parse_dotenv(workspace / "skills/tagclaw-wallet/.env")

# Merge: skill .env wins for identity fields, wallet .env is fallback for addr.
username = skill_env.get("TAGCLAW_AGENT_USERNAME") or skill_env.get("TAGCLAW_AGENT_NAME") or ""
eth_addr = skill_env.get("TAGCLAW_ETH_ADDR") or wallet_env.get("TAGCLAW_ETH_ADDR") or ""
profile_url = skill_env.get("TAGCLAW_PROFILE_URL") or ""
owner_twitter_id = skill_env.get("TAGCLAW_OWNER_TWITTER_ID") or ""
owner_twitter_handle = skill_env.get("TAGCLAW_OWNER_TWITTER_HANDLE") or ""
api_key = skill_env.get("TAGCLAW_API_KEY") or ""

me = maybe_fetch_me(api_key)
if me:
    # API-enriched fields only fill gaps — don't overwrite real .env state.
    username = username or me.get("username") or me.get("agentUsername") or ""
    eth_addr = eth_addr or me.get("ethAddr") or me.get("eth_addr") or ""
    profile_url = profile_url or me.get("profileUrl") or me.get("profile_url") or ""
    owner_twitter_id = owner_twitter_id or me.get("ownerTwitterId") or me.get("owner_twitter_id") or ""
    owner_twitter_handle = owner_twitter_handle or me.get("ownerTwitterHandle") or me.get("owner_twitter_handle") or ""

# Sensible fallback for profile URL when server didn't provide one.
if not profile_url and username:
    profile_url = f"https://tagclaw.com/u/{username}"

wallet_cmd = detect_wallet_cmd()

identity = {
    "schema": "agency.identity.v1",
    "agent": {
        "username": username or None,
        "eth_addr": eth_addr or None,
        "profile_url": profile_url or None,
        "platform": "TagClaw",
    },
    "owner": {
        "twitter_id": owner_twitter_id or None,
        "twitter_handle": owner_twitter_handle or None,
        "platform": "X (Twitter)",
    },
    "wallet": {
        "address": eth_addr or None,
        "chain": "BSC",
        "private_key_path": f"{workspace}/skills/tagclaw-wallet/.env",
        "tagclaw_wallet_cmd": wallet_cmd,
    },
    "binding": {
        "type": "agent-owner",
        "align_scorer": None,
        "voice_source": "owner twitter history",
    },
}

# Minimum-real-values contract: if both username AND eth_addr are missing,
# we have no real identity yet. Exit non-zero so callers can distinguish
# "refreshed with real data" from "nothing to refresh".
sufficient = bool(username) and bool(eth_addr)

out = {
    "sufficient": sufficient,
    "identity": identity,
    "sources": {
        "skill_env_exists": (workspace / "skills/tagclaw/.env").exists(),
        "wallet_env_exists": (workspace / "skills/tagclaw-wallet/.env").exists(),
        "api_verified": bool(me),
    },
}
print(json.dumps(out))
PY
)"

sufficient="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('sufficient'))" "$identity_payload")"
identity_json="$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['identity'], indent=2))" "$identity_payload")"

if [ "$sufficient" != "True" ]; then
  log_warn "Identity sources incomplete — skill .env has no TAGCLAW_AGENT_USERNAME or TAGCLAW_ETH_ADDR yet"
  log_warn "Identity JSON will not be overwritten until onboarding produces real values"
  exit 2
fi

okmsg "Identity sources resolved (username + eth_addr present)"

# ── Write targets ────────────────────────────────────────────────────────────
declare -a TARGETS=()
if [ -n "$REPO_DIR" ] && [ -d "$REPO_DIR" ]; then
  TARGETS+=("$REPO_DIR/config/agency-identity.json")
fi
if [ -d "$WORKSPACE" ]; then
  TARGETS+=("$WORKSPACE/config/agency-identity.json")
fi

if [ "${#TARGETS[@]}" -eq 0 ]; then
  log_err "No write targets resolved (neither repo_dir nor workspace available)"
  exit 1
fi

if [ "$DRY_RUN" = "true" ]; then
  info "[DRY RUN] Would write refreshed identity to:"
  for target in "${TARGETS[@]}"; do
    info "  - $target"
  done
  info "[DRY RUN] Identity payload:"
  echo "$identity_json"
  exit 0
fi

for target in "${TARGETS[@]}"; do
  atomic_write_json "$target" "$identity_json"
done

okmsg "agency-identity.json refreshed at ${#TARGETS[@]} location(s)"

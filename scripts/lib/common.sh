#!/usr/bin/env bash
# common.sh — Shared utility functions for self-ip-agency scripts
# Source this file: source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

set -euo pipefail

# ── Logging ──────────────────────────────────────────────────────────────────

log_info() {
  echo "[INFO]  $(date '+%H:%M:%S') $*"
}

log_ok() {
  echo "[OK]    $(date '+%H:%M:%S') $*"
}

log_err() {
  echo "[ERROR] $(date '+%H:%M:%S') $*" >&2
}

log_warn() {
  echo "[WARN]  $(date '+%H:%M:%S') $*" >&2
}

# ── Path resolution ──────────────────────────────────────────────────────────
# Deployed scripts run from WORKSPACE/scripts/ but repo assets live at REPO_DIR.
# resolve_agency_paths SCRIPT_DIR sets: REPO_DIR, WORKSPACE, AGENCY_VERSION
#
# Detection: if dirname(SCRIPT_DIR) has VERSION + runtime-template/, we're in the
# repo. Otherwise we're in a deployed workspace and read .agency-meta.json.

resolve_agency_paths() {
  local script_dir="${1:?resolve_agency_paths requires SCRIPT_DIR}"
  local parent_dir
  parent_dir="$(dirname "$script_dir")"

  # Default workspace from env or standard path
  WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"

  if [ -f "$parent_dir/VERSION" ] && [ -d "$parent_dir/runtime-template" ]; then
    # Running from repo checkout
    REPO_DIR="$parent_dir"
  else
    # Running from deployed workspace — parent_dir IS the workspace
    WORKSPACE="$parent_dir"
    REPO_DIR=""
    # Try to read repo pointer from meta file
    if [ -f "$WORKSPACE/.agency-meta.json" ]; then
      local meta_repo
      meta_repo="$(python3 -c "import json; print(json.load(open('$WORKSPACE/.agency-meta.json')).get('repo_dir',''))" 2>/dev/null || echo "")"
      if [ -n "$meta_repo" ] && [ -d "$meta_repo" ] && [ -f "$meta_repo/VERSION" ]; then
        REPO_DIR="$meta_repo"
      fi
    fi
  fi

  # Resolve version: repo > workspace meta > unknown
  if [ -n "$REPO_DIR" ] && [ -f "$REPO_DIR/VERSION" ]; then
    AGENCY_VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo "unknown")"
  elif [ -f "$WORKSPACE/.agency-meta.json" ]; then
    AGENCY_VERSION="$(python3 -c "import json; print(json.load(open('$WORKSPACE/.agency-meta.json')).get('version','unknown'))" 2>/dev/null || echo "unknown")"
  else
    AGENCY_VERSION="unknown"
  fi

  export REPO_DIR WORKSPACE AGENCY_VERSION
}

# Check if agency is installed (works from both repo and deployed workspace)
check_agency_installed() {
  # Check workspace marker first (deployed path)
  if [ -f "$WORKSPACE/.agency-installed" ]; then
    return 0
  fi
  # Fallback: check repo marker
  if [ -n "${REPO_DIR:-}" ] && [ -f "$REPO_DIR/.installed" ]; then
    return 0
  fi
  return 1
}

# Resolve path to an agent behavior file (agents/NAME.md)
resolve_agent_file() {
  local agent_name="${1:?agent name required}"
  # Workspace-deployed copy takes priority (.md then .md.tmpl)
  if [ -f "$WORKSPACE/agents/${agent_name}.md" ]; then
    echo "$WORKSPACE/agents/${agent_name}.md"
    return 0
  fi
  if [ -f "$WORKSPACE/agents/${agent_name}.md.tmpl" ]; then
    echo "$WORKSPACE/agents/${agent_name}.md.tmpl"
    return 0
  fi
  # Fallback to repo
  if [ -n "${REPO_DIR:-}" ]; then
    if [ -f "$REPO_DIR/agents/${agent_name}.md" ]; then
      echo "$REPO_DIR/agents/${agent_name}.md"
      return 0
    fi
    if [ -f "$REPO_DIR/agents/${agent_name}.md.tmpl" ]; then
      echo "$REPO_DIR/agents/${agent_name}.md.tmpl"
      return 0
    fi
  fi
  echo ""
  return 1
}

# ── Cloudflared install hint ──────────────────────────────────────────────────
# Returns an appropriate install command for cloudflared based on the current OS.
# Usage: hint="$(cloudflared_install_hint)"
cloudflared_install_hint() {
  case "$(uname -s)" in
    Linux*)
      if command -v apt-get >/dev/null 2>&1; then
        echo "sudo apt-get install -y cloudflared  # or: curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$([ \"$(uname -m)\" = 'aarch64' ] && echo arm64 || echo amd64) -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared"
      else
        local arch="amd64"
        [ "$(uname -m)" = "aarch64" ] && arch="arm64"
        echo "curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch} -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared"
      fi
      ;;
    Darwin*)
      if command -v brew >/dev/null 2>&1; then
        echo "brew install cloudflared"
      else
        echo "See https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
      fi
      ;;
    *)
      echo "See https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
      ;;
  esac
}

# ── Atomic JSON write ─────────────────────────────────────────────────────────
# Usage: atomic_write_json "/path/to/file.json" '{"key":"value"}'
# Writes JSON content to file atomically using tmp + mv pattern.
# Validates JSON before writing (requires python3).

atomic_write_json() {
  local target_path="$1"
  local json_content="$2"

  if [ -z "$target_path" ]; then
    log_err "atomic_write_json: target_path is required"
    return 1
  fi

  if [ -z "$json_content" ]; then
    log_err "atomic_write_json: json_content is required"
    return 1
  fi

  # Validate JSON syntax
  if ! echo "$json_content" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    log_err "atomic_write_json: invalid JSON content"
    return 1
  fi

  local dir
  dir="$(dirname "$target_path")"
  mkdir -p "$dir"

  local tmp_path
  tmp_path="$(mktemp "${target_path}.tmp.XXXXXXXX")"

  echo "$json_content" > "$tmp_path"
  mv "$tmp_path" "$target_path"

  log_ok "Wrote $target_path"
}

# ── JSON field helpers ────────────────────────────────────────────────────────
# Usage: json_get_field "/path/to/file.json" "field_name"

json_get_field() {
  local file="$1"
  local field="$2"
  python3 -c "
import json, sys
try:
    with open('$file') as f:
        d = json.load(f)
    print(d.get('$field', ''))
except Exception as e:
    print('')
" 2>/dev/null
}

# ── Path detection ────────────────────────────────────────────────────────────

detect_tagclaw_wallet() {
  # Try workspace-local repo first, then common binary locations.
  local workspace="${OPENCLAW_WORKSPACE:-}"
  if [ -z "$workspace" ] || [ ! -d "$workspace" ]; then
    workspace="$HOME/.openclaw/workspace"
  fi
  for candidate in \
    "$workspace/skills/tagclaw-wallet/bin/wallet.js" \
    "$(which tagclaw-wallet 2>/dev/null)" \
    "$HOME/.local/bin/tagclaw-wallet" \
    "$HOME/tagclaw-wallet/tagclaw-wallet" \
    "/usr/local/bin/tagclaw-wallet" \
    "$HOME/tagclaw-wallet/dist/tagclaw-wallet"
  do
    if [ -n "$candidate" ] && [ -e "$candidate" ]; then
      case "$candidate" in
        *.js)
          echo "node $candidate"
          return 0
          ;;
        *)
          if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
          fi
          ;;
      esac
    fi
  done
  echo ""
  return 1
}

detect_openclaw_workspace() {
  # Check environment variable first
  if [ -n "${OPENCLAW_WORKSPACE:-}" ] && [ -d "$OPENCLAW_WORKSPACE" ]; then
    echo "$OPENCLAW_WORKSPACE"
    return 0
  fi
  # Try common paths
  for candidate in \
    "$HOME/.openclaw/workspace" \
    "$HOME/openclaw/workspace"
  do
    if [ -d "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  echo ""
  return 1
}

# ── Prerequisite checks ───────────────────────────────────────────────────────

require_python3() {
  if ! command -v python3 &>/dev/null; then
    log_err "python3 is required but not found"
    return 1
  fi
}

require_curl() {
  if ! command -v curl &>/dev/null; then
    log_err "curl is required but not found"
    return 1
  fi
}

# ── OpenClaw scheduler reachability probe ─────────────────────────────────────
# Probes whether the OpenClaw scheduler is reachable, correctly distinguishing:
#   - Scheduler reachable (exit 0) — even if zero jobs are registered
#   - Scheduler unreachable (exit 1) — service down, gateway not started
#   - CLI broken (exit 2) — binary exists but crashes on invocation
#
# Strategy: `openclaw cron list` returning empty output (exit 0) is healthy.
#           Some CLI versions return non-zero for an empty job table, so we
#           fall back to `openclaw health --json` which is a pure connectivity
#           check that does not depend on scheduler state.
#
# Usage: probe_scheduler_reachable [log_prefix]
# Sets: _PROBE_RESULT ("reachable" | "unreachable" | "cli_broken")

probe_scheduler_reachable() {
  local prefix="${1:-}"

  # Gate: CLI must exist
  if ! command -v openclaw >/dev/null 2>&1; then
    _PROBE_RESULT="cli_broken"
    return 2
  fi

  # Gate: CLI must actually execute
  if ! openclaw --version >/dev/null 2>&1; then
    _PROBE_RESULT="cli_broken"
    return 2
  fi

  # Probe 1: `cron list` — if exit 0, scheduler is reachable (even if output is empty)
  local cron_list_out cron_list_rc
  cron_list_out="$(openclaw cron list 2>&1)" && cron_list_rc=0 || cron_list_rc=$?

  if [ "$cron_list_rc" -eq 0 ]; then
    _PROBE_RESULT="reachable"
    return 0
  fi

  # Probe 2: `health --json` — pure connectivity check, independent of job count.
  # This catches the case where `cron list` returns non-zero for an empty table
  # but the scheduler service itself is running fine.
  if openclaw health --json >/dev/null 2>&1; then
    _PROBE_RESULT="reachable"
    return 0
  fi

  # Probe 3: `cron status` — some CLI versions support a status subcommand
  if openclaw cron status >/dev/null 2>&1; then
    _PROBE_RESULT="reachable"
    return 0
  fi

  _PROBE_RESULT="unreachable"
  return 1
}

# ── Cron registration helpers ────────────────────────────────────────────────
# Shared by scripts/install.sh:register_crons and scripts/finalize-crons.sh so
# both paths classify errors identically and use the same post-add verification.
# Callers must set:
#   _STAGE_DIR — a tmp dir the caller owns (mktemp -d), used for per-job stderr
#   CRON_ADD_EXTRA_FLAGS — either "" or "--no-deliver" (from _detect_cron_add_flags)
#
# classify_cron_error STDERR_TEXT → prints one classification token to stdout.
# Classifications (grouped by retry-worthiness):
#   PERMANENT (no point retrying — fix config, not connection):
#     plugin_config_mismatch — openclaw.json entry ≠ plugin package.json name
#     plugin_not_found       — referenced plugin isn't installed/registered
#     permission_denied      — auth, 401/403, invalid api key
#     schema_invalid         — bad request, validation error, 400
#   TRANSIENT (retry loop's reason to exist):
#     gateway_flap           — connect/reset/normal-closure/timeout
#   unknown                  — default; retried once in case it's flaky
classify_cron_error() {
  local t
  t="$(printf '%s' "${1-}" | tr '[:upper:]' '[:lower:]')"
  case "$t" in
    *"plugin"*"mismatch"*|*"entry hint"*"manifest"*|*"plugin id"*"mismatch"*)
      echo "plugin_config_mismatch" ;;
    *"plugin"*"not found"*|*"plugin"*"not registered"*|*"unknown plugin"*)
      echo "plugin_not_found" ;;
    *"permission denied"*|*"unauthorized"*|*"invalid api key"*|*" 401"*|*" 403"*)
      echo "permission_denied" ;;
    *"schema"*"invalid"*|*"validation"*"error"*|*"bad request"*|*" 400"*)
      echo "schema_invalid" ;;
    *"gateway connect failed"*|*"gateway closed"*|*"normal closure"*|*"econnreset"*|*"timed out"*|*"timeout"*|*"connection refused"*|*"connection reset"*)
      echo "gateway_flap" ;;
    *)
      echo "unknown" ;;
  esac
}

is_permanent_kind() {
  case "$1" in
    plugin_config_mismatch|plugin_not_found|permission_denied|schema_invalid) return 0 ;;
    *) return 1 ;;
  esac
}

# kind_to_hint CLASSIFICATION → prints the operator-facing fix hint.
kind_to_hint() {
  case "$1" in
    plugin_config_mismatch)
      echo "Plugin id mismatch between openclaw.json and the plugin's package.json \"name\". Fix: align \`plugins.entries.<id>.entry\` in ~/.openclaw/openclaw.json with the plugin manifest's name, or run \`openclaw doctor\`. Then re-run this script." ;;
    plugin_not_found)
      echo "Scheduler references a plugin that isn't installed/registered. Fix: install the plugin (\`openclaw plugin install ...\`) or remove the stale entry from ~/.openclaw/openclaw.json." ;;
    permission_denied)
      echo "Scheduler rejected the registration. Fix: check OpenClaw auth / API key. Run \`openclaw auth status\` or re-authenticate." ;;
    schema_invalid)
      echo "Scheduler rejected the cron job shape (bad request / schema violation). Fix: update OpenClaw CLI (\`pnpm up -g openclaw@latest\`) — the installer may target a field the CLI no longer accepts." ;;
    gateway_flap)
      echo "All errors are transient OpenClaw gateway drops. Re-run this script — the retry loop already absorbs single flaps, but the gateway was unstable throughout this run." ;;
    mixed_failures)
      echo "Failures have multiple root causes. See failed_details[].kind/stderr_tail per job." ;;
    unknown)
      echo "Unrecognized error. See failed_details[].stderr_tail for the raw CLI output." ;;
    *)
      echo "" ;;
  esac
}

# register_one_with_retry NAME SCHED SESSION MSG — retries transient failures
# (gateway/connection resets, normal-closure websocket drops, timeouts).
# Captures stderr tail and a classification for the final failed attempt.
# Permanent errors break the retry loop immediately. Writes per-job stderr
# to $_STAGE_DIR/add-<safe_name>.err. Exports LAST_REGISTER_ERR_TAIL/KIND.
LAST_REGISTER_ERR_TAIL=""
LAST_REGISTER_ERR_KIND="unknown"
register_one_with_retry() {
  local name="$1" schedule="$2" session="$3" message="$4"
  local safe_name
  safe_name="$(printf '%s' "$name" | tr -c 'a-zA-Z0-9_-' '_')"
  local err_file="$_STAGE_DIR/add-${safe_name}.err"
  local attempt max_attempts=3
  local last_kind="unknown"

  for attempt in 1 2 3; do
    case "$attempt" in
      2) sleep 2 ;;
      3) sleep 5 ;;
    esac
    # shellcheck disable=SC2086  # $CRON_ADD_EXTRA_FLAGS is either "" or "--no-deliver"
    if openclaw cron add \
      --name "$name" \
      --cron "$schedule" \
      --session "$session" \
      --message "$message" \
      $CRON_ADD_EXTRA_FLAGS >/dev/null 2>"$err_file"; then
      return 0
    fi

    local err_body
    err_body="$(cat "$err_file" 2>/dev/null || true)"
    last_kind="$(classify_cron_error "$err_body")"
    if is_permanent_kind "$last_kind"; then
      local hint
      hint="$(printf '%s' "$err_body" | tr -d '\r' | tr '\n' ' ' | cut -c1-120)"
      log_info "  attempt ${attempt}/${max_attempts} failed with ${last_kind} — permanent, aborting retry (${hint:-no stderr})"
      break
    fi
    if [ "$attempt" -lt "$max_attempts" ]; then
      local hint
      hint="$(printf '%s' "$err_body" | tr -d '\r' | tr '\n' ' ' | cut -c1-100)"
      log_info "  attempt ${attempt}/${max_attempts} failed (${last_kind}, ${hint:-no stderr}); retrying..."
    fi
  done

  LAST_REGISTER_ERR_TAIL="$(tr -d '\r' < "$err_file" 2>/dev/null | tail -n 3 | awk 'NR>1{printf " | "} {printf "%s",$0}')"
  LAST_REGISTER_ERR_KIND="$last_kind"
  return 1
}

# verify_registered NAME — post-check that a job name exists in scheduler.
# Used for post-rm residual detection AND post-add success confirmation (the
# CLI has been observed to report non-zero even when a job did get created,
# and conversely report zero while silently dropping the add).
verify_registered() {
  local name="$1"
  local name_re
  name_re="$(printf '%s' "$name" | sed 's/[][\\.^$*+?(){}|/]/\\&/g')"
  openclaw cron list 2>/dev/null | grep -qE "(^|[[:space:]\"'])${name_re}([[:space:]\"']|$)"
}

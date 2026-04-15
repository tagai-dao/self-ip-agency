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

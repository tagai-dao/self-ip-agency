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

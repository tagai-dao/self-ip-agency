#!/usr/bin/env bash
# batch-create-self-ip-agents.sh — batch provision multiple self-IP agents from a TSV manifest.
#
# Manifest format (required header):
#   agent_id<TAB>tagclaw_name<TAB>description
#
# Example:
#   agent_id	tagclaw_name	description
#   alpha	Agt041601	Autonomous self-IP agent focused on research and ops
#   beta	Agt041602	Autonomous self-IP agent focused on content and curation
#
# Safe defaults:
# - one workspace per agent
# - one HOME per agent
# - no auto tweet posting
# - no auto poll-status
# - fail fast on malformed rows

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

MANIFEST=""
WORKSPACE_ROOT="$HOME/.openclaw"
BASE_PORT=7890
LIMIT=0
DRY_RUN=false
KEEP_GOING=false

usage() {
  cat <<'EOF'
Batch-create self-IP agents from a TSV manifest.

Usage:
  bash scripts/batch-create-self-ip-agents.sh --manifest agents.tsv [options]

Required:
  --manifest PATH               TSV file with header: agent_id<TAB>tagclaw_name<TAB>description

Options:
  --workspace-root PATH         Root under which agent workspaces will be created (default: ~/.openclaw)
  --base-port PORT              Starting dashboard port; each selected agent increments by 1 (default: 7890)
  --agent-id ID                 Only process a specific agent_id (repeatable or comma-separated)
  --limit N                     Process at most N selected rows
  --dry-run                     Print planned actions without running install.sh
  --keep-going                  Continue after per-agent failures instead of exiting immediately
  -h, --help                    Show this help

Per-agent layout:
  <workspace-root>/workspace-<agent_id>
  <workspace-root>/workspace-<agent_id>/_home

Notes:
- TagClaw names must be <= 9 chars and alphanumeric only.
- This script does NOT auto-post verification tweets.
- This script does NOT auto-run poll-status by default.
EOF
}

AGENT_FILTER_LIST=""
parse_agent_ids() {
  local raw="$1" id
  IFS=',' read -r -a _ids <<< "$raw"
  for id in "${_ids[@]}"; do
    id="${id//[[:space:]]/}"
    if [ -n "$id" ]; then
      AGENT_FILTER_LIST+="
$id"
    fi
  done
}

agent_id_selected() {
  local needle="$1"
  if [ -z "$AGENT_FILTER_LIST" ]; then
    return 0
  fi
  printf '%s
' "$AGENT_FILTER_LIST" | grep -Fxq "$needle"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --manifest=*) MANIFEST="${1#--manifest=}"; shift ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --workspace-root=*) WORKSPACE_ROOT="${1#--workspace-root=}"; shift ;;
    --workspace-root) WORKSPACE_ROOT="${2:-}"; shift 2 ;;
    --base-port=*) BASE_PORT="${1#--base-port=}"; shift ;;
    --base-port) BASE_PORT="${2:-}"; shift 2 ;;
    --agent-id=*) parse_agent_ids "${1#--agent-id=}"; shift ;;
    --agent-id) parse_agent_ids "${2:-}"; shift 2 ;;
    --limit=*) LIMIT="${1#--limit=}"; shift ;;
    --limit) LIMIT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --keep-going) KEEP_GOING=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) log_err "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

[ -n "$MANIFEST" ] || { log_err "--manifest is required"; usage; exit 1; }
[ -f "$MANIFEST" ] || { log_err "Manifest not found: $MANIFEST"; exit 1; }
[ -f "$REPO_DIR/scripts/install.sh" ] || { log_err "install.sh not found at $REPO_DIR/scripts/install.sh"; exit 1; }

if ! [[ "$BASE_PORT" =~ ^[0-9]+$ ]]; then
  log_err "--base-port must be an integer"
  exit 1
fi
if ! [[ "$LIMIT" =~ ^[0-9]+$ ]]; then
  log_err "--limit must be an integer >= 0"
  exit 1
fi

validate_agent_id() {
  local agent_id="$1"
  if [[ ! "$agent_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    log_err "Invalid agent_id '$agent_id' (use letters, digits, dot, underscore, dash)"
    return 1
  fi
}

validate_tagclaw_name() {
  local name="$1"
  if [ ${#name} -gt 9 ]; then
    log_err "Invalid TagClaw name '$name' (> 9 chars)"
    return 1
  fi
  if [[ ! "$name" =~ ^[A-Za-z0-9]+$ ]]; then
    log_err "Invalid TagClaw name '$name' (letters/digits only)"
    return 1
  fi
}

print_agent_summary() {
  local agent_id="$1"
  local agent_ws="$2"
  local agent_home="$3"
  local port="$4"
  local tagclaw_name="$5"

  cat <<EOF
  ──────────────────────────────────────────────────
  agent_id:         $agent_id
  tagclaw_name:     $tagclaw_name
  workspace:        $agent_ws
  home:             $agent_home
  dashboard_port:   $port
  verification_env: $agent_ws/skills/tagclaw/.env
  wallet_env:       $agent_ws/skills/tagclaw-wallet/.env
  poll_command:     HOME="$agent_home" bash "$agent_ws/scripts/tagclaw-onboard.sh" poll-status --workspace "$agent_ws"
EOF
}

log_info "Batch provisioning from manifest: $MANIFEST"
log_info "Workspace root: $WORKSPACE_ROOT"
log_info "Repository root: $REPO_DIR"
[ "$DRY_RUN" = "true" ] && log_info "DRY RUN mode — install.sh will be invoked with --dry-run"

header_read=false
selected=0
processed=0
successes=0
failures=0
skipped=0
line_no=0

while IFS=$'\t' read -r c1 c2 c3 extra || [ -n "${c1:-}${c2:-}${c3:-}${extra:-}" ]; do
  line_no=$((line_no + 1))

  if [ "$header_read" = false ]; then
    header_read=true
    if [ "$c1" != "agent_id" ] || [ "$c2" != "tagclaw_name" ] || [ "$c3" != "description" ] || [ -n "${extra:-}" ]; then
      log_err "Malformed manifest header on line 1. Expected: agent_id<TAB>tagclaw_name<TAB>description"
      exit 1
    fi
    continue
  fi

  # Skip completely blank lines
  if [ -z "${c1:-}" ] && [ -z "${c2:-}" ] && [ -z "${c3:-}" ] && [ -z "${extra:-}" ]; then
    continue
  fi

  if [ -n "${extra:-}" ]; then
    log_err "Malformed row on line $line_no: expected exactly 3 TSV columns"
    exit 1
  fi
  if [ -z "${c1:-}" ] || [ -z "${c2:-}" ] || [ -z "${c3:-}" ]; then
    log_err "Malformed row on line $line_no: agent_id, tagclaw_name, and description are all required"
    exit 1
  fi

  agent_id="$c1"
  tagclaw_name="$c2"
  description="$c3"

  validate_agent_id "$agent_id"
  validate_tagclaw_name "$tagclaw_name"

  if ! agent_id_selected "$agent_id"; then
    skipped=$((skipped + 1))
    continue
  fi

  selected=$((selected + 1))
  if [ "$LIMIT" -gt 0 ] && [ "$processed" -ge "$LIMIT" ]; then
    skipped=$((skipped + 1))
    continue
  fi

  agent_ws="$WORKSPACE_ROOT/workspace-$agent_id"
  agent_home="$agent_ws/_home"
  port=$((BASE_PORT + processed))
  processed=$((processed + 1))

  log_info "[$processed] Provisioning agent_id=$agent_id tagclaw_name=$tagclaw_name port=$port"
  print_agent_summary "$agent_id" "$agent_ws" "$agent_home" "$port" "$tagclaw_name"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY RUN] Would mkdir -p "$agent_ws" "$agent_home""
    log_info "[DRY RUN] Would run install.sh with isolated HOME + OPENCLAW_WORKSPACE"
    log_info "[DRY RUN] Command: HOME="$agent_home" OPENCLAW_WORKSPACE="$agent_ws" VIZ_PORT=$port bash "$REPO_DIR/scripts/install.sh" --dry-run --tagclaw-name "$tagclaw_name" --tagclaw-description "$description""
    successes=$((successes + 1))
    continue
  fi

  mkdir -p "$agent_ws" "$agent_home"

  set +e
  HOME="$agent_home" OPENCLAW_WORKSPACE="$agent_ws" VIZ_PORT="$port" \
    bash "$REPO_DIR/scripts/install.sh" \
    --tagclaw-name "$tagclaw_name" \
    --tagclaw-description "$description"
  rc=$?
  set -e

  if [ "$rc" -ne 0 ]; then
    failures=$((failures + 1))
    log_err "Provisioning failed for agent_id=$agent_id (exit=$rc)"
    if [ "$KEEP_GOING" = "true" ]; then
      continue
    fi
    exit "$rc"
  fi

  successes=$((successes + 1))
  log_ok "Provisioned agent_id=$agent_id"
  echo ""
done < "$MANIFEST"

if [ "$header_read" = false ]; then
  log_err "Manifest was empty: $MANIFEST"
  exit 1
fi

echo ""
echo "══════════════════════════════════════════════════════════"
echo "Batch provisioning summary"
echo "══════════════════════════════════════════════════════════"
echo "Manifest:     $MANIFEST"
echo "Selected:     $selected"
echo "Processed:    $processed"
echo "Succeeded:    $successes"
echo "Failed:       $failures"
echo "Skipped:      $skipped"
echo "Dry run:      $DRY_RUN"
echo "Keep going:   $KEEP_GOING"

echo ""
echo "Next operator action:"
echo "- For each successful agent, open <workspace>/skills/tagclaw/.env"
echo "- Copy the emitted verification tweet text"
echo "- Post the tweet"
echo "- Then run that agent's printed poll-status command"

if [ "$failures" -gt 0 ]; then
  exit 1
fi

#!/usr/bin/env bash
# finalize-crons.sh — Complete deferred cron registration for cloud/clawdi installs
#
# Usage: bash scripts/finalize-crons.sh [--workspace PATH] [--max-retries N] [--retry-interval S]
#
# Reads the deferred intent artifact (.install-cron-jobs.json), verifies scheduler
# reachability, registers all cron jobs, and updates install state to reflect
# completion. Designed to be machine-dispatchable by agents/operators.
#
# Exit codes:
#   0 — all cron jobs registered successfully
#   1 — precondition failure (no intent artifact, no CLI, already finalized)
#   2 — scheduler not reachable after retries
#   3 — partial registration (some jobs failed)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

WORKSPACE=""
MAX_RETRIES=5
RETRY_INTERVAL=3

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace=*) WORKSPACE="${1#--workspace=}"; shift ;;
    --workspace) WORKSPACE="${2:-}"; shift 2 ;;
    --max-retries=*) MAX_RETRIES="${1#--max-retries=}"; shift ;;
    --max-retries) MAX_RETRIES="${2:-5}"; shift 2 ;;
    --retry-interval=*) RETRY_INTERVAL="${1#--retry-interval=}"; shift ;;
    --retry-interval) RETRY_INTERVAL="${2:-3}"; shift 2 ;;
    *) shift ;;
  esac
done

if [ -z "$WORKSPACE" ]; then
  WORKSPACE="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
fi

AGENCY_DIR="$(dirname "$SCRIPT_DIR")"

# ── Locate intent artifact ──────────────────────────────────────────────────
INTENT_PATH=""
for _candidate in "$WORKSPACE/.install-cron-jobs.json" "$AGENCY_DIR/.install-cron-jobs.json"; do
  if [ -f "$_candidate" ]; then
    INTENT_PATH="$_candidate"
    break
  fi
done

if [ -z "$INTENT_PATH" ]; then
  log_warn "No .install-cron-jobs.json found — nothing to finalize"
  echo '{"status":"no_intent_artifact","message":"No deferred cron intent artifact found. Crons may already be registered or install has not run yet."}'
  exit 1
fi

# ── Check if already finalized ───────────────────────────────────────────────
CURRENT_MODE="$(python3 -c "
import json, sys
try:
    d = json.load(open('$INTENT_PATH'))
    print(d.get('mode', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")"

if [ "$CURRENT_MODE" = "finalized" ]; then
  log_info "Cron registration already finalized — nothing to do"
  echo '{"status":"already_finalized","message":"Cron jobs were already registered."}'
  exit 0
fi

# ── Update state to finalization_dispatched ──────────────────────────────────
# This signals that a finalizer has picked up the intent artifact,
# distinguishing from the initial pending_finalization state.
python3 -c "
import json, os, tempfile
from datetime import datetime, timezone
try:
    with open('$INTENT_PATH') as f:
        d = json.load(f)
    d['mode'] = 'finalization_dispatched'
    d['finalization_dispatched_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with tempfile.NamedTemporaryFile('w', dir=os.path.dirname('$INTENT_PATH'), suffix='.tmp', delete=False) as f:
        json.dump(d, f, indent=2)
        tmp = f.name
    os.replace(tmp, '$INTENT_PATH')
except Exception:
    pass
" 2>/dev/null || true

# ── CLI presence check ──────────────────────────────────────────────────────
if ! command -v openclaw >/dev/null 2>&1; then
  log_warn "openclaw CLI not found in PATH"
  echo '{"status":"cli_not_found","message":"openclaw CLI not in PATH. Install it or add to PATH."}'
  exit 1
fi

if ! openclaw --version >/dev/null 2>&1; then
  log_warn "openclaw CLI found but not executable (broken shim?)"
  echo '{"status":"cli_broken","message":"openclaw CLI exists but fails to execute. Reinstall: pnpm add -g openclaw@latest"}'
  exit 1
fi

log_ok "openclaw CLI available: $(openclaw --version 2>&1 || echo 'unknown')"

# ── Scheduler reachability with retries ──────────────────────────────────────
# Uses multi-signal probe (cron list + health --json + cron status) to correctly
# distinguish "reachable with zero jobs" from "truly unreachable".
log_info "Checking scheduler reachability (max ${MAX_RETRIES} attempts, ${RETRY_INTERVAL}s interval)..."

SCHEDULER_REACHABLE=false
for _attempt in $(seq 1 "$MAX_RETRIES"); do
  if probe_scheduler_reachable "finalize-attempt-$_attempt"; then
    SCHEDULER_REACHABLE=true
    break
  fi
  # On first failure, try starting the gateway
  if [ "$_attempt" -eq 1 ]; then
    log_info "Scheduler not immediately reachable ($_PROBE_RESULT) — attempting gateway start..."
    openclaw gateway start >/dev/null 2>&1 || true
  fi
  if [ "$_attempt" -lt "$MAX_RETRIES" ]; then
    log_info "Attempt ${_attempt}/${MAX_RETRIES}: scheduler not reachable ($_PROBE_RESULT), retrying in ${RETRY_INTERVAL}s..."
    sleep "$RETRY_INTERVAL"
  fi
done

if [ "$SCHEDULER_REACHABLE" != "true" ]; then
  log_warn "Scheduler not reachable after ${MAX_RETRIES} attempts (last probe: $_PROBE_RESULT)"
  echo "{\"status\":\"scheduler_unreachable\",\"probe_result\":\"${_PROBE_RESULT}\",\"message\":\"OpenClaw scheduler not reachable after retries. Check: openclaw gateway status\"}"
  exit 2
fi

log_ok "Scheduler reachable"

# ── Read jobs from intent artifact ──────────────────────────────────────────
JOB_COUNT="$(python3 -c "
import json
with open('$INTENT_PATH') as f:
    d = json.load(f)
print(len(d.get('jobs', [])))
" 2>/dev/null || echo "0")"

if [ "${JOB_COUNT:-0}" -eq 0 ]; then
  log_warn "No jobs found in intent artifact"
  echo '{"status":"no_jobs","message":"Intent artifact exists but contains no job definitions."}'
  exit 1
fi

log_info "Found ${JOB_COUNT} jobs to register"

# Staging area for stderr capture — kept until end of script for diagnostic output.
# Use explicit path form (not `mktemp -d -t PREFIX`) because BSD and GNU mktemp
# interpret `-t` differently; this form is unambiguous on both.
_STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/finalize-crons.XXXXXX")"
trap 'rm -rf "$_STAGE_DIR"' EXIT

# ── Remove existing jobs (idempotent) ────────────────────────────────────────
# stderr goes to the stage dir for later inspection; rm failures are usually
# just "job doesn't exist" and safe to ignore.
python3 -c "
import json
with open('$INTENT_PATH') as f:
    d = json.load(f)
for j in d.get('jobs', []):
    print(j['name'])
" 2>/dev/null | while IFS= read -r job_name; do
  [ -n "$job_name" ] && openclaw cron rm "$job_name" >/dev/null 2>>"$_STAGE_DIR/rm.err" || true
done

# ── Helpers ─────────────────────────────────────────────────────────────────
# register_one_with_retry: retries transient failures (gateway/connection
# resets, normal-closure websocket drops, timeouts). Captures stderr tail on
# final failure into $LAST_REGISTER_ERR_TAIL for diagnostic surfacing.
LAST_REGISTER_ERR_TAIL=""
register_one_with_retry() {
  local name="$1" schedule="$2" session="$3" message="$4"
  # Sanitize name for filesystem use (defend against path traversal if name
  # contains '/' or '..'). Alphanumerics / underscore / dash only; everything
  # else becomes '_'. Hex-suffix to keep distinct sanitized names distinct.
  local safe_name
  safe_name="$(printf '%s' "$name" | tr -c 'a-zA-Z0-9_-' '_')"
  local err_file="$_STAGE_DIR/add-${safe_name}.err"
  local attempt max_attempts=3

  for attempt in 1 2 3; do
    # Back off before attempts 2 and 3; attempt 1 is immediate
    case "$attempt" in
      2) sleep 2 ;;
      3) sleep 5 ;;
    esac
    if openclaw cron add \
      --name "$name" \
      --cron "$schedule" \
      --session "$session" \
      --message "$message" >/dev/null 2>"$err_file"; then
      return 0
    fi
    if [ "$attempt" -lt "$max_attempts" ]; then
      local hint
      hint="$(tr -d '\r' < "$err_file" 2>/dev/null | tr '\n' ' ' | cut -c1-100)"
      log_info "  attempt ${attempt}/${max_attempts} failed (${hint:-no stderr}); retrying..."
    fi
  done

  # Join last 3 stderr lines with ' | '. awk (not tr) because `tr '\n' ' | '`
  # only uses the first char of set2 (space), dropping the pipe.
  LAST_REGISTER_ERR_TAIL="$(tr -d '\r' < "$err_file" 2>/dev/null | tail -n 3 | awk 'NR>1{printf " | "} {printf "%s",$0}')"
  return 1
}

# verify_registered: post-check that the job actually exists in scheduler.
# Handles CLI versions where `cron add` may report non-zero but the job did
# actually get created (observed with gateway flaps).
verify_registered() {
  local name="$1"
  # Escape regex metacharacters in name so names containing '.', '*', '[', etc.
  # don't produce false matches or break the regex.
  local name_re
  name_re="$(printf '%s' "$name" | sed 's/[][\\.^$*+?(){}|/]/\\&/g')"
  openclaw cron list 2>/dev/null | grep -qE "(^|[[:space:]\"'])${name_re}([[:space:]\"']|$)"
}

# ── Register each job (with retry + verification) ───────────────────────────
REGISTERED=0
FAILED=0
FAILED_NAMES=""
FAILED_DETAILS_JSON="["
_sep=""

while IFS=$'\t' read -r name schedule session message; do
  [ -z "$name" ] && continue
  log_info "Registering ${name} (${schedule})..."
  if register_one_with_retry "$name" "$schedule" "$session" "$message"; then
    log_ok "Registered: ${name}"
    REGISTERED=$((REGISTERED + 1))
    continue
  fi

  # Add reported failure. Cross-check: did it actually land anyway?
  if verify_registered "$name"; then
    log_ok "Registered: ${name} (verified via cron list despite add-rc; likely gateway flap)"
    REGISTERED=$((REGISTERED + 1))
    continue
  fi

  log_warn "Failed to register: ${name}"
  [ -n "$LAST_REGISTER_ERR_TAIL" ] && log_warn "  ↳ ${LAST_REGISTER_ERR_TAIL}"
  FAILED=$((FAILED + 1))
  FAILED_NAMES="${FAILED_NAMES:+$FAILED_NAMES, }$name"
  # Build the failed-details entry with python json.dumps so that BOTH the name
  # and the stderr_tail are JSON-escaped. A pathologically-named job (containing
  # '"' or '\\') would otherwise emit malformed JSON.
  _entry="$(TAIL="$LAST_REGISTER_ERR_TAIL" NAME="$name" python3 -c "
import json, os
print(json.dumps({'name': os.environ['NAME'], 'stderr_tail': os.environ['TAIL']}))
" 2>/dev/null || printf '{"name":"?","stderr_tail":"?"}')"
  FAILED_DETAILS_JSON="${FAILED_DETAILS_JSON}${_sep}${_entry}"
  _sep=","
done < <(python3 -c "
import json
with open('$INTENT_PATH') as f:
    d = json.load(f)
for j in d.get('jobs', []):
    print(f\"{j['name']}\t{j['schedule']}\t{j['session']}\t{j['message']}\")
" 2>/dev/null)
FAILED_DETAILS_JSON="${FAILED_DETAILS_JSON}]"

# ── Update artifacts on success ─────────────────────────────────────────────
if [ "$FAILED" -eq 0 ] && [ "$REGISTERED" -gt 0 ]; then
  log_ok "All ${REGISTERED} cron jobs registered successfully"

  # Update intent artifact with finalization receipt
  python3 -c "
import json, os, tempfile
from datetime import datetime, timezone
with open('$INTENT_PATH') as f:
    d = json.load(f)
d['finalized_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
d['finalized_by'] = 'finalize-crons-script'
d['mode'] = 'finalized'
d['registered_count'] = $REGISTERED
p = '$INTENT_PATH'
with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(p), suffix='.tmp', delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, p)
" 2>/dev/null || true

  # Also update workspace copy if different
  WS_INTENT="$WORKSPACE/.install-cron-jobs.json"
  if [ "$INTENT_PATH" != "$WS_INTENT" ] && [ -f "$WS_INTENT" ]; then
    cp "$INTENT_PATH" "$WS_INTENT" 2>/dev/null || true
  fi

  # Update .agency-installed to reflect registered state
  _update_installed_cron_state() {
    local installed_file="$1"
    [ -f "$installed_file" ] || return 0
    python3 -c "
import json, os, tempfile
from datetime import datetime, timezone
with open('$installed_file') as f:
    d = json.load(f)
d['crons_registered'] = True
d['cron_registration_mode'] = 'local-cli'
d['cron_registration_status'] = 'registered'
d['cron_finalized_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
d['cron_finalized_by'] = 'finalize-crons-script'
# Recompute install_status: verified if all conditions met
if d.get('identity_resolved') and d.get('credentials_exist') and d.get('dashboard_status') == 'running':
    d['install_status'] = 'verified'
p = '$installed_file'
with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(p), suffix='.tmp', delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, p)
" 2>/dev/null || true
  }

  _update_installed_cron_state "$WORKSPACE/.agency-installed"
  _update_installed_cron_state "$AGENCY_DIR/.installed"

  echo "{\"status\":\"ok\",\"registered\":${REGISTERED},\"message\":\"All cron jobs registered successfully.\"}"
  exit 0
else
  log_warn "${REGISTERED}/${JOB_COUNT} jobs registered, ${FAILED} failed: ${FAILED_NAMES}"
  echo "{\"status\":\"partial\",\"registered\":${REGISTERED},\"failed\":${FAILED},\"failed_names\":\"${FAILED_NAMES}\",\"failed_details\":${FAILED_DETAILS_JSON},\"message\":\"Some jobs failed to register. See failed_details[].stderr_tail for underlying errors; if it mentions 'gateway' or 'connection', this was a transient OpenClaw gateway drop — re-run the script to retry.\"}"
  exit 3
fi

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
# NOTE: all python invocations pass paths via INTENT_PATH / INSTALLED_FILE env
# vars (NOT shell-interpolated into the python source) to prevent code injection
# if any path contains a single quote or otherwise escapes the literal.
CURRENT_MODE="$(INTENT_PATH="$INTENT_PATH" python3 -c "
import json, os
try:
    d = json.load(open(os.environ['INTENT_PATH']))
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
INTENT_PATH="$INTENT_PATH" python3 -c "
import json, os, tempfile
from datetime import datetime, timezone
p = os.environ['INTENT_PATH']
try:
    with open(p) as f:
        d = json.load(f)
    d['mode'] = 'finalization_dispatched'
    d['finalization_dispatched_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(p), suffix='.tmp', delete=False) as f:
        json.dump(d, f, indent=2)
        tmp = f.name
    os.replace(tmp, p)
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

# ── Detect supported cron-add flags ─────────────────────────────────────────
# `--no-deliver` tells the scheduler NOT to attempt announcing run summaries
# over the outbound mux. Without it, cron jobs whose runs exit 0 still get
# marked `error` on deployments where the outbound route isn't bound
# (observed on clawdi installs: `mux outbound failed (403): route not bound`).
# Our cron jobs write results to runtime/ JSONs — the dashboard reads those
# directly, announce delivery is pure overhead.
CRON_ADD_EXTRA_FLAGS=""
if openclaw cron add --help 2>&1 | grep -q -- '--no-deliver'; then
  CRON_ADD_EXTRA_FLAGS="--no-deliver"
  log_info "openclaw CLI supports --no-deliver — registering cron jobs with announce disabled."
else
  log_warn "openclaw CLI does NOT support --no-deliver. Cron run status may show 'error' on delivery failures even when script succeeds. Consider upgrading: pnpm up -g openclaw@latest"
fi

# ── Preflight: plugin-entries diagnostic (informational) ──────────────────
# `scripts/repair-plugin-entries.sh` cross-references openclaw.json's
# `plugins.entries` against the CLI's authoritative `openclaw plugins list
# --json` output + `openclaw plugins doctor`, and prints actionable jq
# commands for any mismatches/orphans. When cron registration later fails
# with `plugin_config_mismatch`, we surface this preflight report in the
# partial-failure diagnostic so the operator doesn't have to spend 30+
# minutes figuring out which jq edit will unblock them.
_DOCTOR_OUTPUT=""
_DOCTOR_HAS_PLUGIN_WARNING=0
_REPAIR_TOOL="$SCRIPT_DIR/repair-plugin-entries.sh"
if [ -x "$_REPAIR_TOOL" ]; then
  _DOCTOR_OUTPUT="$(bash "$_REPAIR_TOOL" --json 2>/dev/null || true)"
  if [ -n "$_DOCTOR_OUTPUT" ]; then
    _REPAIR_STATUS="$(printf '%s' "$_DOCTOR_OUTPUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo "unknown")"
    case "$_REPAIR_STATUS" in
      ok) ;;
      mismatches_found|orphans_found|unactivated_plugins|plugin_manifest_mismatch|doctor_warnings)
        _DOCTOR_HAS_PLUGIN_WARNING=1
        log_warn "Plugin entries check: ${_REPAIR_STATUS}. This is a common root cause of cron registration failure."
        # Surface the first specific fix command inline so agents don't have to
        # shell out to the repair tool to see the action. Only show one line
        # (the rest is in the tool's full report).
        _TOP_FIX="$(printf '%s' "$_DOCTOR_OUTPUT" | python3 -c '
import sys, json
r = json.load(sys.stdin)
for m in r.get("mismatches", []):
    cmds = m.get("fix_commands") or []
    if cmds:
        print(f"MISMATCH {m[\"config_key\"]}.{m[\"field\"]}: {cmds[0]}")
        sys.exit(0)
for o in r.get("orphans", []):
    cmds = o.get("fix_commands") or []
    if cmds:
        print(f"ORPHAN {o[\"config_key\"]}: {cmds[0]}")
        sys.exit(0)
for u in r.get("unactivated_plugins", []):
    fix = u.get("fix_command")
    if fix:
        print(f"NEEDS ACTIVATION {u[\"plugin_id\"]}: {fix}")
        sys.exit(0)
for mm in r.get("plugin_manifest_mismatches", []):
    cmds = mm.get("fix_commands") or []
    if cmds:
        print(f"MANIFEST MISMATCH {mm[\"plugin_id\"]} (in {mm[\"root_dir\"]}): {cmds[0]}")
        sys.exit(0)
' 2>/dev/null || true)"
        if [ -n "$_TOP_FIX" ]; then
          log_warn "Suggested fix (one jq command): ${_TOP_FIX}"
        fi
        log_warn "Full report: bash $(printf '%q' "$_REPAIR_TOOL")"
        log_warn "If any cron adds fail, the partial-failure JSON will include the full repair diagnostic." ;;
      *) ;;  # parse errors or unknown — include silently in diagnostic
    esac
  fi
elif openclaw doctor --help >/dev/null 2>&1; then
  # Fallback to plain `openclaw doctor` if the repair tool isn't where we
  # expect (e.g., someone invoked finalize-crons.sh from an older checkout).
  _DOCTOR_OUTPUT="$(openclaw doctor 2>&1 | head -c 4000 || true)"
  if printf '%s' "$_DOCTOR_OUTPUT" | grep -qiE 'plugin.*(mismatch|not found|entry hint)'; then
    _DOCTOR_HAS_PLUGIN_WARNING=1
    log_warn "openclaw doctor reports plugin warnings — plugin config mismatches are a common root cause of cron registration failure."
  fi
fi

# ── Read jobs from intent artifact ──────────────────────────────────────────
JOB_COUNT="$(INTENT_PATH="$INTENT_PATH" python3 -c "
import json, os
with open(os.environ['INTENT_PATH']) as f:
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

# ── Helpers ─────────────────────────────────────────────────────────────────
# (Defined before the rm loop so post-rm verification can use verify_registered.)

# classify_cron_error STDERR_TEXT → prints one classification token to stdout.
#
# Classifications (grouped by retry-worthiness):
#   PERMANENT (no point retrying — fix the config, not the connection):
#     plugin_config_mismatch — openclaw.json entry ≠ plugin package.json name
#     plugin_not_found       — referenced plugin isn't installed/registered
#     pairing_required       — CLI device needs a scope-upgrade approval
#     permission_denied      — auth, 401/403, invalid api key
#     schema_invalid         — bad request, validation error, 400
#   TRANSIENT (the retry loop's reason to exist):
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
    *"pairing required"*|*"scope-upgrade"*|*"scope upgrade"*|*"asking for more scopes"*)
      echo "pairing_required" ;;
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
    plugin_config_mismatch|plugin_not_found|pairing_required|permission_denied|schema_invalid) return 0 ;;
    *) return 1 ;;
  esac
}

# kind_to_hint CLASSIFICATION → prints the operator-facing fix hint.
kind_to_hint() {
  case "$1" in
    plugin_config_mismatch)
      echo "Plugin id mismatch between openclaw.json and the plugin's package.json \"name\". Run \`bash scripts/repair-plugin-entries.sh\` — it prints the exact jq commands to fix your config. Then restart OpenClaw and re-run this script." ;;
    plugin_not_found)
      echo "Scheduler references a plugin that isn't installed/registered. Fix: install the plugin (\`openclaw plugin install ...\`) or remove the stale entry from ~/.openclaw/openclaw.json." ;;
    pairing_required)
      echo "The OpenClaw CLI device needs a scope-upgrade approval before shell-driven cron registration can continue. Fix: run \`openclaw devices list --json\`, approve the pending request, then re-run this script. If an agent/tool cron path is available, use the deferred cron intent artifact instead of shelling out through the CLI." ;;
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

# register_one_with_retry: retries transient failures (gateway/connection
# resets, normal-closure websocket drops, timeouts). Captures stderr tail and
# a classification for the *final* failed attempt. Permanent errors break the
# retry loop immediately (no point sleeping through 3 identical plugin-id
# mismatches).
LAST_REGISTER_ERR_TAIL=""
LAST_REGISTER_ERR_KIND="unknown"
register_one_with_retry() {
  local name="$1" schedule="$2" session="$3" message="$4"
  # Sanitize name for filesystem use (defend against path traversal if name
  # contains '/' or '..'). Alphanumerics / underscore / dash only; everything
  # else becomes '_'. Names that only differ in non-alnum chars will collide
  # on err_file, which is acceptable for current installer inputs (three
  # fixed names) and logged distinctly anyway.
  local safe_name
  safe_name="$(printf '%s' "$name" | tr -c 'a-zA-Z0-9_-' '_')"
  local err_file="$_STAGE_DIR/add-${safe_name}.err"
  local attempt max_attempts=3
  local last_kind="unknown"

  for attempt in 1 2 3; do
    # Back off before attempts 2 and 3; attempt 1 is immediate
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

    # Classify this attempt's error so we can fail-fast on permanent errors.
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

  # Join last 3 stderr lines with ' | '. awk (not tr) because `tr '\n' ' | '`
  # only uses the first char of set2 (space), dropping the pipe.
  LAST_REGISTER_ERR_TAIL="$(tr -d '\r' < "$err_file" 2>/dev/null | tail -n 3 | awk 'NR>1{printf " | "} {printf "%s",$0}')"
  LAST_REGISTER_ERR_KIND="$last_kind"
  return 1
}

# verify_registered: post-check that a job name exists in scheduler.
# Used both for post-rm residual detection and for post-add success
# confirmation when the CLI reports non-zero but the job did get created
# (observed with gateway flaps).
verify_registered() {
  local name="$1"
  # Escape regex metacharacters in name so names containing '.', '*', '[', etc.
  # don't produce false matches or break the regex.
  local name_re
  name_re="$(printf '%s' "$name" | sed 's/[][\\.^$*+?(){}|/]/\\&/g')"
  openclaw cron list 2>/dev/null | grep -qE "(^|[[:space:]\"'])${name_re}([[:space:]\"']|$)"
}

# ── Remove existing jobs (idempotent) ────────────────────────────────────────
# stderr goes to the stage dir for later inspection; rm failures when the job
# doesn't exist are fine (idempotent). But if rm *silently* failed on a job
# that DID exist (e.g., gateway flap), we'd later see that stale job in
# cron list and verify_registered would false-positive a subsequent failed
# add. Post-rm verify below catches that.
INTENT_PATH="$INTENT_PATH" python3 -c "
import json, os
with open(os.environ['INTENT_PATH']) as f:
    d = json.load(f)
for j in d.get('jobs', []):
    print(j['name'])
" 2>/dev/null | while IFS= read -r job_name; do
  [ -n "$job_name" ] && openclaw cron rm "$job_name" >/dev/null 2>>"$_STAGE_DIR/rm.err" || true
done

# Short propagation delay so cron list reflects the rms before verification.
sleep 1

# Post-rm verify: every target name must now be ABSENT. If any is still
# present, rm silently failed and proceeding would allow a false-verified
# state on a later failed add (stale job satisfying the name match).
_RESIDUAL_JOBS=""
while IFS= read -r job_name; do
  [ -z "$job_name" ] && continue
  if verify_registered "$job_name"; then
    _RESIDUAL_JOBS="${_RESIDUAL_JOBS:+$_RESIDUAL_JOBS, }$job_name"
  fi
done < <(INTENT_PATH="$INTENT_PATH" python3 -c "
import json, os
with open(os.environ['INTENT_PATH']) as f:
    d = json.load(f)
for j in d.get('jobs', []):
    print(j['name'])
" 2>/dev/null)

if [ -n "$_RESIDUAL_JOBS" ]; then
  log_warn "cron rm did not actually remove: ${_RESIDUAL_JOBS} — aborting to avoid false-verified registration state"
  _RM_ERR_TAIL="$(tr -d '\r' < "$_STAGE_DIR/rm.err" 2>/dev/null | tail -n 5 | awk 'NR>1{printf " | "} {printf "%s",$0}')"
  JSON_OUT="$(RES="$_RESIDUAL_JOBS" ERR="$_RM_ERR_TAIL" python3 -c "
import json, os
print(json.dumps({
    'status': 'rm_failed',
    'residual_jobs': os.environ['RES'],
    'rm_stderr_tail': os.environ['ERR'],
    'message': 'openclaw cron rm silently left jobs behind (likely gateway flap). Re-run the script or manually remove stale jobs first.',
}))
" 2>/dev/null || printf '{"status":"rm_failed","message":"residual jobs after rm"}')"
  echo "$JSON_OUT"
  exit 3
fi

# ── Register each job (with retry + verification) ───────────────────────────
REGISTERED=0
FAILED=0
FAILED_NAMES=""
FAILED_KINDS=""
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

  log_warn "Failed to register: ${name} [${LAST_REGISTER_ERR_KIND}]"
  [ -n "$LAST_REGISTER_ERR_TAIL" ] && log_warn "  ↳ ${LAST_REGISTER_ERR_TAIL}"
  FAILED=$((FAILED + 1))
  FAILED_NAMES="${FAILED_NAMES:+$FAILED_NAMES, }$name"
  # Track per-job kinds for the aggregate diagnostic.
  FAILED_KINDS="${FAILED_KINDS:+$FAILED_KINDS }${LAST_REGISTER_ERR_KIND}"
  # Build the failed-details entry with python json.dumps so that name,
  # stderr_tail, AND kind are JSON-escaped. A pathologically-named job
  # (containing '"' or '\\') would otherwise emit malformed JSON.
  _entry="$(TAIL="$LAST_REGISTER_ERR_TAIL" NAME="$name" KIND="$LAST_REGISTER_ERR_KIND" python3 -c "
import json, os
print(json.dumps({'name': os.environ['NAME'], 'stderr_tail': os.environ['TAIL'], 'kind': os.environ['KIND']}))
" 2>/dev/null || printf '{"name":"?","stderr_tail":"?","kind":"unknown"}')"
  FAILED_DETAILS_JSON="${FAILED_DETAILS_JSON}${_sep}${_entry}"
  _sep=","
done < <(INTENT_PATH="$INTENT_PATH" python3 -c "
import json, os
with open(os.environ['INTENT_PATH']) as f:
    d = json.load(f)
for j in d.get('jobs', []):
    print(f\"{j['name']}\t{j['schedule']}\t{j['session']}\t{j['message']}\")
" 2>/dev/null)
FAILED_DETAILS_JSON="${FAILED_DETAILS_JSON}]"

# ── Update artifacts on success ─────────────────────────────────────────────
if [ "$FAILED" -eq 0 ] && [ "$REGISTERED" -gt 0 ]; then
  log_ok "All ${REGISTERED} cron jobs registered successfully"

  # Update intent artifact with finalization receipt
  INTENT_PATH="$INTENT_PATH" REGISTERED="$REGISTERED" python3 -c "
import json, os, tempfile
from datetime import datetime, timezone
p = os.environ['INTENT_PATH']
with open(p) as f:
    d = json.load(f)
d['finalized_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
d['finalized_by'] = 'finalize-crons-script'
d['mode'] = 'finalized'
d['registered_count'] = int(os.environ['REGISTERED'])
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
    INSTALLED_FILE="$installed_file" python3 -c "
import json, os, tempfile
from datetime import datetime, timezone
p = os.environ['INSTALLED_FILE']
with open(p) as f:
    d = json.load(f)
d['crons_registered'] = True
d['cron_registration_mode'] = 'local-cli'
d['cron_registration_status'] = 'registered'
d['cron_finalized_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
d['cron_finalized_by'] = 'finalize-crons-script'
# Recompute install_status: verified if all conditions met
if d.get('identity_resolved') and d.get('credentials_exist') and d.get('dashboard_status') == 'running':
    d['install_status'] = 'verified'
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
  # Aggregate failed kinds into a single summary classification. If every
  # failure classifies the same way, surface that kind directly. Otherwise,
  # "mixed_failures" signals the operator to inspect failed_details[].kind
  # per job.
  _AGG_KIND="unknown"
  if [ -n "$FAILED_KINDS" ]; then
    _UNIQ_KINDS="$(printf '%s\n' $FAILED_KINDS | sort -u | tr '\n' ' ' | sed 's/ $//')"
    # shellcheck disable=SC2086
    set -- $_UNIQ_KINDS
    if [ "$#" -eq 1 ]; then
      _AGG_KIND="$1"
    else
      _AGG_KIND="mixed_failures"
    fi
  fi
  _AGG_HINT="$(kind_to_hint "$_AGG_KIND")"

  log_warn "${REGISTERED}/${JOB_COUNT} jobs registered, ${FAILED} failed: ${FAILED_NAMES}"

  # Human-readable summary block on stderr so operators reading terminal
  # output see the fix hint without having to parse the JSON stdout. This
  # is the piece that was missing — clawdi's agent had to recognise
  # "plugin id mismatch" and derive the fix itself last time.
  {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "❌ ${FAILED}/${JOB_COUNT} cron jobs failed to register."
    echo ""
    echo "Root cause (aggregate classification): ${_AGG_KIND}"
    if [ -n "$_AGG_HINT" ]; then
      # Wrap the hint at ~72 chars for terminal readability using fold.
      printf '%s\n' "$_AGG_HINT" | fold -s -w 72 | sed 's/^/  /'
    fi
    if [ "$_DOCTOR_HAS_PLUGIN_WARNING" -eq 1 ] && [ -n "$_DOCTOR_OUTPUT" ]; then
      echo ""
      echo "openclaw doctor output (plugin warnings — likely relevant):"
      printf '%s\n' "$_DOCTOR_OUTPUT" | head -n 20 | sed 's/^/  /'
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
  } >&2

  # Structured JSON to stdout (unchanged consumers get the same top-level
  # fields; new `diagnostic` + per-job `kind` are additive).
  _PARTIAL_JSON="$(REGISTERED="$REGISTERED" FAILED="$FAILED" FAILED_NAMES="$FAILED_NAMES" FAILED_DETAILS="$FAILED_DETAILS_JSON" AGG_KIND="$_AGG_KIND" AGG_HINT="$_AGG_HINT" DOCTOR_OUT="$_DOCTOR_OUTPUT" DOCTOR_WARN="$_DOCTOR_HAS_PLUGIN_WARNING" python3 -c "
import json, os
diagnostic = {
    'summary': os.environ['AGG_KIND'],
    'hint': os.environ['AGG_HINT'],
}
if os.environ.get('DOCTOR_WARN') == '1' and os.environ.get('DOCTOR_OUT'):
    diagnostic['doctor_output'] = os.environ['DOCTOR_OUT']
print(json.dumps({
    'status': 'partial',
    'registered': int(os.environ['REGISTERED']),
    'failed': int(os.environ['FAILED']),
    'failed_names': os.environ['FAILED_NAMES'],
    'failed_details': json.loads(os.environ['FAILED_DETAILS']),
    'diagnostic': diagnostic,
    'message': (
        'Some jobs failed to register. See diagnostic.summary for the aggregate '
        'root cause and diagnostic.hint for the fix. Per-job kind + stderr_tail '
        'in failed_details[] for debugging mixed failures.'
    ),
}))
" 2>/dev/null || printf '{\"status\":\"partial\",\"registered\":%d,\"failed\":%d,\"message\":\"partial (json encoder failed)\"}' "$REGISTERED" "$FAILED")"
  echo "$_PARTIAL_JSON"
  exit 3
fi

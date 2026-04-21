#!/usr/bin/env bash
# main-heartbeat.sh — Dedicated main-heartbeat entrypoint for self-ip-agency
#
# This is the RECOMMENDED first-run self-check and recurring heartbeat script.
# It replaces the legacy pattern of "Read runtime/main/task.json" which does not
# exist and causes external agents to fall back to generic/idle behavior.
#
# What this script does:
#   1. Validates the runtime environment (installed, identity, credentials)
#   2. Builds the main input packet (TAS scores, strategy, wiki status)
#   3. Runs the main runtime orchestrator (dispatch bookmarker/trader if needed)
#   4. Writes heartbeat.json and status.json to runtime/main/
#
# Usage:
#   bash scripts/main-heartbeat.sh                    # normal heartbeat
#   bash scripts/main-heartbeat.sh --self-check       # first-run validation only
#   bash scripts/main-heartbeat.sh --dry-run          # show what would run
#
# Source of truth: See HEARTBEAT.md (repo root) and docs/main-heartbeat-contract.md
# NOTE: runtime/main/task.json is NOT the primary task queue. See contract docs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared library (works from both repo and deployed workspace)
if [ -f "$SCRIPT_DIR/lib/common.sh" ]; then
  source "$SCRIPT_DIR/lib/common.sh"
else
  echo "[FATAL] lib/common.sh not found at $SCRIPT_DIR/lib/" >&2
  exit 1
fi

# Resolve REPO_DIR, WORKSPACE, AGENCY_VERSION from context
resolve_agency_paths "$SCRIPT_DIR"
RUNTIME_MAIN="$WORKSPACE/runtime/main"

# ── Color helpers (override common.sh log format for cycle output) ───────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
log_ok()   { echo -e "${GREEN}[OK]${RESET} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${RESET} $1"; }
log_fail() { echo -e "${RED}[FAIL]${RESET} $1"; }
log_info() { echo -e "[INFO] $1"; }

# ── Parse args ───────────────────────────────────────────────────────────────
MODE="heartbeat"
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --self-check) MODE="self-check" ;;
    --dry-run)    DRY_RUN=true ;;
    *)            log_warn "Unknown argument: $arg" ;;
  esac
done

# ── Phase 1: Environment validation (always runs) ───────────────────────────

validate_environment() {
  local errors=0

  log_info "Validating main-heartbeat environment (v$AGENCY_VERSION)..."

  # 1. Check .installed marker (workspace or repo)
  if check_agency_installed; then
    log_ok "Agency installed"
  else
    log_fail "Agency not installed — run: bash scripts/install.sh"
    errors=$((errors + 1))
  fi

  # 2. Check runtime/main directory
  if [ -d "$RUNTIME_MAIN" ]; then
    log_ok "runtime/main/ exists at $RUNTIME_MAIN"
  else
    log_fail "runtime/main/ not found at $RUNTIME_MAIN"
    errors=$((errors + 1))
  fi

  # 3. Check identity (workspace or repo)
  local identity_file=""
  if [ -f "$WORKSPACE/config/agency-identity.json" ]; then
    identity_file="$WORKSPACE/config/agency-identity.json"
  elif [ -n "${REPO_DIR:-}" ] && [ -f "$REPO_DIR/config/agency-identity.json" ]; then
    identity_file="$REPO_DIR/config/agency-identity.json"
  fi
  if [ -n "$identity_file" ] && [ -f "$identity_file" ]; then
    local username
    username="$(python3 -c "import json; d=json.load(open('$identity_file')); print(d.get('agent',{}).get('username',''))" 2>/dev/null || echo "")"
    if [ -n "$username" ]; then
      log_ok "Identity resolved: $username"
    else
      log_warn "Identity file exists but username is empty"
    fi
  else
    log_warn "Identity file not found — run install.sh with TagClaw API access"
    identity_file=""
  fi

  # 4. Check TagClaw skill credentials
  if [ -f "$WORKSPACE/skills/tagclaw/.env" ] && python3 -c "
import pathlib
path = pathlib.Path('$WORKSPACE/skills/tagclaw/.env')
for line in path.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s:
        continue
    k, v = s.split('=', 1)
    if k.strip() == 'TAGCLAW_API_KEY' and v.strip().strip('\"').strip(\"'\"):
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null; then
    log_ok "skills/tagclaw/.env exists and contains TAGCLAW_API_KEY"
  else
    log_warn "TagClaw skill credentials not configured — see docs/deployment-guide.md"
  fi

  # 5. Check heartbeat template
  if [ -f "$RUNTIME_MAIN/heartbeat.json" ]; then
    log_ok "runtime/main/heartbeat.json exists"
  else
    log_warn "runtime/main/heartbeat.json not found (will be created on first cycle)"
  fi

  return $errors
}

# ── Phase 2: Build input packet ─────────────────────────────────────────────

build_input_packet() {
  log_info "Building main input packet..."

  # Find the script in SCRIPT_DIR (co-located), workspace, or repo
  local script=""
  for candidate in \
    "$SCRIPT_DIR/build_main_input_packet_v2.py" \
    "$WORKSPACE/scripts/build_main_input_packet_v2.py" \
    "${REPO_DIR:+$REPO_DIR/scripts/build_main_input_packet_v2.py}"; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
      script="$candidate"
      break
    fi
  done

  if [ -n "$script" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      log_info "[DRY RUN] Would run: python3 $script"
    else
      python3 "$script" 2>&1 || {
        log_warn "Input packet build failed — continuing with stale data"
      }
    fi
  else
    log_warn "build_main_input_packet_v2.py not found — skipping input packet"
  fi
}

# ── Phase 3: Run main runtime ───────────────────────────────────────────────

run_main_runtime() {
  log_info "Running main runtime orchestrator..."

  local script=""
  for candidate in \
    "$SCRIPT_DIR/run_main_runtime_v2.py" \
    "$WORKSPACE/scripts/run_main_runtime_v2.py" \
    "${REPO_DIR:+$REPO_DIR/scripts/run_main_runtime_v2.py}"; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
      script="$candidate"
      break
    fi
  done

  if [ -n "$script" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      log_info "[DRY RUN] Would run: python3 $script"
    else
      python3 "$script" 2>&1 || {
        log_fail "Main runtime failed"
        return 1
      }
    fi
  else
    log_warn "run_main_runtime_v2.py not found — writing minimal heartbeat"
    write_minimal_heartbeat
  fi
}

# ── Fallback: write minimal heartbeat ────────────────────────────────────────

write_minimal_heartbeat() {
  mkdir -p "$RUNTIME_MAIN"
  local RUNTIME_SHARED="$WORKSPACE/runtime/shared"
  mkdir -p "$RUNTIME_SHARED"
  python3 -c "
import json
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
ts = now.isoformat()

hb = {
    'heartbeat_id': 'hb-' + now.strftime('%Y%m%d%H%M%S'),
    'timestamp': ts,
    'mode': 'self-check',
    'source': 'main-heartbeat.sh',
    'tas_score': 0.0,
    'tas_social': 0.0,
    'tas_trade': 0.0,
    'bookmarker_status': 'unknown',
    'trader_status': 'unknown',
    'alerts': [],
    'schema': 'main.heartbeat.v1'
}
with open('$RUNTIME_MAIN/heartbeat.json', 'w') as f:
    json.dump(hb, f, indent=2)
print('Wrote heartbeat.json')

# Also write latest.json so dashboard agent pill shows a timestamp
latest = {
    'schema': 'main.latest.v1',
    'generated_at': ts,
    'status': 'self-check',
    'source': 'main-heartbeat.sh'
}
with open('$RUNTIME_MAIN/latest.json', 'w') as f:
    json.dump(latest, f, indent=2)
print('Wrote latest.json')

# Update shared runtime-status with main heartbeat timestamp
import os
rs_path = '$RUNTIME_SHARED/runtime-status.json'
try:
    rs = json.load(open(rs_path))
except Exception:
    rs = {}
rs.setdefault('schema', 'runtime-status.v1')
rs['main'] = {'status': 'self-check', 'updated_at': ts, 'last_heartbeat': ts}
rs.pop('bootstrap', None)
with open(rs_path, 'w') as f:
    json.dump(rs, f, indent=2)
print('Updated runtime-status.json')
" 2>&1
}

# ──────────────────────────────────────────────────────────────────────────────
# Phase 2.5: Owner binding self-heal (PR-B)
#
# Checks whether TagClaw /me can now confirm the owner.twitter_handle that
# was null at install time. Fully idempotent; opt-out via config. Errors are
# isolated — /me network failures NEVER pollute HEARTBEAT_STATUS and NEVER
# get classified as scheduler_unreachable (see PR #25 / §4.7 of the design).
#
# Triggers refresh-agency-identity.sh --verify-api when ALL of:
#   - TAGCLAW_STATUS == "active" in skill .env
#   - owner.verified != true in workspace config/agency-identity.json
#   - identity-sync.json does not say disabled
#   - last_attempt_at was not within the throttle window
#
# On success writes runtime/shared/events/owner-binding-resolved.json and
# flips identity-sync.json:verified=true so future heartbeats skip /me.
#
# Design refs: docs/design/x-sync-twitter-binding-fix.md §4.3 + §4.4.
# ──────────────────────────────────────────────────────────────────────────────

owner_binding_self_heal() {
  [ "$DRY_RUN" = "true" ] && { log_info "[dry-run] skip owner-binding self-heal"; return 0; }

  local RUNTIME_SHARED="$WORKSPACE/runtime/shared"
  local EVENTS_DIR="$RUNTIME_SHARED/events"
  local SYNC_FILE="$RUNTIME_SHARED/identity-sync.json"
  mkdir -p "$RUNTIME_SHARED" "$EVENTS_DIR" 2>/dev/null || true

  # Locate refresh helper (prefer workspace deploy copy, fall back to repo)
  local REFRESH_SCRIPT=""
  for candidate in \
    "$WORKSPACE/scripts/refresh-agency-identity.sh" \
    "$SCRIPT_DIR/refresh-agency-identity.sh" \
    "${REPO_DIR:+$REPO_DIR/scripts/refresh-agency-identity.sh}"; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
      REFRESH_SCRIPT="$candidate"
      break
    fi
  done
  if [ -z "$REFRESH_SCRIPT" ]; then
    log_warn "owner-binding self-heal: refresh-agency-identity.sh not found; skipping (non-fatal)"
    return 0
  fi

  # Orchestration + state decisions live in Python for quote safety + atomic JSON.
  # Returns one of: SKIP_VERIFIED / SKIP_DISABLED / SKIP_NOT_ACTIVE / SKIP_THROTTLED
  #                 / RUN / ERROR_READING_IDENTITY
  local DECISION
  DECISION="$(WORKSPACE="$WORKSPACE" python3 - <<'PY'
import json, os, pathlib, time
workspace = pathlib.Path(os.environ["WORKSPACE"])

def parse_dotenv(p):
    data = {}
    if not p.exists():
        return data
    try:
        txt = p.read_text()
    except OSError:
        return data
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        data[k.strip()] = v
    return data

skill_env = parse_dotenv(workspace / "skills/tagclaw/.env")
status = (skill_env.get("TAGCLAW_STATUS") or "").strip().lower()
if status != "active":
    print("SKIP_NOT_ACTIVE")
    raise SystemExit(0)

ident_path = workspace / "config/agency-identity.json"
verified = False
try:
    if ident_path.exists():
        d = json.loads(ident_path.read_text(encoding="utf-8"))
        o = d.get("owner") or {}
        verified = bool(o.get("verified"))
except Exception:
    print("ERROR_READING_IDENTITY")
    raise SystemExit(0)

if verified:
    print("SKIP_VERIFIED")
    raise SystemExit(0)

sync_path = workspace / "runtime/shared/identity-sync.json"
state = {}
if sync_path.exists():
    try:
        state = json.loads(sync_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}

if state.get("disabled") is True:
    print("SKIP_DISABLED")
    raise SystemExit(0)

# Throttle: minimum 60s between attempts to prevent concurrent cron collisions.
# Per user decision (§7 #4 locked-in): no exponential backoff until verified.
last_ts = state.get("last_attempt_at") or ""
throttle_sec = 60
now = int(time.time())
try:
    import datetime as _dt
    if last_ts:
        t = _dt.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        if (now - int(t.timestamp())) < throttle_sec:
            print("SKIP_THROTTLED")
            raise SystemExit(0)
except Exception:
    pass

print("RUN")
PY
)"

  case "$DECISION" in
    SKIP_VERIFIED)      return 0 ;;
    SKIP_DISABLED)      return 0 ;;
    SKIP_NOT_ACTIVE)    return 0 ;;
    SKIP_THROTTLED)     return 0 ;;
    ERROR_READING_IDENTITY)
      log_warn "owner-binding self-heal: could not parse identity JSON (non-fatal)"
      return 0 ;;
    RUN)  ;;
    *)
      log_warn "owner-binding self-heal: unknown decision '$DECISION' (non-fatal)"
      return 0 ;;
  esac

  log_info "owner-binding self-heal: TagClaw status=active and identity not yet verified — probing /me"

  # Pre-run: stamp attempt timestamp so concurrent heartbeats throttle each other.
  WORKSPACE="$WORKSPACE" python3 - <<'PY' >/dev/null 2>&1 || true
import json, os, pathlib, datetime
ws = pathlib.Path(os.environ["WORKSPACE"])
p = ws / "runtime/shared/identity-sync.json"
try:
    state = json.loads(p.read_text()) if p.exists() else {}
except Exception:
    state = {}
state["schema"] = "identity.sync.v1"
state["last_attempt_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
state["attempts"] = int(state.get("attempts", 0)) + 1
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(state, indent=2))
PY

  # Run the refresh helper. /me network errors MUST NOT propagate — we catch
  # rc and record it in identity-sync.json.last_error without touching the
  # heartbeat's own status.
  local rc=0
  bash "$REFRESH_SCRIPT" --workspace "$WORKSPACE" --verify-api --quiet >/tmp/.self-ip-self-heal-$$.log 2>&1 || rc=$?
  local log_tail
  log_tail="$(tail -5 /tmp/.self-ip-self-heal-$$.log 2>/dev/null || true)"
  rm -f /tmp/.self-ip-self-heal-$$.log 2>/dev/null || true

  WORKSPACE="$WORKSPACE" REFRESH_RC="$rc" REFRESH_LOG="$log_tail" python3 - <<'PY' 2>&1 || log_warn "owner-binding self-heal: state update failed (non-fatal)"
import json, os, pathlib, datetime
ws = pathlib.Path(os.environ["WORKSPACE"])
rc = int(os.environ.get("REFRESH_RC", "1"))
log_tail = os.environ.get("REFRESH_LOG", "")

sync_path = ws / "runtime/shared/identity-sync.json"
try:
    state = json.loads(sync_path.read_text())
except Exception:
    state = {}

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

ident_path = ws / "config/agency-identity.json"
verified = False
handle = None
twitter_id = None
source = None
try:
    if ident_path.exists():
        d = json.loads(ident_path.read_text(encoding="utf-8"))
        o = d.get("owner") or {}
        verified = bool(o.get("verified"))
        handle = o.get("twitter_handle")
        twitter_id = o.get("twitter_id")
        source = o.get("binding_source")
except Exception:
    pass

if rc == 0 and verified:
    state.update({
        "schema": "identity.sync.v1",
        "verified": True,
        "last_success_at": now,
        "last_error": None,
        "source": source,
    })
    # Event file: consumed by dashboard / ops hooks.
    events_dir = ws / "runtime/shared/events"
    events_dir.mkdir(parents=True, exist_ok=True)
    ev = {
        "schema": "owner-binding-resolved.v1",
        "resolved_at": now,
        "twitter_handle": handle,
        "twitter_id": twitter_id,
        "source": source,
    }
    (events_dir / "owner-binding-resolved.json").write_text(json.dumps(ev, indent=2))
    print(f"[OK] owner-binding self-heal resolved: handle={handle} (source={source})")
else:
    state.update({
        "schema": "identity.sync.v1",
        "verified": False,
        "last_error": f"rc={rc}" + (f" detail={log_tail[:200]!r}" if log_tail else ""),
    })
    print(f"[INFO] owner-binding self-heal deferred (rc={rc}, verified={verified}); will retry next heartbeat")

sync_path.parent.mkdir(parents=True, exist_ok=True)
sync_path.write_text(json.dumps(state, indent=2))
PY

  # Intentionally swallow rc — self-heal failures must not fail the heartbeat.
  return 0
}

update_runtime_status_post_cycle() {
  local RUNTIME_SHARED="$WORKSPACE/runtime/shared"
  mkdir -p "$RUNTIME_SHARED"
  python3 -c "
import json
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
ts = now.isoformat()
rs_path = '$RUNTIME_SHARED/runtime-status.json'
try:
    rs = json.load(open(rs_path))
except Exception:
    rs = {}
rs.setdefault('schema', 'runtime-status.v1')
rs['main'] = {
    'status': 'completed',
    'updated_at': ts,
    'last_heartbeat': ts,
}
rs.pop('bootstrap', None)
with open(rs_path, 'w') as f:
    json.dump(rs, f, indent=2)
print('Updated runtime-status.json (main=completed)')
" 2>&1
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
  echo ""
  echo "  ┌──────────────────────────────────────┐"
  echo "  │  Main Heartbeat — v$AGENCY_VERSION"
  echo "  │  Mode: $MODE"
  echo "  └──────────────────────────────────────┘"
  echo ""

  # Always validate
  validate_environment || {
    if [ "$MODE" = "self-check" ]; then
      log_fail "Self-check failed — fix errors above before running heartbeat"
      exit 1
    fi
    log_warn "Environment issues detected — attempting heartbeat anyway"
  }

  # Self-check mode stops after validation
  if [ "$MODE" = "self-check" ]; then
    log_ok "Self-check complete"
    echo ""
    echo "### BEGIN HEARTBEAT CONTRACT ###"
    echo "HEARTBEAT_MODE=\"self-check\""
    echo "HEARTBEAT_SOURCE=\"scripts/main-heartbeat.sh\""
    echo "HEARTBEAT_STATUS=\"validated\""
    echo "TASK_JSON_IS_PRIMARY=\"false\""
    echo "### END HEARTBEAT CONTRACT ###"
    exit 0
  fi

  # Full heartbeat cycle
  # PR-B: owner-binding self-heal runs first so input packet / runtime see
  # any freshly-verified binding. Failures here are isolated and non-fatal.
  owner_binding_self_heal || true
  build_input_packet
  run_main_runtime
  update_runtime_status_post_cycle

  log_ok "Main heartbeat cycle complete"

  echo ""
  echo "### BEGIN HEARTBEAT CONTRACT ###"
  echo "HEARTBEAT_MODE=\"heartbeat\""
  echo "HEARTBEAT_SOURCE=\"scripts/main-heartbeat.sh\""
  echo "HEARTBEAT_STATUS=\"completed\""
  echo "TASK_JSON_IS_PRIMARY=\"false\""
  echo "### END HEARTBEAT CONTRACT ###"
}

main "$@"

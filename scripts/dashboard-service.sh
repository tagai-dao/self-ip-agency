#!/usr/bin/env bash
# dashboard-service.sh — Canonical owner for the self-IP Agent dashboard lifecycle.
#
# Manages both the local FastAPI dashboard and the optional public Cloudflare
# Quick Tunnel. Machine-readable state of truth lives at:
#   <workspace>/runtime/shared/dashboard-service.json
#
# Usage:
#   dashboard-service.sh start-local   [--port N] [--workspace PATH]
#   dashboard-service.sh start-public  [--port N] [--workspace PATH]
#   dashboard-service.sh status        [--workspace PATH] [--json]
#   dashboard-service.sh stop          [--workspace PATH]
#
# Contract:
#   - Public exposure is OPT-IN only. `start-public` requires local dashboard
#     to be healthy first, and requires `cloudflared` to be installed.
#   - MVP provider is Cloudflare Quick Tunnel (no named tunnel, no Access).
#   - State is written atomically via scripts/lib/common.sh::atomic_write_json.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# ── Defaults ──────────────────────────────────────────────────────────────────
DASHBOARD_PORT="${VIZ_PORT:-7890}"
WORKSPACE_ARG=""
OUTPUT_JSON=false

# Resolve AGENCY_DIR for locating the dashboard source tree (server.py).
# When invoked from the repo, AGENCY_DIR is the repo root. When invoked from
# a deployed workspace, the dashboard tool lives at $WORKSPACE/tools/self-ip-dashboard.
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"

# ── Arg parsing ───────────────────────────────────────────────────────────────
SUBCOMMAND="${1:-}"
if [ -z "$SUBCOMMAND" ]; then
  cat <<'EOF' >&2
usage: dashboard-service.sh SUBCOMMAND [OPTIONS]

Subcommands:
  start-local    Start local dashboard (FastAPI/uvicorn on 127.0.0.1:PORT)
  start-public   Start local dashboard if needed, then Cloudflare Quick Tunnel
  status         Print current state from dashboard-service.json
  guide-public   Emit machine-readable JSON guidance for enabling the public URL
  stop           Stop both local dashboard and cloudflared if managed by this script

Options:
  --port N           Dashboard port (default: $VIZ_PORT or 7890)
  --workspace PATH   OpenClaw workspace path
  --json             (status only) emit raw JSON state
EOF
  exit 2
fi
shift || true

while [ $# -gt 0 ]; do
  case "$1" in
    --port) DASHBOARD_PORT="$2"; shift 2 ;;
    --port=*) DASHBOARD_PORT="${1#--port=}"; shift ;;
    --workspace) WORKSPACE_ARG="$2"; shift 2 ;;
    --workspace=*) WORKSPACE_ARG="${1#--workspace=}"; shift ;;
    --json) OUTPUT_JSON=true; shift ;;
    -h|--help)
      exec "$0"
      ;;
    *)
      log_err "Unknown option: $1"
      exit 2
      ;;
  esac
done

# ── Workspace + paths ─────────────────────────────────────────────────────────
if [ -n "$WORKSPACE_ARG" ]; then
  WORKSPACE="$WORKSPACE_ARG"
else
  WORKSPACE="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
fi

STATE_FILE="$WORKSPACE/runtime/shared/dashboard-service.json"
LOGS_DIR="$WORKSPACE/logs"
LOCAL_LOG="$LOGS_DIR/dashboard.log"
TUNNEL_LOG="$LOGS_DIR/dashboard-tunnel.log"
DASHBOARD_TOOL="$WORKSPACE/tools/self-ip-dashboard"

mkdir -p "$LOGS_DIR" "$WORKSPACE/runtime/shared"

# ── State helpers ─────────────────────────────────────────────────────────────
# Read existing state into python, merge the caller's JSON patch, atomic-write.
_now_iso() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

_write_state() {
  # Args: local_status port pid health_url local_started_at
  #       public_status pub_pid pub_url pub_log pub_started_at
  # Missing scalar values can be passed as empty string.
  local local_status="$1" port="$2" pid="$3" health_url="$4" local_started_at="$5"
  local public_status="$6" pub_pid="$7" pub_url="$8" pub_log="$9" pub_started_at="${10}"
  local now
  now="$(_now_iso)"

  local json
  json="$(python3 -c '
import json, sys

def _int_or_none(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None

def _str_or_none(s):
    return s if s else None

(local_status, port, pid, health_url, local_started_at,
 public_status, pub_pid, pub_url, pub_log, pub_started_at, now) = sys.argv[1:12]

payload = {
    "schema": "dashboard.service.v1",
    "local": {
        "status": local_status or "unknown",
        "port": _int_or_none(port),
        "pid": _int_or_none(pid),
        "health_url": _str_or_none(health_url),
        "started_at": _str_or_none(local_started_at),
    },
    "public": {
        "status": public_status or "disabled",
        "provider": "cloudflare",
        "mode": "quick",
        "pid": _int_or_none(pub_pid),
        "url": _str_or_none(pub_url),
        "log_file": _str_or_none(pub_log),
        "started_at": _str_or_none(pub_started_at),
    },
    "updated_at": now,
}
print(json.dumps(payload, indent=2))
' "$local_status" "$port" "$pid" "$health_url" "$local_started_at" \
  "$public_status" "$pub_pid" "$pub_url" "$pub_log" "$pub_started_at" "$now")"
  # Redirect status log to stderr so --json callers get pure JSON on stdout.
  atomic_write_json "$STATE_FILE" "$json" >&2
}

_read_state_field() {
  # Usage: _read_state_field <section> <field>
  # Prints the value or empty string if not set.
  local section="$1" field="$2"
  if [ ! -f "$STATE_FILE" ]; then
    echo ""
    return 0
  fi
  python3 - "$STATE_FILE" "$section" "$field" <<'PY' 2>/dev/null || echo ""
import json, sys
path, section, field = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path) as f:
        d = json.load(f)
    v = d.get(section, {}).get(field)
    print("" if v is None else v)
except Exception:
    print("")
PY
}

# ── Health check ─────────────────────────────────────────────────────────────
_health_ok() {
  # Args: port
  local port="$1"
  curl -sf "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1
}

_pid_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# ── start-local ───────────────────────────────────────────────────────────────
cmd_start_local() {
  log_info "dashboard-service: start-local (port=$DASHBOARD_PORT)"

  # Ensure dashboard files exist in the workspace tool location. If they are
  # missing and we are running from the repo, copy them in.
  if [ ! -f "$DASHBOARD_TOOL/server.py" ]; then
    if [ -f "$AGENCY_DIR/dashboard/server.py" ]; then
      mkdir -p "$DASHBOARD_TOOL"
      cp -r "$AGENCY_DIR/dashboard/." "$DASHBOARD_TOOL/"
      log_ok "Copied dashboard files into $DASHBOARD_TOOL"
    else
      log_err "Dashboard source not found at $DASHBOARD_TOOL/server.py (and no repo fallback). Run install.sh first."
      _write_state "failed" "$DASHBOARD_PORT" "" "http://127.0.0.1:${DASHBOARD_PORT}/api/health" "$(_now_iso)" \
        "disabled" "" "" "" ""
      return 1
    fi
  fi

  # Validate Python deps.
  local deps_missing=""
  for dep in fastapi uvicorn requests; do
    if ! python3 -c "import $dep" 2>/dev/null; then
      deps_missing="$deps_missing $dep"
    fi
  done
  if [ -n "$deps_missing" ]; then
    log_warn "Dashboard deps missing:$deps_missing"
    log_warn "Install:  pip3 install -r $DASHBOARD_TOOL/requirements.txt"
    _write_state "deps_missing" "$DASHBOARD_PORT" "" "http://127.0.0.1:${DASHBOARD_PORT}/api/health" "$(_now_iso)" \
      "disabled" "" "" "" ""
    return 1
  fi

  # Fast path: if something is already answering /api/health, record and return.
  if _health_ok "$DASHBOARD_PORT"; then
    local existing_pid
    existing_pid="$(pgrep -f "python3 ${DASHBOARD_TOOL}/server.py" 2>/dev/null | head -1 || true)"
    log_ok "Dashboard already healthy at http://127.0.0.1:${DASHBOARD_PORT} (pid: ${existing_pid:-unknown})"
    _write_state "running" "$DASHBOARD_PORT" "${existing_pid:-}" "http://127.0.0.1:${DASHBOARD_PORT}/api/health" "$(_now_iso)" \
      "$(_read_state_field public status)" \
      "$(_read_state_field public pid)" \
      "$(_read_state_field public url)" \
      "$(_read_state_field public log_file)" \
      "$(_read_state_field public started_at)"
    return 0
  fi

  # Start dashboard in background.
  log_info "Starting dashboard on 127.0.0.1:${DASHBOARD_PORT}..."
  OPENCLAW_WORKSPACE="$WORKSPACE" VIZ_PORT="$DASHBOARD_PORT" \
    nohup python3 "$DASHBOARD_TOOL/server.py" \
    > "$LOCAL_LOG" 2>&1 &
  local pid=$!
  local started_at
  started_at="$(_now_iso)"

  # Wait up to 8s for /api/health.
  local ok=false
  for _i in 1 2 3 4 5 6 7 8; do
    sleep 1
    if _health_ok "$DASHBOARD_PORT"; then
      ok=true
      break
    fi
    if ! _pid_alive "$pid"; then
      break
    fi
  done

  if [ "$ok" = "true" ]; then
    log_ok "Dashboard running: http://127.0.0.1:${DASHBOARD_PORT} (pid: $pid)"
    _write_state "running" "$DASHBOARD_PORT" "$pid" "http://127.0.0.1:${DASHBOARD_PORT}/api/health" "$started_at" \
      "$(_read_state_field public status)" \
      "$(_read_state_field public pid)" \
      "$(_read_state_field public url)" \
      "$(_read_state_field public log_file)" \
      "$(_read_state_field public started_at)"
    return 0
  fi

  if _pid_alive "$pid"; then
    log_warn "Dashboard process alive (pid: $pid) but /api/health did not respond within 8s. See $LOCAL_LOG"
    _write_state "started_unverified" "$DASHBOARD_PORT" "$pid" "http://127.0.0.1:${DASHBOARD_PORT}/api/health" "$started_at" \
      "$(_read_state_field public status)" \
      "$(_read_state_field public pid)" \
      "$(_read_state_field public url)" \
      "$(_read_state_field public log_file)" \
      "$(_read_state_field public started_at)"
    return 1
  fi

  log_err "Dashboard failed to start. See $LOCAL_LOG"
  _write_state "failed" "$DASHBOARD_PORT" "" "http://127.0.0.1:${DASHBOARD_PORT}/api/health" "$started_at" \
    "$(_read_state_field public status)" \
    "$(_read_state_field public pid)" \
    "$(_read_state_field public url)" \
    "$(_read_state_field public log_file)" \
    "$(_read_state_field public started_at)"
  return 1
}

# ── start-public ──────────────────────────────────────────────────────────────
cmd_start_public() {
  log_info "dashboard-service: start-public (port=$DASHBOARD_PORT)"

  # 1. Ensure local dashboard is healthy. If not, start it first.
  if ! _health_ok "$DASHBOARD_PORT"; then
    log_info "Local dashboard not healthy — starting it first..."
    if ! cmd_start_local; then
      log_err "Local dashboard is not healthy. Refusing to start public tunnel."
      _write_state "$(_read_state_field local status)" \
        "$(_read_state_field local port)" \
        "$(_read_state_field local pid)" \
        "$(_read_state_field local health_url)" \
        "$(_read_state_field local started_at)" \
        "failed" "" "" "$TUNNEL_LOG" "$(_now_iso)"
      return 1
    fi
  fi

  # 2. Require cloudflared.
  if ! command -v cloudflared >/dev/null 2>&1; then
    log_err "cloudflared not found on PATH. Install from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    _write_state "$(_read_state_field local status)" \
      "$(_read_state_field local port)" \
      "$(_read_state_field local pid)" \
      "$(_read_state_field local health_url)" \
      "$(_read_state_field local started_at)" \
      "failed" "" "" "$TUNNEL_LOG" "$(_now_iso)"
    return 1
  fi

  # 3. If a tunnel is already recorded and still alive, reuse it.
  local existing_pid existing_url
  existing_pid="$(_read_state_field public pid)"
  existing_url="$(_read_state_field public url)"
  if [ -n "$existing_pid" ] && _pid_alive "$existing_pid" && [ -n "$existing_url" ]; then
    log_ok "Public tunnel already running: $existing_url (pid: $existing_pid)"
    _write_state "$(_read_state_field local status)" \
      "$(_read_state_field local port)" \
      "$(_read_state_field local pid)" \
      "$(_read_state_field local health_url)" \
      "$(_read_state_field local started_at)" \
      "running" "$existing_pid" "$existing_url" "$TUNNEL_LOG" \
      "$(_read_state_field public started_at)"
    return 0
  fi

  # 4. Kill any stale cloudflared bound to our target URL, then start a new one.
  pkill -f "cloudflared.*--url http://127.0.0.1:${DASHBOARD_PORT}" 2>/dev/null || true
  : > "$TUNNEL_LOG"
  log_info "Starting Cloudflare Quick Tunnel → http://127.0.0.1:${DASHBOARD_PORT}"
  nohup cloudflared tunnel --url "http://127.0.0.1:${DASHBOARD_PORT}" \
    > "$TUNNEL_LOG" 2>&1 &
  local pid=$!
  local started_at
  started_at="$(_now_iso)"

  # 5. Poll log for https://*.trycloudflare.com URL (up to 30s).
  local url=""
  local _i
  for _i in $(seq 1 30); do
    sleep 1
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)"
    if [ -n "$url" ]; then
      break
    fi
    if ! _pid_alive "$pid"; then
      break
    fi
  done

  if [ -z "$url" ]; then
    log_err "Did not observe a trycloudflare.com URL within 30s. See $TUNNEL_LOG"
    # Leave the cloudflared process (if alive) for debugging; mark failed.
    _write_state "$(_read_state_field local status)" \
      "$(_read_state_field local port)" \
      "$(_read_state_field local pid)" \
      "$(_read_state_field local health_url)" \
      "$(_read_state_field local started_at)" \
      "failed" "${pid:-}" "" "$TUNNEL_LOG" "$started_at"
    return 1
  fi

  log_ok "Public dashboard URL: $url (pid: $pid)"
  log_info "To stop: bash $SCRIPT_DIR/dashboard-service.sh stop"
  _write_state "$(_read_state_field local status)" \
    "$(_read_state_field local port)" \
    "$(_read_state_field local pid)" \
    "$(_read_state_field local health_url)" \
    "$(_read_state_field local started_at)" \
    "running" "$pid" "$url" "$TUNNEL_LOG" "$started_at"
  return 0
}

# ── status ────────────────────────────────────────────────────────────────────
cmd_status() {
  if [ ! -f "$STATE_FILE" ]; then
    # Synthesize an empty baseline so callers always see the schema.
    _write_state "unknown" "$DASHBOARD_PORT" "" "http://127.0.0.1:${DASHBOARD_PORT}/api/health" "" \
      "disabled" "" "" "" ""
  fi

  # Reconcile recorded state with live processes before reporting.
  local lpid ppid lstatus pstatus purl
  lpid="$(_read_state_field local pid)"
  ppid="$(_read_state_field public pid)"
  lstatus="$(_read_state_field local status)"
  pstatus="$(_read_state_field public status)"
  purl="$(_read_state_field public url)"

  local reconciled=false
  if [ "$lstatus" = "running" ] && [ -n "$lpid" ] && ! _pid_alive "$lpid"; then
    lstatus="stopped"; reconciled=true
  fi
  if [ "$pstatus" = "running" ] && [ -n "$ppid" ] && ! _pid_alive "$ppid"; then
    pstatus="stopped"; purl=""; reconciled=true
  fi
  if [ "$reconciled" = "true" ]; then
    _write_state "$lstatus" \
      "$(_read_state_field local port)" \
      "$lpid" \
      "$(_read_state_field local health_url)" \
      "$(_read_state_field local started_at)" \
      "$pstatus" "$ppid" "$purl" \
      "$(_read_state_field public log_file)" \
      "$(_read_state_field public started_at)"
  fi

  if [ "$OUTPUT_JSON" = "true" ]; then
    cat "$STATE_FILE"
    return 0
  fi

  echo ""
  echo "  Dashboard Service"
  echo "  State file: $STATE_FILE"
  echo "  ────────────────────────────────────"
  echo "  Local:"
  echo "    status:     $(_read_state_field local status)"
  echo "    port:       $(_read_state_field local port)"
  echo "    pid:        $(_read_state_field local pid)"
  echo "    health_url: $(_read_state_field local health_url)"
  echo "    started_at: $(_read_state_field local started_at)"
  echo "  Public:"
  echo "    status:     $(_read_state_field public status)"
  echo "    provider:   cloudflare"
  echo "    mode:       quick"
  echo "    pid:        $(_read_state_field public pid)"
  echo "    url:        $(_read_state_field public url)"
  echo "    log_file:   $(_read_state_field public log_file)"
  echo "    started_at: $(_read_state_field public started_at)"
  echo ""
  return 0
}

# ── guide-public ──────────────────────────────────────────────────────────────
# Emit machine-readable guidance for enabling the public dashboard URL.
# Does NOT start anything. Intended for install-time operator guidance and for
# doctor.sh / external agents to compute the next action. Always exits 0.
cmd_guide_public() {
  local local_status local_health local_port
  local_status="$(_read_state_field local status)"
  local_health="$(_read_state_field local health_url)"
  local_port="$(_read_state_field local port)"
  [ -z "$local_port" ] && local_port="$DASHBOARD_PORT"

  local public_status public_url
  public_status="$(_read_state_field public status)"
  public_url="$(_read_state_field public url)"

  local cloudflared_installed="false"
  local install_command=""
  if command -v cloudflared >/dev/null 2>&1; then
    cloudflared_installed="true"
  else
    if command -v brew >/dev/null 2>&1; then
      install_command="brew install cloudflared"
    else
      install_command="See https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    fi
  fi

  local start_command="bash $AGENCY_DIR/scripts/dashboard-service.sh start-public --workspace $WORKSPACE"
  if [ -n "$local_port" ] && [ "$local_port" != "7890" ]; then
    start_command="$start_command --port $local_port"
  fi

  # Decide readiness + recommended next action.
  local ready="false"
  local action="wait"
  local reason=""
  if [ "$local_status" != "running" ]; then
    ready="false"
    action="start_local"
    reason="Local dashboard is not running. Start it first: bash $AGENCY_DIR/scripts/dashboard-service.sh start-local --workspace $WORKSPACE"
  elif [ "$public_status" = "running" ] && [ -n "$public_url" ]; then
    ready="true"
    action="already_running"
    reason="Public tunnel already running."
  elif [ "$cloudflared_installed" != "true" ]; then
    ready="false"
    action="install_cloudflared"
    reason="cloudflared is not installed. Install it with: $install_command"
  else
    ready="true"
    action="start_public"
    reason="Local dashboard is healthy and cloudflared is installed. Run: $start_command"
  fi

  python3 -c '
import json, sys
(ready, action, reason, local_status, local_health, local_port,
 public_status, public_url, cloudflared_installed, install_command,
 start_command, state_file) = sys.argv[1:13]
out = {
    "schema": "dashboard.public.guidance.v1",
    "ready": ready == "true",
    "action": action,
    "reason": reason,
    "local": {
        "status": local_status,
        "health_url": local_health,
        "port": int(local_port) if local_port.isdigit() else None,
    },
    "public": {
        "status": public_status or "disabled",
        "url": public_url,
    },
    "cloudflared_installed": cloudflared_installed == "true",
    "install_command": install_command,
    "start_command": start_command,
    "state_file": state_file,
    "provider": "cloudflare",
    "mode": "quick",
}
print(json.dumps(out, indent=2))
' "$ready" "$action" "$reason" "$local_status" "$local_health" "$local_port" \
   "$public_status" "$public_url" "$cloudflared_installed" "$install_command" \
   "$start_command" "$STATE_FILE"
  return 0
}

# ── stop ──────────────────────────────────────────────────────────────────────
cmd_stop() {
  log_info "dashboard-service: stop"

  local ppid lpid
  ppid="$(_read_state_field public pid)"
  lpid="$(_read_state_field local pid)"

  if [ -n "$ppid" ] && _pid_alive "$ppid"; then
    log_info "Stopping cloudflared (pid: $ppid)..."
    kill "$ppid" 2>/dev/null || true
    # Give it a moment to exit.
    for _i in 1 2 3 4 5; do
      _pid_alive "$ppid" || break
      sleep 1
    done
    _pid_alive "$ppid" && kill -9 "$ppid" 2>/dev/null || true
  fi

  if [ -n "$lpid" ] && _pid_alive "$lpid"; then
    log_info "Stopping local dashboard (pid: $lpid)..."
    kill "$lpid" 2>/dev/null || true
    for _i in 1 2 3 4 5; do
      _pid_alive "$lpid" || break
      sleep 1
    done
    _pid_alive "$lpid" && kill -9 "$lpid" 2>/dev/null || true
  fi

  _write_state "stopped" \
    "$(_read_state_field local port)" \
    "" \
    "$(_read_state_field local health_url)" \
    "$(_read_state_field local started_at)" \
    "stopped" "" "" \
    "$(_read_state_field public log_file)" \
    "$(_read_state_field public started_at)"

  log_ok "dashboard-service: stopped"
  return 0
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "$SUBCOMMAND" in
  start-local)   cmd_start_local ;;
  start-public)  cmd_start_public ;;
  status)        cmd_status ;;
  guide-public)  cmd_guide_public ;;
  stop)          cmd_stop ;;
  *)
    log_err "Unknown subcommand: $SUBCOMMAND"
    exec "$0"
    ;;
esac

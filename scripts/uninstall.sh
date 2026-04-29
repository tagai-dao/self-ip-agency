#!/usr/bin/env bash
# uninstall.sh — Self-IP Agency full teardown
#
# Usage:
#   bash scripts/uninstall.sh                  # print plan, prompt y/N, then execute
#   bash scripts/uninstall.sh --dry-run        # print plan only, no changes
#   bash scripts/uninstall.sh --yes            # skip confirmation prompt
#   bash scripts/uninstall.sh --keep-repo      # do everything except self-delete the repo
#   bash scripts/uninstall.sh --workspace PATH # override OPENCLAW_WORKSPACE
#
# Removes:
#   - This agent's agency cron jobs (<agent>-main-heartbeat, <agent>-bookmarker-cycle, <agent>-trader-cycle, <agent>-x-sync-cycle)
#   - Running dashboard server + cloudflared tunnel
#   - Running claw-wallet sandbox (iff $WALLET_DIR/.env.clay exists)
#   - Every file install.sh deploys into $OPENCLAW_WORKSPACE
#   - $WORKSPACE/skills/tagclaw/ and $WORKSPACE/skills/tagclaw-wallet/
#   - $WORKSPACE/runtime/, memory/, wiki/, schema/, tools/self-ip-dashboard/
#   - Agency log files under $WORKSPACE/logs/
#   - Repo install-time artifacts (.installed, .cache, rendered agent .md)
#   - The agency repo itself (unless --keep-repo)
#
# Design notes:
#   - Uses an explicit file allow-list for $WORKSPACE/{scripts,agents,config}/
#     rather than blanket rm -rf, to respect other skills sharing the workspace.
#   - Each destructive action is wrapped with || true so a single failure
#     never aborts the teardown.
#   - Self-delete detaches a /tmp helper script that rm -rf's the repo after
#     the current process exits — you cannot reliably rm the directory you're
#     running from.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENCY_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# ── Flags ────────────────────────────────────────────────────────────────────

DRY_RUN=false
ASSUME_YES=false
KEEP_REPO=false
WORKSPACE_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "${1:-}" in
    --dry-run) DRY_RUN=true; shift ;;
    --yes|-y) ASSUME_YES=true; shift ;;
    --keep-repo) KEEP_REPO=true; shift ;;
    --workspace=*) WORKSPACE_OVERRIDE="${1#--workspace=}"; shift ;;
    --workspace) WORKSPACE_OVERRIDE="${2:-}"; shift 2 ;;
    --force) ASSUME_YES=true; shift ;;  # legacy alias
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *) log_warn "Unknown argument: $1"; shift ;;
  esac
done

# ── Resolve paths ────────────────────────────────────────────────────────────

if [ -n "$WORKSPACE_OVERRIDE" ]; then
  WORKSPACE="$WORKSPACE_OVERRIDE"
else
  WORKSPACE="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
fi
REPO_DIR="$AGENCY_DIR"

resolve_cron_agent_slug() {
  python3 - <<'PY' "$WORKSPACE" "$AGENCY_DIR"
import json, pathlib, re, sys
workspace = pathlib.Path(sys.argv[1])
agency = pathlib.Path(sys.argv[2])

def env_value(path, key):
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() != key:
            continue
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        return v
    return ""

def json_username(path):
    try:
        data = json.load(open(path))
        return ((data.get("agent") or {}).get("username") or "").strip()
    except Exception:
        return ""

raw = (
    env_value(workspace / "skills" / "tagclaw" / ".env", "TAGCLAW_AGENT_USERNAME")
    or json_username(workspace / "config" / "agency-identity.json")
    or json_username(agency / "config" / "agency-identity.json")
    or workspace.name
)
slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw.strip().lstrip("@")).strip("-_").lower()
print((slug or "selfip")[:32])
PY
}

# Wallet dir probe: prefer workspace-installed copy, fall back to parallel repo
WALLET_DIR=""
for candidate in \
  "$WORKSPACE/skills/tagclaw-wallet" \
  "$(dirname "$REPO_DIR")/tagclaw-wallet"
do
  if [ -d "$candidate" ]; then
    WALLET_DIR="$candidate"
    break
  fi
done

# ── Explicit deploy manifest (mirrors install.sh) ────────────────────────────

DEPLOYED_SHELL_SCRIPTS=(
  main-heartbeat.sh
  bookmarker-cycle.sh
  trader-cycle.sh
  x-sync-cycle.sh
  tagclaw-onboard.sh
  refresh-agency-identity.sh
)

DEPLOYED_PY_SCRIPTS=(
  run_bookmarker_runtime_v1.py
  run_trader_runtime_v1.py
  runtime_utils_v2.py
  build_main_input_packet.py
  run_main_runtime_v2.py
  compute_tas_social_v2.py
  select_strategy_v1.py
  sync_guided_x_tweets.py
  build_x_tweets_wiki_v1.py
  wiki_lint.py
  wiki_utils.py
  wiki_registry.py
  wiki_search.py
  verify_wiki_contract.py
  select_strategy.py
  strategy_experiment.py
  record_strategy_cycle.py
)

WORKSPACE_ROOT_FILES=(
  "$WORKSPACE/.agency-installed"
  "$WORKSPACE/.agency-meta.json"
  "$WORKSPACE/HEARTBEAT.md"
  "$WORKSPACE/tagclaw-verification-tweet.txt"
)

WORKSPACE_CONFIG_FILES=(
  "$WORKSPACE/config/agency.config.yaml"
  "$WORKSPACE/config/agency-identity.json"
  "$WORKSPACE/config/wiki_topic_registry.json"
  "$WORKSPACE/config/cron-jobs.json"
  "$WORKSPACE/config/openclaw-agents.yaml"
)

WORKSPACE_AGENT_FILES=(
  "$WORKSPACE/agents/main.md"
  "$WORKSPACE/agents/bookmarker.md"
  "$WORKSPACE/agents/trader.md"
  "$WORKSPACE/agents/main.md.tmpl"
  "$WORKSPACE/agents/bookmarker.md.tmpl"
  "$WORKSPACE/agents/trader.md.tmpl"
)

WORKSPACE_TREES=(
  "$WORKSPACE/skills/tagclaw"
  "$WORKSPACE/skills/tagclaw-wallet"
  "$WORKSPACE/runtime/main"
  "$WORKSPACE/runtime/bookmarker"
  "$WORKSPACE/runtime/trader"
  "$WORKSPACE/runtime/shared"
  "$WORKSPACE/memory"
  "$WORKSPACE/wiki"
  "$WORKSPACE/schema"
  "$WORKSPACE/tools/self-ip-dashboard"
)

WORKSPACE_LOG_FILES=(
  "$WORKSPACE/logs/dashboard.log"
  "$WORKSPACE/logs/dashboard-tunnel.log"
)

WORKSPACE_RMDIR_IF_EMPTY=(
  "$WORKSPACE/scripts/lib"
  "$WORKSPACE/scripts"
  "$WORKSPACE/agents"
  "$WORKSPACE/config"
  "$WORKSPACE/skills"
  "$WORKSPACE/runtime"
  "$WORKSPACE/tools"
  "$WORKSPACE/logs"
)

REPO_ARTIFACTS=(
  "$REPO_DIR/.installed"
  "$REPO_DIR/.install-next-steps.json"
  "$REPO_DIR/.install-next-steps.md"
  "$REPO_DIR/.cache"
  "$REPO_DIR/agents/main.md"
  "$REPO_DIR/agents/bookmarker.md"
  "$REPO_DIR/agents/trader.md"
)

# ── Counters ─────────────────────────────────────────────────────────────────

STOPPED=0
REMOVED=0
SKIPPED=0
WARNED=0

bump_warn() { WARNED=$((WARNED + 1)); }

# ── Action helpers ───────────────────────────────────────────────────────────

plan_line() {
  echo "    $*"
}

do_rm_path() {
  local path="$1"
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi
  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY] rm -rf $path"
    return 0
  fi
  if rm -rf -- "$path" 2>/dev/null; then
    log_ok "Removed: $path"
    REMOVED=$((REMOVED + 1))
  else
    log_warn "Failed to remove: $path"
    bump_warn
  fi
}

do_rmdir_if_empty() {
  local path="$1"
  if [ ! -d "$path" ]; then
    return 0
  fi
  if [ -n "$(ls -A "$path" 2>/dev/null)" ]; then
    return 0
  fi
  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY] rmdir $path  (empty)"
    return 0
  fi
  if rmdir "$path" 2>/dev/null; then
    log_ok "Removed empty dir: $path"
    REMOVED=$((REMOVED + 1))
  fi
}

# Kill by PID list (space-separated), best-effort.
kill_pids() {
  local label="$1"; shift
  local pids="$*"
  [ -z "$pids" ] && return 0
  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY] kill $label PIDs: $pids"
    return 0
  fi
  local pid killed=0
  for pid in $pids; do
    if kill "$pid" 2>/dev/null; then
      killed=$((killed + 1))
    fi
  done
  # Give them a moment, then SIGKILL any survivors
  sleep 1
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  if [ "$killed" -gt 0 ]; then
    log_ok "Stopped $label (PIDs: $pids)"
    STOPPED=$((STOPPED + killed))
  fi
}

# ── Stage 1: Stop processes ──────────────────────────────────────────────────

stop_dashboard() {
  log_info "Stage 1a: Stopping dashboard + cloudflared tunnel"

  # Prefer the canonical lifecycle script, wherever it lives. It handles
  # both local dashboard and public cloudflared tunnel + cleans its state file.
  local svc=""
  for candidate in "$WORKSPACE/scripts/dashboard-service.sh" "$REPO_DIR/scripts/dashboard-service.sh"; do
    if [ -x "$candidate" ]; then svc="$candidate"; break; fi
  done

  if [ -n "$svc" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      log_info "[DRY] bash $svc stop --workspace $WORKSPACE"
    else
      if bash "$svc" stop --workspace "$WORKSPACE" >/dev/null 2>&1; then
        log_ok "dashboard-service.sh stop succeeded"
        STOPPED=$((STOPPED + 1))
      else
        log_warn "dashboard-service.sh stop exited non-zero — will also try pgrep fallback"
        bump_warn
      fi
    fi
  else
    log_info "dashboard-service.sh not found — using pgrep fallback"
  fi

  # Fallback / belt-and-braces: kill stragglers
  local srv_pids tun_pids state_file state_port port_pids filtered_port_pids pid cmd
  state_file="$WORKSPACE/runtime/shared/dashboard-service.json"
  state_port=""
  if [ -f "$state_file" ]; then
    state_port="$(python3 - "$state_file" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    port = ((data.get("local") or {}).get("port"))
    print("" if port is None else str(port))
except Exception:
    print("")
PY
)"
  fi

  srv_pids="$(pgrep -f "self-ip-dashboard/server.py" 2>/dev/null | tr '\n' ' ' || true)"
  if [ -z "$srv_pids" ]; then
    srv_pids="$(pgrep -f "server.py.*7890" 2>/dev/null | tr '\n' ' ' || true)"
  fi
  if [ -z "$srv_pids" ]; then
    srv_pids="$(pgrep -f "$REPO_DIR/dashboard/server.py" 2>/dev/null | tr '\n' ' ' || true)"
  fi
  if [ -z "$srv_pids" ]; then
    srv_pids="$(pgrep -f "uvicorn.*server:app" 2>/dev/null | tr '\n' ' ' || true)"
  fi

  if command -v lsof >/dev/null 2>&1; then
    local probe_port="${state_port:-7890}"
    if [ -n "$probe_port" ] && [[ "$probe_port" =~ ^[0-9]+$ ]]; then
      port_pids="$(lsof -tiTCP:"$probe_port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
      filtered_port_pids=""
      for pid in $port_pids; do
        cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
        case "$cmd" in
          *self-ip-dashboard*|*dashboard/server.py*|*python3\ server.py*|*uvicorn*server:app*)
            filtered_port_pids="$filtered_port_pids $pid"
            ;;
        esac
      done
      srv_pids="$srv_pids $filtered_port_pids"
    fi
  fi
  kill_pids "dashboard server" $srv_pids

  tun_pids="$(pgrep -f "cloudflared.*--url.*127.0.0.1" 2>/dev/null | tr '\n' ' ' || true)"
  if [ -z "$tun_pids" ]; then
    tun_pids="$(pgrep -f "cloudflared.*tunnel" 2>/dev/null | tr '\n' ' ' || true)"
  fi
  kill_pids "cloudflared tunnel" $tun_pids

  if command -v lsof >/dev/null 2>&1; then
    local final_probe_port="${state_port:-7890}"
    if [ -n "$final_probe_port" ] && [[ "$final_probe_port" =~ ^[0-9]+$ ]]; then
      local survivors=""
      survivors="$(lsof -tiTCP:"$final_probe_port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
      if [ -n "$survivors" ]; then
        log_warn "A process is still listening on dashboard port $final_probe_port (PIDs: $survivors)"
        bump_warn
      fi
    fi
  fi
}

stop_claw_sandbox() {
  log_info "Stage 1b: Stopping claw-wallet sandbox"

  if [ -z "$WALLET_DIR" ]; then
    log_info "No tagclaw-wallet directory found — skipping sandbox shutdown"
    return 0
  fi

  local env_clay="$WALLET_DIR/.env.clay"
  if [ ! -f "$env_clay" ]; then
    log_info "No $env_clay — sandbox never ran, skipping"
    return 0
  fi

  # Preferred path: the wallet's own stop command (reads PID file, calls
  # clay-sandbox stop, cleans up).
  local claw_script="$WALLET_DIR/claw-wallet.sh"
  if [ -x "$claw_script" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      log_info "[DRY] bash $claw_script stop"
    else
      if bash "$claw_script" stop >/dev/null 2>&1; then
        log_ok "claw-wallet.sh stop succeeded"
        STOPPED=$((STOPPED + 1))
      else
        log_warn "claw-wallet.sh stop exited non-zero — falling back to port-based kill"
        bump_warn
      fi
    fi
  fi

  # Fallback: parse LISTEN_ADDR from .env.clay, kill whatever is holding that port.
  local listen_addr port
  listen_addr="$(grep -E '^LISTEN_ADDR=' "$env_clay" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
  port="${listen_addr##*:}"
  if [ -n "$port" ] && [[ "$port" =~ ^[0-9]+$ ]]; then
    local port_pids=""
    if command -v lsof >/dev/null 2>&1; then
      port_pids="$(lsof -ti:"$port" 2>/dev/null | tr '\n' ' ' || true)"
    fi
    # Also target the clay-sandbox binary by name
    local bin_pids
    bin_pids="$(pgrep -f "clay-sandbox" 2>/dev/null | tr '\n' ' ' || true)"
    kill_pids "clay-sandbox (port $port / binary)" $port_pids $bin_pids
  fi

  # Clean up the PID file if leftover
  if [ -f "$WALLET_DIR/sandbox.pid" ] && [ "$DRY_RUN" != "true" ]; then
    rm -f "$WALLET_DIR/sandbox.pid" || true
  fi
}

# ── Stage 2: Cron ────────────────────────────────────────────────────────────

remove_crons() {
  log_info "Stage 2: Unregistering cron jobs"

  local cron_agent_slug
  cron_agent_slug="$(resolve_cron_agent_slug)"
  local jobs=("${cron_agent_slug}-main-heartbeat" "${cron_agent_slug}-bookmarker-cycle" "${cron_agent_slug}-trader-cycle" "${cron_agent_slug}-x-sync-cycle")
  local cron_list=""

  if ! command -v openclaw >/dev/null 2>&1; then
    log_warn "openclaw CLI not found — inspect cron IDs manually after uninstall:"
    for j in "${jobs[@]}"; do
      echo "    openclaw cron list    # find cron ID for $j"
      echo "    openclaw cron remove <cron-id>"
    done
    bump_warn
    return 0
  fi

  cron_list="$(openclaw cron list 2>/dev/null || true)"
  if [ -z "$cron_list" ]; then
    log_warn "Unable to read 'openclaw cron list' — cron IDs must be removed manually"
    for j in "${jobs[@]}"; do
      echo "    openclaw cron list    # find cron ID for $j"
      echo "    openclaw cron remove <cron-id>"
    done
    bump_warn
    return 0
  fi

  for j in "${jobs[@]}"; do
    local script_name expected_path cron_ids cron_id found_any=false
    case "$j" in
      *-main-heartbeat)
        script_name="main-heartbeat.sh"
        expected_path="$WORKSPACE/scripts/main-heartbeat.sh"
        ;;
      *-bookmarker-cycle)
        script_name="bookmarker-cycle.sh"
        expected_path="$WORKSPACE/scripts/bookmarker-cycle.sh"
        ;;
      *-trader-cycle)
        script_name="trader-cycle.sh"
        expected_path="$WORKSPACE/scripts/trader-cycle.sh"
        ;;
      *-x-sync-cycle)
        script_name="x-sync-cycle.sh"
        expected_path="$WORKSPACE/scripts/x-sync-cycle.sh"
        ;;
      *)
        script_name=""
        expected_path=""
        ;;
    esac

    cron_ids="$(CRON_LIST="$cron_list" CRON_JOB_NAME="$j" CRON_SCRIPT_NAME="$script_name" EXPECTED_PATH="$expected_path" python3 - <<'PY'
import os
import re

job = os.environ.get("CRON_JOB_NAME", "").strip()
script = os.environ.get("CRON_SCRIPT_NAME", "").strip()
expected_path = os.environ.get("EXPECTED_PATH", "").strip()
text = os.environ.get("CRON_LIST", "")
uuid_re = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
line_re = re.compile(r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+(\S+)\b", re.I)

lines = text.splitlines()
blocks = []
current = []
for line in lines:
    if line.strip():
        current.append(line)
    elif current:
        blocks.append("\n".join(current))
        current = []
if current:
    blocks.append("\n".join(current))
if not blocks:
    blocks = lines

matches = []
for block in blocks:
    hay = block.lower()
    path_hit = expected_path and expected_path.lower() in hay
    script_hit = script and script.lower() in hay
    job_hit = job.lower() in hay
    if not path_hit:
        continue
    if not (script_hit or job_hit):
        continue
    for cron_id in uuid_re.findall(block):
        if cron_id not in matches:
            matches.append(cron_id)

if not matches:
    for line in lines:
        hay = line.lower()
        path_hit = expected_path and expected_path.lower() in hay
        script_hit = script and script.lower() in hay
        job_hit = job.lower() in hay
        if path_hit and (script_hit or job_hit):
            for cron_id in uuid_re.findall(line):
                if cron_id not in matches:
                    matches.append(cron_id)

if not matches:
    name_matches = []
    for line in lines:
        m = line_re.match(line)
        if not m:
            continue
        cron_id, name = m.group(1), m.group(2)
        if name == job and cron_id not in name_matches:
            name_matches.append(cron_id)
    # `openclaw cron list` may omit message/script paths. Fall back to exact
    # unique name matches only; ambiguous duplicates are intentionally skipped.
    if len(name_matches) == 1:
        matches = name_matches

print("\n".join(matches))
PY
)"

    if [ -z "$cron_ids" ]; then
      log_warn "Could not resolve cron ID for $j from 'openclaw cron list'"
      echo "    openclaw cron list    # locate $j"
      echo "    openclaw cron remove <cron-id>"
      bump_warn
      continue
    fi

    if [ "$DRY_RUN" = "true" ]; then
      while IFS= read -r cron_id; do
        [ -n "$cron_id" ] || continue
        log_info "[DRY] openclaw cron remove $cron_id  # $j"
      done <<< "$cron_ids"
      continue
    fi

    while IFS= read -r cron_id; do
      [ -n "$cron_id" ] || continue
      if openclaw cron remove "$cron_id" 2>/dev/null; then
        log_ok "Removed cron: $j ($cron_id)"
        REMOVED=$((REMOVED + 1))
        found_any=true
      else
        log_warn "Failed to remove cron: $j ($cron_id)"
        bump_warn
      fi
    done <<< "$cron_ids"

    if [ "$found_any" != "true" ]; then
      log_info "Cron not removed: $j"
    fi
  done
}

# ── Stage 3: Workspace state ─────────────────────────────────────────────────

remove_workspace_state() {
  log_info "Stage 3: Removing workspace state at $WORKSPACE"

  if [ ! -d "$WORKSPACE" ]; then
    log_info "Workspace directory not found — skipping"
    return 0
  fi

  # 3a. Deployed shell + python scripts
  for s in "${DEPLOYED_SHELL_SCRIPTS[@]}"; do
    do_rm_path "$WORKSPACE/scripts/$s"
  done
  for s in "${DEPLOYED_PY_SCRIPTS[@]}"; do
    do_rm_path "$WORKSPACE/scripts/$s"
  done
  do_rm_path "$WORKSPACE/scripts/lib/common.sh"
  do_rm_path "$WORKSPACE/scripts/lib/x_fetch_utils.py"

  # 3b. Workspace root markers / metadata
  for f in "${WORKSPACE_ROOT_FILES[@]}"; do
    do_rm_path "$f"
  done

  # 3c. Deployed config
  for f in "${WORKSPACE_CONFIG_FILES[@]}"; do
    do_rm_path "$f"
  done

  # 3d. Deployed agent behavior files
  for f in "${WORKSPACE_AGENT_FILES[@]}"; do
    do_rm_path "$f"
  done

  # 3e. Big subtrees (skills/tagclaw, skills/tagclaw-wallet, runtime/*, memory, wiki, schema, tools/self-ip-dashboard)
  for t in "${WORKSPACE_TREES[@]}"; do
    do_rm_path "$t"
  done

  # 3f. Individual log files (preserves logs/ itself for other tools)
  for f in "${WORKSPACE_LOG_FILES[@]}"; do
    do_rm_path "$f"
  done

  # 3g. Prune now-empty parent dirs (never force-remove — respects other skills)
  for d in "${WORKSPACE_RMDIR_IF_EMPTY[@]}"; do
    do_rmdir_if_empty "$d"
  done
}

# ── Stage 4: Repo install-time artifacts ─────────────────────────────────────

remove_repo_artifacts() {
  log_info "Stage 4: Removing repo install-time artifacts"
  for f in "${REPO_ARTIFACTS[@]}"; do
    do_rm_path "$f"
  done
}

# ── Stage 5: Self-delete repo ────────────────────────────────────────────────

self_delete_repo() {
  if [ "$KEEP_REPO" = "true" ]; then
    log_info "Stage 5: --keep-repo → repo tree preserved at $REPO_DIR"
    log_info "Note: gittracked files were modified by install.sh (config/cron-jobs.json,"
    log_info "      config/openclaw-agents.yaml, dashboard/static/index.html, agents/*.md.tmpl)."
    log_info "      Run: (cd $REPO_DIR && git checkout -- .) to revert."
    return 0
  fi

  log_info "Stage 6: Removing repo tree at $REPO_DIR (detached, runs after this script exits)"

  if [ "$DRY_RUN" = "true" ]; then
    log_info "[DRY] Would detach a /tmp helper to rm -rf $REPO_DIR after this process exits"
    return 0
  fi

  local cleanup_script
  cleanup_script="$(mktemp /tmp/self-ip-uninstall-cleanup-XXXXXX.sh)"
  cat >"$cleanup_script" <<EOF
#!/usr/bin/env bash
sleep 1
rm -rf -- "$REPO_DIR"
rm -f -- "$cleanup_script"
EOF
  chmod +x "$cleanup_script"
  (cd /tmp && nohup bash "$cleanup_script" >/dev/null 2>&1 &)
  disown 2>/dev/null || true

  log_ok "Detached cleanup helper: $cleanup_script"
  log_info "Repo will be removed momentarily: $REPO_DIR"
  REMOVED=$((REMOVED + 1))
}

# ── Plan preview ─────────────────────────────────────────────────────────────

print_plan() {
  echo ""
  echo "  ╔══════════════════════════════════════════════════════════╗"
  echo "  ║  Self-IP Agency Uninstall Plan"
  echo "  ╠══════════════════════════════════════════════════════════╣"
  echo "  ║  Repo:      $REPO_DIR"
  echo "  ║  Workspace: $WORKSPACE"
  echo "  ║  Wallet:    ${WALLET_DIR:-<none>}"
  echo "  ║  Keep repo: $KEEP_REPO"
  echo "  ║  Dry run:   $DRY_RUN"
  echo "  ╠══════════════════════════════════════════════════════════╣"
  echo "  ║  1. Stop: dashboard server, cloudflared tunnel,"
  echo "  ║          claw-wallet sandbox (if .env.clay present)"
  echo "  ║  2. Unregister this agent's prefixed OpenClaw crons"
  echo "  ║  3. Remove workspace state:"
  echo "  ║       - deployed scripts (${#DEPLOYED_SHELL_SCRIPTS[@]} shell, ${#DEPLOYED_PY_SCRIPTS[@]} python + lib/{common.sh,x_fetch_utils.py})"
  echo "  ║       - markers/meta (.agency-installed, .agency-meta.json,"
  echo "  ║         HEARTBEAT.md, tagclaw-verification-tweet.txt)"
  echo "  ║       - config/{agency.config.yaml, agency-identity.json,"
  echo "  ║         wiki_topic_registry.json, cron-jobs.json, openclaw-agents.yaml}"
  echo "  ║       - agents/{main,bookmarker,trader}.{md,md.tmpl}"
  echo "  ║       - skills/tagclaw/, skills/tagclaw-wallet/"
  echo "  ║       - runtime/{main,bookmarker,trader,shared}/"
  echo "  ║       - memory/, wiki/, schema/, tools/self-ip-dashboard/"
  echo "  ║       - logs/{dashboard.log, dashboard-tunnel.log}"
  echo "  ║       - prune now-empty parent dirs"
  echo "  ║  4. Remove repo install artifacts (.installed, .cache, rendered .md)"
  if [ "$KEEP_REPO" = "true" ]; then
    echo "  ║  5. [skipped: --keep-repo]"
  else
    echo "  ║  5. Self-delete repo tree ($REPO_DIR) via detached helper"
  fi
  echo "  ╚══════════════════════════════════════════════════════════╝"
  echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
  print_plan

  if [ "$DRY_RUN" = "true" ]; then
    log_info "Dry run — no changes will be made"
    # Still walk the stages so the log shows which files exist
    stop_dashboard
    stop_claw_sandbox
    remove_crons
    remove_workspace_state
    remove_user_config
    remove_repo_artifacts
    self_delete_repo
    echo ""
    log_ok "Dry run complete. Skipped=$SKIPPED (not present). Re-run without --dry-run to apply."
    return 0
  fi

  if [ "$ASSUME_YES" != "true" ]; then
    echo -n "  Proceed with uninstall? Type 'yes' to confirm: "
    local answer
    read -r answer
    if [ "$answer" != "yes" ]; then
      log_info "Aborted by user."
      return 1
    fi
  fi

  stop_dashboard
  stop_claw_sandbox
  remove_crons
  remove_workspace_state
  remove_repo_artifacts

  echo ""
  echo "  ╔══════════════════════════════════════════════════════════╗"
  echo "  ║  Uninstall Summary"
  echo "  ╠══════════════════════════════════════════════════════════╣"
  printf "  ║  Processes stopped: %d\n" "$STOPPED"
  printf "  ║  Paths removed:     %d\n" "$REMOVED"
  printf "  ║  Not present:       %d\n" "$SKIPPED"
  printf "  ║  Warnings:          %d\n" "$WARNED"
  echo "  ╚══════════════════════════════════════════════════════════╝"
  echo ""

  # Self-delete goes last so the summary is visible before the tree disappears.
  self_delete_repo

  log_ok "Uninstall complete."
  return 0
}

main "$@"

#!/usr/bin/env bash
# x-sync-cycle.sh — periodic owner X sync + wiki compile
#
# Purpose:
#   1. bootstrap owner tweets/replies into raw/x-tweets on first run
#   2. perform incremental syncs on later runs based on newest synced tweet
#   3. compile new raw items into wiki/synthesis/tweets when new data arrives

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
resolve_agency_paths "$SCRIPT_DIR"

SYNC_SCRIPT="$SCRIPT_DIR/sync_guided_x_tweets.py"
COMPILE_SCRIPT="$SCRIPT_DIR/build_x_tweets_wiki_v1.py"
RAW_TWEETS_DIR="$WORKSPACE/raw/x-tweets/tweets"

log_info "Running owner X sync cycle..."

if [ ! -f "$SYNC_SCRIPT" ]; then
  log_err "sync_guided_x_tweets.py not found at $SYNC_SCRIPT"
  exit 1
fi

if [ ! -f "$COMPILE_SCRIPT" ]; then
  log_warn "build_x_tweets_wiki_v1.py not found at $COMPILE_SCRIPT — wiki compile will be skipped"
fi

_window_json="$(WORKSPACE="$WORKSPACE" python3 - <<'PY'
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

workspace = Path(os.environ["WORKSPACE"])
raw_dir = workspace / "raw/x-tweets/tweets"
latest_dt = None

def parse_ts(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

if raw_dir.exists():
    for path in raw_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("created_at", "fetched_at"):
            dt = parse_ts(data.get(key))
            if dt and (latest_dt is None or dt > latest_dt):
                latest_dt = dt

if latest_dt is None:
    out = {
        "mode": "bootstrap",
        "lookback_days": 3,
        "latest_synced_at": None,
    }
else:
    delta_days = max(0.0, (datetime.now(timezone.utc) - latest_dt).total_seconds() / 86400.0)
    lookback_days = min(30, max(1, math.ceil(delta_days) + 1))
    out = {
        "mode": "incremental",
        "lookback_days": lookback_days,
        "latest_synced_at": latest_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

print(json.dumps(out))
PY
)"

SYNC_MODE="$(python3 - <<'PY' "$_window_json"
import json, sys
print(json.loads(sys.argv[1]).get("mode", "bootstrap"))
PY
)"
LOOKBACK_DAYS="$(python3 - <<'PY' "$_window_json"
import json, sys
print(int(json.loads(sys.argv[1]).get("lookback_days", 3)))
PY
)"
LATEST_SYNCED_AT="$(python3 - <<'PY' "$_window_json"
import json, sys
v = json.loads(sys.argv[1]).get("latest_synced_at")
print("" if v is None else v)
PY
)"

if [ "$SYNC_MODE" = "bootstrap" ]; then
  log_info "X sync mode: bootstrap (lookback=${LOOKBACK_DAYS}d)"
else
  log_info "X sync mode: incremental (lookback=${LOOKBACK_DAYS}d, latest=${LATEST_SYNCED_AT:-unknown})"
fi

_sync_tmp="$(mktemp)"
_sync_rc=0
if ! python3 "$SYNC_SCRIPT" \
  --workspace "$WORKSPACE" \
  --lookback-days "$LOOKBACK_DAYS" \
  --include-replies \
  --json >"$_sync_tmp"; then
  _sync_rc=$?
fi

SYNC_STATUS="$(python3 - <<'PY' "$_sync_tmp"
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
print(data.get("status", "failed"))
PY
)"
ITEMS_WRITTEN="$(python3 - <<'PY' "$_sync_tmp"
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
print(int(data.get("items_written", 0)))
PY
)"
ITEMS_SKIPPED="$(python3 - <<'PY' "$_sync_tmp"
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
print(int(data.get("items_skipped_existing", 0)))
PY
)"
ITEMS_FAILED="$(python3 - <<'PY' "$_sync_tmp"
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
print(int(data.get("items_failed_fetch", 0)))
PY
)"
ITEMS_FOUND="$(python3 - <<'PY' "$_sync_tmp"
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
print(int(data.get("tweet_urls_found", 0)))
PY
)"

log_info "guided-x-sync: status=${SYNC_STATUS}, found=${ITEMS_FOUND}, written=${ITEMS_WRITTEN}, skipped=${ITEMS_SKIPPED}, failed=${ITEMS_FAILED}"

if [ "$_sync_rc" -ne 0 ] && [ "$SYNC_STATUS" = "failed" -o "$SYNC_STATUS" = "blocked" ]; then
  log_warn "guided-x-sync returned rc=${_sync_rc}; preserving JSON output for diagnosis:"
  cat "$_sync_tmp" >&2 || true
  rm -f "$_sync_tmp"
  exit "$_sync_rc"
fi

if [ "$ITEMS_WRITTEN" -gt 0 ] && [ -f "$COMPILE_SCRIPT" ]; then
  log_info "New X items detected — compiling wiki synthesis..."
  _compile_tmp="$(mktemp)"
  if python3 "$COMPILE_SCRIPT" --workspace "$WORKSPACE" --json >"$_compile_tmp"; then
    COMPILE_STATUS="$(python3 - <<'PY' "$_compile_tmp"
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
print(data.get("status", "failed"))
PY
)"
    COMPILED_ITEMS="$(python3 - <<'PY' "$_compile_tmp"
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
print(int(data.get("compiled_items", 0)))
PY
)"
    log_ok "build-x-tweets-wiki: status=${COMPILE_STATUS}, compiled=${COMPILED_ITEMS}"
  else
    log_warn "build_x_tweets_wiki_v1.py failed (non-fatal)"
    cat "$_compile_tmp" >&2 || true
  fi
  rm -f "$_compile_tmp"
else
  log_info "No new X items written — skipping wiki compile"
fi

rm -f "$_sync_tmp"

case "$SYNC_STATUS" in
  ok|partial|deferred) exit 0 ;;
  *) exit 1 ;;
esac

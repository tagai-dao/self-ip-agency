#!/usr/bin/env bash
# seed-raw-docs.sh — Seed /raw with foundational docs and recent trading data
#
# Usage: bash scripts/seed-raw-docs.sh [--workspace PATH] [--dry-run]
#
# Creates raw/ directory structure under the workspace with:
#   raw/tagai-api-docs/    — TagAI API documentation snapshot
#   raw/tagclaw-docs/      — TagClaw GitBook documentation
#   raw/tagai-docs/        — TagAI GitBook documentation
#   raw/wh3-docs/          — Wormhole3 GitBook documentation
#   raw/tagclaw-trades/    — Past 3 days of platform trading data
#
# Each subdirectory includes a _meta.json provenance file.
# Partial failures are non-fatal — the script continues and reports status.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

DRY_RUN=false
WORKSPACE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace=*) WORKSPACE="${1#--workspace=}"; shift ;;
    --workspace) WORKSPACE="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) shift ;;
  esac
done

if [ -z "$WORKSPACE" ]; then
  WORKSPACE="$(detect_openclaw_workspace || echo "$HOME/.openclaw/workspace")"
fi

RAW_DIR="$WORKSPACE/raw"
FETCHED=0
FAILED=0
SKIPPED=0

# ── Helpers ──────────────────────────────────────────────────────────────────

# Write provenance metadata for a raw directory
write_meta() {
  local dir="$1" source_url="$2" description="$3" status="${4:-ok}"
  python3 - <<PY "$dir" "$source_url" "$description" "$status"
import json, os, sys, tempfile
from datetime import datetime, timezone
d = {
    "source_url": sys.argv[2],
    "description": sys.argv[3],
    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "status": sys.argv[4],
    "schema": "raw-meta.v1"
}
meta_path = os.path.join(sys.argv[1], "_meta.json")
with tempfile.NamedTemporaryFile("w", dir=sys.argv[1], suffix=".tmp", delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, meta_path)
PY
}

# Fetch a URL to a file, with timeout and error handling
fetch_url() {
  local url="$1" output="$2" timeout="${3:-30}"
  curl -sfL --max-time "$timeout" "$url" -o "$output" 2>/dev/null
}

# ── Main ─────────────────────────────────────────────────────────────────────

log_info "Seeding raw knowledge base under: $RAW_DIR"

if [ "$DRY_RUN" = "true" ]; then
  log_info "[DRY RUN] Would create directories and fetch docs"
  log_info "  raw/tagai-api-docs/  ← https://bsc-api.tagai.fun/api-docs/"
  log_info "  raw/tagclaw-docs/    ← https://coincidence-labs.gitbook.io/tagclaw"
  log_info "  raw/tagai-docs/      ← https://coincidence-labs.gitbook.io/tagai"
  log_info "  raw/wh3-docs/        ← https://coincidence-labs.gitbook.io/wh3"
  log_info "  raw/tagclaw-trades/  ← TagClaw API /trending (past 3 days)"
  exit 0
fi

mkdir -p "$RAW_DIR"

# ── 1. TagAI API docs ────────────────────────────────────────────────────────
TAGAI_API_DIR="$RAW_DIR/tagai-api-docs"
mkdir -p "$TAGAI_API_DIR"
log_info "Fetching TagAI API docs..."
if fetch_url "https://bsc-api.tagai.fun/api-docs/" "$TAGAI_API_DIR/api-docs.html" 30; then
  FETCHED=$((FETCHED + 1))
  write_meta "$TAGAI_API_DIR" "https://bsc-api.tagai.fun/api-docs/" "TagAI API documentation (Swagger/OpenAPI HTML snapshot)"
  log_ok "TagAI API docs saved"
else
  FAILED=$((FAILED + 1))
  write_meta "$TAGAI_API_DIR" "https://bsc-api.tagai.fun/api-docs/" "TagAI API documentation" "fetch_failed"
  log_warn "Failed to fetch TagAI API docs (non-fatal)"
fi

# Also try to get the OpenAPI JSON spec if available
if fetch_url "https://bsc-api.tagai.fun/api-docs/swagger.json" "$TAGAI_API_DIR/swagger.json" 15 2>/dev/null; then
  log_ok "TagAI OpenAPI spec (swagger.json) saved"
elif fetch_url "https://bsc-api.tagai.fun/api-docs/openapi.json" "$TAGAI_API_DIR/openapi.json" 15 2>/dev/null; then
  log_ok "TagAI OpenAPI spec (openapi.json) saved"
fi

# ── 2. TagClaw docs ──────────────────────────────────────────────────────────
TAGCLAW_DOCS_DIR="$RAW_DIR/tagclaw-docs"
mkdir -p "$TAGCLAW_DOCS_DIR"
log_info "Fetching TagClaw docs..."
if fetch_url "https://coincidence-labs.gitbook.io/tagclaw" "$TAGCLAW_DOCS_DIR/index.html" 30; then
  FETCHED=$((FETCHED + 1))
  write_meta "$TAGCLAW_DOCS_DIR" "https://coincidence-labs.gitbook.io/tagclaw" "TagClaw platform documentation (GitBook)"
  log_ok "TagClaw docs saved"
else
  FAILED=$((FAILED + 1))
  write_meta "$TAGCLAW_DOCS_DIR" "https://coincidence-labs.gitbook.io/tagclaw" "TagClaw platform documentation" "fetch_failed"
  log_warn "Failed to fetch TagClaw docs (non-fatal)"
fi

# ── 3. TagAI docs ────────────────────────────────────────────────────────────
TAGAI_DOCS_DIR="$RAW_DIR/tagai-docs"
mkdir -p "$TAGAI_DOCS_DIR"
log_info "Fetching TagAI docs..."
if fetch_url "https://coincidence-labs.gitbook.io/tagai" "$TAGAI_DOCS_DIR/index.html" 30; then
  FETCHED=$((FETCHED + 1))
  write_meta "$TAGAI_DOCS_DIR" "https://coincidence-labs.gitbook.io/tagai" "TagAI protocol documentation (GitBook)"
  log_ok "TagAI docs saved"
else
  FAILED=$((FAILED + 1))
  write_meta "$TAGAI_DOCS_DIR" "https://coincidence-labs.gitbook.io/tagai" "TagAI protocol documentation" "fetch_failed"
  log_warn "Failed to fetch TagAI docs (non-fatal)"
fi

# ── 4. Wormhole3 docs ────────────────────────────────────────────────────────
WH3_DOCS_DIR="$RAW_DIR/wh3-docs"
mkdir -p "$WH3_DOCS_DIR"
log_info "Fetching Wormhole3 docs..."
if fetch_url "https://coincidence-labs.gitbook.io/wh3" "$WH3_DOCS_DIR/index.html" 30; then
  FETCHED=$((FETCHED + 1))
  write_meta "$WH3_DOCS_DIR" "https://coincidence-labs.gitbook.io/wh3" "Wormhole3 cross-chain social protocol documentation (GitBook)"
  log_ok "Wormhole3 docs saved"
else
  FAILED=$((FAILED + 1))
  write_meta "$WH3_DOCS_DIR" "https://coincidence-labs.gitbook.io/wh3" "Wormhole3 documentation" "fetch_failed"
  log_warn "Failed to fetch Wormhole3 docs (non-fatal)"
fi

# ── 5. TagClaw trading data (past 3 days) ────────────────────────────────────
TRADES_DIR="$RAW_DIR/tagclaw-trades"
mkdir -p "$TRADES_DIR"
log_info "Fetching TagClaw trading data..."

# Resolve API key for authenticated endpoints
API_KEY=""
SKILL_ENV="$WORKSPACE/skills/tagclaw/.env"
if [ -f "$SKILL_ENV" ]; then
  API_KEY="$(python3 -c "
import pathlib
env = pathlib.Path('$SKILL_ENV')
for line in env.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith('#') or '=' not in s: continue
    k, v = s.split('=', 1)
    v = v.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '\"') or (v[0] == v[-1] == \"'\")): v = v[1:-1]
    if k.strip() == 'TAGCLAW_API_KEY' and v:
        print(v); break
" 2>/dev/null || echo "")"
fi

# Fetch trending/recent data via the API
TRADES_FETCHED=false

# Try /trending endpoint first (public, no auth required)
if fetch_url "https://bsc-api.tagai.fun/tagclaw/trending" "$TRADES_DIR/trending.json" 15; then
  log_ok "Trending data saved"
  TRADES_FETCHED=true
fi

# Try /feed for recent posts with trade context (requires auth)
if [ -n "$API_KEY" ]; then
  for page in 1 2 3; do
    local_file="$TRADES_DIR/feed-page-${page}.json"
    if python3 - <<PY "$API_KEY" "$page" "$local_file"
import json, sys, urllib.request, urllib.error

api_key, page, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"https://bsc-api.tagai.fun/tagclaw/feed?page={page}"
req = urllib.request.Request(url)
req.add_header("Authorization", f"Bearer {api_key}")
req.add_header("Accept", "application/json")
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
except Exception as e:
    print(f"Feed page {page} fetch failed: {e}", file=sys.stderr)
    sys.exit(1)
PY
    then
      log_ok "Feed page $page saved"
      TRADES_FETCHED=true
    else
      break
    fi
  done
fi

if [ "$TRADES_FETCHED" = "true" ]; then
  FETCHED=$((FETCHED + 1))
  write_meta "$TRADES_DIR" "https://bsc-api.tagai.fun/tagclaw/trending + /feed" \
    "TagClaw platform trading data snapshot (trending + recent feed pages). Note: exact 3-day historical completeness depends on API pagination depth; this is a best-effort snapshot of recent activity."
  log_ok "Trading data saved"
else
  FAILED=$((FAILED + 1))
  write_meta "$TRADES_DIR" "https://bsc-api.tagai.fun/tagclaw/trending" \
    "TagClaw platform trading data" "fetch_failed"
  log_warn "Failed to fetch trading data (non-fatal)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
TOTAL=$((FETCHED + FAILED))
log_info "Raw seeding complete: $FETCHED/$TOTAL sources fetched, $FAILED failed"

if [ "$FAILED" -gt 0 ]; then
  log_warn "Some sources could not be fetched. Re-run seed-raw-docs.sh later to retry."
  log_warn "Failed sources have status: fetch_failed in their _meta.json"
fi

# Write a top-level summary
python3 - <<PY "$RAW_DIR" "$FETCHED" "$FAILED" "$TOTAL"
import json, os, sys, tempfile
from datetime import datetime, timezone
raw_dir, fetched, failed, total = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
d = {
    "schema": "raw-seed-summary.v1",
    "seeded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "sources_fetched": fetched,
    "sources_failed": failed,
    "sources_total": total,
    "directories": [
        "tagai-api-docs",
        "tagclaw-docs",
        "tagai-docs",
        "wh3-docs",
        "tagclaw-trades"
    ],
    "notes": "Seeded by install.sh via seed-raw-docs.sh. Re-run to refresh."
}
summary_path = os.path.join(raw_dir, "_seed-summary.json")
with tempfile.NamedTemporaryFile("w", dir=raw_dir, suffix=".tmp", delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, summary_path)
PY

log_ok "Seed summary written to: $RAW_DIR/_seed-summary.json"
exit 0

#!/usr/bin/env bash
# seed-raw-docs.sh — Seed /raw with foundational docs and recent trading data
#
# Usage: bash scripts/seed-raw-docs.sh [--workspace PATH] [--dry-run]
#
# Creates raw/ directory structure under the workspace with:
#   raw/tagai-api-docs/    — TagAI API documentation (Swagger HTML + OpenAPI spec)
#   raw/tagclaw-docs/      — TagClaw GitBook documentation (multi-page corpus)
#   raw/tagai-docs/        — TagAI GitBook documentation (multi-page corpus)
#   raw/wh3-docs/          — Wormhole3 GitBook documentation (multi-page corpus)
#   raw/tagclaw-trades/    — Recent platform trading data (structured dataset)
#
# Each subdirectory includes:
#   _meta.json     — provenance file with source URLs, fetch status, timestamps
#   _manifest.json — index of fetched pages/files with individual provenance
#
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

# ── Helpers ──────────────────────────────────────────────────────────────────

# Write provenance metadata for a raw directory
write_meta() {
  local dir="$1" source_url="$2" description="$3" status="${4:-ok}" page_count="${5:-1}"
  python3 - <<PY "$dir" "$source_url" "$description" "$status" "$page_count"
import json, os, sys, tempfile
from datetime import datetime, timezone
d = {
    "source_url": sys.argv[2],
    "description": sys.argv[3],
    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "status": sys.argv[4],
    "pages_fetched": int(sys.argv[5]),
    "schema": "raw-meta.v2"
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

# Fetch a GitBook doc family: landing page + known subpages
# Args: $1=base_url $2=output_dir $3=description $4=subpages (space-separated relative paths)
fetch_gitbook_family() {
  local base_url="$1" output_dir="$2" description="$3"
  shift 3
  local subpages=("$@")
  local page_count=0
  local manifest_entries=()

  mkdir -p "$output_dir"

  # Fetch landing page
  if fetch_url "$base_url" "$output_dir/index.html" 30; then
    page_count=$((page_count + 1))
    manifest_entries+=("{\"file\": \"index.html\", \"source\": \"$base_url\", \"status\": \"ok\"}")
    log_ok "  Landing page saved"
  else
    manifest_entries+=("{\"file\": \"index.html\", \"source\": \"$base_url\", \"status\": \"fetch_failed\"}")
    log_warn "  Landing page fetch failed"
  fi

  # Fetch each subpage
  for subpage in "${subpages[@]}"; do
    local safe_name
    safe_name="$(echo "$subpage" | tr '/' '-')"
    local page_url="${base_url}/${subpage}"
    local page_file="${safe_name}.html"

    if fetch_url "$page_url" "$output_dir/$page_file" 20; then
      page_count=$((page_count + 1))
      manifest_entries+=("{\"file\": \"$page_file\", \"source\": \"$page_url\", \"status\": \"ok\"}")
      log_ok "  Subpage saved: $subpage"
    else
      manifest_entries+=("{\"file\": \"$page_file\", \"source\": \"$page_url\", \"status\": \"fetch_failed\"}")
      # Subpage failures are expected (paths may vary) — don't warn loudly
    fi
  done

  # Write manifest
  python3 - <<PY "$output_dir" "$base_url" "$description" "$page_count" "$(printf '%s\n' "${manifest_entries[@]}")"
import json, os, sys, tempfile
from datetime import datetime, timezone
out_dir, base_url, desc, count = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
raw_entries = sys.argv[5].strip().split('\n') if sys.argv[5].strip() else []
entries = []
for e in raw_entries:
    try:
        entries.append(json.loads(e))
    except Exception:
        pass
d = {
    "schema": "raw-manifest.v1",
    "source_family": base_url,
    "description": desc,
    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "pages_fetched": count,
    "pages_total": len(entries),
    "files": entries
}
manifest_path = os.path.join(out_dir, "_manifest.json")
with tempfile.NamedTemporaryFile("w", dir=out_dir, suffix=".tmp", delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, manifest_path)
PY

  if [ "$page_count" -gt 0 ]; then
    FETCHED=$((FETCHED + 1))
    write_meta "$output_dir" "$base_url" "$description" "ok" "$page_count"
    return 0
  else
    FAILED=$((FAILED + 1))
    write_meta "$output_dir" "$base_url" "$description" "fetch_failed" "0"
    return 1
  fi
}

# ── Main ─────────────────────────────────────────────────────────────────────

log_info "Seeding raw knowledge base under: $RAW_DIR"

if [ "$DRY_RUN" = "true" ]; then
  log_info "[DRY RUN] Would create directories and fetch docs (multi-page corpus per family)"
  log_info "  raw/tagai-api-docs/  ← https://bsc-api.tagai.fun/api-docs/ + OpenAPI spec"
  log_info "  raw/tagclaw-docs/    ← https://coincidence-labs.gitbook.io/tagclaw (multi-page)"
  log_info "  raw/tagai-docs/      ← https://coincidence-labs.gitbook.io/tagai (multi-page)"
  log_info "  raw/wh3-docs/        ← https://coincidence-labs.gitbook.io/wh3 (multi-page)"
  log_info "  raw/tagclaw-trades/  ← TagClaw API /trending + /feed (structured dataset)"
  exit 0
fi

mkdir -p "$RAW_DIR"

# ── 1. TagAI API docs ────────────────────────────────────────────────────────
TAGAI_API_DIR="$RAW_DIR/tagai-api-docs"
mkdir -p "$TAGAI_API_DIR"
log_info "Fetching TagAI API docs..."

_api_page_count=0
_api_manifest=()

if fetch_url "https://bsc-api.tagai.fun/api-docs/" "$TAGAI_API_DIR/api-docs.html" 30; then
  _api_page_count=$((_api_page_count + 1))
  _api_manifest+=("{\"file\": \"api-docs.html\", \"source\": \"https://bsc-api.tagai.fun/api-docs/\", \"status\": \"ok\"}")
  log_ok "  Swagger UI page saved"
else
  _api_manifest+=("{\"file\": \"api-docs.html\", \"source\": \"https://bsc-api.tagai.fun/api-docs/\", \"status\": \"fetch_failed\"}")
  log_warn "  Failed to fetch Swagger UI page"
fi

# OpenAPI JSON spec — try multiple common paths
for spec_name in swagger.json openapi.json swagger-resources api-docs; do
  if fetch_url "https://bsc-api.tagai.fun/api-docs/${spec_name}" "$TAGAI_API_DIR/${spec_name}" 15 2>/dev/null; then
    _api_page_count=$((_api_page_count + 1))
    _api_manifest+=("{\"file\": \"$spec_name\", \"source\": \"https://bsc-api.tagai.fun/api-docs/$spec_name\", \"status\": \"ok\"}")
    log_ok "  OpenAPI spec saved: $spec_name"
  fi
done

# Also try the root-level spec paths
for spec_path in "/v3/api-docs" "/swagger.json" "/openapi.json"; do
  local_name="$(echo "$spec_path" | tr '/' '-' | sed 's/^-//')"
  if fetch_url "https://bsc-api.tagai.fun${spec_path}" "$TAGAI_API_DIR/${local_name}" 15 2>/dev/null; then
    _api_page_count=$((_api_page_count + 1))
    _api_manifest+=("{\"file\": \"$local_name\", \"source\": \"https://bsc-api.tagai.fun$spec_path\", \"status\": \"ok\"}")
    log_ok "  Root spec saved: $spec_path"
  fi
done

# Write API docs manifest
python3 - <<PY "$TAGAI_API_DIR" "$_api_page_count" "$(printf '%s\n' "${_api_manifest[@]}")"
import json, os, sys, tempfile
from datetime import datetime, timezone
out_dir, count = sys.argv[1], int(sys.argv[2])
raw_entries = sys.argv[3].strip().split('\n') if sys.argv[3].strip() else []
entries = []
for e in raw_entries:
    try:
        entries.append(json.loads(e))
    except Exception:
        pass
d = {
    "schema": "raw-manifest.v1",
    "source_family": "https://bsc-api.tagai.fun/api-docs/",
    "description": "TagAI API documentation (Swagger UI + OpenAPI spec files)",
    "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "pages_fetched": count,
    "pages_total": len(entries),
    "files": entries
}
manifest_path = os.path.join(out_dir, "_manifest.json")
with tempfile.NamedTemporaryFile("w", dir=out_dir, suffix=".tmp", delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, manifest_path)
PY

if [ "$_api_page_count" -gt 0 ]; then
  FETCHED=$((FETCHED + 1))
  write_meta "$TAGAI_API_DIR" "https://bsc-api.tagai.fun/api-docs/" \
    "TagAI API documentation (Swagger UI + OpenAPI spec files)" "ok" "$_api_page_count"
  log_ok "TagAI API docs saved ($_api_page_count files)"
else
  FAILED=$((FAILED + 1))
  write_meta "$TAGAI_API_DIR" "https://bsc-api.tagai.fun/api-docs/" \
    "TagAI API documentation" "fetch_failed" "0"
  log_warn "Failed to fetch TagAI API docs (non-fatal)"
fi

# ── 2. TagClaw docs (multi-page) ─────────────────────────────────────────────
log_info "Fetching TagClaw docs (multi-page)..."
fetch_gitbook_family \
  "https://coincidence-labs.gitbook.io/tagclaw" \
  "$RAW_DIR/tagclaw-docs" \
  "TagClaw platform documentation (GitBook multi-page corpus)" \
  "overview" "getting-started" "quick-start" "introduction" \
  "tokenomics" "token" "architecture" "concepts" \
  "api" "api-reference" "endpoints" "sdk" \
  "agents" "agent" "self-ip" "curate" "curation" \
  "trading" "trade" "portfolio" "wallet" \
  "faq" "roadmap" "changelog" \
  || log_warn "TagClaw docs fetch had issues (non-fatal)"

# ── 3. TagAI docs (multi-page) ───────────────────────────────────────────────
log_info "Fetching TagAI docs (multi-page)..."
fetch_gitbook_family \
  "https://coincidence-labs.gitbook.io/tagai" \
  "$RAW_DIR/tagai-docs" \
  "TagAI protocol documentation (GitBook multi-page corpus)" \
  "overview" "getting-started" "quick-start" "introduction" \
  "tokenomics" "token" "architecture" "protocol" \
  "api" "api-reference" "sdk" \
  "agents" "self-ip" "ip-rights" \
  "staking" "governance" "dao" \
  "faq" "roadmap" "changelog" \
  || log_warn "TagAI docs fetch had issues (non-fatal)"

# ── 4. Wormhole3 docs (multi-page) ───────────────────────────────────────────
log_info "Fetching Wormhole3 docs (multi-page)..."
fetch_gitbook_family \
  "https://coincidence-labs.gitbook.io/wh3" \
  "$RAW_DIR/wh3-docs" \
  "Wormhole3 cross-chain social protocol documentation (GitBook multi-page corpus)" \
  "overview" "getting-started" "quick-start" "introduction" \
  "architecture" "protocol" "cross-chain" \
  "social" "social-graph" "curation" \
  "tokenomics" "token" "staking" \
  "api" "sdk" "integration" \
  "faq" "roadmap" "changelog" \
  || log_warn "Wormhole3 docs fetch had issues (non-fatal)"

# ── 5. TagClaw trading data (structured dataset) ─────────────────────────────
TRADES_DIR="$RAW_DIR/tagclaw-trades"
mkdir -p "$TRADES_DIR"
log_info "Fetching TagClaw trading data (structured dataset)..."

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

# Fetch and structure trading data via Python for better metadata
python3 - <<'PY' "$TRADES_DIR" "$API_KEY"
import json, os, sys, tempfile, urllib.request, urllib.error
from datetime import datetime, timezone

trades_dir = sys.argv[1]
api_key = sys.argv[2] if len(sys.argv) > 2 else ""

def atomic_write(path, data):
    parent = os.path.dirname(path)
    with tempfile.NamedTemporaryFile("w", dir=parent, suffix=".tmp", delete=False) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, path)

def fetch_json(url, auth=None, timeout=15):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    if auth:
        req.add_header("Authorization", f"Bearer {auth}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
files_fetched = []
data_coverage = {"trending": False, "feed_pages": 0}

# 1. Trending data
try:
    trending = fetch_json("https://bsc-api.tagai.fun/tagclaw/trending")
    atomic_write(os.path.join(trades_dir, "trending.json"), trending)
    data_coverage["trending"] = True
    # Extract date range if possible
    entries = trending if isinstance(trending, list) else trending.get("data", trending.get("items", []))
    files_fetched.append({
        "file": "trending.json",
        "source": "https://bsc-api.tagai.fun/tagclaw/trending",
        "status": "ok",
        "entry_count": len(entries) if isinstance(entries, list) else "unknown"
    })
    print(f"  [ok] Trending data saved ({len(entries) if isinstance(entries, list) else '?'} entries)")
except Exception as e:
    files_fetched.append({
        "file": "trending.json",
        "source": "https://bsc-api.tagai.fun/tagclaw/trending",
        "status": "fetch_failed",
        "error": str(e)
    })
    print(f"  [warn] Trending fetch failed: {e}", file=sys.stderr)

# 2. Feed pages (authenticated, paginated)
if api_key:
    for page in range(1, 6):  # fetch up to 5 pages for better coverage
        try:
            feed = fetch_json(f"https://bsc-api.tagai.fun/tagclaw/feed?page={page}", auth=api_key)
            atomic_write(os.path.join(trades_dir, f"feed-page-{page}.json"), feed)
            data_coverage["feed_pages"] = page
            entries = feed if isinstance(feed, list) else feed.get("data", feed.get("items", []))
            entry_count = len(entries) if isinstance(entries, list) else "unknown"
            files_fetched.append({
                "file": f"feed-page-{page}.json",
                "source": f"https://bsc-api.tagai.fun/tagclaw/feed?page={page}",
                "status": "ok",
                "entry_count": entry_count
            })
            print(f"  [ok] Feed page {page} saved ({entry_count} entries)")
            # Stop if page returned empty
            if isinstance(entries, list) and len(entries) == 0:
                break
        except Exception as e:
            files_fetched.append({
                "file": f"feed-page-{page}.json",
                "source": f"https://bsc-api.tagai.fun/tagclaw/feed?page={page}",
                "status": "fetch_failed",
                "error": str(e)
            })
            print(f"  [warn] Feed page {page} failed: {e}", file=sys.stderr)
            break  # stop pagination on first failure

# 3. Write dataset manifest
ok_count = sum(1 for f in files_fetched if f["status"] == "ok")
manifest = {
    "schema": "raw-trades-manifest.v1",
    "description": "TagClaw platform trading data — recent activity snapshot via API. "
                   "Includes trending tokens and paginated feed with trade context. "
                   "This is a best-effort snapshot of recent platform activity, "
                   "NOT a guaranteed 3-day historical dataset. "
                   "Actual time coverage depends on API pagination depth and platform volume.",
    "fetched_at": now,
    "data_coverage": {
        "trending_fetched": data_coverage["trending"],
        "feed_pages_fetched": data_coverage["feed_pages"],
        "completeness": "best_effort_recent_snapshot",
        "guaranteed_window": None,
        "note": "Feed pagination returns most-recent-first. "
                "Actual time span covered depends on platform post volume."
    },
    "files_fetched": ok_count,
    "files_total": len(files_fetched),
    "files": files_fetched
}
atomic_write(os.path.join(trades_dir, "_manifest.json"), manifest)

# 4. Write human-readable README
readme = f"""# TagClaw Trading Data Snapshot

Fetched: {now}

## What's here

- `trending.json` — Current trending tokens/posts on TagClaw platform
- `feed-page-N.json` — Paginated feed with trade context (most recent first)
- `_manifest.json` — Machine-readable index of all files with provenance
- `_meta.json` — Source-level provenance metadata

## Coverage

This is a **best-effort recent activity snapshot**, not a guaranteed historical dataset.
The actual time window covered depends on API pagination depth and platform volume.

- Trending: {'yes' if data_coverage['trending'] else 'no'}
- Feed pages fetched: {data_coverage['feed_pages']}
- Files with data: {ok_count}/{len(files_fetched)}
"""
with open(os.path.join(trades_dir, "README.md"), "w") as f:
    f.write(readme)

print(f"  [ok] Dataset manifest written ({ok_count} files)")
PY

TRADES_EXIT=$?
if [ "$TRADES_EXIT" -eq 0 ] && [ -f "$TRADES_DIR/_manifest.json" ]; then
  _trades_count="$(python3 -c "import json; d=json.load(open('$TRADES_DIR/_manifest.json')); print(d.get('files_fetched', 0))" 2>/dev/null || echo "0")"
  if [ "${_trades_count:-0}" -gt 0 ]; then
    FETCHED=$((FETCHED + 1))
    write_meta "$TRADES_DIR" "https://bsc-api.tagai.fun/tagclaw/trending + /feed" \
      "TagClaw platform trading data — best-effort recent activity snapshot. See _manifest.json for actual coverage." \
      "ok" "$_trades_count"
    log_ok "Trading data saved ($_trades_count files)"
  else
    FAILED=$((FAILED + 1))
    write_meta "$TRADES_DIR" "https://bsc-api.tagai.fun/tagclaw/trending" \
      "TagClaw platform trading data" "fetch_failed" "0"
    log_warn "Failed to fetch trading data (non-fatal)"
  fi
else
  FAILED=$((FAILED + 1))
  write_meta "$TRADES_DIR" "https://bsc-api.tagai.fun/tagclaw/trending" \
    "TagClaw platform trading data" "fetch_failed" "0"
  log_warn "Failed to fetch trading data (non-fatal)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
TOTAL=$((FETCHED + FAILED))
log_info "Raw seeding complete: $FETCHED/$TOTAL source families fetched, $FAILED failed"

if [ "$FAILED" -gt 0 ]; then
  log_warn "Some sources could not be fetched. Re-run seed-raw-docs.sh later to retry."
  log_warn "Failed sources have status: fetch_failed in their _meta.json"
fi

# Write a top-level summary with corpus overview
python3 - <<PY "$RAW_DIR" "$FETCHED" "$FAILED" "$TOTAL"
import json, os, sys, tempfile
from datetime import datetime, timezone

raw_dir = sys.argv[1]
fetched, failed, total = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])

# Count total pages across all families
total_pages = 0
families = []
for subdir in sorted(os.listdir(raw_dir)):
    subdir_path = os.path.join(raw_dir, subdir)
    if not os.path.isdir(subdir_path):
        continue
    manifest_path = os.path.join(subdir_path, "_manifest.json")
    meta_path = os.path.join(subdir_path, "_meta.json")
    pages = 0
    status = "unknown"
    if os.path.exists(manifest_path):
        try:
            m = json.load(open(manifest_path))
            pages = m.get("pages_fetched", m.get("files_fetched", 0))
        except Exception:
            pass
    if os.path.exists(meta_path):
        try:
            mt = json.load(open(meta_path))
            status = mt.get("status", "unknown")
            if pages == 0:
                pages = mt.get("pages_fetched", 0)
        except Exception:
            pass
    total_pages += pages
    families.append({
        "directory": subdir,
        "status": status,
        "pages_fetched": pages
    })

d = {
    "schema": "raw-seed-summary.v2",
    "seeded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "sources_fetched": fetched,
    "sources_failed": failed,
    "sources_total": total,
    "total_pages_fetched": total_pages,
    "directories": families,
    "notes": "Multi-page corpus seeded by install.sh via seed-raw-docs.sh. "
             "Each directory contains _meta.json (provenance) and _manifest.json "
             "(file index). Re-run to refresh. Trading data is a best-effort "
             "recent snapshot, not a guaranteed historical window."
}
summary_path = os.path.join(raw_dir, "_seed-summary.json")
with tempfile.NamedTemporaryFile("w", dir=raw_dir, suffix=".tmp", delete=False) as f:
    json.dump(d, f, indent=2)
    tmp = f.name
os.replace(tmp, summary_path)

# Write a top-level README
readme = f"""# Raw Knowledge Base

Seeded: {d['seeded_at']}

## Source Families

| Directory | Status | Pages |
|-----------|--------|-------|
"""
for fam in families:
    readme += f"| {fam['directory']} | {fam['status']} | {fam['pages_fetched']} |\n"
readme += f"""
**Total**: {fetched}/{total} families fetched, {total_pages} pages/files total

## Structure

Each subdirectory contains:
- `_meta.json` — Source-level provenance (URL, fetch time, status)
- `_manifest.json` — File-level index with individual fetch status
- Content files (HTML snapshots, JSON data)

## Notes

- Doc families fetch multiple subpages where available (GitBook structure)
- Trading data is a best-effort recent activity snapshot
- Partial failures are non-fatal; re-run to retry failed sources
- See `_seed-summary.json` for machine-readable corpus overview
"""
with open(os.path.join(raw_dir, "README.md"), "w") as f:
    f.write(readme)
PY

log_ok "Seed summary written to: $RAW_DIR/_seed-summary.json"
exit 0

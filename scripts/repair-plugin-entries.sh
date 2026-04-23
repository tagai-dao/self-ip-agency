#!/usr/bin/env bash
# repair-plugin-entries.sh — diagnose OpenClaw plugin entry mismatches that
# block cron registration with "plugin_config_mismatch" errors.
#
# Observed on clawdi installs: ~/.openclaw/openclaw.json has a plugins.entries
# hint (like `entry: "@clawdi/clawdi-plugin"`) that disagrees with the
# plugin's actual package.json `name` (`@clawdi/openclaw-plugin`). OpenClaw's
# scheduler then rejects every `openclaw cron add` with a mismatch error,
# even for cron jobs that have nothing to do with that plugin.
#
# This script:
#   1. Runs `openclaw plugins list --json` and `openclaw plugins doctor` to
#      get authoritative plugin state.
#   2. Reads `~/.openclaw/openclaw.json` (or the path you pass) to see what
#      plugins.entries declares.
#   3. Cross-references them and prints exact jq commands to fix each
#      mismatch — so the operator (or an agent like clawdi) can apply one
#      command and move on instead of hand-crafting edits.
#
# Intentionally print-only. It never writes anything. The jq commands are
# yours to review and run. Applying a mutation to openclaw.json is a
# one-line copy-paste; autoapplying from a script over a fragile third-
# party config has bitten us before.
#
# Usage:
#   bash scripts/repair-plugin-entries.sh
#   bash scripts/repair-plugin-entries.sh --openclaw-json ~/custom/openclaw.json
#   bash scripts/repair-plugin-entries.sh --json    # machine-readable output
#
# Exit codes:
#   0 — no mismatches found
#   2 — mismatches found (report printed; operator must apply suggested fix)
#   1 — prerequisite failure (openclaw CLI missing, openclaw.json unreadable)

set -euo pipefail

OPENCLAW_JSON="${HOME}/.openclaw/openclaw.json"
MACHINE_READABLE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --openclaw-json=*) OPENCLAW_JSON="${1#--openclaw-json=}"; shift ;;
    --openclaw-json) OPENCLAW_JSON="${2:-}"; shift 2 ;;
    --json) MACHINE_READABLE=1; shift ;;
    -h|--help)
      sed -n '2,35p' "$0"
      exit 0 ;;
    *) shift ;;
  esac
done

# ── Preconditions ────────────────────────────────────────────────────────────
if ! command -v openclaw >/dev/null 2>&1; then
  echo '{"status":"cli_missing","message":"openclaw CLI not in PATH"}' >&2
  exit 1
fi

if [ ! -f "$OPENCLAW_JSON" ]; then
  printf '{"status":"config_missing","path":"%s","message":"openclaw.json not found"}\n' "$OPENCLAW_JSON" >&2
  exit 1
fi

# Probe once. The CLI prints ANSI [plugins] log prefix lines before the JSON;
# strip to just the top-level object.
_PLUGIN_LIST_RAW="$(openclaw plugins list --json 2>/dev/null | sed -n '/^{/,/^}$/p' || true)"
if [ -z "$_PLUGIN_LIST_RAW" ]; then
  # Older CLIs may not support `plugins list --json`
  echo '{"status":"plugins_list_unsupported","message":"openclaw plugins list --json failed or not supported. Upgrade openclaw CLI."}' >&2
  exit 1
fi

_PLUGIN_DOCTOR_OUT="$(openclaw plugins doctor 2>&1 | sed 's/\x1b\[[0-9;]*m//g' || true)"

# ── Compute mismatches in a single python pass ──────────────────────────────
# Python does the json.dumps + set logic. Using a tmp python file keeps the
# heredoc out of $(...), which some bash versions choke on.
_ANALYZE_PY="$(mktemp -t repair-plugin-analyze.XXXXXX.py)"
trap 'rm -f "$_ANALYZE_PY"' EXIT

cat > "$_ANALYZE_PY" <<'PY'
import json, os, re, sys

oc_path = os.environ["OPENCLAW_JSON"]
with open(oc_path) as f:
    oc = json.load(f)
entries = ((oc.get("plugins") or {}).get("entries") or {})

try:
    plugins_data = json.loads(os.environ["PLUGIN_LIST"])
except Exception as e:
    print(json.dumps({"status": "parse_error", "message": f"could not parse plugins list JSON: {e}"}))
    sys.exit(1)
plugins_by_id = {p.get("id"): p for p in plugins_data.get("plugins", []) if p.get("id")}

doctor_output = os.environ.get("PLUGIN_DOCTOR", "") or ""

# Recognize "id-like" fields that OpenClaw might compare against manifest.
ID_LIKE = {"entry", "id", "name", "module", "package"}

mismatches = []
orphans = []

for key, cfg in entries.items():
    if not isinstance(cfg, dict):
        continue
    plugin = plugins_by_id.get(key)
    if plugin is None:
        orphans.append({
            "config_key": key,
            "config_value": cfg,
            "hint": (
                f'Plugin id "{key}" is declared in plugins.entries but '
                "is not loaded by the current openclaw CLI. It may be "
                "uninstalled, renamed, or pointing at a directory the "
                "CLI can't resolve. Check with: openclaw plugins list"
            ),
            "fix_commands": [
                f'jq \'del(.plugins.entries["{key}"])\' "{oc_path}" > /tmp/oc.json && mv /tmp/oc.json "{oc_path}"    # remove stale entry',
            ],
        })
        continue

    real_name = plugin.get("name")
    real_id = plugin.get("id")
    for field, value in cfg.items():
        if field not in ID_LIKE:
            continue
        if not isinstance(value, str):
            continue
        if value == real_id or value == real_name:
            continue
        mismatches.append({
            "config_key": key,
            "field": field,
            "config_value": value,
            "plugin_real_id": real_id,
            "plugin_real_name": real_name,
            "plugin_root_dir": plugin.get("rootDir"),
            "plugin_origin": plugin.get("origin"),
            "fix_commands": [
                f'jq \'.plugins.entries["{key}"].{field} = "{real_name or real_id}"\' "{oc_path}" > /tmp/oc.json && mv /tmp/oc.json "{oc_path}"    # align to manifest (recommended)',
                f'jq \'del(.plugins.entries["{key}"].{field})\' "{oc_path}" > /tmp/oc.json && mv /tmp/oc.json "{oc_path}"    # remove override',
            ],
            "next_steps": [
                "After applying one of the fix commands:",
                "  1. Restart OpenClaw (so the scheduler re-loads config)",
                "  2. Re-run: bash scripts/finalize-crons.sh",
            ],
        })

doctor_plugin_lines = [
    ln.strip() for ln in doctor_output.splitlines()
    if re.search(r"plugin", ln, re.IGNORECASE) and re.search(r"mismatch|not.*found|failed|error|not.*loaded|not.*in.*allow|disabled", ln, re.IGNORECASE)
]

# ── Reverse scan: plugins discovered on disk but not activated in config ───
# The mismatch/orphan loops above iterate openclaw.json entries. That misses
# the clawdi-observed case: a plugin physically installed (so it shows up in
# `openclaw plugins list --json`) with status != "loaded", but NO entry in
# plugins.entries and NOT in plugins.allow. The scheduler then rejects cron
# registrations with plugin_config_mismatch referencing that plugin.
#
# To avoid false-positives against the legitimately-disabled-bundled-plugins
# (on a fresh mac mini: 77 of 82 plugins are discovered-but-disabled by
# design), we only report a plugin as "unactivated" when BOTH:
#   1. Status is not "loaded"
#   2. `openclaw plugins doctor` specifically called it out
# That second condition is the trust signal — doctor is the CLI's own
# authority on what's problematic.
allow_list = set((oc.get("plugins") or {}).get("allow") or [])
unactivated = []

def _doctor_mentions(pid: str, name) -> bool:
    needles = [pid]
    if name:
        needles.append(name)
    for ln in doctor_plugin_lines:
        lnl = ln.lower()
        for n in needles:
            if n and n.lower() in lnl:
                return True
    return False

for plugin in plugins_data.get("plugins", []):
    pid = plugin.get("id")
    if not pid:
        continue
    status = plugin.get("status") or ""
    if status == "loaded":
        continue
    if pid in entries:
        # Has an entry already; covered by mismatch loop. Skip to avoid
        # double-reporting.
        continue
    if not _doctor_mentions(pid, plugin.get("name")):
        continue
    real_name = plugin.get("name") or pid
    # Single jq command to atomically whitelist + enable. Using `//` defaults
    # keeps the command idempotent even if plugins.allow or entries is absent.
    combined_fix = (
        f'jq \'.plugins.allow = ((.plugins.allow // []) + ["{pid}"] | unique) '
        f'| .plugins.entries = (.plugins.entries // {{}}) '
        f'| .plugins.entries["{pid}"] = ((.plugins.entries["{pid}"] // {{}}) | .enabled = true)\' '
        f'"{oc_path}" > /tmp/oc.json && mv /tmp/oc.json "{oc_path}"'
    )
    unactivated.append({
        "plugin_id": pid,
        "plugin_name": real_name,
        "status": status,
        "origin": plugin.get("origin"),
        "in_allow": pid in allow_list,
        "root_dir": plugin.get("rootDir"),
        "config_schema": plugin.get("configSchema"),
        "fix_command": combined_fix,
        "next_steps": [
            "After applying:",
            "  1. If the plugin needs config (workspaceDir, apiKey, etc.),",
            f"     run: openclaw plugins inspect {pid}",
            "     then add the required fields under",
            f"     plugins.entries[\"{pid}\"].config in openclaw.json",
            "  2. Restart OpenClaw.",
            "  3. Re-run: bash scripts/finalize-crons.sh",
        ],
    })

# ── Per-plugin internal manifest scan ──────────────────────────────────────
# Observed on clawdi install: `plugin id mismatch (manifest uses "clawdi-plugin",
# entry hints "openclaw-plugin")` persists even after fixing openclaw.json.
# Root cause: each plugin has an `openclaw.plugin.json` file in its rootDir
# with its own `id` field. When a plugin is renamed but openclaw.plugin.json.id
# isn't updated, OpenClaw warns. The fix belongs to the plugin's own files —
# not openclaw.json.
#
# We deliberately IGNORE `package.json#name` as a mismatch source. npm scoping
# (`@openclaw/acpx` → plugin id `acpx`) is a legitimate and widespread pattern;
# OpenClaw doesn't treat it as a mismatch. Flagging it produced false positives
# on bundled plugins (acpx, amazon-bedrock, etc.) on a known-clean Mac mini.
#
# We also gate on `openclaw plugins doctor` explicitly mentioning the plugin.
# If doctor is quiet about a plugin, any id difference we see is benign.
manifest_mismatches = []
for pid, plugin in plugins_by_id.items():
    root_dir = plugin.get("rootDir")
    if not root_dir:
        continue
    oc_plugin_path = os.path.join(root_dir, "openclaw.plugin.json")
    if not os.path.exists(oc_plugin_path):
        continue
    try:
        with open(oc_plugin_path) as f:
            oc_plugin = json.load(f)
    except Exception:
        continue
    oc_plugin_id = oc_plugin.get("id")
    if not oc_plugin_id or oc_plugin_id == pid:
        continue  # aligned — no mismatch

    # Only report if doctor flagged this plugin. Keeps us quiet on installs
    # where an id difference exists but OpenClaw doesn't consider it a bug.
    if not _doctor_mentions(pid, plugin.get("name")):
        continue

    manifest_mismatches.append({
        "plugin_id": pid,
        "plugin_name": plugin.get("name"),
        "root_dir": root_dir,
        "cli_resolved_id": pid,
        "openclaw_plugin_json_id": oc_plugin_id,
        "openclaw_plugin_json_path": oc_plugin_path,
        "fix_commands": [
            # Align the stale openclaw.plugin.json.id to the CLI-loaded pid
            f'jq \'.id = "{pid}"\' "{oc_plugin_path}" > /tmp/oc-plugin.json && '
            f'mv /tmp/oc-plugin.json "{oc_plugin_path}"    '
            f'# align openclaw.plugin.json.id ({oc_plugin_id!r} → {pid!r}) (recommended)',
        ],
        "next_steps": [
            "After applying:",
            "  1. Restart OpenClaw (so the scheduler re-reads the plugin manifest).",
            "  2. Run `openclaw plugins doctor` — the mismatch warning should be gone.",
            "  3. Re-run: bash scripts/finalize-crons.sh",
            "",
            "Why this can't be fixed from openclaw.json: the mismatch lives in the",
            "plugin's own openclaw.plugin.json file, NOT in your OpenClaw config.",
            "Adding/removing entries in openclaw.json won't silence this warning.",
        ],
    })

status = "ok"
if mismatches:
    status = "mismatches_found"
elif orphans:
    status = "orphans_found"
elif unactivated:
    status = "unactivated_plugins"
elif manifest_mismatches:
    status = "plugin_manifest_mismatch"
elif doctor_plugin_lines:
    status = "doctor_warnings"

print(json.dumps({
    "status": status,
    "openclaw_json": oc_path,
    "total_entries": len(entries),
    "loaded_plugins": len(plugins_by_id),
    "mismatches": mismatches,
    "orphans": orphans,
    "unactivated_plugins": unactivated,
    "plugin_manifest_mismatches": manifest_mismatches,
    "doctor_plugin_warnings": doctor_plugin_lines,
}, ensure_ascii=False, indent=2))
PY

_REPORT_JSON="$(OPENCLAW_JSON="$OPENCLAW_JSON" \
                PLUGIN_LIST="$_PLUGIN_LIST_RAW" \
                PLUGIN_DOCTOR="$_PLUGIN_DOCTOR_OUT" \
                python3 "$_ANALYZE_PY" 2>&1)"
_PY_RC=$?
if [ "$_PY_RC" -ne 0 ]; then
  echo "$_REPORT_JSON" >&2
  exit 1
fi

# ── Output ──────────────────────────────────────────────────────────────────
if [ "$MACHINE_READABLE" -eq 1 ]; then
  printf '%s\n' "$_REPORT_JSON"
  STATUS="$(printf '%s' "$_REPORT_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo "error")"
else
  # Pretty human report on stderr; still emit JSON on stdout for anyone
  # piping us. Pass the report via env var (not shell interpolation) to
  # avoid quote-escaping hell between bash/python.
  {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "OpenClaw plugin-entries diagnostic"
    echo "  openclaw.json: $OPENCLAW_JSON"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  } >&2

  REPORT_JSON="$_REPORT_JSON" python3 >&2 <<'PY'
import json, os
r = json.loads(os.environ["REPORT_JSON"])
if r["status"] == "ok":
    print(f"  ✓ No mismatches. {r['total_entries']} entries, {r['loaded_plugins']} plugins loaded.")
else:
    print(f"  ⚠ {r['status']}. {r['total_entries']} entries, {r['loaded_plugins']} plugins loaded.")
    print()

for m in r.get("mismatches", []):
    print(f'MISMATCH: plugins.entries["{m["config_key"]}"].{m["field"]}')
    print(f'    configured : {m["config_value"]!r}')
    print(f'    plugin id  : {m["plugin_real_id"]!r}')
    print(f'    plugin name: {m["plugin_real_name"]!r}')
    print(f'    root dir   : {m["plugin_root_dir"]}')
    print()
    print("  Fix (run one of the below, then restart openclaw):")
    for cmd in m["fix_commands"]:
        print(f"    $ {cmd}")
    print()

for o in r.get("orphans", []):
    print(f'ORPHAN:   plugins.entries["{o["config_key"]}"]')
    print(f'    hint: {o["hint"]}')
    print("  Fix:")
    for cmd in o["fix_commands"]:
        print(f"    $ {cmd}")
    print()

for u in r.get("unactivated_plugins", []):
    print(f'NEEDS ACTIVATION: {u["plugin_id"]}  (status={u["status"]}, origin={u.get("origin")})')
    print(f'    plugin name: {u["plugin_name"]!r}')
    print(f'    root dir   : {u.get("root_dir")}')
    print(f'    in allow   : {u["in_allow"]}')
    has_schema = u.get("config_schema")
    if has_schema:
        print(f'    config_schema: true — plugin has configurable options')
    print()
    print("  Fix (one jq command adds to plugins.allow AND enables the entry):")
    print(f'    $ {u["fix_command"]}')
    print()
    for ln in u.get("next_steps", []):
        print(f"    {ln}")
    print()

for m in r.get("plugin_manifest_mismatches", []):
    print(f'MANIFEST MISMATCH: {m["plugin_id"]}  (fix lives in the plugin dir, NOT in openclaw.json)')
    print(f'    root dir                 : {m["root_dir"]}')
    print(f'    CLI-resolved id          : {m["cli_resolved_id"]!r}')
    print(f'    openclaw.plugin.json id  : {m["openclaw_plugin_json_id"]!r}  (stale — causes the warning)')
    print(f'    file                     : {m["openclaw_plugin_json_path"]}')
    print()
    print("  Fix (one jq command, aligns the stale id to the CLI-resolved id):")
    for cmd in m["fix_commands"]:
        print(f"    $ {cmd}")
    print()
    for ln in m.get("next_steps", []):
        print(f"    {ln}")
    print()

if (r.get("doctor_plugin_warnings")
        and not r.get("mismatches")
        and not r.get("orphans")
        and not r.get("unactivated_plugins")
        and not r.get("plugin_manifest_mismatches")):
    # Only surface raw doctor lines when we couldn't auto-classify them.
    print("openclaw plugins doctor warnings (no matching plugin found in list — investigate manually):")
    for ln in r["doctor_plugin_warnings"][:10]:
        print(f"    {ln}")
PY

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2

  # JSON on stdout for programmatic consumers (e.g., finalize-crons.sh)
  printf '%s\n' "$_REPORT_JSON"
  STATUS="$(printf '%s' "$_REPORT_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo "error")"
fi

case "$STATUS" in
  ok) exit 0 ;;
  *)  exit 2 ;;
esac

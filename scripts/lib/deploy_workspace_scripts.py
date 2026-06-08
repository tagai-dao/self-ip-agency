#!/usr/bin/env python3
"""deploy_workspace_scripts.py — deploy the module scripts into the 3 agent workspaces.

Phase-1 re-baseline: the agency runs as three sibling OpenClaw workspaces (main /
bookmarker / trader). This reads config/workspace-scripts.json (the per-agent deploy
manifest) and copies each agent's script closure — plus the shared agency_paths.py and
shared libs — into that agent's workspace/scripts/ dir. Idempotent; supports --dry-run.

Workspace resolution (each env-overridable; siblings derived from the main workspace so
a custom layout stays co-located, matching scripts/agency_paths.py):
  main       = --main-workspace (required)
  bookmarker = --bookmarker-workspace | $OPENCLAW_BOOKMARKER_WORKSPACE | <parent>/workspace-bookmarker
  trader     = --trader-workspace     | $OPENCLAW_TRADER_WORKSPACE     | <parent>/workspace-trader

Called by install.sh::install_runtime. Standalone-runnable for testing:
  python3 scripts/lib/deploy_workspace_scripts.py --agency-dir . --main-workspace ~/.openclaw/workspace --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def _resolve(args) -> dict[str, Path]:
    main = Path(args.main_workspace).expanduser()
    parent = main.parent
    book = Path(args.bookmarker_workspace or os.environ.get("OPENCLAW_BOOKMARKER_WORKSPACE")
                or (parent / "workspace-bookmarker")).expanduser()
    trader = Path(args.trader_workspace or os.environ.get("OPENCLAW_TRADER_WORKSPACE")
                  or (parent / "workspace-trader")).expanduser()
    return {"main": main, "bookmarker": book, "trader": trader}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agency-dir", required=True)
    ap.add_argument("--main-workspace", required=True)
    ap.add_argument("--bookmarker-workspace", default="")
    ap.add_argument("--trader-workspace", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    agency = Path(args.agency_dir).expanduser().resolve()
    manifest_path = agency / "config" / "workspace-scripts.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shared = manifest.get("shared_to_all", [])
    workspaces = _resolve(args)

    total = 0
    for agent, ws in workspaces.items():
        files = list(shared) + list(manifest.get(agent, []))
        dst_scripts = ws / "scripts"
        print(f"[deploy] {agent}: {len(files)} scripts -> {dst_scripts}")
        for rel in files:
            src = agency / "scripts" / rel
            dst = dst_scripts / rel
            if not src.exists():
                print(f"  ! MISSING in repo: scripts/{rel}", file=sys.stderr)
                continue
            if args.dry_run:
                total += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            total += 1
        # runtime-template into each workspace (placeholder runtime dirs/files)
        rt = agency / "runtime-template"
        if rt.is_dir() and not args.dry_run:
            for item in rt.rglob("*"):
                if item.is_file():
                    target = ws / "runtime" / item.relative_to(rt)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():  # never clobber live runtime data
                        shutil.copy2(item, target)

    verb = "would deploy" if args.dry_run else "deployed"
    print(f"[deploy] {verb} {total} script copies across {len(workspaces)} workspaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())

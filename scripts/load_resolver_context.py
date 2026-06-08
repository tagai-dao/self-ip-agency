#!/usr/bin/env python3
"""Load resolver context for a given task from resolver-pack.json.

Phase 4 helper: makes the resolver-pack consumable by real workflows.

Given a task name (e.g. "lint-wiki"), this module:
  1. Loads the task definition from runtime/shared/resolver-pack.json
  2. Validates referenced paths exist
  3. Returns a structured ResolverContext with load paths, protected writes, and missing paths
  4. Exposes a write guard that rejects writes to protected paths

Usage (library):
    from load_resolver_context import load_context, is_write_allowed

    ctx = load_context("lint-wiki")
    print(ctx.task_name, ctx.load_paths, ctx.protected_writes)
    assert is_write_allowed(ctx, "wiki/concepts/DeSoc.md")   # True
    assert not is_write_allowed(ctx, "wiki/identity/persona.md")  # False

Usage (CLI):
    python3 scripts/load_resolver_context.py lint-wiki
    python3 scripts/load_resolver_context.py --list
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
RESOLVER_PACK_PATH = WORKSPACE / "runtime" / "shared" / "resolver-pack.json"


@dataclass
class ResolverContext:
    """Structured context for a resolver task."""

    task_name: str
    load_paths: list[str] = field(default_factory=list)
    protected_writes: list[str] = field(default_factory=list)
    global_protected_paths: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    pack_version: str = ""
    pack_generated_at: str = ""
    valid: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "load_paths": self.load_paths,
            "protected_writes": self.protected_writes,
            "global_protected_paths": self.global_protected_paths,
            "missing": self.missing,
            "pack_version": self.pack_version,
            "pack_generated_at": self.pack_generated_at,
            "valid": self.valid,
            "error": self.error,
        }


def _load_pack(pack_path: Path | None = None) -> dict | None:
    """Load resolver-pack.json, returning None on missing/invalid."""
    p = pack_path or RESOLVER_PACK_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_context(
    task_name: str,
    pack_path: Path | None = None,
    workspace: Path | None = None,
) -> ResolverContext:
    """Load resolver context for a task.

    Returns a valid ResolverContext even when the pack is missing or the task
    is not found — the caller can check ctx.valid and ctx.error to decide
    how to degrade.
    """
    ws = workspace or WORKSPACE
    pack = _load_pack(pack_path)

    if pack is None:
        return ResolverContext(
            task_name=task_name,
            valid=False,
            error="resolver-pack.json not found or invalid",
        )

    global_protected = pack.get("protected_paths", [])
    version = pack.get("version", "")
    generated_at = pack.get("generated_at", "")

    tasks = pack.get("tasks", {})
    if task_name not in tasks:
        return ResolverContext(
            task_name=task_name,
            global_protected_paths=global_protected,
            pack_version=version,
            pack_generated_at=generated_at,
            valid=False,
            error=f"task '{task_name}' not found in resolver-pack",
        )

    task_def = tasks[task_name]
    load_paths = task_def.get("load", [])
    protected_writes = task_def.get("protected_writes", [])
    declared_missing = task_def.get("missing", [])

    # Re-validate paths at load time (they may have appeared/disappeared since pack was built)
    actual_missing = []
    for p in load_paths:
        full = ws / p
        if not full.exists():
            actual_missing.append(p)

    return ResolverContext(
        task_name=task_name,
        load_paths=load_paths,
        protected_writes=protected_writes,
        global_protected_paths=global_protected,
        missing=actual_missing,
        pack_version=version,
        pack_generated_at=generated_at,
        valid=True,
    )


def is_write_allowed(ctx: ResolverContext, rel_path: str) -> bool:
    """Check if writing to rel_path is allowed given the resolver context.

    A write is blocked if rel_path starts with any protected_writes prefix
    or matches any global_protected_paths entry.
    """
    # Check global protected paths (exact match)
    for gp in ctx.global_protected_paths:
        if rel_path == gp or rel_path.startswith(gp.rstrip("/") + "/"):
            return False

    # Check task-level protected writes (prefix match for directories)
    for pw in ctx.protected_writes:
        if pw.endswith("/"):
            if rel_path.startswith(pw) or rel_path == pw.rstrip("/"):
                return False
        else:
            if rel_path == pw or rel_path.startswith(pw + "/"):
                return False

    return True


def list_tasks(pack_path: Path | None = None) -> list[str]:
    """List all available task names in the resolver pack."""
    pack = _load_pack(pack_path)
    if pack is None:
        return []
    return sorted(pack.get("tasks", {}).keys())


def main() -> int:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/load_resolver_context.py <task-name>")
        print("       python3 scripts/load_resolver_context.py --list")
        return 1

    arg = sys.argv[1]

    if arg == "--list":
        tasks = list_tasks()
        if not tasks:
            print("No tasks found (resolver-pack.json missing?)")
            return 1
        print(f"Available resolver tasks ({len(tasks)}):")
        for t in tasks:
            print(f"  - {t}")
        return 0

    ctx = load_context(arg)
    print(json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False))

    if not ctx.valid:
        print(f"\nWARNING: {ctx.error}", file=sys.stderr)
        return 1

    if ctx.missing:
        print(f"\nNOTE: {len(ctx.missing)} missing paths (gracefully degraded)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

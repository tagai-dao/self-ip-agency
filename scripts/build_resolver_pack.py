#!/usr/bin/env python3
"""Build resolver-pack.json from schema/resolver-map.yaml.

Reads the resolver map, validates referenced paths, and emits
runtime/shared/resolver-pack.json with path existence metadata.

Usage:
    python3 scripts/build_resolver_pack.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    # Inline minimal YAML parser for the subset we need
    yaml = None


WORKSPACE = Path(__file__).resolve().parent.parent
RESOLVER_MAP = WORKSPACE / "schema" / "resolver-map.yaml"
OUTPUT = WORKSPACE / "runtime" / "shared" / "resolver-pack.json"


def _parse_yaml_minimal(text: str) -> dict:
    """Minimal YAML parser for resolver-map.yaml structure.

    Handles the specific subset used: top-level keys, nested dicts,
    and lists with '- ' prefix. No anchors, no multi-line strings.
    """
    result: dict = {}
    stack: list[tuple[int, str, dict | list]] = []
    current_dict = result
    current_key = ""

    for raw_line in text.splitlines():
        # skip comments and blank lines
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # pop stack to find parent at correct indent
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if stack:
            parent_container = stack[-1][2]
        else:
            parent_container = result

        if stripped.startswith("- "):
            # list item
            value = stripped[2:].strip()
            if isinstance(parent_container, list):
                parent_container.append(value)
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if val:
                # simple key: value
                if isinstance(parent_container, dict):
                    parent_container[key] = val
            else:
                # key with nested content — peek ahead to determine list vs dict
                # We'll create a dict by default, convert to list if first child is '- '
                new_container: dict | list = {}
                if isinstance(parent_container, dict):
                    parent_container[key] = new_container
                stack.append((indent, key, new_container))
                current_key = key

    # Second pass: convert dicts that should be lists
    _convert_list_containers(result)
    return result


def _convert_list_containers(obj: dict) -> None:
    """Post-process: if a value is an empty dict but its YAML had '- ' items,
    it was already handled inline. This is a safety pass."""
    pass


def parse_resolver_map(path: Path) -> dict:
    """Parse resolver-map.yaml."""
    text = path.read_text(encoding="utf-8")

    if yaml is not None:
        return yaml.safe_load(text)

    return _parse_yaml_minimal(text)


def _parse_yaml_proper(text: str) -> dict:
    """Proper YAML parsing using line-by-line state machine."""
    lines = text.splitlines()
    result: dict = {}

    # Track hierarchy with (indent, key, container) tuples
    # We'll build the structure iteratively
    root = result
    path_stack: list[tuple[int, dict | list]] = [(-1, root)]

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        i += 1

        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # Pop stack to find correct parent
        while len(path_stack) > 1 and path_stack[-1][0] >= indent:
            path_stack.pop()

        parent_indent, parent = path_stack[-1]

        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if isinstance(parent, list):
                parent.append(value)
            elif isinstance(parent, dict):
                # Need to find the key this list belongs to
                # The parent dict's last key should point to this list
                pass
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if val:
                if isinstance(parent, dict):
                    parent[key] = val
            else:
                # Check if next non-empty line is a list item or dict key
                j = i
                while j < len(lines):
                    next_stripped = lines[j].strip()
                    if next_stripped and not next_stripped.startswith("#"):
                        break
                    j += 1

                if j < len(lines) and lines[j].strip().startswith("- "):
                    new_container: dict | list = []
                else:
                    new_container = {}

                if isinstance(parent, dict):
                    parent[key] = new_container
                path_stack.append((indent, new_container))

    return result


def parse_resolver_map_v2(path: Path) -> dict:
    """Parse resolver-map.yaml with proper list handling."""
    text = path.read_text(encoding="utf-8")

    if yaml is not None:
        return yaml.safe_load(text)

    return _parse_yaml_proper(text)


def validate_path(rel_path: str) -> dict:
    """Check if a workspace-relative path exists."""
    full = WORKSPACE / rel_path
    exists = full.exists()
    is_dir = full.is_dir() if exists else False
    return {
        "path": rel_path,
        "exists": exists,
        "is_directory": is_dir,
    }


def build_pack(resolver_map: dict) -> dict:
    """Build the resolver pack from parsed YAML."""
    version = resolver_map.get("version", "unknown")
    protected_paths_raw = resolver_map.get("protected_paths", [])
    tasks_raw = resolver_map.get("tasks", {})

    # Validate protected paths
    protected_paths = []
    for p in protected_paths_raw:
        info = validate_path(p)
        protected_paths.append(p)

    # Build task entries
    tasks = {}
    for task_name, task_def in tasks_raw.items():
        load_paths = task_def.get("load", [])
        protected_writes = task_def.get("protected_writes", [])

        validated_load = []
        missing = []
        for p in load_paths:
            info = validate_path(p)
            validated_load.append(p)
            if not info["exists"]:
                missing.append(p)

        tasks[task_name] = {
            "load": validated_load,
            "protected_writes": protected_writes,
            "missing": missing,
        }

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "schema": "self-ip-resolver-pack-v1",
        "generated_at": now,
        "version": version,
        "protected_paths": protected_paths,
        "tasks": tasks,
    }


def write_atomic(data: dict, path: Path) -> None:
    """Atomic write: tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False
    ) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        tmp = f.name
    os.replace(tmp, path)


def main() -> int:
    if not RESOLVER_MAP.exists():
        print(f"ERROR: resolver map not found: {RESOLVER_MAP}", file=sys.stderr)
        return 1

    resolver_map = parse_resolver_map_v2(RESOLVER_MAP)

    if not resolver_map:
        print("ERROR: failed to parse resolver-map.yaml", file=sys.stderr)
        return 1

    if "tasks" not in resolver_map:
        print("ERROR: resolver-map.yaml missing 'tasks' key", file=sys.stderr)
        return 1

    pack = build_pack(resolver_map)
    write_atomic(pack, OUTPUT)

    # Summary
    task_count = len(pack["tasks"])
    total_missing = sum(len(t["missing"]) for t in pack["tasks"].values())

    print(f"resolver-pack built: {task_count} tasks, {total_missing} missing paths")
    print(f"  output: {OUTPUT.relative_to(WORKSPACE)}")

    if total_missing > 0:
        print(f"  missing paths (gracefully degraded):")
        for task_name, task_data in pack["tasks"].items():
            for m in task_data["missing"]:
                print(f"    [{task_name}] {m}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

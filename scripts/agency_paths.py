#!/usr/bin/env python3
"""agency_paths.py — portable workspace path resolution for the 3-agent stack.

The agency runs as THREE sibling OpenClaw workspaces:

    <base>/workspace             (main agent — the default workspace)
    <base>/workspace-bookmarker  (bookmarker agent)
    <base>/workspace-trader      (trader agent)

Scripts must never hardcode an absolute machine path (that was the portability poison
that kept the installer pinned to one developer's Mac). Resolution order, each
overridable by the installer via env so non-standard layouts still work:

    OPENCLAW_WORKSPACE            -> WORKSPACE (own workspace; falls back to ~/.openclaw/workspace)
    OPENCLAW_BOOKMARKER_WORKSPACE -> BOOKMARKER_WS (falls back to sibling 'workspace-bookmarker')
    OPENCLAW_TRADER_WORKSPACE     -> TRADER_WS    (falls back to sibling 'workspace-trader')

Siblings are derived from WORKSPACE.parent, so a custom OPENCLAW_WORKSPACE keeps the
three workspaces co-located by convention. This module is deployed into every
workspace's scripts/ dir by install.sh, so `from agency_paths import WORKSPACE` works
in all three.
"""
from __future__ import annotations

import os
from pathlib import Path

# Base OpenClaw home (parent of the workspaces). Honored if a deployment relocates it.
OC_HOME = Path(os.environ.get("OPENCLAW_HOME") or (Path.home() / ".openclaw"))

# This agent's own workspace.
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or (OC_HOME / "workspace"))

# Sibling workspaces (same parent as WORKSPACE by convention; env-overridable).
# MAIN_WS is the shared/main workspace — bookmarker & trader agents read shared artifacts
# (decision-index, community-heat, content-intelligence) from it. For the main agent,
# MAIN_WS == WORKSPACE. For bookmarker/trader, it's the sibling 'workspace' dir.
_SIBLING_PARENT = WORKSPACE.parent
MAIN_WS = Path(os.environ.get("OPENCLAW_MAIN_WORKSPACE")
               or (_SIBLING_PARENT / "workspace"))
BOOKMARKER_WS = Path(os.environ.get("OPENCLAW_BOOKMARKER_WORKSPACE")
                     or (_SIBLING_PARENT / "workspace-bookmarker"))
TRADER_WS = Path(os.environ.get("OPENCLAW_TRADER_WORKSPACE")
                 or (_SIBLING_PARENT / "workspace-trader"))

# Common derived roots (convenience; scripts may also build these themselves).
RUNTIME = WORKSPACE / "runtime"
SHARED = RUNTIME / "shared"
WIKI = WORKSPACE / "wiki"

__all__ = ["OC_HOME", "WORKSPACE", "MAIN_WS", "BOOKMARKER_WS", "TRADER_WS",
           "RUNTIME", "SHARED", "WIKI"]

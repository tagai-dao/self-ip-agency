#!/usr/bin/env python3
"""Bookmarker-owned social execution entrypoint.

PR1 control/execution boundary realignment:
- Main remains the control plane and publishes guidance / intents.
- Bookmarker is the social execution owner and should be invoked through this
  workspace-local entrypoint.

This wrapper delegates to the canonical executor implementation in the main
workspace to avoid code duplication during the transition.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from agency_paths import MAIN_WS

MAIN_ROOT = (MAIN_WS)
IMPL = MAIN_ROOT / 'scripts' / 'execute_social_intent_v2.py'


def main() -> int:
    spec = importlib.util.spec_from_file_location('execute_social_intent_v2', IMPL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'failed to load social executor: {IMPL}')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return int(mod.main())


if __name__ == '__main__':
    raise SystemExit(main())

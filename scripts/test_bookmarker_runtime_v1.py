#!/usr/bin/env python3
"""Regression tests for run_bookmarker_runtime_v1 config parsing.

Covers the failure class where inline YAML comments and naive handwritten
parsing caused the bookmarker native runtime to crash with
``ValueError: invalid literal for int() with base 10: '0.60 # 60% ...'``.

Run directly:
    python3 scripts/test_bookmarker_runtime_v1.py
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _load_module_with_workspace(workspace: Path):
    """Re-import run_bookmarker_runtime_v1 with OPENCLAW_WORKSPACE set."""
    os.environ["OPENCLAW_WORKSPACE"] = str(workspace)
    if "run_bookmarker_runtime_v1" in sys.modules:
        del sys.modules["run_bookmarker_runtime_v1"]
    return importlib.import_module("run_bookmarker_runtime_v1")


def _prep_workspace(yaml_text: str) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="bookmarker-test-"))
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "agency.config.yaml").write_text(yaml_text, encoding="utf-8")
    return ws


FLAT_WITH_INLINE_COMMENT = """\
agency:
  name: "self-ip-agency"

# Curation / VP settings (flat legacy shape)
curation_vp_pct: 0.60          # 60% of VP budget for curation
posting_vp_pct: 0.25           # 25% for creating content
"""

NESTED_WITH_INLINE_COMMENT = """\
agency:
  name: "self-ip-agency"

social:
  vp_budget_daily: 1000          # VP allocated per day
  curation_vp_pct: 0.60          # 60% of VP budget for curation
  posting_vp_pct: 0.25           # 25% for creating content
"""

MALFORMED_VALUE = """\
social:
  curation_vp_pct: abc # not a number
"""

OUT_OF_RANGE_VALUE = """\
social:
  curation_vp_pct: 42.0 # absurd, must clamp to 1.0
"""

REAL_CONFIG = """\
# Curation / VP settings
social:
  vp_budget_daily: 1000          # VP allocated per day
  vp_reserve_floor: 50           # Never go below this
  curation_vp_pct: 0.60          # 60% of VP budget for curation
  posting_vp_pct: 0.25           # 25% for creating content
  reserve_vp_pct: 0.15           # 15% held in reserve
  cycle_interval_minutes: 30     # Bookmarker cycle frequency
"""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_strip_inline_comment(mod) -> None:
    _assert(mod._strip_inline_comment("0.60 # 60%") == "0.60",
            "inline comment not stripped")
    _assert(mod._strip_inline_comment("0.60") == "0.60",
            "plain value should be preserved")
    _assert(mod._strip_inline_comment('"value # not a comment"') == '"value # not a comment"',
            "quoted # should be preserved")
    _assert(mod._strip_inline_comment("'a # b' # real") == "'a # b'",
            "only trailing # comment should be stripped")


def test_coerce_pct(mod) -> None:
    _assert(mod._coerce_pct(0.6) == 0.6, "float pass-through")
    _assert(mod._coerce_pct(1) == 1.0, "int pass-through")
    _assert(mod._coerce_pct("0.6") == 0.6, "string pass-through")
    _assert(mod._coerce_pct("0.60 # 60%") == 0.6,
            "dirty string with inline comment must coerce to 0.6")
    _assert(mod._coerce_pct("abc", default=0.6) == 0.6,
            "malformed string must fall back to default")
    _assert(mod._coerce_pct(None, default=0.6) == 0.6,
            "None must fall back to default")
    _assert(mod._coerce_pct(42.0) == 1.0, "out-of-range must clamp to 1.0")
    _assert(mod._coerce_pct(-3) == 0.0, "negative must clamp to 0.0")
    _assert(mod._coerce_pct(float("nan"), default=0.6) == 0.6,
            "NaN must fall back to default")


def test_inline_comment_flat_config() -> None:
    ws = _prep_workspace(FLAT_WITH_INLINE_COMMENT)
    mod = _load_module_with_workspace(ws)
    cfg = mod.load_config()
    _assert(isinstance(cfg, dict), "load_config must return dict")
    # Flat legacy key should round-trip as a float.
    val = cfg.get("curation_vp_pct")
    _assert(val == 0.6, f"flat curation_vp_pct should be 0.6 float, got {val!r}")
    pct, warn = mod.resolve_curation_vp_pct(cfg)
    _assert(pct == 0.6, f"resolver must see 0.6, got {pct}")
    _assert(warn is None, f"no warning expected on clean read, got {warn!r}")


def test_nested_yaml_config() -> None:
    ws = _prep_workspace(NESTED_WITH_INLINE_COMMENT)
    mod = _load_module_with_workspace(ws)
    cfg = mod.load_config()
    social = cfg.get("social")
    _assert(isinstance(social, dict),
            f"nested 'social' must be a dict, got {type(social).__name__}")
    _assert(social.get("curation_vp_pct") == 0.6,
            f"social.curation_vp_pct must be 0.6, got {social.get('curation_vp_pct')!r}")
    pct, warn = mod.resolve_curation_vp_pct(cfg)
    _assert(pct == 0.6, f"resolver must see 0.6, got {pct}")
    _assert(warn is None, "no warning expected on clean nested read")


def test_malformed_value_does_not_crash() -> None:
    ws = _prep_workspace(MALFORMED_VALUE)
    mod = _load_module_with_workspace(ws)
    cfg = mod.load_config()
    # Must not crash.  Resolver must return default + warning.
    pct, warn = mod.resolve_curation_vp_pct(cfg, default=0.6)
    _assert(pct == 0.6, f"malformed value must fall back to 0.6, got {pct}")
    _assert(warn is not None and "repair" in warn.lower(),
            f"expected repair warning, got {warn!r}")


def test_out_of_range_value_clamps_with_warning() -> None:
    ws = _prep_workspace(OUT_OF_RANGE_VALUE)
    mod = _load_module_with_workspace(ws)
    cfg = mod.load_config()
    pct, warn = mod.resolve_curation_vp_pct(cfg)
    _assert(pct == 1.0, f"out-of-range value must clamp to 1.0, got {pct}")
    _assert(warn is not None and "clamp" in warn.lower(),
            f"expected clamp warning, got {warn!r}")


def test_real_config_matches_expected_shape() -> None:
    ws = _prep_workspace(REAL_CONFIG)
    mod = _load_module_with_workspace(ws)
    cfg = mod.load_config()
    social = cfg.get("social")
    _assert(isinstance(social, dict), "social must parse as nested dict")
    _assert(social.get("vp_budget_daily") == 1000, "vp_budget_daily int")
    _assert(social.get("curation_vp_pct") == 0.60, "curation_vp_pct float")
    _assert(social.get("posting_vp_pct") == 0.25, "posting_vp_pct float")
    pct, warn = mod.resolve_curation_vp_pct(cfg)
    _assert(pct == 0.60 and warn is None,
            f"resolver must report clean 0.60, got pct={pct} warn={warn!r}")
    # max_curations calculation that previously crashed must now succeed.
    max_curations = min(int(pct * 5), 3)
    _assert(max_curations == 3, f"max_curations must be 3, got {max_curations}")


def test_fallback_parser_without_pyyaml(monkey_no_yaml=True) -> None:
    """Force the fallback parser by hiding yaml from the import system."""
    ws = _prep_workspace(NESTED_WITH_INLINE_COMMENT)
    mod = _load_module_with_workspace(ws)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("simulated missing PyYAML")
        return real_import(name, *args, **kwargs)

    if isinstance(__builtins__, dict):
        __builtins__["__import__"] = _blocked_import
    else:
        __builtins__.__import__ = _blocked_import
    try:
        cfg = mod.load_config()
    finally:
        if isinstance(__builtins__, dict):
            __builtins__["__import__"] = real_import
        else:
            __builtins__.__import__ = real_import

    social = cfg.get("social")
    _assert(isinstance(social, dict),
            "fallback parser must produce nested dict for social")
    _assert(social.get("curation_vp_pct") == 0.6,
            f"fallback parser curation_vp_pct must be 0.6, got {social.get('curation_vp_pct')!r}")


def main() -> int:
    mod = _load_module_with_workspace(_prep_workspace(REAL_CONFIG))
    tests = [
        ("_strip_inline_comment", lambda: test_strip_inline_comment(mod)),
        ("_coerce_pct", lambda: test_coerce_pct(mod)),
        ("flat config with inline comment", test_inline_comment_flat_config),
        ("nested config with inline comment", test_nested_yaml_config),
        ("malformed value does not crash", test_malformed_value_does_not_crash),
        ("out-of-range value clamps", test_out_of_range_value_clamps_with_warning),
        ("real config shape", test_real_config_matches_expected_shape),
        ("fallback parser (no PyYAML)", test_fallback_parser_without_pyyaml),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
            traceback.print_exc()
    total = len(tests)
    print(f"\n{total - failures}/{total} checks PASS, {failures} FAIL")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

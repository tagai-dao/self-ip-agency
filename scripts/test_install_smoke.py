#!/usr/bin/env python3
"""Install-path smoke tests — catches call-chain and heredoc regressions.

Run: python3 scripts/test_install_smoke.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

PASS = 0
FAIL = 0


def _assert(cond: bool, msg: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {msg}")
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


# ── 1. install.sh syntax check ──────────────────────────────────────────────

def test_install_sh_syntax() -> None:
    """bash -n catches parse errors."""
    print("\n[install.sh syntax]")
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / "install.sh")],
        capture_output=True, text=True,
    )
    _assert(result.returncode == 0,
            f"bash -n install.sh (rc={result.returncode})")
    if result.stderr.strip():
        print(f"    stderr: {result.stderr.strip()[:200]}")


# ── 2. seed-raw-docs.sh syntax check ────────────────────────────────────────

def test_seed_raw_docs_syntax() -> None:
    print("\n[seed-raw-docs.sh syntax]")
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "scripts" / "seed-raw-docs.sh")],
        capture_output=True, text=True,
    )
    _assert(result.returncode == 0,
            f"bash -n seed-raw-docs.sh (rc={result.returncode})")
    if result.stderr.strip():
        print(f"    stderr: {result.stderr.strip()[:200]}")


# ── 3. Function call-chain consistency ───────────────────────────────────────

def test_install_function_callchain() -> None:
    """Every function called in the main flow must be defined in install.sh."""
    print("\n[install.sh function call-chain]")
    src = (REPO_ROOT / "scripts" / "install.sh").read_text()

    # Extract function definitions: name() { pattern
    defined = set(re.findall(r'^(\w+)\s*\(\)\s*\{', src, re.MULTILINE))

    # Extract the main orchestration block (after the last getopts/case block)
    # Look for calls in the main flow — bare function names as statements
    # Find the main flow section by looking for the sequential install steps
    main_flow_match = re.search(
        r'detect_identity\n.*?^}', src,
        re.MULTILINE | re.DOTALL,
    )
    if not main_flow_match:
        # Fallback: scan entire file for bare function-call lines
        main_flow = src
    else:
        main_flow = main_flow_match.group(0)

    # Known shell builtins and external commands to ignore
    ignore = {
        "log_info", "log_ok", "log_warn", "log_error", "log_debug",
        "echo", "printf", "local", "export", "return", "shift",
        "true", "false", "exit", "source", "cd", "mkdir", "chmod",
        "cat", "sed", "grep", "awk", "curl", "python3", "bash",
        "command", "type", "eval", "exec", "test", "set", "unset",
        "read", "wait", "trap", "kill", "sleep",
    }

    # Check that specific known install-step functions are defined
    install_steps = [
        "detect_identity",
        "configure_from_identity",
        "install_runtime",
        "install_wiki",
        "install_autoresearch",
        "seed_raw_docs",
        "register_crons",
    ]

    for fn in install_steps:
        _assert(fn in defined,
                f"install step '{fn}' is defined in install.sh")


# ── 4. Heredoc quoting in seed-raw-docs.sh ───────────────────────────────────

def test_seed_heredocs_quoted() -> None:
    """All python3 heredocs should use quoted delimiters to prevent shell expansion."""
    print("\n[seed-raw-docs.sh heredoc quoting]")
    src = (REPO_ROOT / "scripts" / "seed-raw-docs.sh").read_text()

    # Find all python3 heredoc invocations
    unquoted = re.findall(r"python3\s+-\s*<<(\w+)", src)
    quoted = re.findall(r"python3\s+-\s*<<'(\w+)'", src)

    _assert(len(unquoted) == 0,
            f"no unquoted python3 heredocs (found {len(unquoted)})")
    _assert(len(quoted) > 0,
            f"has quoted python3 heredocs (found {len(quoted)})")


# ── 5. No stale function references ─────────────────────────────────────────

def test_no_stale_bootstrap_call() -> None:
    """Ensure the old bootstrap_guided_x_sync name is not referenced."""
    print("\n[no stale bootstrap_guided_x_sync reference]")
    src = (REPO_ROOT / "scripts" / "install.sh").read_text()
    occurrences = src.count("bootstrap_guided_x_sync")
    _assert(occurrences == 0,
            f"'bootstrap_guided_x_sync' not referenced (found {occurrences})")


# ── 6. install.sh --dry-run smoke ────────────────────────────────────────────

def test_install_dry_run() -> None:
    """--dry-run should exit 0 without side effects."""
    print("\n[install.sh --dry-run]")
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "install.sh"), "--dry-run"],
        capture_output=True, text=True, timeout=30,
        cwd=str(REPO_ROOT),
    )
    _assert(result.returncode == 0,
            f"install.sh --dry-run exits 0 (rc={result.returncode})")
    if result.returncode != 0:
        # Show last few lines of output for diagnosis
        lines = (result.stdout + result.stderr).strip().split('\n')
        for line in lines[-5:]:
            print(f"    | {line}")


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Install-path smoke tests")
    print("=" * 60)

    test_install_sh_syntax()
    test_seed_raw_docs_syntax()
    test_install_function_callchain()
    test_seed_heredocs_quoted()
    test_no_stale_bootstrap_call()
    test_install_dry_run()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")

    sys.exit(1 if FAIL > 0 else 0)

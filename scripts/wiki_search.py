#!/usr/bin/env python3
"""wiki_search.py — Unified search interface for wiki-concepts collection.

Wraps qmd CLI calls (query/search/vsearch) and outputs JSON results.
Appends each search to wiki/log.md.

Usage:
  python3 scripts/wiki_search.py --query "TokenEconomy bonding curve" --n 3
  python3 scripts/wiki_search.py --query "agent harness" --mode search --n 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
QMD_BIN = os.environ.get("QMD_BIN", os.path.expanduser("~/.bun/bin/qmd"))
LOG_FILE = WORKSPACE / "wiki" / "log.md"
DEFAULT_COLLECTION = "wiki-concepts"


def append_log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception as e:
        print(f"[warn] log append failed: {e}", file=sys.stderr)


def run_qmd(mode: str, query: str, collection: str, n: int) -> list[dict]:
    cmd = [QMD_BIN, mode, query, "--collection", collection, "-n", str(n)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print(f"[error] qmd not found at {QMD_BIN}", file=sys.stderr)
        return []
    except subprocess.TimeoutExpired:
        print("[error] qmd timed out", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[error] qmd call failed: {e}", file=sys.stderr)
        return []

    output = result.stdout
    if result.returncode != 0 and not output:
        print(f"[error] qmd exited {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
        return []

    return parse_qmd_output(output, mode)


def parse_qmd_output(output: str, mode: str) -> list[dict]:
    results: list[dict] = []
    current: dict | None = None

    for line in output.splitlines():
        file_match = re.match(r'^(qmd://[^\s]+)\s+#[0-9a-fA-F]+\s*$', line.strip())
        if file_match:
            if current:
                results.append(current)
            current = {"file": file_match.group(1), "score": None, "snippet": ""}
            continue

        if current is None:
            continue

        score_match = re.match(r'^Score:\s+(\d+)%', line.strip())
        if score_match:
            current["score"] = int(score_match.group(1)) / 100.0
            continue

        if line.startswith("|") or line.startswith("@@") or (line.strip() and not line.startswith("Title:")):
            if len(current.get("snippet", "")) < 200:
                current["snippet"] = (current.get("snippet", "") + " " + line.strip()).strip()

    if current:
        results.append(current)

    if not results and output.strip():
        results = [{"file": "", "score": None, "snippet": output.strip()[:500]}]

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Wiki search via qmd")
    parser.add_argument("--query", required=True, help="Search query text")
    parser.add_argument("--mode", choices=["query", "search", "vsearch"],
                        default="search", help="qmd mode (default: search)")
    parser.add_argument("--n", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION,
                        help=f"qmd collection name (default: {DEFAULT_COLLECTION})")
    args = parser.parse_args()

    results = run_qmd(args.mode, args.query, args.collection, args.n)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    top_file = results[0].get("file", "") if results else ""
    append_log(f"[{ts}] query | {args.query[:50]} | mode: {args.mode} | top: {top_file}")

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

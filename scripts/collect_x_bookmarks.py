#!/usr/bin/env python3
from __future__ import annotations
"""
collect_x_bookmarks.py — 通过 API 分页拉取 @0xNought 的书签
写入 memory/raw/x-bookmarks/ 目录，格式与现有 x-bookmarks 一致

用法:
  python3 scripts/collect_x_bookmarks.py
  python3 scripts/collect_x_bookmarks.py --dry-run
  python3 scripts/collect_x_bookmarks.py --raw-dir /path/to/dir
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
MEMORY = WORKSPACE / "memory"
DEFAULT_RAW_DIR = MEMORY / "raw" / "x-bookmarks"
DEFAULT_STATE_FILE = MEMORY / "x-bookmarks-state.json"

USER_ID = "1436783473"
XURL = shutil.which("xurl") or str(Path.home() / ".local/bin/xurl")


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s)


def run_xurl(args: list[str]) -> tuple[dict | list | None, str | None]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    try:
        proc = subprocess.run(
            [XURL] + args,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None, "xurl timeout"
    out = strip_ansi(proc.stdout or "")
    err = strip_ansi(proc.stderr or "")
    if proc.returncode != 0:
        return None, (err or out or f"xurl exit {proc.returncode}")
    try:
        return json.loads(out), None
    except Exception:
        return None, f"JSON parse error: {out[:300]}"


BIRD = shutil.which("bird") or str(Path.home() / ".local/bin/bird")


def run_bird(args: list[str]) -> tuple[list | None, str | None]:
    """Run bird CLI and return (items_list, error_msg_or_None)."""
    import re as _re
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    try:
        proc = subprocess.run(
            [BIRD, "--plain", "--no-color", "--cookie-source", "chrome"] + args,
            capture_output=True,
            text=True,
            env=env,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return None, "bird timeout"
    out = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", proc.stdout or "")
    err = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", proc.stderr or "")
    if proc.returncode != 0:
        return None, (err or out or f"bird exit {proc.returncode}")[:400]
    # strip info/warn lines
    clean = "\n".join(
        line for line in out.splitlines()
        if not line.startswith("[warn]") and not line.startswith("[info]") and not line.startswith("\u26a0")
    ).strip()
    try:
        items = json.loads(clean)
        if isinstance(items, list):
            return items, None
        if isinstance(items, dict):
            for key in ("tweets", "items", "data"):
                if isinstance(items.get(key), list):
                    return items[key], None
        return None, f"bird returned unexpected structure: {type(items)}"
    except Exception as e:
        return None, f"bird JSON parse error: {e}: {clean[:200]}"


def bird_item_to_v2(item: dict) -> tuple[dict, dict]:
    """Convert a bird-format tweet item to (tweet_v2_dict, user_dict) for build_md()."""
    import re as _re
    author = item.get("author") or {}
    author_id = str(author.get("id") or item.get("authorId") or "unknown")
    username = author.get("username") or "unknown"
    # bird returns createdAt in Twitter RSS format ("Thu Jan 01 00:00:00 +0000 2025")
    # OR ISO 8601 — normalise to ISO 8601 Z for build_md
    raw_ts = item.get("createdAt") or ""
    try:
        from email.utils import parsedate_to_datetime
        iso_ts = parsedate_to_datetime(raw_ts).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        iso_ts = raw_ts  # already ISO or empty
    pm = item.get("publicMetrics") or item.get("public_metrics") or {}
    tweet_v2 = {
        "id": str(item.get("id") or ""),
        "text": item.get("text") or "",
        "created_at": iso_ts,
        "author_id": author_id,
        "public_metrics": {
            "like_count": pm.get("likeCount") or pm.get("like_count") or 0,
            "retweet_count": pm.get("retweetCount") or pm.get("retweet_count") or 0,
            "reply_count": pm.get("replyCount") or pm.get("reply_count") or 0,
        },
    }
    user_v2 = {
        "id": author_id,
        "username": username,
        "name": author.get("name") or username,
    }
    return tweet_v2, user_v2


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {"last_seen_ids": [], "last_sync": None, "total_collected": 0, "oldest_collected_at": None}


def save_state_atomic(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=state_file.parent, suffix=".tmp", delete=False) as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, state_file)


def get_existing_ids(raw_dir: Path) -> set[str]:
    """Read all existing bookmark files and extract tweet_ids."""
    existing: set[str] = set()
    if not raw_dir.exists():
        return existing
    for p in raw_dir.glob("*.md"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end == -1:
            continue
        for line in text[3:end].splitlines():
            if line.startswith("tweet_id:"):
                tid = line.split(":", 1)[1].strip().strip('"')
                if tid:
                    existing.add(tid)
                break
    return existing


def build_md(tweet: dict, users_map: dict) -> tuple[str, str]:
    tweet_id = tweet.get("id", "")
    created_at = tweet.get("created_at", "")
    author_id = tweet.get("author_id", "")

    author_info = users_map.get(author_id, {})
    username = author_info.get("username", author_id)

    source_url = f"https://x.com/{username}/status/{tweet_id}"

    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        date_prefix = dt.strftime("%Y-%m-%d")
    except Exception:
        date_prefix = "0000-00-00"

    text = tweet.get("text", "")
    metrics = tweet.get("public_metrics", {})

    frontmatter = f"""---
tweet_id: "{tweet_id}"
author: "{username}"
created_at: "{created_at}"
theme: ""
source_url: "{source_url}"
type: "bookmark"
public_metrics:
  like_count: {metrics.get("like_count", 0)}
  retweet_count: {metrics.get("retweet_count", 0)}
  reply_count: {metrics.get("reply_count", 0)}
---

{text}
"""
    return frontmatter, date_prefix


def collect(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir)
    state_file = Path(args.state_file)
    raw_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(state_file)
    existing_ids = get_existing_ids(raw_dir)
    seen_ids: set[str] = set(state.get("last_seen_ids", [])) | existing_ids

    print(f"[collect_x_bookmarks] existing={len(existing_ids)} dry_run={args.dry_run}")

    total_written = 0
    total_skipped = 0
    oldest_at: str | None = None

    # ── Priority 1: bird (cookie-based, no API quota) ────────────────────────
    bird_items, bird_err = run_bird(["bookmarks", "-n", "100", "--json"])
    if bird_err:
        print(f"  [bird] unavailable: {bird_err} — falling back to xurl")
    elif bird_items:
        print(f"  [bird] fetched {len(bird_items)} bookmarks")
        for item in bird_items:
            tweet_v2, user_v2 = bird_item_to_v2(item)
            tweet_id = tweet_v2.get("id", "")
            if not tweet_id:
                continue
            if tweet_id in seen_ids:
                total_skipped += 1
                continue
            users_map = {tweet_v2["author_id"]: user_v2}
            md_content, date_prefix = build_md(tweet_v2, users_map)
            filename = f"{date_prefix}-{tweet_id}.md"
            out_path = raw_dir / filename
            if args.dry_run:
                print(f"    [dry-run] would write {filename}")
            elif not out_path.exists():
                out_path.write_text(md_content, encoding="utf-8")
            total_written += 1
            seen_ids.add(tweet_id)
            if not oldest_at or (tweet_v2.get("created_at") and tweet_v2["created_at"] < oldest_at):
                oldest_at = tweet_v2.get("created_at")
        state["last_seen_ids"] = list(seen_ids)[-1000:]
        state["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["total_collected"] = len(existing_ids) + total_written
        if oldest_at:
            state["oldest_collected_at"] = oldest_at
        if not args.dry_run:
            save_state_atomic(state_file, state)
        print(f"\n[collect_x_bookmarks] done (bird): new={total_written} skipped={total_skipped} total={state['total_collected']}")
        return 0
    else:
        print("  [bird] returned 0 items — falling back to xurl")

    # ── Priority 3: xurl (Twitter API v2, quota-consuming) ───────────────────
    next_token: str | None = None
    page = 0

    while True:
        page += 1
        params = [
            "max_results=100",
            "tweet.fields=created_at,author_id,public_metrics,entities",
            "expansions=author_id",
            "user.fields=username,name,public_metrics",
        ]
        if next_token:
            params.append(f"pagination_token={next_token}")

        endpoint = f"/2/users/{USER_ID}/bookmarks?" + "&".join(params)

        print(f"  [xurl page {page}] fetching bookmarks ...", end=" ", flush=True)
        data, err = run_xurl([endpoint])

        if err:
            if "429" in err or "rate limit" in err.lower():
                print(f"[rate-limit] waiting 15s ...")
                time.sleep(15)
                data, err = run_xurl([endpoint])
                if err:
                    print(f"[error] retry failed: {err}", file=sys.stderr)
                    break
            else:
                print(f"[error] {err}", file=sys.stderr)
                break

        tweets = data.get("data", []) if isinstance(data, dict) else []
        includes = data.get("includes", {}) if isinstance(data, dict) else {}
        meta = data.get("meta", {}) if isinstance(data, dict) else {}

        users_map: dict[str, dict] = {}
        for u in includes.get("users", []):
            users_map[u["id"]] = u

        print(f"{len(tweets)} bookmarks", flush=True)

        for tweet in tweets:
            tweet_id = tweet.get("id", "")
            created_at = tweet.get("created_at", "")

            if created_at:
                try:
                    tweet_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    if oldest_at is None or tweet_dt < datetime.fromisoformat(oldest_at.replace("Z", "+00:00")):
                        oldest_at = created_at
                except Exception:
                    pass

            if tweet_id in seen_ids:
                total_skipped += 1
                continue

            md_content, date_prefix = build_md(tweet, users_map)
            filename = f"{date_prefix}-{tweet_id}.md"
            out_path = raw_dir / filename

            if args.dry_run:
                print(f"    [dry-run] would write {filename}")
                seen_ids.add(tweet_id)
                total_written += 1
                continue

            if not out_path.exists():
                out_path.write_text(md_content, encoding="utf-8")
                total_written += 1

            seen_ids.add(tweet_id)

        # Pagination — bookmarks API returns at most ~800 total (no start_time)
        next_token = meta.get("next_token")
        if not next_token:
            break

        time.sleep(1)

    # Update state
    state["last_seen_ids"] = list(seen_ids)[-1000:]
    state["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["total_collected"] = len(existing_ids) + total_written
    if oldest_at:
        state["oldest_collected_at"] = oldest_at

    if not args.dry_run:
        save_state_atomic(state_file, state)

    print(f"\n[collect_x_bookmarks] done: new={total_written} skipped={total_skipped} total={state['total_collected']}")
    if oldest_at:
        print(f"  oldest bookmark date: {oldest_at[:10]}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect @0xNought bookmarks into raw/x-bookmarks/")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't write files")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file path")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Output directory")
    args = parser.parse_args()

    sys.exit(collect(args))


if __name__ == "__main__":
    main()

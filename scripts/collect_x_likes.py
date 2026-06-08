#!/usr/bin/env python3
from __future__ import annotations
"""
collect_x_likes.py — 采集 @0xNought (user_id=1436783473) 近1年的 liked_tweets
写入 memory/raw/x-likes/ 目录，格式与 x-bookmarks 一致

用法:
  python3 scripts/collect_x_likes.py --since 2025-04-07 --max-pages 75
  python3 scripts/collect_x_likes.py --dry-run
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
DEFAULT_RAW_DIR = MEMORY / "raw" / "x-likes"
DEFAULT_STATE_FILE = MEMORY / "x-likes-state.json"

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


def bird_item_to_v2(item: dict, liked_by: str = "0xNought") -> tuple[dict, dict]:
    """Convert a bird-format tweet item to (tweet_v2_dict, user_dict) for build_md()."""
    author = item.get("author") or {}
    author_id = str(author.get("id") or item.get("authorId") or "unknown")
    username = author.get("username") or "unknown"
    raw_ts = item.get("createdAt") or ""
    try:
        from email.utils import parsedate_to_datetime
        iso_ts = parsedate_to_datetime(raw_ts).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        iso_ts = raw_ts
    pm = item.get("publicMetrics") or item.get("public_metrics") or {}
    tweet_v2 = {
        "id": str(item.get("id") or ""),
        "text": item.get("text") or "",
        "created_at": iso_ts,
        "author_id": author_id,
        "conversation_id": str(item.get("conversationId") or ""),
        "lang": item.get("lang") or "",
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
        "public_metrics": {"followers_count": author.get("followersCount") or 0},
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


def build_md(tweet: dict, users_map: dict, liked_by: str = "0xNought") -> str:
    tweet_id = tweet.get("id", "")
    text = tweet.get("text", "")
    created_at = tweet.get("created_at", "")
    lang = tweet.get("lang", "")
    author_id = tweet.get("author_id", "")
    conv_id = tweet.get("conversation_id", "")

    # Resolve author from expansions
    author_info = users_map.get(author_id, {})
    username = author_info.get("username", author_id)
    name = author_info.get("name", "")
    pub = author_info.get("public_metrics", {})
    followers = pub.get("followers_count", 0)

    metrics = tweet.get("public_metrics", {})
    like_count = metrics.get("like_count", 0)
    retweet_count = metrics.get("retweet_count", 0)
    reply_count = metrics.get("reply_count", 0)

    source_url = f"https://x.com/{username}/status/{tweet_id}"

    # Date prefix for filename: use created_at
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        date_prefix = dt.strftime("%Y-%m-%d")
    except Exception:
        date_prefix = "0000-00-00"

    frontmatter = f"""---
tweet_id: "{tweet_id}"
author: "{username}"
author_name: "{name}"
author_followers: {followers}
liked_by: "{liked_by}"
created_at: "{created_at}"
liked_at: null
theme: ""
source_url: "{source_url}"
type: "liked_tweet"
public_metrics:
  like_count: {like_count}
  retweet_count: {retweet_count}
  reply_count: {reply_count}
lang: "{lang}"
---

{text}
"""
    return frontmatter, date_prefix


def collect(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir)
    state_file = Path(args.state_file)
    raw_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(state_file)
    seen_ids: set[str] = set(state.get("last_seen_ids", []))

    # --all mode: fetch full history, no date cutoff
    all_mode: bool = getattr(args, "all", False)

    if all_mode:
        since_dt = None
        print(f"[collect_x_likes] mode=ALL max_pages={args.max_pages} dry_run={args.dry_run}")
    else:
        # Parse since date
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            print(f"[error] invalid --since format: {args.since}", file=sys.stderr)
            return 1
        since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[collect_x_likes] since={since_iso} max_pages={args.max_pages} dry_run={args.dry_run}")

    total_written = 0
    total_skipped = 0
    next_token: str | None = None
    page = 0
    oldest_at: str | None = None
    stop_reason = "exhausted"

    while page < args.max_pages:
        page += 1

        # Build query string
        params = [
            f"max_results=100",
            f"tweet.fields=created_at,author_id,public_metrics,conversation_id,entities,lang",
            f"expansions=author_id",
            f"user.fields=username,name,public_metrics,description",
        ]
        if next_token:
            params.append(f"pagination_token={next_token}")

        endpoint = f"/2/users/{USER_ID}/liked_tweets?" + "&".join(params)

        print(f"  [page {page}] fetching liked_tweets ...", end=" ", flush=True)
        data, err = run_xurl([endpoint])

        if err:
            if "429" in err or "rate limit" in err.lower():
                print(f"[rate-limit] waiting 15s ...")
                time.sleep(15)
                data, err = run_xurl([endpoint])
                if err:
                    print(f"[error] retry failed: {err}", file=sys.stderr)
                    stop_reason = "rate_limit"
                    break
            else:
                print(f"[error] {err}", file=sys.stderr)
                stop_reason = "error"
                break

        tweets = data.get("data", []) if isinstance(data, dict) else []
        includes = data.get("includes", {}) if isinstance(data, dict) else {}
        meta = data.get("meta", {}) if isinstance(data, dict) else {}

        # Build users map from expansions
        users_map: dict[str, dict] = {}
        for u in includes.get("users", []):
            users_map[u["id"]] = u

        print(f"{len(tweets)} tweets", flush=True)

        page_oldest = None
        for tweet in tweets:
            tweet_id = tweet.get("id", "")
            created_at = tweet.get("created_at", "")

            # Check date cutoff (only in incremental mode)
            if created_at and since_dt is not None:
                try:
                    tweet_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    if tweet_dt < since_dt:
                        stop_reason = "date_cutoff"
                        break
                    if page_oldest is None or tweet_dt < datetime.fromisoformat(page_oldest.replace("Z", "+00:00")):
                        page_oldest = created_at
                except Exception:
                    pass
            elif created_at:
                try:
                    tweet_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    if page_oldest is None or tweet_dt < datetime.fromisoformat(page_oldest.replace("Z", "+00:00")):
                        page_oldest = created_at
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

        if page_oldest:
            oldest_at = page_oldest

        if stop_reason == "date_cutoff":
            break

        # Pagination
        next_token = meta.get("next_token")
        if not next_token:
            stop_reason = "exhausted"
            break

        # Polite delay
        time.sleep(1)

    # Update state
    state["last_seen_ids"] = list(seen_ids)[-500:]  # keep last 500
    state["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["total_collected"] = state.get("total_collected", 0) + total_written
    if oldest_at:
        state["oldest_collected_at"] = oldest_at

    if not args.dry_run:
        save_state_atomic(state_file, state)

    print(f"\n[collect_x_likes] done: written={total_written} skipped={total_skipped} stop={stop_reason}")
    print(f"  state: total_collected={state['total_collected']} last_sync={state['last_sync']}")
    return 0


def main() -> None:
    default_since = (datetime.now(timezone.utc).replace(
        year=datetime.now(timezone.utc).year - 1
    )).strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="Collect @0xNought liked tweets into raw/x-likes/")
    parser.add_argument("--since", default=default_since, help="Start date YYYY-MM-DD (default: 1 year ago)")
    parser.add_argument("--all", action="store_true", help="Fetch full history (no date filter). Overrides --since.")
    parser.add_argument("--max-pages", type=int, default=50, help="Max pages to fetch (default 50 = ~5000 tweets)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, don't write files")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file path")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Output directory")
    args = parser.parse_args()

    sys.exit(collect(args))


if __name__ == "__main__":
    main()

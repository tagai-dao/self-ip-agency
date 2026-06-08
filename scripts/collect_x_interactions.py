#!/usr/bin/env python3
from __future__ import annotations
"""
collect_x_interactions.py — 采集 @0xNought 近1年推文的互动数据
(liking_users, retweeted_by, replies) 写入 memory/raw/x-interactions/{tweet_id}.json

用法:
  python3 scripts/collect_x_interactions.py --since 2025-04-07 --max-tweets 300
  python3 scripts/collect_x_interactions.py --dry-run --max-tweets 10
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
MEMORY = WORKSPACE / "memory"
RAW_TWEETS = MEMORY / "raw" / "x-tweets"
DEFAULT_RAW_DIR = MEMORY / "raw" / "x-interactions"
DEFAULT_STATE_FILE = MEMORY / "x-interactions-state.json"

XURL = shutil.which("xurl") or str(Path.home() / ".local/bin/xurl")
SEVEN_DAYS_SECS = 7 * 24 * 3600


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


def run_xurl_with_retry(args: list[str]) -> tuple[dict | list | None, str | None]:
    data, err = run_xurl(args)
    if err and ("429" in err or "rate limit" in err.lower()):
        print("    [rate-limit] waiting 15s ...", flush=True)
        time.sleep(15)
        data, err = run_xurl(args)
    return data, err


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {"last_seen_tweet_ids": [], "last_interaction_check": None, "total_interaction_records": 0}


def save_state_atomic(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=state_file.parent, suffix=".tmp", delete=False) as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, state_file)


def write_json_atomic(out_path: Path, data: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=out_path.parent, suffix=".tmp", delete=False) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, out_path)


def parse_frontmatter(text: str) -> dict:
    """Extract simple key: value frontmatter from a .md file."""
    meta: dict = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')
    return meta


def load_tweet_candidates(since_dt: datetime, min_interactions: int) -> list[dict]:
    """Read raw/x-tweets/ to get candidate tweet IDs for the interaction scan."""
    candidates = []
    if not RAW_TWEETS.exists():
        return candidates
    for p in RAW_TWEETS.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            meta = parse_frontmatter(text)
            created_at = meta.get("created_at", "")
            if not created_at:
                continue
            tweet_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if tweet_dt < since_dt:
                continue
            tweet_id = meta.get("tweet_id", "").strip('"')
            if not tweet_id:
                continue
            candidates.append({
                "tweet_id": tweet_id,
                "created_at": created_at,
                "tweet_dt": tweet_dt,
            })
        except Exception:
            continue
    return candidates


def fetch_from_search(since_dt: datetime, max_tweets: int) -> list[dict]:
    """
    Batch-fetch @0xNought tweets with public_metrics via recent search API.
    X recent search only supports the last 7 days, so clamp start_time accordingly.
    Returns list of {tweet_id, created_at, tweet_dt, like_count, retweet_count, reply_count}
    """
    results: list[dict] = []
    recent_floor_dt = datetime.now(timezone.utc) - timedelta(days=7)
    effective_since_dt = max(since_dt, recent_floor_dt)
    since_iso = effective_since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    next_token: str | None = None
    page = 0
    max_pages = max(1, max_tweets // 100 + 1)

    while page < max_pages and len(results) < max_tweets:
        page += 1
        params = [
            "query=from:0xNought",
            "max_results=100",
            "tweet.fields=created_at,public_metrics",
            f"start_time={since_iso}",
        ]
        if next_token:
            params.append(f"next_token={next_token}")
        endpoint = "/2/tweets/search/recent?" + "&".join(params)

        print(f"  [search page {page}] fetching from:0xNought ...", end=" ", flush=True)
        data, err = run_xurl_with_retry([endpoint])
        if err:
            print(f"error: {err}", file=sys.stderr)
            break

        tweets = data.get("data", []) if isinstance(data, dict) else []
        meta_obj = data.get("meta", {}) if isinstance(data, dict) else {}
        print(f"{len(tweets)} tweets", flush=True)

        for t in tweets:
            created_at = t.get("created_at", "")
            try:
                tweet_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                continue
            metrics = t.get("public_metrics", {})
            results.append({
                "tweet_id": t.get("id", ""),
                "created_at": created_at,
                "tweet_dt": tweet_dt,
                "like_count": metrics.get("like_count", 0),
                "retweet_count": metrics.get("retweet_count", 0),
                "reply_count": metrics.get("reply_count", 0),
            })

        next_token = meta_obj.get("next_token")
        if not next_token:
            break
        time.sleep(1)

    return results


def fetch_liking_users(tweet_id: str) -> tuple[list[dict], str | None]:
    endpoint = f"/2/tweets/{tweet_id}/liking_users?user.fields=username,name,public_metrics&max_results=100"
    data, err = run_xurl_with_retry([endpoint])
    if err:
        if "404" in err:
            return [], "deleted"
        return [], err
    users = data.get("data", []) if isinstance(data, dict) else []
    result = []
    for u in users:
        pub = u.get("public_metrics", {})
        result.append({
            "username": u.get("username", ""),
            "name": u.get("name", ""),
            "followers_count": pub.get("followers_count", 0),
        })
    return result, None


def fetch_retweeted_by(tweet_id: str) -> tuple[list[dict], str | None]:
    endpoint = f"/2/tweets/{tweet_id}/retweeted_by?user.fields=username,name,public_metrics&max_results=100"
    data, err = run_xurl_with_retry([endpoint])
    if err:
        if "404" in err:
            return [], "deleted"
        return [], err
    users = data.get("data", []) if isinstance(data, dict) else []
    result = []
    for u in users:
        pub = u.get("public_metrics", {})
        result.append({
            "username": u.get("username", ""),
            "name": u.get("name", ""),
            "followers_count": pub.get("followers_count", 0),
        })
    return result, None


def fetch_replies(tweet_id: str, created_at: str) -> tuple[list[dict], str]:
    """
    Fetch replies via conversation search (recent only — 7 day window).
    Returns (replies_list, status).
    """
    now = datetime.now(timezone.utc)
    try:
        tweet_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age_secs = (now - tweet_dt).total_seconds()
    except Exception:
        age_secs = SEVEN_DAYS_SECS + 1

    if age_secs > SEVEN_DAYS_SECS:
        return [], "recent_only"

    endpoint = (
        f"/2/tweets/search/recent?query=conversation_id:{tweet_id}"
        f"&tweet.fields=created_at,author_id,public_metrics"
        f"&expansions=author_id"
        f"&user.fields=username,name"
        f"&max_results=100"
    )
    data, err = run_xurl_with_retry([endpoint])
    if err:
        return [], "unavailable"

    tweets = data.get("data", []) if isinstance(data, dict) else []
    includes = data.get("includes", {}) if isinstance(data, dict) else {}
    users_map: dict[str, dict] = {u["id"]: u for u in includes.get("users", [])}

    replies = []
    for t in tweets:
        author_id = t.get("author_id", "")
        author_info = users_map.get(author_id, {})
        replies.append({
            "tweet_id": t.get("id", ""),
            "author": author_info.get("username", author_id),
            "text": t.get("text", ""),
            "created_at": t.get("created_at", ""),
        })
    return replies, "complete"


def load_all_tweet_candidates(seen_ids: set[str]) -> list[dict]:
    """Scan ALL raw/x-tweets/ files, skip already-processed tweet_ids."""
    candidates = []
    if not RAW_TWEETS.exists():
        return candidates
    for p in RAW_TWEETS.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            meta = parse_frontmatter(text)
            tweet_id = meta.get("tweet_id", "").strip('"')
            if not tweet_id or tweet_id in seen_ids:
                continue
            created_at = meta.get("created_at", "")
            try:
                tweet_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                tweet_dt = datetime.min.replace(tzinfo=timezone.utc)
            candidates.append({
                "tweet_id": tweet_id,
                "created_at": created_at,
                "tweet_dt": tweet_dt,
            })
        except Exception:
            continue
    return candidates


def collect(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir)
    state_file = Path(args.state_file)
    raw_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(state_file)
    seen_tweet_ids: set[str] = set(state.get("last_seen_tweet_ids", []))

    all_tweets_mode: bool = getattr(args, "all_tweets", False) or getattr(args, "resume", False)

    if all_tweets_mode:
        print(f"[collect_x_interactions] mode=ALL-TWEETS resume=True seen={len(seen_tweet_ids)}")
        print("\n=== Phase 1: Scan all raw/x-tweets/ for unprocessed IDs ===")
        candidates = load_all_tweet_candidates(seen_tweet_ids)
        # Sort newest first (prioritize recent content)
        candidates.sort(key=lambda x: x.get("tweet_dt", datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        filtered = candidates
        print(f"  found {len(filtered)} unprocessed tweets (skipped {len(seen_tweet_ids)} already done)")
    else:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            print(f"[error] invalid --since: {args.since}", file=sys.stderr)
            return 1

        print(f"[collect_x_interactions] since={args.since} max_tweets={args.max_tweets} min_interactions={args.min_interactions}")

        # Strategy: use search API to get tweets with public_metrics, then filter
        print("\n=== Phase 1: Fetch tweet list with public_metrics ===")
        candidates = fetch_from_search(since_dt, args.max_tweets)

        if not candidates:
            # Fallback: scan raw/x-tweets/ (no metrics, but IDs available)
            print("  [fallback] search failed, scanning raw/x-tweets/ for IDs")
            candidates = load_tweet_candidates(since_dt, args.min_interactions)

        # Filter by min_interactions
        filtered = [
            c for c in candidates
            if (c.get("like_count", 0) + c.get("retweet_count", 0) + c.get("reply_count", 0)) >= args.min_interactions
        ]

        # If no metrics available (fallback), include all
        if not filtered:
            filtered = candidates

        # Sort by date desc, cap at max_tweets
        filtered.sort(key=lambda x: x.get("tweet_dt", datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        filtered = filtered[:args.max_tweets]

        print(f"  candidates: {len(candidates)} total, {len(filtered)} with interactions >= {args.min_interactions}")

    print("\n=== Phase 2: Fetch interaction data per tweet ===")
    total_written = 0
    total_skipped = 0

    for idx, c in enumerate(filtered):
        tweet_id = c["tweet_id"]
        created_at = c.get("created_at", "")

        if tweet_id in seen_tweet_ids:
            print(f"  [{idx+1}/{len(filtered)}] {tweet_id} — skip (already collected)")
            total_skipped += 1
            continue

        print(f"  [{idx+1}/{len(filtered)}] {tweet_id} ({created_at[:10]}) metrics=({c.get('like_count',0)}L/{c.get('retweet_count',0)}RT/{c.get('reply_count',0)}R)", flush=True)

        if args.dry_run:
            print(f"    [dry-run] would collect interactions for {tweet_id}")
            seen_tweet_ids.add(tweet_id)
            total_written += 1
            continue

        # Fetch liking_users
        liking_users: list[dict] = []
        retweeted_by: list[dict] = []
        replies: list[dict] = []
        replies_status = "unavailable"

        print(f"    liking_users ...", end=" ", flush=True)
        lu, lu_err = fetch_liking_users(tweet_id)
        if lu_err == "deleted":
            print(f"tweet deleted, skipping")
            seen_tweet_ids.add(tweet_id)
            continue
        liking_users = lu
        print(f"{len(liking_users)}", end="  ", flush=True)
        time.sleep(0.5)

        print(f"retweeted_by ...", end=" ", flush=True)
        rb, rb_err = fetch_retweeted_by(tweet_id)
        if rb_err and rb_err != "deleted":
            print(f"error: {rb_err}", end="  ", flush=True)
        else:
            retweeted_by = rb
        print(f"{len(retweeted_by)}", end="  ", flush=True)
        time.sleep(0.5)

        if not args.skip_replies:
            print(f"replies ...", end=" ", flush=True)
            replies, replies_status = fetch_replies(tweet_id, created_at)
            print(f"{len(replies)} ({replies_status})", flush=True)
        else:
            replies_status = "skipped"
            print(flush=True)

        record = {
            "tweet_id": tweet_id,
            "author": "0xNought",
            "created_at": created_at,
            "source_url": f"https://x.com/0xNought/status/{tweet_id}",
            "public_metrics": {
                "like_count": c.get("like_count", 0),
                "retweet_count": c.get("retweet_count", 0),
                "reply_count": c.get("reply_count", 0),
            },
            "liking_users": liking_users,
            "retweeted_by": retweeted_by,
            "replies": replies,
            "replies_status": replies_status,
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        out_path = raw_dir / f"{tweet_id}.json"
        write_json_atomic(out_path, record)
        seen_tweet_ids.add(tweet_id)
        total_written += 1

        # Checkpoint every 50 tweets to preserve progress on long runs
        if total_written % 50 == 0:
            state["last_seen_tweet_ids"] = list(seen_tweet_ids)[-3000:]
            state["last_interaction_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            state["total_interaction_records"] = state.get("total_interaction_records", 0) + 0  # running total updated at end
            save_state_atomic(state_file, state)
            print(f"  [checkpoint] saved state at {total_written} tweets written", flush=True)

        # Rate-limit spacing between tweets
        time.sleep(1)

    # Update state
    state["last_seen_tweet_ids"] = list(seen_tweet_ids)[-3000:]
    state["last_interaction_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["total_interaction_records"] = state.get("total_interaction_records", 0) + total_written

    if not args.dry_run:
        save_state_atomic(state_file, state)

    print(f"\n[collect_x_interactions] done: written={total_written} skipped={total_skipped}")
    print(f"  state: total_interaction_records={state['total_interaction_records']}")
    return 0


def main() -> None:
    default_since = (datetime.now(timezone.utc).replace(
        year=datetime.now(timezone.utc).year - 1
    )).strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="Collect @0xNought tweet interaction data")
    parser.add_argument("--since", default=default_since, help="Start date YYYY-MM-DD (default: 1 year ago)")
    parser.add_argument("--all-tweets", action="store_true", help="Process ALL raw/x-tweets/ (overrides --since). Reads seen_ids from state for resume support.")
    parser.add_argument("--resume", action="store_true", help="Alias for --all-tweets (resume a previous --all-tweets run)")
    parser.add_argument("--min-interactions", type=int, default=1, help="Min interactions to include (default 1)")
    parser.add_argument("--max-tweets", type=int, default=200, help="Max tweets to process (default 200, ignored with --all-tweets)")
    parser.add_argument("--dry-run", action="store_true", help="Print only, don't write files")
    parser.add_argument("--skip-replies", action="store_true", help="Skip reply fetching (faster)")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file path")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Output directory")
    args = parser.parse_args()

    sys.exit(collect(args))


if __name__ == "__main__":
    main()

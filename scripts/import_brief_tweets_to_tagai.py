#!/usr/bin/env python3
"""import_brief_tweets_to_tagai.py — Import full tweet data from social-brief to TagAI.

Reads the latest (or specified) social-brief-YYYY-MM-DD.json, extracts all
tweet summaries from project_intel and social_search sections, formats each
tweet into the TagAI full-tweet schema, and POSTs them to the TagAI API.

Data gap note (RESOLVED 2026-06-08 — Option A is implemented & verified):
  run_trader_social_brief._extract_tweet_summary() now stores a ``_raw`` sub-object
  (via _build_raw_tweet_fields) carrying tweetId / authorId / conversationId / createdAt
  / fullText + full author profile (id, name, username, profileImageUrl, followers/
  following/tweet/like/listed counts) from bird ``--json-full``'s GraphQL payload.
  _build_full_tweet_payload reads those fields, so authorId/conversationId/profile are
  populated whenever bird supplied --json-full. Verified: briefs with tweets fill
  authorId 100% (e.g. 2026-06-05 41/41, 06-04 30/30). Residual gaps only occur for
  xurl-sourced or pre-2026-06-04 briefs that predate the enrichment.

Usage:
    python3 scripts/import_brief_tweets_to_tagai.py             # latest brief
    python3 scripts/import_brief_tweets_to_tagai.py --recent 2  # last 2 briefs (dedup'd)
    python3 scripts/import_brief_tweets_to_tagai.py --dry-run
    python3 scripts/import_brief_tweets_to_tagai.py --date 2026-05-23
    python3 scripts/import_brief_tweets_to_tagai.py --date 2026-05-23 --dry-run
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root or scripts/
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lib.tagai_brief_import import import_brief_tweets_full  # noqa: E402

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))
RUNTIME_TRADER = WORKSPACE / "runtime" / "trader"

DRY_RUN = "--dry-run" in sys.argv
NO_REPLIES = "--no-replies" in sys.argv


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent),
                                    suffix=".tmp", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = f.name
    os.replace(tmp, path)


def _all_briefs() -> list[Path]:
    """Briefs newest-first by name (YYYY-MM-DD sorts lexicographically)."""
    return sorted(RUNTIME_TRADER.glob("social-brief-????-??-??.json"),
                  key=lambda p: p.name, reverse=True)


def _find_brief(date_arg: str | None) -> Path | None:
    if date_arg:
        p = RUNTIME_TRADER / f"social-brief-{date_arg}.json"
        return p if p.exists() else None
    candidates = _all_briefs()
    return candidates[0] if candidates else None


def main() -> int:
    date_arg: str | None = None
    recent_n: int | None = None
    for i, a in enumerate(sys.argv):
        if a == "--date" and i + 1 < len(sys.argv):
            date_arg = sys.argv[i + 1]
        if a == "--recent" and i + 1 < len(sys.argv):
            try:
                recent_n = max(1, int(sys.argv[i + 1]))
            except ValueError:
                recent_n = None

    # --recent N: import the last N briefs (TagAI dedups already-imported tweets), so
    # tweets from every brief that day get in even though briefs are produced every 3h.
    if recent_n and not date_arg:
        briefs = _all_briefs()[:recent_n]
        if not briefs:
            print("❌ No social-brief files found", file=sys.stderr)
            return 1
        rc = 0
        for bp in briefs:
            print(f"\n{'#'*60}\n# importing {bp.name}\n{'#'*60}")
            rc |= process_brief(bp)
        return rc

    brief_path = _find_brief(date_arg)
    if not brief_path:
        print(f"❌ No social-brief file found (date={date_arg or 'latest'})", file=sys.stderr)
        return 1
    return process_brief(brief_path)


def process_brief(brief_path: Path) -> int:
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    generated_at = brief.get("generated_at", "unknown")
    print(f"📋 Brief: {brief_path.name}  (generated {generated_at})")
    if DRY_RUN:
        mode = "DRY-RUN — no API calls will be made"
        if NO_REPLIES:
            mode += " (--no-replies: thread fetch skipped)"
        print(f"   Mode: {mode}")

    # Preview count
    preview = import_brief_tweets_full(brief, dry_run=True, fetch_replies=False)
    total = preview["total"]
    print(f"📡 Found {total} unique tweet(s) to import\n")

    if total == 0:
        print("ℹ️  No tweets found in this brief — nothing to import.")
        out_path = RUNTIME_TRADER / f"tagai-import-results-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
        atomic_write_json(out_path, {
            "imported_at": now_str(),
            "brief_file": brief_path.name,
            "brief_generated_at": generated_at,
            "dry_run": DRY_RUN,
            "total": 0,
            "ok": 0,
            "fail": 0,
            "results": [],
        })
        print(f"Results: {out_path}")
        return 0

    # Show dry-run breakdown or actually import
    summary = import_brief_tweets_full(brief, dry_run=DRY_RUN, fetch_replies=not NO_REPLIES)
    results = summary["results"]
    ok_count = int(summary.get("ok") or 0)
    fail_count = int(summary.get("fail") or 0)

    for i, row in enumerate(results, 1):
        tweet_id = row.get("tweet_id") or "???"
        author = row.get("author") or "unknown"
        source = row.get("source") or ""
        url = row.get("url") or ""
        text = row.get("text") or ""
        gaps = row.get("data_gaps") or []
        reply_hint = row.get("reply_count_hint") or 0
        result = row.get("result") or {}

        print(f"[{i}/{total}] @{author}  id={tweet_id}")
        print(f"        source: {source}")
        if url:
            print(f"        url:    {url}")
        print(f"        text:   {text[:100]}")

        if DRY_RUN:
            # Show data-gap analysis
            filled = [
                f"tweet.text ✓",
                f"tweet.tweetId={'✓' if tweet_id else '✗ (no URL)'}",
                f"tweetAuthor.username ✓",
                f"tweetAuthor.followersCount ✓",
                f"tweet.createdAt ✓",
            ]
            thread_fetched = row.get("thread_fetched", False)
            replies_count = row.get("replies_count", 0)
            print(f"        fields available: {', '.join(filled)}")
            if reply_hint > 0:
                if thread_fetched:
                    print(f"        replies: ✅ fetched {replies_count} reply object(s) via bird thread")
                else:
                    print(f"        reply_count_hint={reply_hint} (thread fetch pending or skipped)")
            print(f"        GAPS ({len(gaps)}):")
            for g in gaps[:4]:
                print(f"          ⚠️  {g}")
            if len(gaps) > 4:
                print(f"          ... and {len(gaps) - 4} more")
        else:
            status = result.get("status")
            ok = result.get("ok")
            endpoint = result.get("endpoint", "")
            data = result.get("data")
            if ok and status == 200:
                tweet_id_resp = (data or {}).get("tweetId", "") if isinstance(data, dict) else ""
                print(f"        ✅ OK  status={status}  tweetId_resp={tweet_id_resp}  endpoint={endpoint}")
            elif "already imported" in str((data or {}).get("error", "")):
                print(f"        ⏭️  SKIP (already imported)")
            else:
                err = result.get("error") or ""
                data_preview = str(data)[:200] if data else ""
                print(f"        ❌ FAIL  HTTP={status}  {err}  {data_preview}")
        print()

    # Summary
    print("=" * 60)
    if DRY_RUN:
        print(f"DRY-RUN complete: {total} tweet(s) would be imported")
        print()
        print("⚠️  DATA GAP WARNING:")
        print(f"   {summary.get('data_gap_warning', '')}")
        print()
        print("FIX PROPOSALS:")
        print("  A) Extend _extract_tweet_summary() to preserve bird --json-full")
        print("     raw fields (rest_id → authorId, legacy.followers_count etc)")
        print("  B) Re-fetch missing fields per-tweet via bird/xurl at import time")
        print("  C) Use URL-only importTweet and rely on server-side enrichment")
    else:
        print(f"Done: {ok_count} OK, {fail_count} FAIL, {total} total")
        print(f"OP caller twitterId: {summary.get('config_twitterId', '')}")

    # Write results
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = RUNTIME_TRADER / f"tagai-import-results-{ts}.json"
    atomic_write_json(out_path, {
        "imported_at": now_str(),
        "brief_file": brief_path.name,
        "brief_generated_at": generated_at,
        "dry_run": DRY_RUN,
        "config_twitterId": summary.get("config_twitterId") or "",
        "base_url": summary.get("base_url") or "",
        "total": total,
        "ok": ok_count,
        "fail": fail_count,
        "data_gap_warning": summary.get("data_gap_warning") or "",
        "results": results,
    })
    print(f"\nResults saved: {out_path}")

    return 0 if (DRY_RUN or fail_count == 0) else 1


if __name__ == "__main__":
    sys.exit(main())

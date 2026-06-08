from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

DEFAULT_CONFIG = {
    "baseUrl": "https://bsc-api.tagai.fun",
    "twitterId": "1737485384374767616",
    "pwd": "buidl-ai",
}

# Fields the brief summary carries vs what the TagAI full-tweet schema requires.
# Used to document gaps in dry-run output.
FULL_TWEET_SCHEMA_GAPS = [
    "tweet.authorId — not stored in brief summary (only username is)",
    "tweet.conversationId — not stored in brief summary",
    "tweetAuthor.id — not stored in brief summary (only username is)",
    "tweetAuthor.profileImageUrl — not stored in brief summary",
    "tweetAuthor.followingCount — not stored in brief summary",
    "tweetAuthor.tweetCount — not stored in brief summary",
    "tweetAuthor.listedCount — not stored in brief summary",
    "replies[] — brief stores reply COUNT only, not reply objects",
    "replyAuthors[] — brief stores reply COUNT only, not author objects",
]

_TWEET_ID_FROM_URL_RE = re.compile(r"/status/(\d+)")

_TWEET_URL_RE = re.compile(r"\[🔗\]\((https://x\.com/[^)]+)\)")


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config() -> dict[str, str]:
    return {
        "baseUrl": (os.environ.get("TAGAI_IMPORT_BASE_URL") or DEFAULT_CONFIG["baseUrl"]).strip(),
        "twitterId": (os.environ.get("TAGAI_IMPORT_TWITTER_ID") or DEFAULT_CONFIG["twitterId"]).strip(),
        "pwd": (os.environ.get("TAGAI_IMPORT_PWD") or DEFAULT_CONFIG["pwd"]).strip(),
    }


def extract_tweet_urls(brief: dict[str, Any]) -> list[dict[str, str]]:
    """Extract de-duplicated tweet URLs from a trader brief."""
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    md = str(brief.get("brief_markdown") or "")
    for url in _TWEET_URL_RE.findall(md):
        if url and url not in seen:
            seen.add(url)
            results.append({"url": url, "source": "brief_markdown", "text": ""})

    for proj in brief.get("project_intel", []):
        if not isinstance(proj, dict):
            continue
        handle = proj.get("username") or proj.get("twitter_handle") or ""
        for tweet in proj.get("tweets", []):
            if not isinstance(tweet, dict):
                continue
            url = str(
                tweet.get("tagai_import_url")
                or tweet.get("import_url")
                or tweet.get("url")
                or ""
            ).strip()
            if not url:
                continue
            text = str(tweet.get("text") or "")[:80]
            if url not in seen:
                seen.add(url)
                results.append({
                    "url": url,
                    "source": f"project @{handle}" if handle else "project_intel",
                    "text": text,
                })
                continue
            for item in results:
                if item["url"] == url:
                    item["source"] = f"project @{handle}" if handle else item["source"]
                    item["text"] = text or item["text"]
                    break

    return results


def import_tweet(tweet_url: str, config: dict[str, str] | None = None) -> dict[str, Any]:
    """Call POST /curation/importTweet for one tweet URL."""
    cfg = config or load_config()
    base_url = str(cfg.get("baseUrl") or "").rstrip("/")
    url = f"{base_url}/curation/importTweet"
    body = json.dumps({
        "tweetUrl": tweet_url,
        "twitterId": cfg.get("twitterId") or "",
        "pwd": cfg.get("pwd") or "",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "status": resp.status,
                "ok": True,
                "data": data,
                "error": None,
            }
    except urllib.error.HTTPError as e:
        body_raw = e.read().decode("utf-8", errors="replace")
        try:
            body_json = json.loads(body_raw)
        except json.JSONDecodeError:
            body_json = {"raw": body_raw[:500]}
        return {
            "status": e.code,
            "ok": False,
            "data": body_json,
            "error": f"HTTP {e.code}",
        }
    except Exception as e:
        return {
            "status": 0,
            "ok": False,
            "data": None,
            "error": str(e),
        }


def _parse_tweet_id_from_url(url: str) -> str:
    """Extract tweet numeric ID from an x.com/twitter.com status URL."""
    m = _TWEET_ID_FROM_URL_RE.search(url or "")
    return m.group(1) if m else ""


def _normalize_created_at(created_at: str) -> str:
    """Convert various Twitter date formats to ISO 8601 with milliseconds.

    Handles:
      - Already-ISO: ``2026-05-23T18:05:59Z`` / ``2026-05-23T18:05:59.000Z``
      - Twitter v1 string: ``Sat May 23 18:05:59 +0000 2026``
    Returns the input unchanged if parsing fails (prevents silent data loss).
    """
    s = (created_at or "").strip()
    if not s:
        return ""
    # Already ISO-8601?
    if "T" in s:
        if s.endswith("Z") and "." not in s:
            return s[:-1] + ".000Z"
        return s
    # Twitter v1 API format
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S +0000 %Y")
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except ValueError:
        pass
    return s


def _fetch_thread_replies(tweet_url: str) -> tuple[list[dict], list[dict]]:
    """Fetch thread replies via ``bird thread <url> --json-full``.

    Returns ``(replies, reply_authors)`` where:
      replies      — list of ``{tweetId, authorId, text, createdAt}``
      reply_authors — list of ``{id, name, username, profileImageUrl,
                       followersCount, followingCount, tweetCount,
                       likeCount, listedCount}``

    Returns ``([], [])`` on any error or if bird is unavailable.
    """
    try:
        proc = subprocess.run(
            ["bird", "thread", tweet_url, "--json-full"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw_json = proc.stdout.strip()
        if not raw_json:
            return [], []
        thread = json.loads(raw_json)
    except Exception:
        return [], []

    if not isinstance(thread, list) or len(thread) < 2:
        return [], []

    replies: list[dict] = []
    reply_authors: list[dict] = []
    seen_author_ids: set[str] = set()

    for item in thread[1:]:  # item 0 is the original tweet
        if not isinstance(item, dict):
            continue
        tweet_id = str(item.get("id") or "").strip()
        author_id = str(item.get("authorId") or "").strip()
        text = str(item.get("text") or "")
        created_at = _normalize_created_at(str(item.get("createdAt") or ""))

        if not tweet_id:
            continue

        replies.append({
            "tweetId": tweet_id,
            "authorId": author_id,
            "text": text,
            "createdAt": created_at,
        })

        if author_id and author_id not in seen_author_ids:
            seen_author_ids.add(author_id)
            author_obj = item.get("author") or {}
            raw = item.get("_raw") or {}
            user_result = (raw.get("core") or {}).get("user_results", {}).get("result", {})
            legacy = user_result.get("legacy") or {}
            avatar = user_result.get("avatar") or {}
            user_rest_id = str(user_result.get("rest_id") or "").strip()
            reply_authors.append({
                "id": user_rest_id or author_id,
                "name": str(author_obj.get("name") or ""),
                "username": str(author_obj.get("username") or ""),
                "profileImageUrl": str(avatar.get("image_url") or ""),
                "followersCount": int(legacy.get("followers_count") or 0),
                "followingCount": int(legacy.get("friends_count") or 0),
                "tweetCount": int(legacy.get("statuses_count") or 0),
                "likeCount": int(legacy.get("favourites_count") or 0),
                "listedCount": int(legacy.get("listed_count") or 0),
            })

    return replies, reply_authors


def _build_full_tweet_payload(
    tweet_summary: dict[str, Any],
    config: dict[str, str],
) -> dict[str, Any]:
    """Build the full-tweet payload for TagAI from a brief tweet summary.

    The brief summary carries: text, author (username), author_followers,
    likes, retweets, replies (COUNT), created_at, url/tagai_import_url.

    When the brief was generated with Option-A fix (run_trader_social_brief.py
    _build_raw_tweet_fields), the summary also carries a ``_raw`` sub-object
    with tweetId, authorId, conversationId, createdAt, and full author profile.
    Those fields fill the previously-documented gaps.

    Fields still absent are set to empty string / 0 and annotated in the
    ``_data_gaps`` field so callers can detect remaining blockers.
    """
    url = str(
        tweet_summary.get("tagai_import_url")
        or tweet_summary.get("url")
        or tweet_summary.get("source_url")
        or ""
    )
    tweet_id = _parse_tweet_id_from_url(url)
    author_username = str(tweet_summary.get("author") or "")
    author_followers = int(tweet_summary.get("author_followers") or 0)
    tweet_likes = int(tweet_summary.get("likes") or 0)
    reply_count = int(tweet_summary.get("replies") or 0)
    created_at = _normalize_created_at(str(tweet_summary.get("created_at") or ""))

    # Read Option-A enrichment fields from _raw sub-object when present
    raw = tweet_summary.get("_raw") if isinstance(tweet_summary.get("_raw"), dict) else {}
    raw_author = raw.get("author") if isinstance(raw.get("author"), dict) else {}

    raw_tweet_id = str(raw.get("tweetId") or "").strip()
    raw_author_id = str(raw.get("authorId") or "").strip()
    raw_conversation_id = str(raw.get("conversationId") or "").strip()
    raw_created_at_str = str(raw.get("createdAt") or "").strip()
    raw_author_id_field = str(raw_author.get("id") or "").strip()
    raw_author_name = str(raw_author.get("name") or "").strip()
    raw_author_username = str(raw_author.get("username") or "").strip()
    raw_profile_image = str(raw_author.get("profileImageUrl") or "").strip()
    raw_following_count = raw_author.get("followingCount")
    raw_tweet_count = raw_author.get("tweetCount")
    raw_listed_count = raw_author.get("listedCount")

    # Prefer _raw values over URL-parsed fallbacks
    effective_tweet_id = raw_tweet_id or tweet_id
    effective_author_id = raw_author_id or ""
    effective_conversation_id = raw_conversation_id or effective_tweet_id
    effective_created_at = _normalize_created_at(raw_created_at_str) if raw_created_at_str else created_at
    effective_author_id_field = raw_author_id_field or ""
    effective_author_name = raw_author_name or author_username
    effective_author_username = raw_author_username or author_username
    effective_profile_image = raw_profile_image or ""
    effective_following_count = int(raw_following_count) if isinstance(raw_following_count, (int, float)) else 0
    effective_tweet_count = int(raw_tweet_count) if isinstance(raw_tweet_count, (int, float)) else 0
    effective_listed_count = int(raw_listed_count) if isinstance(raw_listed_count, (int, float)) else 0

    # Build remaining gaps list (skip gaps filled by _raw)
    gaps: list[str] = []
    if not effective_tweet_id:
        gaps.append("tweet.tweetId — could not parse from URL")
    if not effective_author_id:
        gaps.append("tweet.authorId — not stored in brief summary (only username is)")
    if not raw_conversation_id and effective_conversation_id == effective_tweet_id:
        gaps.append("tweet.conversationId — not stored in brief summary (using tweetId as fallback)")
    if not effective_author_id_field:
        gaps.append("tweetAuthor.id — not stored in brief summary (only username is)")
    if not effective_profile_image:
        gaps.append("tweetAuthor.profileImageUrl — not stored in brief summary")
    if not effective_following_count:
        gaps.append("tweetAuthor.followingCount — not stored in brief summary")
    if not effective_tweet_count:
        gaps.append("tweetAuthor.tweetCount — not stored in brief summary")
    if not effective_listed_count:
        gaps.append("tweetAuthor.listedCount — not stored in brief summary")
    if reply_count > 0:
        gaps.append("replies[] — reply_count>0 but thread fetch skipped; use --no-replies to skip thread fetch")
        gaps.append("replyAuthors[] — reply_count>0 but thread fetch skipped; use --no-replies to skip thread fetch")
    # reply_count == 0 → replies=[] is correct, no gap

    # Prefer _raw.fullText (RT-resolved + note_tweet-resolved complete text)
    # over tweet_summary.text which may be truncated at 280 chars.
    raw_full_text = str(raw.get("fullText") or "").strip()
    effective_text = raw_full_text or str(tweet_summary.get("text") or "")

    payload: dict[str, Any] = {
        "twitterId": config.get("twitterId") or "",
        "pwd": config.get("pwd") or "",
        "tweet": {
            "tweetId": effective_tweet_id,
            "authorId": effective_author_id,
            "text": effective_text,
            "createdAt": effective_created_at,
            "conversationId": effective_conversation_id,
        },
        "tweetAuthor": {
            "id": effective_author_id_field,
            "name": effective_author_name,
            "username": effective_author_username,
            "profileImageUrl": effective_profile_image,
            "followersCount": author_followers,
            "followingCount": effective_following_count,
            "tweetCount": effective_tweet_count,
            "likeCount": tweet_likes,
            "listedCount": effective_listed_count,
        },
        "replies": [],      # GAP: brief stores reply COUNT only
        "replyAuthors": [], # GAP: brief stores reply COUNT only
        "_data_gaps": gaps,
        "_reply_count_available": reply_count,
        "_source_url": url,
    }
    return payload


def import_full_tweet(
    tweet_data: dict[str, Any],
    author_data: dict[str, Any],
    replies: list[dict[str, Any]],
    reply_authors: list[dict[str, Any]],
    config: dict[str, str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """POST a full tweet payload to TagAI.

    Tries ``POST /curation/importTweet`` with the full body first.
    Falls back to ``POST /curation/importFullTweet`` on 404/405.

    Parameters match the TagAI full-tweet schema:
      tweet_data   — {tweetId, authorId, text, createdAt, conversationId}
      author_data  — {id, name, username, profileImageUrl, followersCount, ...}
      replies      — list of {tweetId, authorId, text, createdAt}
      reply_authors — list of author objects matching reply authorIds
      tags         — optional list of hashtag strings to pin on the tweet, e.g.
                     ``["#BUIDL", "#BASE", "#SOL"]``. ``#BUIDL`` (primary, derived
                     from ``pwd="buidl-ai"``) is conventionally first; cashtag
                     subtags from the social brief follow. Omitted from the body
                     if None — preserves backward compat with callers that don't
                     yet supply tags.
    """
    cfg = config or load_config()
    base_url = str(cfg.get("baseUrl") or "").rstrip("/")

    body_dict: dict[str, Any] = {
        "twitterId": cfg.get("twitterId") or "",
        "pwd": cfg.get("pwd") or "",
        "tweet": tweet_data,
        "tweetAuthor": author_data,
        "replies": replies,
        "replyAuthors": reply_authors,
    }
    if tags is not None:
        body_dict["tags"] = tags
    body = json.dumps(body_dict).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    endpoints = [
        f"{base_url}/curation/importTweet",
        f"{base_url}/curation/importFullTweet",
    ]
    for endpoint in endpoints:
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with opener.open(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "status": resp.status,
                    "ok": True,
                    "data": data,
                    "error": None,
                    "endpoint": endpoint,
                }
        except urllib.error.HTTPError as e:
            if e.code in (404, 405) and endpoint != endpoints[-1]:
                continue  # try next endpoint
            body_raw = e.read().decode("utf-8", errors="replace")
            try:
                body_json = json.loads(body_raw)
            except json.JSONDecodeError:
                body_json = {"raw": body_raw[:500]}
            return {
                "status": e.code,
                "ok": False,
                "data": body_json,
                "error": f"HTTP {e.code}",
                "endpoint": endpoint,
            }
        except Exception as exc:
            return {
                "status": 0,
                "ok": False,
                "data": None,
                "error": str(exc),
                "endpoint": endpoint,
            }
    # Should not reach here
    return {"status": 0, "ok": False, "data": None, "error": "no endpoints tried", "endpoint": ""}


def import_brief_tweets_full(
    brief: dict[str, Any],
    *,
    config: dict[str, str] | None = None,
    dry_run: bool = False,
    fetch_replies: bool = True,
) -> dict[str, Any]:
    """Extract all tweets from a brief and import each via import_full_tweet.

    Covers both project_intel tweets and social_search (cashtag/hashtag) tweets.
    Each tweet is built via _build_full_tweet_payload from its brief summary.

    When ``fetch_replies=True`` (default), tweets with reply_count > 0 trigger
    a ``bird thread <url> --json-full`` call to populate replies[] and
    replyAuthors[].  Pass ``fetch_replies=False`` to skip thread fetches.

    Returns a results dict with data-gap warnings documented.
    """
    cfg = config or load_config()
    all_tweets: list[dict[str, Any]] = []

    # Collect from project_intel
    for proj in brief.get("project_intel", []):
        if not isinstance(proj, dict):
            continue
        handle = proj.get("username") or ""
        for t in proj.get("tweets", []):
            if isinstance(t, dict):
                t["_source"] = f"project_intel/@{handle}" if handle else "project_intel"
                all_tweets.append(t)

    # Collect from social_search.cashtag_search
    ss = brief.get("social_search") or {}
    for ticker, tweets in (ss.get("cashtag_search") or {}).items():
        for t in (tweets or []):
            if isinstance(t, dict):
                t["_source"] = f"cashtag/${ticker}"
                all_tweets.append(t)

    # Collect from social_search.hashtag_search
    for tag, tweets in (ss.get("hashtag_search") or {}).items():
        for t in (tweets or []):
            if isinstance(t, dict):
                t["_source"] = f"hashtag/#{tag}"
                all_tweets.append(t)

    # Deduplicate by URL, but collect EVERY source a duplicate URL was seen under
    # (so a tweet matching both $BASE and $SOL cashtag searches keeps both — and
    # both surface as #BASE + #SOL secondary tags on the TagAI import).
    url_to_tweet: dict[str, dict[str, Any]] = {}
    url_to_sources: dict[str, list[str]] = {}
    no_url_tweets: list[dict[str, Any]] = []
    for t in all_tweets:
        url = str(t.get("tagai_import_url") or t.get("url") or "")
        src = t.get("_source") or ""
        if not url:
            no_url_tweets.append(t)
            continue
        if url not in url_to_tweet:
            url_to_tweet[url] = t
            url_to_sources[url] = []
        if src and src not in url_to_sources[url]:
            url_to_sources[url].append(src)
    for url, t in url_to_tweet.items():
        t["_sources"] = url_to_sources[url]  # list — union of every brief section it appeared in
    unique_tweets: list[dict[str, Any]] = list(url_to_tweet.values()) + no_url_tweets

    results: list[dict[str, Any]] = []
    ok_count = 0
    fail_count = 0

    def _tags_for(tweet_obj: dict[str, Any]) -> list[str]:
        """#BUIDL primary (matches pwd='buidl-ai') + any cashtag/hashtag the brief
        attributed this tweet to, in stable first-seen order. project_intel / KOL
        tweets carry only ``["#BUIDL"]``."""
        tags = ["#BUIDL"]
        srcs = tweet_obj.get("_sources") or ([tweet_obj.get("_source")] if tweet_obj.get("_source") else [])
        for s in srcs:
            if not s:
                continue
            if s.startswith("cashtag/$"):
                ht = "#" + s[len("cashtag/$"):]
            elif s.startswith("hashtag/#"):
                ht = "#" + s[len("hashtag/#"):]
            else:
                continue
            if ht and ht not in tags:
                tags.append(ht)
        return tags

    for t in unique_tweets:
        payload = _build_full_tweet_payload(t, cfg)
        source = t.get("_source", "unknown")
        sources_all = t.get("_sources") or [source]
        tags = _tags_for(t)
        url = payload.get("_source_url") or ""
        gaps = payload.pop("_data_gaps", [])
        reply_count_hint = payload.pop("_reply_count_available", 0)
        payload.pop("_source_url", None)

        # Thread fetch: populate replies[] / replyAuthors[] when reply_count > 0
        thread_fetched = False
        if fetch_replies and reply_count_hint > 0 and url:
            fetched_replies, fetched_authors = _fetch_thread_replies(url)
            if fetched_replies:
                payload["replies"] = fetched_replies
                payload["replyAuthors"] = fetched_authors
                # Remove the now-filled gap entries
                gaps = [
                    g for g in gaps
                    if not g.startswith("replies[]") and not g.startswith("replyAuthors[]")
                ]
                thread_fetched = True

        if dry_run:
            result = {
                "status": 0, "ok": True, "data": None, "error": None,
                "dry_run": True, "thread_fetched": thread_fetched,
            }
            ok_count += 1
        else:
            result = import_full_tweet(
                tweet_data=payload["tweet"],
                author_data=payload["tweetAuthor"],
                replies=payload["replies"],
                reply_authors=payload["replyAuthors"],
                config=cfg,
                tags=tags,
            )
            result["thread_fetched"] = thread_fetched
            if result.get("ok") and int(result.get("status") or 0) == 200:
                ok_count += 1
            elif "already imported" in str((result.get("data") or {}).get("error", "")):
                ok_count += 1
            else:
                fail_count += 1

        results.append({
            "url": url,
            "source": source,
            "sources": sources_all,
            "tags": tags,
            "text": str(t.get("text") or "")[:80],
            "tweet_id": payload["tweet"]["tweetId"],
            "author": payload["tweetAuthor"]["username"],
            "data_gaps": gaps,
            "reply_count_hint": reply_count_hint,
            "thread_fetched": thread_fetched,
            "replies_count": len(payload["replies"]),
            "result": result,
        })

    return {
        "imported_at": now_str(),
        "config_twitterId": cfg.get("twitterId") or "",
        "base_url": cfg.get("baseUrl") or "",
        "total": len(unique_tweets),
        "ok": ok_count,
        "fail": fail_count,
        "fetch_replies": fetch_replies,
        "data_gap_warning": (
            "Brief summaries lack authorId, conversationId, and full author profile. "
            "replies[] and replyAuthors[] are auto-filled via bird thread fetch for "
            "tweets with reply_count > 0 (pass --no-replies to skip). "
            "See per-result data_gaps for remaining gaps."
        ),
        "results": results,
    }


def import_brief_tweets(
    brief: dict[str, Any],
    *,
    config: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import every tweet linked in a brief into TagAI."""
    cfg = config or load_config()
    tweets = extract_tweet_urls(brief)
    results: list[dict[str, Any]] = []
    ok_count = 0
    fail_count = 0

    for item in tweets:
        if dry_run:
            result = {"status": 0, "ok": True, "data": None, "error": None, "dry_run": True}
            ok_count += 1
        else:
            result = import_tweet(item["url"], cfg)
            status = int(result.get("status") or 0)
            data = result.get("data")
            if result.get("ok") and status == 200:
                ok_count += 1
            elif isinstance(data, dict) and "already imported" in str(data.get("error", "")):
                ok_count += 1
            else:
                fail_count += 1
        results.append({**item, "result": result})

    return {
        "imported_at": now_str(),
        "config_twitterId": cfg.get("twitterId") or "",
        "base_url": cfg.get("baseUrl") or "",
        "total": len(tweets),
        "ok": ok_count,
        "fail": fail_count,
        "results": results,
    }

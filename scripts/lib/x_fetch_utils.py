#!/usr/bin/env python3
"""x_fetch_utils.py — zero-credential X fetch/discovery helpers.

This module provides a pragmatic bootstrap path for guided X sync:
- prefer a browser-guided URL manifest if one exists
- otherwise fall back to public RSS discovery for authored tweets
- fetch per-tweet structured content via FxTwitter-style API

The browser-guided manifest keeps the canonical path compatible with future
OpenClaw browser/chirp integration, while the RSS fallback makes the bootstrap
usable today without requiring X API keys or manual cookie extraction.
"""

from __future__ import annotations

import email.utils
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

USER_AGENT = 'Mozilla/5.0 (self-ip-agency guided-x-sync)'
NITTER_BASE = 'https://nitter.net'
FXTWITTER_BASE = 'https://api.fxtwitter.com'
GUIDED_MANIFEST_RELPATH = Path('runtime/shared/guided-x-urls.json')


@dataclass
class DiscoveredUrl:
    url: str
    published_at: str | None = None
    title: str | None = None
    source: str = 'unknown'
    is_reply_hint: bool = False


class FetchError(RuntimeError):
    pass


def http_get_text(url: str, timeout: int = 20, retries: int = 2) -> str:
    last_error: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', 'ignore')
        except Exception as e:  # best-effort retry for flaky public endpoints
            last_error = e
    assert last_error is not None
    raise last_error


def http_get_json(url: str, timeout: int = 20) -> dict[str, Any]:
    text = http_get_text(url, timeout=timeout)
    return json.loads(text)


def parse_tweet_url(url: str) -> tuple[str, str]:
    match = re.search(r'(?:x\.com|twitter\.com|nitter\.net)/([A-Za-z0-9_]+)/status/(\d+)', url)
    if not match:
        raise ValueError(f'Cannot parse tweet URL: {url}')
    return match.group(1), match.group(2)


def normalize_status_url(url: str) -> str:
    username, tweet_id = parse_tweet_url(url)
    return f'https://x.com/{username}/status/{tweet_id}'


def rss_item_to_discovered(item: ET.Element, default_source: str) -> DiscoveredUrl | None:
    link = item.findtext('link')
    if not link:
        return None
    link = normalize_status_url(link.replace('#m', ''))
    title = item.findtext('title') or ''
    pub_date = item.findtext('pubDate')
    published_at = None
    if pub_date:
        try:
            dt = email.utils.parsedate_to_datetime(pub_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            published_at = dt.astimezone(timezone.utc).isoformat(timespec='seconds')
        except Exception:
            published_at = None
    text = (title or '').strip()
    is_reply_hint = text.startswith('@')
    return DiscoveredUrl(
        url=link,
        published_at=published_at,
        title=title,
        source=default_source,
        is_reply_hint=is_reply_hint,
    )


def discover_urls_from_rss(handle: str, lookback_days: int = 3, include_replies: bool = True) -> tuple[list[DiscoveredUrl], list[str]]:
    warnings: list[str] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    discovered: dict[str, DiscoveredUrl] = {}
    rss_paths = [f'/{handle}/rss']
    if include_replies:
        rss_paths.append(f'/{handle}/with_replies/rss')

    for path in rss_paths:
        url = f'{NITTER_BASE}{path}'
        try:
            text = http_get_text(url)
            root = ET.fromstring(text)
            for item in root.findall('./channel/item'):
                d = rss_item_to_discovered(item, default_source=f'nitter:{path}')
                if not d:
                    continue
                if d.published_at:
                    try:
                        dt = datetime.fromisoformat(d.published_at)
                        if dt < cutoff:
                            continue
                    except Exception:
                        pass
                discovered.setdefault(d.url, d)
        except urllib.error.HTTPError as e:
            warnings.append(f'RSS fetch failed for {path}: HTTP {e.code}')
        except Exception as e:
            warnings.append(f'RSS fetch failed for {path}: {e}')

    items = sorted(
        discovered.values(),
        key=lambda d: d.published_at or '',
        reverse=True,
    )
    return items, warnings


def load_guided_manifest(workspace: Path) -> tuple[list[DiscoveredUrl], list[str]]:
    manifest_path = workspace / GUIDED_MANIFEST_RELPATH
    if not manifest_path.exists():
        return [], [f'guided manifest not found: {manifest_path}']
    try:
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception as e:
        return [], [f'invalid guided manifest: {e}']

    urls = data.get('urls') or data.get('tweet_urls') or []
    out: list[DiscoveredUrl] = []
    for entry in urls:
        if isinstance(entry, str):
            out.append(DiscoveredUrl(url=normalize_status_url(entry), source='guided-manifest'))
        elif isinstance(entry, dict) and entry.get('url'):
            out.append(DiscoveredUrl(
                url=normalize_status_url(str(entry['url'])),
                published_at=entry.get('published_at'),
                title=entry.get('title'),
                source='guided-manifest',
                is_reply_hint=bool(entry.get('is_reply_hint', False)),
            ))
    return out, []


def discover_tweet_urls(workspace: Path, handle: str, lookback_days: int = 3, include_replies: bool = True) -> tuple[list[DiscoveredUrl], dict[str, Any]]:
    guided, guided_warnings = load_guided_manifest(workspace)
    if guided:
        return guided, {
            'provider': 'browser-guided',
            'warnings': guided_warnings,
            'guided_manifest_path': str(workspace / GUIDED_MANIFEST_RELPATH),
        }

    rss_items, rss_warnings = discover_urls_from_rss(handle, lookback_days=lookback_days, include_replies=include_replies)
    return rss_items, {
        'provider': 'public-rss',
        'warnings': guided_warnings + rss_warnings,
        'guided_manifest_path': str(workspace / GUIDED_MANIFEST_RELPATH),
    }


def fetch_tweet_via_fxtwitter(url: str) -> dict[str, Any]:
    username, tweet_id = parse_tweet_url(url)
    api_url = f'{FXTWITTER_BASE}/{username}/status/{tweet_id}'
    try:
        data = http_get_json(api_url, timeout=20)
    except Exception as e:
        raise FetchError(f'FxTwitter fetch failed for {url}: {e}') from e

    if data.get('code') != 200 or 'tweet' not in data:
        raise FetchError(f'FxTwitter returned code={data.get("code")} message={data.get("message", "unknown")}')
    return data['tweet']


def normalize_fxtwitter_tweet(url: str, tweet: dict[str, Any], discovery_source: str, lookback_days: int, reply_hint: bool = False) -> dict[str, Any]:
    username, tweet_id = parse_tweet_url(url)
    created_at = tweet.get('created_at') or ''
    quote = tweet.get('quote') or {}
    author = tweet.get('author') or {}
    text = tweet.get('text') or ''
    is_reply = bool(reply_hint or re.match(r'^@\w+', text.strip()))
    article = tweet.get('article') or {}
    normalized: dict[str, Any] = {
        'schema': 'raw-x-tweet.v1',
        'tweet_id': tweet_id,
        'author_handle': author.get('screen_name') or username,
        'author_name': author.get('name') or '',
        'created_at': created_at,
        'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'kind': 'reply' if is_reply else 'tweet',
        'is_reply': is_reply,
        'conversation_id': tweet.get('conversation_id') or tweet_id,
        'in_reply_to_tweet_id': tweet.get('replying_to') or None,
        'text': text,
        'url': normalize_status_url(url),
        'lang': tweet.get('lang') or '',
        'is_note_tweet': bool(tweet.get('is_note_tweet', False)),
        'is_article': bool(article),
        'article': {
            'title': article.get('title') or '',
            'preview_text': article.get('preview_text') or '',
        } if article else None,
        'quote': {
            'text': quote.get('text') or '',
            'author_handle': (quote.get('author') or {}).get('screen_name') or '',
            'author_name': (quote.get('author') or {}).get('name') or '',
        } if quote else None,
        'source': {
            'provider': 'browser+fxtwitter' if discovery_source == 'browser-guided' else 'public-rss+fxtwitter',
            'discovery': discovery_source,
            'content_fetch': 'x-tweet-fetcher-compatible',
            'query_window_days': int(lookback_days),
        },
        'engagement': {
            'likes': int(tweet.get('likes') or 0),
            'retweets': int(tweet.get('retweets') or 0),
            'bookmarks': int(tweet.get('bookmarks') or 0),
            'views': int(tweet.get('views') or 0),
            'replies': int(tweet.get('replies') or 0),
        },
    }
    return normalized


def collect_concept_matches(text: str, alias_map: dict[str, str]) -> list[str]:
    hay = text.lower()
    matches: set[str] = set()
    for alias, canonical in alias_map.items():
        alias_l = alias.lower()
        if len(alias_l) < 3:
            continue
        if alias_l in hay:
            matches.add(canonical)
    return sorted(matches)

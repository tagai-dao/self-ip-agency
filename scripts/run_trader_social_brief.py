#!/usr/bin/env python3
"""run_trader_social_brief.py — Trader social + trading brief module.

Implements R1-R6 from task trader-social-trading-brief-20260518:
  R1: Hourly fetch of @btcbabycow tweets
  R2: Extract $Cashtags and token contract addresses from tweets
  R3: gmgn-cli token info / traders (smart money) → trading brief
  R4: Track project Twitter accounts via quoted tweets + last 3 days tweets
  R5: Search $Cashtags and #Hashtags for trending tweets
  R6: Merge social+trading brief → send via trader agent message channel

CLI priority: bird > xurl (NOTE1). Forbidden: xactions.
gmgn-cli requires IPv4 (NOTE2). Rate: 10 req/s, holders/traders weight=5.

Usage:
    python3 scripts/run_trader_social_brief.py
    python3 scripts/run_trader_social_brief.py --dry-run
    python3 scripts/run_trader_social_brief.py --target-user btcbabycow
    python3 scripts/run_trader_social_brief.py --self-check
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))
RUNTIME_TRADER = WORKSPACE / "runtime" / "trader"
RUNTIME_SHARED = WORKSPACE / "runtime" / "shared"
SOCIAL_BRIEF_BIRD_AUTH_FILE = WORKSPACE / "runtime" / "credentials" / "social-brief-bird.json"

TARGET_USERS: list[str] = ["VitalikButerin", "0xLuo", "basedsnipez", "basezh"]  # R1 target users
TWEET_FETCH_LIMIT = 20      # max tweets to fetch per run per user
SEARCH_RESULTS_LIMIT = 10   # max search results per cashtag/hashtag
GMGN_RATE_DELAY = 0.15      # seconds between gmgn-cli calls (rate=10 cap=10)

# Delta-since-last-brief state file (2026-05-19): records the last successful
# brief's generated_at + the set of source tweet IDs that brief consumed.
# Subsequent runs filter out tweets we already covered. Prevents the
# "every hourly brief is 95% the same content" issue.
LAST_BRIEF_STATE_PATH = RUNTIME_TRADER / "LAST_BRIEF_STATE.json"
DELTA_MAX_LOOKBACK_HOURS = 24   # never consider tweets older than this
DELTA_MIN_LOOKBACK_HOURS = 1    # never consider tweets newer than (now - this)?  No — keep recent tweets in. Used as minimum window when state file is empty/fresh.

DRY_RUN = "--dry-run" in sys.argv
SELF_CHECK = "--self-check" in sys.argv

# Parse --target-user overrides (append multiple)
_user_overrides: list[str] = []
for _i, _a in enumerate(sys.argv):
    if _a == "--target-user" and _i + 1 < len(sys.argv):
        _user_overrides.append(sys.argv[_i + 1])
if _user_overrides:
    TARGET_USERS = _user_overrides

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent),
                                    suffix=".tmp", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = f.name
    os.replace(tmp, path)


def read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_social_brief_bird_auth() -> dict[str, Any]:
    data = read_json(SOCIAL_BRIEF_BIRD_AUTH_FILE)
    return data if isinstance(data, dict) else {}


SOCIAL_BRIEF_BIRD_AUTH = load_social_brief_bird_auth()


def resolve_social_brief_bird() -> tuple[list[str], str]:
    """Resolve bird auth for this brief only.

    Precedence:
      1. SOCIAL_BRIEF_BIRD_* env vars
      2. shared X_BIRD_* / BIRD_* env vars
      3. runtime/credentials/social-brief-bird.json
    """
    cfg = SOCIAL_BRIEF_BIRD_AUTH if isinstance(SOCIAL_BRIEF_BIRD_AUTH, dict) else {}
    auth_token = (
        os.environ.get("SOCIAL_BRIEF_BIRD_AUTH_TOKEN")
        or os.environ.get("X_BIRD_AUTH_TOKEN")
        or os.environ.get("BIRD_AUTH_TOKEN")
        or cfg.get("auth_token")
    )
    ct0 = (
        os.environ.get("SOCIAL_BRIEF_BIRD_CT0")
        or os.environ.get("X_BIRD_CT0")
        or os.environ.get("BIRD_CT0")
        or cfg.get("ct0")
    )
    profile = (
        os.environ.get("SOCIAL_BRIEF_BIRD_CHROME_PROFILE")
        or os.environ.get("X_BIRD_CHROME_PROFILE")
        or os.environ.get("BIRD_CHROME_PROFILE")
        or cfg.get("chrome_profile")
        or "Default"
    )

    base = ["bird", "--plain", "--no-color"]
    if auth_token and ct0:
        return base + ["--auth-token", str(auth_token), "--ct0", str(ct0)], "explicit"
    return base + ["--chrome-profile", str(profile)], "chrome-profile"


SOCIAL_BRIEF_BIRD_ARGS, SOCIAL_BRIEF_BIRD_AUTH_MODE = resolve_social_brief_bird()


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run command, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s or "")


def _run_bird(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run bird with social-brief-scoped credentials only."""
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    try:
        r = subprocess.run(
            SOCIAL_BRIEF_BIRD_ARGS + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return r.returncode, _strip_ansi(r.stdout).strip(), _strip_ansi(r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return -1, "", "command not found: bird"
    except Exception as e:
        return -1, "", str(e)


# ---------------------------------------------------------------------------
# R1: Fetch @btcbabycow tweets (bird > xurl)
# ---------------------------------------------------------------------------


def _parse_bird_tweets(out: str) -> list[dict]:
    """Parse bird's stdout into a list of tweet dicts. Tolerates: plain JSON list,
    wrapper dict, or line-delimited JSON objects."""
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, AttributeError):
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("tweets", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    # Fall back to line-delimited JSON.
    tweets = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                tweets.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return tweets


def fetch_user_tweets_bird(username: str, limit: int = TWEET_FETCH_LIMIT) -> list[dict]:
    """Fetch user tweets via bird CLI, with retry on transient failures.

    The single-shot version of this function used to silently lose KOL data whenever
    bird hit a transient timeout/connection reset (very common against X's GraphQL
    endpoint). 3 attempts with backoff + a slightly longer timeout fix that — direct
    bird calls always succeed; the script's losses were almost entirely transient.
    --json-full embeds the GraphQL `_raw` payload (authorId, conversationId, etc.).
    """
    last_rc = -1
    last_err = ""
    for attempt in range(1, 4):
        rc, out, err = _run_bird(
            ["user-tweets", username, "-n", str(limit), "--json-full"], timeout=45
        )
        if rc == 0 and out:
            tweets = _parse_bird_tweets(out)
            if tweets:
                return tweets
        last_rc, last_err = rc, (err or "no output")
        if attempt < 3:
            time.sleep(1.5 * attempt)  # 1.5s, then 3.0s
    print(
        f"[social-brief] bird user-tweets @{username} failed after 3 attempts "
        f"(rc={last_rc}): {last_err}",
        file=sys.stderr,
    )
    return []


def fetch_tweet_xurl(tweet_url: str) -> dict | None:
    """Fetch single tweet via xurl. xurl read <url> (outputs JSON natively)."""
    rc, out, err = _run(["xurl", "read", tweet_url])
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw_text": out, "url": tweet_url}


def fetch_user_tweets_xurl(username: str, limit: int = TWEET_FETCH_LIMIT) -> list[dict]:
    """Fetch user timeline via xurl search.

    xurl search "from:<username>" -n <limit>  (outputs JSON natively)
    """
    # Clamp limit: xurl min=10, max=100
    n = max(10, min(limit, 100))
    rc, out, err = _run(["xurl", "search", f"from:{username}", "-n", str(n)])
    if rc != 0 or not out:
        print(f"[social-brief] xurl search fallback failed (rc={rc}): {err or 'no output'}", file=sys.stderr)
        return []
    try:
        data = json.loads(out)
        if isinstance(data, list):
            return data
        for key in ("tweets", "data", "results", "statuses"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    except json.JSONDecodeError:
        return []


def fetch_user_tweets(username: str, limit: int = TWEET_FETCH_LIMIT) -> list[dict]:
    """Fetch user tweets. Priority: bird > xurl."""
    print(f"[social-brief] Fetching @{username} tweets (limit={limit})...")

    # Check bird availability
    rc, _, _ = _run(["bird", "--version"])
    if rc == 0:
        tweets = fetch_user_tweets_bird(username, limit)
        if tweets:
            print(f"[social-brief] bird: {len(tweets)} tweets from @{username}")
            return tweets

    # xurl fallback
    tweets = fetch_user_tweets_xurl(username, limit)
    if tweets:
        print(f"[social-brief] xurl fallback: {len(tweets)} tweets from @{username}")
        return tweets

    print(f"[social-brief] No tweets fetched for @{username} (both bird and xurl failed)")
    return []


# ---------------------------------------------------------------------------
# R2: Parse $Cashtags and contract addresses
# ---------------------------------------------------------------------------

# BSC / ETH contract address pattern (0x + 40 hex chars)
_CONTRACT_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
# $TICKER pattern — 1-10 uppercase (or mixed) letters after $, not followed by digit
_CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{0,9})\b")
# #Hashtag pattern
_HASHTAG_RE = re.compile(r"#([A-Za-z][A-Za-z0-9_]{1,49})\b")
# Twitter handle inside x.com / twitter.com URLs (1–15 chars, alnum + underscore)
_TWITTER_URL_RE = re.compile(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})(?:[/?#]|$)")
# Reserved Twitter path segments that look like usernames but aren't.
_TWITTER_RESERVED = frozenset({
    "intent", "i", "home", "explore", "search", "status", "compose",
    "messages", "settings", "notifications", "share", "login", "signup",
})


def _extract_twitter_handle(value: Any) -> str | None:
    """Normalize a project link value into a bare Twitter username.

    Accepts a bare handle (`aeonframework`, `@aeonframework`), an x.com /
    twitter.com URL (`https://x.com/aeonframework`,
    `https://x.com/EzBruv/status/...?s=20`), or junk. Returns the handle
    without the leading `@`, or None if nothing usable.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "twitter.com" in s or "x.com" in s or "://" in s:
        m = _TWITTER_URL_RE.search(s)
        if not m:
            return None
        handle = m.group(1)
    else:
        handle = s.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
        return None
    if handle.lower() in _TWITTER_RESERVED:
        return None
    return handle


def _tweet_text(tweet: dict) -> str:
    """Extract text from various tweet dict shapes."""
    return (
        tweet.get("full_text") or tweet.get("text") or
        tweet.get("content") or tweet.get("body") or ""
    )


def extract_cashtags(tweets: list[dict]) -> list[str]:
    """Extract unique $CAShTAG tickers from tweet list (R2)."""
    seen: set[str] = set()
    result: list[str] = []
    for t in tweets:
        text = _tweet_text(t)
        for m in _CASHTAG_RE.finditer(text):
            ticker = m.group(1).upper()
            if ticker not in seen:
                seen.add(ticker)
                result.append(ticker)
    return result


def extract_contract_addresses(tweets: list[dict]) -> list[str]:
    """Extract unique contract addresses from tweet text (R2)."""
    seen: set[str] = set()
    result: list[str] = []
    for t in tweets:
        text = _tweet_text(t)
        for m in _CONTRACT_RE.finditer(text):
            addr = m.group(0).lower()
            if addr not in seen:
                seen.add(addr)
                result.append(addr)
    return result


def extract_hashtags(tweets: list[dict]) -> list[str]:
    """Extract unique #hashtags from tweets."""
    seen: set[str] = set()
    result: list[str] = []
    for t in tweets:
        text = _tweet_text(t)
        for m in _HASHTAG_RE.finditer(text):
            tag = m.group(1)
            if tag.lower() not in seen:
                seen.add(tag.lower())
                result.append(tag)
    return result


def load_onchain_ticks_addresses() -> dict[str, str]:
    """Load ticker→contract map from onchain-ticks.json (NOTE8)."""
    data = read_json(RUNTIME_TRADER / "onchain-ticks.json")
    if not isinstance(data, dict):
        return {}
    mapping: dict[str, str] = {}
    for tick in (data.get("ticks") or []):
        if isinstance(tick, dict):
            sym = (tick.get("tick") or tick.get("token_symbol") or "").upper()
            addr = (tick.get("contract") or tick.get("address") or "").lower()
            if sym and addr:
                mapping[sym] = addr
    return mapping


# ---------------------------------------------------------------------------
# R3: gmgn-cli integration — token info + smart money traders
# ---------------------------------------------------------------------------


def _gmgn(args: list[str], timeout: int = 20) -> dict | list | None:
    """Run gmgn-cli command, return parsed JSON or None."""
    cmd = ["gmgn-cli"] + args + ["--raw"]
    if DRY_RUN:
        print(f"[social-brief][DRY-RUN] gmgn-cli {' '.join(args)}", file=sys.stderr)
        return None
    rc, out, err = _run(cmd, timeout=timeout)
    if rc != 0 or not out:
        print(f"[social-brief] gmgn-cli {args[0]} failed (rc={rc}): {err or 'no output'}", file=sys.stderr)
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# Per-run cache of trending lists, keyed by chain. Populated lazily by
# _load_trending_chain(). Used to resolve $TICKER → (address, chain) when a
# cashtag is mentioned in tweets but the contract address is not.
_TRENDING_CACHE: dict[str, list[dict]] = {}
# Order matters: most current meme calls are on Base, with BSC and Sol as
# fallbacks. The resolver scans all chains and picks the highest-volume match.
_RESOLVER_CHAINS: tuple[str, ...] = ("base", "bsc", "sol")


def _load_trending_chain(chain: str) -> list[dict]:
    """Load and cache the gmgn trending list for one chain (24h, top 100 by volume)."""
    if chain in _TRENDING_CACHE:
        return _TRENDING_CACHE[chain]
    data = _gmgn(["market", "trending", "--chain", chain, "--interval", "24h",
                  "--limit", "100", "--order-by", "volume", "--direction", "desc"])
    time.sleep(GMGN_RATE_DELAY)
    rows: list[dict] = []
    if isinstance(data, dict):
        inner = data.get("data") or data
        if isinstance(inner, dict):
            rank = inner.get("rank")
            if isinstance(rank, list):
                rows = [r for r in rank if isinstance(r, dict)]
    _TRENDING_CACHE[chain] = rows
    return rows


def resolve_ticker_via_gmgn(ticker: str,
                             chains: tuple[str, ...] = _RESOLVER_CHAINS) -> tuple[str, str] | None:
    """Resolve $TICKER → (contract_address, chain) via gmgn trending lists.

    Scans the trending lists of each chain in `chains`, collects symbol matches
    (case-insensitive), and returns the (address, chain) of the most active
    match (highest 24h USD volume). Returns None if no match found.
    """
    target = ticker.lower()
    candidates: list[tuple[float, str, str]] = []  # (volume, address, chain)
    for chain in chains:
        for row in _load_trending_chain(chain):
            sym = str(row.get("symbol") or "").lower()
            if sym != target:
                continue
            addr = str(row.get("address") or "").strip()
            if not addr:
                continue
            try:
                vol = float(row.get("volume") or 0)
            except (TypeError, ValueError):
                vol = 0.0
            candidates.append((vol, addr, chain))
    if not candidates:
        return None
    candidates.sort(reverse=True)  # highest volume first
    _, addr, chain = candidates[0]
    return (addr, chain)


# Dexscreener fallback: catches tickers that aren't in gmgn's top-100 trending
# (e.g. dust meme tokens, just-bonded launches, anything outside gmgn's index).
# Endpoint is unauthenticated, ~300 req/min — well below our 6 cashtags/run.
_DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q={q}"
# Map dexscreener chainId → gmgn chain (so downstream token info/security/traders
# can still be fetched). Other chains are dropped because gmgn won't index them.
_DEXSCREENER_CHAIN_MAP: dict[str, str] = {"base": "base", "bsc": "bsc", "solana": "sol"}
# Caches keyed by lowercase ticker → (addr, chain) | None, and lowercase address
# → the best pair data (used to backfill price/cap/vol when gmgn token info has
# no data for a freshly-listed token).
_DEXSCREENER_TICKER_CACHE: dict[str, tuple[str, str] | None] = {}
_DEXSCREENER_PAIR_CACHE: dict[str, dict] = {}


def _dexscreener_search(ticker: str) -> list[dict]:
    """Raw GET /latest/dex/search?q=<ticker>. Returns pairs list or []."""
    url = _DEXSCREENER_SEARCH_URL.format(q=urllib.parse.quote(ticker))
    if DRY_RUN:
        print(f"[social-brief][DRY-RUN] GET {url}", file=sys.stderr)
        return []
    req = urllib.request.Request(url, headers={"User-Agent": "trader-social-brief/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, OSError) as e:
        print(f"[social-brief] dexscreener search ${ticker} failed: {e}", file=sys.stderr)
        return []
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    return [p for p in (pairs or []) if isinstance(p, dict)]


def resolve_ticker_via_dexscreener(ticker: str) -> tuple[str, str] | None:
    """Resolve $TICKER → (address, chain) via Dexscreener search.

    Same selection rule as the gmgn resolver: case-insensitive symbol match,
    pick the (address, chain) with the highest aggregated 24h USD volume
    (summed across all pairs for that token). Only returns tokens on chains
    gmgn can fetch info for (base/bsc/sol). Cached per-run by ticker.
    """
    key = ticker.lower()
    if key in _DEXSCREENER_TICKER_CACHE:
        return _DEXSCREENER_TICKER_CACHE[key]

    pairs = _dexscreener_search(ticker)
    # Aggregate h24 volume across pairs by (address, chain). One token can have
    # many pools (LFI/WETH, LFI/USDC, ...) on the same chain — we want token-level
    # volume, not pool-level.
    by_token: dict[tuple[str, str], float] = {}
    best_pair_for: dict[tuple[str, str], dict] = {}
    for p in pairs:
        base = p.get("baseToken") or {}
        if (base.get("symbol") or "").lower() != key:
            continue
        chain_raw = (p.get("chainId") or "").lower()
        chain = _DEXSCREENER_CHAIN_MAP.get(chain_raw)
        if chain is None:
            continue
        addr = str(base.get("address") or "").strip()
        if not addr:
            continue
        try:
            vol = float((p.get("volume") or {}).get("h24") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        tk = (addr, chain)
        by_token[tk] = by_token.get(tk, 0.0) + vol
        # Keep the pair with the highest per-pair volume for backfill data.
        prev = best_pair_for.get(tk)
        prev_vol = 0.0
        if prev:
            try:
                prev_vol = float((prev.get("volume") or {}).get("h24") or 0)
            except (TypeError, ValueError):
                pass
        if not prev or vol > prev_vol:
            best_pair_for[tk] = p

    if not by_token:
        _DEXSCREENER_TICKER_CACHE[key] = None
        return None

    (addr, chain), _ = max(by_token.items(), key=lambda kv: kv[1])
    result = (addr, chain)
    _DEXSCREENER_TICKER_CACHE[key] = result
    _DEXSCREENER_PAIR_CACHE[addr.lower()] = best_pair_for[(addr, chain)]
    return result


def _dexscreener_pair(address: str) -> dict | None:
    """Look up the cached dexscreener pair for an address (lower-cased lookup)."""
    return _DEXSCREENER_PAIR_CACHE.get(address.lower())


def _to_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def fetch_token_info(address: str, chain: str = "base") -> dict | None:
    """gmgn token info — price, market cap, volume (NOTE3, NOTE7 weight=1).

    gmgn returns `price` as a nested object with `volume_24h`, `price_24h`,
    etc. inside it, and has no `price_change_*` or `market_cap` field. This
    helper flattens the nested price object and derives % changes so the
    downstream brief renderer can read flat scalar fields. `market_cap` is
    sourced separately from the trending cache by build_trading_brief.
    """
    data = _gmgn(["token", "info", "--chain", chain, "--address", address])
    time.sleep(GMGN_RATE_DELAY)
    if not isinstance(data, dict):
        return None
    inner = data.get("data") or data
    if not isinstance(inner, dict):
        return None
    price_obj = inner.get("price") if isinstance(inner.get("price"), dict) else None
    if price_obj:
        current = _to_float(price_obj.get("price"))
        p1h = _to_float(price_obj.get("price_1h"))
        p24h = _to_float(price_obj.get("price_24h"))
        inner["price"] = current  # replace nested dict with scalar for renderer
        inner["volume_24h"] = _to_float(price_obj.get("volume_24h"))
        inner["volume_1h"] = _to_float(price_obj.get("volume_1h"))
        inner["buys_24h"] = price_obj.get("buys_24h") or 0
        inner["sells_24h"] = price_obj.get("sells_24h") or 0
        inner["swaps_24h"] = price_obj.get("swaps_24h") or 0
        if current > 0 and p1h > 0:
            inner["price_change_1h"] = (current - p1h) / p1h * 100
        if current > 0 and p24h > 0:
            inner["price_change_24h"] = (current - p24h) / p24h * 100
    return inner


def _trending_row(address: str, chain: str) -> dict | None:
    """Look up a token's row in the cached trending list for a given chain."""
    addr_lc = address.lower()
    for row in _TRENDING_CACHE.get(chain) or []:
        if str(row.get("address") or "").lower() == addr_lc:
            return row
    return None


def fetch_token_security(address: str, chain: str = "base") -> dict | None:
    """gmgn token security — honeypot, tax, ownership risks (weight=1)."""
    data = _gmgn(["token", "security", "--chain", chain, "--address", address])
    time.sleep(GMGN_RATE_DELAY)
    if not isinstance(data, dict):
        return None
    inner = data.get("data") or data
    return inner if isinstance(inner, dict) else data


def fetch_token_traders(address: str, chain: str = "base", limit: int = 10) -> list[dict]:
    """gmgn token traders — smart money wallets (NOTE7 weight=5, use sparingly).

    Response shape: {"data": {"list": [...]}} on success.
    """
    data = _gmgn(["token", "traders", "--chain", chain, "--address", address,
                  "--limit", str(limit)], timeout=30)
    # Bigger delay for weight=5 endpoints
    time.sleep(GMGN_RATE_DELAY * 5)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        for key in ("list", "traders", "items", "results"):
            val = inner.get(key) if isinstance(inner, dict) else None
            if isinstance(val, list):
                return val
    return []


def build_trading_brief(addresses: list[str], cashtags: list[str],
                         onchain_map: dict[str, str]) -> dict:
    """R3: Build trading brief for discovered tokens.

    Resolves addresses from three sources, in priority order:
      1. Contract addresses extracted directly from tweet text (chain inferred
         from gmgn trending or defaults to base).
      2. cashtag→address mapping in onchain-ticks.json (bsc, stale snapshot).
      3. gmgn trending list fallback — for cashtags not in the static map,
         query market trending across base/bsc/sol and pick the highest-volume
         symbol match. This is what catches Base meme tokens like $AEON/$LFI
         which never make it into the offline onchain-ticks snapshot.
    """
    # address → (label, chain)
    all_addresses: dict[str, tuple[str, str]] = {}

    # 1. Tweet-extracted contract addresses (0x... EVM). Try to recover chain
    #    via trending lookup by address; otherwise default to base.
    for addr in addresses:
        addr_lc = addr.lower()
        chain_match = "base"
        for c in _RESOLVER_CHAINS:
            for row in _load_trending_chain(c):
                if str(row.get("address") or "").lower() == addr_lc:
                    chain_match = c
                    break
            else:
                continue
            break
        all_addresses[addr] = (addr[:8] + "...", chain_match)

    # 2. onchain-ticks.json cashtag map — snapshot is BSC-only today.
    for ticker in cashtags:
        if ticker in onchain_map:
            addr = onchain_map[ticker]
            all_addresses[addr] = (ticker, "bsc")

    # 3+4. Resolver fallback for unresolved cashtags: gmgn trending → dexscreener.
    # gmgn is the preferred source because the same provider serves info/
    # security/traders downstream — using its own listing avoids "resolved on
    # base via dexscreener but gmgn doesn't index it" gaps.
    for ticker in cashtags:
        if ticker in onchain_map:
            continue
        if any(label == ticker for label, _ in all_addresses.values()):
            continue
        resolved = resolve_ticker_via_gmgn(ticker)
        source = "gmgn trending"
        if resolved is None:
            resolved = resolve_ticker_via_dexscreener(ticker)
            source = "dexscreener"
        if resolved is None:
            print(f"[social-brief] ${ticker}: no match (gmgn trending + dexscreener)")
            continue
        addr, chain = resolved
        all_addresses[addr] = (ticker, chain)
        print(f"[social-brief] ${ticker}: resolved via {source} → {addr[:10]}... on {chain}")

    if not all_addresses:
        return {"status": "no_tokens", "tokens": []}

    token_briefs: list[dict] = []
    for addr, (label, chain) in all_addresses.items():
        print(f"[social-brief] Fetching gmgn data for {label} ({addr[:10]}... on {chain})...")
        info = fetch_token_info(addr, chain=chain)
        security = fetch_token_security(addr, chain=chain)

        # Fetch traders only for tokens with a known ticker label (resolved
        # from cashtags or onchain_map). The truncated-address labels from
        # bare tweet CAs are skipped to conserve the weight=5 budget.
        should_fetch_traders = label in cashtags or label in onchain_map
        traders = fetch_token_traders(addr, chain=chain, limit=5) if should_fetch_traders else []

        smart_money: list[dict] = []
        for t in traders:
            if not isinstance(t, dict):
                continue
            tag_list = t.get("wallet_tag_v2") or t.get("tags") or []
            if isinstance(tag_list, list) and tag_list:
                tag_label = ",".join(str(x) for x in tag_list[:3])
            else:
                tag_label = str(tag_list) if tag_list else ""
            handle = t.get("twitter_username") or t.get("name") or ""
            buy_vol = _to_float(t.get("buy_volume_cur"))
            sell_vol = _to_float(t.get("sell_volume_cur"))
            if buy_vol > sell_vol:
                action = "buy"
            elif sell_vol > buy_vol:
                action = "sell"
            else:
                action = "hold"
            smart_money.append({
                "wallet": t.get("address") or "",
                "label": tag_label or handle or "wallet",
                "twitter": handle,
                "type": "smart_money" if tag_list else "wallet",
                "action": action,
                "usd_value": _to_float(t.get("usd_value")),
                "buy_volume_usd": buy_vol,
                "sell_volume_usd": sell_vol,
                "realized_pnl": _to_float(t.get("realized_profit") or t.get("realized_pnl")),
                "unrealized_pnl": _to_float(t.get("unrealized_profit") or t.get("unrealized_pnl")),
                "is_suspicious": bool(t.get("is_suspicious")),
            })

        entry: dict[str, Any] = {
            "ticker": label,
            "address": addr,
            "chain": chain,
        }

        # Backfill chain. Priority: gmgn token info → gmgn trending row →
        # dexscreener pair. Dexscreener is the last resort because its
        # priceChange/volume are pair-scoped, not token-scoped.
        info = info or {}
        trend = _trending_row(addr, chain) or {}
        ds_pair = _dexscreener_pair(addr) or {}
        ds_pc = ds_pair.get("priceChange") or {}
        ds_vol = ds_pair.get("volume") or {}
        ds_base = ds_pair.get("baseToken") or {}
        ds_liq = ds_pair.get("liquidity") or {}

        if info or trend or ds_pair:
            entry["price_usd"] = (_to_float(info.get("price"))
                                   or _to_float(trend.get("price"))
                                   or _to_float(ds_pair.get("priceUsd")))
            entry["market_cap"] = (_to_float(info.get("market_cap"))
                                    or _to_float(trend.get("market_cap"))
                                    or _to_float(ds_pair.get("marketCap"))
                                    or _to_float(ds_pair.get("fdv")))
            entry["volume_24h"] = (_to_float(info.get("volume_24h"))
                                    or _to_float(trend.get("volume"))
                                    or _to_float(ds_vol.get("h24")))
            entry["price_change_1h"] = (info.get("price_change_1h")
                                         or trend.get("price_change_percent1h")
                                         or ds_pc.get("h1") or 0)
            entry["price_change_24h"] = (info.get("price_change_24h")
                                          or trend.get("price_change_percent")
                                          or ds_pc.get("h24") or 0)
            entry["symbol"] = (info.get("symbol") or trend.get("symbol")
                                or ds_base.get("symbol") or label)
            entry["name"] = (info.get("name") or trend.get("name")
                              or ds_base.get("name") or label)
            entry["liquidity"] = (_to_float(info.get("liquidity"))
                                   or _to_float(trend.get("liquidity"))
                                   or _to_float(ds_liq.get("usd")))
            entry["holders"] = (info.get("holders") or info.get("holder_count")
                                 or trend.get("holder_count") or 0)

            # Project Twitter / website / telegram. gmgn nests these under
            # `link`; dexscreener under `info.socials` / `info.websites`.
            # gmgn first (its `twitter_username` is already a bare handle),
            # dexscreener fallback (URLs — extracted via _extract_twitter_handle).
            gmgn_link = info.get("link") if isinstance(info.get("link"), dict) else {}
            ds_info = ds_pair.get("info") if isinstance(ds_pair.get("info"), dict) else {}
            ds_socials = ds_info.get("socials") if isinstance(ds_info.get("socials"), list) else []
            ds_websites = ds_info.get("websites") if isinstance(ds_info.get("websites"), list) else []
            ds_twitter_url = next(
                (s.get("url") for s in ds_socials
                 if isinstance(s, dict) and (s.get("type") or "").lower() == "twitter"),
                None,
            )
            ds_telegram_url = next(
                (s.get("url") for s in ds_socials
                 if isinstance(s, dict) and (s.get("type") or "").lower() == "telegram"),
                None,
            )
            ds_website_url = next(
                (w.get("url") for w in ds_websites if isinstance(w, dict)),
                None,
            )
            project_twitter = (_extract_twitter_handle(gmgn_link.get("twitter_username"))
                                or _extract_twitter_handle(ds_twitter_url)
                                or _extract_twitter_handle(trend.get("twitter_username")))
            if project_twitter:
                entry["project_twitter"] = project_twitter
            project_links = {
                "twitter": project_twitter,
                "website": gmgn_link.get("website") or ds_website_url or None,
                "telegram": gmgn_link.get("telegram") or ds_telegram_url or None,
                "discord": gmgn_link.get("discord") or None,
                "github": gmgn_link.get("github") or None,
            }
            project_links = {k: v for k, v in project_links.items() if v}
            if project_links:
                entry["project_links"] = project_links

        if security:
            is_hp = bool(security.get("is_honeypot")) or _to_float(security.get("honeypot")) > 0
            # gmgn returns buy_tax/sell_tax as strings, sometimes 0–1 fraction, sometimes 0–100 percent.
            buy_tax_raw = _to_float(security.get("buy_tax"))
            sell_tax_raw = _to_float(security.get("sell_tax"))
            buy_tax_pct = buy_tax_raw * 100 if buy_tax_raw and buy_tax_raw <= 1 else buy_tax_raw
            sell_tax_pct = sell_tax_raw * 100 if sell_tax_raw and sell_tax_raw <= 1 else sell_tax_raw
            cannot_sell = _to_float(security.get("can_not_sell")) > 0
            high_tax = _to_float(security.get("high_tax")) > 0 or buy_tax_pct > 10 or sell_tax_pct > 10
            renounced = bool(security.get("is_renounced")) or _to_float(security.get("renounced")) > 0
            # Synthesize a risk_level since gmgn doesn't return one directly.
            if is_hp or cannot_sell:
                risk = "high"
            elif high_tax:
                risk = "medium"
            else:
                risk = "low"
            entry["security"] = {
                "is_honeypot": is_hp,
                "buy_tax": round(buy_tax_pct, 2),
                "sell_tax": round(sell_tax_pct, 2),
                "owner_renounced": renounced,
                "risk_level": risk,
            }

        entry["smart_money"] = smart_money
        entry["smart_money_count"] = len(smart_money)
        token_briefs.append(entry)

    return {"status": "ok", "tokens": token_briefs}


# ---------------------------------------------------------------------------
# R4: Social graph — track project Twitter accounts via quoted tweets
# ---------------------------------------------------------------------------


# Cache of target usernames for exclusion filtering in extract_quoted_authors
_target_users_lower_cache: list[str] = []


def extract_quoted_authors(tweets: list[dict]) -> list[str]:
    """Extract Twitter usernames from quoted/referenced tweets.

    Covers: tweet.quoted_tweet.user.screen_name, tweet.referenced_tweets,
    and URL patterns in tweet text.
    """
    authors: set[str] = set()

    for t in tweets:
        target_users_lower = _target_users_lower_cache

        for key in ("quoted_tweet", "quoted_status", "quote"):
            qt = t.get(key)
            if isinstance(qt, dict):
                user = qt.get("user") or qt.get("author") or {}
                if isinstance(user, dict):
                    sn = user.get("screen_name") or user.get("username") or user.get("handle")
                    if sn and sn.lower() not in target_users_lower:
                        authors.add(sn)

        # Referenced tweets array (v2 API shape)
        for ref in (t.get("referenced_tweets") or []):
            if isinstance(ref, dict):
                author = ref.get("author") or ref.get("user") or {}
                if isinstance(author, dict):
                    sn = author.get("screen_name") or author.get("username")
                    if sn and sn.lower() not in target_users_lower:
                        authors.add(sn)

        # Twitter URLs in text: https://x.com/<user>/status/<id>
        text = _tweet_text(t)
        for m in re.finditer(r"https?://(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)/status/", text):
            sn = m.group(1)
            if sn.lower() not in (["i"] + target_users_lower):
                authors.add(sn)

    return list(authors)


def fetch_project_recent_tweets(username: str, days: int = 3) -> list[dict]:
    """R4: Fetch project account's recent tweets (last N days)."""
    tweets = fetch_user_tweets(username, limit=20)
    if not tweets:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent: list[dict] = []
    for t in tweets:
        # Try to parse created_at
        created_raw = (t.get("created_at") or t.get("timestamp") or
                       t.get("date") or t.get("time") or "")
        if created_raw:
            try:
                # Handle "Mon May 18 12:00:00 +0000 2026" Twitter format
                from email.utils import parsedate_to_datetime
                try:
                    dt = parsedate_to_datetime(str(created_raw))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt >= cutoff:
                        recent.append(t)
                    continue
                except Exception:
                    pass
                # ISO format
                if str(created_raw).endswith("Z"):
                    created_raw = str(created_raw)[:-1] + "+00:00"
                dt = datetime.fromisoformat(str(created_raw))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    recent.append(t)
            except Exception:
                # Can't parse date — include conservatively
                recent.append(t)
        else:
            # No date — include all
            recent.append(t)

    return recent


def build_project_social_intel(accounts: list[dict]) -> list[dict]:
    """R4: For each project account, fetch recent tweets.

    `accounts` is a list of ``{"username": str, "sources": [str, ...]}`` —
    ``sources`` records why the account is in the brief (e.g. ``"quoted"`` for
    accounts mentioned in target users' tweets, or ``"$AEON"`` for accounts
    derived from a token's CA metadata). Capped at 10 to bound rate-limit blast.
    """
    results: list[dict] = []
    for acc in accounts[:10]:
        username = acc.get("username") or ""
        if not username:
            continue
        sources = acc.get("sources") or []
        print(f"[social-brief] Fetching project account @{username} (sources={sources})...")
        recent = fetch_project_recent_tweets(username, days=3)
        # Use the same extractor section 4 (social search) uses so engagement
        # counters and follower count come through consistently. The old
        # inline mapping missed bird's camelCase fields (`likeCount` etc.)
        # which made every tweet's likes/retweets show 0 in the brief.
        summaries = [_extract_tweet_summary(t) for t in recent[:10]]
        # Project account's follower count: take the first non-zero from the
        # tweets we fetched (all tweets in the batch share the same author).
        author_followers = next((s.get("author_followers") or 0 for s in summaries if s.get("author_followers")), 0)
        results.append({
            "username": username,
            "sources": sources,
            "recent_tweet_count": len(recent),
            "author_followers": author_followers,
            "tweets": summaries,
        })
    return results


def _tweet_url(tweet: dict, username: str = "") -> str:
    """Best-effort tweet URL from tweet dict."""
    if tweet.get("url"):
        return tweet["url"]
    tid = tweet.get("id") or tweet.get("id_str") or tweet.get("tweet_id")
    if tid and username:
        return f"https://x.com/{username}/status/{tid}"
    return ""


def _nested_get(obj: Any, path: tuple[str, ...]) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _extract_username(candidate: Any) -> str:
    if not isinstance(candidate, dict):
        return ""
    return str(
        candidate.get("screen_name")
        or candidate.get("username")
        or candidate.get("handle")
        or ""
    ).strip()


def _extract_original_retweet_url(tweet: dict) -> str:
    """Return the original tweet URL when ``tweet`` is a retweet.

    bird/xurl payloads vary a lot, so we probe several common shapes:
    top-level nested retweet objects, v2 referenced_tweets arrays, and
    GraphQL raw payloads from bird ``--json-full``.
    """
    text = _tweet_text(tweet)
    raw = tweet.get("_raw") if isinstance(tweet.get("_raw"), dict) else {}

    direct_candidates = [
        tweet.get("retweeted_status"),
        tweet.get("retweeted_tweet"),
        tweet.get("retweeted_status_result"),
        tweet.get("retweeted_tweet_result"),
        _nested_get(raw, ("retweeted_status_result", "result")),
        _nested_get(raw, ("legacy", "retweeted_status_result", "result")),
    ]
    for cand in direct_candidates:
        if not isinstance(cand, dict):
            continue
        tid = cand.get("id") or cand.get("id_str") or cand.get("tweet_id") or cand.get("rest_id")
        user = cand.get("user") or cand.get("author") or _nested_get(cand, ("core", "user_results", "result", "legacy")) or _nested_get(cand, ("core", "user_results", "result", "core")) or {}
        username = _extract_username(user)
        if tid and username:
            return f"https://x.com/{username}/status/{tid}"

    for ref in (tweet.get("referenced_tweets") or []):
        if not isinstance(ref, dict):
            continue
        ref_type = str(ref.get("type") or "").lower()
        if ref_type not in ("retweeted", "retweet"):
            continue
        tid = ref.get("id") or ref.get("id_str") or ref.get("tweet_id") or ref.get("rest_id")
        user = ref.get("user") or ref.get("author") or {}
        username = _extract_username(user)
        if tid and username:
            return f"https://x.com/{username}/status/{tid}"

    m = re.match(r"RT @([A-Za-z0-9_]{1,15}):", text)
    if m:
        username = m.group(1)
        for key in ("retweeted_status_id", "retweeted_status_id_str", "retweeted_tweet_id"):
            tid = tweet.get(key)
            if tid:
                return f"https://x.com/{username}/status/{tid}"
        for cand in direct_candidates:
            if isinstance(cand, dict):
                tid = cand.get("id") or cand.get("id_str") or cand.get("tweet_id") or cand.get("rest_id")
                if tid:
                    return f"https://x.com/{username}/status/{tid}"
    return ""


# ---------------------------------------------------------------------------
# R5: Search $Cashtags and #Hashtags for trending tweets
# ---------------------------------------------------------------------------


def search_tweets_bird(query: str, limit: int = SEARCH_RESULTS_LIMIT) -> list[dict]:
    """Search tweets via bird CLI.

    Uses ``--json-full`` so each result carries the full GraphQL ``_raw`` payload
    (including ``_raw.core.user_results.result.legacy.followers_count`` — the
    only path that exposes follower counts; bird 0.8.0 has no ``user`` command
    and xurl's X-API path is currently down for us).
    """
    rc, out, err = _run_bird(["search", query, "-n", str(limit), "--json-full"])
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, list):
            return data
        for key in ("tweets", "data", "results", "statuses"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    except json.JSONDecodeError:
        return []


def search_tweets_xurl(query: str, limit: int = SEARCH_RESULTS_LIMIT) -> list[dict]:
    """Search tweets via xurl CLI. xurl search "QUERY" -n <limit> (JSON native)."""
    n = max(10, min(limit, 100))
    rc, out, err = _run(["xurl", "search", query, "-n", str(n)])
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, list):
            return data
        for key in ("tweets", "data", "results", "statuses"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    except json.JSONDecodeError:
        return []


def search_tweets(query: str, limit: int = SEARCH_RESULTS_LIMIT) -> list[dict]:
    """Search tweets. Priority: bird > xurl."""
    tweets = search_tweets_bird(query, limit)
    if tweets:
        return tweets
    return search_tweets_xurl(query, limit)


def _extract_full_text_from_raw(raw: dict) -> str:
    """Extract the most complete tweet text available from a bird --json-full ``_raw`` node.

    Priority order:
      RT with long-form (note_tweet):
        _raw.legacy.retweeted_status_result.result.note_tweet.note_tweet_results.result.text
      RT with regular full_text:
        _raw.legacy.retweeted_status_result.result.legacy.full_text
      Own note_tweet (long-form non-RT):
        _raw.legacy.note_tweet.note_tweet_results.result.text
      Own legacy full_text:
        _raw.legacy.full_text
      Empty string on fallback.

    For RT tweets the returned text is prefixed with ``RT @{screen_name}: ``
    to preserve the expected display format.
    """
    leg = raw.get("legacy") if isinstance(raw.get("legacy"), dict) else {}

    rt_result_wrapper = leg.get("retweeted_status_result")
    if isinstance(rt_result_wrapper, dict):
        rt_result = rt_result_wrapper.get("result") if isinstance(rt_result_wrapper.get("result"), dict) else rt_result_wrapper
        if not isinstance(rt_result, dict):
            rt_result = {}

        # Try RT note_tweet (long-form) first
        rt_note = (
            ((rt_result.get("note_tweet") or {})
             .get("note_tweet_results") or {})
            .get("result") or {}
        )
        rt_full_text = (
            (rt_note.get("text") if isinstance(rt_note, dict) else None)
            or (((rt_result.get("legacy") or {}) if isinstance(rt_result.get("legacy"), dict) else {})
                .get("full_text"))
            or ""
        )

        if rt_full_text:
            # Get the original author's screen_name for the RT prefix
            rt_leg = rt_result.get("legacy") if isinstance(rt_result.get("legacy"), dict) else {}
            rt_core = rt_result.get("core") if isinstance(rt_result.get("core"), dict) else {}
            rt_user_result = (rt_core.get("user_results") or {}).get("result") or {}
            rt_user_leg = rt_user_result.get("legacy") if isinstance(rt_user_result.get("legacy"), dict) else {}
            rt_user_core = rt_user_result.get("core") if isinstance(rt_user_result.get("core"), dict) else {}
            rt_screen_name = (
                rt_user_leg.get("screen_name")
                or rt_user_core.get("screen_name")
                or ""
            )
            prefix = f"RT @{rt_screen_name}: " if rt_screen_name else "RT: "
            return prefix + rt_full_text

    # Non-RT: try note_tweet (long-form) first, then legacy.full_text
    own_note = (
        ((leg.get("note_tweet") or {})
         .get("note_tweet_results") or {})
        .get("result") or {}
    )
    own_note_text = own_note.get("text") if isinstance(own_note, dict) else None
    if own_note_text:
        return own_note_text

    return leg.get("full_text") or ""


def _build_raw_tweet_fields(tweet: dict, raw: dict, username: str) -> dict:
    """Build a ``_raw`` sub-object preserving bird --json-full fields needed by TagAI import.

    ``raw`` is ``tweet["_raw"]`` (the GraphQL tweet node).  When the source is
    not a --json-full response, most fields degrade gracefully to None.

    ``fullText`` carries the most complete tweet text available: RT tweets are
    assembled from ``retweeted_status_result`` (long-form note_tweet preferred),
    long-form non-RT tweets use ``note_tweet``.  Falls back to ``legacy.full_text``.
    """
    leg = raw.get("legacy") if isinstance(raw.get("legacy"), dict) else {}
    user_result = (
        ((raw.get("core") or {}).get("user_results") or {}).get("result") or {}
    )
    user_leg = user_result.get("legacy") if isinstance(user_result.get("legacy"), dict) else {}
    user_core = user_result.get("core") if isinstance(user_result.get("core"), dict) else {}

    tweet_id = (
        raw.get("rest_id")
        or tweet.get("id")
        or tweet.get("id_str")
        or tweet.get("tweet_id")
        or None
    )
    author_id = (
        leg.get("user_id_str")
        or str(user_result.get("rest_id", "") or "")
        or None
    )
    conversation_id = leg.get("conversation_id_str") or None

    # Author ID from user_result.rest_id (numeric string)
    author_result_id = str(user_result.get("rest_id", "") or "") or author_id or None

    author_name = (
        user_leg.get("name")
        or user_core.get("name")
        or (tweet.get("author") or tweet.get("user") or {}).get("name")
        or None
    )
    author_username = (
        user_leg.get("screen_name")
        or user_core.get("screen_name")
        or username if username != "unknown" else None
    )
    profile_image_url = user_leg.get("profile_image_url_https") or None
    followers_count = user_leg.get("followers_count") or None
    following_count = user_leg.get("friends_count") or None
    tweet_count = user_leg.get("statuses_count") or None
    like_count = user_leg.get("favourites_count") or None
    listed_count = user_leg.get("listed_count") or None

    created_at = (
        leg.get("created_at")
        or tweet.get("createdAt")
        or tweet.get("created_at")
        or None
    )

    full_text = _extract_full_text_from_raw(raw) or None

    return {
        "tweetId": tweet_id,
        "authorId": author_id,
        "conversationId": conversation_id,
        "createdAt": created_at,
        "fullText": full_text,
        "author": {
            "id": author_result_id,
            "name": author_name,
            "username": author_username,
            "profileImageUrl": profile_image_url,
            "followersCount": followers_count,
            "followingCount": following_count,
            "tweetCount": tweet_count,
            "likeCount": like_count,
            "listedCount": listed_count,
        },
    }


def _extract_tweet_summary(tweet: dict) -> dict:
    """Extract key fields for brief display.

    Field-mapping bug fix (2026-05-19):
        bird CLI emits ``likeCount`` / ``retweetCount`` / ``createdAt`` (camelCase)
        in the top-level shape, not ``like_count`` / ``retweet_count`` / ``created_at``.
        The prior code looked for the snake_case names only, so every counter
        in the brief reported 0.

    Followers lookup:
        bird's top-level ``author`` block only carries ``username`` + ``name``.
        Follower counts live in the GraphQL raw payload at
        ``_raw.core.user_results.result.legacy.followers_count``. We probe that
        path (only present when ``--json-full`` was used). When the raw payload
        is missing (e.g. xurl source, or bird older mode), we degrade to 0
        rather than mis-attribute.

    URL construction:
        bird does not emit a tweet URL field; build it from
        ``id`` + ``author.username``.
    """
    # top-level author block (bird --json/--json-full both populate this)
    author_block = tweet.get("user") or tweet.get("author") or {}
    if not isinstance(author_block, dict):
        author_block = {}
    username = (
        author_block.get("screen_name")
        or author_block.get("username")
        or author_block.get("handle")
        or ""
    )

    # _raw.core.user_results.result.{legacy.followers_count, core.screen_name}
    # path is bird --json-full's GraphQL payload. xurl wraps user_followers
    # differently (public_metrics.followers_count under expansions.users), so
    # try both shapes.
    followers = (
        author_block.get("followers_count")
        or author_block.get("followers")
        or 0
    )
    raw = tweet.get("_raw") if isinstance(tweet.get("_raw"), dict) else {}
    if not followers and raw:
        user_result = (
            ((raw.get("core") or {}).get("user_results") or {}).get("result") or {}
        )
        legacy = user_result.get("legacy") if isinstance(user_result.get("legacy"), dict) else {}
        public_metrics = (
            user_result.get("public_metrics")
            if isinstance(user_result.get("public_metrics"), dict)
            else (author_block.get("public_metrics") or {})
        )
        followers = (
            legacy.get("followers_count")
            or public_metrics.get("followers_count")
            or public_metrics.get("follower_count")
            or 0
        )
        # `--json-full` also nests the verified screen_name inside user_result.core
        if not username:
            user_core = user_result.get("core") if isinstance(user_result.get("core"), dict) else {}
            username = user_core.get("screen_name") or user_core.get("username") or username

    if not username:
        username = "unknown"

    tweet_id = tweet.get("id") or tweet.get("id_str") or tweet.get("tweet_id") or ""
    source_url = tweet.get("url") or ""
    if not source_url and tweet_id and username and username != "unknown":
        source_url = f"https://x.com/{username}/status/{tweet_id}"
    tagai_import_url = _extract_original_retweet_url(tweet) or source_url

    return {
        "text": _tweet_text(tweet)[:280],
        "author": username,
        "author_followers": int(followers) if isinstance(followers, (int, float, str)) and str(followers).isdigit() else (followers if isinstance(followers, int) else 0),
        # bird's camelCase first, then snake_case fallbacks for xurl / other sources
        "likes": (
            tweet.get("likeCount")
            or tweet.get("favorite_count")
            or tweet.get("likes")
            or tweet.get("like_count")
            or (tweet.get("public_metrics") or {}).get("like_count")
            or 0
        ),
        "retweets": (
            tweet.get("retweetCount")
            or tweet.get("retweet_count")
            or tweet.get("retweets")
            or (tweet.get("public_metrics") or {}).get("retweet_count")
            or 0
        ),
        "replies": (
            tweet.get("replyCount")
            or tweet.get("reply_count")
            or tweet.get("replies")
            or (tweet.get("public_metrics") or {}).get("reply_count")
            or 0
        ),
        "created_at": (
            tweet.get("createdAt")
            or tweet.get("created_at")
            or tweet.get("timestamp")
            or ""
        ),
        "url": tagai_import_url,
        "source_url": source_url,
        "tagai_import_url": tagai_import_url,
        "_raw": _build_raw_tweet_fields(tweet, raw, username),
    }


# X advanced search "Top" filter ladder.
#
# bird 0.8.0 does not expose `product=Top`/`product=Latest` switching through
# the CLI, but Twitter's advanced-search operators (min_faves, min_retweets,
# min_replies) approximate the Top filter perfectly: they suppress 0-engagement
# spam and surface tweets the network actually engaged with. We try the
# strictest filter first and fall back to looser filters when a cashtag/hashtag
# is too cold to clear the high bar — guarantees results for both the popular
# tickers and the long-tail ones.
_TOP_FILTER_LADDER = ["min_faves:50", "min_faves:10", ""]


def _search_top_tweets(query_core: str, limit: int = SEARCH_RESULTS_LIMIT) -> tuple[list[dict], str]:
    """Search ``query_core`` (e.g. ``$AEO`` or ``#OpenHuman``) and return
    the highest-engagement tweets we can find. Returns ``(tweets, filter_used)``
    so the caller can log which rung of the ladder produced results.
    """
    for fltr in _TOP_FILTER_LADDER:
        full_q = f"{query_core} {fltr}".strip() if fltr else query_core
        tweets = search_tweets(full_q, limit=limit)
        if tweets:
            return tweets, (fltr or "no_filter")
    return [], "no_results"


def build_social_search_intel(cashtags: list[str], hashtags: list[str]) -> dict:
    """R5: search Top tweets for each $Cashtag and #Hashtag.

    Top filter (2026-05-19): we now layer X advanced-search operators
    ``min_faves:50 → min_faves:10 → no filter`` to skip the spam pool and
    surface the same tweets a human would see under X's Top tab. Without
    this, the brief was full of 0-engagement copypasta because bird's
    default ``SearchTimeline`` is Latest-sorted.
    """
    cashtag_results: dict[str, list[dict]] = {}
    hashtag_results: dict[str, list[dict]] = {}
    filters_used: dict[str, str] = {}

    # Search cashtags
    for ticker in cashtags[:5]:  # cap to 5 cashtags
        query_core = f"${ticker}"
        print(f"[social-brief] Searching Top tweets: {query_core} (ladder={_TOP_FILTER_LADDER})...")
        tweets, fltr = _search_top_tweets(query_core, limit=SEARCH_RESULTS_LIMIT)
        cashtag_results[ticker] = [_extract_tweet_summary(t) for t in tweets] if tweets else []
        filters_used[f"${ticker}"] = fltr
        print(f"[social-brief]   → {len(tweets)} results for {query_core}  filter={fltr}")

    # Search hashtags (limit to most relevant)
    for tag in hashtags[:5]:
        query_core = f"#{tag}"
        print(f"[social-brief] Searching Top tweets: {query_core} (ladder={_TOP_FILTER_LADDER})...")
        tweets, fltr = _search_top_tweets(query_core, limit=SEARCH_RESULTS_LIMIT)
        hashtag_results[tag] = [_extract_tweet_summary(t) for t in tweets] if tweets else []
        filters_used[f"#{tag}"] = fltr
        print(f"[social-brief]   → {len(tweets)} results for {query_core}  filter={fltr}")

    return {
        "cashtag_search": cashtag_results,
        "hashtag_search": hashtag_results,
        "filters_used": filters_used,  # diagnostic: shows which ladder rung produced each result set
    }


# ---------------------------------------------------------------------------
# R6: Brief generation and delivery
# ---------------------------------------------------------------------------


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown special characters in tweet text.

    Characters escaped: _ * ~ ` [ ]  (outside of markdown link syntax).
    We do NOT escape these inside the [🔗](url) link label — the emoji
    is safe and the URL is passed through clean.
    """
    for ch in ("_", "*", "~", "`", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


def generate_markdown_brief(
    target_user: str,
    source_tweets: list[dict],
    cashtags: list[str],
    hashtags: list[str],
    addresses: list[str],
    trading_brief: dict,
    social_search: dict,
    project_intel: list[dict],
    ts: str,
) -> str:
    """R6: Generate human-readable Markdown brief."""
    lines: list[str] = []

    lines.append(f"# 社交交易简报 — {ts}")
    lines.append(f"\n**数据来源**: {target_user} | **时间**: {ts}\n")

    # ── 1. Source tweets summary ──
    lines.append("## 1. 信号源推文")
    if source_tweets:
        lines.append(f"抓取 {target_user} 最新 {len(source_tweets)} 条推文，发现：")
        if cashtags:
            lines.append(f"- **$代币标签**: {', '.join(f'${t}' for t in cashtags)}")
        if hashtags:
            lines.append(f"- **#话题标签**: {', '.join(f'#{t}' for t in hashtags)}")
        if addresses:
            lines.append(f"- **合约地址**: {len(addresses)} 个")
        lines.append("")
        for t in source_tweets[:5]:
            text = _escape_md(_tweet_text(t)[:150].replace("\n", " "))
            summary = _extract_tweet_summary(t)
            tweet_url = summary.get("url") or ""
            if tweet_url:
                lines.append(f"> {text}  [🔗]({tweet_url})")
            else:
                lines.append(f"> {text}")
        if len(source_tweets) > 5:
            lines.append(f"> *(共 {len(source_tweets)} 条推文，仅显示前 5 条)*")
    else:
        lines.append(f"⚠️ 未能抓取 {target_user} 的推文")
    lines.append("")

    # ── 2. Trading brief (R3) ──
    lines.append("## 2. 交易情报（链上数据）")
    tokens = trading_brief.get("tokens") or []
    if tokens:
        for tok in tokens:
            ticker = tok.get("ticker") or tok.get("symbol") or "?"
            def _to_num(v) -> float:
                if isinstance(v, (int, float)):
                    return float(v)
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0

            price = _to_num(tok.get("price_usd"))
            cap = _to_num(tok.get("market_cap"))
            vol = _to_num(tok.get("volume_24h"))
            ch1h = _to_num(tok.get("price_change_1h"))
            ch24h = _to_num(tok.get("price_change_24h"))
            sm_count = _to_num(tok.get("smart_money_count"))
            addr = str(tok.get("address") or "")
            sec = tok.get("security") or {}

            lines.append(f"### ${ticker}")
            lines.append(f"- 合约: `{addr}`")
            if price > 0:
                lines.append(f"- 价格: ${price:.8f}")
            if cap > 0:
                lines.append(f"- 市值: ${cap:,.0f}")
            if vol > 0:
                lines.append(f"- 24h成交量: ${vol:,.0f}")
            if ch1h != 0:
                arrow = "▲" if ch1h > 0 else "▼"
                lines.append(f"- 1h涨跌: {arrow}{abs(ch1h):.2f}%")
            if ch24h:
                arrow = "▲" if ch24h > 0 else "▼"
                lines.append(f"- 24h涨跌: {arrow}{abs(ch24h):.2f}%")
            if sec:
                hp = sec.get("is_honeypot")
                risk = sec.get("risk_level") or "?"
                buy_tax = sec.get("buy_tax") or 0
                sell_tax = sec.get("sell_tax") or 0
                lines.append(f"- 安全评级: {risk} | 蜜罐: {'⚠️ 是' if hp else '否'}")
                lines.append(f"- 税率: 买入{buy_tax}% / 卖出{sell_tax}%")
            if sm_count:
                lines.append(f"- **聪明钱**: {int(sm_count)} 个钱包")
                for sm in tok.get("smart_money") or []:
                    w = sm.get("wallet") or ""
                    label = sm.get("label") or "wallet"
                    handle = sm.get("twitter") or ""
                    action = sm.get("action") or ""
                    pnl = _to_num(sm.get("realized_pnl"))
                    usd_val = _to_num(sm.get("usd_value"))
                    handle_str = f" (@{handle})" if handle else ""
                    pnl_str = f" PnL=${pnl:,.0f}" if pnl else ""
                    val_str = f" 持仓=${usd_val:,.0f}" if usd_val else ""
                    lines.append(f"  - [{label}]{handle_str} `{w[:10]}...` {action}{val_str}{pnl_str}")
            lines.append("")
    else:
        lines.append("⚠️ 未获取到代币链上数据")
    lines.append("")

    # ── 3. Project social intel (R4) ──
    lines.append("## 3. 项目方动态（项目方/关联账号）")
    if project_intel:
        for proj in project_intel:
            uname = proj.get("username") or "?"
            cnt = proj.get("recent_tweet_count") or 0
            sources = proj.get("sources") or []
            src_label = f" [{', '.join(sources)}]" if sources else ""
            followers = int(proj.get("author_followers") or 0)
            fol_label = f" 粉丝:{followers:,}" if followers else ""
            lines.append(f"### @{uname}{src_label}{fol_label} (近3天 {cnt} 条推文)")
            for t in (proj.get("tweets") or [])[:3]:
                text = _escape_md((t.get("text") or "")[:150].replace("\n", " "))
                likes = int(t.get("likes") or 0)
                rt = int(t.get("retweets") or 0)
                replies = int(t.get("replies") or 0)
                tweet_url = t.get("url") or ""
                if tweet_url:
                    lines.append(f"> {text}  [🔗]({tweet_url})")
                else:
                    lines.append(f"> {text}")
                lines.append(f"> ❤️{likes:,} 🔁{rt:,} 💬{replies:,}")
            lines.append("")
    else:
        lines.append("⚠️ 未找到项目方或关联推特账号")
    lines.append("")

    # ── 4. Social search (R5) ──
    lines.append("## 4. 热门推文搜索")
    ct_results = social_search.get("cashtag_search") or {}
    ht_results = social_search.get("hashtag_search") or {}

    if ct_results:
        lines.append("### $代币标签热搜（Top 推文）")
        for ticker, tlist in ct_results.items():
            lines.append(f"**${ticker}** — {len(tlist)} 条结果")
            for t in tlist[:3]:
                author = t.get("author") or "?"
                text = _escape_md((t.get("text") or "")[:120].replace("\n", " "))
                likes = int(t.get("likes") or 0)
                retweets = int(t.get("retweets") or 0)
                replies = int(t.get("replies") or 0)
                followers = int(t.get("author_followers") or 0)
                tweet_url = t.get("url") or ""
                if tweet_url:
                    lines.append(f"  - @{author} (粉丝:{followers:,}) ❤️{likes:,} 🔁{retweets:,} 💬{replies:,}: {text}  [🔗]({tweet_url})")
                else:
                    lines.append(f"  - @{author} (粉丝:{followers:,}) ❤️{likes:,} 🔁{retweets:,} 💬{replies:,}: {text}")
            lines.append("")

    if ht_results:
        lines.append("### #话题标签热搜（Top 推文）")
        for tag, tlist in ht_results.items():
            lines.append(f"**#{tag}** — {len(tlist)} 条结果")
            for t in tlist[:3]:
                author = t.get("author") or "?"
                text = _escape_md((t.get("text") or "")[:120].replace("\n", " "))
                likes = int(t.get("likes") or 0)
                retweets = int(t.get("retweets") or 0)
                replies = int(t.get("replies") or 0)
                followers = int(t.get("author_followers") or 0)
                tweet_url = t.get("url") or ""
                if tweet_url:
                    lines.append(f"  - @{author} (粉丝:{followers:,}) ❤️{likes:,} 🔁{retweets:,} 💬{replies:,}: {text}  [🔗]({tweet_url})")
                else:
                    lines.append(f"  - @{author} (粉丝:{followers:,}) ❤️{likes:,} 🔁{retweets:,} 💬{replies:,}: {text}")
            lines.append("")

    if not ct_results and not ht_results:
        lines.append("⚠️ 未获取到热门搜索结果")
    lines.append("")

    lines.append("---")
    lines.append(f"*由 run_trader_social_brief.py 自动生成 @ {ts}*")

    return "\n".join(lines)


def send_brief_via_trader(brief_md: str, brief_json_path: Path) -> bool:
    """R6: Deliver brief via trader agent message channel.

    Strategy (NOTE9):
    1. Write to runtime/trader/social-brief-<date>.json (primary)
    2. Also write to runtime/trader/latest-social-brief.json (convenience)

    The trader cycle picks this up and can relay to Telegram.
    """
    # Primary write is handled by the caller (brief_json_path already written)
    # Write convenience latest pointer
    latest_path = RUNTIME_TRADER / "latest-social-brief.json"
    try:
        content = read_json(brief_json_path)
        if content:
            atomic_write_json(latest_path, content)
            print(f"[social-brief] Wrote latest-social-brief.json")
    except Exception as e:
        print(f"[social-brief] latest-social-brief.json write failed: {e}", file=sys.stderr)

    # Also write human-readable Markdown
    md_path = RUNTIME_TRADER / f"social-brief-{today_str()}.md"
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(brief_md, encoding="utf-8")
        print(f"[social-brief] Wrote {md_path.name}")
    except Exception as e:
        print(f"[social-brief] Markdown write failed: {e}", file=sys.stderr)

    # File a wiki/queries/ mirror so this synthesis compounds into the
    # knowledge base. Best-effort; never fatal to the main brief flow.
    try:
        from file_to_wiki_query import file_brief_to_wiki
        generated_at_str = result.get("generated_at") or now_iso()
        gen_dt = datetime.fromisoformat(generated_at_str.replace("Z", "+00:00"))
        hhmm = gen_dt.strftime("%H%M")
        cashtags = result.get("cashtags") or []
        # Map cashtags → related concepts when the cashtag's resolved
        # token has a project_links entry. Conservative — only the
        # canonical themes our concepts/ directory tracks.
        canonical_themes = {
            'AgentInfrastructure', 'AgentEconomy', 'AgentSwarm', 'TokenEconomy',
            'DeSoc', 'TagClaw', 'TagAI', 'Wormhole3', 'Projects',
            'MarketTrading', 'Payments', 'ATOC', 'AttentionEconomy', 'Bitcoin',
            'Ethereum', 'Philosophy', 'iweb3', 'PoB', 'SocialFi', 'Web3Identity',
        }
        # Heuristic: trader briefs are almost always TokenEconomy +
        # MarketTrading. Tag the most-common deeper themes too if the
        # brief mentions them by name.
        related: list[str] = ['[[concepts/TokenEconomy]]', '[[concepts/MarketTrading]]']
        for theme in canonical_themes:
            if theme in brief_md and f'[[concepts/{theme}]]' not in related:
                related.append(f'[[concepts/{theme}]]')
        tags = ['social-brief', 'trader', 'on-chain']
        if cashtags:
            tags.append(f'cashtags:{len(cashtags)}')
        title = f"Trader Social Brief — {gen_dt.strftime('%Y-%m-%d %H:%M UTC')}"
        out = file_brief_to_wiki(
            source_md_path=md_path,
            source_agent='trader',
            title=title,
            tags=tags,
            related_concepts=related,
            file_stem=f"trader-social-brief-{hhmm}",
        )
        if out:
            print(f"[social-brief] Filed wiki copy: {out}")
    except Exception as e:
        print(f"[social-brief] wiki/queries mirror failed (non-fatal): {e}", file=sys.stderr)

    return True


def write_pending_brief(result: dict, brief_md: str) -> bool:
    """Option A: Write PENDING_BRIEF.json for main-session delivery.

    The main session (which has Telegram access) polls this file every 2 minutes
    via scripts/push_pending_brief_to_telegram.py and delivers the full brief.
    Cron isolated sessions cannot use the message tool directly.
    """
    from datetime import timezone, timedelta
    generated_at = result.get("generated_at") or now_iso()

    # Cycle-level idempotency guard (2026-06-06): both the LLM cron (0 */3) and the
    # launchd fallback runner (:10, +10min) call this. Without a per-cycle check the
    # runner produced a SECOND full brief whenever fresh tweets appeared in the 10-min
    # gap (the tweet delta-filter only trims overlap, it doesn't dedupe whole briefs)
    # → duplicate Telegram briefs. Skip if a brief for the CURRENT 3h cycle already
    # exists (PENDING or already-claimed). Genuine fallback still fires if the cron
    # produced nothing this cycle.
    try:
        CST = timezone(timedelta(hours=8))  # cron tz = Asia/Shanghai; cycles at 0,3,6,...
        now_cst = datetime.now(CST)
        cycle_start = now_cst.replace(hour=(now_cst.hour // 3) * 3, minute=0, second=0, microsecond=0)
        for existing in (RUNTIME_TRADER / "PENDING_BRIEF.json", RUNTIME_TRADER / "PENDING_BRIEF.claimed.json"):
            if not existing.exists():
                continue
            try:
                prev = json.loads(existing.read_text(encoding="utf-8"))
                prev_at = datetime.fromisoformat(str(prev.get("generated_at", "")).replace("Z", "+00:00"))
                if prev_at >= cycle_start:
                    print(f"[social-brief] cycle no-op: brief for current 3h cycle (since {cycle_start.isoformat()}) "
                          f"already exists in {existing.name} (generated {prev.get('generated_at')}) — skipping duplicate")
                    return True
            except Exception:
                continue
    except Exception:
        pass  # guard must never block a legitimate write

    # Expires in 3 hours — if main session misses one cycle it will still deliver
    try:
        expires_dt = datetime.fromisoformat(
            generated_at.replace("Z", "+00:00")
        ) + timedelta(hours=3)
        expires_at = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        expires_at = ""

    pending: dict[str, Any] = {
        "schema": "trader.pending-brief.v1",
        "brief_id": generated_at,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "delivered": False,
        "telegram_chat_id": "7948500820",
        "brief_markdown": brief_md,
        "source_tweet_count": result.get("source_tweet_count") or 0,
        "cashtags": result.get("cashtags") or [],
        "date": result.get("date") or today_str(),
    }
    pending_path = RUNTIME_TRADER / "PENDING_BRIEF.json"
    try:
        atomic_write_json(pending_path, pending)
        print(f"[social-brief] Wrote PENDING_BRIEF.json (expires {expires_at}) — awaiting main-session delivery")
        return True
    except Exception as e:
        print(f"[social-brief] PENDING_BRIEF.json write failed: {e}", file=sys.stderr)
        return False


def compile_trader_llm_wiki(result: dict[str, Any]) -> None:
    """Best-effort knowledge-layer compile for the trader social brief."""
    try:
        from trader_llm_wiki_v1 import compile_brief_to_wiki

        wiki_result = compile_brief_to_wiki(result)
        signal_count = int(wiki_result.get("signal_count") or 0)
        brief_page = wiki_result.get("brief_page") or ""
        print(f"[social-brief] Trader LLM Wiki updated: signals={signal_count} page={brief_page}")
    except Exception as e:
        print(f"[social-brief] Trader LLM Wiki compile failed (non-fatal): {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------


def run_self_check() -> int:
    """Validate environment prerequisites."""
    errors = 0

    print("[social-brief] Running self-check...")

    # bird
    rc, out, _ = _run_bird(["--version"])
    if rc == 0:
        print(f"[OK] bird: {out.splitlines()[0] if out else 'available'} (auth_mode={SOCIAL_BRIEF_BIRD_AUTH_MODE})")
    else:
        print("[WARN] bird CLI not available (xurl will be used as fallback)")

    rc, out, err = _run_bird(["whoami"])
    if rc == 0 and out:
        preview = out.splitlines()[0][:200]
        print(f"[OK] social-brief bird whoami: {preview}")
    else:
        print(f"[WARN] social-brief bird whoami failed (auth_mode={SOCIAL_BRIEF_BIRD_AUTH_MODE}): {err or out or 'no output'}")

    # xurl (uses "xurl version" subcommand, not --version flag)
    rc, out, _ = _run(["xurl", "version"])
    if rc == 0:
        print(f"[OK] xurl: {out.splitlines()[0] if out else 'available'}")
    else:
        # xurl is present but version subcommand may differ — try help
        rc2, _, _ = _run(["xurl", "--help"])
        if rc2 == 0:
            print(f"[OK] xurl: available (version unknown)")
        else:
            print("[FAIL] xurl not available — no X data fetch capability")
            errors += 1

    # gmgn-cli
    rc, out, _ = _run(["gmgn-cli", "--version"])
    if rc == 0:
        print(f"[OK] gmgn-cli: {out.splitlines()[0] if out else 'available'}")
    else:
        print("[WARN] gmgn-cli not available — trading brief will be empty")

    # onchain-ticks.json
    ticks_path = RUNTIME_TRADER / "onchain-ticks.json"
    if ticks_path.exists():
        print(f"[OK] onchain-ticks.json exists ({ticks_path})")
    else:
        print("[WARN] onchain-ticks.json not found — cashtag→address mapping unavailable")

    # runtime/trader dir
    if RUNTIME_TRADER.exists():
        print(f"[OK] runtime/trader/ exists")
    else:
        print("[FAIL] runtime/trader/ not found — OPENCLAW_WORKSPACE may be misconfigured")
        errors += 1

    print(f"[social-brief] Self-check complete. errors={errors}")
    return errors


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def _load_last_brief_state() -> dict:
    """Read the prior delta marker. Returns empty dict if missing/corrupt."""
    if not LAST_BRIEF_STATE_PATH.exists():
        return {}
    try:
        d = json.loads(LAST_BRIEF_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {}
        return d
    except Exception:
        return {}


def _save_last_brief_state(generated_at: str, tweet_ids: list[str], tweet_count: int) -> None:
    """Persist delta marker after a successful brief is written to PENDING."""
    state = {
        "version": 1,
        "last_generated_at": generated_at,
        # Keep last 1000 IDs (~48h at 3h cadence). Was 200 when only KOL tweets
        # were tracked; now also stores cashtag/hashtag/project_intel IDs, so
        # per-cycle ID count is 5-10× larger and 200 only covered ~10h.
        "last_tweet_ids": list(tweet_ids)[-1000:],
        "last_tweet_count": tweet_count,
    }
    try:
        LAST_BRIEF_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(LAST_BRIEF_STATE_PATH.parent),
                                        suffix=".tmp", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            tmp = f.name
        os.replace(tmp, LAST_BRIEF_STATE_PATH)
    except Exception as e:
        print(f"[social-brief] WARN: could not save LAST_BRIEF_STATE.json: {e}", file=sys.stderr)


def _parse_tweet_dt(raw: str):
    """Best-effort parse for the createdAt field bird emits."""
    if not raw:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(str(raw))
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    try:
        s = str(raw)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _tweet_id_any(t: dict) -> str:
    """Extract a tweet ID across the brief's three tweet shapes:
      - KOL fetches (`fetch_user_tweets_bird`): top-level ``id`` / ``id_str``
      - cashtag/hashtag/project_intel summaries: only ``_raw.tweetId`` plus ``url``
        is preserved (no top-level id), so we must look deeper.
    Returns "" if no ID can be recovered. Used by _apply_delta_filter and
    tweet_ids_collected so dedup works uniformly across all content sources.
    """
    if not isinstance(t, dict):
        return ""
    tid = t.get("id") or t.get("id_str")
    if tid:
        return str(tid)
    raw = t.get("_raw") or {}
    if isinstance(raw, dict):
        rid = raw.get("tweetId") or raw.get("rest_id") or raw.get("id") or raw.get("id_str")
        if rid:
            return str(rid)
    url = t.get("url") or t.get("source_url") or ""
    if "/status/" in url:
        first = url.rsplit("/status/", 1)[-1].split("?")[0].split("/")[0]
        if first.isdigit():
            return first
    return ""


def _apply_delta_filter(tweets: list[dict], state: dict) -> list[dict]:
    """Keep only tweets that this brief has not already covered.

    Rules:
      - If state is empty (first run): keep the last DELTA_MIN_LOOKBACK_HOURS
        worth of tweets, but at most the most recent 10. This gives the first
        brief reasonable content without dumping a full day of history.
      - Otherwise: drop tweets whose id is in state['last_tweet_ids'] OR whose
        createdAt is older than state['last_generated_at']. Also enforce the
        DELTA_MAX_LOOKBACK_HOURS hard ceiling.
    """
    if not tweets:
        return tweets

    now = datetime.now(timezone.utc)
    max_cutoff = now - timedelta(hours=DELTA_MAX_LOOKBACK_HOURS)

    last_at_str = state.get("last_generated_at") or ""
    last_dt = _parse_tweet_dt(last_at_str) if last_at_str else None
    last_ids = set(state.get("last_tweet_ids") or [])

    # Cutoff: only the absolute MAX_LOOKBACK ceiling. We used to ALSO reject any
    # tweet older than the previous brief's timestamp, but that silently starved
    # downstream TagAI import for slow-tweeting KOLs — a tweet older than the
    # prior brief's mtime would be dropped EVEN IF it had never been seen before
    # (e.g. KOL tweets every 2 days, cron runs every 3h: the tweet is captured in
    # one brief, then every subsequent brief drops it as "older than last", so it
    # only ever reaches TagAI for that single ~3h window — and not at all if any
    # transient failure missed that window). ID-based dedup (`last_ids`) is the
    # correct mechanism for "already covered"; the time test was double-filtering.
    if last_dt is not None:
        cutoff = max_cutoff
    else:
        # First run — use the MIN lookback as cutoff so we have some content.
        cutoff = now - timedelta(hours=DELTA_MIN_LOOKBACK_HOURS)

    kept: list[dict] = []
    for t in tweets:
        tid = _tweet_id_any(t)
        if tid and tid in last_ids:
            continue
        ts_raw = t.get("createdAt") or t.get("created_at") or t.get("timestamp")
        dt = _parse_tweet_dt(ts_raw) if ts_raw else None
        if dt is not None and dt < cutoff:
            continue
        kept.append(t)
    return kept


def run_social_brief(target_users: list[str] | None = None) -> dict:
    """Execute full social brief pipeline for all target users. Returns merged result dict."""
    if target_users is None:
        target_users = TARGET_USERS
    ts = now_iso()
    errors: list[str] = []
    warnings: list[str] = []

    # Populate the exclusion cache for extract_quoted_authors
    global _target_users_lower_cache
    _target_users_lower_cache = [u.lower() for u in target_users]

    print(f"[social-brief] Starting social brief pipeline @ {ts}")
    print(f"[social-brief] Targets: @{', @'.join(target_users)}")

    # Delta-since-last-brief marker. We use it to filter out tweets already
    # covered by the previous successful brief, so each brief contains only
    # new content rather than re-iterating the same rolling 24h window.
    delta_state = _load_last_brief_state()
    last_at = delta_state.get("last_generated_at") or "(no prior state — first run)"
    print(f"[social-brief] Delta state: last_generated_at={last_at}, last_tweet_ids_count={len(delta_state.get('last_tweet_ids') or [])}")

    # Aggregate across all target users
    all_source_tweets: list[dict] = []
    all_cashtags: set[str] = set()
    all_hashtags: set[str] = set()
    all_addresses: set[str] = set()
    all_quoted_authors: set[str] = set()
    per_user: dict[str, dict] = {}
    delta_dropped_total = 0

    for target_user in target_users:
        # R1: Fetch source tweets
        source_tweets: list[dict] = []
        if not DRY_RUN:
            raw_tweets = fetch_user_tweets(target_user, limit=TWEET_FETCH_LIMIT)
            # Apply delta filter — drop tweets already covered by previous brief
            source_tweets = _apply_delta_filter(raw_tweets, delta_state)
            _dropped = len(raw_tweets) - len(source_tweets)
            delta_dropped_total += _dropped
            if _dropped:
                print(f"[social-brief] @{target_user}: dropped {_dropped} tweets already covered by previous brief; {len(source_tweets)} new tweets remain")
        else:
            print(f"[social-brief][DRY-RUN] Skipping tweet fetch for @{target_user}")
            source_tweets = []
            raw_tweets = []

        if not source_tweets:
            # Distinguish "bird fetched nothing" from "fetched OK but all delta-filtered".
            # The old message conflated these — leading to "No tweets fetched" warnings
            # in briefs where bird actually returned data, which masked the real problem.
            if raw_tweets:
                warnings.append(
                    f"No NEW tweets from @{target_user} "
                    f"({len(raw_tweets)} fetched, all already covered by delta filter)"
                )
            else:
                warnings.append(f"No tweets fetched from @{target_user}")

        # R2: Extract signals
        cashtags = extract_cashtags(source_tweets)
        hashtags = extract_hashtags(source_tweets)
        tweet_addresses = extract_contract_addresses(source_tweets)
        quoted_authors = extract_quoted_authors(source_tweets)

        all_source_tweets.extend(source_tweets)
        all_cashtags.update(cashtags)
        all_hashtags.update(hashtags)
        all_addresses.update(tweet_addresses)
        all_quoted_authors.update(quoted_authors)

        per_user[target_user] = {
            "tweet_count": len(source_tweets),
            "cashtags": cashtags,
            "hashtags": hashtags,
            "addresses": tweet_addresses,
            "quoted_authors": list(quoted_authors),
        }

        print(f"[social-brief] @{target_user}: cashtags={cashtags} "
              f"hashtags={hashtags} addresses={tweet_addresses} "
              f"quoted={len(quoted_authors)}")

    onchain_map = load_onchain_ticks_addresses()
    print(f"[social-brief] Aggregated: cashtags={sorted(all_cashtags)} "
          f"hashtags={sorted(all_hashtags)} addresses={sorted(all_addresses)} "
          f"onchain_ticks={len(onchain_map)} quoted_authors={len(all_quoted_authors)}")

    # R3: Trading brief (deduped by address across all users)
    trading_brief = build_trading_brief(list(all_addresses), list(all_cashtags), onchain_map)

    # R4: Project social intel — quoted authors + token-derived project Twitters.
    # When a tweet mentions a ticker we resolved to a CA, gmgn / dexscreener
    # often expose the project's official Twitter handle. We feed those into
    # the same recent-tweet flow so the brief tracks the project's own
    # announcements alongside accounts the watched users quoted.
    project_intel: list[dict] = []
    targets_lower = {u.lower() for u in target_users}
    sources_by_handle: dict[str, list[str]] = {}
    for handle in all_quoted_authors:
        if not handle or handle.lower() in targets_lower:
            continue
        sources_by_handle.setdefault(handle, []).append("quoted")
    for tok in (trading_brief.get("tokens") or []):
        handle = tok.get("project_twitter")
        ticker = tok.get("ticker") or ""
        if not handle or handle.lower() in targets_lower:
            continue
        # Dedupe by case-insensitive handle match against existing entries.
        existing = next((k for k in sources_by_handle if k.lower() == handle.lower()), None)
        key = existing or handle
        sources_by_handle.setdefault(key, []).append(f"${ticker}" if ticker else "token")
    accounts = [{"username": u, "sources": s} for u, s in sources_by_handle.items()]
    print(f"[social-brief] Project accounts to track: "
          f"{[(a['username'], a['sources']) for a in accounts]}")
    if accounts and not DRY_RUN:
        project_intel = build_project_social_intel(accounts)

    # R5: Social search (deduped cashtags + hashtags)
    all_cashtags_list = sorted(all_cashtags)
    all_hashtags_list = sorted(all_hashtags)
    social_search: dict = {"cashtag_search": {}, "hashtag_search": {}}
    if (all_cashtags_list or all_hashtags_list) and not DRY_RUN:
        social_search = build_social_search_intel(all_cashtags_list, all_hashtags_list)

    # Preserve social_search from previous brief if current run produced nothing.
    # Root cause guard: when delta dedup drops all tweets, all_cashtags_list is
    # empty → build_social_search_intel is never called → social_search stays as
    # empty dicts, silently overwriting a valid prior run's data.
    if not social_search.get("cashtag_search") and not social_search.get("hashtag_search"):
        _prev_brief_path = RUNTIME_TRADER / f"social-brief-{today_str()}.json"
        try:
            _prev = json.loads(_prev_brief_path.read_text(encoding="utf-8"))
            _prev_ss = _prev.get("social_search", {})
            if _prev_ss.get("cashtag_search") or _prev_ss.get("hashtag_search"):
                social_search = _prev_ss
                print(
                    "[social-brief] social_search empty this cycle — "
                    "preserved from previous brief to avoid overwrite.",
                    file=sys.stderr,
                )
        except Exception:
            pass

    # Apply ID-based dedup to cashtag/hashtag/project_intel against last_tweet_ids
    # — previously only KOL tweets went through the delta filter, so cashtag content
    # was re-pushed every 3h regardless of whether it had appeared before. Reuses
    # _apply_delta_filter (ID-only after the 2026-06-05 edit, with MAX_LOOKBACK hard
    # ceiling), so semantics match KOL filtering.
    for kind in ("cashtag_search", "hashtag_search"):
        sub = social_search.get(kind) or {}
        for key, tweets in list(sub.items()):
            if isinstance(tweets, list):
                sub[key] = _apply_delta_filter(tweets, delta_state)
    # project_intel is a list of {username, tweets[]} — filter each project's
    # nested tweets list, then drop entries that ended up empty after dedup.
    for proj in project_intel or []:
        if isinstance(proj, dict) and isinstance(proj.get("tweets"), list):
            proj["tweets"] = _apply_delta_filter(proj["tweets"], delta_state)
    project_intel = [
        p for p in (project_intel or [])
        if isinstance(p, dict) and (p.get("tweets") or [])
    ]

    # R6: Generate merged brief
    brief_md = generate_markdown_brief(
        target_user=", ".join(target_users),
        source_tweets=all_source_tweets,
        cashtags=all_cashtags_list,
        hashtags=all_hashtags_list,
        addresses=sorted(all_addresses),
        trading_brief=trading_brief,
        social_search=social_search,
        project_intel=project_intel,
        ts=ts,
    )

    # Build output JSON
    result: dict[str, Any] = {
        "schema": "trader.social-brief.v1",
        "generated_at": ts,
        "date": today_str(),
        "target_users": target_users,
        "per_user": per_user,
        "status": "ok" if not errors else "partial",
        "source_tweet_count": len(all_source_tweets),
        "cashtags": all_cashtags_list,
        "hashtags": all_hashtags_list,
        "tweet_addresses": sorted(all_addresses),
        "quoted_authors": sorted(all_quoted_authors),
        "trading_brief": trading_brief,
        "social_search": social_search,
        "project_intel": project_intel,
        "brief_markdown": brief_md,
        "errors": errors,
        "warnings": warnings,
    }

    # Delta-since-last-brief gate (2026-05-19): if every fetched tweet was
    # filtered out by the delta check, this cycle has nothing new to publish.
    # Skip writing PENDING_BRIEF so the pusher does not deliver a re-package
    # of the previous brief's content. The latest-social-brief.json and
    # social-brief-YYYY-MM-DD.json are still updated for archival.
    #
    # 2026-06-05: broadened to consider ALL content sources, not just KOL tweets.
    # The KOL-only gate caused 12h+ of Telegram silence whenever KOLs went quiet,
    # even though cashtag/hashtag had plenty of new content. Now any new tweet
    # from any source — KOL, cashtag, hashtag, project_intel — counts.
    _cs = (social_search or {}).get("cashtag_search") or {}
    _hs = (social_search or {}).get("hashtag_search") or {}
    cashtag_new_n = sum(len(v) for v in _cs.values() if isinstance(v, list))
    hashtag_new_n = sum(len(v) for v in _hs.values() if isinstance(v, list))
    project_intel_new_n = sum(
        len(p.get("tweets") or [])
        for p in (project_intel or [])
        if isinstance(p, dict)
    )
    has_new_content = (
        len(all_source_tweets) > 0
        or cashtag_new_n > 0
        or hashtag_new_n > 0
        or project_intel_new_n > 0
    )
    result["delta"] = {
        "previous_generated_at": delta_state.get("last_generated_at"),
        "dropped_already_covered": delta_dropped_total,
        "kept_new_tweets": len(all_source_tweets),
        "kept_new_cashtag": cashtag_new_n,
        "kept_new_hashtag": hashtag_new_n,
        "kept_new_project_intel": project_intel_new_n,
        "has_new_content": has_new_content,
    }

    # Write output files (R6 / NOTE9)
    brief_path = RUNTIME_TRADER / f"social-brief-{today_str()}.json"
    if not DRY_RUN:
        atomic_write_json(brief_path, result)
        print(f"[social-brief] Wrote {brief_path.name}")
        send_brief_via_trader(brief_md, brief_path)
        compile_trader_llm_wiki(result)
        # Auto-import tweets to TagAI after brief JSON is written
        try:
            from lib.tagai_brief_import import import_brief_tweets_full
            _tagai_result = import_brief_tweets_full(result, dry_run=False, fetch_replies=True)
            _ok = _tagai_result.get('ok', 0)
            _fail = _tagai_result.get('fail', 0)
            _total = _tagai_result.get('total', 0)
            if _total > 0:
                print(f'[social-brief] TagAI import: {_ok} OK, {_fail} FAIL, {_total} total')
            else:
                print(f'[social-brief] TagAI import: no tweets to import')
        except Exception as _tagai_exc:
            print(f'[social-brief] TagAI import failed (non-fatal): {_tagai_exc}')
        # Option A: write delivery marker for main-session Telegram push
        # IFF this cycle actually produced new content. Otherwise we silently
        # noop the push — no Telegram spam re-iterating the same data.
        if result.get("status") in ("ok", "partial") and has_new_content:
            write_pending_brief(result, brief_md)
            # Stamp delta marker so next cycle knows where we stopped.
            # Collect IDs from ALL content sources (was: only KOL all_source_tweets).
            # Without this, cashtag/hashtag tweets would re-trigger pushes every 3h
            # because nothing recorded that we'd already seen them.
            _cs_lists = ((social_search or {}).get("cashtag_search") or {}).values()
            _hs_lists = ((social_search or {}).get("hashtag_search") or {}).values()
            _all_for_dedup = (
                list(all_source_tweets)
                + [t for sub in _cs_lists if isinstance(sub, list) for t in sub]
                + [t for sub in _hs_lists if isinstance(sub, list) for t in sub]
                + [t for p in (project_intel or []) if isinstance(p, dict)
                   for t in (p.get("tweets") or [])]
            )
            tweet_ids_collected = [
                tid for tid in (_tweet_id_any(t) for t in _all_for_dedup) if tid
            ]
            _save_last_brief_state(
                generated_at=ts,
                tweet_ids=tweet_ids_collected,
                tweet_count=len(all_source_tweets),
            )
            print(f"[social-brief] Saved delta marker: generated_at={ts} tweet_ids_count={len(tweet_ids_collected)}")
        else:
            print(
                f"[social-brief] Skipping PENDING_BRIEF write — has_new_content={has_new_content}, "
                f"dropped {delta_dropped_total} tweets already covered. No Telegram push this cycle.",
                file=sys.stderr,
            )
    else:
        print(f"[social-brief][DRY-RUN] Would write {brief_path.name}")

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    if SELF_CHECK:
        return run_self_check()

    try:
        result = run_social_brief(TARGET_USERS)
    except Exception as e:
        import traceback
        print(f"[social-brief] FATAL: {e}", file=sys.stderr)
        traceback.print_exc()
        # Write error result
        err_result = {
            "schema": "trader.social-brief.v1",
            "generated_at": now_iso(),
            "date": today_str(),
            "target_users": TARGET_USERS,
            "status": "blocked",
            "errors": [str(e)],
        }
        if not DRY_RUN:
            atomic_write_json(RUNTIME_TRADER / f"social-brief-{today_str()}.json", err_result)
        return 1

    status = result.get("status", "blocked")
    tokens_found = len((result.get("trading_brief") or {}).get("tokens") or [])
    tweets_fetched = result.get("source_tweet_count") or 0
    print(f"[social-brief] Complete: status={status} tweets={tweets_fetched} tokens={tokens_found}")

    return 0 if status in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""run_bookmarker_runtime_v1.py — Native bookmarker cycle runtime.

Replaces the dev-claude.sh / claude CLI dependency with a self-contained
Python runtime that handles the bookmarker social curation cycle:

  1. Read feed from TagClaw API
  2. Score and select posts for curation
  3. Execute curation actions (like/curate)
  4. Write result.json and latest.json

No LLM dependency. Uses TagClaw API directly.

Usage (called by bookmarker-cycle.sh):
    cd $WORKSPACE && python3 scripts/run_bookmarker_runtime_v1.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))
RUNTIME_BOOKMARKER = WORKSPACE / "runtime" / "bookmarker"
RUNTIME_SHARED = WORKSPACE / "runtime" / "shared"
RAW_BOOKMARKER = WORKSPACE / "raw" / "bookmarker"
CONFIG_DIR = WORKSPACE / "config"
BEHAVIOR_FILE = WORKSPACE / "agents" / "bookmarker.md"

# TAS_social weights — mirror compute_tas_social_v2.py so operator-facing
# semantics stay stable across execution backends.
ALIGN_WEIGHTS = {"like": 1, "curation": 3, "comment": 5, "retweet": 3}
ALIGN_NORMALIZE = 4.0
ALIGN_CAP = 5.0
COMMUNITY_NORMALIZE = 20.0
COMMUNITY_CAP = 5.0
WEIGHT_ALIGN = 0.7
WEIGHT_COMMUNITY = 0.3


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent),
                                     suffix=".tmp", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = f.name
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# TagClaw API
# ---------------------------------------------------------------------------

def resolve_api_key() -> str:
    """Resolve TagClaw API key from skill env or legacy credentials."""
    skill_env = WORKSPACE / "skills" / "tagclaw" / ".env"
    if skill_env.exists():
        for line in skill_env.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if k == "TAGCLAW_API_KEY" and v:
                return v

    legacy = Path.home() / ".config" / "tagclaw" / "credentials.json"
    if legacy.exists():
        try:
            creds = json.loads(legacy.read_text())
            return creds.get("apiKey") or creds.get("api_key") or creds.get("API_KEY") or ""
        except Exception:
            pass
    return ""


def tagclaw_get(endpoint: str, api_key: str,
                base_url: str = "https://bsc-api.tagai.fun/tagclaw") -> dict | list | None:
    """HTTP GET against TagClaw API. Returns parsed JSON or None.

    ``base_url`` may be overridden to reach sibling namespaces on the same
    host (e.g. the ``/curation`` endpoints).
    """
    import urllib.request
    import urllib.error

    url = f"{base_url}{endpoint}"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def tagclaw_post(endpoint: str, api_key: str, data: dict) -> dict | None:
    """HTTP POST against TagClaw API."""
    import urllib.request
    import urllib.error

    base_url = "https://bsc-api.tagai.fun/tagclaw"
    url = f"{base_url}{endpoint}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Curation logic
# ---------------------------------------------------------------------------

def _strip_inline_comment(value: str) -> str:
    """Strip a trailing ``# ...`` comment from a YAML scalar value.

    Respects single and double quoted strings so that ``#`` inside quotes
    is preserved.  Returns the trimmed string.
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            # Only treat ``#`` as a comment marker when preceded by whitespace
            # or at the start of the value; this avoids stripping ``#tag`` in
            # an unquoted scalar (YAML allows it, but conventionally we quote).
            if i == 0 or value[i - 1].isspace():
                return value[:i].rstrip()
    return value.rstrip()


def _coerce_scalar(value: str) -> Any:
    """Coerce a raw YAML scalar string into a Python typed value."""
    if value == "" or value.lower() in ("null", "~"):
        return None
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    # Numeric coercion — try int first, then float.
    try:
        if value.startswith(("0x", "-0x", "+0x", "0X", "-0X", "+0X")):
            return int(value, 16)
        if "." in value or "e" in value.lower():
            return float(value)
        return int(value)
    except ValueError:
        pass
    # Quoted string
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _fallback_yaml_load(text: str) -> dict:
    """Minimal indentation-aware YAML loader for nested mappings.

    Supports the subset used by agency.config.yaml: nested mappings, scalar
    values with optional inline ``#`` comments, and quoted strings.  Lists
    are not supported — callers should prefer ``yaml.safe_load`` when
    available.  Returns ``{}`` on unrecoverable parse failure.
    """
    root: dict[str, Any] = {}
    # Stack of (indent, container).  Root container lives at indent -1.
    stack: list[tuple[int, dict]] = [(-1, root)]

    for raw_line in text.splitlines():
        # Drop full-line comments and blanks.
        stripped_full = raw_line.strip()
        if not stripped_full or stripped_full.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        # Unindent to the enclosing container.
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            # Corrupt indentation — bail out so caller can use defaults.
            return root
        parent = stack[-1][1]

        line = raw_line.strip()
        if ":" not in line:
            # Unsupported construct (e.g. list item) — skip rather than crash.
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = _strip_inline_comment(rest.strip())
        if rest == "":
            # Opens a nested mapping.
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _coerce_scalar(rest)
    return root


def load_config() -> dict:
    """Load agency config for social settings.

    Uses ``yaml.safe_load`` when PyYAML is available, otherwise falls back
    to a minimal indentation-aware parser.  Inline ``#`` comments and
    nested mappings are handled correctly in both paths.  Always returns a
    dict (empty on failure) so callers can proceed with defaults.
    """
    config_path = CONFIG_DIR / "agency.config.yaml"
    if not config_path.exists():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception:
        return {}

    try:
        import yaml  # type: ignore
        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return loaded
        return {}
    except ImportError:
        pass
    except Exception:
        # PyYAML present but config malformed — fall back to best effort.
        pass

    try:
        return _fallback_yaml_load(text)
    except Exception:
        return {}


def _coerce_pct(value: Any, default: float = 0.6) -> float:
    """Coerce a config percentage into a float clamped to [0.0, 1.0].

    Handles dirty strings (e.g. ``'0.60 # 60%'``) left over from legacy
    parsers, as well as ints, floats, and None.  Never raises.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        candidate = _strip_inline_comment(value).strip().strip("'\"")
        try:
            out = float(candidate)
        except (ValueError, TypeError):
            return max(0.0, min(1.0, default))
    else:
        return max(0.0, min(1.0, default))
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return max(0.0, min(1.0, default))
    return max(0.0, min(1.0, out))


def resolve_curation_vp_pct(config: dict, default: float = 0.6) -> tuple[float, str | None]:
    """Resolve ``curation_vp_pct`` from nested or flat config.

    Returns (value, warning).  ``warning`` is ``None`` on clean reads; a
    short human-readable string when the raw value was missing, malformed,
    or clamped — so operators can see *why* the runtime fell back to the
    default.
    """
    raw: Any = None
    source = "default"
    social = config.get("social")
    if isinstance(social, dict) and "curation_vp_pct" in social:
        raw = social["curation_vp_pct"]
        source = "social.curation_vp_pct"
    elif "curation_vp_pct" in config:
        raw = config["curation_vp_pct"]
        source = "curation_vp_pct (flat)"

    if raw is None:
        return (default, f"config fallback: curation_vp_pct missing, using default={default}")

    coerced = _coerce_pct(raw, default=default)
    # Compare against the "clean" float interpretation of raw to detect
    # whether we had to repair a dirty value.
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if abs(float(raw) - coerced) < 1e-9:
            return (coerced, None)
        return (coerced, f"config clamp: {source}={raw!r} clamped to {coerced}")
    # Everything else (strings, unknown types) counts as a repair.
    return (coerced, f"config repair: {source}={raw!r} coerced to {coerced}")


def score_post(post: dict) -> float:
    """Simple heuristic scoring for curation candidate posts."""
    score = 0.0

    # Engagement signals
    likes = post.get("likes", 0) or 0
    replies = post.get("replies", 0) or 0
    curates = post.get("curates", 0) or 0
    score += min(likes * 0.5, 5.0)
    score += min(replies * 1.0, 5.0)
    score += min(curates * 0.3, 3.0)

    # Content quality signals
    content = post.get("content", "") or ""
    word_count = len(content.split())
    if 20 <= word_count <= 300:
        score += 2.0  # reasonable length
    if word_count < 5:
        score -= 2.0  # too short

    # Recency bonus
    created = post.get("created_at") or post.get("createdAt")
    if created:
        try:
            ts = created.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if age_hours < 1:
                score += 3.0
            elif age_hours < 6:
                score += 1.5
            elif age_hours < 24:
                score += 0.5
        except Exception:
            pass

    return max(score, 0.0)


def load_agency_identity() -> dict:
    """Load agency-identity.json from the workspace (agent/owner handles).

    Returns an empty dict when the file is missing or malformed so callers
    can proceed with defaults. Never raises.
    """
    path = CONFIG_DIR / "agency-identity.json"
    data = read_json(path) or {}
    return data if isinstance(data, dict) else {}


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def run_curation_cycle() -> dict:
    """Execute one bookmarker curation cycle. Returns result dict."""
    ts_start = now_iso()
    api_key = resolve_api_key()
    actions_taken: list[dict] = []
    errors: list[str] = []
    feed_size = 0

    # 1. Fetch feed
    feed_raw = tagclaw_get("/feed", api_key)
    feed_fetch_ok = feed_raw is not None
    if not feed_fetch_ok:
        errors.append("Failed to fetch feed from TagClaw API")
        feed = []
    elif isinstance(feed_raw, dict):
        feed = feed_raw.get("posts") or feed_raw.get("items") or feed_raw.get("data") or []
    else:
        feed = feed_raw if isinstance(feed_raw, list) else []
    feed_size = len(feed)

    # 2. Score and rank posts
    scored: list[tuple[float, dict]] = []
    for post in feed:
        if not isinstance(post, dict):
            continue
        s = score_post(post)
        scored.append((s, post))
    scored.sort(key=lambda x: x[0], reverse=True)

    # 3. Select top candidates for curation (conservative: max 3 per cycle)
    config = load_config()
    config_warnings: list[str] = []
    curation_vp_pct, warn = resolve_curation_vp_pct(config, default=0.6)
    if warn:
        config_warnings.append(warn)
    max_curations = min(int(curation_vp_pct * 5), 3)
    candidates = scored[:max_curations]

    # 4. Execute curation actions
    for score_val, post in candidates:
        post_id = post.get("id") or post.get("postId") or post.get("post_id")
        if not post_id:
            continue

        # Try curate action
        result = tagclaw_post("/curate", api_key, {"postId": str(post_id)})
        if result is not None:
            actions_taken.append({
                "action": "curate",
                "post_id": str(post_id),
                "score": round(score_val, 2),
                "status": "ok"
            })
        else:
            # Fallback: try like
            result = tagclaw_post("/like", api_key, {"postId": str(post_id)})
            if result is not None:
                actions_taken.append({
                    "action": "like",
                    "post_id": str(post_id),
                    "score": round(score_val, 2),
                    "status": "ok"
                })
            else:
                actions_taken.append({
                    "action": "curate",
                    "post_id": str(post_id),
                    "score": round(score_val, 2),
                    "status": "failed"
                })

    # 5. Build result
    ok_actions = [a for a in actions_taken if a["status"] == "ok"]
    status = "ok" if not errors else ("partial" if ok_actions else "blocked")

    return {
        "schema": "bookmarker.result.v1",
        "status": status,
        "started_at": ts_start,
        "completed_at": now_iso(),
        "feed_size": feed_size,
        "candidates_scored": len(scored),
        "actions_taken": actions_taken,
        "actions_ok": len(ok_actions),
        "actions_failed": len(actions_taken) - len(ok_actions),
        "errors": errors,
        "config_warnings": config_warnings,
        "curation_vp_pct": curation_vp_pct,
        "execution_backend": "native-python",
        # Internal: pass raw data for canonical output publishing
        "_scored_posts": scored,
        "_feed_fetch_ok": feed_fetch_ok,
        "_feed_raw_sample": feed[:10] if isinstance(feed, list) else [],
        "_api_key_present": bool(api_key),
    }


# ---------------------------------------------------------------------------
# TAS_social native publisher — bookmarker is sole owner (2026-03-25)
# ---------------------------------------------------------------------------

def _fetch_own_posts_24h(api_key: str, own_username: str) -> tuple[list[dict], str, str | None]:
    """Fetch this agent's own posts in the last 24h.

    Tries authenticated /feed/me first (returns participation flags). Falls
    back to public /feed filtered by username. Returns (posts, source, error).
    """
    if not own_username:
        return [], "unknown", "agency-identity missing agent username"

    now_dt = datetime.now(timezone.utc)
    window_start = now_dt - timedelta(hours=24)
    own_username_lc = str(own_username).lower()

    def _coerce(resp: Any) -> list:
        if resp is None:
            return []
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            for key in ("posts", "tweets", "items", "data"):
                val = resp.get(key)
                if isinstance(val, list):
                    return val
        return []

    def _filter(posts: list) -> list[dict]:
        eligible: list[dict] = []
        for t in posts:
            if not isinstance(t, dict):
                continue
            username = str(t.get("twitterUsername") or t.get("username") or t.get("author") or "").lower()
            if own_username_lc and username and username != own_username_lc:
                continue
            ts = _parse_ts(t.get("tweetTime") or t.get("createdAt") or t.get("created_at"))
            if ts and ts < window_start:
                continue
            tid = t.get("tweetId") or t.get("id") or t.get("postId")
            if not tid:
                continue
            eligible.append({
                "id": str(tid),
                "created_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None,
                "content": str(t.get("content") or "")[:160],
                "likes": int(t.get("likeCount") or t.get("likes") or 0),
                "retweets": int(t.get("retweetCount") or t.get("retweets") or 0),
                "replies": int(t.get("replyCount") or t.get("replies") or 0),
                "tick": t.get("tick") or "",
            })
        return eligible

    # Preferred: authenticated /feed/me
    resp = tagclaw_get("/feed/me?pages=0&limit=50", api_key) if api_key else None
    posts = _coerce(resp)
    if posts:
        return _filter(posts), "/feed/me", None

    # Fallback: public /feed filtered by username
    resp = tagclaw_get("/feed", api_key)
    posts = _coerce(resp)
    if posts:
        return _filter(posts), "/feed", None

    return [], "unavailable", "could not fetch /feed/me or /feed"


def _compute_align_via_api(api_key: str, post_ids: list[str],
                           owner_twitter_id: str, owner_username: str
                           ) -> tuple[dict[str, int] | None, str]:
    """Check owner interactions across the given post IDs via curation endpoints.

    Returns (signals_dict, source_label). signals_dict is None when the
    curation endpoints are all unreachable. Probes at most 5 posts to keep
    the cycle cheap.
    """
    if not api_key or not post_ids:
        return None, "skipped"
    if not owner_twitter_id and not owner_username:
        return None, "no-owner-binding"

    signals = {k: 0 for k in ALIGN_WEIGHTS}
    any_success = False
    owner_username_lc = str(owner_username or "").lower()
    owner_twitter_id_s = str(owner_twitter_id or "")

    curation_base = "https://bsc-api.tagai.fun/curation"
    for post_id in post_ids[:5]:
        # Curators (likes / curations)
        curate_resp = tagclaw_get(
            f"/tweetCurateList?tweetId={post_id}", api_key, base_url=curation_base
        )
        if isinstance(curate_resp, dict):
            curate_list = (
                curate_resp.get("data") or curate_resp.get("curateList")
                or curate_resp.get("list") or curate_resp.get("curations") or []
            )
            if isinstance(curate_list, list):
                any_success = True
                for entry in curate_list:
                    if not isinstance(entry, dict):
                        continue
                    tid = str(entry.get("twitterId") or entry.get("userId") or "")
                    uname = str(entry.get("twitterUsername") or "").lower()
                    if (owner_twitter_id_s and tid == owner_twitter_id_s) or \
                       (owner_username_lc and uname == owner_username_lc):
                        signals["like"] += 1

        # Replies
        reply_resp = tagclaw_get(
            f"/getReplyOfTweet?tweetId={post_id}&pages=0", api_key, base_url=curation_base
        )
        if isinstance(reply_resp, dict):
            reply_list = (
                reply_resp.get("tweets") or reply_resp.get("data")
                or reply_resp.get("list") or reply_resp.get("replies") or []
            )
            if isinstance(reply_list, list):
                any_success = True
                for entry in reply_list:
                    if not isinstance(entry, dict):
                        continue
                    tid = str(entry.get("twitterId") or entry.get("userId") or "")
                    uname = str(entry.get("twitterUsername") or "").lower()
                    if (owner_twitter_id_s and tid == owner_twitter_id_s) or \
                       (owner_username_lc and uname == owner_username_lc):
                        signals["comment"] += 1

    return (signals, "curation-endpoints") if any_success else (None, "inconclusive")


def compute_native_tas_social(api_key: str, identity: dict,
                              api_key_present: bool) -> dict:
    """Compute a conservative, native TAS_social for the bookmarker runtime.

    Uses /feed/me (preferred) or /feed to gather this agent's own posts in
    the rolling 24h window, computes a Track B (community) score from
    aggregate engagement, and opportunistically probes curation endpoints
    for Track A (owner interactions). When the computation is blocked at
    any step, returns an explicit blocked payload with ``null_reason`` so
    downstream aggregation can display something more useful than a bare
    dash.
    """
    now_dt = datetime.now(timezone.utc)
    ts_now = now_iso()
    window_start = now_dt - timedelta(hours=24)

    agent = identity.get("agent") if isinstance(identity, dict) else None
    owner = identity.get("owner") if isinstance(identity, dict) else None
    own_username = ""
    if isinstance(agent, dict):
        own_username = str(agent.get("username") or "").strip()
    owner_twitter_id = ""
    owner_username = ""
    if isinstance(owner, dict):
        owner_twitter_id = str(owner.get("twitter_id") or "").strip()
        owner_username = str(owner.get("twitter_handle") or "").strip().lstrip("@")

    if not api_key_present:
        return {
            "schema": "bookmarker.tas-social.v1",
            "status": "blocked",
            "generated_at": ts_now,
            "updated_at": ts_now,
            "value": None,
            "align_score": None,
            "community_score": None,
            "null_reason": "blocked: no TagClaw API key configured",
            "display_status": "blocked",
            "source_class": "bookmarker-native",
            "execution_backend": "native-python",
            "formula": f"TAS_social = min(5.0, {WEIGHT_ALIGN}×align_score + {WEIGHT_COMMUNITY}×community_score)",
            "window": {"start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "end": ts_now, "hours": 24},
            "inputs": {
                "own_username": own_username or None,
                "owner_username": owner_username or None,
            },
            "errors": ["missing_api_key"],
        }

    if not own_username:
        return {
            "schema": "bookmarker.tas-social.v1",
            "status": "blocked",
            "generated_at": ts_now,
            "updated_at": ts_now,
            "value": None,
            "align_score": None,
            "community_score": None,
            "null_reason": "blocked: agency-identity.json missing agent.username",
            "display_status": "blocked",
            "source_class": "bookmarker-native",
            "execution_backend": "native-python",
            "formula": f"TAS_social = min(5.0, {WEIGHT_ALIGN}×align_score + {WEIGHT_COMMUNITY}×community_score)",
            "window": {"start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "end": ts_now, "hours": 24},
            "inputs": {"own_username": None, "owner_username": owner_username or None},
            "errors": ["missing_agent_identity"],
        }

    eligible, feed_source, feed_error = _fetch_own_posts_24h(api_key, own_username)

    if feed_error and not eligible:
        return {
            "schema": "bookmarker.tas-social.v1",
            "status": "blocked",
            "generated_at": ts_now,
            "updated_at": ts_now,
            "value": None,
            "align_score": None,
            "community_score": None,
            "null_reason": f"blocked: {feed_error}",
            "display_status": "blocked",
            "source_class": "bookmarker-native",
            "execution_backend": "native-python",
            "feed_source": feed_source,
            "formula": f"TAS_social = min(5.0, {WEIGHT_ALIGN}×align_score + {WEIGHT_COMMUNITY}×community_score)",
            "window": {"start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "end": ts_now, "hours": 24},
            "inputs": {"own_username": own_username, "owner_username": owner_username or None},
            "errors": [feed_error],
        }

    # Track B — community (aggregate engagement on our own posts in 24h window)
    total_likes = sum(p.get("likes", 0) for p in eligible)
    total_retweets = sum(p.get("retweets", 0) for p in eligible)
    total_replies = sum(p.get("replies", 0) for p in eligible)
    total_community = total_likes + total_retweets + total_replies
    community_score = min(COMMUNITY_CAP, (total_community / COMMUNITY_NORMALIZE) * COMMUNITY_CAP)

    # Track A — owner alignment via curation endpoints (opportunistic)
    post_ids = [str(p.get("id")) for p in eligible if p.get("id")]
    align_signals, align_source = _compute_align_via_api(
        api_key, post_ids, owner_twitter_id, owner_username
    )
    if align_signals is not None:
        raw_align = sum(align_signals.get(k, 0) * ALIGN_WEIGHTS[k] for k in ALIGN_WEIGHTS)
        align_score = min(ALIGN_CAP, raw_align / ALIGN_NORMALIZE) if raw_align > 0 else 0.0
        align_track_status = "ok"
    else:
        # Preserve conservative behavior from compute_tas_social_v2.py: align=0
        # when owner interactions cannot be observed (no prior-TAS leakage).
        raw_align = 0
        align_score = 0.0
        align_track_status = "inconclusive"

    tas_social = min(5.0, WEIGHT_ALIGN * align_score + WEIGHT_COMMUNITY * community_score)

    if not eligible:
        status = "partial"
        null_reason = "partial: no own posts in 24h window — TAS_social reflects blank window"
    elif align_track_status != "ok":
        status = "partial"
        null_reason = "partial: align track inconclusive (curation endpoints unreachable)"
    else:
        status = "ok"
        null_reason = None

    return {
        "schema": "bookmarker.tas-social.v1",
        "status": status,
        "generated_at": ts_now,
        "updated_at": ts_now,
        "value": round(tas_social, 4),
        "align_score": round(align_score, 4),
        "community_score": round(community_score, 4),
        "display_status": status,
        "null_reason": null_reason,
        "source_class": "bookmarker-native",
        "execution_backend": "native-python",
        "feed_source": feed_source,
        "formula": f"TAS_social = min(5.0, {WEIGHT_ALIGN}×align_score + {WEIGHT_COMMUNITY}×community_score)",
        "window": {
            "start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": ts_now,
            "hours": 24,
        },
        "community_signals": {
            "total_likes": total_likes,
            "total_retweets": total_retweets,
            "total_replies": total_replies,
            "total_interactions": total_community,
        },
        "community_source": feed_source,
        "track_a_detail": {
            "scorer": f"@{owner_username}" if owner_username else None,
            "target": f"@{own_username} posts",
            "window_hours": 24,
            "raw_align": raw_align,
            "signals": align_signals or {k: 0 for k in ALIGN_WEIGHTS},
            "source": align_source,
            "status": align_track_status,
            "fallback_rule": "align_score=0 when no in-window owner interaction (no prior-TAS leakage)",
        },
        "track_b_detail": {
            "post_count": len(eligible),
            "post_ids": post_ids[:10],
            "source": feed_source,
        },
        "inputs": {
            "own_username": own_username,
            "owner_username": owner_username or None,
            "eligible_posts": eligible[:10],
        },
        "normalization": {
            "align": f"raw_align / {ALIGN_NORMALIZE} capped at {ALIGN_CAP}",
            "community": f"total_interactions / {COMMUNITY_NORMALIZE} × {COMMUNITY_CAP} capped at {COMMUNITY_CAP}",
        },
        "notes": [
            "native bookmarker-owned TAS_social (2026-04-16)",
            "owner alignment is opportunistic; align=0 on inconclusive endpoints",
        ],
    }


# ---------------------------------------------------------------------------
# Canonical runtime output publishers
# ---------------------------------------------------------------------------

def publish_bookmarker_canonical(result: dict, ts_now: str) -> None:
    """Publish canonical runtime JSON files that dashboard and input-packet read.

    Files: topic-brief, source-health, content-candidates, social-drafts,
           autonomy-intent.  Written after every cycle so dashboard never
    shows stale bootstrap/null data.
    """
    status = result.get("status", "blocked")
    scored = result.get("_scored_posts", [])
    actions = result.get("actions_taken", [])
    errors = result.get("errors", [])
    config_warnings = result.get("config_warnings") or []

    # ── topic-brief.json ─────────────────────────────────────────────────
    # Extract topic keywords from scored post content
    word_freq: dict[str, int] = {}
    for _, post in scored:
        content = (post.get("content") or "").lower()
        for word in content.split():
            word = word.strip(".,!?#@()[]{}<>\"'")
            if len(word) >= 4 and word.isalpha():
                word_freq[word] = word_freq.get(word, 0) + 1
    top_keywords = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]

    high_signal = sum(1 for s, _ in scored if s >= 5.0)
    urgency = "high" if high_signal >= 3 else ("medium" if high_signal >= 1 else "low")

    topic_brief = {
        "schema": "bookmarker.topic-brief.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "status": status,
        "keywords": [{"term": w, "count": c} for w, c in top_keywords],
        "high_signal_count": high_signal,
        "content_urgency": urgency,
        "summary": f"Feed scan: {len(scored)} posts scored, {high_signal} high-signal",
        "topics": [w for w, _ in top_keywords[:5]],
    }
    atomic_write_json(RUNTIME_BOOKMARKER / "topic-brief.json", topic_brief)

    # ── source-health.json ───────────────────────────────────────────────
    api_ok = status != "blocked"
    # A config-fallback warning should make source-health at least ``degraded``
    # even if the feed itself is healthy, so operators notice it in the dashboard.
    if not api_ok:
        sh_status = "blocked"
    elif errors or config_warnings:
        sh_status = "degraded"
    else:
        sh_status = "ok"
    source_health = {
        "schema": "bookmarker.source-health.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "status": sh_status,
        "bird": "ok" if api_ok else "unavailable",
        "browser_relay": None,
        "xurl": None,
        "mismatch": None,
        "sources": [
            {"name": "tagclaw-feed", "status": "ok" if api_ok else "blocked",
             "last_check": ts_now}
        ],
        "source_class": "native-runtime",
        "warnings": list(config_warnings),
    }
    atomic_write_json(RUNTIME_BOOKMARKER / "source-health.json", source_health)

    # ── content-candidates.json ──────────────────────────────────────────
    items = []
    for score_val, post in scored[:20]:  # top 20 candidates
        items.append({
            "post_id": str(post.get("id") or post.get("postId") or ""),
            "score": round(score_val, 2),
            "content_preview": (post.get("content") or "")[:120],
            "author": post.get("author") or post.get("username") or "",
        })
    content_candidates = {
        "schema": "bookmarker.content-candidates.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "status": status,
        "items": items,
        "total_scored": len(scored),
    }
    atomic_write_json(RUNTIME_BOOKMARKER / "content-candidates.json", content_candidates)

    # ── social-drafts.json ───────────────────────────────────────────────
    drafts = []
    for action in actions:
        if action.get("status") == "ok":
            drafts.append({
                "post_id": action.get("post_id", ""),
                "action": action.get("action", "curate"),
                "score": action.get("score", 0),
            })
    social_drafts = {
        "schema": "bookmarker.social-drafts.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "status": status,
        "drafts": drafts,
    }
    atomic_write_json(RUNTIME_BOOKMARKER / "social-drafts.json", social_drafts)

    # ── autonomy-intent.json ─────────────────────────────────────────────
    autonomy_intent = {
        "schema": "bookmarker.autonomy-intent.v1",
        "generated_at": ts_now,
        "updated_at": ts_now,
        "status": status,
        "intent": "curate" if actions else "observe",
        "mode": "native-auto",
        "autonomy_mode": "native-auto",
        "actions_planned": len(actions),
        "actions_executed": len([a for a in actions if a.get("status") == "ok"]),
        "warnings": list(config_warnings),
    }
    atomic_write_json(RUNTIME_BOOKMARKER / "autonomy-intent.json", autonomy_intent)

    # ── tas-social.json ──────────────────────────────────────────────────
    # Bookmarker is the sole owner of TAS_social (2026-03-25). Publish on
    # every cycle — either a lightweight native score or an explicit
    # blocked/partial payload with ``null_reason`` so the aggregator can
    # surface a meaningful status instead of a bare null.
    try:
        identity = load_agency_identity()
        api_key = resolve_api_key()
        tas_social = compute_native_tas_social(
            api_key=api_key,
            identity=identity,
            api_key_present=bool(result.get("_api_key_present")),
        )
    except Exception as exc:
        tas_social = {
            "schema": "bookmarker.tas-social.v1",
            "status": "blocked",
            "generated_at": ts_now,
            "updated_at": ts_now,
            "value": None,
            "align_score": None,
            "community_score": None,
            "null_reason": f"blocked: tas-social computation raised {type(exc).__name__}",
            "display_status": "blocked",
            "source_class": "bookmarker-native",
            "execution_backend": "native-python",
            "errors": [str(exc)[:200]],
        }
    atomic_write_json(RUNTIME_BOOKMARKER / "tas-social.json", tas_social)

    # ── raw ingest snapshot (P3) ─────────────────────────────────────────
    # Truthful, minimal artifact so operators see something under raw/
    # instead of a blank panel on a fresh install.
    try:
        publish_bookmarker_raw(result, tas_social, ts_now)
    except Exception as exc:
        print(f"[bookmarker-runtime] raw snapshot publisher skipped: {exc}")

    print(f"[bookmarker-runtime] Published canonical outputs: topic-brief, source-health, content-candidates, social-drafts, autonomy-intent, tas-social")


def publish_bookmarker_raw(result: dict, tas_social: dict, ts_now: str) -> None:
    """Write a minimal raw snapshot for dashboard raw panel visibility.

    Not a full ingest pipeline — just a truthful, lightweight artifact
    summarising the feed fetch so operators can see what came in.
    """
    RAW_BOOKMARKER.mkdir(parents=True, exist_ok=True)
    feed_sample = result.get("_feed_raw_sample") or []
    snapshot = {
        "schema": "raw.bookmarker.feed-snapshot.v1",
        "generated_at": ts_now,
        "source": "/feed (TagClaw API)",
        "fetch_ok": bool(result.get("_feed_fetch_ok")),
        "feed_size": result.get("feed_size", 0),
        "scored_count": result.get("candidates_scored", 0),
        "tas_social_status": tas_social.get("status"),
        "tas_social_value": tas_social.get("value"),
        "errors": result.get("errors", []),
        "sample_post_ids": [
            str(p.get("id") or p.get("postId") or p.get("tweetId") or "")
            for p in feed_sample if isinstance(p, dict)
        ],
    }
    atomic_write_json(RAW_BOOKMARKER / "latest-feed-snapshot.json", snapshot)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"[bookmarker-runtime] Starting native curation cycle at {now_iso()}")

    try:
        result = run_curation_cycle()
    except Exception as e:
        result = {
            "schema": "bookmarker.result.v1",
            "status": "blocked",
            "started_at": now_iso(),
            "completed_at": now_iso(),
            "errors": [f"Runtime exception: {e}"],
            "execution_backend": "native-python",
            "traceback": traceback.format_exc(),
        }

    # Write result.json
    atomic_write_json(RUNTIME_BOOKMARKER / "result.json", result)
    print(f"[bookmarker-runtime] Wrote result.json (status={result['status']})")

    # Write latest.json
    ts_now = now_iso()
    latest = {
        "schema": "bookmarker.latest.v1",
        "generated_at": ts_now,
        "status": result["status"],
        "source": "run_bookmarker_runtime_v1.py",
        "actions_ok": result.get("actions_ok", 0),
        "feed_size": result.get("feed_size", 0),
        "config_warnings": result.get("config_warnings") or [],
    }
    atomic_write_json(RUNTIME_BOOKMARKER / "latest.json", latest)
    print(f"[bookmarker-runtime] Wrote latest.json")

    # ── Publish canonical runtime outputs for dashboard/input-packet ──────
    publish_bookmarker_canonical(result, ts_now)

    # Update shared runtime-status
    rs_path = RUNTIME_SHARED / "runtime-status.json"
    try:
        rs = json.loads(rs_path.read_text()) if rs_path.exists() else {}
    except Exception:
        rs = {}
    rs.setdefault("schema", "runtime-status.v1")
    rs["bookmarker"] = {"status": result["status"], "updated_at": ts_now}
    rs.pop("bootstrap", None)
    atomic_write_json(rs_path, rs)

    status_code = 0 if result["status"] in ("ok", "partial") else 1
    print(f"[bookmarker-runtime] Cycle complete (exit={status_code})")
    return status_code


if __name__ == "__main__":
    sys.exit(main())

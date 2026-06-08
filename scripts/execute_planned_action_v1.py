#!/usr/bin/env python3
"""execute_planned_action_v1.py — execute the one action picked for this hour.

Plan C executor. Glues together:
  - next-action-intent.json    (which action to do)
  - social-drafts.json         (wiki-grounded post bodies for 'post' action)
  - interaction-candidates.json (targets for 'reply' / 'like' / 'retweet' / 'curate')
  - tagclaw_budget.read_balance / record_consumption (live OP/VP awareness)
  - pick_hourly_action_v1.commit_picked_action (decrement remaining_today)

Sends to TagClaw API directly (bypasses execute_social_intent_v2.py's
heavyweight machinery — the previous executor cold-starts a session
worker per cron tick, which is overkill for a single action). Direct
endpoints used:

  POST /tagclaw/post     body: { content, tick? }
  POST /tagclaw/reply    body: { tweetId, content }
  POST /tagclaw/like     body: { tweetId }
  POST /tagclaw/retweet  body: { tweetId }
  POST /tagclaw/curate   body: { tweetId, weight? }  (VP-priced)

No-quote rule (user policy 2026-05-26): reply bodies are short, wiki-
themed reflections; they never quote the target tweet's text or its ID.

Usage:
  python3 execute_planned_action_v1.py
  python3 execute_planned_action_v1.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(WORKSPACE / "scripts"))
from lib.tagclaw_budget import (  # noqa: E402
    ACTION_COSTS, read_balance, record_consumption,
)
from pick_hourly_action_v1 import commit_picked_action  # noqa: E402
from runtime_utils import append_wiki_event, path_ref  # noqa: E402

INTENT_PATH = WORKSPACE / "runtime" / "bookmarker" / "next-action-intent.json"
# Plan B: two draft sources. wiki-grounded-drafts.json is owned by
# build_wiki_grounded_drafts_v1 (never overwritten by unified-heartbeat);
# social-drafts.json is the legacy pool (bridge / x-cache drafts). The
# executor unions both, preferring wiki-grounded for posts + replies.
WIKI_DRAFTS_PATH = WORKSPACE / "runtime" / "bookmarker" / "wiki-grounded-drafts.json"
DRAFTS_PATH = WORKSPACE / "runtime" / "bookmarker" / "social-drafts.json"
CANDS_PATH = WORKSPACE / "runtime" / "bookmarker" / "interaction-candidates.json"
EXEC_LOG_PATH = WORKSPACE / "runtime" / "bookmarker" / "planned-action-log.jsonl"
CREDS_PATH = WORKSPACE.parent / "workspace-bookmarker" / "runtime" / "credentials" / "tagclaw-bookmarker.json"

TAGCLAW_API_BASE = "https://bsc-api.tagai.fun/tagclaw"
USER_AGENT = "openclaw-bookmarker/1.0 (https://tagclaw.com)"
HTTP_TIMEOUT = 20

# Real TagClaw communities clawdbot can post into (from /ticks/trending +
# the agent's own post history). Wiki concept names are NOT communities —
# posting to them returns "Community not found".
#
# 2026-05-31 owner directive: #BUIDL is the default community.
# Only pure TagClaw protocol content (contract/chain/tokenomics) routes to TagClaw.
KNOWN_COMMUNITIES = {"TagClaw", "BUIDL", "AGENT", "TTAI", "NOUGHT", "CLAW"}
DEFAULT_POST_COMMUNITY = os.environ.get("TAGCLAW_DEFAULT_COMMUNITY") or "BUIDL"

# ── BUIDL-first community resolution ──────────────────────────────────
# Import tick_routing from the bookmarker workspace for content-aware routing.
_BOOKMARKER_SCRIPTS = WORKSPACE.parent / "workspace-bookmarker" / "scripts"
if str(_BOOKMARKER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BOOKMARKER_SCRIPTS))

try:
    from tick_routing import choose_tick as _choose_tick_routed
except ImportError:
    _choose_tick_routed = None


def _resolve_post_community(draft: dict[str, Any]) -> str:
    """Resolve the TagClaw community (tick) for a post draft.

    Priority:
    1. If the draft tick is a known community, use it directly.
    2. If tick_routing is available, use content-aware BUIDL-first routing.
    3. Otherwise, fall back to DEFAULT_POST_COMMUNITY (BUIDL).
    """
    raw_tick = (draft.get("tick") or "").strip()
    if raw_tick in KNOWN_COMMUNITIES:
        return raw_tick

    # Content-aware routing via tick_routing's BUIDL-first logic
    if _choose_tick_routed is not None:
        text = draft.get("text", "")
        keywords = draft.get("keywords") or []
        theme = draft.get("theme") or ""
        try:
            return _choose_tick_routed(
                keywords=list(keywords) if isinstance(keywords, list) else [],
                text=str(text),
                theme=str(theme),
            )
        except Exception:
            pass

    return DEFAULT_POST_COMMUNITY

# Owner-recognition notify (Issue-1 fix 2026-05-28). After clawdbot posts,
# forward the post to 0xNought via the bookmarker Telegram bot with inline
# 认可 / 非常认可 buttons. The button callback_data is the tcfb token that
# telegram-feedback-poller records into twin-recognition.json, which Track A
# of compute_native_tas_social reads for align_score. Without this, owner
# alignment can never be observed → align_score stuck at 0.
NOTIFY_BOT_ACCOUNT = os.environ.get("BOOKMARKER_NOTIFY_ACCOUNT") or "8655852696"
NOTIFY_CHAT_ID = os.environ.get("BOOKMARKER_NOTIFY_CHAT") or "7948500820"
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_PROXY = (
    os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    or "http://127.0.0.1:50328"
)


# ── HTTP plumbing ────────────────────────────────────────────────────

def _api_key() -> str:
    return json.loads(CREDS_PATH.read_text())["api_key"]


def _post_api(path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any] | str]:
    """POST to TagClaw via curl subprocess.

    Urllib triggers WAF 403/1010 / 301-with-error-body on the write endpoints
    (/post, /reply, /like, /retweet, /curate) while curl with the same
    headers/body succeeds. The legacy executor (execute_social_intent_v2.py)
    documented this in 2026-04; we adopt the same workaround verbatim.

    HTTP status is parsed from the `-w '%{http_code}'` curl format string;
    body is whatever came back. JSON parsed when possible.
    """
    body_json = json.dumps(body, ensure_ascii=False)
    cmd = [
        "curl", "-sS",
        "-X", "POST", f"{TAGCLAW_API_BASE}{path}",
        "-H", f"Authorization: Bearer {_api_key()}",
        "-H", "Content-Type: application/json",
        "-H", f"User-Agent: {USER_AGENT}",
        "-H", "Accept: application/json",
        "-d", body_json,
        "-w", "\n__HTTP_STATUS__:%{http_code}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=HTTP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return (0, f"curl timeout after {HTTP_TIMEOUT}s")
    out = proc.stdout or ""
    # Split off the trailing __HTTP_STATUS__:NNN line.
    status_code = 0
    body_text = out
    if "__HTTP_STATUS__:" in out:
        body_text, _, status_tail = out.rpartition("__HTTP_STATUS__:")
        try:
            status_code = int(status_tail.strip().splitlines()[0])
        except Exception:
            status_code = 0
        body_text = body_text.rstrip()
    if proc.returncode != 0 and status_code == 0:
        return (0, (proc.stderr or out or "curl failed").strip()[:500])
    try:
        return (status_code, json.loads(body_text)) if body_text else (status_code, "")
    except Exception:
        return (status_code, body_text)


# ── Owner-recognition Telegram notify ───────────────────────────────

def _resolve_notify_token() -> str:
    try:
        cfg = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return ""
    accts = cfg.get("channels", {}).get("telegram", {}).get("accounts", {})
    a = accts.get(NOTIFY_BOT_ACCOUNT)
    if isinstance(a, dict) and a.get("botToken"):
        return str(a["botToken"])
    return ""


def notify_owner_for_recognition(tweet_id: str, text: str) -> dict[str, Any]:
    """Send the post to 0xNought with 认可 / 非常认可 inline buttons.

    callback_data carries the tcfb token telegram-feedback-poller records.
    Best-effort: failure here never blocks the post (the post already
    landed on TagClaw). Routed through Clash because api.telegram.org is
    GFW-blocked.
    """
    token = _resolve_notify_token()
    if not token:
        return {"ok": False, "error": f"no botToken for account {NOTIFY_BOT_ACCOUNT}"}
    msg = (
        "🐾 clawdbot 新帖 — 请给予认可反馈\n\n"
        f"{text}\n\n"
        f"https://tagai.fun/post/{tweet_id}"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "👍 认可", "callback_data": f"tcfb:{tweet_id}:like"},
        {"text": "❤️ 非常认可", "callback_data": f"tcfb:{tweet_id}:heart"},
    ]]}
    payload = {
        "chat_id": NOTIFY_CHAT_ID,
        "text": msg,
        "reply_markup": json.dumps(keyboard, ensure_ascii=False),
        "disable_web_page_preview": "true",
    }
    cmd = [
        "curl", "-sS", "-X", "POST",
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload, ensure_ascii=False),
        "-w", "\n__HTTP_STATUS__:%{http_code}",
    ]
    if TELEGRAM_PROXY:
        cmd[1:1] = ["--proxy", TELEGRAM_PROXY]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=HTTP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "telegram notify timeout"}
    out = proc.stdout or ""
    status = 0
    if "__HTTP_STATUS__:" in out:
        body, _, tail = out.rpartition("__HTTP_STATUS__:")
        try:
            status = int(tail.strip().splitlines()[0])
        except Exception:
            status = 0
    ok = 200 <= status < 300
    return {"ok": ok, "http_status": status, "tweet_id": tweet_id}


# ── JSON helpers ─────────────────────────────────────────────────────

def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _append_log(record: dict[str, Any]) -> None:
    EXEC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with EXEC_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Action handlers ──────────────────────────────────────────────────

def _pick_post_draft(drafts_obj: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the highest-priority unused wiki-grounded draft."""
    drafts = (drafts_obj or {}).get("drafts") or []
    log = _exec_log_recent_ids(days=14)
    candidates = [
        d for d in drafts
        if isinstance(d, dict)
        and d.get("type") == "post"
        and d.get("source_kind") == "wiki-grounded"
        and d.get("id") not in log
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda d: -int(d.get("priority", 0)))
    return candidates[0]


def _exec_log_recent_ids(days: int = 14) -> set[str]:
    """Return draft_ids / tweet_ids that should not be re-attempted.

    Includes only records where ``status == 'ok'`` (we successfully acted
    on the target — don't repeat) OR the response said "already X-ed"
    (historical action by the legacy executor — server will reject again).
    Hard errors (network blip, wrong body, etc.) are NOT included so the
    next tick can retry the same target with the bug fix in place.
    """
    seen: set[str] = set()
    if not EXEC_LOG_PATH.exists():
        return seen
    cutoff_ts = (datetime.now(timezone.utc).timestamp() - days * 86400)
    try:
        for line in EXEC_LOG_PATH.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            try:
                ts = datetime.fromisoformat(rec.get("ts", "").replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if ts < cutoff_ts:
                continue
            status = rec.get("status")
            is_already_error = False
            if status != "ok":
                resp = rec.get("response")
                err_text = ""
                if isinstance(resp, dict):
                    err_text = str(resp.get("error") or "")
                elif isinstance(resp, str):
                    err_text = resp
                is_already_error = "already" in err_text.lower() and any(
                    kw in err_text.lower() for kw in ("curat", "lik", "retweet")
                )
            if status == "ok" or is_already_error:
                for key in ("draft_id", "target_tweet_id"):
                    v = rec.get(key)
                    if v:
                        seen.add(str(v))
    except Exception:
        pass
    return seen


def _reply_text_for(target: dict[str, Any], drafts_obj: dict[str, Any]) -> str | None:
    """Return a short reply body. NEVER quotes the target text or its id.
    Picks a wiki insight matching the target's tick when possible.
    """
    target_tick = (target.get("tick") or "").strip()
    drafts = (drafts_obj or {}).get("drafts") or []
    same_theme = [
        d for d in drafts
        if isinstance(d, dict)
        and d.get("source_kind") == "wiki-grounded"
        and d.get("theme") == target_tick
    ]
    pool = same_theme or [
        d for d in drafts
        if isinstance(d, dict) and d.get("source_kind") == "wiki-grounded"
    ]
    if not pool:
        return None
    # Prefix with a short reflective phrase so it reads as engagement
    # rather than a standalone post. No quotes, no @-mentions.
    openers = [
        "想到这个角度：",
        "顺着这个聊：",
        "我的视角：",
        "Adding a thought:",
        "Tangent worth flagging:",
    ]
    pick = random.choice(pool)
    body = pick.get("text", "")
    return f"{random.choice(openers)} {body}"[:280]


def execute_action(intent: dict[str, Any], drafts_obj: dict[str, Any],
                    cands_obj: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Returns a result dict to be logged + recorded against budget."""
    action = intent.get("action") or "noop"
    base = {
        "action": action,
        "intent_reason": intent.get("reason", ""),
        "balance_at_pick": intent.get("balance_at_pick"),
        "dry_run": dry_run,
    }
    if action == "noop":
        return {**base, "status": "noop", "detail": intent.get("reason", "noop")}

    # Verify balance one more time live — picker's snapshot may be stale.
    cost = ACTION_COSTS.get(action, {"op": 0, "vp": 0})
    bal = read_balance()
    if bal.get("op", 0) < cost["op"] or bal.get("vp", 0) < cost["vp"]:
        return {
            **base,
            "status": "blocked",
            "detail": f"insufficient balance op={bal.get('op',0):.1f} vp={bal.get('vp',0):.1f}",
        }

    if action == "post":
        draft = _pick_post_draft(drafts_obj)
        if not draft:
            return {**base, "status": "blocked", "detail": "no fresh wiki-grounded draft"}
        text = draft.get("text", "")
        # Resolve community via _resolve_post_community() which implements
        # BUIDL-first routing per 2026-05-31 owner directive.
        # The concept hashtag stays in the body text.
        tick = _resolve_post_community(draft)
        body = {"text": text, "tick": tick}
        if dry_run:
            return {**base, "status": "dry_run", "draft_id": draft["id"], "text_preview": text[:120]}
        code, resp = _post_api("/post", body)
        ok = (200 <= code < 300) and (not isinstance(resp, dict) or resp.get("success") is not False)
        notify = None
        if ok:
            # Extract the assigned tweetId and forward to owner for recognition.
            tweet_id = ""
            if isinstance(resp, dict):
                tweet_id = str((resp.get("post") or {}).get("tweetId") or "")
            if tweet_id:
                notify = notify_owner_for_recognition(tweet_id, text)
        return {
            **base, "status": "ok" if ok else "error",
            "draft_id": draft["id"],
            "http_status": code, "response": resp,
            "text_preview": text[:120],
            "owner_notify": notify,
        }

    if action in ("reply", "like", "retweet", "curate"):
        pool = list((cands_obj or {}).get(action) or [])
        seen = _exec_log_recent_ids(days=14)
        # Walk the full pool on soft-error ("already X-ed") because the
        # legacy executor's history isn't in our exec log yet. The soft
        # errors get persisted into exec log by this run's failures, so
        # subsequent ticks will pre-filter them via _exec_log_recent_ids.
        max_attempts = min(len(pool), 8)
        last_result: dict[str, Any] = {}
        for attempt in range(max_attempts):
            target = next((p for p in pool if p.get("tweet_id") not in seen), None)
            if not target:
                return {**base, "status": "blocked",
                        "detail": f"no fresh candidate for {action} after {attempt} attempts (pool={len(pool)})",
                        "last_attempt": last_result}
            tid = target["tweet_id"]
            if action == "reply":
                text = _reply_text_for(target, drafts_obj)
                if not text:
                    return {**base, "status": "blocked", "detail": "no wiki insight for reply"}
                body = {"tweetId": tid, "text": text}
                path = "/reply"
                extra = {"target_tweet_id": tid, "text_preview": text[:120]}
            elif action == "like":
                body = {"tweetId": tid, "vp": 1}
                path = "/like"
                extra = {"target_tweet_id": tid, "vp": 1}
            elif action == "retweet":
                body = {"tweetId": tid}; path = "/retweet"
                extra = {"target_tweet_id": tid}
            else:  # curate
                body = {"tweetId": tid, "vp": 7}
                path = "/like"
                extra = {"target_tweet_id": tid, "vp": 7}
            if dry_run:
                return {**base, "status": "dry_run", **extra,
                        "would_POST": f"{path} body_keys={list(body.keys())}"}
            code, resp = _post_api(path, body)
            ok = (200 <= code < 300) and (not isinstance(resp, dict) or resp.get("success") is not False)
            if ok:
                return {**base, "status": "ok", **extra,
                        "http_status": code, "response": resp,
                        "attempts": attempt + 1}
            # Soft failure: "already curated/liked/retweeted" → mark + retry.
            err_text = ""
            if isinstance(resp, dict):
                err_text = str(resp.get("error") or "")
            elif isinstance(resp, str):
                err_text = resp
            soft = ("already" in err_text.lower()) and any(
                kw in err_text.lower() for kw in ("curat", "lik", "retweet")
            )
            last_result = {"target_tweet_id": tid, "http_status": code, "response": resp}
            if not soft:
                # Hard error — give up on this action this tick.
                return {**base, "status": "error", **extra,
                        "http_status": code, "response": resp,
                        "attempts": attempt + 1}
            # Persist soft error so future runs skip this tid via _exec_log_recent_ids.
            _append_log({
                **base, "status": "error", **extra,
                "http_status": code, "response": resp,
                "attempt_index": attempt + 1, "skip_reason": "already_acted_on",
            })
            # Mark in-memory so the same loop picks next.
            seen.add(tid)
        return {**base, "status": "blocked",
                "detail": "all top candidates already acted on", "last_attempt": last_result}

    return {**base, "status": "error", "detail": f"unknown action: {action}"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    intent = _read_json(INTENT_PATH)
    if not intent:
        print("[exec-planned] no next-action-intent.json — run pick_hourly_action_v1 first",
              file=sys.stderr)
        return 1
    # Plan B: union the wiki-grounded pool (own file) with the legacy
    # social-drafts pool. wiki-grounded first so _pick_post_draft /
    # _reply_text_for (which filter source_kind == 'wiki-grounded') always
    # see them regardless of what unified-heartbeat did to social-drafts.json.
    wiki_drafts = (_read_json(WIKI_DRAFTS_PATH) or {}).get("drafts") or []
    legacy_obj = _read_json(DRAFTS_PATH) or {}
    legacy_drafts = legacy_obj.get("drafts") or []
    drafts_obj = {
        "drafts": list(wiki_drafts) + list(legacy_drafts),
        "_sources": {
            "wiki_grounded": len(wiki_drafts),
            "legacy": len(legacy_drafts),
        },
    }
    cands_obj = _read_json(CANDS_PATH) or {}

    result = execute_action(intent, drafts_obj, cands_obj, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])

    if args.dry_run:
        return 0

    _append_log(result)
    # Pool-exhausted blocked → consume the quota anyway so the picker
    # doesn't loop on the same dry action every hour. Other "blocked"
    # statuses (insufficient balance, no candidates yet) keep the quota
    # so a future tick can retry once candidates refresh.
    if result.get("status") == "blocked" and (
        "all top candidates" in (result.get("detail") or "")
        or "no fresh candidate for" in (result.get("detail") or "")
    ):
        commit_picked_action(result["action"], note="pool_exhausted_consumed")
    if result.get("status") == "ok":
        cost = ACTION_COSTS.get(result["action"], {"op": 0, "vp": 0})
        note_bits = [
            f"intent_action={result['action']}",
        ]
        if result.get("draft_id"):
            note_bits.append(f"draft_id={result['draft_id']}")
        if result.get("target_tweet_id"):
            note_bits.append(f"tweet_id={result['target_tweet_id']}")
        record_consumption(
            result["action"], op_used=cost["op"], vp_used=cost["vp"],
            note=" ".join(note_bits),
        )
        commit_picked_action(result["action"], note=" ".join(note_bits))
        try:
            append_wiki_event(
                event_type="planned_action_executed",
                producer="execute_planned_action_v1",
                entity=result.get("draft_id") or result.get("target_tweet_id") or "",
                artifact=path_ref(EXEC_LOG_PATH, WORKSPACE),
                status="ok",
                summary=f"{result['action']}",
                detail={
                    "action": result["action"],
                    "draft_id": result.get("draft_id"),
                    "target_tweet_id": result.get("target_tweet_id"),
                    "http_status": result.get("http_status"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[exec-planned] event emit failed: {exc}", file=sys.stderr)
    return 0 if result.get("status") in ("ok", "noop", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())

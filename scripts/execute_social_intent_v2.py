#!/usr/bin/env python3
"""Execute V2 social-intent as the bookmarker social worker.

This script is intentionally conservative:
- requires an active social-intent
- requires lock acquisition
- records all outcomes to runtime/bookmarker/social-execution.json
- does not retry failed writes
- uses direct TagClaw API write endpoints only (`/tagclaw/post`, `/reply`, `/like`)
- does NOT use browser/web posting paths for social writes

It is designed to be called by the bookmarker worker path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from agency_paths import BOOKMARKER_WS, MAIN_WS

# Feed scan constants for fallback curation
FEED_FALLBACK_MAX = 5        # max curations from feed scan
FEED_FALLBACK_VP = 4         # default neutral VP for feed fallback curations
FEED_FALLBACK_VP_MIN = 1
FEED_FALLBACK_VP_MAX = 10
# The TagClaw feed is saturated with highly-curated posts (VP often 50-18000+).
# A closed 20-VP filter blocks all feed fallback curation.  Raise to 500 so we
# can still curate reasonably-active posts without wasting VP on already-maxxed ones.
FEED_FALLBACK_CURATED_VP_MAX = 500

ROOT = (MAIN_WS)
RUNTIME = ROOT / 'runtime'
# Worker autonomy model (2026-03-25): Bookmarker is self-authorizing via autonomy-intent.json.
# Main's social-intent.json is retained as a reference/override but is NOT the execution gate.
AUTONOMY_INTENT = RUNTIME / 'bookmarker' / 'autonomy-intent.json'
SOCIAL_INTENT = RUNTIME / 'main' / 'social-intent.json'   # reference only
SOCIAL_DRAFTS = RUNTIME / 'bookmarker' / 'social-drafts.json'
SOCIAL_EXECUTION = RUNTIME / 'bookmarker' / 'social-execution.json'
SOCIAL_EXECUTION_PLAN = RUNTIME / 'bookmarker' / 'social-execution-plan.json'
SOCIAL_EXECUTION_RESULT = RUNTIME / 'bookmarker' / 'social-execution-result.json'
SOCIAL_HISTORY = RUNTIME / 'shared' / 'social-history.json'
SOCIAL_WRITE_STATE = RUNTIME / 'shared' / 'social-write-state.json'
LOCKS = RUNTIME / 'shared' / 'locks.json'
SOCIAL_INTENT = RUNTIME / 'main' / 'social-intent.json'
STRATEGY_PLAN = RUNTIME / 'main' / 'strategy-plan.json'
CREDENTIALS = (BOOKMARKER_WS / 'runtime' / 'credentials' / 'tagclaw-bookmarker.json')
BASE_URL = 'https://bsc-api.tagai.fun/tagclaw'
BUDGET_ALLOCATION = RUNTIME / 'shared' / 'budget-allocation.json'
LOCK_NAME = 'social_execution_lock'
TRADE_DRAFT_DEDUP = RUNTIME / 'shared' / 'trade-draft-dedup.json'
LOCK_TTL_SECONDS = 1800
# P2B: shared exclusion-set path (same schema as run_bookmarker_runtime.py)
CURATED_EXCLUSION_PATH = RUNTIME / 'bookmarker' / 'curated-exclusion-set.json'

BOOKMARKER_ROOT = (BOOKMARKER_WS)
BOOKMARKER_RESOURCE_STATUS = BOOKMARKER_ROOT / 'memory' / 'tagclaw-resource-status.json'
TAGCLAW_POSTS_RAW = BOOKMARKER_ROOT / 'memory' / 'raw' / 'tagclaw-posts'
ENGAGEMENT_LOG = RUNTIME / 'bookmarker' / 'post-engagement-log.json'
SH_TZ = timezone(timedelta(hours=8))

# OP cost per action type (from HEARTBEAT.md)
OP_COST: dict[str, int] = {
    'post': 200,
    'reply': 50,
    'like': 3,
    'curate': 3,
    'retweet': 4,
}


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec='seconds')


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    t = s.strip()
    if t.endswith('Z'):
        t = t[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(t)
    except Exception:
        return None


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, path)


def _write_curated_exclusion(post_id: str) -> None:
    """P2B: Atomically append post_id to curated-exclusion-set.json.

    Mirrors the same function in run_bookmarker_runtime.py so the intent-path
    and direct-path share a unified curation state across cycles.
    """
    try:
        data = read_json(CURATED_EXCLUSION_PATH)
        if not isinstance(data, dict):
            data = {}
        entries = data.get('entries')
        if not isinstance(entries, dict):
            entries = {}
        entries[str(post_id)] = iso()
        atomic_write_json(CURATED_EXCLUSION_PATH, {
            'schema': 'bookmarker.curated-exclusion-set.v1',
            'updated_at': iso(),
            'entries': entries,
        })
    except Exception:
        pass


def compute_vp_floor_from_resource_status() -> dict[str, float]:
    status = read_json(BOOKMARKER_RESOURCE_STATUS) or {}
    target = float(status.get('daily_vp_min_spend') or 67.0)
    spent = float(status.get('estimated_vp_spent_today') or 0.0)
    remaining = max(0.0, float(status.get('remaining_vp_to_target') or max(0.0, target - spent)))

    now_sh = datetime.now(SH_TZ)
    end_of_day = now_sh.replace(hour=23, minute=59, second=59, microsecond=0)
    remaining_seconds = max(1.0, (end_of_day - now_sh).total_seconds())
    cycles_remaining_today = max(1, math.ceil(remaining_seconds / (30 * 60)))
    baseline_per_cycle = target / 48.0
    catchup_per_cycle = remaining / cycles_remaining_today if remaining > 0 else 0.0
    floor_this_cycle = max(baseline_per_cycle, catchup_per_cycle) if remaining > 0 else 0.0
    return {
        'daily_vp_target': round(target, 4),
        'vp_spent_today': round(spent, 4),
        'vp_remaining_to_target': round(remaining, 4),
        'cycles_remaining_today': float(cycles_remaining_today),
        'vp_floor_this_cycle': round(floor_this_cycle, 4),
    }


def append_social_history(results: list[dict[str, Any]], generated_at: str, actor: str = 'bookmarker') -> None:
    history = read_json(SOCIAL_HISTORY) or {'version': 'v2', 'updated_at': generated_at, 'items': []}
    items = history.get('items') if isinstance(history.get('items'), list) else []
    social_intent = read_json(SOCIAL_INTENT) or {}
    strategy_plan = read_json(STRATEGY_PLAN) or {}
    cycle_id = social_intent.get('cycle_id') or strategy_plan.get('cycle_id')
    strategy_id = social_intent.get('strategy_id') or strategy_plan.get('strategy_id')
    for item in results:
        if not isinstance(item, dict):
            continue
        status = item.get('status')
        if status not in {'ok', 'noop'}:
            continue
        target_key = item.get('target_key')
        if not target_key:
            continue
        request_obj = item.get('request') if isinstance(item.get('request'), dict) else {}
        vp_value = None
        if item.get('type') == 'curate':
            try:
                vp_value = int(request_obj.get('vp')) if request_obj.get('vp') is not None else None
            except Exception:
                vp_value = None
        record: dict[str, Any] = {
            'executed_at': generated_at,
            'actor': actor,
            'cycle_id': cycle_id,
            'strategy_id': strategy_id,
            'type': item.get('type'),
            'result_status': status,
            'post_id': extract_post_id_from_result(item),
            'target_key': target_key,
            'draft_ref': item.get('draft_ref'),
            'request': item.get('request'),
            'vp': vp_value,
            'vp_spent': vp_value,
            'note': item.get('note'),
        }
        # BUG-2 fix: store tick directly so trade-tick dedup can find it in history
        _tk = str(target_key or '')
        if _tk.startswith('tagclaw:post-'):
            record['tick'] = _tk[len('tagclaw:post-'):]
        # Preserve text_body_normalized for dedup (text-similarity / URL extraction)
        text_body = item.get('text_body_normalized') or ''
        if text_body:
            record['text_body_normalized'] = text_body
        # Preserve source_tweet_id from request for cross-rewrite dedup
        request_obj = item.get('request') if isinstance(item.get('request'), dict) else {}
        stid = request_obj.get('source_tweet_id') or ''
        if stid:
            record['_source_tweet_id'] = stid
        # Priority-2: record (draft_source, tick) in sidecar state file for last-resort dedup
        if item.get('type') == 'post' and status == 'ok':
            _hist_draft_source = str(request_obj.get('draft_source') or '').strip()
            _hist_tick = record.get('tick') or ''
            if _hist_draft_source and _hist_tick:
                _trade_draft_dedup_record(_hist_draft_source, _hist_tick)
        items.append(record)
    history['version'] = 'v2'
    history['updated_at'] = generated_at
    history['items'] = items[-200:]
    atomic_write_json(SOCIAL_HISTORY, history)


def update_social_write_state(results: list[dict[str, Any]], generated_at: str) -> None:
    state = read_json(SOCIAL_WRITE_STATE) or {
        'version': 'v1',
        'updated_at': generated_at,
        'breaker': {
            'state': 'closed',
            'consecutive_1010_failures': 0,
            'open_after_consecutive_1010_runs': 3,
            'cooldown_minutes': 240,
            'opened_at': None,
            'until': None,
            'last_failure_at': None,
            'last_failure_reason': None,
        },
    }
    breaker = state.get('breaker') if isinstance(state.get('breaker'), dict) else {}
    threshold = int(breaker.get('open_after_consecutive_1010_runs', 1) or 1)
    cooldown_minutes = int(breaker.get('cooldown_minutes', 240) or 240)

    all_1010 = bool(results) and all(
        isinstance(item, dict)
        and item.get('status') == 'blocked'
        and isinstance(item.get('remote'), dict)
        and item['remote'].get('status') == 403
        and '1010' in str(item['remote'].get('error', ''))
        for item in results
    )
    any_non_1010_progress = any(
        isinstance(item, dict)
        and item.get('status') in {'ok', 'noop'}
        for item in results
    )

    if all_1010:
        consecutive = int(breaker.get('consecutive_1010_failures', 0) or 0) + 1
        breaker['consecutive_1010_failures'] = consecutive
        breaker['last_failure_at'] = generated_at
        breaker['last_failure_reason'] = 'all attempted social writes failed with 403/1010'
        if consecutive >= threshold:
            opened_at = parse_dt(generated_at) or now()
            breaker['state'] = 'open'
            breaker['opened_at'] = generated_at
            breaker['until'] = iso(opened_at + __import__('datetime').timedelta(minutes=cooldown_minutes))
    elif any_non_1010_progress:
        breaker['state'] = 'closed'
        breaker['consecutive_1010_failures'] = 0
        breaker['opened_at'] = None
        breaker['until'] = None
        breaker['last_failure_reason'] = None

    state['version'] = 'v1'
    state['updated_at'] = generated_at
    state['breaker'] = breaker
    atomic_write_json(SOCIAL_WRITE_STATE, state)


def refresh_runtime_status() -> None:
    script = ROOT / 'scripts' / 'build_runtime_status_v2.py'
    subprocess.run(['python3', str(script)], check=False, capture_output=True, text=True)


def _trade_draft_dedup_blocked(source: str, tick: str) -> bool:
    """Return True if (source, tick) was already posted within _TRADE_DRAFT_DEDUP_TTL.

    Last-resort gate independent of draft_type propagation.
    Reads runtime/shared/trade-draft-dedup.json.
    """
    if not source or not tick:
        return False
    try:
        import datetime as _dt
        data = read_json(TRADE_DRAFT_DEDUP) or {}
        entries = data.get('entries') if isinstance(data.get('entries'), list) else []
        cutoff = now() - _dt.timedelta(seconds=_TRADE_DRAFT_DEDUP_TTL)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get('source') != source or str(entry.get('tick') or '').lower() != tick.lower():
                continue
            posted_at = parse_dt(entry.get('posted_at'))
            if posted_at and posted_at >= cutoff:
                print(f'[execute_social_intent_v2] trade-draft-dedup sidecar: source={source} tick={tick} blocked (posted at {entry.get("posted_at")})', file=sys.stderr)
                return True
    except Exception as _e:
        print(f'[execute_social_intent_v2] trade-draft-dedup sidecar read error (non-fatal): {_e}', file=sys.stderr)
    return False


def _trade_draft_dedup_record(source: str, tick: str) -> None:
    """Record a successful (source, tick) post in the sidecar file (best-effort)."""
    if not source or not tick:
        return
    try:
        import datetime as _dt
        data = read_json(TRADE_DRAFT_DEDUP) or {}
        entries = data.get('entries') if isinstance(data.get('entries'), list) else []
        cutoff = now() - _dt.timedelta(seconds=_TRADE_DRAFT_DEDUP_TTL)
        entries = [e for e in entries if isinstance(e, dict) and parse_dt(e.get('posted_at')) and parse_dt(e.get('posted_at')) >= cutoff]
        entries.append({'source': source, 'tick': tick, 'posted_at': iso()})
        atomic_write_json(TRADE_DRAFT_DEDUP, {'version': 'v1', 'updated_at': iso(), 'entries': entries})
    except Exception as _e:
        print(f'[execute_social_intent_v2] trade-draft-dedup sidecar write error (non-fatal): {_e}', file=sys.stderr)


def build_social_execution_plan(run_id: str, autonomy: dict[str, Any], drafts_obj: dict[str, Any] | None, actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    drafts = drafts_obj.get('drafts') if isinstance(drafts_obj, dict) and isinstance(drafts_obj.get('drafts'), list) else []
    recommended_actions = list(autonomy.get('recommended_actions') or []) if isinstance(autonomy, dict) else []
    action_types = [str(a.get('type')) for a in (actions or []) if isinstance(a, dict) and a.get('type')]
    return {
        'version': 'v1',
        'plan_kind': 'social-execution-plan',
        'agent': 'bookmarker',
        'executor': 'bookmarker',
        'execution_owner': 'bookmarker',
        'control_plane': 'main',
        'run_id': run_id,
        'generated_at': iso(),
        'source_class': 'bookmarker-execution-plane',
        'control_ref': 'runtime/main/social-intent.json',
        'guidance_ref': 'runtime/main/bookmarker-guidance.json',
        'autonomy_ref': 'runtime/bookmarker/autonomy-intent.json',
        'drafts_ref': 'runtime/bookmarker/social-drafts.json',
        'status': 'ready' if (actions or recommended_actions) else 'hold',
        'autonomy_mode': autonomy.get('mode', 'conservative') if isinstance(autonomy, dict) else 'conservative',
        'strategy_action': autonomy.get('strategy_action') if isinstance(autonomy, dict) else None,
        'planning_focus': autonomy.get('planning_focus') if isinstance(autonomy, dict) else None,
        'payload': {
            'recommended_actions': recommended_actions,
            'selected_action_types': action_types,
            'target_actions': int((autonomy.get('target_actions') or 0) if isinstance(autonomy, dict) else 0),
            'target_curations': int((autonomy.get('target_curations') or 0) if isinstance(autonomy, dict) else 0),
            'draft_count': len(drafts),
            'action_count': len(actions or []),
        },
        'notes': 'Bookmarker-owned social execution plan derived from autonomy-intent + social drafts.',
    }


def write_social_execution_result(out: dict[str, Any]) -> None:
    atomic_write_json(SOCIAL_EXECUTION, out)
    projected = dict(out)
    projected['result_kind'] = 'social-execution-result'
    projected['executor'] = 'bookmarker'
    projected['execution_owner'] = 'bookmarker'
    projected['control_plane'] = 'main'
    projected['source_class'] = 'bookmarker-execution-plane'
    projected['control_ref'] = 'runtime/main/social-intent.json'
    projected['guidance_ref'] = 'runtime/main/bookmarker-guidance.json'
    projected['legacy_result_ref'] = 'runtime/bookmarker/social-execution.json'
    projected['plan_ref'] = 'runtime/bookmarker/social-execution-plan.json'
    atomic_write_json(SOCIAL_EXECUTION_RESULT, projected)


_TS_SUFFIX_RE = re.compile(r'\s*\[\d{2}:\d{2}(?::\d{2})?\s*UTC\]', re.IGNORECASE)
_TRADE_DRAFT_DEDUP_TTL = 8 * 3600  # 8h — matches trade-tick dedup window


def normalize_draft_text(text: str) -> str:
    """Strip trailing [HH:MM UTC] / [HH:MM:SS UTC] timestamp suffix added by synthesize_trade_drafts.

    These timestamps make every cycle's text unique, defeating content-based dedup.
    Normalizing before hash/text comparisons restores dedup effectiveness.
    """
    return _TS_SUFFIX_RE.sub('', text).strip()


def deref_draft(drafts_obj: dict[str, Any] | None, ref: str | None) -> dict[str, Any] | None:
    if not drafts_obj or not ref:
        return None
    drafts = drafts_obj.get('drafts')
    if not isinstance(drafts, list):
        return None
    wanted = ref.split('#', 1)[-1] if '#' in ref else ref
    for draft in drafts:
        if not isinstance(draft, dict):
            continue
        if draft.get('id') == wanted:
            return draft
    return None


def load_api_key() -> str:
    data = read_json(CREDENTIALS)
    if not data or not data.get('api_key'):
        raise RuntimeError(f'missing api_key in {CREDENTIALS}')
    api_key = str(data['api_key']).strip()
    if not api_key or api_key.upper() in {'DUMMY', 'REPLACE_ME', 'PLACEHOLDER'}:
        raise RuntimeError(f'invalid placeholder api_key in {CREDENTIALS}')
    if not api_key.startswith('tagclaw_'):
        raise RuntimeError(f'invalid api_key format in {CREDENTIALS}')
    return api_key


def tagclaw_post(api_key: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Write to TagClaw via curl subprocess.

    Reason: direct urllib requests have been observed to trigger 403/1010 on
    write endpoints in the worker path, while curl succeeds with the same key.
    """
    body = json.dumps(payload, ensure_ascii=False)
    cmd = [
        'curl', '-sS',
        '-X', 'POST', f'{BASE_URL}/{endpoint}',
        '-H', f'Authorization: Bearer {api_key}',
        '-H', 'Content-Type: application/json',
        '-d', body,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        raw = (proc.stdout or '').strip()
        if proc.returncode != 0:
            return {'ok': False, 'status': None, 'error': (proc.stderr or raw or 'curl failed').strip()}
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {'raw': raw}
        if isinstance(parsed, dict) and parsed.get('success') is True:
            return {'ok': True, 'status': 200, 'response': parsed}
        err_text = raw if raw else (proc.stderr or 'unknown error')
        return {'ok': False, 'status': parsed.get('status') if isinstance(parsed, dict) else None, 'error': err_text, 'response': parsed}
    except Exception as e:
        return {'ok': False, 'status': None, 'error': str(e)}


def acquire_lock(run_id: str) -> tuple[bool, dict[str, Any]]:
    locks = read_json(LOCKS) or {'version': 'v1'}
    current = ((locks.get(LOCK_NAME) or {}) if isinstance(locks, dict) else {})
    state = current.get('state')
    expires_at = parse_dt(current.get('expires_at'))
    if state == 'acquired' and expires_at and expires_at > now():
        return False, locks
    lock = {
        'state': 'acquired',
        'owner': 'bookmarker',
        'run_id': run_id,
        'acquired_at': iso(),
        'expires_at': iso(now().astimezone() + __import__('datetime').timedelta(seconds=LOCK_TTL_SECONDS)),
    }
    locks[LOCK_NAME] = lock
    atomic_write_json(LOCKS, locks)
    return True, locks


def release_lock() -> None:
    locks = read_json(LOCKS) or {'version': 'v1'}
    locks[LOCK_NAME] = {
        'state': 'unlocked',
        'owner': None,
        'run_id': None,
        'acquired_at': None,
        'expires_at': None,
    }
    atomic_write_json(LOCKS, locks)


def execute_action(api_key: str, action: dict[str, Any], drafts_obj: dict[str, Any] | None) -> dict[str, Any]:
    action_type = action.get('type')
    draft_ref = action.get('draft_ref')
    # Support inlined drafts from build_actions_from_autonomy (no draft_ref indirection)
    inline = action.get('_inline_draft')
    draft = inline if inline else deref_draft(drafts_obj, draft_ref)
    payload = draft or {}
    target_key = payload.get('target_key')
    base = {
        'type': action_type,
        'draft_ref': draft_ref,
        'target_key': target_key,
    }

    if action_type == 'hold':
        return {**base, 'status': 'ok', 'note': 'no-op hold action'}

    if action_type == 'post':
        text = payload.get('text')
        tick = payload.get('tick')
        directive_hash = payload.get('directive_hash')
        source_tweet_id = payload.get('source_tweet_id') or ''
        if not text or not tick:
            return {**base, 'status': 'blocked', 'error': 'post draft missing text or tick'}
        # FIX-2: strip [HH:MM UTC] timestamp from final post text before publishing.
        # synthesize_trade_drafts embeds a timestamp to defeat 7d text-content dedup
        # (ensuring each cycle's draft has a unique hash), but the timestamp must not
        # appear in the published post visible to end users.
        clean_text = normalize_draft_text(text)
        resp = tagclaw_post(api_key, 'post', {'text': clean_text, 'tick': tick})
        req = {'tick': tick}
        if directive_hash:
            req['directive_hash'] = directive_hash
        if source_tweet_id:
            req['source_tweet_id'] = source_tweet_id
        # BUG-2 fix: store draft_type and draft_source in request so history-based
        # trade-tick dedup can recognize past trade-draft posts.
        _req_draft_type = str(payload.get('draft_type') or '').strip()
        _req_draft_source = str(payload.get('_draft_source') or '').strip()
        # If _draft_source is not set (e.g. direct post_directive path), fall back to
        # payload['source'] only when it is a real draft origin (not the generic labels).
        if not _req_draft_source:
            _src = str(payload.get('source') or '').strip()
            if _src not in ('social-intent-actions', 'social-intent-post_directive', ''):
                _req_draft_source = _src
        if _req_draft_type:
            req['draft_type'] = _req_draft_type
        if _req_draft_source:
            req['draft_source'] = _req_draft_source
        return {**base, 'status': 'ok' if resp['ok'] else 'blocked', 'request': req, 'remote': resp, 'text_body_normalized': clean_text}

    if action_type == 'reply':
        text = payload.get('text')
        tweet_id = payload.get('tweetId') or payload.get('tweet_id')
        if not text or not tweet_id:
            return {**base, 'status': 'blocked', 'error': 'reply draft missing text or tweetId'}
        resp = tagclaw_post(api_key, 'reply', {'tweetId': tweet_id, 'text': text})
        return {**base, 'status': 'ok' if resp['ok'] else 'blocked', 'request': {'tweetId': tweet_id}, 'remote': resp}

    if action_type == 'like':
        tweet_id = payload.get('tweetId') or payload.get('tweet_id')
        if not tweet_id:
            return {**base, 'status': 'blocked', 'error': 'like draft missing tweetId'}
        req = {'tweetId': tweet_id}
        if payload.get('vp') is not None:
            req['vp'] = payload.get('vp')
        resp = tagclaw_post(api_key, 'like', req)
        return {**base, 'status': 'ok' if resp['ok'] else 'blocked', 'request': req, 'remote': resp}

    if action_type == 'curate':
        tweet_id = payload.get('tweetId') or payload.get('tweet_id')
        vp = payload.get('vp')
        if not tweet_id:
            return {**base, 'status': 'blocked', 'error': 'curate draft missing tweetId'}
        req = {'tweetId': tweet_id}
        if vp is not None:
            req['vp'] = vp
        resp = tagclaw_post(api_key, 'like', req)
        if not resp['ok'] and 'already curated this tweet' in str((resp.get('response') or {}).get('error', '') or resp.get('error', '')).lower():
            return {**base, 'status': 'noop', 'request': req, 'remote': resp, 'note': 'tweet already curated; treated as business no-op'}
        return {**base, 'status': 'ok' if resp['ok'] else 'blocked', 'request': req, 'remote': resp}

    return {**base, 'status': 'blocked', 'error': f'unsupported action type: {action_type}'}


def build_actions_from_autonomy(autonomy: dict, drafts_obj: dict) -> list[dict]:
    """Build executable actions from Bookmarker's autonomy-intent + social-drafts.

    Replaces Main's social-intent.payload.actions as the action source.
    Respects autonomy's recommended_actions, max_per_type, cooldown guardrails.
    """
    mode = autonomy.get('mode', 'conservative')
    recommended = set(autonomy.get('recommended_actions') or [])
    max_per_type = autonomy.get('max_per_type') or {'post': 1, 'reply': 1, 'curate': 1, 'like': 1}
    target_curations = int(autonomy.get('target_curations') or 3)

    if mode == 'conservative' or not recommended:
        return []

    drafts = drafts_obj.get('drafts') or []
    actions: list[dict] = []
    counts: dict[str, int] = {}

    # Map draft types to autonomy recommended_actions
    type_map = {'post': 'post', 'reply': 'reply', 'curate': 'curate', 'like': 'like'}

    for draft in sorted(drafts, key=lambda d: -(d.get('priority') or 0)):
        dtype = draft.get('type')
        mapped = type_map.get(dtype)
        if not mapped or mapped not in recommended:
            continue
        cap = int(max_per_type.get(mapped) or 1)
        # For curations in active mode, allow up to target_curations
        if mapped == 'curate' and mode in ('active',):
            cap = min(target_curations, 10)
        if counts.get(mapped, 0) >= cap:
            continue
        # Wrap as action with inlined payload (no draft_ref indirection)
        actions.append({
            'type': dtype,
            'draft_ref': None,
            '_inline_draft': draft,   # execute_action reads this directly
        })
        counts[mapped] = counts.get(mapped, 0) + 1

    return actions


def is_intent_active(social_intent: dict[str, Any]) -> bool:
    """Return True if social-intent is active and not yet expired."""
    if not isinstance(social_intent, dict):
        return False
    if social_intent.get('status') != 'active':
        return False
    expires_at = parse_dt(social_intent.get('expires_at'))
    return bool(expires_at and expires_at > now())


def build_curate_actions_from_intent(
    curate_targets: list[dict[str, Any]],
    own_post_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert social-intent curate_targets into executable curation actions.

    own_post_ids: optional set of tweet_ids that belong to this agent. Any
    target matching this set is skipped to prevent 'You cannot curate your
    own post' API errors from wasting the action slot (F2 fix, 2026-05-24).
    """
    _own = set(own_post_ids or [])
    actions: list[dict[str, Any]] = []
    for target in curate_targets:
        if not isinstance(target, dict):
            continue
        tweet_id = str(target.get('tweet_id') or '').strip()
        if not tweet_id:
            continue
        # F2: skip own posts — API returns error, wastes action slot, blocks cycle
        if tweet_id in _own:
            print(f'[execute_social_intent_v2] F2: skipping own-post curate target {tweet_id}', file=sys.stderr)
            continue
        vp = target.get('suggested_vp')
        try:
            vp = int(vp) if vp is not None else FEED_FALLBACK_VP
        except (ValueError, TypeError):
            vp = FEED_FALLBACK_VP
        vp = max(1, min(10, vp))
        actions.append({
            'type': 'curate',
            'draft_ref': None,
            '_inline_draft': {
                'type': 'curate',
                'tweetId': tweet_id,
                'vp': vp,
                'target_key': f'tagclaw:{tweet_id}',
                'reason': str(target.get('reason') or 'main intent curate_target'),
                'source': 'social-intent-curate_targets',
            },
        })
    return actions


def filter_recent_curate_targets(
    actions: list[dict[str, Any]],
    lookback_hours: int = 4,
) -> list[dict[str, Any]]:
    """Filter curate actions whose targets were recently seen in social-history.json.

    Cooldown rules:
    - ok or noop within ``lookback_hours`` → skip (general dedup)
    - noop within 2 hours → skip (shorter: noop means already curated by others)

    For feed-fallback curations use lookback_hours=24 to prevent already-curated
    targets from resurfacing across cycles.
    """
    from datetime import timedelta
    history = read_json(SOCIAL_HISTORY) or {}
    items = history.get('items') if isinstance(history.get('items'), list) else []
    if not items:
        return actions

    cutoff_main = now() - timedelta(hours=lookback_hours)
    cutoff_2h = now() - timedelta(hours=2)

    skip_keys: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'curate':
            continue
        target_key = item.get('target_key') or ''
        if not target_key:
            continue
        result_status = item.get('result_status')
        executed_at = parse_dt(item.get('executed_at'))
        if not executed_at:
            continue
        if result_status in ('ok', 'noop') and executed_at > cutoff_main:
            skip_keys.add(target_key)
        if result_status == 'noop' and executed_at > cutoff_2h:
            skip_keys.add(target_key)

    if not skip_keys:
        return actions

    filtered = []
    for action in actions:
        inline = action.get('_inline_draft') or {}
        target_key = inline.get('target_key') or ''
        if target_key in skip_keys:
            continue
        filtered.append(action)
    return filtered


def tagclaw_get(api_key: str, endpoint: str) -> dict[str, Any]:
    """Read-only GET from TagClaw API via curl subprocess.

    Uses curl instead of urllib because the TagClaw API returns 403 for
    urllib requests (including with User-Agent headers) while curl succeeds.
    This was causing feed scanning and participation checks to silently fail,
    resulting in curation_source='none' every cycle.
    """
    cmd = [
        'curl', '-sS',
        '-H', f'Authorization: Bearer {api_key}',
        '-H', 'User-Agent: TagClawX/1.0',
        f'{BASE_URL}/{endpoint}',
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        raw = (proc.stdout or '').strip()
        if proc.returncode != 0:
            return {'ok': False, 'status': proc.returncode, 'error': (proc.stderr or raw or 'curl failed').strip()}
        parsed = json.loads(raw) if raw else {}
        return {'ok': True, 'response': parsed}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def get_post_participation_flags(api_key: str, tweet_id: str) -> dict[str, float | int]:
    """Return current user's participation flags for a post.

    Feed rows do not expose whether *this* agent already curated the target. The single-post
    endpoint does expose participation markers (`liked`, `curated`, etc.), so feed-fallback
    uses it to avoid sending known already-curated targets into the executor.
    """
    if not tweet_id:
        return {}
    resp = tagclaw_get(api_key, f'post/{tweet_id}')
    if not resp.get('ok'):
        return {}
    data = resp.get('response') or {}
    post = data.get('post') if isinstance(data, dict) else {}
    if not isinstance(post, dict):
        return {}
    try:
        curated_value = float(post.get('curated') or 0)
    except Exception:
        curated_value = 0.0
    return {
        'liked': int(post.get('liked') or 0),
        'curated': curated_value,
        'replied': int(post.get('replied') or 0),
        'retweeted': int(post.get('retweeted') or 0),
    }


def _feed_items_from_response(data: Any) -> list[dict[str, Any]]:
    """Return a normalized list of feed rows across legacy and modern TagClaw schemas."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ('feeds', 'tweets', 'items', 'posts', 'data', 'feed', 'results'):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_feed_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize one feed row from nested `feeds[].tweet` or flat `tweets[]` schemas."""
    tweet = item.get('tweet') if isinstance(item.get('tweet'), dict) else item
    author_obj = tweet.get('author') if isinstance(tweet.get('author'), dict) else {}
    author = (
        author_obj.get('userName')
        or author_obj.get('username')
        or tweet.get('twitterUsername')
        or tweet.get('author')
        or tweet.get('authorId')
        or tweet.get('userId')
        or tweet.get('twitterId')
        or ''
    )
    return {
        'tweet_id': str(tweet.get('tweetId') or tweet.get('tweet_id') or tweet.get('id') or '').strip(),
        'author': str(author or '').lower(),
        'vp': tweet.get('vp') or tweet.get('curateVp') or tweet.get('curated') or 0,
        'like_count': tweet.get('likeCount') or tweet.get('likedCount') or tweet.get('likes') or 0,
        'reply_count': tweet.get('replyCount') or tweet.get('commentCount') or tweet.get('comments') or 0,
        'retweet_count': tweet.get('retweetCount') or tweet.get('retweetedCount') or tweet.get('retweets') or 0,
        'created_at': tweet.get('createdAt') or tweet.get('tweetTime') or tweet.get('created_at') or tweet.get('timestamp'),
    }



def recommend_curate_vp(
    *,
    engagement: int,
    current_curated_vp: float,
    created_at_raw: str | None,
    like_count: int = 0,
    reply_count: int = 0,
    retweet_count: int = 0,
) -> int:
    """Return a PoB-aware VP recommendation in the 1..10 range.

    Heuristic goals:
    - higher VP for genuinely high-signal posts
    - reward earlier curation when current curated VP is still low
    - keep some budget for broad discovery; not every curate should be 8-10
    - avoid the old behavior where almost everything collapsed to VP=1
    """
    vp_score = 0.0

    # 1) Engagement / conversation quality (primary signal)
    # Use a soft-log scale so strong posts rise clearly without instantly pinning to 10.
    vp_score += min(4.0, math.log1p(max(0, engagement)) / math.log(20)) * 2.2
    if reply_count >= 8:
        vp_score += 1.2
    elif reply_count >= 3:
        vp_score += 0.6
    if retweet_count >= 5:
        vp_score += 0.8
    if like_count >= 20:
        vp_score += 0.5

    # 2) Early-curate advantage from PoB docs: favor earlier curations more strongly.
    # Lower existing curate VP means the post is still earlier in its curation lifecycle.
    if current_curated_vp <= 0:
        vp_score += 2.0
    elif current_curated_vp <= 2:
        vp_score += 1.5
    elif current_curated_vp <= 5:
        vp_score += 0.8
    elif current_curated_vp >= 15:
        vp_score -= 0.8

    # 3) Freshness: PoB / social-distribution rewards earlier signal.
    created_dt = parse_dt(created_at_raw) if created_at_raw else None
    if created_dt:
        age_hours = max(0.0, (now() - created_dt).total_seconds() / 3600.0)
        if age_hours <= 0.5:
            vp_score += 1.8
        elif age_hours <= 2:
            vp_score += 1.2
        elif age_hours <= 6:
            vp_score += 0.6
        elif age_hours >= 48:
            vp_score -= 1.2
        elif age_hours >= 24:
            vp_score -= 0.6

    # Convert to 1..10. Center around 4 as the default meaningful curate.
    vp = int(round(vp_score))
    vp = max(1, min(10, vp))

    # Keep low-signal candidates above the old pathological floor when they already
    # survived candidate ranking. This preserves exploration while avoiding "always 1".
    if engagement >= 5 and vp < 2:
        vp = 2
    if engagement >= 20 and vp < 3:
        vp = 3
    if engagement >= 50 and vp < 4:
        vp = 4
    return vp


def scan_feed_for_curations(api_key: str) -> list[dict[str, Any]]:
    """Fallback: GET /tagclaw/feed?pages=0,1,2,3 and return top-engagement low-VP posts as curate actions.

    Used when social-intent is expired/revoked or has no curate_targets.
    Returns curation actions with low VP (FEED_FALLBACK_VP_MIN to FEED_FALLBACK_VP_MAX).

    Checks pages 0-3 because the first page is often saturated with already-liked
    posts, while deeper pages contain fresh content from agent peers.
    """
    all_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    # FIX-C-2026-05-25: expanded from 4→8 pages to surface fresher content beyond
    # the first 30 posts. The early-stop heuristic (< 20 items) handles short feeds.
    for page in range(8):  # pages 0-7 (was 0-3)
        resp = tagclaw_get(api_key, f'feed?pages={page}')
        if not resp.get('ok'):
            continue
        data = resp.get('response') or {}
        page_items = _feed_items_from_response(data)
        for item in page_items:
            tid = str((item.get('tweet') if isinstance(item.get('tweet'), dict) else item).get('tweetId') or item.get('tweetId') or item.get('tweet_id') or item.get('id') or '')
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                all_items.append(item)
        if len(page_items) < 20:  # last page, stop paginating
            break

    candidates: list[dict[str, Any]] = []
    for item in all_items:
        normalized = _normalize_feed_item(item)
        tweet_id = normalized['tweet_id']
        if not tweet_id:
            continue
        # Skip own posts (match by agentId prefix or username)
        author = normalized['author']
        if 'clawdbot' in author or 'agent_01kg82hhh5tf1nm3af35fh396q' in author:
            continue
        # Skip already-heavily-curated posts; feed fallback should spend VP where our signal
        # still matters. Keep lightly-curated posts in play so VP can scale 1..10.
        try:
            current_curated_vp = float(normalized['vp'] or 0)
        except Exception:
            current_curated_vp = 0.0
        if current_curated_vp >= FEED_FALLBACK_CURATED_VP_MAX:
            continue
        like_count = int(normalized['like_count'] or 0)
        reply_count = int(normalized['reply_count'] or 0)
        retweet_count = int(normalized['retweet_count'] or 0)
        # Engagement score: likeCount + 3*replyCount + 2*retweetCount
        engagement = like_count + reply_count * 3 + retweet_count * 2
        candidates.append({
            'tweet_id': tweet_id,
            'engagement': engagement,
            'current_curated_vp': current_curated_vp,
            'created_at': normalized['created_at'],
            'like_count': like_count,
            'reply_count': reply_count,
            'retweet_count': retweet_count,
        })

    # Sort by engagement descending, then inspect top candidates until we fill the fallback set.
    # We use the single-post endpoint here because feed rows do not tell us whether *this* agent
    # already curated the target. That prevents known already-curated targets from entering the
    # executor and turning into business no-ops.
    candidates.sort(key=lambda c: -c['engagement'])
    actions: list[dict[str, Any]] = []
    for c in candidates:
        if len(actions) >= FEED_FALLBACK_MAX:
            break
        participation = get_post_participation_flags(api_key, c['tweet_id'])
        # FIX-C-2026-05-25: removed 'liked' from exclusion check — liking a post
        # does not prevent curation; only curated>0 should block. The liked check
        # was eliminating valid curation candidates and exhausting the thin candidate pool.
        if participation and float(participation.get('curated') or 0) > 0:
            continue
        eng = c['engagement']
        vp = recommend_curate_vp(
            engagement=eng,
            current_curated_vp=float(c.get('current_curated_vp') or 0),
            created_at_raw=str(c.get('created_at') or ''),
            like_count=int(c.get('like_count') or 0),
            reply_count=int(c.get('reply_count') or 0),
            retweet_count=int(c.get('retweet_count') or 0),
        )
        actions.append({
            'type': 'curate',
            'draft_ref': None,
            '_inline_draft': {
                'type': 'curate',
                'tweetId': c['tweet_id'],
                'vp': vp,
                'target_key': f'tagclaw:{c["tweet_id"]}',
                'reason': (
                    'feed fallback curation '
                    f'(engagement={eng}, current_curated_vp={float(c.get("current_curated_vp") or 0):.1f}, vp={vp})'
                ),
                'source': 'feed-fallback',
            },
        })
    return actions


REPLY_TEMPLATES = [
    "有意思的视角。{topic_hint}这条路上，agent 的 coordination 层始终是关键——social signal 要能沉淀成可复用的 protocol。",
    "同感。从 TagClaw 的实践来看，{topic_hint}真正的挑战不是单个 agent 更聪明，而是 swarm 之间的信任和激励如何建立。",
    "这个方向值得持续关注。{topic_hint}Agent + Crypto + Social 的融合点，比大多数人预期的要快。",
]

WIKI_INDEX = (MAIN_WS / 'wiki' / 'INDEX.md')


def _get_topic_hint() -> str:
    """Read a topic hint from wiki/index.md, or return empty string."""
    try:
        if WIKI_INDEX.exists():
            text = WIKI_INDEX.read_text(encoding='utf-8')
            # Extract first non-empty line after a heading as a theme hint
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith('#') and len(line) > 10:
                    # Use a brief excerpt as topic hint (max 40 chars, end with space)
                    hint = line[:40].rstrip() + '，'
                    return hint
    except Exception:
        pass
    return ''


def _pick_reply_template(idx: int, topic_hint: str) -> str:
    """Round-robin template selection with topic_hint substituted."""
    template = REPLY_TEMPLATES[idx % len(REPLY_TEMPLATES)]
    return template.format(topic_hint=topic_hint)


def build_engagement_reply_actions(
    api_key: str,
    post_config: dict[str, Any],
    our_post_id: str,
    op_budget_remaining: float,
) -> list[dict[str, Any]]:
    """Scan feed for target_agents' recent posts and build reply actions.

    Only returns actions when:
    - engagement_mode == 'reply_to_top_agents'
    - reply_after_post == True
    - op_budget_remaining > 100 (50 OP/reply × 2 replies + buffer)
    - feed returns recent posts (<6h) from target_agents
    """
    if not isinstance(post_config, dict):
        return []
    if post_config.get('engagement_mode') != 'reply_to_top_agents':
        return []
    if not post_config.get('reply_after_post', True):
        return []
    if op_budget_remaining <= 100:
        return []

    target_agents: list[str] = list(post_config.get('target_agents') or [])
    max_replies: int = int(post_config.get('max_replies_per_cycle') or 2)
    if not target_agents or max_replies <= 0:
        return []

    # Scan feed for target agent posts
    try:
        req = urllib.request.Request(
            f'{BASE_URL}/feed?pages=0',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            feed_data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f'[warn] engagement_reply: feed fetch failed (graceful degrade): {e}', file=__import__('sys').stderr)
        return []

    items = _feed_items_from_response(feed_data)
    now_dt = now()
    from datetime import timedelta
    cutoff = now_dt - timedelta(hours=6)

    # One post per target agent, most recent within 6h
    seen_agents: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for item in items:
        normalized = _normalize_feed_item(item)
        author = normalized['author']
        if not any(ta.lower() == author for ta in target_agents):
            continue
        # Normalise agent name for dedup
        matched_agent = next((ta for ta in target_agents if ta.lower() == author), author)
        if matched_agent in seen_agents:
            continue
        tweet_id = normalized['tweet_id']
        if not tweet_id or tweet_id == our_post_id:
            continue
        # Check freshness
        created_raw = normalized['created_at']
        created_dt = parse_dt(str(created_raw)) if created_raw else None
        if created_dt and created_dt < cutoff:
            continue  # too old
        seen_agents.add(matched_agent)
        candidates.append({'agent': matched_agent, 'tweet_id': tweet_id})
        if len(candidates) >= max_replies:
            break

    topic_hint = _get_topic_hint()
    actions: list[dict[str, Any]] = []
    for idx, cand in enumerate(candidates[:max_replies]):
        reply_text = _pick_reply_template(idx, topic_hint)
        actions.append({
            'type': 'reply',
            'draft_ref': None,
            '_engagement_reply': True,
            '_target_agent': cand['agent'],
            '_target_post_id': cand['tweet_id'],
            '_inline_draft': {
                'type': 'reply',
                'tweet_id': cand['tweet_id'],
                'text': reply_text,
                'target_key': f'tagclaw:engagement-reply-{cand["tweet_id"]}',
                'reason': f'engagement reply to {cand["agent"]} post {cand["tweet_id"]}',
                'source': 'engagement-reply',
            },
        })
    return actions


def write_engagement_log(
    cycle_id: str,
    our_post_id: str,
    engagement_actions: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> None:
    """Append engagement reply record to post-engagement-log.json (atomic, keep 50 entries)."""
    import sys as _sys2
    try:
        existing = read_json(ENGAGEMENT_LOG) or {'updated_at': iso(), 'entries': []}
        entries = existing.get('entries') if isinstance(existing.get('entries'), list) else []

        action_log = []
        for act, res in zip(engagement_actions, results):
            action_log.append({
                'type': 'reply',
                'target_agent': act.get('_target_agent', ''),
                'target_post_id': act.get('_target_post_id', ''),
                'reply_text': (act.get('_inline_draft') or {}).get('text', ''),
                'status': res.get('status', 'unknown'),
            })

        entry = {
            'cycle_id': cycle_id,
            'our_post_id': our_post_id,
            'engagement_actions': action_log,
            'curators_attracted': [],
            'creator_reward_usd': None,
        }
        entries.append(entry)
        # Keep most recent 50
        entries = entries[-50:]

        new_doc = {'updated_at': iso(), 'entries': entries}
        atomic_write_json(ENGAGEMENT_LOG, new_doc)
    except Exception as e:
        print(f'[warn] write_engagement_log failed (graceful degrade): {e}', file=_sys2.stderr)


def build_post_action_from_directive(post_directive: dict[str, Any]) -> dict[str, Any] | None:
    """Convert social-intent post_directive into a post action."""
    if not isinstance(post_directive, dict):
        return None
    text = str(post_directive.get('text') or '').strip()
    tick = str(post_directive.get('tick') or 'BUIDL').strip()
    if not text or not tick:
        return None
    # Normalize before hashing so [HH:MM UTC] suffix doesn't defeat hash-based dedup.
    directive_hash = hashlib.sha1(f'{tick}\n{normalize_draft_text(text)}'.encode('utf-8')).hexdigest()[:12]
    # Extract source_tweet_id for cross-rewrite dedup
    source_tweet_id = str(post_directive.get('source_tweet_id') or '').strip()
    # Fallback 1: extract from target_key (e.g. 'x:2049804551872786459')
    if not source_tweet_id:
        raw_target = str(post_directive.get('target_key') or '').strip()
        if raw_target.startswith('x:'):
            source_tweet_id = raw_target[2:].strip()
    # Fallback 2: resolve draft_ref to get source_tweet_id from draft
    if not source_tweet_id:
        draft_ref = str(post_directive.get('draft_ref') or '').strip()
        if draft_ref:
            try:
                parts = draft_ref.split('#', 1)
                file_path = ROOT / parts[0] if parts else None
                draft_key = parts[1] if len(parts) > 1 else None
                if file_path and file_path.exists() and draft_key:
                    drafts_data = read_json(file_path) or {}
                    draft_list = drafts_data.get('drafts') if isinstance(drafts_data, dict) else []
                    for d in draft_list:
                        if isinstance(d, dict) and d.get('id') == draft_key:
                            stid = str(d.get('source_tweet_id') or '').strip()
                            if stid:
                                source_tweet_id = stid
                                break
            except Exception:
                pass
    # Fallback 3: extract x.com URL from post text (e.g. "... → https://x.com/user/status/ID")
    if not source_tweet_id:
        import re as _re
        m = _re.search(r'x\.com/\w+/status/(\d+)', text)
        if m:
            source_tweet_id = m.group(1)
    # FIX-3: normalize text in inline draft so stored text_body_normalized in history
    # is already clean (no [HH:MM UTC] suffix), making text-similarity dedup more reliable.
    inline = {
        'type': 'post',
        'text': normalize_draft_text(text),
        'tick': tick,
        'target_key': str(post_directive.get('target_key') or f'tagclaw:post-{tick}'),
        'directive_hash': directive_hash,
        'reason': str(post_directive.get('reason') or 'main intent post_directive'),
        'source': str(post_directive.get('source') or 'social-intent-post_directive'),
    }
    if post_directive.get('draft_type'):
        inline['draft_type'] = str(post_directive.get('draft_type'))
    if source_tweet_id:
        inline['source_tweet_id'] = source_tweet_id
    return {
        'type': 'post',
        'draft_ref': None,
        '_inline_draft': inline,
        'request': {'tick': tick, 'directive_hash': directive_hash},
    }


def _enrich_request_with_source(request: dict[str, Any], inline_draft: dict[str, Any] | None) -> dict[str, Any]:
    """Add source_tweet_id to request for history dedup."""
    if inline_draft:
        stid = inline_draft.get('source_tweet_id')
        if stid:
            request['source_tweet_id'] = stid
    return request


def last_successful_post_age_hours(history_obj: dict[str, Any] | None) -> float | None:
    """Return hours since the most recent successful post in social history, or None if none found."""
    if not isinstance(history_obj, dict):
        return None
    for item in reversed(history_obj.get('items') or []):
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'post' and item.get('result_status') == 'ok':
            executed_at = parse_dt(item.get('executed_at'))
            if executed_at:
                return (now() - executed_at).total_seconds() / 3600.0
    return None


def post_directive_already_executed(
    post_directive: dict[str, Any] | None,
    social_intent: dict[str, Any] | None,
    history_obj: dict[str, Any] | None,
) -> bool:
    """Return True when the current post_directive already produced a successful post.

    Scope is intentionally narrow: dedupe only the *same active intent*, not all posts on the
    same tick. This prevents duplicate reruns of one social-intent cycle while still allowing
    later cycles to post again on the same tick/community.
    """
    if not isinstance(post_directive, dict) or not isinstance(history_obj, dict):
        return False

    text = str(post_directive.get('text') or '').strip()
    tick = str(post_directive.get('tick') or 'BUIDL').strip()
    if not text or not tick:
        return False

    # Normalize before dedup: strip [HH:MM UTC] suffix added by synthesize_trade_drafts.
    # Without this, every cycle's hash differs → all four dedup layers are bypassed.
    text_normalized = normalize_draft_text(text)

    cycle_id = str((social_intent or {}).get('cycle_id') or '').strip()
    strategy_id = str((social_intent or {}).get('strategy_id') or '').strip()
    target_key = f'tagclaw:post-{tick}'
    directive_hash = hashlib.sha1(f'{tick}\n{text_normalized}'.encode('utf-8')).hexdigest()[:12]

    # Trade-tick tick-level dedup: prevent same tick from posting >1 trade-tick
    # per lookback window, regardless of text variation from timestamps.
    _tick = str(post_directive.get('tick') or '').strip()
    _draft_type = str(post_directive.get('draft_type') or '').strip()
    _draft_source = str(post_directive.get('source') or '').strip()
    _is_trade_draft = (
        _draft_source == 'synthesize_trade_drafts'
        or _draft_type in ('market_commentary', 'community_heat_observation')
    )
    TRADE_TICK_DEDUP_TTL = 8 * 3600  # 8 hours between same-tick trade-tick posts
    if _is_trade_draft and _tick:
        for item in reversed(history_obj.get('items') or []):
            if not isinstance(item, dict):
                continue
            if item.get('type') != 'post' or item.get('result_status') != 'ok':
                continue
            _hist_tick = str(item.get('tick') or '').strip()
            _hist_draft_type = str((item.get('request') or {}).get('draft_type') or '').strip()
            _hist_draft_source = str((item.get('request') or {}).get('draft_source') or '').strip()
            _hist_is_trade = (
                _hist_draft_source == 'synthesize_trade_drafts'
                or _hist_draft_type in ('market_commentary', 'community_heat_observation')
            )
            if _hist_is_trade and _hist_tick.lower() == _tick.lower():
                executed_at = parse_dt(item.get('executed_at'))
                if executed_at and (now() - executed_at).total_seconds() <= TRADE_TICK_DEDUP_TTL:
                    print(f'[execute_social_intent_v2] trade-tick dedup: tick={_tick} already posted at {item.get("executed_at")}', file=sys.stderr)
                    return True
        # Priority-2 last-resort: check sidecar state file (catches cases where history
        # lacks draft_type/draft_source due to BUG-2 in older history entries).
        if _trade_draft_dedup_blocked(_draft_source, _tick):
            return True

    # --- source-tweet dedup (24h) ---
    # Detect same-source-tweet rewrites: if any post in the last 24h referenced
    # the same X tweet as the current draft, block republishing.  This catches
    # content-hash misses (different phrasing of the same source material).
    source_tweet_id = str(post_directive.get('source_tweet_id') or '').strip()
    if not source_tweet_id:
        # Fallback: extract from target_key like 'x:2049804551872786459'
        raw_target = str(post_directive.get('target_key') or '').strip()
        if raw_target.startswith('x:'):
            source_tweet_id = raw_target[2:].strip()
    if not source_tweet_id:
        # Fallback: load from draft_ref (e.g. social-drafts.json#draft-post-1)
        draft_ref = str(post_directive.get('draft_ref') or '').strip()
        if draft_ref:
            try:
                parts = draft_ref.split('#', 1)
                file_path = ROOT / parts[0] if parts else None
                draft_key = parts[1] if len(parts) > 1 else None
                if file_path and file_path.exists() and draft_key:
                    drafts_data = read_json(file_path) or {}
                    draft_list = drafts_data.get('drafts') if isinstance(drafts_data, dict) else []
                    for d in draft_list:
                        if isinstance(d, dict) and d.get('id') == draft_key:
                            stid = str(d.get('source_tweet_id') or '').strip()
                            if stid:
                                source_tweet_id = stid
                                break
            except Exception:
                pass
    SOURCE_DEDUP_TTL = 24 * 3600
    if source_tweet_id:
        # Check sidecar tracking file (fast, always populated)
        published_sources = read_json(RUNTIME / 'bookmarker' / 'published-source-tweets.json') or {}
        prev = published_sources.get(source_tweet_id)
        if prev and isinstance(prev, dict):
            prev_at = parse_dt(prev.get('published_at'))
            if prev_at and (now() - prev_at).total_seconds() <= SOURCE_DEDUP_TTL:
                print(f'[execute_social_intent_v2] source-tweet dedup: {source_tweet_id} already posted as {prev.get("post_id")} at {prev.get("published_at")}', file=sys.stderr)
                return True
        # Also scan history for backwards compatibility
        for item in reversed(history_obj.get('items') or []):
            if not isinstance(item, dict):
                continue
            if item.get('type') != 'post' or item.get('result_status') != 'ok':
                continue
            # Check request.source_tweet_id
            hist_source = str((item.get('request') or {}).get('source_tweet_id') or '').strip()
            # Also check _source_tweet_id (explicit field added by append_social_history)
            if not hist_source:
                hist_source = str(item.get('_source_tweet_id') or '').strip()
            if not hist_source:
                # Fallback: scan text_body_normalized for x.com source URL
                text_body = str(item.get('text_body_normalized') or '')
                import re as _re
                m = _re.search(r'x\.com/\w+/status/(\d+)', text_body)
                if m:
                    hist_source = m.group(1)
            if hist_source and hist_source == source_tweet_id:
                executed_at = parse_dt(item.get('executed_at'))
                if executed_at and (now() - executed_at).total_seconds() <= SOURCE_DEDUP_TTL:
                    print(f'[execute_social_intent_v2] source-tweet dedup: {source_tweet_id} already posted at {item.get("executed_at")}', file=sys.stderr)
                    return True

    # --- hash-based dedup (4h) ---
    # Matches from older, expired cycles must not block re-queued content in new cycles.
    HASH_DEDUP_TTL_SECONDS = 4 * 3600

    for item in reversed(history_obj.get('items') or []):
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'post' or item.get('result_status') != 'ok':
            continue
        if item.get('target_key') != target_key:
            continue

        same_cycle = cycle_id and str(item.get('cycle_id') or '').strip() == cycle_id
        same_strategy = strategy_id and str(item.get('strategy_id') or '').strip() == strategy_id
        note_text = str(item.get('note') or '')
        request_obj = item.get('request') if isinstance(item.get('request'), dict) else {}
        same_hash = (
            directive_hash and (
                request_obj.get('directive_hash') == directive_hash
                or f'directive_hash={directive_hash}' in note_text
            )
        )
        # Hash-only dedup is time-bounded: only block if the matching post is within the
        # intent TTL window. A hash match from an older cycle means content was legitimately
        # re-queued for a new intent — that must not be blocked.
        if same_hash and not same_cycle and not same_strategy:
            executed_at = parse_dt(item.get('executed_at'))
            if not executed_at or (now() - executed_at).total_seconds() > HASH_DEDUP_TTL_SECONDS:
                same_hash = False
        if same_cycle or same_strategy or same_hash:
            return True

    # --- text-content dedup (7d defense-in-depth) ---
    # Catch identical draft text reused across different cycles/strategies,
    # even when source_tweet_id is missing or hash dedup window has expired.
    # Use normalize_draft_text to strip [HH:MM UTC] suffixes before comparing.
    TEXT_DEDUP_TTL_SECONDS = 7 * 24 * 3600
    normalized_text = ' '.join(normalize_draft_text(text).split()).strip().lower()
    if len(normalized_text) > 40:
        for item in reversed(history_obj.get('items') or []):
            if not isinstance(item, dict):
                continue
            if item.get('type') != 'post' or item.get('result_status') != 'ok':
                continue
            hist_text = str(item.get('text_body_normalized') or '')
            if not hist_text:
                continue
            hist_normalized = ' '.join(normalize_draft_text(hist_text).split()).strip().lower()
            if hist_normalized == normalized_text:
                executed_at = parse_dt(item.get('executed_at'))
                if executed_at and (now() - executed_at).total_seconds() <= TEXT_DEDUP_TTL_SECONDS:
                    print(f'[execute_social_intent_v2] text-content dedup: identical text posted at {item.get("executed_at")}', file=sys.stderr)
                    return True

    return False



def social_intent_already_has_successful_post(
    social_intent: dict[str, Any] | None,
    history_obj: dict[str, Any] | None,
) -> bool:
    """Return True if this social-intent cycle/strategy already emitted any successful post.

    This is intentionally broader than ``post_directive_already_executed``: once one post has
    already succeeded for the active social-intent cycle, reruns should not emit a second
    autonomy/draft post on another tick/community.
    """
    if not isinstance(social_intent, dict) or not isinstance(history_obj, dict):
        return False

    cycle_id = str((social_intent or {}).get('cycle_id') or '').strip()
    strategy_id = str((social_intent or {}).get('strategy_id') or '').strip()
    if not cycle_id and not strategy_id:
        return False

    for item in reversed(history_obj.get('items') or []):
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'post' or item.get('result_status') != 'ok':
            continue
        same_cycle = cycle_id and str(item.get('cycle_id') or '').strip() == cycle_id
        same_strategy = strategy_id and str(item.get('strategy_id') or '').strip() == strategy_id
        if same_cycle or same_strategy:
            return True
    return False



def extract_post_id_from_result(result: dict[str, Any] | None) -> str | None:
    """Extract TagClaw post/tweet id from heterogeneous API response shapes."""
    if not isinstance(result, dict):
        return None

    remote = result.get('remote') if isinstance(result.get('remote'), dict) else {}
    resp_data = remote.get('response') if isinstance(remote.get('response'), dict) else {}
    post_obj = resp_data.get('post') if isinstance(resp_data.get('post'), dict) else {}
    data_obj = resp_data.get('data') if isinstance(resp_data.get('data'), dict) else {}
    data_post_obj = data_obj.get('post') if isinstance(data_obj.get('post'), dict) else {}

    for candidate in [
        result.get('tweet_id'),
        result.get('tweetId'),
        result.get('post_id'),
        result.get('postId'),
        result.get('id'),
        resp_data.get('id'),
        resp_data.get('tweetId'),
        resp_data.get('postId'),
        post_obj.get('id'),
        post_obj.get('tweetId'),
        post_obj.get('postId'),
        data_obj.get('id'),
        data_obj.get('tweetId'),
        data_obj.get('postId'),
        data_post_obj.get('id'),
        data_post_obj.get('tweetId'),
        data_post_obj.get('postId'),
    ]:
        if candidate:
            return str(candidate)
    return None


def build_reply_action_from_directive(reply_directive: dict[str, Any]) -> dict[str, Any] | None:
    """Convert social-intent reply_directive into a reply action."""
    if not isinstance(reply_directive, dict):
        return None
    tweet_id = str(reply_directive.get('tweet_id') or '').strip()
    text = str(reply_directive.get('text') or '').strip()
    if not tweet_id or not text:
        return None
    return {
        'type': 'reply',
        'draft_ref': None,
        '_inline_draft': {
            'type': 'reply',
            'tweetId': tweet_id,
            'text': text,
            'target_key': f'tagclaw:{tweet_id}',
            'source': 'social-intent-reply_directive',
        },
    }


def build_post_action_from_actions_item(item: dict[str, Any], drafts_obj: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a post action from a payload.actions item (type=post, draft_ref=...)."""
    if not isinstance(item, dict):
        return None
    draft_ref = item.get('draft_ref') or ''
    draft = deref_draft(drafts_obj, draft_ref) if draft_ref else None
    text = str((draft or {}).get('text') or item.get('text') or '').strip()
    tick = str((draft or {}).get('tick') or item.get('tick') or 'BUIDL').strip()
    if not text or not tick:
        return None
    # Normalize before hashing so timestamp suffix doesn't defeat hash-based dedup.
    directive_hash = hashlib.sha1(f'{tick}\n{normalize_draft_text(text)}'.encode('utf-8')).hexdigest()[:12]
    # Propagate source_tweet_id from draft for cross-rewrite dedup (24h source-tweet gate)
    source_tweet_id = str((draft or {}).get('source_tweet_id') or item.get('source_tweet_id') or '').strip()
    source_url = str((draft or {}).get('source_url') or item.get('source_url') or '').strip()
    # FIX-3: normalize text in inline draft so stored text_body_normalized in history
    # is already clean (no [HH:MM UTC] suffix), making text-similarity dedup more reliable.
    inline: dict[str, Any] = {
        'type': 'post',
        'text': normalize_draft_text(text),
        'tick': tick,
        'target_key': f'tagclaw:post-{tick}',
        'directive_hash': directive_hash,
        'reason': str(item.get('reason') or 'social-intent actions[].post'),
        'source': 'social-intent-actions',
    }
    if source_tweet_id:
        inline['source_tweet_id'] = source_tweet_id
    if source_url:
        inline['source_url'] = source_url
    # Propagate draft_type and source from draft for dedup (trade-tick tick-level gate, etc.)
    # Fall back to item fields (populated by analyze_social_action_selection) when deref_draft
    # fails — e.g. when social-drafts.json was regenerated with new cycle IDs between main
    # writing the intent and bookmarker executing it.
    _draft_source_raw = str((draft or {}).get('source') or item.get('source') or '')
    _draft_type_raw = str((draft or {}).get('draft_type') or item.get('draft_type') or '')
    if _draft_type_raw:
        inline['draft_type'] = _draft_type_raw
    if _draft_source_raw:
        inline['_draft_source'] = _draft_source_raw
    return {
        'type': 'post',
        'draft_ref': draft_ref or None,
        '_inline_draft': inline,
    }


def build_reply_action_from_actions_item(item: dict[str, Any], drafts_obj: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a reply action from a payload.actions item (type=reply, draft_ref=...)."""
    if not isinstance(item, dict):
        return None
    draft_ref = item.get('draft_ref') or ''
    draft = deref_draft(drafts_obj, draft_ref) if draft_ref else None
    tweet_id = str(item.get('tweet_id') or (draft or {}).get('tweetId') or '').strip()
    text = str((draft or {}).get('text') or item.get('text') or '').strip()
    if not tweet_id or not text:
        return None
    # Propagate source_tweet_id from draft for cross-rewrite dedup
    source_tweet_id = str((draft or {}).get('source_tweet_id') or item.get('source_tweet_id') or '').strip()
    inline: dict[str, Any] = {
        'type': 'reply',
        'tweetId': tweet_id,
        'text': text,
        'target_key': f'tagclaw:{tweet_id}',
        'source': 'social-intent-actions',
    }
    if source_tweet_id:
        inline['source_tweet_id'] = source_tweet_id
    return {
        'type': 'reply',
        'draft_ref': draft_ref or None,
        '_inline_draft': inline,
    }


def archive_post_result(action: dict[str, Any], result: dict[str, Any], run_id: str) -> None:
    """Archive a successful post/reply to workspace-bookmarker/memory/raw/tagclaw-posts/.

    Fails gracefully — never interrupts the social execution main flow.
    Path: YYYY-MM-DD-{post_id}.md with YAML frontmatter + post body.
    """
    import sys as _sys
    try:
        action_type = result.get('type')
        if action_type not in ('post', 'reply'):
            return
        if result.get('status') != 'ok':
            return

        inline = action.get('_inline_draft') or {}
        text = inline.get('text') or ''
        tick = inline.get('tick') or ''
        theme = inline.get('theme') or ''
        writing_mode = inline.get('_writing_mode') or inline.get('writing_mode') or ''
        wiki_source = inline.get('wiki_source')

        post_id_raw = extract_post_id_from_result(result)
        if not post_id_raw:
            ts_suffix = now().strftime('%H%M%S')
            post_id_raw = f'{run_id[-8:]}-{ts_suffix}'

        post_id = f'tagclaw-{post_id_raw}'
        date_str = now().strftime('%Y-%m-%d')
        created_at = iso()

        safe_id = str(post_id_raw).replace('/', '-').replace(':', '-').replace(' ', '-')
        filename = f'{date_str}-{safe_id}.md'

        wiki_source_yaml = json.dumps(wiki_source) if wiki_source is not None else 'null'
        content = (
            f'---\n'
            f'post_id: "{post_id}"\n'
            f'type: "{action_type}"\n'
            f'tick: "{tick}"\n'
            f'created_at: "{created_at}"\n'
            f'wiki_source: {wiki_source_yaml}\n'
            f'theme: "{theme}"\n'
            f'writing_mode: "{writing_mode}"\n'
            f'owner_reaction: null\n'
            f'liked_by_owner: false\n'
            f'commented_by_owner: false\n'
            f'retweeted_by_owner: false\n'
            f'---\n\n'
            f'{text}\n'
        )

        dest = TAGCLAW_POSTS_RAW / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', dir=str(dest.parent), suffix='.tmp',
                                         delete=False, encoding='utf-8') as f:
            f.write(content)
            tmp_name = f.name
        os.replace(tmp_name, str(dest))

    except Exception as e:
        print(f'[warn] archive_post_result failed (graceful degrade): {e}', file=_sys.stderr)


def _record_published_source(action: dict[str, Any], result: dict[str, Any]) -> None:
    """Record source_tweet_id → post_id mapping for cross-rewrite dedup."""
    import sys as _sys2
    try:
        inline = action.get('_inline_draft') or {}
        source_tweet_id = str(inline.get('source_tweet_id') or '').strip()
        if not source_tweet_id:
            target_key = str(inline.get('target_key') or '').strip()
            if target_key.startswith('x:'):
                source_tweet_id = target_key[2:].strip()
        # Fallback: extract from result.request.source_tweet_id (set by execute_action)
        if not source_tweet_id:
            req = result.get('request') if isinstance(result.get('request'), dict) else {}
            source_tweet_id = str(req.get('source_tweet_id') or '').strip()
        # Fallback: extract x.com URL from result.text_body_normalized
        if not source_tweet_id:
            text_body = str(result.get('text_body_normalized') or '')
            if text_body:
                import re as _re
                m = _re.search(r'x\.com/\w+/status/(\d+)', text_body)
                if m:
                    source_tweet_id = m.group(1)
        if not source_tweet_id:
            return
        post_id = extract_post_id_from_result(result)
        tracking_path = RUNTIME / 'bookmarker' / 'published-source-tweets.json'
        data = read_json(tracking_path) or {}
        data[source_tweet_id] = {
            'post_id': post_id,
            'published_at': iso(),
            'tick': inline.get('tick', ''),
        }
        # Prune entries older than 7 days
        cutoff = now() - timedelta(days=7)
        data = {k: v for k, v in data.items() if isinstance(v, dict) and (not v.get('published_at') or parse_dt(v.get('published_at')) is None or (parse_dt(v.get('published_at')) or now()) > cutoff)}
        atomic_write_json(tracking_path, data)
        print(f'[record_published_source] recorded source_tweet_id={source_tweet_id} → post_id={post_id}', file=_sys2.stderr)
    except Exception as e:
        print(f'[warn] record_published_source failed (graceful degrade): {e}', file=_sys2.stderr)


def _trigger_telegram_sync(action: dict[str, Any], result: dict[str, Any]) -> None:
    """Send a Telegram notification for a successful post/reply (graceful degrade).

    Passes post data via HOOK_PAYLOAD env var to sync_tagclaw_telegram_v1.py.
    Attempts exactly once. On failure, logs a BLOCKER message and suggests
    running tagclaw_post_telegram_sync_v1.py for backfill. Never blocks posting.
    """
    import sys as _sys2
    try:
        action_type = result.get('type')
        if action_type not in ('post', 'reply'):
            return
        if result.get('status') != 'ok':
            return

        inline = action.get('_inline_draft') or {}
        text = inline.get('text') or ''
        tick = inline.get('tick') or ''

        post_id_raw = extract_post_id_from_result(result)
        if not post_id_raw:
            return

        payload = json.dumps({
            'post_id': str(post_id_raw),
            'text': text,
            'tick': tick,
            'type': action_type,
        }, ensure_ascii=False)

        env = {**os.environ, 'HOOK_PAYLOAD': payload}
        proc = subprocess.run(
            ['python3', str(Path(__file__).parent / 'sync_tagclaw_telegram_v1.py')],
            env=env, timeout=60, capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return
        stderr = (proc.stderr or '').strip().replace('\n', ' ')[:400]
        stdout = (proc.stdout or '').strip().replace('\n', ' ')[:400]
        last_detail = stderr or stdout or f'returncode={proc.returncode}'
        print(
            f'[BLOCKER] OWNER NOTIFICATION NOT DELIVERED — telegram sync failed on the immediate attempt '
            f'for post {post_id_raw} ({action_type}). Detail: {last_detail}. '
            f'Post was published successfully but owner (7948500820) was NOT notified. '
            f'Run backfill: python3 scripts/tagclaw_post_telegram_sync_v1.py',
            file=_sys2.stderr,
        )
    except Exception as e:
        print(f'[warn] telegram sync failed (graceful degrade): {e}', file=_sys2.stderr)


def main() -> int:
    run_id = f'bookmarker-social-exec-{now().strftime("%Y%m%dT%H%M%S")}'

    # Worker autonomy model: autonomy-intent governs post/reply/like execution.
    # Curations are always attempted (from social-intent or feed fallback) regardless of
    # autonomy_mode — this ensures strategy-ledger shows executions every 2h cycle.
    autonomy = read_json(AUTONOMY_INTENT) or {}
    social_intent = read_json(SOCIAL_INTENT) or {}
    drafts_obj = read_json(SOCIAL_DRAFTS) or {}
    social_hist = read_json(SOCIAL_HISTORY) or {}

    # Compute daily resource floor status inline (mirrors runtime_utils.compute_daily_consumption).
    # Used to lower the post-dedupe bypass threshold when the P0 daily floor is unmet.
    _DAILY_OP_MIN = 670.0
    _DAILY_VP_MIN = 67.0
    _today_str = datetime.now(timezone.utc).astimezone(SH_TZ).strftime('%Y-%m-%d')
    _daily_op = 0.0
    _daily_vp = 0.0
    for _item in (social_hist.get('items') or []):
        if not isinstance(_item, dict):
            continue
        _ts = _item.get('executed_at') or ''
        if not _ts.startswith(_today_str):
            _dt = parse_dt(_ts)
            if not _dt or _dt.astimezone(SH_TZ).strftime('%Y-%m-%d') != _today_str:
                continue
        _atype = _item.get('type') or ''
        _op_cost = {'post': 200.0, 'reply': 50.0, 'curate': 3.0, 'like': 3.0, 'retweet': 4.0}
        _daily_op += _op_cost.get(_atype, 0.0)
        if _atype in ('curate', 'like'):
            _daily_vp += float(_item.get('vp') or _item.get('vp_spent') or 0)
    _resource_floor_unmet = (_daily_op < _DAILY_OP_MIN) or (_daily_vp < _DAILY_VP_MIN)

    if not autonomy:
        atomic_write_json(SOCIAL_EXECUTION_PLAN, build_social_execution_plan(run_id, {}, drafts_obj, []))
        out = {
            'version': 'v2', 'agent': 'bookmarker', 'run_id': run_id, 'status': 'blocked',
            'generated_at': iso(), 'autonomy_ref': 'runtime/bookmarker/autonomy-intent.json',
            'lock_name': LOCK_NAME, 'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
            'notes': 'missing autonomy-intent.json'
        }
        write_social_execution_result(out)
        refresh_runtime_status()
        print(json.dumps({'status': out['status'], 'reason': out['notes']}, ensure_ascii=False))
        return 1

    autonomy_mode = autonomy.get('mode', 'conservative')

    # --- Load API key early (needed for feed fallback curation scan) ---
    api_key = load_api_key()

    # === CURATION PATH (always runs every cycle, independent of autonomy_mode) ===
    # Source priority: (1) social-intent.curate_targets if active+not-expired,
    #                  (2) feed scan fallback otherwise.
    intent_active = is_intent_active(social_intent)
    intent_curate_targets: list[dict[str, Any]] = []
    if intent_active:
        intent_curate_targets = list(((social_intent.get('payload') or {}).get('curate_targets') or []))

    # F2: build set of own post IDs from content-candidates so build_curate_actions_from_intent
    # can skip them. Social-intent's curate_targets may include own posts (e.g. k8zvDIu4xn from
    # clawdbot) which cause 'You cannot curate your own post' API errors that waste action slots.
    _candidates_obj = read_json(RUNTIME / 'bookmarker' / 'content-candidates.json') or {}
    _own_post_ids: set[str] = {
        str(item.get('post_id') or '')
        for item in (_candidates_obj.get('items') or [])
        if isinstance(item, dict)
        and (
            'clawdbot' in str(item.get('author') or '').lower()
            or 'agent_01kg82hhh5tf1nm3af35fh396q' in str(item.get('author') or '').lower()
        )
    }
    _own_post_ids.discard('')

    curation_source: str
    curation_actions: list[dict[str, Any]]
    if intent_active and intent_curate_targets:
        curation_actions = build_curate_actions_from_intent(intent_curate_targets, own_post_ids=_own_post_ids)
        curation_actions = filter_recent_curate_targets(curation_actions)
        if curation_actions:
            curation_source = 'social-intent-curate_targets'
        else:
            # FIX-2026-05-25: all intent curate_targets were filtered out (already noop'd within
            # lookback window) — fall through to feed scan so the cycle isn't wasted.
            print('[execute_social_intent_v2] all intent curate_targets filtered; falling back to feed scan', file=sys.stderr)
            _raw_feed_actions = scan_feed_for_curations(api_key)
            curation_actions = filter_recent_curate_targets(_raw_feed_actions, lookback_hours=8)
            curation_source = 'feed-fallback' if curation_actions else 'none'
    else:
        # Fallback: scan feed for top-engagement low-VP posts
        # Use 8h lookback (reduced from 24h) to prevent the 30-item feed from being exhausted
        _raw_feed_actions = scan_feed_for_curations(api_key)
        curation_actions = filter_recent_curate_targets(_raw_feed_actions, lookback_hours=8)
        curation_source = 'feed-fallback' if curation_actions else 'none'
        if curation_source == 'none':
            # Explicit blocker — do NOT silently swallow this. Surfaces in execution result.
            print(
                f'[execute_social_intent_v2] BLOCKER: curation_source=none — '
                f'no executable curation actions after feed fallback scan. '
                f'Possible causes: (1) feed API unreachable or returned 0 pages, '
                f'(2) all feed candidates already curated (participation check blocked all), '
                f'(3) cooldown filter (8h) exhausted all candidates. '
                f'intent_active={intent_active}, intent_curate_targets_count={len(intent_curate_targets)}, '
                f'raw_feed_candidates={len(_raw_feed_actions)}',
                file=sys.stderr,
            )

    # === POST/REPLY PATH (from social-intent directives or autonomy + drafts) ===
    # Also read post_directive / reply_directive from social-intent if present.
    post_reply_actions: list[dict[str, Any]] = []
    post_directive_deduped = False
    intent_post_already_executed = intent_active and social_intent_already_has_successful_post(social_intent, social_hist)
    if intent_active:
        intent_payload = social_intent.get('payload') or {}
        post_dir = intent_payload.get('post_directive')
        reply_dir = intent_payload.get('reply_directive')
        if post_dir:
            deduped = intent_post_already_executed or post_directive_already_executed(post_dir, social_intent, social_hist)
            if deduped:
                # Bypass threshold: 4h when daily resource floor is unmet (catch-up mode),
                # 24h otherwise (normal cadence gate).
                # Rationale: when floor is unmet we cannot afford to skip posts due to
                # hash-only dedup from a previous cycle; the posting cadence must increase.
                _dedupe_bypass_h = 4.0 if _resource_floor_unmet else 24.0
                age_h = last_successful_post_age_hours(social_hist)
                # Bypass when: no successful posts at all (age_h is None) OR last post
                # was older than the threshold.
                if age_h is None or age_h > _dedupe_bypass_h:
                    deduped = False
            if deduped:
                post_directive_deduped = True
            else:
                pa = build_post_action_from_directive(post_dir)
                if pa:
                    post_reply_actions.append(pa)
        if reply_dir:
            ra = build_reply_action_from_directive(reply_dir)
            if ra:
                post_reply_actions.append(ra)
        # New: parse payload.actions array for post/reply items
        # post_directive / reply_directive take priority; actions[] only fill gaps
        _posted_ticks_this_run: set[str] = set()  # BUG-3: prevent same tick posting twice in one run
        for action_item in (intent_payload.get('actions') or []):
            if not isinstance(action_item, dict):
                continue
            action_type = action_item.get('type')
            if action_type == 'post':
                if intent_post_already_executed:
                    post_directive_deduped = True
                    continue
                pa = build_post_action_from_actions_item(action_item, drafts_obj)
                if pa:
                    inline_post = pa.get('_inline_draft') or {}
                    dedup_directive: dict[str, Any] = {
                        'text': inline_post.get('text'),
                        'tick': inline_post.get('tick'),
                        'draft_type': inline_post.get('draft_type', ''),
                        'source': inline_post.get('_draft_source', inline_post.get('source', '')),
                    }
                    if inline_post.get('source_tweet_id'):
                        dedup_directive['source_tweet_id'] = inline_post['source_tweet_id']
                    if post_directive_already_executed(dedup_directive, social_intent, social_hist):
                        post_directive_deduped = True
                        continue
                    # BUG-3: per-tick gate — skip if same tick already queued in this run.
                    # When only one trending tick exists, synthesize_trade_drafts generates
                    # two drafts targeting the same tick (trade-tick + trade-heat can collide).
                    # This prevents them both executing in one cycle.
                    _action_tick = str(inline_post.get('tick') or '').strip().lower()
                    if _action_tick and _action_tick in _posted_ticks_this_run:
                        print(f'[execute_social_intent_v2] per-tick-run dedup: tick={_action_tick} already queued this run, skipping', file=sys.stderr)
                        post_directive_deduped = True
                        continue
                    if _action_tick:
                        _posted_ticks_this_run.add(_action_tick)
                    # Last-resort sidecar gate: (source, tick) pair blocked if posted within 8h.
                    _sidecar_src = str(inline_post.get('_draft_source') or inline_post.get('source') or '').strip()
                    _sidecar_tick = str(inline_post.get('tick') or '').strip()
                    if _sidecar_src and _sidecar_tick and _trade_draft_dedup_blocked(_sidecar_src, _sidecar_tick):
                        post_directive_deduped = True
                        continue
                    # FIX-A-2026-05-25: removed single-post-per-run gate — allow all
                    # non-deduped post actions from intent.payload.actions[] to execute.
                    # The upstream max_per_type cap (run_main_runtime) already limits how
                    # many posts are selected; here we honour that selection fully.
                    # Cross-intent dedup (intent_post_already_executed, line 1732) still
                    # prevents reposting the same intent twice across bookmarker re-runs.
                    post_reply_actions.append(pa)
            elif action_type == 'reply':
                ra = build_reply_action_from_actions_item(action_item, drafts_obj)
                if ra and not any(a.get('type') == 'reply' for a in post_reply_actions):
                    post_reply_actions.append(ra)

    # Autonomy-based actions (post/reply/like from drafts, non-conservative only)
    autonomy_actions: list[dict[str, Any]] = []
    if autonomy_mode != 'conservative':
        autonomy_actions = build_actions_from_autonomy(autonomy, drafts_obj)

    # Merge: curations first (intent or fallback), then post/reply from directives, then autonomy actions
    # De-duplicate curate targets already covered by intent
    intent_tweet_ids = {a['_inline_draft']['tweetId'] for a in curation_actions if isinstance(a.get('_inline_draft'), dict)}
    filtered_autonomy: list[dict[str, Any]] = []
    for a in autonomy_actions:
        inline = a.get('_inline_draft') or {}
        if a.get('type') == 'curate' and inline.get('tweetId') in intent_tweet_ids:
            continue  # skip: already in curate_actions from intent
        if a.get('type') == 'post' and intent_post_already_executed:
            post_directive_deduped = True
            continue
        _autonomy_dedup: dict[str, Any] = {
            'text': inline.get('text'),
            'tick': inline.get('tick'),
        }
        if inline.get('source_tweet_id'):
            _autonomy_dedup['source_tweet_id'] = inline['source_tweet_id']
        if a.get('type') == 'post' and post_directive_already_executed(_autonomy_dedup, social_intent, social_hist):
            post_directive_deduped = True
            continue
        filtered_autonomy.append(a)

    # post_reply_actions from intent take priority; skip autonomy post/reply if intent provided them
    intent_has_post = any(a.get('type') == 'post' for a in post_reply_actions)
    intent_has_reply = any(a.get('type') == 'reply' for a in post_reply_actions)
    for a in filtered_autonomy:
        if a.get('type') == 'post' and (intent_has_post or intent_post_already_executed):
            post_directive_deduped = True
            continue
        if a.get('type') == 'reply' and intent_has_reply:
            continue
        post_reply_actions.append(a)

    actions = curation_actions + post_reply_actions

    if not actions:
        atomic_write_json(SOCIAL_EXECUTION_PLAN, build_social_execution_plan(run_id, autonomy, drafts_obj, []))
        dedupe_note = ', post_directive=already-executed' if post_directive_deduped else ''
        out = {
            'version': 'v2', 'agent': 'bookmarker', 'run_id': run_id, 'status': 'noop',
            'generated_at': iso(), 'autonomy_ref': 'runtime/bookmarker/autonomy-intent.json',
            'social_intent_ref': 'runtime/main/social-intent.json',
            'lock_name': LOCK_NAME, 'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
            'notes': f'no executable actions (autonomy_mode={autonomy_mode}, intent_active={intent_active}, curation_source={curation_source}{dedupe_note})',
            'autonomy_mode': autonomy_mode, 'autonomy_reason': autonomy.get('reason'),
            'intent_active': intent_active, 'curation_source': curation_source,
            'post_directive_deduped': post_directive_deduped,
        }
        write_social_execution_result(out)
        refresh_runtime_status()
        print(json.dumps({'status': 'noop', 'reason': out['notes']}, ensure_ascii=False))
        return 0

    acquired, _locks = acquire_lock(run_id)
    if not acquired:
        atomic_write_json(SOCIAL_EXECUTION_PLAN, build_social_execution_plan(run_id, autonomy, drafts_obj, []))
        out = {
            'version': 'v2', 'agent': 'bookmarker', 'run_id': run_id, 'status': 'blocked',
            'generated_at': iso(), 'autonomy_ref': 'runtime/bookmarker/autonomy-intent.json',
            'lock_name': LOCK_NAME, 'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
            'notes': 'social execution lock is currently held by another run'
        }
        write_social_execution_result(out)
        refresh_runtime_status()
        print(json.dumps({'status': out['status'], 'reason': out['notes']}, ensure_ascii=False))
        return 1

    try:
        atomic_write_json(SOCIAL_EXECUTION_PLAN, build_social_execution_plan(run_id, autonomy, drafts_obj, actions))

        # --- Budget hard constraint enforcement ---
        budget_doc = read_json(BUDGET_ALLOCATION) or {}
        bk_alloc = ((budget_doc.get('allocations') or {}).get('bookmarker') or {})
        op_budget_remaining = float(bk_alloc.get('op_budget', 0))
        vp_budget_remaining = float(bk_alloc.get('vp_budget', 0))

        # Fallback curation minimum budget: even when main gives 0 budget,
        # bookmarker can spend up to 15 OP + 15 VP on feed-fallback curations.
        # This ensures at least ~5 low-VP curations happen every cycle.
        FALLBACK_MIN_OP = 15.0   # 5 curations × 3 OP each
        FALLBACK_MIN_VP = 15.0   # 5 curations × 3 VP max each
        if curation_source == 'feed-fallback' and op_budget_remaining < FALLBACK_MIN_OP:
            op_budget_remaining = max(op_budget_remaining, FALLBACK_MIN_OP)
            vp_budget_remaining = max(vp_budget_remaining, FALLBACK_MIN_VP)

        # ── Daily VP floor enforcement ───────────────────────────────────────
        # Canonical policy: daily VP target is tracked by the bookmarker resource
        # tracker. If we're behind pace, raise this cycle's VP budget based on the
        # remaining shortfall and the number of 30m cycles left today.
        vp_target_state = compute_vp_floor_from_resource_status()
        vp_floor_this_cycle = float(vp_target_state.get('vp_floor_this_cycle') or 0.0)
        if vp_budget_remaining < vp_floor_this_cycle:
            vp_budget_remaining = vp_floor_this_cycle
            print(json.dumps({
                'vp_floor_applied': True,
                'vp_spent_today': vp_target_state.get('vp_spent_today'),
                'vp_remaining_to_target': vp_target_state.get('vp_remaining_to_target'),
                'cycles_remaining_today': int(vp_target_state.get('cycles_remaining_today') or 0),
                'vp_floor_this_cycle': round(vp_floor_this_cycle, 2),
                'reason': f"daily VP pacing: spent {vp_target_state.get('vp_spent_today'):.1f} of {vp_target_state.get('daily_vp_target'):.1f}; "
                          f"raising vp_budget to {vp_floor_this_cycle:.2f} this cycle",
            }, ensure_ascii=False), flush=True)
        # ─────────────────────────────────────────────────────────────────────
        budget_enforced = True
        budget_dropped: list[dict[str, Any]] = []
        budget_passed: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for action in actions:
            atype = action.get('type') or ''
            op_cost = OP_COST.get(atype, 0)
            # VP cost: curate uses the vp field from the draft; others 0
            inline = action.get('_inline_draft') or {}
            vp_cost = float(inline.get('vp', 0)) if atype == 'curate' else 0.0
            if op_cost > op_budget_remaining:
                budget_dropped.append({
                    'type': atype, 'reason': f'op_budget_remaining={op_budget_remaining} < op_cost={op_cost}',
                    'target_key': inline.get('target_key'),
                })
                continue
            if vp_cost > 0 and vp_cost > vp_budget_remaining:
                budget_dropped.append({
                    'type': atype, 'reason': f'vp_budget_remaining={vp_budget_remaining} < vp_cost={vp_cost}',
                    'target_key': inline.get('target_key'),
                })
                continue

            res = execute_action(api_key, action, drafts_obj)
            results.append(res)
            budget_passed.append(action)
            archive_post_result(action, res, run_id)
            _record_published_source(action, res)
            _trigger_telegram_sync(action, res)
            # Sidecar write: record (source, tick) on successful trade-draft posts.
            if res.get('status') == 'ok' and action.get('type') == 'post':
                _il = action.get('_inline_draft') or {}
                _sd_src = str(_il.get('_draft_source') or _il.get('source') or '').strip()
                _sd_tick = str(_il.get('tick') or '').strip()
                if _sd_src and _sd_tick:
                    _trade_draft_dedup_record(_sd_src, _sd_tick)

            # P2B: write tweet_id to shared exclusion set for curate ok + noop
            # so both intent-path and direct-path share curation state
            if atype == 'curate' and res.get('status') in ('ok', 'noop'):
                _ctid = str((action.get('_inline_draft') or {}).get('tweetId') or '').strip()
                if _ctid:
                    _write_curated_exclusion(_ctid)

            # Only successful writes consume budget. This prevents feed-fallback curate no-ops
            # (e.g. already-curated race/edge cases) from starving later post actions.
            if res.get('status') == 'ok':
                op_budget_remaining -= op_cost
                vp_budget_remaining -= vp_cost

        # === ENGAGEMENT REPLY PATH ===
        # After successful post, reply to high-Credit target agents to attract curation.
        # Does NOT count against max_total_actions. Graceful degrade on any failure.
        try:
            post_config = (social_intent.get('payload') or {}).get('post_config') or {}
            if post_config.get('reply_after_post', True) and post_config.get('engagement_mode') == 'reply_to_top_agents':
                # Check if a post action succeeded this cycle
                successful_post_id: str | None = None
                for act, res in zip(budget_passed, results):
                    if act.get('type') == 'post' and res.get('status') == 'ok':
                        successful_post_id = extract_post_id_from_result(res) or run_id
                        break
                if successful_post_id:
                    engagement_actions = build_engagement_reply_actions(
                        api_key, post_config, successful_post_id, op_budget_remaining,
                    )
                    engagement_results: list[dict[str, Any]] = []
                    for eng_act in engagement_actions:
                        eng_res = execute_action(api_key, eng_act, drafts_obj)
                        engagement_results.append(eng_res)
                    if engagement_actions:
                        write_engagement_log(
                            cycle_id=run_id,
                            our_post_id=successful_post_id,
                            engagement_actions=engagement_actions,
                            results=engagement_results,
                        )
        except Exception as _eng_err:
            import sys as _sys_eng
            print(f'[warn] engagement reply path failed (graceful degrade): {_eng_err}', file=_sys_eng.stderr)

        succeeded = sum(1 for r in results if r.get('status') == 'ok')
        noops = sum(1 for r in results if r.get('status') == 'noop')
        failed = sum(1 for r in results if r.get('status') == 'blocked')
        status = 'ok' if failed == 0 else ('partial' if (succeeded + noops) > 0 else 'blocked')
        generated_at = iso()
        out = {
            'version': 'v2',
            'agent': 'bookmarker',
            'actor': 'bookmarker',
            'run_id': run_id,
            'status': status,
            'generated_at': generated_at,
            'autonomy_ref': 'runtime/bookmarker/autonomy-intent.json',
            'social_intent_ref': 'runtime/main/social-intent.json',
            'autonomy_mode': autonomy_mode,
            'autonomy_reason': autonomy.get('reason'),
            'intent_active': intent_active,
            'curation_source': curation_source,
            'post_directive_deduped': post_directive_deduped,
            'lock_name': LOCK_NAME,
            'results': results,
            'summary': {
                'attempted': len(actions),
                'succeeded': succeeded,
                'noop': noops,
                'failed': failed,
                'curations_attempted': sum(1 for a in budget_passed if a.get('type') == 'curate'),
            },
            'notes': (
                f'bookmarker execution: autonomy_mode={autonomy_mode}, '
                f'intent_active={intent_active}, curation_source={curation_source}'
            ),
            'budget_enforcement': {
                'enforced': budget_enforced,
                'op_budget_initial': float(bk_alloc.get('op_budget', 0)),
                'vp_budget_initial': float(bk_alloc.get('vp_budget', 0)),
                'op_budget_remaining': round(op_budget_remaining, 6),
                'vp_budget_remaining': round(vp_budget_remaining, 6),
                'actions_passed': len(budget_passed),
                'actions_dropped': len(budget_dropped),
                'dropped_details': budget_dropped,
            },
        }
        write_social_execution_result(out)
        # actor='bookmarker' is enforced in append_social_history default parameter
        append_social_history(results, generated_at, actor='bookmarker')
        update_social_write_state(results, generated_at)
        refresh_runtime_status()
        print(json.dumps({'status': status, 'attempted': len(actions), 'succeeded': succeeded, 'noop': noops, 'failed': failed,
                          'autonomy_mode': autonomy_mode, 'curation_source': curation_source}, ensure_ascii=False))
        return 0 if status in {'ok', 'partial'} else 1
    finally:
        release_lock()
        refresh_runtime_status()


if __name__ == '__main__':
    raise SystemExit(main())

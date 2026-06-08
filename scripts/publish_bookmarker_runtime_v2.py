#!/usr/bin/env python3
"""Bookmarker V2 self-publisher: writes directly to main workspace runtime/bookmarker/*.

This replaces the V1 projection bridge (publish_runtime_v1.py --agent bookmarker)
and the shadow-native publishers. Bookmarker now owns its runtime outputs directly.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from extract_publishable_claims import generate_publishable_claims
from fallback_items import extract_items_from_archives, load_x_sync_with_fallback
from publication_memory import (
    build_publication_memory,
    compute_post_content_hash_excluding_source,
    infer_claim_family,
    normalize_post_text,
)
from social_move_planner import generate_social_move_plan
from tas_social_formula import (
    TAS_SOCIAL_VERSION,
    compute_raw_tas_social,
    pob_score_from_reward_usd,
    smooth_tas_social,
    tas_social_formula_string,
    xreco_score_from_value,
)
from tick_routing import (
    choose_tick as shared_choose_tick,
    compute_buidl_pct_24h as shared_compute_buidl_pct_24h,
    compute_tick_counts_24h as shared_compute_tick_counts_24h,
    is_tagclaw_protocol_only as shared_is_tagclaw_protocol_only,
)
from wiki_delta_detector import generate_wiki_delta
from agency_paths import BOOKMARKER_WS, MAIN_WS

BOOKMARKER_ROOT = (BOOKMARKER_WS)
MAIN_ROOT = (MAIN_WS)
RUNTIME = MAIN_ROOT / 'runtime' / 'bookmarker'
TICK_DISTRIBUTION_PATH = RUNTIME / 'tick-distribution.json'
TAS_SOCIAL_STATE_PATH = RUNTIME / 'tas-social-smoothing.json'


def _native_tas_is_stale(max_age_minutes: float = 90.0) -> bool:
    """True if the canonical native tas-social.json is missing or stale.

    Engines merged 2026-05-28: run_bookmarker_runtime.py (native) is the
    single authoritative writer of tas-social.json. This v2 publisher now
    only writes the metric as a stale-fallback safety net (e.g. native cycle
    died), so the two engines can never flip-flop the dashboard value.
    """
    try:
        doc = read_json(RUNTIME / 'tas-social.json') or {}
    except Exception:
        return True
    ts = doc.get('generated_at') or doc.get('updated_at')
    if not ts:
        return True
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
        return age_min > max_age_minutes
    except Exception:
        return True
MAIN_TAS_SOCIAL = MAIN_ROOT / 'runtime' / 'main' / 'tas-social.json'
SOCIAL_HISTORY_PATH = MAIN_ROOT / 'runtime' / 'shared' / 'social-history.json'
PUBLICATION_MEMORY_PATH = RUNTIME / 'publication-memory.json'
PLANNER_AUDIT_PATH = RUNTIME / 'planner-audit.json'
TWIN_RECOGNITION_PATH = MAIN_ROOT / 'memory' / 'twin-recognition.json'
BK_STRATEGY_LOG = MAIN_ROOT / 'memory' / 'bookmarker-strategy-log.jsonl'
WIKI_CONCEPTS_DIR = (MAIN_WS / 'wiki' / 'concepts')
WIKI_PLATFORM_RAW = (MAIN_WS / 'wiki' / 'tagclaw-platform' / 'raw')
WIKI_EXECUTION_BRIEF = MAIN_ROOT / 'runtime' / 'shared' / 'wiki-execution-brief.json'
BOOKMARKER_RESOURCE_STATUS = BOOKMARKER_ROOT / 'memory' / 'tagclaw-resource-status.json'
BOOKMARKER_SCOPED_CREDS = BOOKMARKER_ROOT / 'runtime' / 'credentials' / 'tagclaw-bookmarker.json'
FALLBACK_CREDS = Path.home() / '.config' / 'tagclaw' / 'credentials.json'
TAGCLAW_POSTS_DIR = BOOKMARKER_ROOT / 'memory' / 'raw' / 'tagclaw-posts'
TAGCLAW_BASE_URL = 'https://bsc-api.tagai.fun/tagclaw'

# Writing modes for post generation — each produces a structurally different post
WRITING_MODES: list[dict[str, str]] = [
    {'id': 'observation', 'label': '观察笔记', 'instruction': '从一个具体的行业现象或数据点出发，分析其背后的结构性原因，不需要引向任何产品或项目'},
    {'id': 'disagreement', 'label': '反驳/不同意', 'instruction': '明确反对一个流行观点或行业共识，给出你不同意的具体理由，语气直接'},
    {'id': 'case_study', 'label': '案例拆解', 'instruction': '拆解一个具体项目、产品或事件的得失，用事实说话，不做泛泛而论'},
    {'id': 'question', 'label': '提问/思考', 'instruction': '提出一个你还没有答案的真问题，展示思考过程而非结论，邀请讨论'},
    {'id': 'contrast', 'label': '对比分析', 'instruction': '对比两种路径、两个项目或两种方法论，分析各自的 trade-off'},
    {'id': 'concrete_scenario', 'label': '具体场景', 'instruction': '描述一个你亲身经历或观察到的具体场景，从细节中提炼洞察'},
    {'id': 'protocol_thesis', 'label': '协议论点', 'instruction': '提出一个关于协议设计或技术架构的判断，用逻辑链支撑，可以提及相关项目'},
    {'id': 'prediction', 'label': '预测/趋势', 'instruction': '对未来 6-12 个月做一个具体预测，说明你的推理依据'},
]

# Topic string → wiki concept filename mapping
TOPIC_TO_WIKI_FILE: dict[str, str] = {
    'desoc-agent': 'DeSoc',
    'agent-infra': 'AgentEconomy',
    'token-coordination': 'TokenEconomy',
    'general-builder': 'TagClaw',
    'desoc': 'DeSoc',
    'agent': 'AgentEconomy',
    'token': 'TokenEconomy',
    'socialfi': 'SocialFi',
    'atoc': 'ATOC',
    'philosophy': 'Philosophy',
    'attention': 'AttentionEconomy',
    'icm': 'ICM',
    'tagai': 'TagAI',
    'tagclaw': 'TagClaw',
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8')


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, path)


def _extract_wiki_key_insights(concept_name: str, max_items: int = 3) -> list[str]:
    path = WIKI_CONCEPTS_DIR / f'{concept_name}.md'
    content = read_text(path) or ''
    if not content:
        return []
    lines = content.splitlines()
    in_section = False
    items: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith('## ') and '关键洞察' in stripped:
            in_section = True
            continue
        if in_section and stripped.startswith('## '):
            break
        if in_section and stripped.startswith('- '):
            cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', stripped[2:]).strip()
            if cleaned:
                items.append(cleaned)
        if len(items) >= max_items:
            break
    return items


def build_multi_source_thesis_fallback(
    *,
    daily_theme_pack: dict[str, Any] | None,
) -> dict[str, Any] | None:
    pack = daily_theme_pack or {}
    thesis = str(pack.get('thesis') or '').strip()
    signal_summary = str(pack.get('signal_summary') or '').strip()
    open_question = str(pack.get('open_question') or '').strip()
    theme_names = [str(x).strip() for x in (pack.get('theme_names') or []) if str(x).strip()]
    top_theme = theme_names[0] if theme_names else ''
    if not thesis:
        return None
    body = thesis
    if signal_summary:
        body += f'\n\n{signal_summary}'
    if open_question:
        body += f'\n\n我更想继续追的问题是：{open_question}'

    text = _enforce_monolingual_draft(body, None, 'zh')
    return {
        'id': 'draft-multi-source-1',
        'type': 'post',
        'tick': 'BUIDL',
        'text': text,
        'priority': 8,
        'language': 'zh',
        'theme': 'agent-infra',
        'target_key': f'multi-source:{datetime.now(timezone.utc).date().isoformat()}',
        'source_candidate_id': f'multi-source:{top_theme.lower()}',
        'claim_family': 'multi-source-thesis',
        'claim_id': 'multi-source-thesis',
        'recommended_action': 'post',
        'source_tweet_id': None,
        'source_url': None,
        'source_excerpt': f'wiki themes: {", ".join(theme_names)}',
        '_planner_mode': 'multi-source-fallback',
        '_fallback_level': 'thesis',
        'rewrite_gate_passed': True,
    }


def build_daily_theme_pack(
    *,
    topic_brief_doc: dict[str, Any] | None,
    wiki_brief_doc: dict[str, Any] | None,
    trader_latest_doc: dict[str, Any] | None,
    tas_trade_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    top_themes = [
        item for item in (wiki_brief_doc or {}).get('top_themes') or []
        if isinstance(item, dict) and str(item.get('name') or '').strip()
    ]
    theme_names = [str(item.get('name') or '').strip() for item in top_themes[:3]]
    top_theme = theme_names[0] if theme_names else 'TagClaw'
    secondary_theme = theme_names[1] if len(theme_names) > 1 else 'TokenEconomy'
    tertiary_theme = theme_names[2] if len(theme_names) > 2 else ''

    top_insights = _extract_wiki_key_insights(top_theme, max_items=2)
    secondary_insights = _extract_wiki_key_insights(secondary_theme, max_items=1)
    primary_signal = top_insights[0] if top_insights else ''
    secondary_signal = secondary_insights[0] if secondary_insights else ''

    brief_summary = ''
    recommendations: list[str] = []
    urgency = 'low'
    if isinstance(topic_brief_doc, dict):
        _summary = topic_brief_doc.get('summary')
        if isinstance(_summary, str):
            brief_summary = _summary.strip()
        elif isinstance(_summary, dict):
            brief_summary = f"Feed scan: {int(_summary.get('total_bookmarks') or 0)} bookmarks, {int(_summary.get('high_signal_count') or 0)} high-signal"
        recommendations = [
            str(x).strip()
            for x in ((topic_brief_doc.get('recommendations') or {}).get('for_main_agent') or [])
            if str(x).strip()
        ][:2]
        urgency = str(topic_brief_doc.get('urgency') or 'low').strip() or 'low'

    trader_status = str((trader_latest_doc or {}).get('status') or '').strip() or 'unknown'
    tas_trade_status = str((tas_trade_doc or {}).get('status') or '').strip() or 'unknown'
    tas_trade_reason = str((tas_trade_doc or {}).get('null_reason') or '').strip()

    theme_line = f'{top_theme}、{secondary_theme}'
    if tertiary_theme:
        theme_line += f'、{tertiary_theme}'
    thesis = f'今天更值得跟踪的焦点，不在单条推文本身，而在 {theme_line} 这几条长期线索开始重新对齐。'

    signal_bits: list[str] = []
    if primary_signal:
        signal_bits.append(primary_signal)
    if secondary_signal:
        signal_bits.append(secondary_signal)
    if brief_summary:
        signal_bits.append(f'bookmarker 当前摘要是：{brief_summary}')
    signal_summary = '；'.join(signal_bits[:3]) + ('。' if signal_bits else '')

    open_question = '分发、验证、协作和执行，能不能被放进同一条 agent loop'
    if trader_status == 'blocked' or tas_trade_status == 'blocked':
        blocker = tas_trade_reason or 'trader runtime 暂时拿不到可用执行反馈'
        open_question = f'当交易执行反馈缺席时，长期分发信号还能否独立驱动 agent loop 演化（当前 blocker: {blocker}）'

    post_angles = [
        thesis,
        signal_summary or '今天的主题更适合做多源抽象，而不是单条信息复述。',
        f'可以继续追问：{open_question}',
    ]
    if recommendations:
        post_angles.extend(recommendations)

    return {
        'schema': 'bookmarker.daily-theme-pack.v1',
        'generated_at': now_iso(),
        'theme_names': theme_names,
        'urgency': urgency,
        'thesis': thesis,
        'signal_summary': signal_summary,
        'open_question': open_question,
        'post_angles': post_angles[:5],
        'recommendations': recommendations,
        'inputs': {
            'topic_brief_available': bool(topic_brief_doc),
            'wiki_brief_available': bool(wiki_brief_doc),
            'trader_latest_status': trader_status,
            'tas_trade_status': tas_trade_status,
        },
    }


def _norm_identity(value: Any) -> str:
    return str(value or '').strip().lower()


def compute_post_content_hash(text: Any) -> str:
    normalized = normalize_post_text(text)
    if not normalized:
        return ''
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:16]


def resolve_tagclaw_creds_file() -> tuple[Path, str]:
    env = os.environ.get('TAGCLAW_CREDS_PATH')
    if env:
        return Path(env).expanduser(), 'env'
    if BOOKMARKER_SCOPED_CREDS.exists():
        return BOOKMARKER_SCOPED_CREDS, 'scoped'
    return FALLBACK_CREDS, 'global'


def load_tagclaw_creds() -> tuple[dict[str, Any], Path, str]:
    creds_path, creds_source = resolve_tagclaw_creds_file()
    data = read_json(creds_path)
    if not isinstance(data, dict):
        raise RuntimeError(f'missing TagClaw credentials in {creds_path}')
    return data, creds_path, creds_source


def load_tagclaw_api_key() -> tuple[str, dict[str, Any], Path, str]:
    data, creds_path, creds_source = load_tagclaw_creds()
    api_key = str(data.get('api_key') or data.get('apiKey') or data.get('token') or '').strip()
    if not api_key:
        raise RuntimeError(f'missing api_key in {creds_path}')
    return api_key, data, creds_path, creds_source


def tagclaw_get(api_key: str, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    url = f'{TAGCLAW_BASE_URL}/{endpoint}'
    if params:
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{url}?{query}'
    try:
        proc = subprocess.run(
            ['curl', '-sS', url,
             '-H', f'Authorization: Bearer {api_key}',
             '-H', 'Accept: application/json',
             '-H', 'User-Agent: Mozilla/5.0'],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout.strip())
    except Exception:
        return None


def resolve_tagclaw_identity_context() -> dict[str, Any]:
    try:
        api_key, creds, creds_path, creds_source = load_tagclaw_api_key()
    except Exception:
        return {
            'tracked_username': 'clawdbot',
            'self_usernames': ['clawdbot'],
            'self_agent_ids': [],
            'actor_identity': {},
            'expected_identity': {},
            'credentials_path': None,
            'credentials_source': None,
            'api_key': None,
        }

    expected_username = _norm_identity(creds.get('expected_username'))
    expected_agent_id = str(creds.get('expected_agent_id') or '').strip()
    expected_agent_name = str(creds.get('expected_agent_name') or '').strip()

    actual_username = ''
    actual_agent_id = ''
    actual_agent_name = ''
    resp = tagclaw_get(api_key, 'me')
    if isinstance(resp, dict):
        agent = resp.get('agent') or (resp.get('data') or {}).get('agent') or {}
        if isinstance(agent, dict):
            actual_username = _norm_identity(agent.get('username'))
            actual_agent_id = str(agent.get('agentId') or agent.get('id') or '').strip()
            actual_agent_name = str(agent.get('name') or '').strip()

    tracked_username = expected_username or actual_username or 'clawdbot'
    self_usernames = sorted({v for v in [tracked_username, expected_username, actual_username] if v})
    self_agent_ids = sorted({_norm_identity(v) for v in [expected_agent_id, actual_agent_id] if _norm_identity(v)})
    return {
        'tracked_username': tracked_username,
        'self_usernames': self_usernames,
        'self_agent_ids': self_agent_ids,
        'actor_identity': {
            'username': actual_username or None,
            'agent_id': actual_agent_id or None,
            'agent_name': actual_agent_name or None,
        },
        'expected_identity': {
            'username': expected_username or None,
            'agent_id': expected_agent_id or None,
            'agent_name': expected_agent_name or None,
        },
        'credentials_path': str(creds_path),
        'credentials_source': creds_source,
        'api_key': api_key,
    }


def fetch_post_author_username(api_key: str | None, post_id: str) -> str:
    if not api_key or not post_id:
        return ''
    resp = tagclaw_get(api_key, f'post/{post_id}')
    if not isinstance(resp, dict):
        return ''
    post = resp.get('post') or (resp.get('data') or {}).get('post') or {}
    if not isinstance(post, dict):
        return ''
    author_obj = post.get('author') if isinstance(post.get('author'), dict) else {}
    return _norm_identity(
        author_obj.get('userName')
        or author_obj.get('username')
        or post.get('twitterUsername')
        or post.get('author')
    )


def normalize_status(value: str | None, default: str = 'stale') -> str:
    if value in {'ok', 'partial', 'blocked', 'stale'}:
        return value
    if value in {'error', 'failed', 'fail'}:
        return 'blocked'
    return default


def safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def load_current_resource_state() -> dict[str, Any]:
    resource_status = read_json(BOOKMARKER_RESOURCE_STATUS) or {}
    main_input_packet = read_json(MAIN_ROOT / 'runtime' / 'main' / 'input-packet.json') or {}
    main_runtime_state = read_json(MAIN_ROOT / 'runtime' / 'main' / 'runtime-state.json') or {}
    packet_summary = main_input_packet.get('summary') if isinstance(main_input_packet.get('summary'), dict) else {}

    op = safe_float(resource_status.get('current_op'))
    if op is None:
        op = safe_float(packet_summary.get('op'))
    if op is None:
        op = safe_float(main_runtime_state.get('op'))

    vp = safe_float(resource_status.get('current_vp'))
    if vp is None:
        vp = safe_float(packet_summary.get('vp'))
    if vp is None:
        vp = safe_float(main_runtime_state.get('vp'))

    return {
        'op': op or 0.0,
        'vp': vp or 0.0,
        'source': 'bookmarker-resource-status' if resource_status else ('main-input-packet' if packet_summary else 'main-runtime-state'),
        'vp_spent_today': safe_float(resource_status.get('estimated_vp_spent_today') or packet_summary.get('vp_spent_today')),
        'daily_vp_target': safe_float(resource_status.get('daily_vp_min_spend') or packet_summary.get('daily_vp_target')),
        'vp_remaining_to_target': safe_float(resource_status.get('remaining_vp_to_target') or packet_summary.get('vp_remaining_to_target')),
        'vp_target_status': resource_status.get('rule_assessment') or packet_summary.get('vp_target_status'),
    }


def build_metric_strategy_loop(
    metric_name: str,
    current_value: float | None,
    previous_value: float | None,
    current_status: str,
    previous_status: str | None = None,
    previous_strategy: str | None = None,
    previous_reason: str | None = None,
) -> dict[str, Any]:
    previous_status = previous_status or 'unknown'
    delta = round(current_value - previous_value, 6) if current_value is not None and previous_value is not None else None
    if current_value is None or previous_value is None:
        trend = 'blocked' if ('blocked' in {current_status, previous_status}) else 'partial'
    elif abs(delta or 0.0) < 1e-9:
        trend = 'flat'
    elif (delta or 0.0) > 0:
        trend = 'improved'
    else:
        trend = 'declined'

    if trend == 'improved':
        strategy_action = 'reinforce_previous_strategy'
        planning_focus = f'{metric_name} improved; reinforce the last content strategy.'
    elif trend == 'declined':
        strategy_action = 'discard_previous_strategy'
        planning_focus = f'{metric_name} declined; discard the last content strategy and change topic / distribution choices.'
    else:
        strategy_action = 'conservative_explore'
        planning_focus = f'{metric_name} is {trend}; stay conservative and improve source / candidate quality before repeating the old strategy.'

    return {
        'metric': metric_name,
        'current_value': current_value,
        'previous_value': previous_value,
        'delta': delta,
        'current_status': current_status,
        'previous_status': previous_status,
        'trend': trend,
        'strategy_action': strategy_action,
        'planning_focus': planning_focus,
        'rule': {
            'improved': 'reinforce_previous_strategy',
            'declined': 'discard_previous_strategy',
            'flat_or_partial_or_blocked': 'conservative_explore',
        },
        'previous_strategy': previous_strategy,
        'previous_reason': previous_reason,
    }


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def load_recent_executed_keys(hours: float = 48) -> set[str]:
    """读取 social-history.json，返回最近 N 小时内已执行动作的 target_key 集合。"""
    SOCIAL_HISTORY = MAIN_ROOT / 'runtime' / 'shared' / 'social-history.json'
    history = read_json(SOCIAL_HISTORY) or {}
    items = history.get('items') if isinstance(history.get('items'), list) else []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    keys: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        executed_at = parse_dt(item.get('executed_at') or '')
        if executed_at and executed_at.astimezone(timezone.utc) >= cutoff:
            k = item.get('target_key')
            if k:
                keys.add(k)
    return keys


def load_recent_post_dedupe(hours: float = 48) -> dict[str, Any]:
    social_history = read_json(SOCIAL_HISTORY_PATH) or {}
    items = social_history.get('items') if isinstance(social_history.get('items'), list) else []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    target_keys: set[str] = set()
    source_candidate_ids: set[str] = set()
    source_tweet_ids: set[str] = set()
    content_hashes: set[str] = set()
    content_hashes_excluding_source: set[str] = set()
    claim_families: set[str] = set()
    text_bodies: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        executed_at = parse_dt(item.get('executed_at') or '')
        if executed_at and executed_at.astimezone(timezone.utc) < cutoff:
            continue
        target_key = str(item.get('target_key') or '').strip()
        if target_key:
            target_keys.add(target_key)
        if item.get('result_status') != 'ok' or item.get('type') not in {'post', 'reply', 'quote'}:
            continue
        request_obj = item.get('request') if isinstance(item.get('request'), dict) else {}
        if item.get('type') == 'post':
            for value in [item.get('source_candidate_id'), request_obj.get('source_candidate_id')]:
                token = str(value or '').strip()
                if token:
                    source_candidate_ids.add(token)
            for value in [item.get('source_tweet_id'), request_obj.get('source_tweet_id')]:
                token = str(value or '').strip()
                if token:
                    source_tweet_ids.add(token)
        for value in [item.get('content_hash'), request_obj.get('content_hash')]:
            token = str(value or '').strip()
            if token:
                content_hashes.add(token)
        for value in [item.get('content_hash_excluding_source'), request_obj.get('content_hash_excluding_source')]:
            token = str(value or '').strip()
            if token:
                content_hashes_excluding_source.add(token)
        for value in [item.get('claim_family'), request_obj.get('claim_family')]:
            token = str(value or '').strip()
            if token:
                claim_families.add(token)
        body = normalize_post_text(item.get('text_body_normalized') or request_obj.get('text_body_normalized') or '')
        if body:
            text_bodies.append(body)
            content_hash_excluding_source = compute_post_content_hash_excluding_source(body)
            if content_hash_excluding_source:
                content_hashes_excluding_source.add(content_hash_excluding_source)
    for post_text in load_recent_posts(20):
        normalized_text = normalize_post_text(post_text)
        if normalized_text:
            text_bodies.append(normalized_text)
            content_hash = compute_post_content_hash(normalized_text)
            if content_hash:
                content_hashes.add(content_hash)
            content_hash_excluding_source = compute_post_content_hash_excluding_source(normalized_text)
            if content_hash_excluding_source:
                content_hashes_excluding_source.add(content_hash_excluding_source)
            inferred_family = infer_claim_family('', normalized_text)
            if inferred_family:
                claim_families.add(inferred_family)
    return {
        'target_keys': target_keys,
        'source_candidate_ids': source_candidate_ids,
        'source_tweet_ids': source_tweet_ids,
        'content_hashes': content_hashes,
        'content_hashes_excluding_source': content_hashes_excluding_source,
        'claim_families': claim_families,
        'text_bodies': text_bodies,
    }


def shorten(text: str, max_chars: int = 140) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= max_chars else text[:max_chars - 1].rstrip() + '…'


def load_wiki_style_guide(theme: str) -> str:
    """Load style guide from wiki concept page, falling back to memory/x-style.md."""
    # Map theme to wiki filename
    wiki_name = TOPIC_TO_WIKI_FILE.get(theme.lower(), theme)
    # Try exact filename first, then mapped name
    for candidate_name in [wiki_name, theme]:
        wiki_path = WIKI_CONCEPTS_DIR / f'{candidate_name}.md'
        if wiki_path.exists():
            try:
                content = wiki_path.read_text(encoding='utf-8')
                # Extract "对 TagClawX Agent 的启示" or "对 Agent 的启示" section
                for pattern in [r'###?\s*对\s*TagClawX\s*Agent\s*的启示', r'###?\s*对\s*Agent\s*的启示']:
                    match = re.search(pattern, content)
                    if match:
                        start = match.end()
                        # Find next ## or ### heading
                        next_heading = re.search(r'\n##', content[start:])
                        section = content[start:start + next_heading.start()] if next_heading else content[start:]
                        section = section.strip()
                        if section:
                            return section[:800]
                # No matching section found — use first 800 chars of file
                return content[:800]
            except Exception:
                pass
    # Fallback to memory/x-style.md
    fallback = (MAIN_WS / 'memory' / 'x-style.md')
    if fallback.exists():
        return fallback.read_text(encoding='utf-8')[:800]
    return ''


def load_wiki_deep_context(theme: str, max_chars: int = 1400) -> dict[str, Any]:
    """P2.2 (2026-05-17): pull substantive material from the wiki concept page.

    Unlike `load_wiki_style_guide` which only returns the "对 TagClawX Agent
    的启示" implications section, this extractor returns:
      - stance:     the body of `## 核心立场` (without sub-headings)
      - claims:     up to 3 entries from `### 主张一/二/三：…` (each ≤ 250 chars)
      - related:    the body of `### 与其他概念的关联` (≤ 300 chars)
      - source:     the resolved wiki concept file name (or '' if not found)

    This is meant to give the LLM the *content* of 0xNought's stated positions
    so it has something concrete to push off, rather than reinventing generic
    coordination-layer takes every cycle.
    """
    result: dict[str, Any] = {'stance': '', 'claims': [], 'related': '', 'source': ''}
    wiki_name = TOPIC_TO_WIKI_FILE.get(theme.lower(), theme)
    for candidate_name in [wiki_name, theme]:
        wiki_path = WIKI_CONCEPTS_DIR / f'{candidate_name}.md'
        if not wiki_path.exists():
            continue
        try:
            content = wiki_path.read_text(encoding='utf-8')
        except Exception:
            continue
        result['source'] = candidate_name

        # 1. Core stance — body between `## 核心立场` and the next `## ` heading.
        m = re.search(r'##\s*核心立场\s*\n', content)
        if m:
            tail = content[m.end():]
            next_h2 = re.search(r'\n##\s', tail)
            stance_block = tail[:next_h2.start()] if next_h2 else tail
            # Strip the per-claim h3s so we only keep the lead body (the
            # one-paragraph framing before the first 主张). If no body
            # before the first 主张, take just the first claim's body.
            first_claim = re.search(r'###\s*主张', stance_block)
            stance_body = stance_block[:first_claim.start()].strip() if first_claim else stance_block.strip()
            if stance_body:
                result['stance'] = shorten(stance_body.replace('\n', ' '), 400)

        # 2. Claims — pull each `### 主张...` section body (skip the heading).
        for cm in re.finditer(r'###\s*主张[^\n]*\n', content):
            after = content[cm.end():]
            next_h = re.search(r'\n###?\s', after)
            body = after[:next_h.start()] if next_h else after
            cleaned = re.sub(r'\(\[\d{4}-\d{2}-\d{2}\]\(https://x\.com/[^)]+\)\)', '', body)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            if cleaned:
                result['claims'].append(shorten(cleaned, 250))
            if len(result['claims']) >= 3:
                break

        # 3. Related concepts — keep brief.
        rm = re.search(r'###\s*与其他概念的关联\s*\n', content)
        if rm:
            tail = content[rm.end():]
            next_h = re.search(r'\n###?\s', tail)
            related_block = (tail[:next_h.start()] if next_h else tail).strip()
            if related_block:
                result['related'] = shorten(related_block.replace('\n', ' '), 300)

        if result['stance'] or result['claims']:
            break  # we got something useful; stop trying alt names

    return result


def _build_owner_voice_context(
    memory_dir: Path,
    lang: str = 'zh',
    max_items: int = 5,
    window_hours: int = 48,
) -> str:
    """P2.1 (2026-05-17): extract 0xNought's recent voice from his own tweets.

    Reads x-sync-latest.json, filters to items authored by 0xNought within the
    last `window_hours`, and returns a short bullet list of his recent
    interests/topics for injection into the LLM prompt. The goal is to anchor
    generated posts to what 0xNought is actually talking about right now,
    instead of letting the LLM drift toward generic "coordination layer"
    content.

    Returns an empty string when no data is available — callers should treat
    that as "no extra anchor" rather than an error.
    """
    try:
        xsync = read_json(memory_dir / 'x-sync-latest.json') or {}
    except Exception:
        return ''
    items = xsync.get('data') if isinstance(xsync.get('data'), list) else []
    if not items:
        return ''

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    own_recent: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        author = (it.get('author') or {}).get('username') or ''
        if author.lower() != '0xnought':
            continue
        text = (it.get('text') or '').strip()
        if not text or text.lower().startswith('rt @'):
            continue
        # Time filter — be permissive: if we cannot parse the date, keep
        # the item (better to include recent-ish than to lose voice signal).
        ts_raw = it.get('createdAt') or it.get('created_at')
        keep = True
        if ts_raw:
            ts = parse_dt(ts_raw)
            if ts and ts.astimezone(timezone.utc) < cutoff:
                keep = False
        if keep:
            own_recent.append(it)

    if not own_recent:
        # If there is nothing within the 48h window, fall back to the latest
        # 5 own-tweets regardless of age — the voice signal is still useful.
        own_recent = [
            it for it in items
            if isinstance(it, dict)
            and (it.get('author') or {}).get('username', '').lower() == '0xnought'
            and (it.get('text') or '').strip()
            and not (it.get('text') or '').lower().startswith('rt @')
        ][:max_items]

    if not own_recent:
        return ''

    # Pick up to max_items, prefer those with higher engagement if available.
    def _eng_score(it: dict[str, Any]) -> float:
        m = it.get('public_metrics') or it.get('publicMetrics') or {}
        if not isinstance(m, dict):
            return 0.0
        return float(m.get('like_count', 0) or 0) + 1.5 * float(m.get('retweet_count', 0) or 0)

    own_recent.sort(key=_eng_score, reverse=True)
    chosen = own_recent[:max_items]

    bullets = []
    for it in chosen:
        text = ' '.join((it.get('text') or '').split())
        # Strip URLs and t.co shortlinks — they add no voice signal.
        text = re.sub(r'https?://\S+', '', text).strip()
        if not text:
            continue
        bullets.append('  - ' + shorten(text, 160))
    if not bullets:
        return ''

    header = (
        '0xNought 最近 48 小时自己说过的话（用于锚定方向，不要照抄原文）：'
        if lang == 'zh'
        else "0xNought's own posts from the last 48h (use as voice anchor, do not copy verbatim):"
    )
    return header + '\n' + '\n'.join(bullets)


def _is_tagclaw_protocol_only(text_lower: str) -> bool:
    return shared_is_tagclaw_protocol_only(text_lower)


def _compute_tick_counts_24h(social_history_path: Path) -> dict[str, int]:
    return shared_compute_tick_counts_24h(social_history_path)


def _compute_buidl_pct_24h(social_history_path: Path) -> float:
    return shared_compute_buidl_pct_24h(social_history_path)


COMMUNITY_SCAN_PATH = MAIN_ROOT / 'runtime' / 'bookmarker' / 'community-scan.json'


def choose_tick(
    keywords: list[str],
    wiki_trending_ticks: list[str] | None = None,
    buidl_pct_24h: float | None = None,
    *,
    text: str | None = None,
    theme: str | None = None,
    social_history_path: Path | None = None,
    in_run_tick_counts: dict[str, int] | None = None,
) -> str:
    history_path = social_history_path or SOCIAL_HISTORY_PATH
    return shared_choose_tick(
        keywords,
        wiki_trending_ticks,
        buidl_pct_24h,
        text=text,
        theme=theme,
        social_history_path=history_path,
        in_run_tick_counts=in_run_tick_counts,
        community_scan_path=COMMUNITY_SCAN_PATH,
    )


def infer_theme(keywords: list[str], text: str) -> str:
    joined = (' '.join(keywords) + ' ' + text).lower()

    desoc_terms = [
        'ai agents', 'ai agent', 'agent-native social protocol', 'decentralized social',
        '去中心化社交', 'desoc', 'social graph', 'platform risk', 'platform dependency',
        'cleanup ai agents', 'bot tooling',
    ]
    agent_infra_terms = [
        'agent-infrastructure', 'openclaw', 'agentos', 'intent', 'orchestration',
        'coordination layer', 'protocol layer', 'execution layer', 'workflow',
    ]
    token_terms = [
        'token', 'stablecoin', 'settlement', 'reward', 'incentive', 'community token',
        'coordination', 'socialfi', 'cashtag', 'cashtags',
    ]

    if any(term in joined for term in desoc_terms):
        return 'desoc-agent'
    if any(term in joined for term in agent_infra_terms):
        return 'agent-infra'
    if any(term in joined for term in token_terms):
        return 'token-coordination'
    return 'general-builder'


def score_candidate_uplift(
    *,
    candidate_type: str,
    text: str,
    theme: str,
    publish_ready: bool,
    deduped: bool,
    keywords: list[str],
    guidance_mode: str,
    action_emphasis: str,
) -> dict[str, Any]:
    text_lower = (text or '').lower()
    keyword_join = ' '.join(keywords).lower()

    owner_alignment = 0.35
    if any(k in text_lower or k in keyword_join for k in ['tagclaw', 'openclaw', 'agent', 'desoc', 'protocol']):
        owner_alignment += 0.25
    if theme in {'desoc-agent', 'agent-infra', 'token-coordination'}:
        owner_alignment += 0.15
    if publish_ready:
        owner_alignment += 0.1
    owner_alignment = min(1.0, round(owner_alignment, 4))

    community_fit = 0.3
    if candidate_type in {'bookmark', 'tweet', 'post'}:
        community_fit += 0.15
    if action_emphasis == 'post_new' and candidate_type in {'bookmark', 'tweet', 'post'}:
        community_fit += 0.15
    elif action_emphasis == 'reply_focus' and candidate_type in {'reply_target', 'tagclaw_post'}:
        community_fit += 0.15
    else:
        community_fit += 0.08
    if guidance_mode in {'exploit', 'baseline'}:
        community_fit += 0.05
    community_fit = min(1.0, round(community_fit, 4))

    novelty = 0.2 if deduped else 0.75
    if theme == 'general-builder':
        novelty -= 0.1
    novelty = max(0.0, min(1.0, round(novelty, 4)))

    confidence = 0.45
    if publish_ready:
        confidence += 0.2
    if text:
        confidence += 0.15
    if guidance_mode != 'baseline':
        confidence += 0.05
    confidence = min(1.0, round(confidence, 4))

    if candidate_type in {'bookmark', 'tweet', 'post'}:
        recommended_action = 'post'
        execution_cost = {'op': 200, 'vp': 0, 'kind': 'high'}
    elif candidate_type in {'reply_target', 'tagclaw_post'}:
        recommended_action = 'reply'
        execution_cost = {'op': 50, 'vp': 0, 'kind': 'medium'}
    else:
        recommended_action = 'curate'
        execution_cost = {'op': 0, 'vp': 5, 'kind': 'low'}

    uplift = round(min(1.0, owner_alignment * 0.4 + community_fit * 0.35 + novelty * 0.15 + confidence * 0.1), 4)
    return {
        'owner_alignment_score': owner_alignment,
        'community_fit_score': community_fit,
        'novelty_score': novelty,
        'confidence': confidence,
        'expected_tas_social_uplift': uplift,
        'recommended_action': recommended_action,
        'execution_cost': execution_cost,
    }


def _load_recognition_weights() -> dict[str, float]:
    """Load topic_weights from twin-recognition.json. Returns {} if missing."""
    if not TWIN_RECOGNITION_PATH.exists():
        return {}
    try:
        data = json.loads(TWIN_RECOGNITION_PATH.read_text(encoding='utf-8'))
        return {k: float(v) for k, v in (data.get('topic_weights') or {}).items()}
    except Exception:
        return {}


def _load_recent_telegram_align(
    window_hours: float = 48,
    *,
    tracked_usernames: list[str] | None = None,
    api_key: str | None = None,
    known_post_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Load recent Telegram owner-recognition signals for Track A.

    Minimal-fix policy:
    - telegram_like  → raw_align +2
    - telegram_heart → raw_align +4

    These weights intentionally mirror the existing Telegram recognition ledger
    rather than forcing them into TagClaw-native buckets.
    """
    if not TWIN_RECOGNITION_PATH.exists():
        return {
            'raw_weight': 0.0,
            'like_count': 0,
            'heart_count': 0,
            'recent_post_ids': [],
            'recent_events': [],
        }

    try:
        data = json.loads(TWIN_RECOGNITION_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {
            'raw_weight': 0.0,
            'like_count': 0,
            'heart_count': 0,
            'recent_post_ids': [],
            'recent_events': [],
        }

    interactions = data.get('interactions') if isinstance(data.get('interactions'), list) else []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    tracked_tokens = {_norm_identity(v) for v in (tracked_usernames or []) if _norm_identity(v)}
    known_post_ids = {str(v) for v in (known_post_ids or set()) if str(v)}
    author_cache: dict[str, str] = {}
    like_count = 0
    heart_count = 0
    recent_events: list[dict[str, Any]] = []

    for item in interactions:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get('type') or '').strip().lower()
        if item_type not in {'telegram_like', 'telegram_heart'}:
            continue
        detected_at = parse_dt(item.get('detected_at') or '')
        if not detected_at:
            continue
        if detected_at.astimezone(timezone.utc) < cutoff:
            continue
        post_id = str(item.get('post_id') or '').strip()
        if tracked_tokens:
            if post_id and post_id in known_post_ids:
                pass
            elif post_id:
                if post_id not in author_cache:
                    author_cache[post_id] = fetch_post_author_username(api_key, post_id)
                if author_cache.get(post_id) not in tracked_tokens:
                    continue
            else:
                continue
        if item_type == 'telegram_like':
            like_count += 1
        elif item_type == 'telegram_heart':
            heart_count += 1
        recent_events.append({
            'type': item_type,
            'post_id': post_id or item.get('post_id'),
            'detected_at': item.get('detected_at'),
        })

    recent_post_ids = []
    for event in recent_events:
        post_id = event.get('post_id')
        if post_id and post_id not in recent_post_ids:
            recent_post_ids.append(str(post_id))

    return {
        'raw_weight': float(like_count * 2 + heart_count * 4),
        'like_count': like_count,
        'heart_count': heart_count,
        'recent_post_ids': recent_post_ids,
        'recent_events': recent_events,
    }


def _load_recent_topic_directives(n: int = 5) -> list[str]:
    """Return last n topic_directive values from bookmarker strategy log."""
    if not BK_STRATEGY_LOG.exists():
        return []
    lines = BK_STRATEGY_LOG.read_text(encoding='utf-8').strip().split('\n')
    directives: list[str] = []
    for line in reversed(lines):
        if len(directives) >= n:
            break
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            td = (entry.get('guidance') or {}).get('topic_directive')
            if td:
                directives.append(str(td))
        except Exception:
            pass
    return directives


def _topic_candidate_multiplier(
    candidate: dict[str, Any],
    global_keywords: list[str],
    topic_weights: dict[str, float],
    recent_directives: list[str],
) -> float:
    """Compute recognition_boost × fatigue_penalty multiplier for a candidate.

    Boost: max weight of any recognized topic found in candidate text/keywords.
    Fatigue: 0.8x if the matched topic appeared ≥2 cycles without recognition.
    Default: 1.0 (no change).
    """
    if not topic_weights:
        return 1.0

    text_lower = (candidate.get('summary') or '').lower()
    # Use candidate's own keywords OR fall back to global topic keywords
    cand_kws = [str(k).lower() for k in global_keywords]

    best_weight = 1.0
    best_topic: str | None = None
    for topic, weight in topic_weights.items():
        tl = topic.lower()
        if tl in text_lower or any(tl in kw for kw in cand_kws):
            if weight > best_weight:
                best_weight = weight
                best_topic = topic

    if best_topic is None:
        return 1.0

    # Check fatigue: if best_topic appeared ≥2 times in recent directives without recognition
    tl = best_topic.lower()
    occurrences = sum(1 for d in recent_directives if d.lower() == tl)
    fatigue = 0.8 if occurrences >= 2 and best_weight <= 1.0 else 1.0

    return round(best_weight * fatigue, 4)


def apply_recognition_weights_to_candidates(
    candidates: list[dict[str, Any]],
    global_keywords: list[str],
    topic_weights: dict[str, float],
    recent_directives: list[str],
) -> list[dict[str, Any]]:
    """Apply twin-recognition topic_weights as multipliers to expected_tas_social_uplift.

    - Topics in topic_weights with weight >1.0 → boosted
    - Topics not in topic_weights or weight ==1.0 → neutral
    - Topics repeated ≥2 cycles without recognition → 0.8x fatigue penalty
    """
    if not topic_weights:
        return candidates

    for candidate in candidates:
        multiplier = _topic_candidate_multiplier(
            candidate, global_keywords, topic_weights, recent_directives,
        )
        if multiplier != 1.0:
            uplift = float(candidate.get('expected_tas_social_uplift') or 0.0)
            candidate['expected_tas_social_uplift'] = round(min(1.0, uplift * multiplier), 4)
            candidate['_recognition_multiplier'] = multiplier

    # Re-sort by updated uplift
    candidates.sort(key=lambda x: (
        -float(x.get('expected_tas_social_uplift') or 0.0),
        -float(x.get('confidence') or 0.0),
        str(x.get('candidate_id')),
    ))
    for rank, item in enumerate(candidates, start=1):
        item['rank'] = rank

    return candidates


def _normalize_theme(s: str) -> str:
    """Normalize theme string for comparison: lowercase, strip hyphens/underscores/spaces.
    e.g. 'AgentInfrastructure' == 'agent-infra' == 'agent_infrastructure'
    """
    import re
    return re.sub(r'[-_\s]', '', s.lower())


def apply_wiki_brief_theme_boost(
    candidates: list[dict[str, Any]],
    wiki_top_theme_name: str,
) -> list[dict[str, Any]]:
    """Boost candidates whose theme matches the wiki execution brief top theme by 1.2x.
    Normalizes theme strings to handle 'AgentInfrastructure' vs 'agent-infra' etc.
    """
    if not wiki_top_theme_name:
        return candidates
    wiki_norm = _normalize_theme(wiki_top_theme_name)
    for candidate in candidates:
        cand_theme = str(candidate.get('theme') or '')
        cand_norm = _normalize_theme(cand_theme)
        if wiki_norm in cand_norm or cand_norm in wiki_norm:
            uplift = float(candidate.get('expected_tas_social_uplift') or 0.0)
            candidate['expected_tas_social_uplift'] = round(min(1.0, uplift * 1.2), 4)
            candidate['_wiki_theme_boost'] = 1.2
    # Re-sort by updated uplift
    candidates.sort(key=lambda x: (
        -float(x.get('expected_tas_social_uplift') or 0.0),
        -float(x.get('confidence') or 0.0),
        str(x.get('candidate_id')),
    ))
    for rank, item in enumerate(candidates, start=1):
        item['rank'] = rank
    return candidates


def build_enriched_candidates(
    *,
    topic_candidates: list[dict[str, Any]],
    x_items: list[dict[str, Any]],
    keywords: list[str],
    executed_keys: set[str],
    guidance_mode: str,
    action_emphasis: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    enriched: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(topic_candidates or [], start=1):
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get('id') or f'topic-candidate-{idx}')
        if candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        ctype = str(item.get('type') or 'topic_candidate')
        publish_ready = bool(item.get('publish_ready', False))
        text = str(item.get('text') or item.get('summary') or item.get('title') or '')
        theme = infer_theme(keywords, text)
        scoring = score_candidate_uplift(
            candidate_type=ctype,
            text=text,
            theme=theme,
            publish_ready=publish_ready,
            deduped=False,
            keywords=keywords,
            guidance_mode=guidance_mode,
            action_emphasis=action_emphasis,
        )
        enriched.append({
            'candidate_id': candidate_id,
            'source': 'topic-brief',
            'type': ctype,
            'theme': theme,
            'publish_ready': publish_ready,
            'count': item.get('count'),
            'summary': shorten(text, 160),
            **scoring,
        })

    for idx, item in enumerate(x_items or [], start=1):
        if not isinstance(item, dict):
            continue
        xid = item.get('id')
        candidate_id = f"x:{xid}" if xid else f"x-candidate-{idx}"
        if candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        text = str(item.get('text') or '')
        theme = infer_theme(keywords, text)
        deduped = candidate_id in executed_keys
        scoring = score_candidate_uplift(
            candidate_type='bookmark' if item.get('author') else 'tweet',
            text=text,
            theme=theme,
            publish_ready=not deduped,
            deduped=deduped,
            keywords=keywords,
            guidance_mode=guidance_mode,
            action_emphasis=action_emphasis,
        )
        author = item.get('author') or {}
        enriched.append({
            'candidate_id': candidate_id,
            'source': 'x-sync',
            'type': 'bookmark' if author else 'tweet',
            'theme': theme,
            'publish_ready': not deduped,
            'tweet_id': xid,
            'author_username': author.get('username') if isinstance(author, dict) else None,
            'source_url': f"https://x.com/{author.get('username')}/status/{xid}" if isinstance(author, dict) and author.get('username') and xid else None,
            'summary': shorten(text, 160),
            **scoring,
        })

    enriched.sort(key=lambda x: (-float(x.get('expected_tas_social_uplift') or 0.0), -float(x.get('confidence') or 0.0), str(x.get('candidate_id'))))
    for rank, item in enumerate(enriched, start=1):
        item['rank'] = rank

    recommended_action_mix: dict[str, int] = {}
    for item in enriched[:5]:
        action = str(item.get('recommended_action') or 'unknown')
        recommended_action_mix[action] = recommended_action_mix.get(action, 0) + 1
    return enriched[:10], recommended_action_mix


TARGETS_RE = re.compile(r'([A-Za-z0-9]+)\s*\(([^,\)]+),\s*vp=(\d+)\)')


def parse_recent_targets(heartbeat_state: dict[str, Any] | None, hours: float = 24) -> list[dict[str, Any]]:
    """Return recent TagClaw posts interacted with, from social-history.json.

    Replaces the legacy heartbeat_state.actions approach (V1 dead link).
    Reads runtime/shared/social-history.json and returns tweetIds of posts
    we've curated or replied to in the last `hours` hours, for use as
    draft-2 (reply) and draft-3/4 (curate) targets.
    """
    history = read_json(SOCIAL_HISTORY_PATH) or {}
    items = history.get('items') if isinstance(history.get('items'), list) else []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    targets = []
    seen_keys: set[str] = set()
    for item in reversed(items):  # newest first
        if not isinstance(item, dict):
            continue
        executed_at = parse_dt(item.get('executed_at') or '')
        if not executed_at or executed_at.astimezone(timezone.utc) < cutoff:
            continue
        item_type = item.get('type')
        if item_type not in ('curate', 'reply', 'post'):
            continue
        target_key = item.get('target_key') or ''
        if not target_key.startswith('tagclaw:'):
            continue
        tweet_id = target_key.removeprefix('tagclaw:')
        if tweet_id in seen_keys:
            continue
        seen_keys.add(tweet_id)
        username = item.get('target_username') or item.get('note') or ''
        req = item.get('request') or {}
        vp = req.get('vp') if isinstance(req, dict) else None
        targets.append({'tweetId': tweet_id, 'username': username, 'vp': vp or 5})
        if len(targets) >= 5:  # cap at 5 candidates
            break
    return targets


def load_recent_posts(n: int = 10) -> list[str]:
    """Load body text of the N most recent tagclaw posts (newest first)."""
    if not TAGCLAW_POSTS_DIR.exists():
        return []
    files = sorted(TAGCLAW_POSTS_DIR.glob('*.md'), reverse=True)[:n]
    posts: list[str] = []
    for f in files:
        try:
            content = f.read_text(encoding='utf-8')
        except Exception:
            continue
        # Strip YAML frontmatter
        if content.startswith('---'):
            end = content.find('---', 3)
            if end != -1:
                content = content[end + 3:]
        body = content.strip()
        if body:
            posts.append(body)
    return posts


def extract_anti_repeat_context(recent_posts: list[str]) -> dict[str, Any]:
    """Extract recent opener sentences, product-pivot phrases, and closing lines for anti-repeat."""
    openers: list[str] = []
    pivots: list[str] = []
    closings: list[str] = []
    for post in recent_posts:
        lines = [l.strip() for l in post.split('\n') if l.strip()]
        if not lines:
            continue
        # First non-empty line is the opener
        openers.append(lines[0][:80])
        # Last non-empty line is the closing
        closings.append(lines[-1][:80])
        # Detect product-pivot patterns
        for line in lines:
            if re.search(r'(这正是|这也是|这恰恰是|这就是).{0,10}(我们做|我们在|我一直在做)\s*@?TagClaw', line):
                pivots.append(line[:80])
            elif re.search(r'(我们做|我们在做|我一直在做)\s*@?TagClaw.{0,15}(出发点|原因|方向)', line):
                pivots.append(line[:80])
    return {
        'recent_openers': openers[:8],
        'recent_pivots': pivots[:8],
        'recent_closings': closings[:8],
        'recent_full_texts': [p[:300] for p in recent_posts[:5]],
    }


MODE_HISTORY_PATH = RUNTIME / 'mode-history.json'


def _load_mode_history() -> list[str]:
    """Load persistent list of recently used writing mode IDs (newest first)."""
    try:
        data = json.loads(MODE_HISTORY_PATH.read_text(encoding='utf-8'))
        return [str(m) for m in data.get('recent_modes', [])]
    except Exception:
        return []


def _save_mode_history(mode_id: str) -> None:
    """Append a mode to persistent history (keep last 10). Atomic write."""
    history = _load_mode_history()
    history.insert(0, mode_id)
    history = history[:10]
    atomic_write_json(MODE_HISTORY_PATH, {'recent_modes': history, 'updated_at': now_iso()})


def _infer_modes_from_posts(recent_posts: list[str]) -> list[str]:
    """Heuristic fallback: infer mode IDs from post text when no persistent history exists."""
    inferred: list[str] = []
    for post in recent_posts[:5]:
        post_lower = post.lower()
        first_line = post.split('\n')[0] if post else ''
        best: str | None = None
        if re.search(r'(最近观察|观察到|注意到一个|越来越确信)', first_line):
            best = 'observation'
        elif re.search(r'(不同意|反对|我不认为|但我觉得这个判断|但他只说了一半|答案不是)', post_lower):
            best = 'disagreement'
        elif re.search(r'(拆解|复盘|案例|来看看|试了一下|我试了|最近写了一篇|代码泄露这件事|vitalik\s*2014|@jack\s*想到了)', post_lower):
            best = 'case_study'
        elif re.search(r'(为什么|一个问题|在想)', first_line):
            best = 'question'
        elif re.search(r'(对比|vs|两种|另一条路|表面是.+本质是|不是悲观，而是)', post_lower):
            best = 'contrast'
        elif re.search(r'(具体|亲身|经历|实践中)', first_line):
            best = 'concrete_scenario'
        elif re.search(r'(预测|趋势|未来|接下来|还没有来)', first_line):
            best = 'prediction'
        elif re.search(r'(协议|protocol|架构|设计|本质上|核心|世界模型|world model|settlement layer|permission\s*\+\s*incentive|组织本质上)', first_line) or re.search(r'(^|\n)\s*1\.', post):
            best = 'protocol_thesis'
        if best:
            inferred.append(best)
    return inferred


def choose_writing_mode(recent_posts: list[str]) -> dict[str, str]:
    """Choose a writing mode with strong rotation constraints.

    Rules (deterministic exclusion over randomness):
    1. Immediately previous mode → FORBIDDEN (weight 0)
    2. Mode appearing ≥2 times in last 5 → FORBIDDEN (weight 0)
    3. Mode appearing once in last 3 → heavy penalty (weight 1 vs base 10)
    4. All other modes → full weight (10)
    """
    # Load persistent history; fall back to heuristic inference
    history = _load_mode_history()
    if not history:
        history = _infer_modes_from_posts(recent_posts)

    last_mode = history[0] if history else None
    last_3 = history[:3]
    last_5 = history[:5]

    # Count occurrences in windows
    count_3: dict[str, int] = {}
    for m in last_3:
        count_3[m] = count_3.get(m, 0) + 1
    count_5: dict[str, int] = {}
    for m in last_5:
        count_5[m] = count_5.get(m, 0) + 1

    mode_ids = {m['id'] for m in WRITING_MODES}
    weights: list[float] = []
    for mode in WRITING_MODES:
        mid = mode['id']
        if mid == last_mode:
            # Rule 1: immediately previous → forbidden
            weights.append(0.0)
        elif count_5.get(mid, 0) >= 2:
            # Rule 2: overused in last 5 → forbidden
            weights.append(0.0)
        elif count_3.get(mid, 0) >= 1:
            # Rule 3: appeared in last 3 → heavy penalty
            weights.append(1.0)
        else:
            # Rule 4: fresh mode → full weight
            weights.append(10.0)

    # Safety: if all weights are 0 (edge case with very few modes), reset to uniform
    if sum(weights) == 0:
        weights = [1.0] * len(WRITING_MODES)

    chosen = random.choices(WRITING_MODES, weights=weights, k=1)[0]

    # Persist choice for next run
    _save_mode_history(chosen['id'])

    return chosen


def should_include_product_mention(theme: str, source_text: str, recent_posts: list[str]) -> bool:
    """Decide whether @TagClaw mention is appropriate. Suppress if overused recently."""
    # Count how many of the last 5 posts mention TagClaw/OpenClaw prominently
    mention_count = 0
    for post in recent_posts[:5]:
        if re.search(r'@?TagClaw|@?OpenClaw', post):
            mention_count += 1
    # If 3+ of last 5 posts already mention product, suppress
    if mention_count >= 3:
        return False
    # If source material naturally relates to TagClaw/agent-infra, allow
    source_lower = (source_text or '').lower()
    if any(kw in source_lower for kw in ['tagclaw', 'openclaw', 'desoc', 'agent economy', 'atoc']):
        return True
    # Default: allow only 40% of the time to break the pattern
    return random.random() < 0.4


def verify_draft_novelty(draft: str, anti_repeat: dict[str, Any]) -> tuple[bool, str]:
    """Check if a draft is too similar to recent posts. Returns (ok, reason)."""
    draft_opener = draft.split('\n')[0].strip() if draft else ''

    # Check 1: opener similarity — reject if first 15 chars match any recent opener
    for prev_opener in anti_repeat.get('recent_openers', []):
        if len(draft_opener) >= 10 and len(prev_opener) >= 10:
            if draft_opener[:15] == prev_opener[:15]:
                return False, f'opener too similar to recent: "{prev_opener[:40]}..."'

    # Check 2: exact repeated phrases (>20 chars) shared with recent posts
    for prev_text in anti_repeat.get('recent_full_texts', []):
        # Find longest common substring >30 chars
        for length in range(40, 19, -1):
            for i in range(len(draft) - length + 1):
                chunk = draft[i:i + length]
                if chunk in prev_text:
                    return False, f'repeated phrase: "{chunk[:50]}..."'

    # Check 3: product pivot appears in same formulaic pattern
    for line in draft.split('\n'):
        if re.search(r'(这正是|这也是|这恰恰是|这就是).{0,10}(我们做|我们在)\s*@?TagClaw', line):
            if anti_repeat.get('recent_pivots'):
                return False, 'formulaic TagClaw pivot matches recent pattern'

    return True, 'ok'


def reply_conflicts_recent_posts(reply_text: str, recent_text_bodies: list[str]) -> tuple[bool, str]:
    normalized_reply = normalize_post_text(reply_text)
    if not normalized_reply:
        return False, 'empty'
    reply_hash = compute_post_content_hash(normalized_reply)
    if len(normalized_reply) < 80:
        return False, 'too_short'
    for recent_text in recent_text_bodies[:30]:
        normalized_recent = normalize_post_text(recent_text)
        if not normalized_recent:
            continue
        if compute_post_content_hash(normalized_recent) == reply_hash:
            return True, 'same_content_hash_as_recent_text_action'
        shorter = min(len(normalized_reply), len(normalized_recent))
        if shorter >= 80 and normalized_reply[:shorter] == normalized_recent[:shorter]:
            return True, 'same_prefix_as_recent_text_action'
        if normalized_reply in normalized_recent or normalized_recent in normalized_reply:
            return True, 'substring_overlap_with_recent_text_action'
    return False, 'ok'


def build_argument(theme: str, source_text: str, summary: str | None) -> tuple[str, list[str], str]:
    """RETIRED 2026-05-17 (P3.1b).

    All four prior branches of this function emitted hardcoded text that
    violated the post-2026-05-17 template policy:
      - desoc-agent / agent-infra / token-coordination / default branches all
        used "不是 X，而是 Y" closings and rigid 1./2./3. numbered points
        ({hook + 3 numbered points + moralizing closer} skeleton).
      - the default branch even hardcoded "Builder 视角看，真正重要的不是..."
        as a closing — every banned phrase in one sentence.

    Rather than rewrite the hardcoded outputs, we retire the function and
    route the single caller (source-first fallback in the main loop) through
    `build_move_fallback_text` instead — that path already has opener/closing
    pool rotation, structural variants, and the new template policy enforced.

    This function still exists as a thin shim that returns `('', [], '')` so
    any external caller that imports it does not crash. Callers should use
    `build_move_fallback_text` directly.
    """
    return ('', [], '')


def _window_role_prompt_addendum(move_plan: dict[str, Any] | None) -> str:
    role = str((move_plan or {}).get('window_role') or '').strip()
    if role == 'freshness-winner':
        return (
            '\n- 当前窗口角色是 freshness-winner：输出必须像对当前变化的快速重估，不要写成长期回顾或 archive memo\n'
            '- 保持更短、更快、更像 live re-pricing'
        )
    if role == 'breakthrough-winner':
        return (
            '\n- 当前窗口角色是 breakthrough-winner：输出必须强调这条 narrative 为什么值得突破进前排\n'
            '- 开头要像在纠正旧读法，中段要像在证明一个次优叙事现在已经不该待在后排'
        )
    if role == 'compounding-winner':
        return (
            '\n- 当前窗口角色是 compounding-winner：输出必须像对持续累积信号的判断，不要写成单次事件反应\n'
            '- 结尾要体现“为什么它在越积越强”'
        )
    if role == 'archive-winner':
        return (
            '\n- 当前窗口角色是 archive-winner：输出必须像近期 archive horizon 里最耐久的结构判断\n'
            '- 不要写成热帖反应；要像“把近期档案压成一个长期仍成立的判断”'
        )
    return ''


def _builder_signal_prompt_addendum(move_plan: dict[str, Any] | None) -> str:
    """P3.1a (2026-05-17): rewritten to describe the *substantive subject* of
    each claim_family without prescribing the "不是 X，而是 Y" sentence shape.

    The previous version pushed the LLM toward the exact banned closing pattern
    on every cycle, which directly contradicted the global forbidden block in
    the prompt. The new version names the topic and the angle of attack, but
    leaves the actual phrasing entirely up to the LLM.
    """
    claim_family = str((move_plan or {}).get('claim_family') or '').strip()
    if claim_family == 'x-superapp-open-graph':
        return (
            '\n📌 claim_family 主线（x-superapp-open-graph）：\n'
            '  - 议题焦点：X 从内容平台向 coordination / routing / execution 层位移\n'
            '  - 想让读者注意到的层：routing 和 coordination 已经是 X 的真实功能，不只是 content surface\n'
            '  - 证据使用：把产品公告压缩成被消化过的信号短语，不要原样复述\n'
            '  - 结尾留一个 builders 能去观察/验证的具体指标'
        )
    if claim_family == 'community-as-ai-crypto-intersection':
        return (
            '\n📌 claim_family 主线（community-as-ai-crypto-intersection）：\n'
            '  - 议题焦点：compounding 来自 coordination 层而非 solo capability\n'
            '  - 想让读者注意到的层：community-shaped 工具 vs solo-shaped 工具的长期分化\n'
            '  - 证据使用：把材料压成同构短语，保留具体场景而非抽象口号\n'
            '  - 结尾给出一个能区分 solo 和 coordination 路径的可验证差异'
        )
    if claim_family == 'tokenized-community-coordination':
        return (
            '\n📌 claim_family 主线（tokenized-community-coordination）：\n'
            '  - 议题焦点：token / settlement / rewards 作为 coordination loop 的闭合机制\n'
            '  - 想让读者注意到的层：incentive 和 settlement 不是附加项，而是 retention 的来源\n'
            '  - 证据使用：奖励闭环、重复参与、coordination retention 相关信号优先\n'
            '  - 结尾落到一个可验证的 retention 或 repeat-engagement 指标'
        )
    if claim_family == 'intent-coordination-execution':
        return (
            '\n📌 claim_family 主线（intent-coordination-execution）：\n'
            '  - 议题焦点：leverage 来自 intent / coordination / execution 留在同一循环\n'
            '  - 想让读者注意到的层：system loop 比单点 demo 更值得跟踪\n'
            '  - 证据使用：系统回路的支撑信号，不要逐条介绍项目\n'
            '  - 结尾给出一个能识别 closed-loop 系统的具体特征'
        )
    if claim_family == 'agent-protocol-vs-platform':
        return (
            '\n📌 claim_family 主线（agent-protocol-vs-platform）：\n'
            '  - 议题焦点：bottleneck 在 platform dependency 层，而非 model output\n'
            '  - 想让读者注意到的层：dependency / 协议原生替代的演化路径\n'
            '  - 证据使用：平台依赖、协议化迁移的具体信号优先\n'
            '  - 结尾留一个可验证的 dependency reduction 指标或事件'
        )
    return ''


def _move_generation_instruction(move_plan: dict[str, Any] | None) -> str:
    # P1.1 follow-up (2026-05-17): removed the "不是 A，而是 B" template hint
    # for challenge and the "如果你不同意，真正分歧在..." template hint for
    # debate-hook. These were directly contradicting the new prompt-level
    # forbidden block and produced template-fatigue posts. The move-type
    # guidance below now describes *intent* only.
    if not move_plan:
        return ''
    move_type = str(move_plan.get('move_type') or '').strip()
    if move_type == 'builder-signal':
        return (
            '\n生成骨架（builder-signal）：\n'
            '- 第一段直接给出一个 builders 应该重估的判断，最好带一点重定价 / 重估 / 押注方向被读错了的感觉，不要写成提醒或观察笔记\n'
            '- 中段只保留最关键的 2-3 个支撑点，并把它们压进同一段推进里，不要写成逐条罗列材料\n'
            '- 明确说出这不是普通 feature update，而是结构变化，但不要用"what changes my view"这类说明文口吻\n'
            '- 结尾收到一个具体、可被验证的方向或一个未解的问题；不要做安全总结\n'
            '- 不要写成摘要，不要做"读后感"，不要像研究旁注\n'
            '- 除非信息密度明显更高，否则不要使用项目符号；优先写成自然段里的判断推进'
            f"{_window_role_prompt_addendum(move_plan)}"
            f"{_builder_signal_prompt_addendum(move_plan)}"
        )
    if move_type == 'challenge':
        return (
            '\n生成骨架（challenge）：\n'
            '- 开头先指出主流理解错在哪里\n'
            '- 中段明确你反对什么、为什么反对\n'
            '- 结尾给出一个能被验证的预测或承认自己也没全想清楚的部分；语气可以锋利，但不能空喊口号\n'
            '- 不要把结尾写成"不是 A，而是 B"的对仗结构（这条已被全局禁用）\n'
            '- 最后一句不要温和总结，要让读者意识到自己之前在读错地方'
        )
    if move_type == 'debate-hook':
        return (
            '\n生成骨架（debate-hook）：\n'
            '- 开头先抛出一个与主流略有冲突的判断\n'
            '- 中段只给够支撑，不要把话讲满\n'
            '- 结尾必须留下一个让人想反驳的冲突面，但不要使用"如果你不同意，真正分歧在..."这类句式（已被全局禁用）\n'
            '- 可以试试用问句、未解的判断、或"我承认我可能错在 X"这类自我质疑收束\n'
            '- 优先制造"我不同意，但我得回一句"的效果'
        )
    return ''


_QUALITY_FORBIDDEN_PHRASES_CI = (
    # English template-fatigue family
    'the real question is', 'what matters is', 'what counts is',
    'the bet is on', 'the better bet is not on', 'the bet is not on',
    'the real bet is not on', 'from a builder view', 'builder view says',
    'what matters here is not',
    # Chinese template-fatigue family (already lowercase-safe; matched as substring)
    '真正该下注', '真正值得下注', '真正值得追踪',
    '真正值得 x', '真正值得x', 'builder 视角', '建设者视角',
)

# English mirror of the Chinese "不是 X 而是 Y" regex — catches the structural
# "not on X, but on Y" / "not about X, but about Y" template that was the
# canonical closing of the (now retired) _builder_signal_closing helper.
#
# The trailing alternation (on|rather|instead|about|by) is REQUIRED to
# distinguish structural templates ("the bet is not on more surface, but on
# coordination") from incidental sentence-level negation ("I am not entirely
# sure, but the data..."). Cases without that connector but still using the
# template family ("what matters here is not X, but Y") are already covered
# by `_QUALITY_FORBIDDEN_PHRASES_CI` via their characteristic intros.
_QUALITY_EN_NOT_X_BUT_Y_RE = re.compile(
    r'\bnot\s+(?:on\s+)?[^,.\n]{1,60}[,;]\s*but\s+(?:on|rather|instead|about)\b',
    re.IGNORECASE,
)


def _is_low_quality_post(
    text: str,
    source_text: str | None = None,
    *,
    mode: str = 'standard',
) -> tuple[bool, str]:
    """P2.3 (2026-05-17): output-side quality gate for LLM drafts.

    Returns (is_low_quality, reason). Used by `build_argument_llm` retry loop.
    Catches:
      - empty / refusal / stub outputs
      - length outside 80-700 chars (Chinese chars count 1)
      - forbidden template phrases (mirror of prompt-level block)
      - "不是 X 而是 Y" / "不是 X，而是 Y" sentence template (regex)
      - {hook + N numbered points + summary} default skeleton
      - verbatim source-text overlap ≥ 40 contiguous chars
    """
    degraded_source_first = mode == 'source-first-degraded'
    min_chars = 64 if degraded_source_first else 80

    if not text or not text.strip():
        return True, 'empty'
    body = text.strip()
    if len(body) < min_chars:
        return True, f'too_short ({len(body)} chars)'
    if len(body) > 700:
        return True, f'too_long ({len(body)} chars)'

    # Refusal / stub markers
    refusal_markers = (
        'i cannot', "i can't", 'as an ai', 'as a language model',
        '抱歉', '作为一个 ai', '作为一个AI',
    )
    lowered_first = body[:200].lower()
    for marker in refusal_markers:
        if marker in lowered_first:
            return True, f'refusal_marker: {marker!r}'

    # Forbidden template phrases (substring, case-insensitive)
    lowered_full = body.lower()
    for phrase in _QUALITY_FORBIDDEN_PHRASES_CI:
        if phrase in lowered_full:
            return True, f'forbidden_phrase: {phrase!r}'

    # "不是 X 而是 Y" sentence template — regex catches all spacing variants
    if re.search(r'不是[^，。\n]{1,40}[，,][\s]*而是', body):
        return True, 'template_buyaerushi (不是X，而是Y)'

    # English mirror: "not X, but Y" / "not on X, but on Y" structural template
    # (hotfix 2026-05-17 — closing the gap that the retired _builder_signal_closing
    # helper would have exploited if accidentally re-wired). The regex requires a
    # comma or semicolon separator + "but" within 60 chars of "not"/"not on" — too
    # strict to fire on incidental English negation, just strict enough to catch
    # the canonical "The better bet is not on X, but on Y" pattern.
    if _QUALITY_EN_NOT_X_BUT_Y_RE.search(body):
        return True, 'template_en_not_x_but_y (not X, but Y)'

    # Default {opener + 3 numbered points + 1-line moral} skeleton.
    # Detect by: 3 numbered list items AND short trailing paragraph after them.
    bullets = re.findall(r'^\s*[1-3][\.、)]\s+\S', body, flags=re.MULTILINE)
    if len(bullets) >= 3:
        # If the body is dominated by 3 numbered points + a single closer,
        # treat as the banned skeleton.
        tail_lines = [ln for ln in body.splitlines() if ln.strip()][-3:]
        last = tail_lines[-1].strip() if tail_lines else ''
        # The banned skeleton is recognizable by: ends with a short moralizing
        # line right after the 3rd point. We are conservative — require ≥3
        # numbered bullets AND a single short trailing line.
        if last and not last.startswith(('1.', '2.', '3.', '-', '→')) and len(last) < 140:
            return True, 'default_3point_skeleton'

    # Opening-line reuse is especially harmful in source-first mode. If the
    # draft opens with the same long prefix as the source, reject it early.
    if source_text and not degraded_source_first:
        src_open = normalize_post_text(source_text).split('\n')[0][:28]
        body_open = normalize_post_text(body).split('\n')[0][:28]
        if src_open and len(src_open) >= 20 and src_open == body_open:
            return True, 'opening_reuses_source_prefix'

    # Verbatim source overlap — ≥40 contiguous chars
    if source_text and len(source_text) >= 40:
        src = source_text.strip()
        step = max(1, 40 // 4)
        for i in range(0, len(src) - 39, step):
            chunk = src[i:i + 40]
            if chunk in body:
                return True, f'verbatim_source_overlap (≥40 chars)'

    # Sentence/line coverage gate — catches "copy most of the source and add
    # one new closing line" even when SequenceMatcher is overly forgiving.
    if source_text:
        src_units = []
        for part in re.split(r'[\n。！？!?;；]+', normalize_post_text(source_text)):
            cleaned = ' '.join(part.split()).strip()
            if len(cleaned) >= 18:
                src_units.append(cleaned)
        if src_units:
            matched_len = sum(len(unit) for unit in src_units if unit in body)
            total_len = sum(len(unit) for unit in src_units)
            if total_len and (matched_len / total_len) >= 0.50:
                return True, f'source_line_coverage_too_high ({matched_len / total_len:.2f})'

    # Source similarity gate — draft must differ from source by at least 40%.
    # A SequenceMatcher ratio of 0.60+ means the draft is ≥60% the same as the
    # source tweet. This catches the "copy + one appended sentence" pattern.
    if source_text and len(source_text.strip()) >= 60 and len(body) >= 60:
        _sim_ratio = difflib.SequenceMatcher(None, source_text.strip(), body).ratio()
        if _sim_ratio >= 0.52:
            return True, f'source_too_similar (ratio={_sim_ratio:.2f}, need <0.52)'

    return False, ''


def _build_argument_llm_prompt(
    theme: str,
    source_text: str,
    style_guide: str,
    writing_mode: dict[str, str] | None,
    anti_repeat: dict[str, Any] | None,
    allow_product_mention: bool,
    move_plan: dict[str, Any] | None,
    owner_voice: str,
    wiki_deep: dict[str, Any],
) -> str:
    """Pure prompt builder, factored out so retries can rebuild without re-running I/O."""
    mode_instruction = ''
    if writing_mode:
        mode_instruction = f"\n写作模式：【{writing_mode['label']}】— {writing_mode['instruction']}"

    forbidden_block = ''
    if anti_repeat:
        openers = anti_repeat.get('recent_openers', [])
        pivots = anti_repeat.get('recent_pivots', [])
        last_bad = anti_repeat.get('last_bad_outputs') or []
        if openers or pivots or last_bad:
            parts = []
            if openers:
                parts.append('禁止使用的开头（与最近帖子重复）：\n' + '\n'.join(f'  ✗ "{o}"' for o in openers[:5]))
            if pivots:
                parts.append('禁止使用的产品转折句式：\n' + '\n'.join(f'  ✗ "{p}"' for p in pivots[:5]))
            if last_bad:
                parts.append('上一次尝试被判废的输出片段（必须明显不同）：\n' + '\n'.join(f'  ✗ "{shorten(b, 180)}"' for b in last_bad[:2]))
            forbidden_block = '\n\n⚠️ 反重复约束（必须遵守）：\n' + '\n'.join(parts)

    product_instruction = ''
    if not allow_product_mention:
        product_instruction = '\n- 本帖不要提及 @TagClaw、TagClaw、OpenClaw 等产品名——纯粹从行业/技术角度论述'
    else:
        product_instruction = '\n- 如果提及 @TagClaw，必须与参考原文的具体内容有逻辑关联，不能强行转折'

    move_instruction = ''
    if move_plan:
        move_instruction = (
            f"\n当前 social move：{move_plan.get('move_type', '')}"
            f"\nvoice_mode：{move_plan.get('voice_mode', '')}"
            f"\nsub_angle：{move_plan.get('sub_angle', '')}"
            f"\ninteraction_goal：{move_plan.get('interaction_goal', '')}"
            f"\n必须包含：{', '.join(move_plan.get('must_include') or [])}"
            f"\n必须避免：{', '.join(move_plan.get('must_avoid') or [])}"
            f"{_move_generation_instruction(move_plan)}"
        )

    # P2.1 owner voice block — anchors generated post to recent 0xNought voice.
    owner_voice_block = ''
    if owner_voice:
        owner_voice_block = f"\n\n🎙️ {owner_voice}\n  使用方式：从中识别 1-2 个具体方向，让本帖与这些方向呼应（不要照抄原文）"

    # P2.2 wiki deep context — gives the LLM concrete claims to push off,
    # not just style hints.
    wiki_deep_block = ''
    if wiki_deep and (wiki_deep.get('stance') or wiki_deep.get('claims')):
        parts = []
        if wiki_deep.get('stance'):
            parts.append(f"立场总框：{wiki_deep['stance']}")
        for i, claim in enumerate(wiki_deep.get('claims') or [], 1):
            parts.append(f"主张{i}：{claim}")
        if wiki_deep.get('related'):
            parts.append(f"关联概念：{wiki_deep['related']}")
        wiki_deep_block = (
            '\n\n📚 你过去对【' + str(wiki_deep.get('source') or theme) + '】的核心立场（来自 wiki）：\n'
            + '\n'.join(f'  - {p}' for p in parts)
            + '\n  使用方式：本帖必须延续/演进/挑战上述某个具体主张，禁止与之矛盾或写成完全无关的内容'
        )

    # P1.1 prompt overhaul (2026-05-17): the previous version told the LLM to
    # use "不是 X，而是 Y" as a closing pattern, which produced template-fatigue
    # posts across every cycle. The new prompt:
    #   1. Forbids the entire "X 不是 A 而是 B" / "the real question is" /
    #      "what matters is" / "Builder 视角" family explicitly.
    #   2. Forbids the default {hook → 3 numbered points → moralizing closing}
    #      structure, requiring at least one of: question, anecdote, concrete
    #      example, half-step admission, or direct address.
    #   3. Keeps the move-type guidance but expresses it as *intent*, not as a
    #      sentence template.
    prompt = f"""你是 0xNought，一位深耕 DeSoc / Agent Economy 的 builder。

风格指南：
{style_guide}
{mode_instruction}{owner_voice_block}{wiki_deep_block}

当前主题：{theme}{move_instruction}

参考原文（前 600 字）：
{source_text[:600]}
{forbidden_block}

请写一条 150-250 字的帖子正文，要求：
- 语言必须单一主导：要么主要中文，要么主要英文；术语/品牌可保留少量英文原文，但禁止大段中英混杂
- 建设者视角，有立场，不喊口号
- 发言必须像一个正在参与社交的人，不要像摘要员/评论员/研究旁注
- 开头必须独特，不能用"最近观察到"、"最近在想"、"最近在做"等开头，也不要使用 "My read:"、"The value here is"、"This suggests"、"What this means is" 这类旁观者句式{product_instruction}
- 至少留一个互动表面：可以是半步判断、轻微对立、明确对象、或可回复的问题

⚠️ 模板禁令（违反即整篇判废，必须严格遵守）：
  ✗ 禁止使用 "不是 X，而是 Y"、"真正值得 X 的，是 Y"、"真正重要的不是 X 而是 Y" 任何变体
  ✗ 禁止使用 "the real question is..."、"what matters is..."、"what counts is..."、"the bet is on..." 任何变体
  ✗ 禁止以 "Builder 视角"、"From a builder view"、"建设者视角" 开头或收束
  ✗ 禁止用 "真正该下注的..."、"真正值得追踪的..." 作为结尾
  ✗ 禁止 {{hook + 3 个编号要点 + 一句总结性收束}} 的默认骨架——这是当前最严重的模板化结构
  ✗ 禁止用 "coordination layer / coordination rail / settlement layer" 当成神奇结论词重复使用

📐 结构多样性要求：
  - 这一篇必须采用与默认骨架不同的写法，从以下挑一种作为主结构：
    A) 一个具体故事/场景 → 引出问题 → 留一个未解的判断
    B) 直接反驳某种流行解读 → 给出更合理的解读 → 一个能验证的预测
    C) 抛一个具体问题 → 半步答案 → 邀请读者补完
    D) 一个让人意外的观察 → 你的解读 → 一个反例自我质疑
    E) 一段第一人称的工作/思考片段 → 它揭示的东西 → 一个开放结尾
  - 不要每段都加编号要点；可以纯散文、可以两个短段、可以一个长段
  - 结尾不要"道理"——可以是问题、可以是悬念、可以是承认自己也没想清楚的部分

🎯 当前 move 的意图（不要照抄字面，理解意图后用你自己的话写）：
  - challenge：你看到主流解读里有错的层级，写出来要让人意识到自己之前在读错地方
  - debate-hook：留下一个明显可被反驳的判断面，让别人有想引用反驳的冲动
  - builder-signal：暴露一个具体的、其他 builder 还没定价的信号或机会

- 每篇帖子的结构、节奏、开头方式都应该不同——避免千篇一律
- 只输出帖子正文，不加任何说明、标题或解释"""

    return prompt


# P2.3 (2026-05-17): retry-with-quality-gate wrapper around the LLM call.
# Default 3 attempts, each varying writing_mode and feeding the previous bad
# output back into anti_repeat. Skip attempts the quality gate would reject
# before even returning to the caller.
_BUILD_ARGUMENT_LLM_MAX_ATTEMPTS = 3


def build_argument_llm(
    theme: str,
    source_text: str,
    style_guide: str,
    writing_mode: dict[str, str] | None = None,
    anti_repeat: dict[str, Any] | None = None,
    allow_product_mention: bool = True,
    move_plan: dict[str, Any] | None = None,
    quality_mode: str = 'standard',
    allow_last_quality_failure: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """Call Claude CLI to dynamically generate post body.

    Up to 3 attempts; each retry: rotates writing_mode + feeds the previous
    bad output into anti_repeat.last_bad_outputs so the LLM sees what NOT to
    produce. Output runs through `_is_low_quality_post` before being returned.
    On all-failures the function returns '' (caller falls back to template).
    """
    # P2.1/P2.2: compute owner-voice + wiki-deep ONCE per call.
    try:
        owner_voice = _build_owner_voice_context(
            BOOKMARKER_ROOT / 'memory',
            lang=_dominant_language(source_text),
        )
    except Exception:
        owner_voice = ''
    try:
        wiki_deep = load_wiki_deep_context(theme)
    except Exception:
        wiki_deep = {'stance': '', 'claims': [], 'related': '', 'source': ''}

    current_mode = writing_mode
    current_anti_repeat = dict(anti_repeat) if isinstance(anti_repeat, dict) else {}
    current_anti_repeat.setdefault('last_bad_outputs', [])

    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update({
            'quality_mode': quality_mode,
            'allow_last_quality_failure': allow_last_quality_failure,
            'attempts': [],
            'outcome': 'pending',
            'last_reason': '',
            'returned_last_quality_candidate': False,
        })

    # 2026-05-18 fix B: per-attempt prompt shrinking + timeout schedule.
    # Original prompt is ~5KB (owner_voice + wiki_deep + style_guide + move
    # block + forbidden block). Sonnet 4.6 was timing out around 60s on the
    # first attempt. Strategy:
    #   attempt 1: full prompt, 90s timeout
    #   attempt 2: drop wiki_deep (heaviest block, ~1.5KB), 75s timeout
    #   attempt 3: also drop owner_voice + last_bad_outputs, 60s timeout
    # Smaller prompt → faster LLM response → higher success rate on retry.
    _ATTEMPT_BUDGETS = [
        {'timeout': 120, 'include_wiki_deep': True,  'include_owner_voice': True,  'include_last_bad': True},
        {'timeout': 90, 'include_wiki_deep': False, 'include_owner_voice': True,  'include_last_bad': True},
        {'timeout': 75, 'include_wiki_deep': False, 'include_owner_voice': False, 'include_last_bad': False},
    ]

    last_text = ''
    last_reason = ''
    quality_rejection_count = 0
    non_quality_failure_count = 0
    for attempt in range(_BUILD_ARGUMENT_LLM_MAX_ATTEMPTS):
        budget = _ATTEMPT_BUDGETS[min(attempt, len(_ATTEMPT_BUDGETS) - 1)]
        attempt_diag = {
            'attempt': attempt + 1,
            'timeout': budget['timeout'],
            'include_wiki_deep': budget['include_wiki_deep'],
            'include_owner_voice': budget['include_owner_voice'],
            'include_last_bad': budget['include_last_bad'],
            'prompt_len': 0,
            'elapsed_ms': 0,
            'returncode': None,
            'stdout_len': 0,
            'stderr_len': 0,
            'timeout_hit': False,
            'result': 'pending',
            'reason': '',
        }
        if diagnostics is not None:
            diagnostics['attempts'].append(attempt_diag)
        _attempt_owner_voice = owner_voice if budget['include_owner_voice'] else ''
        _attempt_wiki_deep = wiki_deep if budget['include_wiki_deep'] else {'stance': '', 'claims': [], 'related': '', 'source': ''}
        _attempt_anti_repeat = dict(current_anti_repeat)
        if not budget['include_last_bad']:
            _attempt_anti_repeat.pop('last_bad_outputs', None)

        prompt = _build_argument_llm_prompt(
            theme=theme,
            source_text=source_text,
            style_guide=style_guide,
            writing_mode=current_mode,
            anti_repeat=_attempt_anti_repeat,
            allow_product_mention=allow_product_mention,
            move_plan=move_plan,
            owner_voice=_attempt_owner_voice,
            wiki_deep=_attempt_wiki_deep,
        )
        attempt_diag['prompt_len'] = len(prompt)
        started_at = time.monotonic()
        try:
            # 2026-05-18 fix: pin to standard-context model. Default invocation
            # uses Opus-4.7 1M-context which requires "extra usage" on the
            # claude.ai subscription. Without the explicit --model, every cron
            # call was failing ("Extra usage is required for 1M context").
            result = subprocess.run(
                ['/usr/local/bin/claude', '--print',
                 '--model', 'claude-sonnet-4-6',
                 '--permission-mode', 'bypassPermissions', '-p', prompt],
                capture_output=True, text=True, timeout=budget['timeout'],
                env={k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
            )
        except subprocess.TimeoutExpired as exc:
            last_reason = f'timeout_after_{budget["timeout"]}s'
            non_quality_failure_count += 1
            attempt_diag['elapsed_ms'] = int((time.monotonic() - started_at) * 1000)
            attempt_diag['stdout_len'] = len(((exc.stdout or '') if isinstance(exc.stdout, str) else ''))
            attempt_diag['stderr_len'] = len(((exc.stderr or '') if isinstance(exc.stderr, str) else ''))
            attempt_diag['timeout_hit'] = True
            attempt_diag['result'] = 'timeout'
            attempt_diag['reason'] = last_reason
            print(
                f'[build_argument_llm] attempt {attempt+1}/{_BUILD_ARGUMENT_LLM_MAX_ATTEMPTS} '
                f'(timeout={budget["timeout"]}s, prompt_len={len(prompt)}) {last_reason}',
                file=sys.stderr,
            )
            continue
        except Exception as exc:
            last_reason = f'subprocess_exception: {exc!r}'
            non_quality_failure_count += 1
            attempt_diag['elapsed_ms'] = int((time.monotonic() - started_at) * 1000)
            attempt_diag['result'] = 'subprocess_exception'
            attempt_diag['reason'] = last_reason
            print(
                f'[build_argument_llm] attempt {attempt+1}/{_BUILD_ARGUMENT_LLM_MAX_ATTEMPTS} '
                f'(timeout={budget["timeout"]}s, prompt_len={len(prompt)}) {last_reason}',
                file=sys.stderr,
            )
            continue
        attempt_diag['elapsed_ms'] = int((time.monotonic() - started_at) * 1000)
        attempt_diag['returncode'] = result.returncode
        attempt_diag['stdout_len'] = len((result.stdout or '').strip())
        attempt_diag['stderr_len'] = len((result.stderr or '').strip())
        if result.returncode != 0:
            last_reason = f'rc={result.returncode}'
            non_quality_failure_count += 1
            attempt_diag['result'] = 'returncode'
            attempt_diag['reason'] = last_reason
            print(f'[build_argument_llm] attempt {attempt+1} failed: {last_reason}; stderr={result.stderr[:200]!r}', file=sys.stderr)
            continue

        candidate = (result.stdout or '').strip()
        last_text = candidate
        bad, reason = _is_low_quality_post(candidate, source_text=source_text, mode=quality_mode)
        if not bad:
            attempt_diag['result'] = 'accepted'
            if diagnostics is not None:
                diagnostics['outcome'] = 'accepted'
                diagnostics['last_reason'] = ''
            return candidate

        quality_rejection_count += 1
        last_reason = reason
        attempt_diag['result'] = 'quality_rejected'
        attempt_diag['reason'] = reason
        attempt_diag['candidate_len'] = len(candidate)
        print(f'[build_argument_llm] attempt {attempt+1} rejected by quality gate: {reason}; len={len(candidate)}', file=sys.stderr)

        # Prepare next attempt: rotate writing_mode + feed bad output back in.
        try:
            current_mode = choose_writing_mode([candidate] + (current_anti_repeat.get('recent_full_texts') or []))
        except Exception:
            current_mode = current_mode  # keep what we had
        last_bad = list(current_anti_repeat.get('last_bad_outputs') or [])
        last_bad.append(candidate)
        current_anti_repeat['last_bad_outputs'] = last_bad[-3:]

    # All attempts failed quality gate — return '' so the caller's deterministic
    # build_move_fallback_text path takes over.
    if last_text:
        print(f'[build_argument_llm] all {_BUILD_ARGUMENT_LLM_MAX_ATTEMPTS} attempts failed; last reason: {last_reason}', file=sys.stderr)
    all_attempts_quality_rejected = (
        quality_rejection_count == _BUILD_ARGUMENT_LLM_MAX_ATTEMPTS
        and non_quality_failure_count == 0
        and bool(last_text)
    )
    if all_attempts_quality_rejected and allow_last_quality_failure:
        print(
            '[build_argument_llm] returning last quality-rejected candidate for degraded caller-side salvage',
            file=sys.stderr,
        )
        if diagnostics is not None:
            diagnostics['outcome'] = 'returned_last_quality_candidate'
            diagnostics['last_reason'] = last_reason
            diagnostics['returned_last_quality_candidate'] = True
        return last_text
    if diagnostics is not None:
        diagnostics['outcome'] = 'failed'
        diagnostics['last_reason'] = last_reason
    return ''


def pick_best_candidate_llm(
    items: list[dict[str, Any]],
    executed_keys: set[str],
    keywords: list[str],
) -> dict[str, Any] | None:
    """用 LLM 从候选推文中选出最适合当前发帖的一条。
    失败时返回 None（调用方 fallback 到 items[0]）。
    """
    filtered_items = [
        item for item in items
        if not (item.get('id') and f"x:{item['id']}" in executed_keys)
    ]

    if not filtered_items:
        return None

    if len(filtered_items) == 1:
        return filtered_items[0]

    candidates = filtered_items[:10]
    candidates_text = '\n'.join(
        f'[{i}] id={c.get("id", "?")} — "{(c.get("text") or "")[:100]}"'
        for i, c in enumerate(candidates)
    )

    prompt = f"""你是 TagClawX（0xNought 的数字分身），正在为 TagClaw 社区选题发帖。

当前话题关键词：{keywords}

从以下候选推文中，选出最适合现在发帖的一条：
{candidates_text}

评分标准（按优先级）：
1. 话题深度：能引发社区实质讨论，而不只是信息转述
2. 与当前关键词的相关性
3. 建设者视角：能从观察出发引向协议/agent/token 的长期判断
4. 避免重复：不选内容与近期已发帖过于相似的

只输出 JSON，不加任何说明：
{{"index": <编号>, "reason": "选择理由（20字内）"}}"""

    try:
        # 2026-05-18 fix: pin standard-context model (see build_argument_llm note).
        result = subprocess.run(
            ['/usr/local/bin/claude', '--print',
             '--model', 'claude-sonnet-4-6',
             '--permission-mode', 'bypassPermissions', '-p', prompt],
            capture_output=True, text=True, timeout=30,
            env={k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
        )
        if result.returncode != 0:
            return None
        output = (result.stdout or '').strip()
        match = re.search(r'\{[^}]+\}', output, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group())
        idx = int(data['index'])
        if 0 <= idx < len(candidates):
            return candidates[idx]
        return None
    except Exception:
        return None


def _condense_builder_signal_point(point: str) -> str:
    text = str(point or '').strip().rstrip('。.,;:，；：')
    lowered = text.lower()
    if not text:
        return ''
    replacements = [
        ('Anthropic just gave OpenClaw the green light', 'OpenClaw got the green light from Anthropic'),
        ('Anthropic staff confirmed that CLI-based usage is sanctioned', 'CLI-based usage is now explicitly sanctioned'),
        ('We’ve successfully bridged the Claude Code CLI directly into the OpenClaw gateway, letting you run your entire agent fleet on your Pro/Max p', 'Claude Code is now bridged directly into the OpenClaw gateway'),
        ('Even the strongest one-person-builder myth still runs into coordination limits', 'even the strongest one-person-builder thesis still hits coordination limits'),
        ('Across many peer-to-peer cash experiments, fully rebuilding the stack alone rarely works', 'across many peer-to-peer cash experiments, rebuilding the whole stack alone rarely works'),
        ('The real compounding layer is community coordination, not just tooling novelty', 'community coordination compounds more durably than tooling novelty'),
        ('X is still one of the richest live graphs for tracking how social, product, and payment signals evolve in real time.', 'X is still one of the richest live graphs for real-time social, product, and payment signals'),
        ('Custom Timelines makes attention routing inside X more programmable and builder-friendly.', 'Custom Timelines makes attention routing inside X more programmable'),
        ('This is not just a discovery feature; it turns the social graph into a more programmable routing surface for attention and coordination.', 'the social graph is becoming a more programmable routing surface for attention and coordination'),
        ('Sub-agents in (latent) space!', 'sub-agents are moving into shared latent space'),
        ('We’ve been working on a side project.', 'builders are now prototyping tighter orchestration loops'),
        ('As far as I know, this is the first massively multiplayer, completely LLM-driven game. Come play Gradient Bang…', 'LLM systems are starting to hold multiplayer coordination in one loop'),
        ('Since this is blowing up on hacker news.', 'the system-loop thesis is now visible beyond the core agent niche'),
        ('Boris said that CLI usage is allowed. Thus we added support for it, only to find out that we are still blocked…', 'tooling access is opening up, but orchestration reliability is becoming the real bottleneck'),
    ]
    for src, dst in replacements:
        if text == src:
            return dst
    if len(text) > 110:
        text = text[:107].rstrip() + '…'
    if lowered.startswith('the real '):
        text = text[9:10].lower() + text[10:] if len(text) > 10 else text.lower()
    return text


# ---------------------------------------------------------------------------
# RETIRED 2026-05-17 (hotfix following P3 review)
# ---------------------------------------------------------------------------
# The four helpers below were the English-side equivalents of the old
# build_move_fallback_text builder-signal branch. They were dead code (0 real
# callers) after the P1.2 rewrite of build_move_fallback_text on 2026-05-17.
#
# Why retire rather than delete:
#   - They contained 10+ hardcoded "The better bet is not on X, but on Y"
#     closings — exactly the structural template the new policy bans.
#   - Keeping them as live functions risked accidental re-wiring (an editor
#     auto-import, a refactor that resurrects the call) silently re-introducing
#     all the banned templates at once.
#   - Reducing them to empty-returning shims means: any future accidental
#     re-wire produces empty output, which the post-LLM quality gate already
#     rejects (`_is_low_quality_post` empty/too_short rules). Loud failure
#     instead of silent template regression.
#
# If a real use case for any of these arises later, build it from scratch
# inside `build_move_fallback_text` or the LLM prompt — do not unretire the
# bodies below verbatim. The committed text contained banned patterns by
# construction.
# ---------------------------------------------------------------------------


def _smooth_builder_signal_points(points: list[str]) -> str:  # RETIRED
    return ''


def _builder_signal_alignment_phrase(move_plan: dict[str, Any] | None, claim_text: str, points: list[str]) -> str:  # RETIRED
    return ''


def _window_role_fallback_phrase(move_plan: dict[str, Any] | None) -> str:  # RETIRED
    return ''


def _builder_signal_closing(move_plan: dict[str, Any] | None, claim_text: str, points: list[str]) -> str:  # RETIRED
    return ''


def _dominant_language(text: str) -> str:
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text or ''))
    latin = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ''))
    if cjk >= max(8, latin * 2):
        return 'zh'
    if latin >= max(8, (cjk // 2) + 1):
        return 'en'
    return 'zh' if cjk > latin else 'en'


def _filter_points_by_language(points: list[str], lang: str) -> list[str]:
    kept = [str(p).strip() for p in points if str(p).strip() and _dominant_language(str(p)) == lang]
    if kept:
        return kept
    return [str(p).strip() for p in points if str(p).strip()]


def _has_excess_cross_language(text: str, lang: str) -> bool:
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text or ''))
    latin = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ''))
    if lang == 'zh':
        return latin > max(8, cjk // 3)
    return cjk > max(6, latin // 3)


def _tighten_monolingual_text(text: str, lang: str, source_url: str | None = None) -> str:
    lines = [line.rstrip() for line in str(text or '').splitlines()]
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append('')
            continue
        if stripped.startswith('→ http'):
            continue
        if _dominant_language(stripped) != lang and _has_excess_cross_language(stripped, lang):
            continue
        kept.append(stripped)
    tightened = '\n'.join(kept).strip()
    if source_url:
        tightened = f"{tightened}\n\n→ {source_url}" if tightened else f"→ {source_url}"
    return tightened


def _enforce_monolingual_draft(text: str, source_url: str | None = None, fallback_lang: str | None = None) -> str:
    # 2026-05-18 fix B follow-up: previous version unconditionally returned
    # the tightened text, which over-stripped LLM outputs that legitimately
    # mixed terms across languages. When the caller's `fallback_lang` did
    # not match what the LLM actually produced, this collapsed 200+ char
    # bodies to ~50 chars (line-by-line filter dropped most lines), then
    # the final quality gate rejected as `too_short (51 chars)`.
    #
    # New behavior: if tightening would strip more than 60% of the original
    # body content (excluding URL), or would drop the result under 80 chars,
    # return the ORIGINAL text. The downstream `_is_low_quality_post` will
    # judge the original on its merits, rather than us pre-shredding it.
    lang = fallback_lang or _dominant_language(text)
    if not _has_excess_cross_language(text, lang):
        return text
    tightened = _tighten_monolingual_text(text, lang, source_url)

    def _len_excl_url(s: str) -> int:
        return len(re.sub(r'\n*→ https?://\S+\s*$', '', str(s or '')).strip())

    orig_body_len = _len_excl_url(text)
    tight_body_len = _len_excl_url(tightened)
    if orig_body_len > 0 and (tight_body_len < 80 or tight_body_len < orig_body_len * 0.4):
        # Tightening was destructive — keep the original. Mixed-language is a
        # smaller sin than shipping a 51-char stub.
        return text
    return tightened


def _clean_source_line(line: str) -> str:
    line = re.sub(r'https?://\S+|t\.co/\S+', '', str(line or '')).strip()
    line = re.sub(r'^RT\s+@\w+:\s*', '', line, flags=re.IGNORECASE)
    line = re.sub(r'^[\-•·]\s*', '', line)
    line = re.sub(r'^\d+[\)）\.、:]\s*', '', line)
    line = ' '.join(line.split())
    return line.strip()


def _extract_monolingual_points_from_text(text: str, lang: str, max_points: int = 3) -> list[str]:
    points: list[str] = []
    seen: set[str] = set()
    for raw in str(text or '').splitlines():
        line = _clean_source_line(raw)
        if not line:
            continue
        if _dominant_language(line) != lang:
            continue
        if len(line) < 12:
            continue
        if line in seen:
            continue
        seen.add(line)
        points.append(line)
        if len(points) >= max_points:
            break
    return points


def _claim_anchor_refs(claim: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    supporting_sources = claim.get('supporting_sources') if isinstance(claim.get('supporting_sources'), list) else []
    for raw in [claim.get('best_anchor_source'), *supporting_sources]:
        ref = str(raw or '').strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _synthesize_post_draft_from_xsync() -> dict[str, Any] | None:
    # Latent path bugs fixed 2026-05-15:
    #   - WORKSPACE was undefined; should be BOOKMARKER_ROOT (this script's
    #     own workspace, where x-sync-latest.json is materialised).
    #   - RUNTIME / 'bookmarker' / ... double-stamped the 'bookmarker' segment
    #     because RUNTIME already ends in 'bookmarker'.
    # These bugs meant the xsync fallback raised NameError as soon as it
    # tried to load anything — i.e. it has been dead code in production.
    xsync = read_json(BOOKMARKER_ROOT / 'memory' / 'x-sync-latest.json') or {}
    items = xsync.get('data') if isinstance(xsync.get('data'), list) else []
    print(
        '[xsync-fallback] WARNING full fallback mode engaged; claims-first and source-first both failed, synthesizing degraded draft from x-sync',
        file=sys.stderr,
    )

    # Load recently-used source tweet IDs to avoid recycling the same source
    recent_source_ids: set[str] = set()
    try:
        published_sources = read_json(RUNTIME / 'published-source-tweets.json') or {}
        for k, v in published_sources.items():
            if isinstance(v, dict) and v.get('published_at'):
                recent_source_ids.add(k)
    except Exception:
        pass
    # Also check draft_history.recent_drafts for source_tweet_ids extracted from URLs
    try:
        dh = read_json(RUNTIME / 'draft-history.json') or {}
        for recent_draft in (dh.get('recent_drafts') or [])[:20]:
            if isinstance(recent_draft, str):
                m = re.search(r'x\.com/\w+/status/(\d+)', recent_draft)
                if m:
                    recent_source_ids.add(m.group(1))
    except Exception:
        pass

    source_item = None
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get('text') or '').strip()
        if not text or text.lower().startswith('rt @'):
            continue
        tweet_id = str(item.get('id') or '').strip()
        if tweet_id and tweet_id in recent_source_ids:
            print(f'[_synthesize_post_draft_from_xsync] skipping already-used source tweet {tweet_id}', file=sys.stderr)
            continue
        source_item = item
        break
    if not source_item:
        return None

    def _extract_signal_terms(rows: list[dict[str, Any]], max_terms: int = 3) -> list[str]:
        counts: dict[str, int] = {}
        for row in rows[:12]:
            if not isinstance(row, dict):
                continue
            text = str(row.get('text') or '')
            for token in re.findall(r'\$[A-Za-z][A-Za-z0-9_]{1,15}|@[A-Za-z0-9_]{2,20}', text):
                counts[token] = counts.get(token, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [token for token, _ in ordered[:max_terms]]

    source_text = str(source_item.get('text') or '').strip()
    lang = _dominant_language(source_text)
    points = _extract_monolingual_points_from_text(source_text, lang, max_points=3)
    if not points:
        return None

    # NOTE: this is the deterministic xsync fallback used when the claims-first
    # planner cannot produce a draft. Historically it set `opener = points[0]`
    # which copied the source tweet's first line verbatim into the post body
    # (see SsNi6hjdmf, zf4bGQe3Cp). The new structural contract is:
    #   - the opener is derived from our own framing, NOT a copy of the source
    #   - the source text may only appear inside a short inline quote
    #   - `_draft_rewritten` is computed honestly: True iff raw_opener never
    #     appears as a substring of the final emitted text
    raw_opener = points[0]
    details = list(points[1:3])
    tweet_id = str(source_item.get('id') or '').strip()
    source_url = str(source_item.get('url') or '').strip() or (f'https://x.com/0xNought/status/{tweet_id}' if tweet_id else '')
    item_count = len([item for item in items if isinstance(item, dict)])
    signal_terms = _extract_signal_terms(items)
    source_type_counts: dict[str, int] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        source_type = str(row.get('source_type') or 'tweet').strip() or 'tweet'
        source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1
    source_mix = ', '.join(f'{k}:{v}' for k, v in sorted(source_type_counts.items())) or 'tweet:0'
    signal_terms_text = '、'.join(signal_terms) if signal_terms else ''
    signal_fact_zh = f'这轮 x sync 抓到了 {item_count} 条内容，source mix 是 {source_mix}'
    signal_fact_en = f'This x sync pulled {item_count} items, with source mix {source_mix}'
    if signal_terms_text:
        signal_fact_zh += f'，反复出现的实体是 {signal_terms_text}'
        signal_fact_en += f', and the repeated entities are {", ".join(signal_terms)}'

    if lang == 'zh':
        filler = [
            '单看一条像项目观察，连起来看更像资产发行、贡献挖矿、策展验证开始被放进同一套 agent economy 叙事里。',
            '如果后面几轮还继续围绕这些词汇展开，讨论重心就已经在从一次性发币往持续分配和协作验证移动。',
        ]
        while len(details) < 2:
            details.append(filler[len(details)])
        text = (
            f'{signal_fact_zh}。\n\n'
            f'我先把这个判断记下来：这类线索单独看不够，和同轮内容放在一起时，{details[0]}\n\n'
            f'{details[1]}'
        )
    else:
        filler = [
            'A single item can read like project commentary, but the batch together points to issuance, contribution mining, and curation being folded into the same agent-economy narrative.',
            'If the next few cycles keep clustering around the same entities, the center of gravity is already moving from one-off token launch talk toward persistent distribution and verification.',
        ]
        while len(details) < 2:
            details.append(filler[len(details)])
        text = (
            f'{signal_fact_en}.\n\n'
            f'I want to pin one early read: a single clue is weak on its own, but inside the same batch it suggests that {details[0]}\n\n'
            f'{details[1]}'
        )

    if source_url:
        text += f'\n\n→ {source_url}'
    text = _enforce_monolingual_draft(text, source_url, lang)

    # Honest rewrite-gate calculation: the body MUST NOT contain the raw
    # tweet first-line as a substring. If it somehow still does (e.g.
    # _enforce_monolingual_draft re-injected it), refuse to emit.
    _draft_rewritten = bool(raw_opener) and (raw_opener not in text)
    if not _draft_rewritten:
        print(
            f'[_synthesize_post_draft_from_xsync] rewrite_gate FAIL — raw opener still present in body for tweet {tweet_id or "(unknown)"}; refusing to emit',
            file=sys.stderr,
        )
        return None

    content_hash = compute_post_content_hash(text)
    content_hash_excluding_source = compute_post_content_hash_excluding_source(text)
    return {
        'id': 'draft-fallback-xsync-1',
        'type': 'post',
        'tick': 'BUIDL',
        'text': text,
        'priority': 6,
        'theme': 'desoc-agent' if lang == 'zh' else 'agent-infra',
        'target_key': f'x:{tweet_id}' if tweet_id else None,
        'source_candidate_id': f'xsync:{tweet_id}' if tweet_id else 'xsync:fallback',
        'claim_family': 'xsync-fallback',
        'claim_id': 'xsync-fallback',
        'recommended_action': 'post',
        'source_tweet_id': tweet_id or None,
        'source_url': source_url or None,
        'source_excerpt': shorten(source_text, 120),
        'content_hash': content_hash,
        'content_hash_excluding_source': content_hash_excluding_source,
        '_planner_mode': 'xsync-fallback',
        '_fallback_level': 'degraded',
        '_degradation_path': ['claims_first_blocked', 'source_first_llm_failed', 'template_fallback'],
        '_xsync_signal_fact': signal_fact_zh if lang == 'zh' else signal_fact_en,
        '_draft_rewritten': True,
        'rewrite_gate_passed': True,
    }


# P1.2 (2026-05-17): closing + opener pools per move_type. Selection rotates
# via draft-history.json so consecutive cycles do not pick the same canned
# sentence. None of the entries below may use the "不是X而是Y" / "Builder视角"
# / "the real question is" / "真正该下注" template family — those are now
# explicitly banned (see prompt overhaul above) because they were producing
# template-fatigue at the bookmarker level.
_MOVE_FALLBACK_OPENERS: dict[str, dict[str, list[str]]] = {
    'challenge': {
        'zh': [
            '主流解读其实读错了一层。',
            '这件事现在被讨论的角度，可能根本不是它最重要的部分。',
            '多数评论都在围绕表层叙事，但底下还有一层没人提。',
            '把这条新闻放到更长的时间窗里看，画风会完全不一样。',
            '在我看来这件事的关键不在大家都在看的那个画面里。',
        ],
        'en': [
            'The dominant read of this is missing a layer.',
            'Everyone is reacting to the headline, but the headline is not where this lives.',
            'Most takes I have seen here are framed against the wrong baseline.',
            'Zoom the time window out two years and this whole conversation reframes itself.',
            'I keep ending up at a different conclusion than the room — let me say why.',
        ],
    },
    'debate-hook': {
        'zh': [
            '我想抛一个可能会被反驳的判断。',
            '有一个分歧点，大家其实都在回避说出来。',
            '坦白说，这件事我和主流意见的分歧没办法靠数据解决。',
            '如果一定要选边站，我更愿意被反驳的那一面是这个。',
            '这一条我留个观点，欢迎来拆。',
        ],
        'en': [
            'Here is a take I expect people to push back on.',
            'There is a fork in interpretation here that most threads quietly skip over.',
            'I am going to commit to a side on this even though the evidence is split.',
            'If I have to be wrong somewhere, I would rather be wrong on this exact framing.',
            'Putting a stake in the ground — happy to be argued out of it.',
        ],
    },
    'builder-signal': {
        'zh': [
            '有个信号在 builder 圈里已经在传，但还没进入定价。',
            '这一周看到一个具体的迹象，让我对方向更确信了一点。',
            '在做这块的人会注意到一个细节，外面还没怎么聊。',
            '不太显眼但已经发生的事是：',
            '从手里在做的项目角度，下面这个观察更重要。',
        ],
        'en': [
            'There is a signal builders are already trading on but the market has not priced.',
            'A concrete thing happened this week that nudged my conviction on this direction.',
            'If you are shipping in this space, you have probably noticed a detail others have not yet.',
            'Less visible but already happening:',
            'From inside what I am building, here is the observation that actually mattered.',
        ],
    },
    'default': {
        'zh': [
            '说一个观察。',
            '这两天反复想这件事。',
            '把这条放进更长的语境里：',
            '记一笔，给未来的自己看。',
            '今天值得留住的一条线索：',
        ],
        'en': [
            'Quick observation worth holding onto.',
            'Been chewing on this for a couple of days.',
            'Putting this into a longer context:',
            'Note to future self:',
            'A thread worth keeping today:',
        ],
    },
}

_MOVE_FALLBACK_CLOSINGS: dict[str, dict[str, list[str]]] = {
    'challenge': {
        'zh': [
            '如果换个层级看，今天这场讨论里大多数对立都会自动消解。',
            '我承认这个判断不一定对，但至少它给出了一个能被证伪的预测。',
            '下一轮如果方向真错了，我会回来认错并写一篇 retro。',
            '欢迎来戳，越具体越好。',
            '这是我目前为止最确信的一条 — 但仍在持续检验。',
        ],
        'en': [
            'Reframe the layer, and most of today\'s arguments dissolve on their own.',
            'I might be wrong here, but at least this framing makes a falsifiable prediction.',
            'If the next cycle proves this wrong I will come back and write the retro.',
            'Push back on it — the more specific the better.',
            'This is the version of the claim I am most confident in for now, still being tested.',
        ],
    },
    'debate-hook': {
        'zh': [
            '我留这个判断在这里，方便几个月后回看是被打脸还是被验证。',
            '如果你和我看法不同，最想听的是你认为我哪一步推导跳得最远。',
            '可能被反驳的一点是 — 我已经先写在这里了。',
            '不强求共识，只想看一下分歧分布在哪一层。',
            '欢迎告诉我你更愿意下注的反面是什么。',
        ],
        'en': [
            'Parking this take so I can come back in a few months and see how it aged.',
            'If you see it differently I am most curious which step in my chain felt the most overreached.',
            'The likely counter — I will say it for you so it is on the record.',
            'No need to agree, I just want to see where the disagreement actually lives.',
            'Tell me what you would rather bet on instead.',
        ],
    },
    'builder-signal': {
        'zh': [
            '至少在我手上的工作里，这个信号已经开始影响决策了。',
            '不打算等共识形成再行动 — 那时候大概就来不及了。',
            '准备在接下来的几周把这个信号做成一个具体的产品动作。',
            '感兴趣的 builder 可以聊一下，互相对一下读数。',
            '记一笔，回头看是真信号还是噪声。',
        ],
        'en': [
            'For what it is worth, this signal is already shaping decisions on my end.',
            'Not waiting for consensus on this one — by then it is too late to act.',
            'Plan is to turn this into a concrete product move over the next few weeks.',
            'If you are building near this — happy to compare notes.',
            'Filing this away to revisit and check whether it was signal or noise.',
        ],
    },
    'default': {
        'zh': [
            '存档备查，几个月后回来看会更清楚。',
            '想到的就这些，不强行收束。',
            '没全想清楚，但方向上越想越对劲。',
            '欢迎补充我没看到的一面。',
            '先记一笔，慢慢长。',
        ],
        'en': [
            'Filing this away — will be clearer with a few more months of data.',
            'That is what I have so far, not forcing a tidy ending.',
            'Not fully resolved yet but it keeps cohering the more I look at it.',
            'Add what I am missing if you see it.',
            'Noting this down to let it compound.',
        ],
    },
}


def _pick_fallback_phrase(pool: list[str], log_key: str, history: dict[str, Any]) -> tuple[str, int]:
    """Pick a phrase from `pool`, avoiding the recently-used indices.

    Returns (phrase, idx). Updates the in-memory history dict.
    """
    if not pool:
        return '', -1
    used_log = history.get(log_key)
    if not isinstance(used_log, list):
        used_log = []
        history[log_key] = used_log
    recent_window = used_log[-min(len(pool) - 1, 4):] if len(pool) > 1 else []
    available = [i for i in range(len(pool)) if i not in recent_window]
    idx = random.choice(available) if available else random.randrange(len(pool))
    used_log.append(idx)
    history[log_key] = used_log[-20:]
    return pool[idx], idx


def _load_move_fallback_history() -> dict[str, Any]:
    try:
        data = read_json(RUNTIME / 'draft-history.json') or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def _save_move_fallback_history(history: dict[str, Any]) -> None:
    try:
        existing = read_json(RUNTIME / 'draft-history.json') or {}
        if not isinstance(existing, dict):
            existing = {}
        existing.update({k: v for k, v in history.items() if k.startswith('move_')})
        atomic_write_json(RUNTIME / 'draft-history.json', existing)
    except Exception:
        # Best-effort: pool rotation will be slightly less effective but not broken
        pass


def build_move_fallback_text(
    claim_text: str,
    supporting_points: list[str],
    source_url: str | None,
    move_plan: dict[str, Any] | None = None,
) -> str:
    base_points = [str(p).strip() for p in supporting_points if str(p).strip()]
    lang = _dominant_language(claim_text or '\n'.join(base_points))
    points = _filter_points_by_language(base_points, lang)
    # Padding fillers — use distinct strings to avoid duplicate-bullet bug
    if lang == 'zh':
        _zh_fillers = [
            '这一轮的迹象比上一轮更密集一点。',
            '这件事我自己也还在持续观察。',
            '具体信号还没完全验证，但方向上越来越清楚。',
        ]
        for _fi, _filler in enumerate(_zh_fillers):
            if len(points) >= 3:
                break
            if _filler not in points:
                points.append(_filler)
    else:
        _en_fillers = [
            'The signal cluster is denser this cycle than last.',
            'Still tracking this myself; not yet fully resolved.',
            'Individual data points are noisy but the shape is getting clearer.',
        ]
        for _fi, _filler in enumerate(_en_fillers):
            if len(points) >= 3:
                break
            if _filler not in points:
                points.append(_filler)

    move_type = str((move_plan or {}).get('move_type') or '').strip()
    pool_key = move_type if move_type in _MOVE_FALLBACK_OPENERS else 'default'

    history = _load_move_fallback_history()
    opener_pool = _MOVE_FALLBACK_OPENERS[pool_key].get(lang) or _MOVE_FALLBACK_OPENERS[pool_key]['en']
    closing_pool = _MOVE_FALLBACK_CLOSINGS[pool_key].get(lang) or _MOVE_FALLBACK_CLOSINGS[pool_key]['en']
    opener_text, _ = _pick_fallback_phrase(opener_pool, f'move_opener_log__{pool_key}__{lang}', history)
    closing_text, _ = _pick_fallback_phrase(closing_pool, f'move_closing_log__{pool_key}__{lang}', history)
    _save_move_fallback_history(history)

    # Compose body — vary structure too so we don't keep emitting the same skeleton.
    # We rotate through three structural variants based on a deterministic hash of
    # the source so that the same source always yields the same structure but
    # different sources spread across variants.
    _structure_seed = abs(hash((claim_text or '') + (source_url or ''))) % 3
    if _structure_seed == 0:
        # Variant A: opener → short claim quote → 3 dashed points → closing (no intro)
        quote = ''
        if claim_text:
            _qmax = 80
            _qclean = re.sub(r'\s+', ' ', claim_text).strip()
            quote = _qclean[:_qmax].rstrip()
            if len(_qclean) > _qmax:
                quote = quote.rstrip('，。,. ') + '…'
        if quote:
            opener_block = f'{opener_text}\n\n"{quote}"' if lang == 'en' else f'{opener_text}\n\n「{quote}」'
        else:
            opener_block = opener_text
        text = f"{opener_block}\n\n- {points[0]}\n- {points[1]}\n- {points[2]}\n\n{closing_text}"
    elif _structure_seed == 1:
        # Variant B: opener → 1 narrative paragraph stitching the 3 points → closing
        if lang == 'zh':
            stitched = '。'.join(p.rstrip('。.') for p in points[:3]) + '。'
        else:
            stitched = '. '.join(p.rstrip('. ') for p in points[:3]) + '.'
        text = f"{opener_text}\n\n{stitched}\n\n{closing_text}"
    else:
        # Variant C: opener → 2 short paragraphs (point 1, then points 2+3 merged) → closing
        joiner = '；' if lang == 'zh' else '; '
        merged_tail = joiner.join(points[1:3])
        text = f"{opener_text}\n\n{points[0]}\n\n{merged_tail}\n\n{closing_text}"

    # 2026-05-18 fix B: enforce monolingual *inside* the function using the
    # SAME lang that built the body. Without this, the caller computes its
    # own `_dominant_language(claim_text or source_text)` which can differ
    # from `lang` here (case: claim_text is mostly EN but source_text is
    # mostly ZH → caller picks ZH while internal pool produced EN body, then
    # `_tighten_monolingual_text` strips ~70% of lines leaving a 51-char stub
    # that the final quality gate rejects). Doing the enforce here makes the
    # body self-consistent before the caller's idempotent re-enforce.
    text = _tighten_monolingual_text(text, lang, source_url=None) if _has_excess_cross_language(text, lang) else text

    # Length guard: if even our deterministic-pool path produced a body that
    # is too short, return '' so the caller logs a clean skip rather than
    # passing on a degraded draft.
    #
    # Threshold is language-aware: CJK chars carry ~1.5x the info density of
    # Latin chars, so a 60-char ZH body has roughly the same content as a
    # 90-char EN body. Both clear the downstream `_is_low_quality_post`
    # 80-char floor once the source URL (~50-70 chars) is appended.
    _MIN_BODY_LEN = 60 if lang == 'zh' else 90
    if len(text.strip()) < _MIN_BODY_LEN:
        print(
            f'[build_move_fallback_text] body too short ({len(text.strip())} < {_MIN_BODY_LEN} chars for lang={lang}); returning empty',
            file=sys.stderr,
        )
        return ''

    if source_url:
        text += f"\n\n→ {source_url}"
    return text


def build_reply_text(theme: str, claim: dict[str, Any] | None = None) -> str:
    claim_family = str((claim or {}).get('claim_family') or '').strip()
    claim_text = str((claim or {}).get('claim_text') or '').strip()
    points = [str(p).strip() for p in ((claim or {}).get('supporting_points') or []) if str(p).strip()]

    if claim_family == 'x-superapp-open-graph':
        point = points[0] if points else '这已经不是单一 feature update。'
        return f'我更关注的是，这些变化是不是开始把 social、AI、crypto 收敛到同一条 operating path。像“{point}”这种信号，说明 X 想做的已经不只是社交产品迭代。'
    if claim_family == 'agent-protocol-vs-platform':
        return '关键不是给旧平台补一个 bot layer，而是把 social、identity、settlement 接成同一条 protocol 路径。不然 agent network 还是会被平台权限卡住。'
    if claim_family == 'community-as-ai-crypto-intersection':
        return '真正值得追的是 community coordination 这一层。没有这层，AI 和 Crypto 最后都容易停在一堆工具和短期情绪里。'

    if theme == 'desoc-agent':
        return '关键不是给旧平台补一个 bot layer，而是把 social、identity、settlement 接成同一条 protocol 路径。不然 agent network 还是会被平台权限卡住。'
    if theme == 'agent-infra':
        return '我更关心的不是单个 agent 更聪明，而是 intent、coordination、memory 能不能被长期复用。没有 protocol 层，这些能力很难沉淀。'
    if theme == 'token-coordination':
        if claim_text:
            return f'我更看重的是：{claim_text[:70]}。真正的难点不是自动化本身，而是有没有公开规则去给正反馈。'
        return '是的。真正的难点不是自动化本身，而是有没有公开规则去给正反馈。smart contract + token 在这里比平台积分自然得多。'
    return '我更看重的是，social signal、coordination 和 settlement 能不能形成闭环。没有这层，agent network 很容易停在 demo。'


def run_community_scan() -> dict[str, Any]:
    """Run the TagClaw community feed scanner and return results."""
    import subprocess as _sp
    scanner = BOOKMARKER_ROOT / 'scripts' / 'scan_tagclaw_community.py'
    try:
        proc = _sp.run(
            ['python3', str(scanner)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return json.loads(proc.stdout.strip())
    except Exception:
        pass
    return {}


def _extract_items_from_archives(memory: Path, max_items: int = 15) -> list[dict]:
    return extract_items_from_archives(memory, max_items=max_items)


def main() -> int:
    SH_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
    memory = BOOKMARKER_ROOT / 'memory'
    x_sync, x_items, x_sync_used_fallback = load_x_sync_with_fallback(memory)
    topic_extraction = read_json(memory / 'topic-extraction-latest.json') or {}
    topic_brief_payload = read_json(memory / 'topic-brief-payload.json') or {}
    heartbeat_state = read_json(MAIN_ROOT / 'memory' / 'heartbeat-state.json') or {}
    main_tas_social = read_json(MAIN_TAS_SOCIAL) or {}
    legacy_tas_social = read_json(MAIN_ROOT / 'memory' / 'tas-social-latest.json') or {}
    previous_tas_social_doc = read_json(RUNTIME / 'tas-social.json') or {}
    previous_autonomy_intent = read_json(RUNTIME / 'autonomy-intent.json') or {}
    previous_latest = read_json(RUNTIME / 'latest.json') or {}

    # Read Main guidance (if available) — overrides defaults when present
    main_guidance_doc = read_json(MAIN_ROOT / 'runtime' / 'main' / 'bookmarker-guidance.json') or {}
    main_guidance = main_guidance_doc.get('guidance') or {}
    guidance_mode = main_guidance_doc.get('experiment_mode', 'baseline')
    g_action_emphasis = main_guidance.get('action_emphasis', 'curate_heavy')

    reward_attribution = read_json(RUNTIME / 'reward-attribution.json') or {}

    # Run community scan — primary source for TAS_social Track B
    community_scan = read_json(RUNTIME / 'community-scan.json') or {}
    # If stale (>4h) or missing, re-run scanner inline
    _scan_ts = community_scan.get('scanned_at')
    _scan_age_ok = False
    if _scan_ts:
        try:
            from datetime import datetime as _dt, timezone as _tz
            _scanned = _dt.fromisoformat(_scan_ts.replace('Z', '+00:00'))
            _scan_age_ok = (datetime.now(timezone.utc) - _scanned.astimezone(timezone.utc)).total_seconds() < 14400
        except Exception:
            pass
    if not _scan_age_ok:
        _fresh = run_community_scan()
        if _fresh.get('status') == 'ok':
            community_scan = read_json(RUNTIME / 'community-scan.json') or {}

    # Use the most recent timestamp across all sources.
    # topic-brief-payload / topic-extraction are often fresher than x-sync (which may be stale).
    _candidates = [
        topic_brief_payload.get('timestamp'),
        topic_extraction.get('timestamp'),
        x_sync.get('fetched_at'),
    ]
    _parsed = [(parse_dt(ts), ts) for ts in _candidates if ts]
    _parsed = [(dt, ts) for dt, ts in _parsed if dt]
    fetched_at = max(_parsed, key=lambda x: x[0])[1] if _parsed else now_iso()
    generated_at = fetched_at if parse_dt(fetched_at) else now_iso()
    sync_status = normalize_status(x_sync.get('status'), default='stale')

    # Source health
    source_health = {'bird': 'unknown', 'browser_relay': 'unknown', 'xurl': 'unknown', 'mismatch': None}
    for item in x_sync.get('attempts') or []:
        source = item.get('source')
        ok = item.get('ok')
        if source == 'chrome-relay':
            source = 'browser_relay'
        if source in source_health:
            source_health[source] = 'ok' if ok else 'blocked'
    selected = x_sync.get('source')
    if selected == 'chrome-relay':
        selected = 'browser_relay'
    if selected in source_health and sync_status == 'ok':
        source_health[selected] = 'ok'
        for other in ('bird', 'browser_relay', 'xurl'):
            if other != selected and source_health[other] == 'unknown':
                source_health[other] = 'standby'
    source_health['mismatch'] = bool((topic_brief_payload or topic_extraction) and sync_status != 'ok')

    # TAS social (from main runtime handoff)
    tas_social_source = main_tas_social if main_tas_social else legacy_tas_social
    tas_status = normalize_status(tas_social_source.get('status'), default='stale') if tas_social_source else 'stale'
    tas_value = tas_social_source.get('value') if tas_social_source else None

    # Topic brief
    topics = topic_brief_payload.get('topics') if isinstance(topic_brief_payload.get('topics'), list) else []
    keywords = [t.get('name') for t in topics if isinstance(t, dict)]
    recommendations = ((topic_brief_payload.get('recommendations') or {}).get('for_main_agent') or [])
    candidates = topic_brief_payload.get('candidates') if isinstance(topic_brief_payload.get('candidates'), list) else []
    urgency = topic_brief_payload.get('urgency') or topic_extraction.get('urgency') or 'low'
    high_signal_count = (topic_brief_payload.get('summary', {}).get('high_signal_count') or topic_extraction.get('high_signal_content') or 0)

    # Social drafts
    x_items = x_sync.get('data') if isinstance(x_sync.get('data'), list) else x_items

    current_publication_memory = read_json(PUBLICATION_MEMORY_PATH) or {}
    _pub_recent_claims = current_publication_memory.get('recent_claims') if isinstance(current_publication_memory.get('recent_claims'), list) else []
    _claim_family_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_claim_families_24h: set[str] = set()
    recent_claim_family_last_at: dict[str, datetime] = {}
    recent_anchor_refs_24h: set[str] = set()
    recent_anchor_last_at: dict[str, datetime] = {}
    recent_source_tweet_ids_24h: set[str] = set()
    for _rc in _pub_recent_claims:
        if not isinstance(_rc, dict):
            continue
        _fam = str(_rc.get('claim_family') or '').strip()
        _anchor = str(_rc.get('anchor_source') or '').strip()
        _dt = parse_dt(_rc.get('published_at') or '')
        if _dt and _dt.astimezone(timezone.utc) < _claim_family_cutoff:
            continue
        if _fam:
            recent_claim_families_24h.add(_fam)
            if _dt and (_fam not in recent_claim_family_last_at or _dt > recent_claim_family_last_at[_fam]):
                recent_claim_family_last_at[_fam] = _dt
        if _anchor:
            recent_anchor_refs_24h.add(_anchor)
            if _dt and (_anchor not in recent_anchor_last_at or _dt > recent_anchor_last_at[_anchor]):
                recent_anchor_last_at[_anchor] = _dt
            if _anchor.startswith('x:'):
                recent_source_tweet_ids_24h.add(_anchor.split(':', 1)[1])

    _wiki_brief_doc = read_json(WIKI_EXECUTION_BRIEF)
    trader_latest_doc = read_json(MAIN_ROOT / 'runtime' / 'trader' / 'latest.json') or {}
    tas_trade_doc = read_json(MAIN_ROOT / 'runtime' / 'trader' / 'tas-trade.json') or {}
    daily_theme_pack = build_daily_theme_pack(
        topic_brief_doc=topic_brief_payload,
        wiki_brief_doc=_wiki_brief_doc,
        trader_latest_doc=trader_latest_doc,
        tas_trade_doc=tas_trade_doc,
    )
    atomic_write_json(RUNTIME / 'daily-theme-pack.json', daily_theme_pack)

    wiki_delta_doc = generate_wiki_delta(
        bookmaker_root=BOOKMARKER_ROOT,
        main_root=MAIN_ROOT,
        runtime=RUNTIME,
        publication_memory=current_publication_memory,
        x_sync_doc=x_sync,
    )
    atomic_write_json(RUNTIME / 'wiki-delta.json', wiki_delta_doc)
    publishable_claims_doc = generate_publishable_claims(
        runtime=RUNTIME,
        bookmaker_root=BOOKMARKER_ROOT,
        wiki_delta=wiki_delta_doc,
        publication_memory=current_publication_memory,
        x_sync_doc=x_sync,
        topic_brief_doc=topic_brief_payload,
    )
    atomic_write_json(RUNTIME / 'publishable-claims.json', publishable_claims_doc)
    social_move_plan_doc = generate_social_move_plan(
        runtime=RUNTIME,
        publishable_claims_doc=publishable_claims_doc,
    )
    atomic_write_json(RUNTIME / 'social-move-plan.json', social_move_plan_doc)
    publishable_claims = [item for item in (publishable_claims_doc.get('claims') or []) if isinstance(item, dict)]
    social_moves = [item for item in (social_move_plan_doc.get('moves') or []) if isinstance(item, dict)]

    recent_post_dedupe = load_recent_post_dedupe(hours=48)
    executed_keys = set(recent_post_dedupe.get('target_keys') or set())
    recent_source_candidate_ids = set(recent_post_dedupe.get('source_candidate_ids') or set())
    recent_source_tweet_ids = set(recent_post_dedupe.get('source_tweet_ids') or set())
    recent_content_hashes = set(recent_post_dedupe.get('content_hashes') or set())
    recent_content_hashes_excluding_source = set(recent_post_dedupe.get('content_hashes_excluding_source') or set())
    # 7-day hard-block window for source tweet IDs (added 2026-05-15 after
    # hI0Jw76Koe 6x-repeat incident). This set is consulted alongside the 48h
    # set but is NOT subject to `_allow_recent_source_override` — if a tweet
    # ID is in here, the claim is rejected unconditionally.
    _recent_post_dedupe_7d = load_recent_post_dedupe(hours=168)
    recent_source_tweet_ids_7d = set(_recent_post_dedupe_7d.get('source_tweet_ids') or set())
    recent_claim_families = recent_claim_families_24h
    recent_text_bodies = list(recent_post_dedupe.get('text_bodies') or [])
    x_item_by_ref = {
        f"x:{item.get('id')}": item
        for item in x_items
        if isinstance(item, dict) and item.get('id')
    }
    move_by_claim_id = {
        str(item.get('claim_id') or ''): item
        for item in social_moves
        if isinstance(item, dict) and str(item.get('claim_id') or '')
    }
    top_claim = None
    claims_first_attempted = bool(publishable_claims)
    claims_considered = 0
    claims_skipped = 0
    claims_selected = 0
    claims_skip_reasons: dict[str, int] = {}
    claims_first_failure_reason = ''
    claims_audit_entries: list[dict[str, Any]] = []
    source_first_skip_reasons: list[str] = []
    claims_source_exhaustion_count = 0
    all_claims_blocked_by_source_exhaustion = False
    source_first_llm_failed = False
    source_first_llm_failure_reason = ''
    source_first_used_relaxed_7d = False
    source_first_llm_diagnostics: dict[str, Any] = {}
    fallback_template_used = False
    degradation_path: list[str] = []

    # F2 (permanent fix): compute own-author username early from community_scan so we can
    # exclude own posts from the x_items candidate pool — prevents "You cannot curate your
    # own post" TagClaw errors that arise when clawdbot's own published posts re-enter the
    # content-candidates feed and get routed into curate drafts.
    _own_username_early = str(community_scan.get('tracked_username') or 'clawdbot').strip().lower()

    if x_items:
        _filtered_pool = [
            item for item in x_items
            if not (
                item.get('id') and (
                    f"x:{item['id']}" in executed_keys
                    or f"x:{item['id']}" in recent_source_candidate_ids
                    or str(item['id']) in recent_source_tweet_ids
                )
            )
            # F2: skip own-author posts — curating your own post is rejected by TagClaw API
            and str((item.get('author') or {}).get('username') or '').strip().lower() != _own_username_early
        ]
        candidate_pool_size = len(_filtered_pool)
        picked = pick_best_candidate_llm(_filtered_pool, set(), keywords) if _filtered_pool else None
        top_item = picked if picked is not None else (_filtered_pool[0] if _filtered_pool else None)
        if not _filtered_pool:
            picker_source = 'no-candidates'
        elif picked is not None:
            picker_source = 'llm'
        else:
            picker_source = 'fallback-first'
    else:
        top_item = None
        candidate_pool_size = 0
        picker_source = 'no-candidates'

    enriched_candidates, recommended_action_mix = build_enriched_candidates(
        topic_candidates=candidates,
        x_items=_filtered_pool if x_items else [],
        keywords=keywords,
        executed_keys=executed_keys,
        guidance_mode=guidance_mode,
        action_emphasis=g_action_emphasis,
    )

    # Apply twin-recognition topic_weights as multipliers to candidate ranking
    _recognition_weights = _load_recognition_weights()
    _recent_directives = _load_recent_topic_directives(5)
    if _recognition_weights:
        enriched_candidates = apply_recognition_weights_to_candidates(
            enriched_candidates, keywords, _recognition_weights, _recent_directives,
        )
        # Rebuild recommended_action_mix from updated top-5
        recommended_action_mix = {}
        for item in enriched_candidates[:5]:
            action = str(item.get('recommended_action') or 'unknown')
            recommended_action_mix[action] = recommended_action_mix.get(action, 0) + 1

    # Wiki trending ticks for choose_tick (T2: wiki-first)
    _wiki_trending_ticks: list[str] = []
    _ticks_trending_doc = read_json(WIKI_PLATFORM_RAW / 'ticks_trending.json')
    if _ticks_trending_doc:
        _ticks_data = (_ticks_trending_doc.get('data') or {}).get('ticks') or []
        _wiki_trending_ticks = [t['tick'] for t in _ticks_data[:5] if isinstance(t, dict) and t.get('tick')]

    # Tick distribution enforcement (issue-3): compute BUIDL % from last 24h posts.
    # choose_tick() uses this to bias towards BUIDL when its share < 50%.
    _buidl_pct_24h: float = _compute_buidl_pct_24h(SOCIAL_HISTORY_PATH)
    _in_run_tick_counts: dict[str, int] = {}  # tracks ticks assigned in this run

    # Wiki execution brief top theme for candidate boost (T3: wiki-first)
    _wiki_top_theme_name: str = ''
    if _wiki_brief_doc:
        _top_themes = _wiki_brief_doc.get('top_themes') or []
        if _top_themes and isinstance(_top_themes[0], dict):
            _wiki_top_theme_name = _top_themes[0].get('name') or ''

    # Apply wiki brief theme boost to candidates (T3)
    if _wiki_top_theme_name and enriched_candidates:
        enriched_candidates = apply_wiki_brief_theme_boost(enriched_candidates, _wiki_top_theme_name)
        # Rebuild recommended_action_mix from updated top-5
        recommended_action_mix = {}
        for item in enriched_candidates[:5]:
            action = str(item.get('recommended_action') or 'unknown')
            recommended_action_mix[action] = recommended_action_mix.get(action, 0) + 1

    top_candidate = enriched_candidates[0] if enriched_candidates else None
    theme = str(top_claim.get('theme') or '') if top_claim else ''
    if not theme:
        theme = infer_theme(keywords, (top_item or {}).get('text', '')) if top_item else (top_candidate.get('theme') if top_candidate else 'general-builder')
    recent_targets = parse_recent_targets(heartbeat_state)
    summary_text = str(daily_theme_pack.get('thesis') or '').strip()
    if not summary_text:
        summary_text = recommendations[0] if recommendations else topic_brief_payload.get('summary') if isinstance(topic_brief_payload.get('summary'), str) else ''
    # --- Anti-repeat: load recent text actions and extract patterns ---
    _recent_posts = list(dict.fromkeys(recent_text_bodies + load_recent_posts(10)))[:20]
    _anti_repeat = extract_anti_repeat_context(_recent_posts)
    _writing_mode = choose_writing_mode(_recent_posts)

    drafts = []
    skipped_by_dedup = 0

    def _record_claim_skip(reason: str) -> None:
        nonlocal claims_skipped
        claims_skipped += 1
        claims_skip_reasons[reason] = claims_skip_reasons.get(reason, 0) + 1

    def _allow_recent_family_override(claim: dict[str, Any], claim_family: str) -> bool:
        last_dt = recent_claim_family_last_at.get(claim_family)
        if not last_dt:
            return False
        age_hours = (datetime.now(timezone.utc) - last_dt.astimezone(timezone.utc)).total_seconds() / 3600.0
        score = float(claim.get('publication_score') or 0.0)
        claim_type = str(claim.get('claim_type') or '').strip()
        if claim_family == 'x-superapp-open-graph' and score >= 0.88 and claim_type in {'new_connection', 'validated'} and age_hours >= 8:
            return True
        if claim_family == 'agent-protocol-vs-platform' and score >= 0.80 and claim_type == 'validated' and age_hours >= 12:
            return True
        return False

    def _allow_recent_source_override(claim: dict[str, Any], anchor_ref: str) -> bool:
        # Tightened 2026-05-15 after hI0Jw76Koe 6x-repeat incident. Previous
        # thresholds (score >= 0.70, age >= 16h) let the same source tweet
        # produce multiple posts within a single day. New contract: a source
        # may only be reused if BOTH the publication score is very high AND
        # at least 48h have elapsed since the last post against it.
        last_dt = recent_anchor_last_at.get(anchor_ref)
        if not last_dt:
            return False
        age_hours = (datetime.now(timezone.utc) - last_dt.astimezone(timezone.utc)).total_seconds() / 3600.0
        score = float(claim.get('publication_score') or 0.0)
        claim_type = str(claim.get('claim_type') or '').strip()
        if score >= 0.90 and claim_type in {'new_connection', 'validated'} and age_hours >= 48:
            return True
        return False

    def _try_build_post_from_claim(claim: dict[str, Any]) -> bool:
        nonlocal top_claim, top_item, theme, picker_source, claims_considered, claims_selected, skipped_by_dedup, claims_source_exhaustion_count
        claims_considered += 1
        claim_family = str(claim.get('claim_family') or '').strip()
        claim_id = str(claim.get('claim_id') or claim_family).strip()
        move_plan = move_by_claim_id.get(claim_id) or {}
        audit_entry = {
            'claim_id': claim_id,
            'claim_family': claim_family,
            'theme': claim.get('theme'),
            'publication_score': claim.get('publication_score'),
            'recommended_actions': claim.get('recommended_actions') or [],
            'best_anchor_source': claim.get('best_anchor_source'),
            'supporting_sources': _claim_anchor_refs(claim),
            'score_breakdown': claim.get('score_breakdown') or {},
            'move_type': move_plan.get('move_type'),
            'voice_mode': move_plan.get('voice_mode'),
            'sub_angle': move_plan.get('sub_angle'),
            'result': 'pending',
            'reason': '',
        }
        anchor_ref = ''
        claim_item = None
        tweet_id = None
        source_url = None
        candidate_key = None
        source_candidate_id = None
        claim_theme = str(claim.get('theme') or '').strip() or infer_theme(keywords, str(claim.get('source_excerpt') or ''))
        _grouped_points = claim.get('supporting_point_groups') or []
        if _grouped_points:
            _ordered = []
            for _kind in ['source_fact', 'product_signal', 'structural_inference']:
                _ordered.extend([str(p.get('text') or '').strip() for p in _grouped_points if str(p.get('type') or '') == _kind and str(p.get('text') or '').strip()])
            _support_points = _ordered[:4]
        else:
            _support_points = [str(p).strip() for p in (claim.get('supporting_points') or [])[:4] if str(p).strip()]
        support_lines = [f'- {p}' for p in _support_points]
        claim_text = str(claim.get('claim_text') or '').strip()
        claim_is_multi_source = claim_family == 'multi-source-thesis'
        if claim_is_multi_source:
            source_text = '\n'.join([
                claim_text,
                *support_lines,
            ]).strip()
        else:
            source_text = '\n'.join([
                claim_text,
                *support_lines,
                str(claim.get('source_excerpt') or '').strip(),
            ]).strip()
        quality_source_text = None if claim_is_multi_source else source_text

        if not source_text:
            audit_entry['result'] = 'skipped'
            audit_entry['reason'] = 'empty_source_text'
            claims_audit_entries.append(audit_entry)
            _record_claim_skip('empty_source_text')
            return False
        anchor_attempts: list[dict[str, Any]] = []
        all_anchor_refs = _claim_anchor_refs(claim)
        all_anchors_hit_7d = bool(all_anchor_refs)
        for candidate_anchor_ref in all_anchor_refs:
            if candidate_anchor_ref.startswith('multi-source:'):
                anchor_attempts.append({
                    'anchor_ref': candidate_anchor_ref,
                    'tweet_id': None,
                    'reason': 'selected',
                })
                claim_item = None
                anchor_ref = candidate_anchor_ref
                tweet_id = None
                candidate_key = candidate_anchor_ref
                source_candidate_id = claim_id or candidate_anchor_ref
                source_url = None
                break
            candidate_item = x_item_by_ref.get(candidate_anchor_ref)
            if candidate_item is None and candidate_anchor_ref.startswith('x:'):
                anchor_id = candidate_anchor_ref.split(':', 1)[1]
                candidate_item = next((item for item in _filtered_pool if str(item.get('id')) == anchor_id), None)
            candidate_tweet_id = str((candidate_item or {}).get('id') or '').strip() or None
            candidate_key_ref = f'x:{candidate_tweet_id}' if candidate_tweet_id else None
            attempt_reason = ''
            if candidate_tweet_id and candidate_tweet_id in recent_source_tweet_ids_7d:
                attempt_reason = 'source_already_used_7d_hard_block'
            else:
                all_anchors_hit_7d = False
            if not attempt_reason and (
                (candidate_key_ref and candidate_key_ref in executed_keys)
                or (candidate_anchor_ref and candidate_anchor_ref in recent_anchor_refs_24h)
                or (candidate_tweet_id and candidate_tweet_id in recent_source_tweet_ids_24h)
            ):
                if _allow_recent_source_override(claim, candidate_anchor_ref):
                    attempt_reason = 'source_recent_override'
                else:
                    attempt_reason = 'source_already_used'
            anchor_attempts.append({
                'anchor_ref': candidate_anchor_ref,
                'tweet_id': candidate_tweet_id,
                'reason': attempt_reason or 'selected',
            })
            if attempt_reason and attempt_reason != 'source_recent_override':
                continue
            claim_item = candidate_item
            anchor_ref = candidate_anchor_ref
            tweet_id = candidate_tweet_id
            candidate_key = candidate_key_ref
            source_candidate_id = f"{claim_id}@{anchor_ref}" if claim_id and anchor_ref else (claim_id or candidate_key)
            author = ((claim_item or {}).get('author') or {}).get('username') if claim_item else '0xNought'
            author = author or '0xNought'
            source_url = f'https://x.com/{author}/status/{tweet_id}' if tweet_id else None
            break
        audit_entry['anchor_attempts'] = anchor_attempts
        audit_entry['selected_anchor_source'] = anchor_ref or None
        if not anchor_ref:
            skipped_by_dedup += 1
            audit_entry['result'] = 'skipped'
            if all_anchors_hit_7d:
                claims_source_exhaustion_count += 1
                audit_entry['reason'] = 'source_already_used_7d_hard_block'
                _record_claim_skip('source_already_used_7d_hard_block')
            elif anchor_attempts and all(a.get('reason') == 'source_already_used' for a in anchor_attempts):
                audit_entry['reason'] = 'source_already_used'
                _record_claim_skip('source_already_used')
            else:
                audit_entry['reason'] = 'no_viable_anchor_source'
                _record_claim_skip('no_viable_anchor_source')
            claims_audit_entries.append(audit_entry)
            return False
        if claim_family and claim_family in recent_claim_families:
            if _allow_recent_family_override(claim, claim_family):
                audit_entry['reason'] = 'claim_family_recent_override'
            else:
                skipped_by_dedup += 1
                audit_entry['result'] = 'skipped'
                audit_entry['reason'] = 'claim_family_recent'
                claims_audit_entries.append(audit_entry)
                _record_claim_skip('claim_family_recent')
                return False

        style_guide = load_wiki_style_guide(claim_theme)
        _allow_product = should_include_product_mention(claim_theme, source_text, _recent_posts)
        _claim_llm_diag: dict[str, Any] = {}
        text = build_argument_llm(
            claim_theme, source_text, style_guide,
            writing_mode=_writing_mode,
            anti_repeat=_anti_repeat,
            allow_product_mention=_allow_product,
            move_plan=move_plan,
            diagnostics=_claim_llm_diag,
        )
        _draft_ok = True
        if text:
            _draft_ok, _ = verify_draft_novelty(text, _anti_repeat)
        if text and not _draft_ok:
            _stronger_anti = dict(_anti_repeat)
            _stronger_anti['recent_full_texts'] = [text[:300]] + _stronger_anti.get('recent_full_texts', [])
            text = build_argument_llm(
                claim_theme, source_text, style_guide,
                writing_mode=_writing_mode,
                anti_repeat=_stronger_anti,
                allow_product_mention=False,
                diagnostics=_claim_llm_diag,
            )
        audit_entry['llm_diagnostics'] = _claim_llm_diag
        if not text:
            if claim_is_multi_source:
                text = build_multi_source_thesis_fallback(
                    daily_theme_pack=daily_theme_pack,
                ) or {}
                text = str(text.get('text') or '')
            else:
                text = build_move_fallback_text(
                    claim_text,
                    [str(p).strip() for p in (claim.get('supporting_points') or [])[:3] if str(p).strip()],
                    source_url,
                    move_plan,
                )
        text = _enforce_monolingual_draft(text, source_url, _dominant_language(claim_text or source_text))

        content_hash = compute_post_content_hash(text)
        content_hash_excluding_source = compute_post_content_hash_excluding_source(text)
        _content_duplicate = (
            (content_hash and content_hash in recent_content_hashes)
            or (
                content_hash_excluding_source
                and content_hash_excluding_source in recent_content_hashes_excluding_source
            )
        )
        if _content_duplicate and float(claim.get('publication_score') or 0.0) >= 0.85:
            _stronger_anti = dict(_anti_repeat)
            _stronger_anti['recent_full_texts'] = [text[:300]] + _stronger_anti.get('recent_full_texts', [])
            alt_source_text = source_text + '\n\nUse a sharper angle than recent posts. Emphasize what is structurally new in this cycle, not the generic thesis.'
            alt_text = build_argument_llm(
                claim_theme, alt_source_text, style_guide,
                writing_mode=_writing_mode,
                anti_repeat=_stronger_anti,
                allow_product_mention=_allow_product,
                move_plan=move_plan,
            )
            if alt_text:
                text = alt_text
                content_hash = compute_post_content_hash(text)
                content_hash_excluding_source = compute_post_content_hash_excluding_source(text)
                _content_duplicate = (
                    (content_hash and content_hash in recent_content_hashes)
                    or (
                        content_hash_excluding_source
                        and content_hash_excluding_source in recent_content_hashes_excluding_source
                    )
                )
            if _content_duplicate:
                _points = [str(p).strip() for p in (claim.get('supporting_points') or [])[:3] if str(p).strip()]
                while len(_points) < 3:
                    _points.append('This cycle adds a structurally different signal from the previous round.')
                alt_template = (
                    f"{claim_text}\n\n"
                    f"What feels different in this cycle is not just feature churn:\n"
                    f"- {_points[0]}\n"
                    f"- {_points[1]}\n"
                    f"- {_points[2]}\n\n"
                    f"The important shift is stack convergence and coordination depth, not isolated product drops."
                )
                if source_url:
                    alt_template += f"\n\n→ {source_url}"
                text = alt_template
                content_hash = compute_post_content_hash(text)
                content_hash_excluding_source = compute_post_content_hash_excluding_source(text)
                _content_duplicate = (
                    (content_hash and content_hash in recent_content_hashes)
                    or (
                        content_hash_excluding_source
                        and content_hash_excluding_source in recent_content_hashes_excluding_source
                    )
                )
        if _content_duplicate:
            skipped_by_dedup += 1
            audit_entry['result'] = 'skipped'
            audit_entry['reason'] = 'content_duplicate'
            claims_audit_entries.append(audit_entry)
            _record_claim_skip('content_duplicate')
            return False

        # P3.2 (2026-05-17): final quality guard for claims-first path.
        # Even after LLM retries (with quality gate) + move-fallback +
        # alt-template + monolingual enforcement, run one last check before
        # appending. Belt-and-suspenders so no banned-template / too-short /
        # verbatim draft can ever reach the main runtime.
        _final_bad, _final_reason = _is_low_quality_post(text, source_text=quality_source_text)
        if _final_bad:
            skipped_by_dedup += 1
            audit_entry['result'] = 'skipped'
            audit_entry['reason'] = f'final_quality_gate:{_final_reason}'
            claims_audit_entries.append(audit_entry)
            _record_claim_skip('final_quality_gate')
            print(
                f'[claims-first] skipping claim {claim_id} — final quality gate rejected: {_final_reason}',
                file=sys.stderr,
            )
            return False

        _chosen_tick = choose_tick(
            keywords,
            _wiki_trending_ticks,
            buidl_pct_24h=_buidl_pct_24h,
            text=text,
            theme=claim_theme,
            social_history_path=SOCIAL_HISTORY_PATH,
            in_run_tick_counts=_in_run_tick_counts,
        )
        _in_run_tick_counts[_chosen_tick] = _in_run_tick_counts.get(_chosen_tick, 0) + 1
        drafts.append({
            'id': 'draft-1', 'type': 'post', 'tick': _chosen_tick, 'text': text,
            'priority': 9, 'theme': claim_theme,
            'target_key': candidate_key,
            'source_candidate_id': source_candidate_id,
            'claim_family': claim_family,
            'claim_id': claim_id,
            'expected_tas_social_uplift': float(claim.get('publication_score') or 0.0),
            'recommended_action': (claim.get('recommended_actions') or ['post'])[0],
            'source_tweet_id': tweet_id, 'source_url': source_url,
            'source_excerpt': shorten(source_text, 120),
            'content_hash': content_hash,
            'content_hash_excluding_source': content_hash_excluding_source,
            '_planner_mode': 'claims-first',
            '_writing_mode': _writing_mode.get('id', ''),
            '_product_mention_allowed': _allow_product,
            '_draft_rewritten': not _draft_ok,
            'rewrite_gate_passed': True,
        })
        audit_entry['result'] = 'selected'
        audit_entry['reason'] = 'top_claim_selected'
        claims_audit_entries.append(audit_entry)
        top_claim = claim
        top_item = claim_item
        theme = claim_theme
        picker_source = 'claims-first'
        claims_selected += 1
        return True

    if publishable_claims:
        for _claim in publishable_claims:
            if _try_build_post_from_claim(_claim):
                break
        if not drafts:
            claims_first_failure_reason = 'all_claims_rejected'
            all_claims_blocked_by_source_exhaustion = claims_source_exhaustion_count == claims_considered and claims_considered > 0
            if all_claims_blocked_by_source_exhaustion:
                print(
                    '[claims-first] all claims were blocked by source_already_used_7d_hard_block; enabling degraded source-first fallback with 24h/48h dedupe still intact',
                    file=sys.stderr,
                )
    else:
        claims_first_failure_reason = 'no_publishable_claims'

    if not drafts and top_item:
        author = ((top_item or {}).get('author') or {}).get('username') if top_item else '0xNought'
        author = author or '0xNought'
        tweet_id = (top_item or {}).get('id') if top_item else None
        anchor_source_text = (top_item or {}).get('text') or ''
        source_url = f'https://x.com/{author}/status/{tweet_id}' if tweet_id else None
        candidate_key = f'x:{tweet_id}' if tweet_id else None
        source_candidate_id = top_candidate.get('candidate_id') if top_candidate else candidate_key
        claim_family = infer_claim_family(theme, anchor_source_text, keywords)
        source_text = anchor_source_text
        source_first_7d_blocked = bool(tweet_id and str(tweet_id) in recent_source_tweet_ids_7d)
        if source_first_7d_blocked:
            multi_source_draft = build_multi_source_thesis_fallback(
                daily_theme_pack=daily_theme_pack,
            )
            if multi_source_draft:
                _multi_text = str(multi_source_draft.get('text') or '')
                _multi_bad, _multi_reason = _is_low_quality_post(_multi_text, source_text=None)
                if _multi_bad:
                    skipped_by_dedup += 1
                    source_first_skip_reasons.append(f'multi_source_quality_gate:{_multi_reason}')
                else:
                    _chosen_tick_ms = choose_tick(
                        keywords,
                        _wiki_trending_ticks,
                        buidl_pct_24h=_buidl_pct_24h,
                        text=_multi_text,
                        theme=str(multi_source_draft.get('theme') or theme or 'agent-infra'),
                        social_history_path=SOCIAL_HISTORY_PATH,
                        in_run_tick_counts=_in_run_tick_counts,
                    )
                    _in_run_tick_counts[_chosen_tick_ms] = _in_run_tick_counts.get(_chosen_tick_ms, 0) + 1
                    multi_source_draft['tick'] = _chosen_tick_ms
                    multi_source_draft['content_hash'] = compute_post_content_hash(_multi_text)
                    multi_source_draft['content_hash_excluding_source'] = compute_post_content_hash_excluding_source(_multi_text)
                    drafts.append(multi_source_draft)
                    source_first_skip_reasons.append('source_7d_blocked_routed_to_multi_source')
            else:
                skipped_by_dedup += 1
                source_first_skip_reasons.append('source_7d_blocked_no_multi_source_fallback')
        elif (
            (candidate_key and candidate_key in executed_keys)
            or (source_candidate_id and source_candidate_id in recent_source_candidate_ids)
            or (tweet_id and str(tweet_id) in recent_source_tweet_ids)
        ):
            skipped_by_dedup += 1
            source_first_skip_reasons.append('source_already_used')
        elif claim_family in recent_claim_families:
            skipped_by_dedup += 1
            source_first_skip_reasons.append('claim_family_recent')
        else:
            style_guide = load_wiki_style_guide(theme)
            _allow_product = should_include_product_mention(theme, source_text, _recent_posts)
            source_first_used_multi_source = False
            source_first_claim_id = claim_family
            text = build_argument_llm(
                theme, source_text, style_guide,
                writing_mode=_writing_mode,
                anti_repeat=_anti_repeat,
                allow_product_mention=_allow_product,
                quality_mode='source-first-degraded',
                allow_last_quality_failure=True,
                diagnostics=source_first_llm_diagnostics,
            )
            _draft_ok = True
            if text:
                _draft_ok, _ = verify_draft_novelty(text, _anti_repeat)
            if text and not _draft_ok:
                _stronger_anti = dict(_anti_repeat)
                _stronger_anti['recent_full_texts'] = [text[:300]] + _stronger_anti.get('recent_full_texts', [])
                text = build_argument_llm(
                    theme, source_text, style_guide,
                    writing_mode=_writing_mode,
                    anti_repeat=_stronger_anti,
                    allow_product_mention=False,
                    quality_mode='source-first-degraded',
                    allow_last_quality_failure=True,
                    diagnostics=source_first_llm_diagnostics,
                )
            if not text:
                source_first_llm_failed = True
                source_first_llm_failure_reason = str(source_first_llm_diagnostics.get('last_reason') or 'empty_after_retries')
                print(
                    f'[source-first] LLM failed in degraded mode; falling back to template synthesis: {source_first_llm_failure_reason}',
                    file=sys.stderr,
                )
                _multi_source = build_multi_source_thesis_fallback(
                    daily_theme_pack=daily_theme_pack,
                )
                if _multi_source:
                    text = str(_multi_source.get('text') or '')
                    source_url = None
                    source_text = ''
                    claim_family = str(_multi_source.get('claim_family') or 'multi-source-thesis')
                    source_first_claim_id = str(_multi_source.get('claim_id') or claim_family)
                    source_candidate_id = str(_multi_source.get('source_candidate_id') or source_candidate_id or '')
                    candidate_key = str(_multi_source.get('target_key') or candidate_key or '')
                    theme = str(_multi_source.get('theme') or theme or 'agent-infra')
                    source_first_used_multi_source = True
                else:
                    # P3.1b (2026-05-17): retired build_argument's hardcoded
                    # templates. Route through build_move_fallback_text instead,
                    # which has opener/closing pool rotation and structural
                    # variants. Synthesize a minimal claim_text from summary_text
                    # (or the source's first sentence) so the framing is anchored.
                    _claim_seed = (summary_text or '').strip()
                    if not _claim_seed and source_text:
                        _first_sentence = re.split(r'[。\.！!？?\n]', source_text, maxsplit=1)[0].strip()
                        _claim_seed = _first_sentence[:120]
                    text = build_move_fallback_text(
                        claim_text=_claim_seed,
                        supporting_points=[],  # pool fillers will fill in
                        source_url=source_url,
                        move_plan={'move_type': 'default'},
                    )
            text = _enforce_monolingual_draft(text, source_url, _dominant_language(source_text))

            # P3.2 (2026-05-17): final quality guard before this draft can
            # leave the bookmarker. Even after retries + monolingual enforce,
            # if the output is empty, too short, refusal-marked, or contains
            # any banned template phrase, skip it entirely rather than ship.
            _quality_source_text = None if source_first_used_multi_source else source_text
            _quality_bad, _quality_reason = _is_low_quality_post(
                text,
                source_text=_quality_source_text,
                mode='source-first-degraded',
            )
            if _quality_bad:
                print(
                    f'[source-first] skipping draft — final quality gate rejected: {_quality_reason}',
                    file=sys.stderr,
                )
                skipped_by_dedup += 1
                source_first_skip_reasons.append(f'final_quality_gate:{_quality_reason}')
                source_first_llm_failed = True
                source_first_llm_failure_reason = _quality_reason
                # Fall through to the rest of the loop without appending.
                # Reuse skipped_by_dedup counter so the downstream audit shows
                # the skip in aggregate; specific reason is in stderr only.
                continue_to_next = True
            else:
                continue_to_next = False

            if continue_to_next:
                pass
            else:
                content_hash = compute_post_content_hash(text)
                content_hash_excluding_source = compute_post_content_hash_excluding_source(text)
                if (
                    (content_hash and content_hash in recent_content_hashes)
                    or (
                        content_hash_excluding_source
                        and content_hash_excluding_source in recent_content_hashes_excluding_source
                    )
                ):
                    skipped_by_dedup += 1
                    source_first_skip_reasons.append('content_duplicate')
                else:
                    _chosen_tick_sf = choose_tick(
                        keywords,
                        _wiki_trending_ticks,
                        buidl_pct_24h=_buidl_pct_24h,
                        text=text,
                        theme=theme,
                        social_history_path=SOCIAL_HISTORY_PATH,
                        in_run_tick_counts=_in_run_tick_counts,
                    )
                    _in_run_tick_counts[_chosen_tick_sf] = _in_run_tick_counts.get(_chosen_tick_sf, 0) + 1
                    drafts.append({
                        'id': 'draft-1', 'type': 'post', 'tick': _chosen_tick_sf, 'text': text,
                        'priority': 9, 'theme': theme,
                        'target_key': candidate_key,
                        'source_candidate_id': source_candidate_id,
                        'claim_family': claim_family,
                        'claim_id': source_first_claim_id,
                        'expected_tas_social_uplift': top_candidate.get('expected_tas_social_uplift') if top_candidate else None,
                        'recommended_action': top_candidate.get('recommended_action') if top_candidate else 'post',
                        'source_tweet_id': None if source_first_used_multi_source else tweet_id,
                        'source_url': None if source_first_used_multi_source else source_url,
                        'source_excerpt': (
                            str(_multi_source.get('source_excerpt') or '')
                            if source_first_used_multi_source and _multi_source
                            else shorten(source_text, 120)
                        ),
                        'content_hash': content_hash,
                        'content_hash_excluding_source': content_hash_excluding_source,
                        '_planner_mode': 'multi-source-fallback' if source_first_used_multi_source else 'source-first',
                        '_writing_mode': _writing_mode.get('id', ''),
                        '_product_mention_allowed': _allow_product,
                        '_draft_rewritten': not _draft_ok,
                        'rewrite_gate_passed': True,
                    })

    if recent_targets:
        t = recent_targets[0]
        if t.get('tweetId'):
            candidate_key = f"tagclaw:{t['tweetId']}"
            if candidate_key in executed_keys:
                skipped_by_dedup += 1
            else:
                reply_text = build_reply_text(theme, top_claim)
                reply_claim_family = infer_claim_family(theme, reply_text, keywords)
                reply_conflict, reply_conflict_reason = reply_conflicts_recent_posts(reply_text, recent_text_bodies)
                if reply_claim_family in recent_claim_families:
                    skipped_by_dedup += 1
                elif reply_conflict:
                    skipped_by_dedup += 1
                else:
                    drafts.append({
                        'id': 'draft-2', 'type': 'reply', 'tweetId': t['tweetId'],
                        'text': reply_text, 'priority': 7,
                        'target_key': candidate_key, 'target_username': t.get('username'),
                        'source_candidate_id': (str(top_claim.get('claim_id') or top_claim.get('claim_family') or '').strip() if top_claim else (top_candidate.get('candidate_id') if top_candidate else candidate_key)),
                        'claim_family': reply_claim_family,
                        'claim_id': (str(top_claim.get('claim_id') or top_claim.get('claim_family') or '').strip() if top_claim else reply_claim_family),
                        'expected_tas_social_uplift': (float(top_claim.get('publication_score') or 0.0) if top_claim else (top_candidate.get('expected_tas_social_uplift') if top_candidate else None)),
                        'recommended_action': ((top_claim.get('recommended_actions') or ['reply'])[0] if top_claim else 'reply'),
                        'content_hash': compute_post_content_hash(reply_text),
                        'content_hash_excluding_source': compute_post_content_hash_excluding_source(reply_text),
                        '_reply_conflict_checked': True,
                        '_reply_conflict_reason': reply_conflict_reason,
                    })

    for idx, t in enumerate(recent_targets[:2], start=3):
        if t.get('tweetId'):
            candidate_key = f"tagclaw:{t['tweetId']}"
            if candidate_key in executed_keys:
                skipped_by_dedup += 1
            else:
                drafts.append({
                    'id': f'draft-{idx}', 'type': 'curate', 'tweetId': t['tweetId'],
                    'vp': t.get('vp') or 5, 'priority': 5,
                    'target_key': candidate_key, 'target_username': t.get('username'),
                    'source_candidate_id': top_candidate.get('candidate_id') if top_candidate else candidate_key,
                    'expected_tas_social_uplift': top_candidate.get('expected_tas_social_uplift') if top_candidate else None,
                    'recommended_action': 'curate',
                })

    # Write all outputs
    atomic_write_json(RUNTIME / 'source-health.json', {
        'version': 'v2', 'updated_at': generated_at, 'source_class': 'bookmarker-native',
        **source_health, 'notes': 'bookmarker self-published V2',
    })
    atomic_write_json(RUNTIME / 'topic-brief.json', {
        'version': 'v2', 'updated_at': generated_at, 'status': normalize_status(sync_status, default='stale'),
        'source_class': 'bookmarker-native',
        'summary': str(daily_theme_pack.get('thesis') or recommendations[0] if recommendations else summary_text),
        'keywords': keywords, 'viewpoints': recommendations, 'candidates': enriched_candidates,
        'daily_theme_pack_ref': 'runtime/bookmarker/daily-theme-pack.json',
        'content_urgency': urgency,
        'high_signal_count': int(high_signal_count),
        'notes': 'bookmarker self-published V2',
    })
    atomic_write_json(RUNTIME / 'daily-theme-pack.json', daily_theme_pack)
    atomic_write_json(RUNTIME / 'content-candidates.json', {
        'version': 'v2', 'updated_at': generated_at, 'status': normalize_status(sync_status, default='stale'),
        'source_class': 'bookmarker-native', 'items': enriched_candidates,
        'recommended_action_mix': recommended_action_mix,
        'notes': 'bookmarker self-published V2 with uplift scoring',
    })
    atomic_write_json(RUNTIME / 'topic-performance.json', {
        'version': 'v1', 'updated_at': generated_at, 'status': normalize_status(sync_status, default='stale'),
        'source_class': 'bookmarker-native',
        'theme': theme,
        'keywords': keywords,
        'candidate_count': len(enriched_candidates),
        'top_candidate_id': top_candidate.get('candidate_id') if top_candidate else None,
        'top_candidate_uplift': top_candidate.get('expected_tas_social_uplift') if top_candidate else None,
        'recommended_action_mix': recommended_action_mix,
        'recognition_weights_applied': bool(_recognition_weights),
        'recognition_weights_count': len(_recognition_weights),
        'notes': 'Minimal topic/candidate performance snapshot; recognition weights applied. topic_fatigue_v1.py enriches per_topic_scores.',
    })
    # P3 2026-05-20: Bookmarker computes TAS_social with community/PoB-first weighting
    # plus bounded smoothing to avoid single-cycle spikes.

    identity_ctx = resolve_tagclaw_identity_context()
    tracked_username = str((community_scan.get('tracked_username') or identity_ctx.get('tracked_username') or 'clawdbot')).strip().lower()
    self_usernames = sorted({
        _norm_identity(v)
        for v in ((community_scan.get('self_usernames') or []) + (identity_ctx.get('self_usernames') or []) + [tracked_username])
        if _norm_identity(v)
    })
    cb_community = community_scan.get('clawdbot_community') or {}
    main_signals = (main_tas_social or {}).get('inputs', {})
    legacy_signals = (legacy_tas_social or {}).get('inputs', {})
    align_signals = (
        main_signals.get('align_signals')
        or main_signals.get('signals')
        or legacy_signals.get('align_signals')
        or legacy_signals.get('signals')
        or {}
    )
    align_api_source = (
        'runtime/main/tas-social.json inputs.align_signals'
        if (main_signals.get('align_signals') or main_signals.get('signals'))
        else ('memory/tas-social-latest.json inputs.align_signals'
              if (legacy_signals.get('align_signals') or legacy_signals.get('signals'))
              else 'missing')
    )
    telegram_align = _load_recent_telegram_align(
        window_hours=48,
        tracked_usernames=self_usernames,
        api_key=identity_ctx.get('api_key'),
        known_post_ids={str(v) for v in (cb_community.get('post_ids') or []) if str(v)},
    )

    # Track A score
    raw_align_api = sum(align_signals.get(k, 0) * {'like': 1, 'curation': 3, 'comment': 5, 'retweet': 3}.get(k, 0)
                        for k in ('like', 'curation', 'comment', 'retweet'))
    raw_align_telegram = float(telegram_align.get('raw_weight') or 0.0)
    raw_align = raw_align_api + raw_align_telegram
    # Track A: 0 when no in-window @0xNought interaction — never inherit prior TAS_social
    align_score_bk = min(5.0, raw_align / 4.0) if raw_align > 0 else 0.0

    # Track B score — tracked bookmarker posts only (not whole-feed aggregate)
    # V2: final Track B community_score = 0.2 * raw_interaction_score + 0.8 * credit_weighted_score.
    community_score_bk = float(cb_community.get('community_score') or 0.0)
    community_raw_interaction_score_bk = float(cb_community.get('raw_interaction_score') or 0.0)
    community_credit_weighted_score_bk = float(cb_community.get('credit_weighted_score') or 0.0)
    community_credit_weighted_interactions_bk = float(cb_community.get('credit_weighted_interactions') or 0.0)
    community_signals_raw = {
        'total_likes': cb_community.get('likes', 0),
        'total_retweets': cb_community.get('retweets', 0),
        'total_replies': cb_community.get('replies', 0),
        'total_interactions': cb_community.get('total_interactions', 0),
    }
    community_source = f'{tracked_username}-posts-credit-v2' if cb_community.get('post_count', 0) > 0 else 'passive-fallback-credit-v2'
    cb_cap_note = cb_community.get('cap_note', '')
    cb_credit_weighting = cb_community.get('credit_weighting') if isinstance(cb_community.get('credit_weighting'), dict) else {}

    # Track C — PoB reward score: TagClaw-tick curation reward only (post-specific)
    # NOT the broad trader claimable aggregate which includes unrelated ticks (BUIDL, TTAI, etc.)
    pob_reward_score = 0.0
    pob_claimable_usd = 0.0
    pob_source = 'none'
    try:
        _reward_status_pob = read_json(MAIN_ROOT / 'runtime' / 'trader' / 'reward-status.json') or {}
        for _item in (_reward_status_pob.get('claimable') or []):
            if isinstance(_item, dict) and _item.get('tick') == 'TagClaw':
                pob_claimable_usd = float(_item.get('reward_value_usd') or 0.0)
                pob_source = 'reward-status.json TagClaw tick'
                break
    except Exception:
        pass  # graceful fallback: pob_reward_score stays 0.0
    pob_reward_score = pob_score_from_reward_usd(pob_claimable_usd)

    xreco_doc = read_json(RUNTIME / 'tas-xreco.json') or {}
    xreco_score = xreco_score_from_value(xreco_doc.get('value'))

    raw_tas_social, weighted_components = compute_raw_tas_social(
        community_score=community_score_bk,
        pob_score=pob_reward_score,
        align_score=align_score_bk,
        xreco_score=xreco_score,
    )
    smoothing_state = read_json(TAS_SOCIAL_STATE_PATH) or {}
    previous_smoothed = smoothing_state.get('smoothed_value')
    if previous_smoothed is None:
        previous_smoothed = previous_tas_social_doc.get('value')
    tas_social_smoothed, smoothing_detail = smooth_tas_social(raw_tas_social, previous_smoothed)
    tas_social_computed = round(tas_social_smoothed, 4)
    tas_social_status = 'ok' if tas_social_computed is not None else tas_status
    bookmarker_strategy_loop = build_metric_strategy_loop(
        'TAS_social',
        tas_social_computed if tas_social_computed is not None else tas_value,
        safe_float(previous_tas_social_doc.get('value')),
        tas_social_status,
        normalize_status(previous_tas_social_doc.get('status'), default='stale'),
        previous_autonomy_intent.get('strategy_action') or previous_autonomy_intent.get('mode'),
        previous_autonomy_intent.get('reason'),
    )

    _formula_str = tas_social_formula_string()
    _v2_tas_doc = {
        'version': TAS_SOCIAL_VERSION, 'updated_at': generated_at,
        'status': tas_social_status,
        'value': tas_social_computed if tas_social_computed is not None else tas_value,
        'raw_value': round(raw_tas_social, 4),
        'align_score': round(align_score_bk, 4),
        'community_score': round(community_score_bk, 4),
        'community_raw_interaction_score': round(community_raw_interaction_score_bk, 4),
        'community_credit_weighted_score': round(community_credit_weighted_score_bk, 4),
        'community_credit_weighted_interactions': round(community_credit_weighted_interactions_bk, 4),
        'pob_reward_score': round(pob_reward_score, 4),
        'pob_claimable_usd': round(pob_claimable_usd, 6),
        'xreco_score': round(xreco_score, 4),
        'community_signals': community_signals_raw,
        'community_source': community_source,
        'community_scan_ref': 'runtime/bookmarker/community-scan.json → clawdbot_community',
        'weighted_components': {k: round(v, 4) for k, v in weighted_components.items()},
        'smoothing_detail': smoothing_detail,
        'track_a_detail': {
            'source': f'{align_api_source} + memory/twin-recognition.json Telegram feedback',
            'window_hours': 48,
            'scorer': '@0xNought',
            'target': f'@{tracked_username} posts',
            'raw_align': raw_align,
            'raw_align_api': raw_align_api,
            'raw_align_api_source': align_api_source,
            'raw_align_telegram': raw_align_telegram,
            'telegram_like_count': int(telegram_align.get('like_count') or 0),
            'telegram_heart_count': int(telegram_align.get('heart_count') or 0),
            'telegram_recent_post_ids': telegram_align.get('recent_post_ids') or [],
            'telegram_recent_events': telegram_align.get('recent_events') or [],
            'telegram_weight_rule': 'telegram_like=+2 raw_align, telegram_heart=+4 raw_align',
            'fallback_rule': 'align_score=0 when no in-window @0xNought interaction across API + Telegram feedback (no prior-TAS leakage)',
        },
        'track_b_detail': {
            'source': f'community-scan.json clawdbot_community V2 (only @{tracked_username} posts)',
            'version': 'v2',
            'post_count': cb_community.get('post_count', 0),
            'post_ids': cb_community.get('post_ids', []),
            'raw_interaction_score': round(community_raw_interaction_score_bk, 4),
            'credit_weighted_score': round(community_credit_weighted_score_bk, 4),
            'community_score': round(community_score_bk, 4),
            'credit_weighted_interactions': round(community_credit_weighted_interactions_bk, 4),
            'formula': cb_community.get('formula'),
            'score_formulas': cb_community.get('score_formulas') or {},
            'credit_weighting': cb_credit_weighting,
            'interaction_breakdown': cb_community.get('interaction_breakdown') or {},
            'attribution_paths': cb_community.get('attribution_paths') or [],
            'provenance': cb_community.get('provenance') or {},
            'raw_is_capped': cb_community.get('raw_is_capped', False),
            'is_capped': cb_community.get('is_capped', False),
            'cap_note': cb_cap_note,
            'note': cb_community.get('note'),
        },
        'track_c_detail': {
            'source': pob_source,
            'window_hours': 24,
            'pob_claimable_usd': round(pob_claimable_usd, 6),
            'pob_reward_score': round(pob_reward_score, 4),
            'baseline_usd': 5.0,
            'note': 'TagClaw-tick curation reward only; excludes unrelated ticks (BUIDL, TTAI, etc.)',
        },
        'track_d_detail': {
            'source': 'runtime/bookmarker/tas-xreco.json',
            'xreco_score': round(xreco_score, 4),
            'raw_value': xreco_doc.get('value'),
            'hits': xreco_doc.get('hits'),
            'pushes': xreco_doc.get('pushes'),
            'hit_rate': xreco_doc.get('hit_rate'),
            'note': 'Tertiary quality prior only; intentionally weaker than community and PoB.',
        },
        'curate_reward_usd': reward_attribution.get('curate_reward_usd', 0.0),
        'curate_reward_score': reward_attribution.get('curate_reward_score', 0.0),
        'creator_reward_usd': reward_attribution.get('creator_reward_usd', 0.0),
        'creator_reward_score': reward_attribution.get('creator_reward_score', 0.0),
        'source_agent': 'bookmarker',
        'source_class': 'bookmarker-native',
        'comparison': bookmarker_strategy_loop,
        'strategy_action': bookmarker_strategy_loop['strategy_action'],
        'planning_focus': bookmarker_strategy_loop['planning_focus'],
        'formula': _formula_str,
        'tracked_username': tracked_username,
        'actor_identity': community_scan.get('actor_identity') or identity_ctx.get('actor_identity'),
        'expected_identity': community_scan.get('expected_identity') or identity_ctx.get('expected_identity'),
        'self_usernames': self_usernames,
        'self_agent_ids': community_scan.get('self_agent_ids') or identity_ctx.get('self_agent_ids') or [],
        'credentials_path': community_scan.get('credentials_path') or identity_ctx.get('credentials_path'),
        'credentials_source': community_scan.get('credentials_source') or identity_ctx.get('credentials_source'),
        'notes': f'bookmarker-native {TAS_SOCIAL_VERSION}; community and PoB are primary, align is secondary, X reco is tertiary; bounded smoothing applied before publishing.',
    }
    # Engines merged 2026-05-28: native run_bookmarker_runtime.py is the sole
    # canonical writer of tas-social.json. Only write here when the native
    # value is missing or stale (>90 min) so the two engines never flip-flop.
    if _native_tas_is_stale():
        atomic_write_json(RUNTIME / 'tas-social.json', _v2_tas_doc)
        atomic_write_json(TAS_SOCIAL_STATE_PATH, {
            'version': TAS_SOCIAL_VERSION,
            'updated_at': generated_at,
            'raw_value': round(raw_tas_social, 4),
            'smoothed_value': tas_social_computed,
            'detail': smoothing_detail,
        })
        print('[publish_bookmarker_runtime_v2] native tas-social.json missing/stale — wrote v2 fallback')
    else:
        print('[publish_bookmarker_runtime_v2] native tas-social.json fresh — deferring (no overwrite)')

    # P1 fix: write reward-attribution.json from trader/reward-status.json
    try:
        reward_status_doc = read_json(MAIN_ROOT / 'runtime' / 'trader' / 'reward-status.json') or {}
        curate_reward_usd = 0.0
        claimable_detail = []
        for _item in (reward_status_doc.get('claimable') or []):
            if isinstance(_item, dict) and _item.get('tick') == 'TagClaw':
                curate_reward_usd = float(_item.get('reward_value_usd') or 0.0)
                claimable_detail.append({
                    'tick': _item.get('tick'),
                    'amount': _item.get('claimable_amount'),
                    'usd': curate_reward_usd,
                    'status': _item.get('status'),
                })
                break
        BASELINE_CURATE_USD = 0.01
        curate_reward_score = min(1.0, curate_reward_usd / BASELINE_CURATE_USD)
        atomic_write_json(RUNTIME / 'reward-attribution.json', {
            'version': 'v1',
            'updated_at': generated_at,
            'window_hours': 24,
            'curate_reward_usd': round(curate_reward_usd, 6),
            'creator_reward_usd': 0.0,
            'curate_reward_score': round(curate_reward_score, 4),
            'creator_reward_score': 0.0,
            'baseline_curate_usd': BASELINE_CURATE_USD,
            'baseline_creator_usd': 0.005,
            'source': 'runtime/trader/reward-status.json',
            'claimable_detail': claimable_detail,
        })
    except Exception:
        pass  # graceful degrade — reward-attribution write failure must not interrupt main flow

    # F1 (permanent fix): pre-compute post-draft availability and hours_since_last_post so the
    # autonomy-intent generator can force 'post' into recommended_actions even in conservative mode.
    # Without this, the 2h regeneration cycle would erase the bandaid patch every cycle.
    has_post_draft = any(d.get('type') == 'post' for d in drafts)
    _sh_items_f1 = (read_json(SOCIAL_HISTORY_PATH) or {}).get('items') or []
    _last_post_dt_f1 = None
    for _shi in reversed(_sh_items_f1):
        if isinstance(_shi, dict) and _shi.get('type') == 'post':
            _last_post_dt_f1 = parse_dt(_shi.get('executed_at') or '')
            if _last_post_dt_f1:
                break
    hours_since_last_post = (
        (datetime.now(timezone.utc) - _last_post_dt_f1.astimezone(timezone.utc)).total_seconds() / 3600
        if _last_post_dt_f1 else 999.0
    )

    # P4+P6: Bookmarker autonomy intent — integrate Main's OP/VP/mode + social policy
    # Resource truth comes from bookmarker resource tracker first, then main packet,
    # then main runtime-state as the last fallback.
    resource_state = load_current_resource_state()
    dispatch_config = read_json(MAIN_ROOT / 'runtime' / 'shared' / 'dispatch-config.json') or {}
    social_gate = dispatch_config.get('social') or {}
    op = float(resource_state.get('op') or 0)
    vp = float(resource_state.get('vp') or 0)

    # Replicate Main's mode logic (from runtime_utils_v2.py), including VP drain modes.
    if op > 1200 and vp > 150:
        resource_mode = 'super-active'
        mode_target_actions = 3
        mode_target_curations = 10
    elif op > 1000 and vp > 120:
        resource_mode = 'mid-active'
        mode_target_actions = 2
        mode_target_curations = 8
    elif op > 800 and vp > 100:
        resource_mode = 'active'
        mode_target_actions = 1
        mode_target_curations = 5
    elif op <= 800 and vp >= 150:
        resource_mode = 'vp-flush'
        mode_target_actions = 1
        mode_target_curations = 10
    elif (vp >= 180 and 100 <= op < 800) or (vp >= 150 and op < 200):
        resource_mode = 'vp-drain'
        mode_target_actions = 1
        mode_target_curations = 8
    else:
        resource_mode = 'conservative'
        mode_target_actions = 0
        mode_target_curations = 3

    # Intersect TAS_social gate with resource mode, applying Main guidance
    tas_for_mode = tas_social_computed if tas_social_computed is not None else (tas_value or 0.0)

    # Apply guidance overrides
    g_action_emphasis = main_guidance.get('action_emphasis', 'curate_heavy')
    g_signal_priority = main_guidance.get('signal_priority', 'balanced')
    g_interaction_target_mode = main_guidance.get('interaction_target_mode', 'high_engagement_authors')
    g_suggested_targets = main_guidance.get('suggested_targets', [])
    g_vp_budget = main_guidance.get('interaction_budget_vp', 'mid')
    g_topic = main_guidance.get('topic_directive', 'agent_economy')

    # VP budget → vp values
    vp_budget_map = {'low': (2, 4), 'mid': (4, 6), 'high': (6, 9)}
    vp_min, vp_max = vp_budget_map.get(g_vp_budget, (4, 6))

    # Action set based on guidance emphasis
    if g_action_emphasis == 'post_new':
        guidance_actions = ['post', 'curate']
    elif g_action_emphasis == 'reply_focus':
        guidance_actions = ['reply', 'curate']
    else:  # curate_heavy (default)
        guidance_actions = ['curate']

    consecutive_conservative = 0

    if tas_for_mode >= 2.0 and resource_mode in ('active', 'mid-active', 'super-active'):
        autonomy_mode = 'active'
        recommended_actions = list({'post', 'curate', 'reply'} | set(guidance_actions))
        autonomy_reason = (f'TAS_social={tas_for_mode:.2f}≥2.0 + resource_mode={resource_mode} '
                           f'(OP={op:.0f}/VP={vp:.0f}) + guidance={guidance_mode} → active')
    elif tas_for_mode >= 0.5 and resource_mode in ('vp-flush', 'vp-drain'):
        autonomy_mode = 'standard'
        recommended_actions = ['curate'] if resource_mode == 'vp-flush' else list({'curate'} | set(guidance_actions))
        autonomy_reason = (f'TAS_social={tas_for_mode:.2f}≥0.5 + resource_mode={resource_mode} '
                           f'(OP={op:.0f}/VP={vp:.0f}) + resource_source={resource_state.get("source")} → standard-curate')
    elif tas_for_mode >= 0.5 and op > 200:
        autonomy_mode = 'standard'
        recommended_actions = guidance_actions
        autonomy_reason = (f'TAS_social={tas_for_mode:.2f}≥0.5 + OP={op:.0f}>200 '
                           f'(resource_mode={resource_mode}) + guidance={guidance_mode} → standard')
    else:
        # P0: Circuit breaker — escape conservative deadlock
        # If TAS_social is stuck below thresholds for 6+ consecutive cycles (~24h),
        # force standard mode to break the no-posts → no-engagement → low-TAS loop.
        if previous_autonomy_intent and previous_autonomy_intent.get('mode') == 'conservative':
            consecutive_conservative = int(previous_autonomy_intent.get('consecutive_conservative_cycles') or 0) + 1
        if consecutive_conservative >= 6:
            autonomy_mode = 'standard'
            recommended_actions = ['post', 'curate']
            autonomy_reason = (
                f'circuit-breaker: {consecutive_conservative} consecutive conservative cycles, '
                f'TAS_social={tas_for_mode:.2f} flat, forcing standard to break deadlock'
            )
        else:
            autonomy_mode = 'conservative'
            recommended_actions = []
            autonomy_reason = (f'TAS_social={tas_for_mode:.2f} or OP={op:.0f}≤200 → conservative')
            # F1 (permanent fix): conservative mode must NOT block posting when we have a post draft
            # AND the account is in a starved state (TAS near-zero or no post in >12h).
            # The no-posts→no-engagement→low-TAS deadlock must be broken here, not just at the
            # 6-cycle circuit breaker. Runs every 2h regeneration so bandaid patches cannot revert.
            if has_post_draft and (tas_for_mode < 0.1 or hours_since_last_post > 12):
                recommended_actions = ['post']
                autonomy_reason += (
                    f' | F1-post-override: post forced (has_post_draft=True, '
                    f'TAS={tas_for_mode:.2f}<0.1 or hours_since_last_post={hours_since_last_post:.1f}h>12)'
                )

    # P1: VP target override — dynamic mode escalation + constraint relaxation
    resource_status = read_json(BOOKMARKER_ROOT / 'memory' / 'tagclaw-resource-status.json') or {}
    vp_spent_today = float(resource_status.get('estimated_vp_spent_today') or 0.0)
    vp_daily_target = float(resource_status.get('daily_vp_min_spend') or 67.0)
    SH_TZ = timezone(timedelta(hours=8))
    now_sh = datetime.now(SH_TZ)
    vp_pct = vp_spent_today / max(vp_daily_target, 1.0)
    vp_deficit_severe = vp_pct < 0.3  # <30% of daily target consumed
    vp_deficit_moderate = vp_pct < 0.6  # <60% consumed

    # Escalate mode based on VP deficit severity
    if vp_deficit_severe and autonomy_mode == 'conservative':
        autonomy_mode = 'standard'
        recommended_actions = ['curate', 'post', 'reply']
        mode_target_actions = max(mode_target_actions, 5)
        mode_target_curations = max(mode_target_curations, 15)
        autonomy_reason = (
            f'VP-override(severe): spent={vp_spent_today:.1f}/{vp_daily_target:.0f} VP '
            f'({vp_pct*100:.1f}%) at hour={now_sh.hour}, '
            f'forcing standard + raised targets | was: {autonomy_reason}'
        )
    elif vp_deficit_moderate and autonomy_mode in ('conservative', 'standard'):
        mode_target_actions = max(mode_target_actions, 3)
        mode_target_curations = max(mode_target_curations, 10)

    # F4 (permanent fix): P0 daily OP/VP floor — overrides conservative when daily consumption
    # is below the minimum operational floor. P0 resource floors take precedence over TAS strategy
    # signals because a starved agent cannot generate the engagement needed to improve TAS anyway.
    # Floors: OP=667 (1/3 of 2000 daily OP recovery), VP=67 (daily_vp_min_spend from resource tracker).
    # Note: social-intent.json had resource_floor_override=true but it was not wired into
    # autonomy-intent generation — this fix closes that gap permanently.
    _op_spent_today_f4 = float(resource_status.get('estimated_op_spent_today') or 0.0)
    _op_daily_floor_f4 = 667.0
    _op_below_floor_f4 = _op_spent_today_f4 < _op_daily_floor_f4
    _vp_below_floor_f4 = vp_spent_today < vp_daily_target  # vp_daily_target already = daily_vp_min_spend
    if (_op_below_floor_f4 or _vp_below_floor_f4) and autonomy_mode == 'conservative':
        autonomy_mode = 'active'
        recommended_actions = list({'post', 'curate', 'reply'} | set(recommended_actions))
        mode_target_actions = max(mode_target_actions, 2)
        mode_target_curations = max(mode_target_curations, 8)
        autonomy_reason = (
            f'F4-resource-floor-override: '
            f'OP_consumed={_op_spent_today_f4:.0f}<{_op_daily_floor_f4:.0f} ({_op_below_floor_f4}) '
            f'or VP_consumed={vp_spent_today:.1f}<{vp_daily_target:.0f} ({_vp_below_floor_f4}), '
            f'P0 floor > TAS signal, upgrading conservative→active | was: {autonomy_reason}'
        )

    # Cap actions by dispatch-config limits, relaxed when VP is below target
    _gate_max = social_gate.get('max_per_type') or {}
    max_per_type = {
        'post': max(_gate_max.get('post', 1), 3 if vp_deficit_severe else 2 if vp_deficit_moderate else 1),
        'reply': max(_gate_max.get('reply', 1), 5 if vp_deficit_severe else 3 if vp_deficit_moderate else 1),
        'curate': max(_gate_max.get('curate', 1), 15 if vp_deficit_severe else 10 if vp_deficit_moderate else 1),
        'like': max(_gate_max.get('like', 1), 20 if vp_deficit_severe else 10 if vp_deficit_moderate else 1),
    }
    _gate_cooldown = int(social_gate.get('cooldown_hours') or 24)
    cooldown_hours = 2 if vp_deficit_severe else 6 if vp_deficit_moderate else _gate_cooldown

    atomic_write_json(RUNTIME / 'autonomy-intent.json', {
        'version': 'v2', 'updated_at': generated_at, 'source_class': 'bookmarker-native',
        'mode': autonomy_mode,
        'recommended_actions': recommended_actions,
        'reason': autonomy_reason,
        'strategy_action': bookmarker_strategy_loop['strategy_action'],
        'planning_focus': bookmarker_strategy_loop['planning_focus'],
        'strategy_loop': bookmarker_strategy_loop,
        'tas_social_value': tas_for_mode,
        'resource_mode': resource_mode,
        'resource_state_source': resource_state.get('source'),
        'op': op, 'vp': vp,
        'consecutive_conservative_cycles': consecutive_conservative,
        'target_actions': mode_target_actions,
        'target_curations': mode_target_curations,
        'vp_target_progress': {
            'spent_today': resource_state.get('vp_spent_today'),
            'daily_target': resource_state.get('daily_vp_target'),
            'remaining_to_target': resource_state.get('vp_remaining_to_target'),
            'status': resource_state.get('vp_target_status'),
        },
        'max_per_type': max_per_type,
        'cooldown_hours': cooldown_hours,
        'thresholds': {
            'tas_active': 2.0, 'tas_standard': 0.5,
            'op_super_active': 1200, 'op_mid_active': 1000, 'op_active': 800,
            'vp_super_active': 150, 'vp_mid_active': 120, 'vp_active': 100,
        },
        'notes': 'v2: TAS_social × OP/VP resource mode × Main guidance',
        'main_guidance': {
            'experiment_mode': guidance_mode,
            'signal_priority': g_signal_priority,
            'topic_directive': g_topic,
            'interaction_target_mode': g_interaction_target_mode,
            'suggested_targets': g_suggested_targets,
            'vp_budget': g_vp_budget,
            'vp_range': [vp_min, vp_max],
            'action_emphasis': g_action_emphasis,
        },
    })
    planner_mode = 'claims-first' if top_claim else 'source-first'
    selected_move_runtime = move_by_claim_id.get(str((top_claim or {}).get('claim_id') or '')) if top_claim else None
    selected_move_reason = (selected_move_runtime or {}).get('move_reason') if isinstance(selected_move_runtime, dict) else None
    selected_move_gate = (selected_move_runtime or {}).get('evidence_gate') if isinstance(selected_move_runtime, dict) else None
    downgrade_reason = selected_move_reason if str(selected_move_reason or '').startswith('gate_fallback_') else None
    if not drafts:
        multi_source_draft = build_multi_source_thesis_fallback(
            daily_theme_pack=daily_theme_pack,
        )
        if multi_source_draft:
            _ms_text = str(multi_source_draft.get('text') or '')
            _ms_bad, _ms_reason = _is_low_quality_post(_ms_text, source_text=None)
            if not _ms_bad:
                _chosen_tick_ms = choose_tick(
                    keywords,
                    _wiki_trending_ticks,
                    buidl_pct_24h=_buidl_pct_24h,
                    text=_ms_text,
                    theme=str(multi_source_draft.get('theme') or 'agent-infra'),
                    social_history_path=SOCIAL_HISTORY_PATH,
                    in_run_tick_counts=_in_run_tick_counts,
                )
                _in_run_tick_counts[_chosen_tick_ms] = _in_run_tick_counts.get(_chosen_tick_ms, 0) + 1
                multi_source_draft['tick'] = _chosen_tick_ms
                multi_source_draft['content_hash'] = compute_post_content_hash(_ms_text)
                multi_source_draft['content_hash_excluding_source'] = compute_post_content_hash_excluding_source(_ms_text)
                drafts.append(multi_source_draft)
                planner_mode = 'multi-source-fallback'
                picker_source = picker_source or 'multi-source-fallback'
            else:
                source_first_skip_reasons.append(f'multi_source_quality_gate:{_ms_reason}')
    if not drafts:
        fallback_draft = _synthesize_post_draft_from_xsync()
        if fallback_draft:
            # P3.2 follow-up (2026-05-17 hotfix): the xsync-fallback path
            # previously bypassed the final quality gate that claims-first and
            # source-first now share. Even though the xsync fallback uses
            # hand-written framing, run it through the same gate so any future
            # change to that fallback cannot ship banned-template/verbatim/
            # too-short content to TagClaw.
            _fb_text = fallback_draft.get('text', '') or ''
            _fb_source = fallback_draft.get('source_excerpt', '') or fallback_draft.get('source_tweet_text', '') or ''
            _fb_bad, _fb_reason = _is_low_quality_post(_fb_text, source_text=_fb_source or None)
            if _fb_bad:
                print(
                    f'[xsync-fallback] skipping draft — final quality gate rejected: {_fb_reason}',
                    file=sys.stderr,
                )
                source_first_skip_reasons.append(f'xsync_final_quality_gate:{_fb_reason}')
            else:
                fallback_template_used = True
                drafts.append(fallback_draft)
                picker_source = picker_source or 'xsync-fallback'

    if not top_claim and claims_first_failure_reason in {'all_claims_rejected', 'no_publishable_claims'}:
        degradation_path.append('claims_first_blocked')
    if source_first_llm_failed:
        degradation_path.append('source_first_llm_failed')
    if fallback_template_used:
        degradation_path.append('template_fallback')
    if fallback_template_used and degradation_path:
        planner_mode = 'fallback'
        if drafts and isinstance(drafts[0], dict):
            drafts[0]['_planner_mode'] = 'fallback'
            drafts[0]['planner_mode'] = 'fallback'
            drafts[0]['degradation_path'] = degradation_path[:]
            drafts[0]['_fallback_level'] = drafts[0].get('_fallback_level') or 'degraded'
    if not top_claim and drafts and isinstance(drafts[0], dict):
        _planner_override = str(drafts[0].get('_planner_mode') or '').strip()
        if _planner_override:
            planner_mode = _planner_override

    planner_diagnostics = {
        'selected_claim_id': top_claim.get('claim_id') if top_claim else None,
        'selected_claim_family': top_claim.get('claim_family') if top_claim else None,
        'selected_move_type': (selected_move_runtime or {}).get('move_type') if isinstance(selected_move_runtime, dict) else None,
        'selected_move_reason': selected_move_reason,
        'selected_move_gate': selected_move_gate,
        'downgrade_reason': downgrade_reason,
        'all_claims_blocked_by_source_exhaustion': all_claims_blocked_by_source_exhaustion,
        'source_first_llm_failed': source_first_llm_failed,
        'source_first_llm_failure_reason': source_first_llm_failure_reason or None,
        'source_first_used_relaxed_7d': source_first_used_relaxed_7d,
        'source_first_llm_diagnostics': source_first_llm_diagnostics or None,
        'degradation_path': degradation_path[:],
    }
    atomic_write_json(RUNTIME / 'social-drafts.json', {
        'version': 'v2', 'updated_at': now_iso(), 'status': 'ok' if drafts else 'stale',
        'source_class': 'bookmarker-native', 'drafts': drafts,
        'notes': 'bookmarker self-published V2 social drafts',
        'meta': {'keyword_count': len(keywords), 'x_items_seen': len(x_items),
                 'recent_target_count': len(recent_targets), 'draft_types': [d.get('type') for d in drafts],
                 'dedup_window_hours': 48, 'executed_keys_checked': len(executed_keys),
                 'skipped_by_dedup': skipped_by_dedup,
                 'candidate_pool_size': candidate_pool_size, 'picker_source': picker_source,
                 'planner_mode': planner_mode,
                 'degradation_path': degradation_path[:],
                 'claims_first_attempted': claims_first_attempted,
                 'claims_considered': claims_considered,
                 'claims_selected': claims_selected,
                 'claims_skipped': claims_skipped,
                 'claims_skip_reasons': claims_skip_reasons,
                 'source_first_skip_reasons': source_first_skip_reasons[-20:],
                 'claims_first_failure_reason': claims_first_failure_reason,
                 'all_claims_blocked_by_source_exhaustion': all_claims_blocked_by_source_exhaustion,
                 'source_first_llm_failed': source_first_llm_failed,
                 'source_first_llm_failure_reason': source_first_llm_failure_reason or None,
                 'source_first_used_relaxed_7d': source_first_used_relaxed_7d,
                 'source_first_llm_diagnostics': source_first_llm_diagnostics or None,
                 'top_claim_id': top_claim.get('claim_id') if top_claim else None,
                 'selected_move_type': (selected_move_runtime or {}).get('move_type') if isinstance(selected_move_runtime, dict) else None,
                 'selected_move_reason': selected_move_reason,
                 'selected_move_gate': selected_move_gate,
                 'downgrade_reason': downgrade_reason,
                 'planner_diagnostics': planner_diagnostics,
                 'top_candidate_id': top_candidate.get('candidate_id') if top_candidate else None,
                 'wiki_delta_claim_count': len(wiki_delta_doc.get('claim_deltas') or []),
                 'publishable_claim_count': len(publishable_claims_doc.get('claims') or []),
                 'social_move_count': len(social_move_plan_doc.get('moves') or []),
                 'recommended_action_mix': recommended_action_mix},
    })
    atomic_write_json(PLANNER_AUDIT_PATH, {
        'version': 'v1',
        'generated_at': now_iso(),
        'planner_mode': planner_mode,
        'claims_first_attempted': claims_first_attempted,
        'claims_considered': claims_considered,
        'claims_selected': claims_selected,
        'claims_skipped': claims_skipped,
        'claims_skip_reasons': claims_skip_reasons,
        'source_first_skip_reasons': source_first_skip_reasons[-20:],
        'claims_first_failure_reason': claims_first_failure_reason,
        'all_claims_blocked_by_source_exhaustion': all_claims_blocked_by_source_exhaustion,
        'source_first_llm_failed': source_first_llm_failed,
        'source_first_llm_failure_reason': source_first_llm_failure_reason or None,
        'source_first_used_relaxed_7d': source_first_used_relaxed_7d,
        'source_first_llm_diagnostics': source_first_llm_diagnostics or None,
        'degradation_path': degradation_path[:],
        'selected_claim': top_claim,
        'selected_move': selected_move_runtime,
        'selected_move_reason': selected_move_reason,
        'selected_move_gate': selected_move_gate,
        'downgrade_reason': downgrade_reason,
        'planner_diagnostics': planner_diagnostics,
        'selected_draft_id': drafts[0].get('id') if drafts else None,
        'draft_count': len(drafts),
        'claim_audit_entries': claims_audit_entries,
        'publishable_claims_preview': [
            {
                'claim_id': c.get('claim_id'),
                'claim_family': c.get('claim_family'),
                'publication_score': c.get('publication_score'),
                'score_breakdown': c.get('score_breakdown') or {},
                'best_anchor_source': c.get('best_anchor_source'),
            }
            for c in publishable_claims[:5]
        ],
    })
    publication_memory = build_publication_memory(
        SOCIAL_HISTORY_PATH,
        recent_hours=48,
        existing=current_publication_memory,
        theme_weights=(wiki_delta_doc.get('snapshot') or {}).get('theme_weights') or ({theme: float(top_candidate.get('expected_tas_social_uplift') or 0.0)} if top_candidate else {}),
        top_claim_ids=[str(d.get('claim_id') or d.get('claim_family') or d.get('id')) for d in drafts if d.get('type') == 'post'] or ((wiki_delta_doc.get('snapshot') or {}).get('top_claim_ids') or []),
    )
    atomic_write_json(PUBLICATION_MEMORY_PATH, publication_memory)

    # Tick distribution tracking — persist per-run counts for BUIDL bias enforcement.
    # Merges in-run counts with existing file's today_counts for cross-run tracking.
    _today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    _existing_tick_dist = read_json(TICK_DISTRIBUTION_PATH) or {}
    _today_counts = _existing_tick_dist.get('today_counts') if _existing_tick_dist.get('date') == _today_str else {}
    if not isinstance(_today_counts, dict):
        _today_counts = {}
    for _tk, _cnt in _in_run_tick_counts.items():
        _today_counts[_tk] = _today_counts.get(_tk, 0) + _cnt
    _total_today = sum(_today_counts.values())
    atomic_write_json(TICK_DISTRIBUTION_PATH, {
        'date': _today_str,
        'updated_at': now_iso(),
        'today_counts': _today_counts,
        'buidl_pct_today': round(_today_counts.get('BUIDL', 0) / _total_today, 3) if _total_today else 0.0,
        'buidl_pct_24h_at_run_start': round(_buidl_pct_24h, 3),
        'in_run_counts': _in_run_tick_counts,
    })

    # PR1: explicit execution-plane artifacts for bookmarker-owned social execution
    social_plan = {
        'version': 'v1',
        'plan_kind': 'social-execution-plan',
        'agent': 'bookmarker',
        'executor': 'bookmarker',
        'execution_owner': 'bookmarker',
        'control_plane': 'main',
        'generated_at': generated_at,
        'source_class': 'bookmarker-execution-plane',
        'control_ref': 'runtime/main/social-intent.json',
        'guidance_ref': 'runtime/main/bookmarker-guidance.json',
        'autonomy_ref': 'runtime/bookmarker/autonomy-intent.json',
        'drafts_ref': 'runtime/bookmarker/social-drafts.json',
        'status': 'ready' if recommended_actions else 'hold',
        'autonomy_mode': autonomy_mode,
        'strategy_action': bookmarker_strategy_loop['strategy_action'],
        'planning_focus': bookmarker_strategy_loop['planning_focus'],
        'payload': {
            'recommended_actions': recommended_actions,
            'recommended_action_mix': recommended_action_mix,
            'target_actions': mode_target_actions,
            'target_curations': mode_target_curations,
            'draft_count': len(drafts),
            'top_candidate_id': top_candidate.get('candidate_id') if top_candidate else None,
        },
        'notes': 'Bookmarker execution plane consumes Main guidance but owns social writes.',
    }
    atomic_write_json(RUNTIME / 'social-execution-plan.json', social_plan)

    legacy_social_execution = read_json(RUNTIME / 'social-execution.json') or {
        'version': 'v2', 'agent': 'bookmarker', 'status': 'idle', 'generated_at': generated_at,
        'run_id': None, 'results': [], 'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
        'notes': 'awaiting bookmarker social execution worker'
    }
    social_result = dict(legacy_social_execution)
    social_result['result_kind'] = 'social-execution-result'
    social_result['executor'] = 'bookmarker'
    social_result['execution_owner'] = 'bookmarker'
    social_result['control_plane'] = 'main'
    social_result['source_class'] = 'bookmarker-execution-plane'
    social_result['control_ref'] = 'runtime/main/social-intent.json'
    social_result['guidance_ref'] = 'runtime/main/bookmarker-guidance.json'
    social_result['legacy_result_ref'] = 'runtime/bookmarker/social-execution.json'
    social_result['plan_ref'] = 'runtime/bookmarker/social-execution-plan.json'
    atomic_write_json(RUNTIME / 'social-execution-result.json', social_result)

    # latest.json envelope
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not (memory / 'x-sync-latest.json').exists():
        _wiki_me = read_json(WIKI_PLATFORM_RAW / 'me.json')
        _wiki_fallback_note = ''
        if _wiki_me:
            _me_data = _wiki_me.get('data') or {}
            _me_agent = _me_data.get('agent') or {}
            _wiki_vp = _me_agent.get('vp')
            _wiki_op = _me_agent.get('op')
            _wiki_fallback_note = f' (wiki fallback: vp={_wiki_vp}, op={_wiki_op})'
        warnings.append({'code': 'x_sync_missing', 'message': f'x-sync-latest.json missing — degraded to warning{_wiki_fallback_note}', 'severity': 'warning'})
    if source_health['mismatch']:
        warnings.append({'code': 'source_mismatch', 'severity': 'warning', 'message': 'derived topic artifacts exist while raw x-sync is not ok'})
    if x_sync_used_fallback:
        warnings.append({'code': 'x_sync_fallback_active', 'severity': 'warning', 'message': 'raw x-sync unavailable; archive fallback items are driving claim/draft generation'})

    recovery_ready = bool(x_sync_used_fallback and (wiki_delta_doc.get('claim_deltas') or []) and (publishable_claims_doc.get('claims') or []) and drafts)
    effective_latest_status = 'blocked' if blockers else (sync_status if sync_status != 'stale' else 'partial')
    if recovery_ready:
        effective_latest_status = 'ok'
    elif x_sync_used_fallback and ((wiki_delta_doc.get('claim_deltas') or []) or drafts):
        effective_latest_status = 'partial'

    latest = {
        'version': 'v2', 'agent': 'bookmarker',
        'run_id': f"bookmarker-v2-{datetime.now().strftime('%Y%m%dT%H%M%S')}",
        'status': effective_latest_status,
        'generated_at': generated_at,
        'data_window': {'start': generated_at, 'end': generated_at},
        'ttl_seconds': 14400, 'freshness_seconds': 0,
        'source_class': 'bookmarker-native',
        'inputs': {
            'x_sync': 'memory/x-sync-latest.json',
            'topic_extraction': 'memory/topic-extraction-latest.json',
            'topic_brief_payload': 'memory/topic-brief-payload.json',
            'tas_social_runtime': 'runtime/main/tas-social.json' if main_tas_social else None,
        },
        'outputs': {
            'content_urgency': urgency,
            'high_signal_count': int(high_signal_count),
            'source_health': {**source_health, 'fallback_used': bool(x_sync_used_fallback)},
            'tas_social': {'status': tas_social_status, 'value': tas_social_computed if tas_social_computed is not None else tas_value},
            'candidate_quality': {
                'top_candidate_id': top_candidate.get('candidate_id') if top_candidate else None,
                'top_candidate_uplift': top_candidate.get('expected_tas_social_uplift') if top_candidate else None,
                'recommended_action_mix': recommended_action_mix,
            },
            'strategy_loop': bookmarker_strategy_loop,
            'strategy_action': bookmarker_strategy_loop['strategy_action'],
            'planning_focus': bookmarker_strategy_loop['planning_focus'],
            'topic_brief_ref': 'runtime/bookmarker/topic-brief.json',
            'daily_theme_pack_ref': 'runtime/bookmarker/daily-theme-pack.json',
            'topic_performance_ref': 'runtime/bookmarker/topic-performance.json',
            'content_candidates_ref': 'runtime/bookmarker/content-candidates.json',
            'social_drafts_ref': 'runtime/bookmarker/social-drafts.json',
            'social_execution_plan_ref': 'runtime/bookmarker/social-execution-plan.json',
            'social_execution_result_ref': 'runtime/bookmarker/social-execution-result.json',
            'social_execution_ref': 'runtime/bookmarker/social-execution.json',
        },
        'blockers': blockers, 'warnings': warnings,
        'next_recommended_action': 'wait for social-intent from main or continue content sync',
        'meta': {
            'candidate_count': len(enriched_candidates),
            'selected_source': x_sync.get('source'),
            'x_sync_used_fallback': bool(x_sync_used_fallback),
            'recovery_ready': recovery_ready,
            'previous_run_id': previous_latest.get('run_id'),
            'top_candidate_id': top_candidate.get('candidate_id') if top_candidate else None,
        },
        # wiki-first fields (T2/T3 接入状态，供监控层读取)
        'wiki_brief_available': bool(_wiki_brief_doc),
        'wiki_top_theme': _wiki_top_theme_name or None,
        'wiki_trending_ticks': _wiki_trending_ticks or [],
        'wiki_platform_available': bool(read_json(WIKI_PLATFORM_RAW / 'manifest.json')),
    }
    atomic_write_json(RUNTIME / 'latest.json', latest)

    # ── Sync X data files to main workspace memory (single source of truth) ──
    # So dashboard server.py and any main-workspace scripts always read the freshest data.
    import shutil
    _x_files_to_sync = [
        'x-sync-latest.json',
        'x-latest-tweets.md',
        'x-tweets-state.json',
        'x-bookmarks-state.json',
        'x-bookmarks-categorized.md',
        'x-posts-archive.md',
    ]
    _main_memory = MAIN_ROOT / 'memory'
    _main_memory.mkdir(parents=True, exist_ok=True)
    _synced = []
    for _fname in _x_files_to_sync:
        _src = memory / _fname
        _dst = _main_memory / _fname
        if _src.exists():
            try:
                shutil.copy2(_src, _dst)
                _synced.append(_fname)
            except Exception:
                pass  # non-fatal: best-effort sync

    print(json.dumps({'status': latest['status'], 'source_class': 'bookmarker-native', 'outputs_written': 9,
                      'autonomy_mode': autonomy_mode, 'tas_social': tas_social_computed,
                      'community_source': community_source,
                      'x_sync_to_main': _synced}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

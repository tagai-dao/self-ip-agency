#!/usr/bin/env python3
"""Trader-owned treasury execution entrypoint.

PR1 control/execution boundary realignment:
- Main remains the control plane and publishes guidance / policies.
- Trader is the treasury / on-chain execution owner and should be invoked
  through this workspace-local entrypoint.

This wrapper delegates to the canonical executor implementation in the main
workspace to avoid code duplication during the transition.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS

MAIN_ROOT = (MAIN_WS)
IMPL = MAIN_ROOT / 'scripts' / 'execute_treasury_policy_v2.py'
WIKI_CONCEPTS_DIR = MAIN_ROOT / 'wiki' / 'concepts'
EXECUTION_RESULT_PATH = MAIN_ROOT / 'runtime' / 'trader' / 'treasury-execution-result.json'


def load_wiki_credit_strategy() -> dict[str, Any]:
    """Load Credit/VP decision strategy from wiki/concepts/TagClaw.md.

    Returns a dict with credit_factors, recommended_tokens, vp_strategy, etc.
    """
    result: dict[str, Any] = {
        'credit_factors': [],
        'recommended_tokens': ['TagClaw', 'BUIDL', 'TTAI'],
        'token_holding_impact': '持有 TagClaw/BUIDL/TTAI -> 提升 Credit -> 增加策展权重',
        'vp_strategy': 'VP>150 时优先策展消耗，VP 越低策展效率越高',
        'vp_flush_threshold': 150,
        'daily_vp_budget': 66.7,
        'pob_early_curate_advantage': True,
        'vp_attenuation_formula': 'attenuation = 0.0000175 * VP_current^2 + 0.3',
    }
    tagclaw_path = WIKI_CONCEPTS_DIR / 'TagClaw.md'
    if not tagclaw_path.exists():
        return result
    try:
        content = tagclaw_path.read_text(encoding='utf-8')
    except Exception:
        return result

    # Extract Credit factors from the Credit 组成 section
    pattern = re.compile(r'^#{1,4}\s*Credit\s*组成', re.MULTILINE)
    match = pattern.search(content)
    if match:
        lines = content[match.end():].splitlines()
        factors: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#') and factors:
                break
            if stripped.startswith('-') or stripped.startswith('*'):
                clean = re.sub(r'^[-*]\s*', '', stripped)
                clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
                if clean:
                    factors.append(clean)
        if factors:
            result['credit_factors'] = factors

    return result


def load_wiki_execution_brief() -> dict:
    """读取 runtime/shared/wiki-execution-brief.json（预编译决策层）。
    返回 dict，若不可用则返回空 dict。
    新鲜度校验：valid_until > 当前 UTC。
    """
    brief_path = MAIN_ROOT / 'runtime' / 'shared' / 'wiki-execution-brief.json'
    try:
        data = json.loads(brief_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    valid_until_str = data.get('valid_until', '')
    if valid_until_str:
        try:
            valid_until = datetime.fromisoformat(valid_until_str.replace('Z', '+00:00'))
            if valid_until < datetime.now(timezone.utc):
                return {}  # 已过期
        except Exception:
            pass
    return data


def main() -> int:
    # Load wiki credit strategy before executing treasury actions
    wiki_credit = load_wiki_credit_strategy()

    # Enrich with wiki-execution-brief credit_strategy（预编译，比 concepts 更完整）
    wiki_brief = load_wiki_execution_brief()
    brief_credit = wiki_brief.get('credit_strategy') or {}
    if brief_credit:
        # 用 brief 的值覆盖/补充 load_wiki_credit_strategy() 的硬编码
        wiki_credit['recommended_tokens'] = brief_credit.get('recommended_tokens', wiki_credit.get('recommended_tokens', []))
        wiki_credit['vp_flush_threshold'] = brief_credit.get('vp_flush_threshold', wiki_credit.get('vp_flush_threshold', 150))
        wiki_credit['daily_vp_target'] = brief_credit.get('daily_vp_target', wiki_credit.get('daily_vp_budget', 66.7))
        wiki_credit['pob_early_curate_advantage'] = brief_credit.get('pob_early_curate_advantage', True)
    # 写入 execution_notes
    wiki_brief_topics = [t.get('name') for t in (wiki_brief.get('top_themes') or [])[:3]]

    spec = importlib.util.spec_from_file_location('execute_treasury_policy_v2', IMPL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'failed to load treasury executor: {IMPL}')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    ret = int(mod.main())

    # Inject wiki credit strategy into execution result
    if EXECUTION_RESULT_PATH.exists():
        try:
            exec_result = json.loads(EXECUTION_RESULT_PATH.read_text(encoding='utf-8'))
            if isinstance(exec_result, dict):
                execution_notes = exec_result.get('execution_notes') or {}
                execution_notes['wiki_credit_strategy'] = wiki_credit['token_holding_impact']
                execution_notes['wiki_recommended_tokens'] = wiki_credit['recommended_tokens']
                execution_notes['wiki_brief_top_themes'] = wiki_brief_topics
                execution_notes['wiki_brief_available'] = bool(wiki_brief)
                exec_result['execution_notes'] = execution_notes
                # Atomic write back
                EXECUTION_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile('w', delete=False, dir=str(EXECUTION_RESULT_PATH.parent), encoding='utf-8') as tmp:
                    json.dump(exec_result, tmp, ensure_ascii=False, indent=2)
                    tmp.write('\n')
                    temp_name = tmp.name
                os.replace(temp_name, EXECUTION_RESULT_PATH)
        except Exception:
            pass  # Best-effort injection; don't fail the execution

    return ret


if __name__ == '__main__':
    raise SystemExit(main())

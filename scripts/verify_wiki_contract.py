#!/usr/bin/env python3
"""verify_wiki_contract.py — Wiki-runtime contract verifier.

Checks presence, freshness, and contract consistency of wiki source artifacts
and their derived runtime artifacts.

Usage:
    python3 scripts/verify_wiki_contract.py
    python3 scripts/verify_wiki_contract.py --json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent

CONTRACT: list[dict[str, Any]] = [
    {
        'name': 'topic-heatmap',
        'description': 'X interaction heatmap derived from raw interactions + tweet themes',
        'source': ['raw/x-interactions/', 'wiki/synthesis/tweets/'],
        'derived': 'runtime/bookmarker/topic-heatmap.json',
        'freshness_hours': None,
        'schema_checks': ['heatmap', 'community_fit_scores', 'schema_version'],
    },
    {
        'name': 'wiki-execution-brief',
        'description': 'Weekly execution brief compiled from concepts + heatmap',
        'source': ['wiki/concepts/', 'runtime/bookmarker/topic-heatmap.json'],
        'derived': 'runtime/shared/wiki-execution-brief.json',
        'freshness_hours': 168,
        'schema_checks': ['top_themes', 'schema'],
    },
    {
        'name': 'community-heat',
        'description': 'Community heat scores from trending ticks wiki pages',
        'source': ['wiki/tagclaw-platform/trending-ticks.md'],
        'derived': 'runtime/shared/community-heat.json',
        'freshness_hours': 48,
        'schema_checks': ['ticks', 'source_health'],
    },
    {
        'name': 'wiki-lint-status',
        'description': 'Wiki lint results from wiki_lint.py',
        'source': ['wiki/'],
        'derived': 'runtime/shared/wiki-lint-status.json',
        'freshness_hours': None,
        'schema_checks': [],
    },
    {
        'name': 'topic-registry',
        'description': 'Canonical topic/tick/concept registry',
        'source': [],
        'derived': 'config/wiki_topic_registry.json',
        'freshness_hours': None,
        'schema_checks': ['concepts', 'ticks', 'schema'],
    },
    {
        'name': 'x-tweets-raw',
        'description': 'Raw X tweets bootstrap from guided sync',
        'source': [],
        'derived': 'raw/x-tweets/',
        'freshness_hours': None,
        'schema_checks': [],
    },
    {
        'name': 'x-tweets-meta',
        'description': 'Raw X tweets bootstrap metadata',
        'source': ['raw/x-tweets/'],
        'derived': 'raw/x-tweets/_meta.json',
        'freshness_hours': None,
        'schema_checks': ['schema', 'status', 'discovery_method'],
    },
    {
        'name': 'x-tweets-wiki',
        'description': 'Wiki synthesis pages compiled from raw X tweets',
        'source': ['raw/x-tweets/tweets/'],
        'derived': 'wiki/synthesis/tweets/',
        'freshness_hours': None,
        'schema_checks': [],
    },
]


def check_presence(path: Path) -> tuple[bool, str]:
    if path.is_file():
        return True, 'file exists'
    if path.is_dir():
        children = list(path.iterdir())
        if children:
            return True, f'directory with {len(children)} entries'
        return False, 'directory exists but is empty'
    return False, 'not found'


def check_freshness(path: Path, max_hours: float) -> tuple[bool, str]:
    if not path.is_file():
        return False, 'file not found'
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = datetime.now(timezone.utc) - mtime
    age_hours = age.total_seconds() / 3600
    if age_hours <= max_hours:
        return True, f'age={age_hours:.1f}h (limit={max_hours}h)'
    return False, f'stale: age={age_hours:.1f}h > limit={max_hours}h'


def check_json_schema(path: Path, required_keys: list[str]) -> tuple[bool, str]:
    if not path.is_file():
        return False, 'file not found'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        return False, f'invalid JSON: {e}'
    missing = [k for k in required_keys if k not in data]
    if missing:
        return False, f'missing keys: {missing}'
    return True, f'all {len(required_keys)} keys present'


def check_registry_consistency() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        sys.path.insert(0, str(WORKSPACE / 'scripts'))
        from wiki_registry import validate_registry, resolve_concept, get_tracked_ticks
        issues = validate_registry()
        results.append({
            'check': 'registry-internal-consistency',
            'ok': len(issues) == 0,
            'detail': 'clean' if not issues else '; '.join(issues[:5]),
        })

        ticks = get_tracked_ticks()
        results.append({
            'check': 'tracked-ticks-non-empty',
            'ok': len(ticks) > 0,
            'detail': f'{len(ticks)} tracked ticks: {ticks}',
        })
    except Exception as e:
        results.append({
            'check': 'registry-import',
            'ok': False,
            'detail': str(e),
        })
    return results


def check_x_bootstrap_health() -> list[dict[str, Any]]:
    """Separate bootstrap health (handle + manifest) from feed parse health."""
    results: list[dict[str, Any]] = []
    identity_path = WORKSPACE / 'config' / 'agency-identity.json'
    handle = None
    if identity_path.is_file():
        try:
            data = json.loads(identity_path.read_text(encoding='utf-8'))
            handle = (data.get('owner') or {}).get('twitter_handle')
        except Exception:
            pass
    results.append({
        'check': 'x-bootstrap:owner-twitter-handle',
        'ok': bool(handle),
        'detail': f'handle={handle}' if handle else 'missing_owner_twitter_handle — X sync cannot proceed',
    })

    manifest_path = WORKSPACE / 'runtime' / 'shared' / 'guided-x-urls.json'
    results.append({
        'check': 'x-bootstrap:guided-manifest',
        'ok': manifest_path.is_file(),
        'detail': 'manifest exists' if manifest_path.is_file() else 'guided manifest not yet generated — run sync_guided_x_tweets.py or complete browser guidance',
    })

    meta_path = WORKSPACE / 'raw' / 'x-tweets' / '_meta.json'
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            status = meta.get('status', 'unknown')
            method = meta.get('discovery_method', 'unknown')
            results.append({
                'check': 'x-bootstrap:sync-status',
                'ok': status in ('ok', 'partial'),
                'detail': f'status={status} method={method}',
            })
        except Exception as e:
            results.append({
                'check': 'x-bootstrap:sync-status',
                'ok': False,
                'detail': f'invalid _meta.json: {e}',
            })
    else:
        results.append({
            'check': 'x-bootstrap:sync-status',
            'ok': False,
            'detail': 'no sync run yet — raw/x-tweets/_meta.json missing',
        })
    return results


def run_all_checks() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for entry in CONTRACT:
        name = entry['name']

        for src in entry.get('source', []):
            src_path = WORKSPACE / src
            ok, detail = check_presence(src_path)
            results.append({
                'check': f'{name}:source:{src}',
                'ok': ok,
                'detail': detail,
            })

        derived_path = WORKSPACE / entry['derived']
        ok, detail = check_presence(derived_path)
        results.append({
            'check': f'{name}:derived:{entry["derived"]}',
            'ok': ok,
            'detail': detail,
        })

        freshness_hours = entry.get('freshness_hours')
        if freshness_hours is not None and derived_path.is_file():
            ok, detail = check_freshness(derived_path, freshness_hours)
            results.append({
                'check': f'{name}:freshness',
                'ok': ok,
                'detail': detail,
            })

        schema_checks = entry.get('schema_checks', [])
        if schema_checks and derived_path.is_file():
            ok, detail = check_json_schema(derived_path, schema_checks)
            results.append({
                'check': f'{name}:schema',
                'ok': ok,
                'detail': detail,
            })

    results.extend(check_registry_consistency())
    results.extend(check_x_bootstrap_health())

    pass_count = sum(1 for r in results if r['ok'])
    fail_count = sum(1 for r in results if not r['ok'])

    return {
        'verified_at': now.isoformat(timespec='seconds'),
        'schema': 'wiki-runtime-contract-v1',
        'pass': pass_count,
        'fail': fail_count,
        'status': 'ok' if fail_count == 0 else 'degraded',
        'checks': results,
    }


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=str(path.parent), suffix='.tmp',
                                      delete=False, encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
        tmp = f.name
    os.replace(tmp, str(path))


def main() -> int:
    report = run_all_checks()
    json_mode = '--json' in sys.argv

    out_path = WORKSPACE / 'runtime' / 'shared' / 'wiki-contract-verify.json'
    atomic_write_json(out_path, report)

    if json_mode:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Wiki-Runtime Contract Verification: {report['status'].upper()}")
        print(f"  PASS: {report['pass']}  FAIL: {report['fail']}")
        if report['fail'] > 0:
            print("\nFailed checks:")
            for r in report['checks']:
                if not r['ok']:
                    print(f"  x {r['check']}: {r['detail']}")

    return 0 if report['fail'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""verify_wiki_runtime_contract_v1.py — Wiki-runtime contract verifier.

Checks presence, freshness, and contract consistency of wiki source artifacts
and their derived runtime artifacts. Designed to prevent silent staleness drift
(e.g. the community_heat incident).

Usage:
    python3 scripts/verify_wiki_runtime_contract_v1.py
    python3 scripts/verify_wiki_runtime_contract_v1.py --json  # machine-readable output

Exit codes:
    0 = all checks pass
    1 = at least one check failed
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS

WORKSPACE = (MAIN_WS)

# Import shared wiki event helper
sys.path.insert(0, str(WORKSPACE / 'scripts'))
try:
    from runtime_utils_v2 import append_wiki_event
except Exception:
    def append_wiki_event(*a: Any, **kw: Any) -> None:  # type: ignore[misc]
        pass

# ── Contract definition ──
# Each entry: (check_name, source_path, derived_path, max_staleness_hours | None)
# max_staleness_hours=None means presence-only check (no freshness requirement).
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
        'name': 'onchain-ticks-index',
        'description': 'Onchain ticks index compiled from raw trade data',
        'source': ['raw/onchain-token-transation/'],
        'derived': 'wiki/onchain-ticks/INDEX.json',
        'freshness_hours': None,
        'schema_checks': ['ticks', 'tick_count'],
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
        'name': 'wiki-events-ledger',
        'description': 'Append-only wiki events ledger (JSONL)',
        'source': [],
        'derived': 'runtime/shared/wiki-events.jsonl',
        'freshness_hours': None,
        'schema_checks': [],
    },
    {
        'name': 'decision-index',
        'description': 'Decision-memory ledger compiled from agent decision trails',
        'source': ['runtime/shared/strategy-ledger.jsonl', 'runtime/bookmarker/planned-action-log.jsonl'],
        'derived': 'runtime/shared/decision-index.json',
        'freshness_hours': 48,
        'schema_checks': ['decisions', 'count', 'schema'],
    },
]


def check_presence(path: Path) -> tuple[bool, str]:
    """Check if a path exists (file or non-empty directory)."""
    if path.is_file():
        return True, 'file exists'
    if path.is_dir():
        children = list(path.iterdir())
        if children:
            return True, f'directory with {len(children)} entries'
        return False, 'directory exists but is empty'
    return False, 'not found'


def check_freshness(path: Path, max_hours: float) -> tuple[bool, str]:
    """Check if a file was modified within max_hours."""
    if not path.is_file():
        return False, 'file not found'
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = datetime.now(timezone.utc) - mtime
    age_hours = age.total_seconds() / 3600
    if age_hours <= max_hours:
        return True, f'age={age_hours:.1f}h (limit={max_hours}h)'
    return False, f'stale: age={age_hours:.1f}h > limit={max_hours}h'


def check_json_schema(path: Path, required_keys: list[str]) -> tuple[bool, str]:
    """Check if a JSON file contains required top-level keys."""
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


def check_provenance_sidecar(derived_path: str, name: str) -> list[dict[str, Any]]:
    """Check that a provenance sidecar exists and has the expected shape."""
    results: list[dict[str, Any]] = []
    dp = WORKSPACE / derived_path
    sidecar_path = dp.parent / f"{dp.name}.provenance.json"
    if not sidecar_path.is_file():
        results.append({
            'check': f'{name}:provenance-sidecar-exists',
            'ok': False,
            'detail': f'{sidecar_path.name} not found',
        })
        return results
    results.append({
        'check': f'{name}:provenance-sidecar-exists',
        'ok': True,
        'detail': f'{sidecar_path.name} present',
    })
    try:
        data = json.loads(sidecar_path.read_text(encoding='utf-8'))
    except Exception as e:
        results.append({
            'check': f'{name}:provenance-sidecar-valid-json',
            'ok': False,
            'detail': str(e),
        })
        return results
    required = {'schema', 'artifact_ref', 'generated_at', 'producer'}
    missing = required - set(data.keys())
    results.append({
        'check': f'{name}:provenance-sidecar-schema',
        'ok': len(missing) == 0,
        'detail': f'all required keys present' if not missing else f'missing: {sorted(missing)}',
    })
    if data.get('schema') != 'provenance-sidecar-v1':
        results.append({
            'check': f'{name}:provenance-sidecar-version',
            'ok': False,
            'detail': f"schema={data.get('schema')!r}, expected 'provenance-sidecar-v1'",
        })
    else:
        results.append({
            'check': f'{name}:provenance-sidecar-version',
            'ok': True,
            'detail': 'provenance-sidecar-v1',
        })
    return results


# Artifacts that should have provenance sidecars
PROVENANCE_ARTIFACTS = [
    ('runtime/shared/wiki-execution-brief.json', 'wiki-execution-brief'),
    ('runtime/bookmarker/topic-heatmap.json', 'topic-heatmap'),
    ('runtime/shared/community-heat.json', 'community-heat'),
]


def check_registry_consistency() -> list[dict[str, Any]]:
    """Validate registry internal consistency."""
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

        # Verify alias resolution works for known cases
        test_cases = [
            ('AgentInfrastructure', 'ATOC'),
            ('AgentSwarm', 'ATOC'),
            ('desoc-agent', 'DeSoc'),
            ('token-economy', 'TokenEconomy'),
            ('atoc-agent', 'ATOC'),
            ('general-builder', 'BuilderLife'),
            ('TagClaw', 'TagClaw'),
        ]
        for input_name, expected in test_cases:
            actual = resolve_concept(input_name)
            results.append({
                'check': f'alias-resolve:{input_name}',
                'ok': actual == expected,
                'detail': f'{input_name} → {actual} (expected {expected})',
            })

        # Verify tracked ticks is non-empty
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


def check_cross_artifact_consistency() -> list[dict[str, Any]]:
    """Cross-artifact consistency checks: ensure derived artifacts agree with the registry."""
    results: list[dict[str, Any]] = []
    try:
        sys.path.insert(0, str(WORKSPACE / 'scripts'))
        from wiki_registry import resolve_concept, get_tracked_ticks, get_all_concepts

        all_concepts = set(get_all_concepts())
        tracked_ticks = set(get_tracked_ticks())

        # 1. Execution brief top_themes must resolve via registry
        brief_path = WORKSPACE / 'runtime' / 'shared' / 'wiki-execution-brief.json'
        if brief_path.is_file():
            brief = json.loads(brief_path.read_text(encoding='utf-8'))
            themes = brief.get('top_themes', [])
            unresolved = []
            for th in themes:
                name = th.get('name', '')
                canonical = resolve_concept(name)
                # If resolve returns the input unchanged AND it's not in concepts, it's unresolved
                if canonical == name and name not in all_concepts:
                    unresolved.append(name)
            results.append({
                'check': 'cross:brief-themes-resolvable',
                'ok': len(unresolved) == 0,
                'detail': 'all themes resolve' if not unresolved else f'unresolved: {unresolved}',
            })
        else:
            results.append({
                'check': 'cross:brief-themes-resolvable',
                'ok': False,
                'detail': 'execution brief not found',
            })

        # 2. Community heat tracked ticks must match registry tracked ticks
        heat_path = WORKSPACE / 'runtime' / 'shared' / 'community-heat.json'
        if heat_path.is_file():
            heat = json.loads(heat_path.read_text(encoding='utf-8'))
            heat_ticks = set(heat.get('ticks', {}).keys())
            missing_in_heat = tracked_ticks - heat_ticks
            extra_in_heat = heat_ticks - tracked_ticks
            ok = len(missing_in_heat) == 0 and len(extra_in_heat) == 0
            detail = 'ticks match registry'
            if not ok:
                parts = []
                if missing_in_heat:
                    parts.append(f'missing from heat: {sorted(missing_in_heat)}')
                if extra_in_heat:
                    parts.append(f'extra in heat: {sorted(extra_in_heat)}')
                detail = '; '.join(parts)
            results.append({
                'check': 'cross:heat-ticks-match-registry',
                'ok': ok,
                'detail': detail,
            })
        else:
            results.append({
                'check': 'cross:heat-ticks-match-registry',
                'ok': False,
                'detail': 'community-heat.json not found',
            })

        # 3. Heatmap themes should be resolvable concepts
        # Heatmap structure: heatmap.{timeframe}.{theme} — timeframe keys (1m, 6m, 5y) are not concepts
        heatmap_path = WORKSPACE / 'runtime' / 'bookmarker' / 'topic-heatmap.json'
        if heatmap_path.is_file():
            heatmap = json.loads(heatmap_path.read_text(encoding='utf-8'))
            hm_data = heatmap.get('heatmap', {})
            # Collect theme names from all timeframe buckets
            theme_names: set[str] = set()
            for bucket_val in hm_data.values():
                if isinstance(bucket_val, dict):
                    theme_names.update(bucket_val.keys())
            unresolved_hm = []
            for theme_name in sorted(theme_names)[:30]:
                canonical = resolve_concept(theme_name)
                if canonical == theme_name and theme_name not in all_concepts:
                    unresolved_hm.append(theme_name)
            results.append({
                'check': 'cross:heatmap-themes-resolvable',
                'ok': len(unresolved_hm) == 0,
                'detail': 'all heatmap themes resolve' if not unresolved_hm
                          else f'{len(unresolved_hm)} unresolved: {unresolved_hm[:5]}',
            })

    except Exception as e:
        results.append({
            'check': 'cross:import-error',
            'ok': False,
            'detail': str(e),
        })
    return results


def run_all_checks() -> dict[str, Any]:
    """Run all contract checks, return structured report."""
    results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for entry in CONTRACT:
        name = entry['name']

        # Source presence
        for src in entry.get('source', []):
            src_path = WORKSPACE / src
            ok, detail = check_presence(src_path)
            results.append({
                'check': f'{name}:source:{src}',
                'ok': ok,
                'detail': detail,
            })

        # Derived presence
        derived_path = WORKSPACE / entry['derived']
        ok, detail = check_presence(derived_path)
        results.append({
            'check': f'{name}:derived:{entry["derived"]}',
            'ok': ok,
            'detail': detail,
        })

        # Freshness
        freshness_hours = entry.get('freshness_hours')
        if freshness_hours is not None and derived_path.is_file():
            ok, detail = check_freshness(derived_path, freshness_hours)
            results.append({
                'check': f'{name}:freshness',
                'ok': ok,
                'detail': detail,
            })

        # Schema
        schema_checks = entry.get('schema_checks', [])
        if schema_checks and derived_path.is_file():
            ok, detail = check_json_schema(derived_path, schema_checks)
            results.append({
                'check': f'{name}:schema',
                'ok': ok,
                'detail': detail,
            })

    # Provenance sidecar checks
    for derived, name in PROVENANCE_ARTIFACTS:
        results.extend(check_provenance_sidecar(derived, name))

    # Registry-specific checks
    results.extend(check_registry_consistency())

    # Cross-artifact consistency checks
    results.extend(check_cross_artifact_consistency())

    pass_count = sum(1 for r in results if r['ok'])
    fail_count = sum(1 for r in results if not r['ok'])

    report = {
        'verified_at': now.isoformat(timespec='seconds'),
        'schema': 'wiki-runtime-contract-v1',
        'pass': pass_count,
        'fail': fail_count,
        'status': 'ok' if fail_count == 0 else 'degraded',
        'checks': results,
    }
    return report


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=str(path.parent), suffix='.tmp',
                                      delete=False, encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
        tmp = f.name
    os.replace(tmp, str(path))


def build_alert_artifact(report: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic alert artifact from verifier results.

    Emitted to runtime/shared/wiki-contract-alert.json on every run.
    When status is 'ok', severity is 'clear'. When 'degraded', severity
    is derived from fail count: warning (1-3 failures) or critical (4+).
    """
    status = report.get('status', 'unknown')
    fail_count = report.get('fail', 0)
    pass_count = report.get('pass', 0)

    if status == 'ok':
        severity = 'clear'
    elif fail_count >= 4:
        severity = 'critical'
    else:
        severity = 'warning'

    failing_checks = [
        c['check'] for c in report.get('checks', []) if not c.get('ok')
    ]

    return {
        'schema': 'wiki-contract-alert-v1',
        'status': status,
        'severity': severity,
        'pass': pass_count,
        'fail': fail_count,
        'verified_at': report.get('verified_at'),
        'failing_checks': failing_checks[:10],
        'message': f'Wiki contract {status}: {pass_count} pass, {fail_count} fail'
                   + (f' — top failures: {", ".join(failing_checks[:3])}' if failing_checks else ''),
    }


def main() -> int:
    report = run_all_checks()

    json_mode = '--json' in sys.argv

    # Always write machine-readable output
    out_path = WORKSPACE / 'runtime' / 'shared' / 'wiki-contract-verify.json'
    atomic_write_json(out_path, report)

    # Always write alert artifact (deterministic degraded signal)
    alert = build_alert_artifact(report)
    alert_path = WORKSPACE / 'runtime' / 'shared' / 'wiki-contract-alert.json'
    atomic_write_json(alert_path, alert)

    # Emit wiki event
    append_wiki_event(
        event_type='contract_verify',
        producer='verify_wiki_runtime_contract_v1',
        artifact='runtime/shared/wiki-contract-verify.json',
        status=report.get('status', 'unknown'),
        summary=f"pass={report['pass']} fail={report['fail']} severity={alert['severity']}",
        detail={'pass': report['pass'], 'fail': report['fail'], 'severity': alert['severity']},
    )

    if json_mode:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Wiki-Runtime Contract Verification: {report['status'].upper()}")
        print(f"  PASS: {report['pass']}  FAIL: {report['fail']}")
        if report['fail'] > 0:
            print("\nFailed checks:")
            for r in report['checks']:
                if not r['ok']:
                    print(f"  ✗ {r['check']}: {r['detail']}")
        severity_label = alert['severity'].upper()
        print(f"\nAlert artifact: {alert_path.name} [severity={severity_label}]")

    return 0 if report['fail'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Mirror canonical TAS_social into runtime/main for compatibility.

Canonical source of truth:
- runtime/bookmarker/tas-social.json

Compatibility mirror:
- runtime/main/tas-social.json

Legacy fallback remains available through memory/tas-social-latest.json only when the
canonical runtime file is unavailable.
"""

from __future__ import annotations

import copy
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS

ROOT = (MAIN_WS)
MEMORY = ROOT / 'memory'
RUNTIME = ROOT / 'runtime'


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


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
    Path(temp_name).replace(path)


def _notes_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if value is None:
        return []
    return [str(value)]


def main() -> int:
    canonical = read_json(RUNTIME / 'bookmarker' / 'tas-social.json') or {}
    legacy = read_json(MEMORY / 'tas-social-latest.json') or {}

    using_canonical = canonical.get('value') is not None
    source = canonical if using_canonical else legacy
    source_agent = 'bookmarker' if using_canonical else 'legacy-main'
    source_ref = 'runtime/bookmarker/tas-social.json' if using_canonical else 'memory/tas-social-latest.json'

    out = copy.deepcopy(source) if isinstance(source, dict) else {}
    out['metric'] = 'TAS_social'
    out['source_agent'] = source_agent
    out['source_class'] = 'main-runtime-handoff'
    out['canonical_source_ref'] = 'runtime/bookmarker/tas-social.json'
    out['mirrored_from'] = source_ref
    out['legacy_source_ref'] = 'memory/tas-social-latest.json'
    out['updated_at'] = now_iso()
    out.setdefault('status', 'stale')
    out.setdefault('value', None)
    out.setdefault('formula', canonical.get('formula') if using_canonical else source.get('formula'))

    # Normalize timestamp fields for consumers that still expect main-style metadata.
    if using_canonical:
        out['computed_at'] = (
            canonical.get('updated_at')
            or canonical.get('computed_at')
            or now_iso()
        )
    else:
        out['computed_at'] = source.get('computed_at') or now_iso()

    notes = _notes_list(out.get('notes'))
    if using_canonical:
        notes.append('runtime/main mirror of canonical runtime/bookmarker/tas-social.json')
    else:
        notes.append('runtime/main fell back to legacy memory/tas-social-latest.json because canonical runtime source was unavailable')
    out['notes'] = notes

    target = RUNTIME / 'main' / 'tas-social.json'
    atomic_write_json(target, out)
    print(json.dumps({
        'status': out['status'],
        'source_agent': source_agent,
        'mirrored_from': source_ref,
        'path': str(target),
        'value': out.get('value'),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

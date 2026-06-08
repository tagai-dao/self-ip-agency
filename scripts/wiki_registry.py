#!/usr/bin/env python3
"""wiki_registry.py — Shared canonical topic/tick/concept resolver for self-IP LLM Wiki.

All wiki pipeline scripts should use this module instead of maintaining local alias maps.
The registry is loaded from config/wiki_topic_registry.json.

Usage:
    from wiki_registry import resolve_concept, get_tracked_ticks, get_concept_aliases

    canonical = resolve_concept("AgentInfrastructure")  # → "ATOC"
    ticks = get_tracked_ticks()                          # → ["TagClaw", "BUIDL", ...]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parent.parent)
REGISTRY_PATH = WORKSPACE / 'config' / 'wiki_topic_registry.json'

_registry_cache: dict[str, Any] | None = None
_alias_index: dict[str, str] | None = None


def _load_registry() -> dict[str, Any]:
    """Load and cache the canonical registry."""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    if not REGISTRY_PATH.exists():
        _registry_cache = {'concepts': {}, 'ticks': {}}
        return _registry_cache
    _registry_cache = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    return _registry_cache


def _build_alias_index() -> dict[str, str]:
    """Build a case-insensitive alias → canonical_name index."""
    global _alias_index
    if _alias_index is not None:
        return _alias_index
    reg = _load_registry()
    index: dict[str, str] = {}
    for canonical, meta in reg.get('concepts', {}).items():
        index[canonical.lower()] = canonical
        for alias in meta.get('aliases', []):
            index[alias.lower()] = canonical
    _alias_index = index
    return _alias_index


def resolve_concept(name: str) -> str:
    """Resolve a topic/concept name to its canonical form.

    Returns the canonical name if found in the registry (exact or alias match).
    Returns the input unchanged if not found — no fabrication.
    """
    idx = _build_alias_index()
    return idx.get(name.lower(), name)


def get_concept_aliases() -> dict[str, str]:
    """Return a flat alias → canonical_name dict (compatible with legacy CONCEPT_ALIAS format)."""
    reg = _load_registry()
    aliases: dict[str, str] = {}
    for canonical, meta in reg.get('concepts', {}).items():
        for alias in meta.get('aliases', []):
            aliases[alias] = canonical
    return aliases


def get_tracked_ticks() -> list[str]:
    """Return the list of tracked tick names from the registry."""
    reg = _load_registry()
    return [
        name for name, meta in reg.get('ticks', {}).items()
        if meta.get('tracked', False)
    ]


def get_all_ticks() -> list[str]:
    """Return all tick names from the registry."""
    reg = _load_registry()
    return list(reg.get('ticks', {}).keys())


def get_concept_wiki_file(concept_name: str) -> str | None:
    """Return the wiki file path for a concept, or None."""
    reg = _load_registry()
    canonical = resolve_concept(concept_name)
    meta = reg.get('concepts', {}).get(canonical)
    if meta:
        return meta.get('wiki_file')
    return None


def get_all_concepts() -> list[str]:
    """Return all canonical concept names."""
    reg = _load_registry()
    return list(reg.get('concepts', {}).keys())


def get_concept_category(concept_name: str) -> str | None:
    """Return the category for a concept."""
    reg = _load_registry()
    canonical = resolve_concept(concept_name)
    meta = reg.get('concepts', {}).get(canonical)
    if meta:
        return meta.get('category')
    return None


def reload_registry() -> None:
    """Force reload of the registry (useful after edits)."""
    global _registry_cache, _alias_index
    _registry_cache = None
    _alias_index = None


def validate_registry() -> list[str]:
    """Validate registry integrity. Returns list of issues (empty = ok)."""
    issues: list[str] = []
    reg = _load_registry()

    # Check concepts
    concepts = reg.get('concepts', {})
    seen_aliases: dict[str, str] = {}
    for canonical, meta in concepts.items():
        if meta.get('canonical_name') != canonical:
            issues.append(f"concept {canonical}: canonical_name mismatch ({meta.get('canonical_name')})")
        wiki_file = meta.get('wiki_file')
        if wiki_file and not (WORKSPACE / wiki_file).exists():
            issues.append(f"concept {canonical}: wiki_file {wiki_file} does not exist")
        for alias in meta.get('aliases', []):
            if alias.lower() in seen_aliases:
                issues.append(f"concept {canonical}: alias '{alias}' conflicts with {seen_aliases[alias.lower()]}")
            seen_aliases[alias.lower()] = canonical

    # Check ticks
    ticks = reg.get('ticks', {})
    for tick_name, meta in ticks.items():
        if meta.get('canonical_name') != tick_name:
            issues.append(f"tick {tick_name}: canonical_name mismatch ({meta.get('canonical_name')})")
        wiki_file = meta.get('wiki_file')
        if wiki_file and not (WORKSPACE / wiki_file).exists():
            issues.append(f"tick {tick_name}: wiki_file {wiki_file} does not exist")

    return issues

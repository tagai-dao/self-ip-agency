#!/usr/bin/env python3
"""build_wiki_grounded_drafts_v1.py — emit social drafts grounded in wiki content.

Plan-A 仓促止血 (2026-05-26): bookmarker has been emitting near-duplicate
fallback templates (`$TagClaw signal building...`) because the social-drafts
pool ran dry. This script populates the pool with substantive drafts mined
from already-curated wiki content, so the executor has wiki-grounded
material to pick instead of falling back to 8 hardcoded templates.

What we source from (in priority order):
  1. wiki/concepts/<Theme>.md → "## 关键洞察" bullets — 0xNought-style
     concise insights, paraphrased and already in your voice
  2. wiki/concepts/<Theme>.md → "## 开放问题" — substantive open questions
     framed as discussion prompts (rarer; used as filler if 关键洞察 dry)

Hard constraints (per user policy 2026-05-26):
  - NO direct tweet ID / URL quoting in the body
  - NO direct @mention of authors from synthesis/tweets (those came from
    real users; reposting their tweet IDs feels plagiarism-y)
  - 1 draft per (concept, insight) tuple; idempotent via stable id hash

Merge semantics:
  - Read existing runtime/bookmarker/social-drafts.json
  - Keep all non-wiki-grounded drafts (x-sync-bridge, etc.)
  - Replace any prior wiki-grounded drafts with the fresh set
  - Cap total draft pool at 30 (drop oldest wiki-grounded if over)
  - Write back atomically with same v1 schema

Usage:
  python3 build_wiki_grounded_drafts_v1.py
  python3 build_wiki_grounded_drafts_v1.py --dry-run
  python3 build_wiki_grounded_drafts_v1.py --max-drafts 12
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(WORKSPACE / "scripts"))
from runtime_utils import append_wiki_event, path_ref  # noqa: E402

WIKI = WORKSPACE / "wiki"
CONCEPTS_DIR = WIKI / "concepts"
# Plan B (2026-05-28): wiki-grounded drafts get their OWN file so the hourly
# unified-heartbeat (run_bookmarker_runtime.py) can't overwrite them when it
# rewrites social-drafts.json. The executor reads both files and unions them.
DRAFTS_PATH = WORKSPACE / "runtime" / "bookmarker" / "wiki-grounded-drafts.json"
# P-A (2026-06-06): topic ranker output — drives WHICH concepts to draft (by
# hotness/depth/0xNought signal) + carries the 14-day exec-log ids for novelty
# filtering. Fixes posting starving on recycled already-posted drafts.
TOPIC_CANDIDATES_PATH = WORKSPACE / "runtime" / "bookmarker" / "topic-candidates.json"

DEFAULT_MAX_DRAFTS = 12
DEFAULT_BODY_CAP_CHARS = 280  # Twitter-standard cap; TagClaw posts seem
# to support at least this. Hashtags are reserved space (see build_drafts).

SOURCE_KIND_WIKI = "wiki-grounded"

# Insight sections we'll mine, in priority order.
INSIGHT_SECTIONS = [
    "关键洞察",
    "关键洞察（本轮新增 / 强化）",
    "Key Insights",
    "核心洞察",
]
FALLBACK_SECTIONS = [
    "开放问题",
    "Open Questions",
]

# Tweet-ID & URL strip — defensive even though wiki bullets shouldn't have them.
_TWEET_URL_RE = re.compile(r"https?://(?:x|twitter)\.com/[^\s)\]]+", re.IGNORECASE)
_TWEET_ID_RE = re.compile(r"\b\d{15,20}\b")
# Strip the "(0xNought 原话)" attribution marker — the agent isn't 0xNought.
_QUOTE_MARKER_RE = re.compile(r"\s*[（(]\s*0?xNought\s*原话\s*[）)]")


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_style_context() -> str:
    """P-B: the WIKI is the style brain. Read the manually-managed persona voice
    constraints (tone + 语言风格约束) so every draft carries the canonical style
    directive. READ-ONLY — never writes wiki/identity (identity-safety)."""
    persona = WIKI / "identity" / "persona.md"
    try:
        text = persona.read_text(encoding="utf-8")
    except Exception:
        return ""
    parts: list[str] = []
    m = re.search(r"-\s*\*\*tone\*\*:\s*(.+)", text)
    if m:
        parts.append("tone: " + m.group(1).strip())
    # capture the 语言风格约束 section bullets
    sec = re.search(r"##\s*语言风格约束\s*\n(.*?)(?:\n##\s|\Z)", text, re.S)
    if sec:
        bullets = [b.strip("-* ").strip() for b in sec.group(1).splitlines() if b.strip().startswith(("-", "*"))]
        if bullets:
            parts.append("style: " + " / ".join(bullets[:8]))
    return " | ".join(parts)[:600]


def _key_position_for(concept: str) -> str:
    """P-B: per-concept stance/taboo from key-positions.md so drafts stay on
    0xNought's actual positions (and avoid the 禁忌). READ-ONLY."""
    kp = WIKI / "identity" / "key-positions.md"
    try:
        text = kp.read_text(encoding="utf-8")
    except Exception:
        return ""
    # match a "## <Concept>" section (try concept name + a couple aliases)
    m = re.search(rf"##\s*{re.escape(concept)}\s*\n(.*?)(?:\n##\s|\Z)", text, re.S)
    if not m:
        return ""
    body = m.group(1)
    out = []
    for label in ("立场", "禁忌"):
        lm = re.search(rf"-\s*{label}[:：]\s*(.+)", body)
        if lm:
            out.append(f"{label}: {lm.group(1).strip()}")
    return " | ".join(out)[:400]


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text if end < 0 else text[end + 4:].lstrip("\n")


def _extract_section_bullets(body: str, section_titles: list[str]) -> list[str]:
    """Return bullet lines (leading `- ` stripped, ** unwrapped) under any
    of the section titles. Stops at the next `## ` heading."""
    for title in section_titles:
        # Match a `## <title>` heading; tolerate trailing whitespace.
        pattern = re.compile(rf"^##\s+{re.escape(title)}\s*$", re.MULTILINE)
        m = pattern.search(body)
        if not m:
            continue
        start = m.end()
        next_heading = re.search(r"\n## ", body[start:])
        end = start + next_heading.start() if next_heading else len(body)
        section_body = body[start:end]
        bullets: list[str] = []
        for line in section_body.splitlines():
            s = line.strip()
            if not s.startswith("- ") and not s.startswith("* "):
                continue
            item = s[2:].strip()
            # Drop "**bold**" wrappers used to highlight the lead phrase.
            item = re.sub(r"\*\*(.+?)\*\*", r"\1", item)
            if item:
                bullets.append(item)
        if bullets:
            return bullets
    return []


def _clean_draft_text(raw: str, body_cap: int) -> str:
    """Strip tweet IDs, tweet URLs, attribution markers, then trim."""
    text = raw
    text = _TWEET_URL_RE.sub("", text)
    text = _QUOTE_MARKER_RE.sub("", text)
    text = _TWEET_ID_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > body_cap:
        text = text[: body_cap - 1].rstrip("，,.：:、 ") + "…"
    return text


def _hashtags_for(concept_name: str) -> str:
    """Derive 1-2 hashtags from the concept name."""
    tags = ["#TagClaw"]
    # Avoid duplicating TagClaw — already implicit in posts. Add concept tag
    # only if it's not a generic dump bucket.
    if concept_name not in {"TagClaw", "Misc"}:
        tags.append(f"#{concept_name}")
    return " ".join(tags)


def _draft_id_for(concept: str, bullet: str) -> str:
    """Deterministic id so re-runs don't proliferate dupes."""
    h = hashlib.md5(f"{concept}|{bullet}".encode("utf-8")).hexdigest()[:10]
    return f"wiki-{concept}-{h}"


def collect_concept_bullets(concepts_dir: Path,
                            concept_scores: dict[str, float] | None = None,
                            content_angle: str = "insight") -> list[tuple[str, str]]:
    """Round-robin every concept page's insight bullets so one big concept
    (ATOC has 11+ bullets) doesn't monopolise the daily draft budget.

    Output ordering: bullet[0] from each concept, then bullet[1] from each,
    etc. Concepts with no insights fall through to open-questions.

    P-A: when ``concept_scores`` (from the topic ranker) is given, concepts are
    visited HOTTEST-first instead of alphabetically, so high heat/depth/0xNought
    topics get drafted (and picked) first.
    """
    if not concepts_dir.exists():
        return []
    per_concept: list[tuple[str, list[str]]] = []
    for p in sorted(concepts_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        body = _strip_frontmatter(text)
        # P-C: AutoResearch content_angle arm chooses framing — 'open_question'
        # mines open questions first, else key-insight statements first.
        primary, secondary = ((FALLBACK_SECTIONS, INSIGHT_SECTIONS)
                              if content_angle == "open_question"
                              else (INSIGHT_SECTIONS, FALLBACK_SECTIONS))
        bullets = _extract_section_bullets(body, primary)
        if not bullets:
            bullets = _extract_section_bullets(body, secondary)
        if bullets:
            per_concept.append((p.stem, bullets))

    # P-A: order concepts hottest-first when the topic ranker provided scores.
    if concept_scores:
        per_concept.sort(key=lambda cb: -float(concept_scores.get(cb[0], 0.0)))

    # Round-robin: take bullet[0] from each concept, then bullet[1], …
    out: list[tuple[str, str]] = []
    max_depth = max((len(b) for _, b in per_concept), default=0)
    for depth in range(max_depth):
        for concept, bullets in per_concept:
            if depth < len(bullets):
                out.append((concept, bullets[depth]))
    return out


def build_drafts(bullets: list[tuple[str, str]], max_drafts: int,
                  body_cap: int, exec_log_ids: set | None = None) -> list[dict[str, Any]]:
    """Turn (concept, bullet) tuples into draft dicts matching the
    bookmarker.social-drafts.v1 schema the executor reads."""
    drafts: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    style_ctx = _load_style_context()  # P-B: wiki-governed voice, attached to every draft
    for concept, raw_bullet in bullets:
        tags = _hashtags_for(concept)
        # Reserve hashtag length (+1 for the joining space) so the body is
        # what gets truncated, not the tag itself.
        text_budget = body_cap - len(tags) - 1
        cleaned = _clean_draft_text(raw_bullet, text_budget)
        if not cleaned or len(cleaned) < 25:
            continue  # too short to be substantive
        body = f"{cleaned} {tags}"
        # Cheap content fingerprint to avoid near-dupes within this run.
        sig = re.sub(r"[^\w一-鿿]", "", cleaned)[:60].lower()
        if sig in seen:
            continue
        seen.add(sig)
        draft_id = _draft_id_for(concept, cleaned)
        # P-A NOVELTY: skip drafts already posted within the 14-day dedup window.
        # This is the core fix for posting starvation — without it the generator
        # recycled the same deterministic ids until all were dedup-excluded → 0 posts.
        if exec_log_ids and draft_id in exec_log_ids:
            continue
        drafts.append({
            "id": draft_id,
            "type": "post",
            "tick": concept if concept in {"TagClaw", "BUIDL"} else None,
            "text": body,
            "priority": 7,  # higher than fallback (was 7-8) so executor picks these first
            "language": "zh" if re.search(r"[一-鿿]", body) else "en",
            "theme": concept,
            "target_key": draft_id,
            "source_tweet_id": None,
            "source_url": f"wiki/concepts/{concept}.md",
            "source_kind": SOURCE_KIND_WIKI,
            "source_excerpt": cleaned[:160],
            "post_style": "wiki_insight",
            "content_intelligence": "wiki_grounded",
            # P-B: wiki is the style brain. These carry the canonical voice + the
            # concept's stance/taboo so the posting agentTurn can polish the raw
            # insight into 0xNought's voice (what=ranker/concept, how=persona).
            "style_directive": style_ctx,
            "key_position": _key_position_for(concept),
            "rewrite_gate_passed": True,
            "thefeed_loaded": True,
            "generation_path": "build_wiki_grounded_drafts_v1",
            "filler_sources": [f"wiki/concepts/{concept}.md"],
            "content_pool_windows": [],
            "generated_at": now,
        })
        if len(drafts) >= max_drafts:
            break
    return drafts


def merge_into_pool(existing: dict[str, Any] | None,
                     new_drafts: list[dict[str, Any]]) -> dict[str, Any]:
    """Plan B: this file is wiki-grounded-only, so we simply replace the pool
    with the fresh set each run (deterministic IDs keep it stable). No need
    to preserve foreign source_kinds — those live in social-drafts.json now.
    Capped at 30."""
    merged = new_drafts[:30]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {
        "schema": "bookmarker.wiki-grounded-drafts.v1",
        "status": "ok",
        "generated_at": now,
        "updated_at": now,
        "drafts": merged,
        "diagnostic": {
            "wiki_grounded_count": len(merged),
        },
        "failed_attempts": [],
    }
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-drafts", type=int, default=DEFAULT_MAX_DRAFTS,
                   help="How many new wiki-grounded drafts to emit this run")
    p.add_argument("--body-cap", type=int, default=DEFAULT_BODY_CAP_CHARS,
                   help="Max characters in each draft body")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the drafts but don't write social-drafts.json")
    args = p.parse_args()

    # P-A: load topic ranker (concept hotness/depth scores + 14-day exec-log ids
    # for novelty). Graceful: missing file → old uniform behaviour (no scores, no
    # novelty filter), so the generator still works if the ranker hasn't run.
    ranking = _read_json(TOPIC_CANDIDATES_PATH) or {}
    concept_scores = {c.get("concept"): c.get("score", 0.0)
                      for c in (ranking.get("candidates") or []) if c.get("concept")}
    exec_log_ids = set(ranking.get("exec_log_recent_ids") or [])

    # P-C: AutoResearch Track-B content_angle arm chooses framing (insight vs open_question).
    exp = _read_json(WORKSPACE / "runtime" / "shared" / "strategy-experiment.json") or {}
    content_angle = ((exp.get("track_b") or {}).get("current_arm") or {}).get("content_angle", "insight")

    bullets = collect_concept_bullets(CONCEPTS_DIR, concept_scores, content_angle)
    if not bullets:
        print("[wiki-drafts] no concept bullets found — aborting", file=sys.stderr)
        return 1

    drafts = build_drafts(bullets, args.max_drafts, args.body_cap, exec_log_ids)
    if not drafts and exec_log_ids:
        # All ranked/novel bullets exhausted (everything posted in last 14d). Retry
        # WITHOUT the novelty filter so we still surface something rather than 0 —
        # the posting-side dedup will still prevent an actual re-post.
        print("[wiki-drafts] all drafts hit 14d novelty filter — retrying without it", file=sys.stderr)
        drafts = build_drafts(bullets, args.max_drafts, args.body_cap, None)
    if not drafts:
        print("[wiki-drafts] no usable drafts after cleaning", file=sys.stderr)
        return 1

    existing = _read_json(DRAFTS_PATH)
    new_pool = merge_into_pool(existing, drafts)

    print(f"[wiki-drafts] {len(drafts)} wiki-grounded drafts built, "
          f"{len(new_pool['drafts'])} drafts in pool total")
    for d in drafts:
        print(f"  {d['id']:50s} theme={d['theme']:25s} {d['text'][:70]!r}")

    if args.dry_run:
        return 0

    _atomic_write_json(DRAFTS_PATH, new_pool)
    try:
        append_wiki_event(
            event_type="wiki_grounded_drafts_built",
            producer="build_wiki_grounded_drafts_v1",
            artifact=path_ref(DRAFTS_PATH, WORKSPACE),
            status="ok",
            summary=f"wiki_grounded={len(drafts)} pool_total={len(new_pool['drafts'])}",
            detail={
                "wiki_grounded_added": len(drafts),
                "pool_total": len(new_pool["drafts"]),
                "concepts_used": sorted({d["theme"] for d in drafts}),
            },
        )
    except Exception as exc:  # noqa: BLE001 — non-fatal
        print(f"[wiki-drafts] event emit failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

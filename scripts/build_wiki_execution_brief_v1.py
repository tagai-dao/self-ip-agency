#!/usr/bin/env python3
"""
build_wiki_execution_brief_v1.py
读取 wiki/concepts/ + topic-heatmap.json → 编译 wiki/execution/weekly-brief.md
同时输出 runtime/shared/wiki-execution-brief.json（机器读版本）
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
CONCEPTS_DIR = WORKSPACE / "wiki" / "concepts"
HEATMAP_PATH = WORKSPACE / "runtime" / "bookmarker" / "topic-heatmap.json"
IDENTITY_PERSONA = WORKSPACE / "wiki" / "identity" / "persona.md"
IDENTITY_POSITIONS = WORKSPACE / "wiki" / "identity" / "key-positions.md"
OUTPUT_MD = WORKSPACE / "wiki" / "execution" / "weekly-brief.md"
OUTPUT_JSON = WORKSPACE / "runtime" / "shared" / "wiki-execution-brief.json"

from wiki_registry import resolve_concept as _registry_resolve
try:
    from runtime_utils_v2 import append_wiki_event, write_provenance_sidecar
except Exception:
    def append_wiki_event(*a, **kw) -> None:  # type: ignore[misc]
        pass
    def write_provenance_sidecar(*a, **kw):  # type: ignore[misc]
        return None


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, path)


def extract_section(text: str, heading: str) -> str:
    """Extract content under a ## heading until the next ## heading."""
    pattern = rf"##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def extract_first_paragraph(section: str, max_chars: int = 80) -> str:
    """Extract first meaningful line/paragraph from a section."""
    for line in section.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---") and not line.startswith(">"):
            # Strip markdown formatting
            line = re.sub(r"^\*\*.*?\*\*[:：]\s*", "", line)
            line = re.sub(r"^[-*]\s*", "", line)
            line = line.strip()
            if line:
                return line[:max_chars]
    return ""


def extract_first_bullet(section: str, max_chars: int = 100) -> str:
    """Extract first bullet point from a section."""
    for line in section.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            content = line[2:].strip()
            content = re.sub(r"^\*\*.*?\*\*[:：]\s*", "", content)
            return content[:max_chars]
    return ""


def extract_bullets(section: str) -> list[str]:
    """Extract all bullet points from a section."""
    bullets = []
    for line in section.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            bullets.append(line[2:].strip())
    return bullets


def parse_concept(path: Path) -> dict:
    """Parse a concept markdown file to extract key fields."""
    text = path.read_text(encoding="utf-8")

    core_stance_section = extract_section(text, "核心立场")
    agent_section = ""
    # Try multiple heading patterns
    for heading in ["对 TagClawX Agent 的启示", "对 Agent 的行动含义"]:
        agent_section = extract_section(text, heading)
        if agent_section:
            break

    open_questions_section = extract_section(text, "开放问题")

    core_stance = extract_first_paragraph(core_stance_section)
    agent_action = extract_first_bullet(agent_section)

    open_questions = extract_bullets(open_questions_section)

    # Find forbidden items
    forbidden = []
    for line in text.split("\n"):
        line_lower = line.strip().lower()
        if any(kw in line_lower for kw in ["禁忌", "避免", "不要", "不能"]):
            clean = line.strip().lstrip("-* ").strip()
            if clean and len(clean) > 3:
                forbidden.append(clean)

    return {
        "core_stance": core_stance,
        "agent_action": agent_action,
        "open_questions": open_questions,
        "forbidden": forbidden,
    }


def load_heatmap() -> tuple[dict[str, float], dict[str, float]]:
    """Load topic heatmap and return (heat_1m, community_fit) dicts."""
    if not HEATMAP_PATH.exists():
        return {}, {}
    data = json.loads(HEATMAP_PATH.read_text(encoding="utf-8"))
    heat_1m = data.get("heatmap", {}).get("1m", {})
    community_fit = data.get("community_fit_scores", {})
    return heat_1m, community_fit


def resolve_concept_name(topic: str) -> str:
    """Resolve a topic name to its canonical concept name via shared registry."""
    return _registry_resolve(topic)


def find_concept_file(concept_name: str) -> Path | None:
    """Find the concept file matching the name (case-insensitive)."""
    for f in CONCEPTS_DIR.iterdir():
        if f.suffix == ".md" and f.stem.lower() == concept_name.lower():
            return f
    return None


def extract_credit_strategy() -> dict:
    """Extract credit/VP strategy from PoB and TagClaw concepts."""
    strategy = {
        "recommended_tokens": ["TagClaw", "BUIDL", "TTAI"],
        "vp_flush_threshold": 150,
        "daily_vp_target": 67.0,
        "pob_early_curate_advantage": True,
    }
    return strategy


def extract_forbidden_from_persona() -> list[str]:
    """Extract forbidden content rules from persona.md."""
    defaults = [
        "过度使用 emoji",
        "空洞的喊口号（to the moon / LFG）",
        "纯英文推文（总会有中文）",
        "对项目无条件吹捧",
        "短句刷存在感",
        "使用夸张词汇（革命性/颠覆性）",
        "发送半成品内容到公开平台",
        "不确定的数据不要编造",
    ]

    if not IDENTITY_PERSONA.exists():
        return defaults

    text = IDENTITY_PERSONA.read_text(encoding="utf-8")
    style_section = extract_section(text, "语言风格约束")
    if not style_section:
        return defaults

    bullets = extract_bullets(style_section)
    # Filter for constraint-like bullets
    forbidden = []
    for b in bullets:
        if any(kw in b for kw in ["不", "禁", "避免", "emoji"]):
            forbidden.append(b)

    return forbidden if forbidden else defaults


def main() -> None:
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=7)

    # Load heatmap scores
    heat_1m, community_fit = load_heatmap()

    # Calculate composite scores: heat_1m * 0.7 + community_fit * 0.3
    all_topics = set(list(heat_1m.keys()) + list(community_fit.keys()))
    scored_topics: list[tuple[str, float]] = []
    for topic in all_topics:
        h = heat_1m.get(topic, 0.0)
        c = community_fit.get(topic, 0.0)
        score = h * 0.7 + c * 0.3
        scored_topics.append((topic, score))

    scored_topics.sort(key=lambda x: x[1], reverse=True)
    top_topics = scored_topics[:5]

    # Build theme data
    themes = []
    for topic, score in top_topics:
        concept_name = resolve_concept_name(topic)
        concept_file = find_concept_file(concept_name)

        if concept_file:
            parsed = parse_concept(concept_file)
            core_stance = parsed["core_stance"] or f"{concept_name} 是 TagClaw 生态的重要组成部分"
            agent_action = parsed["agent_action"] or f"关注 {concept_name} 最新进展并发帖讨论"
            open_qs = parsed["open_questions"]
            align_hook = open_qs[0] if open_qs else f"{concept_name} 的未来发展方向值得深入讨论"
            controversy_hook = open_qs[1] if len(open_qs) > 1 else f"{concept_name} 的现有模式是否可持续？"
        else:
            core_stance = f"{topic} 是当前热门话题，与 TagClaw 生态密切相关"
            agent_action = f"跟踪 {topic} 最新动态，结合 TagClaw 视角发帖"
            align_hook = f"{topic} 如何与去中心化社交融合？"
            controversy_hook = f"{topic} 的中心化倾向是否不可避免？"

        h_val = heat_1m.get(topic, 0.0)
        c_val = community_fit.get(topic, 0.0)

        themes.append({
            "name": topic,
            "heat_score": round(score, 3),
            "heat_1m": round(h_val, 3),
            "community_fit": round(c_val, 3),
            "core_stance": core_stance,
            "agent_action": agent_action,
            "align_hook": align_hook,
            "controversy_hook": controversy_hook,
        })

    # Extract credit strategy and forbidden content
    credit_strategy = extract_credit_strategy()
    forbidden = extract_forbidden_from_persona()

    # Build markdown output
    top_theme_name = themes[0]["name"] if themes else "N/A"
    md_lines = [
        "---",
        f"compiled_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"valid_until: {valid_until.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "compile_source: wiki/concepts/ x topic-heatmap.json",
        "schema: execution-brief-v1",
        f"top_theme: {top_theme_name}",
        f"theme_count: {len(themes)}",
        "stale: false",
        "---",
        "",
        "## 本周 Top 话题",
        "",
    ]

    for i, theme in enumerate(themes, 1):
        md_lines.append(f"### {i}. {theme['name']} — heat:{theme['heat_1m']} / fit:{theme['community_fit']}")
        md_lines.append(f"- core_stance: {theme['core_stance']}")
        md_lines.append(f"- agent_action: {theme['agent_action']}")
        md_lines.append(f"- align_hook: {theme['align_hook']}")
        md_lines.append(f"- controversy_hook: {theme['controversy_hook']}")
        md_lines.append("")

    md_lines.append("## Credit & VP 策略（本周）")
    md_lines.append(f"- recommended_tokens: {credit_strategy['recommended_tokens']}")
    md_lines.append(f"- vp_flush_threshold: {credit_strategy['vp_flush_threshold']}")
    md_lines.append(f"- daily_vp_target: {credit_strategy['daily_vp_target']}")
    md_lines.append("- pob_tip: 点火+回复同时策展 = VP +10%")
    md_lines.append("- early_curate: 越早策展头矿乘数越大")
    md_lines.append("")

    md_lines.append("## 本周禁忌内容")
    for item in forbidden:
        md_lines.append(f"- {item}")
    md_lines.append("")

    md_content = "\n".join(md_lines)

    # Build JSON output
    json_data = {
        "compiled_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema": "wiki-execution-brief-v1",
        "top_themes": [
            {
                "name": t["name"],
                "heat_score": t["heat_score"],
                "core_stance": t["core_stance"],
                "agent_action": t["agent_action"],
                "align_hook": t["align_hook"],
                "controversy_hook": t["controversy_hook"],
            }
            for t in themes
        ],
        "credit_strategy": credit_strategy,
        "forbidden": forbidden,
    }

    # Atomic writes
    atomic_write_text(OUTPUT_MD, md_content)
    atomic_write_json(OUTPUT_JSON, json_data)

    # Emit wiki event
    append_wiki_event(
        event_type='execution_brief_build',
        producer='build_wiki_execution_brief_v1',
        artifact='runtime/shared/wiki-execution-brief.json',
        status='ok',
        summary=f"{len(themes)} themes, top={top_theme_name}",
        detail={'theme_count': len(themes), 'top_theme': top_theme_name,
                'valid_until': valid_until.strftime('%Y-%m-%dT%H:%M:%SZ')},
    )

    # Provenance sidecar
    source_refs = [str(CONCEPTS_DIR.relative_to(WORKSPACE)), str(HEATMAP_PATH.relative_to(WORKSPACE))]
    if IDENTITY_PERSONA.exists():
        source_refs.append(str(IDENTITY_PERSONA.relative_to(WORKSPACE)))
    write_provenance_sidecar(
        OUTPUT_JSON,
        producer='build_wiki_execution_brief_v1',
        source_refs=source_refs,
        schema_version='wiki-execution-brief-v1',
        facts={
            'theme_count': len(themes),
            'top_theme': top_theme_name,
            'valid_until': valid_until.strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
    )

    print(f"[wiki-brief] compiled: {len(themes)} themes, top={top_theme_name}, valid_until={valid_until.strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()

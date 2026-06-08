#!/usr/bin/env python3
"""Helpers for filtering internal scratchpad / malformed text before user-visible sends."""
from __future__ import annotations

import re
from typing import Any

SCRATCHPAD_PHRASES = [
    "i'll start by",
    "let me ",
    "now let me",
    "now i need to",
    "now update",
    "now add",
    "now run",
    "now verify",
    "let me check",
    "let me verify",
    "let me read",
    "let me debug",
]

MALFORMED_KEYWORDS = {
    "hookup",
    "toggle",
    "attach",
    "attachment",
    "compiler",
    "topics",
    "fw",
    "sdk",
    "pkt",
    "db",
    "toggle_attach",
    "compiler_attach",
    "job_hook",
    "attachment_toggle",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./:-]*")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def classify_text(text: str) -> tuple[str, dict[str, Any]]:
    raw = text or ""
    text = normalize_text(raw)
    lower = text.lower()
    meta: dict[str, Any] = {
        "scratchpad_hits": 0,
        "keyword_hits": 0,
        "underscoreish_tokens": 0,
        "token_count": 0,
    }
    if not text:
        return "empty", meta

    scratchpad_hits = sum(lower.count(p) for p in SCRATCHPAD_PHRASES)
    tokens = _WORD_RE.findall(text)
    token_count = len(tokens)
    underscoreish_tokens = sum(1 for t in tokens if ('_' in t or '/' in t) and len(t) >= 8)
    keyword_hits = 0
    for t in tokens:
        tl = t.lower()
        if tl in MALFORMED_KEYWORDS or any(k in tl for k in MALFORMED_KEYWORDS if len(k) >= 6):
            keyword_hits += 1

    meta.update(
        scratchpad_hits=scratchpad_hits,
        keyword_hits=keyword_hits,
        underscoreish_tokens=underscoreish_tokens,
        token_count=token_count,
    )

    if scratchpad_hits >= 3:
        return "scratchpad", meta

    # malformed stream / token soup heuristics
    if token_count >= 20 and (underscoreish_tokens >= 8 or keyword_hits >= 8):
        return "malformed", meta
    if token_count >= 12 and keyword_hits >= 8:
        return "malformed", meta
    if token_count >= 40 and (underscoreish_tokens / max(token_count, 1)) >= 0.15:
        return "malformed", meta
    if len(text) >= 220 and keyword_hits >= 6 and scratchpad_hits >= 1:
        return "malformed", meta

    return "ok", meta


def sanitize_summary(
    summary: str,
    *,
    files_changed: list[str] | None = None,
    tests_passed: bool | None = None,
    blockers: list[Any] | None = None,
) -> tuple[str, str | None, dict[str, Any]]:
    text = normalize_text(summary)
    kind, meta = classify_text(text)
    if kind == "ok":
        return text[:280], None, meta

    file_count = len(files_changed or [])
    tests_text = "已通过" if tests_passed else ("未通过 / 未知" if tests_passed is not None else "未知")
    blocker_count = len(blockers or [])

    if kind == "empty":
        safe = f"自动摘要为空；请以文件变更和测试结果为准。文件变更：{file_count} 个；测试：{tests_text}。"
        return safe, kind, meta

    reason = "检测到内部过程 narration" if kind == "scratchpad" else "检测到异常 token / 脏输出"
    safe = (
        f"自动摘要已清洗（{reason}），已隐藏不适合直接发送的中间输出。"
        f"文件变更：{file_count} 个；测试：{tests_text}；blockers：{blocker_count}。"
    )
    return safe, kind, meta


def sanitize_text_fragment(text: Any, *, fallback: str = "内部异常输出已隐藏") -> str:
    s = normalize_text(str(text or ""))
    kind, _meta = classify_text(s)
    if kind in {"scratchpad", "malformed", "empty"}:
        return fallback
    return s[:200]


def sanitize_blockers(blockers: list[Any] | None) -> list[str]:
    cleaned: list[str] = []
    for b in blockers or []:
        cleaned.append(sanitize_text_fragment(b, fallback="内部异常输出已隐藏；请查看 result.json 详情"))
    return cleaned

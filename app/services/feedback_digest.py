"""Weekly digest of the replies users were unhappy with.

Thumbs-down ratings (with the reasons users picked) come first; replies the
conversation quality evaluator judged negative/angry follow, so the digest is
useful even in weeks nobody clicks a button. The ops cron writes one Markdown
and one JSON file per week under ``<runtime>/ops/feedback-digest/``; admins can
also read it on demand from ``GET /quality/feedback-digest``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.repository import feedback_digest as repository

_UTC8 = timezone(timedelta(hours=8))
_DB_FORMAT = "%Y-%m-%d %H:%M:%S"
DIGEST_SUBDIR = Path("ops") / "feedback-digest"

REASON_LABELS = {
    "wrong": "结果有误",
    "incomplete": "不完整",
    "failed": "任务失败/报错",
    "slow": "太慢",
    "off_topic": "没理解我的问题",
    "other": "其他",
}
LEVEL_LABELS = {"satisfied": "满意", "acceptable": "可接受", "negative": "不满意", "angry": "非常不满"}


def build_digest(conn: sqlite3.Connection, *, now: Optional[datetime] = None, days: int = 7, limit: int = 50) -> Dict[str, Any]:
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = end - timedelta(days=days)
    data = repository.collect(conn, since=start.strftime(_DB_FORMAT), until=end.strftime(_DB_FORMAT), limit=limit)
    return {"since": start.isoformat(), "until": end.isoformat(), "days": days, **data}


def _local(stamp: Any) -> str:
    if not stamp:
        return "?"
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return str(stamp)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_UTC8).strftime("%m-%d %H:%M")


def _quote(text: str) -> str:
    return text.replace("\n", " ") if text else "（空）"


def _case_lines(index: int, case: Dict[str, Any], header: str, extra: list) -> list:
    lines = [f"### {index}. {header}", ""]
    lines.append(f"- 用户：{case.get('owner_email') or case.get('owner_id') or '?'}；会话 `{case.get('session_id', '?')}`；运行 `{case['run_id']}`（{case.get('run_status', '?')}）")
    lines.extend(extra)
    lines.append(f"- 提问：{_quote(case.get('question', ''))}")
    lines.append(f"- 回答：{_quote(case.get('answer', ''))}")
    lines.append("")
    return lines


def render_markdown(digest: Dict[str, Any]) -> str:
    ratings = digest["ratings"]
    levels = digest["auto_levels"]
    lines = [
        f"# PhageAgent 不满意案例周报（{_local(digest['since'])} ~ {_local(digest['until'])}，北京时间）",
        "",
        f"- 点赞 {ratings.get('useful', 0)} 次，点踩 {ratings.get('needs_work', 0)} 次",
        "- 自动满意度评估："
        + ("，".join(f"{LEVEL_LABELS.get(name, name)} {count}" for name, count in sorted(levels.items())) or "无"),
        "",
        f"## 用户点踩（{len(digest['downvotes'])}）",
        "",
    ]
    if not digest["downvotes"]:
        lines += ["本周没有点踩。", ""]
    for index, case in enumerate(digest["downvotes"], 1):
        reasons = "、".join(REASON_LABELS.get(item, item) for item in case.get("reasons") or []) or "未选原因"
        extra = [f"- 原因：{reasons}"]
        if case.get("comment"):
            extra.append(f"- 补充：{_quote(case['comment'])}")
        lines += _case_lines(index, case, _local(case.get("feedback_at")), extra)
    lines += [f"## 自动评估为不满意（{len(digest['auto_cases'])}）", ""]
    if not digest["auto_cases"]:
        lines += ["本周没有自动评估为不满意的对话。", ""]
    for index, case in enumerate(digest["auto_cases"], 1):
        level = LEVEL_LABELS.get(case.get("satisfaction_level"), case.get("satisfaction_level"))
        extra = [f"- 评估：{level}（置信度 {float(case.get('confidence') or 0):.2f}）；问题类型：{'、'.join(case.get('failure_modes') or []) or '未标注'}"]
        if case.get("evidence"):
            extra.append(f"- 依据：{_quote(case['evidence'])}")
        lines += _case_lines(index, case, _local(case.get("evaluated_at")), extra)
    return "\n".join(lines).rstrip() + "\n"


def load_digest(main_db: Path, *, now: Optional[datetime] = None, days: int = 7) -> Dict[str, Any]:
    """Build the digest over a private read-only connection to *main_db*."""
    conn = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return build_digest(conn, now=now, days=days)
    finally:
        conn.close()


def write_weekly_digest(*, main_db: Path, runtime_root: Path, now: Optional[datetime] = None, days: int = 7) -> Dict[str, Any]:
    """Write this week's digest files; read-only on the database."""
    now = now or datetime.now(timezone.utc)
    if not main_db.exists():
        return {"skipped": "main database missing"}
    digest = load_digest(main_db, now=now, days=days)
    out_dir = runtime_root / DIGEST_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "feedback-digest-" + now.astimezone(_UTC8).strftime("%Y-%m-%d")
    markdown_path = out_dir / f"{stem}.md"
    markdown_path.write_text(render_markdown(digest), encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(markdown_path),
        "downvotes": len(digest["downvotes"]),
        "auto_cases": len(digest["auto_cases"]),
    }

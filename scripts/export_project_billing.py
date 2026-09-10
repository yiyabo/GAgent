#!/usr/bin/env python3
"""Export per-project x billing-key LLM usage detail.

Read-only tool: static SQL constants only; no dynamic SQL construction.
Supports both the 26-column production ledger and the 20-column historical
ledger (call_purpose/tool_name mapped via billing_keys fallback rules).
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sqlite3
import sys

try:
    from app.billing_keys import (  # type: ignore
        INTERNAL_UNCATEGORIZED,
        billing_key_for_purpose,
    )
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    try:
        from app.billing_keys import INTERNAL_UNCATEGORIZED, billing_key_for_purpose
    except ImportError:
        INTERNAL_UNCATEGORIZED = "internal.uncategorized"

        def billing_key_for_purpose(purpose, tool_name=None):  # type: ignore
            _p = str(purpose or "").strip().lower()
            _t = str(tool_name or "").strip().lower()
            mapping = {
                "chat_main": "chat.main",
                "deep_think_iteration": "deep_think.iteration",
                "plan_task_execution": "plan.task_execution",
                "qwen_code_cli_execution": "coding_agent.qwen_code_cli",
                "conversation_quality_evaluation": "internal.conversation_quality_evaluation",
            }
            if _p in mapping:
                return mapping[_p]
            if _t == "code_executor":
                return "tool.code_executor"
            if _p or _t:
                return "tool.execution"
            return INTERNAL_UNCATEGORIZED

SESSION_PLAN_SQL = (
    "SELECT s.id AS session_id, s.plan_id, s.owner_id, p.title AS plan_title "
    "FROM chat_sessions s LEFT JOIN plans p ON s.plan_id = p.id"
)

LEDGER_SELECT_FULL = (
    "SELECT id AS row_id, created_at, provider, model, prompt_tokens, "
    "completion_tokens, total_tokens, COALESCE(session_id, '') AS session_id, "
    "plan_id AS plan_id_ledger, COALESCE(call_purpose, '') AS call_purpose, "
    "COALESCE(tool_name, '') AS tool_name, "
    "COALESCE(call_status, 'ok') AS call_status, "
    "COALESCE(billing_key, '') AS billing_key, "
    "COALESCE(logical_call_id, '') AS logical_call_id, "
    "COALESCE(attempt_no, '') AS attempt_no, "
    "COALESCE(estimated_cost, 0.0) AS estimated_cost "
    "FROM llm_usage_log"
)

LEDGER_SELECT_LEGACY = (
    "SELECT id AS row_id, created_at, provider, model, prompt_tokens, "
    "completion_tokens, total_tokens, COALESCE(session_id, '') AS session_id, "
    "plan_id AS plan_id_ledger, COALESCE(call_purpose, '') AS call_purpose, "
    "COALESCE(tool_name, '') AS tool_name, "
    "COALESCE(call_status, 'ok') AS call_status, "
    "'' AS billing_key, "
    "'' AS logical_call_id, "
    "'' AS attempt_no, "
    "COALESCE(estimated_cost, 0.0) AS estimated_cost "
    "FROM llm_usage_log"
)

MODEL_PRICE_GAP_SQL = (
    "SELECT model, SUM(CASE WHEN estimated_cost IS NULL OR estimated_cost = 0 "
    "THEN 1 ELSE 0 END) FROM llm_usage_log GROUP BY model"
)

PRAGMA_COLUMNS_SQL = "PRAGMA table_info(llm_usage_log)"


def _read_only(db_path):
    conn = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_business_bindings(business_db):
    conn = _read_only(business_db)
    try:
        bindings = {}
        for r in conn.execute(SESSION_PLAN_SQL).fetchall():
            bindings[r["session_id"]] = {
                "plan_id": r["plan_id"],
                "plan_title": r["plan_title"],
            }
        return bindings
    finally:
        conn.close()


def load_ledger(ledger_db):
    conn = _read_only(ledger_db)
    try:
        cols = [r[1] for r in conn.execute(PRAGMA_COLUMNS_SQL)]
        sql = LEDGER_SELECT_FULL if "billing_key" in cols else LEDGER_SELECT_LEGACY
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def resolve_billing_key(row):
    if row.get("billing_key"):
        return str(row["billing_key"]), False
    key = billing_key_for_purpose(
        row.get("call_purpose") or None, row.get("tool_name") or None
    )
    return key, key == INTERNAL_UNCATEGORIZED


SUMMARY_COLUMNS = [
    "课题ID", "课题标题", "billing_key", "调用次数",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "重试行数", "失败行数", "估算成本CNY", "该组内无标注兜底行数",
]

DETAIL_COLUMNS = [
    "row_id", "created_at", "课题ID", "课题标题", "session_id",
    "provider", "model", "billing_key", "call_purpose", "tool_name",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "logical_call_id", "attempt_no", "call_status", "estimated_cost",
]


def main():
    parser = argparse.ArgumentParser(description="课题×分组 LLM 用量导出")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--business", required=True)
    parser.add_argument("--out-dir", default="billing_export")
    args = parser.parse_args()

    ledger_db = pathlib.Path(args.ledger)
    business_db = pathlib.Path(args.business)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bindings = load_business_bindings(business_db)
    ledger = load_ledger(ledger_db)

    detail_rows = []
    agg = {}
    n_plan_bound = 0
    n_unbound = 0
    n_untagged = 0

    def _bucket(plan_id, key):
        k = (plan_id, key)
        if k not in agg:
            agg[k] = {
                "课题标题": "",
                "调用次数": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "重试行数": 0,
                "失败行数": 0,
                "估算成本CNY": 0.0,
                "该组内无标注兜底行数": 0,
            }
        return agg[k]

    for row in ledger:
        session_id = row.get("session_id") or ""
        binding = bindings.get(session_id) if session_id else None
        if binding and binding["plan_id"] is not None:
            plan_id = binding["plan_id"]
            plan_title = binding["plan_title"] or ""
            n_plan_bound += 1
        else:
            plan_id = None
            plan_title = ""
            n_unbound += 1

        key, untagged = resolve_billing_key(row)
        if untagged:
            n_untagged += 1

        attempt_no = row.get("attempt_no")
        is_retry = attempt_no not in (None, "", 1)
        status = row.get("call_status") or "ok"

        detail_rows.append(
            {
                "row_id": row["row_id"],
                "created_at": row["created_at"],
                "课题ID": plan_id,
                "课题标题": plan_title,
                "session_id": session_id,
                "provider": row["provider"],
                "model": row["model"],
                "billing_key": key,
                "call_purpose": row.get("call_purpose"),
                "tool_name": row.get("tool_name"),
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "logical_call_id": row.get("logical_call_id"),
                "attempt_no": attempt_no,
                "call_status": status,
                "estimated_cost": row.get("estimated_cost"),
            }
        )

        bucket = _bucket(plan_id, key)
        bucket["课题标题"] = plan_title
        bucket["调用次数"] += 1
        bucket["prompt_tokens"] += row["prompt_tokens"] or 0
        bucket["completion_tokens"] += row["completion_tokens"] or 0
        bucket["total_tokens"] += row["total_tokens"] or 0
        if is_retry:
            bucket["重试行数"] += 1
        if status != "ok":
            bucket["失败行数"] += 1
        bucket["估算成本CNY"] += row.get("estimated_cost") or 0.0
        if untagged:
            bucket["该组内无标注兜底行数"] += 1

    summary_path = out_dir / "project_billing_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for (plan_id, key), bucket in sorted(
            agg.items(), key=lambda kv: (kv[0][0] is None, kv[0][0] or 0, kv[0][1])
        ):
            writer.writerow({"课题ID": plan_id, "billing_key": key, **bucket})

    detail_path = out_dir / "project_billing_detail.csv"
    with detail_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_COLUMNS)
        writer.writeheader()
        writer.writerows(detail_rows)

    total = len(ledger)
    conn = _read_only(ledger_db)
    try:
        models_missing_price = [
            (r[0], r[1]) for r in conn.execute(MODEL_PRICE_GAP_SQL).fetchall()
        ]
    finally:
        conn.close()

    quality_path = out_dir / "export_quality_report.csv"
    with quality_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["指标", "值"])
        writer.writerow(["账本总行数", total])
        writer.writerow(["关联到课题的行数", n_plan_bound])
        writer.writerow(["未关联课题的行数", n_unbound])
        writer.writerow(["无归因兜底(internal.uncategorized)行数", n_untagged])
        if total:
            writer.writerow(["课题关联率%", round(100 * n_plan_bound / total, 1)])
            writer.writerow(["归因覆盖率%", round(100 * (total - n_untagged) / total, 1)])
        else:
            writer.writerow(["课题关联率%", 0])
            writer.writerow(["归因覆盖率%", 0])
        writer.writerow([])
        writer.writerow(["模型", "成本为0的行数(缺单价)"])
        for m, c in sorted(models_missing_price):
            writer.writerow([m, c])

    print("ledger rows:", total)
    print("summary ->", summary_path)
    print("detail  ->", detail_path)
    print("quality ->", quality_path)


if __name__ == "__main__":
    main()

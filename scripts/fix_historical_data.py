"""Fix historical data: recalculate costs with official prices + fix generic summaries."""
import json
import sqlite3
import glob

MAIN_DB = "data/databases/main/plan_registry.db"
PLAN_DBS = glob.glob("data/databases/plans/*.sqlite")

# Official qwen3.7-max prices (限时5折): 6元/百万input, 18元/百万output
INPUT_RATE = 0.006
OUTPUT_RATE = 0.018


def fix_costs():
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row

    before = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost),0) as c FROM llm_usage_log"
    ).fetchone()["c"]

    conn.execute("""
        UPDATE llm_usage_log
        SET input_cost = prompt_tokens * ? / 1000.0,
            output_cost = completion_tokens * ? / 1000.0,
            estimated_cost = (prompt_tokens * ? / 1000.0) + (completion_tokens * ? / 1000.0)
        WHERE model LIKE '%qwen%'
    """, (INPUT_RATE, OUTPUT_RATE, INPUT_RATE, OUTPUT_RATE))
    conn.commit()

    after = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost),0) as c FROM llm_usage_log"
    ).fetchone()["c"]

    print(f"[Costs] Updated {conn.total_changes} rows")
    print(f"  Before: ¥{before:.4f}")
    print(f"  After:  ¥{after:.4f}")
    print(f"  Saved:  ¥{before - after:.4f}")
    conn.close()


def _build_summary(exec_result: dict) -> str:
    meta = exec_result.get("metadata", {})
    status = meta.get("execution_status") or exec_result.get("status", "")
    verification = meta.get("verification_status", "")
    artifacts = meta.get("contract_artifacts", [])
    produced = [a for a in artifacts if isinstance(a, dict) and a.get("exists")]

    parts = []
    if status:
        parts.append(f"Execution: {status}")
    if verification:
        parts.append(f"Verification: {verification}")
    if produced:
        names = [a.get("expected", a.get("path", "")).split("/")[-1] for a in produced[:3]]
        parts.append(f"Artifacts: {', '.join(names)}")

    return " | ".join(parts) if parts else "Task completed successfully."


def fix_summaries():
    total_fixed = 0
    for db_path in PLAN_DBS:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        try:
            rows = conn.execute(
                "SELECT id, name, execution_result FROM tasks "
                "WHERE execution_result LIKE '%External task delegation completed%'"
            ).fetchall()
        except sqlite3.OperationalError:
            conn.close()
            continue

        if not rows:
            conn.close()
            continue

        for row in rows:
            try:
                exec_result = json.loads(row["execution_result"])
            except (json.JSONDecodeError, TypeError):
                continue

            new_summary = _build_summary(exec_result)
            exec_result["content"] = new_summary
            conn.execute(
                "UPDATE tasks SET execution_result = ? WHERE id = ?",
                (json.dumps(exec_result, ensure_ascii=False), row["id"]),
            )
            total_fixed += 1
            print(f"  [{db_path.split('/')[-1]}] Task {row['id']} ({row['name']}): \"{new_summary}\"")

        conn.commit()
        conn.close()

    print(f"[Summaries] Fixed {total_fixed} tasks across {len(PLAN_DBS)} plan DBs")


if __name__ == "__main__":
    print("=== Fixing historical costs ===")
    fix_costs()
    print()
    print("=== Fixing generic summaries ===")
    fix_summaries()
    print()
    print("Done. Restart backend to see changes.")

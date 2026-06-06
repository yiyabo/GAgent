#!/usr/bin/env python3
"""
迁移脚本：将项目级 results/plans/ 中的 artifacts 迁移到 session-scoped 路径

这个脚本会：
1. 扫描 results/plans/ 目录下的所有 plan artifacts
2. 对于每个 plan，查找对应的 session_id（从 plan 元数据或最近的 session）
3. 将 artifacts 复制到 runtime/session_{session_id}/artifacts/plan_{plan_id}/
4. 保留原始文件作为备份（不删除）

使用方法：
    python scripts/migrate_artifacts_to_session_scoped.py --dry-run  # 预览模式
    python scripts/migrate_artifacts_to_session_scoped.py            # 实际迁移
"""

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def find_session_id_for_plan(plan_id: int, runtime_dir: Path) -> Optional[str]:
    """查找 plan 对应的 session_id
    
    策略：
    1. 从 plan 元数据中读取 session_id
    2. 从最近的 session 目录中查找包含该 plan 的 session
    """
    # 策略 1: 从 plan 元数据中读取
    plan_meta_file = Path(f"results/plans/plan_{plan_id}/plan_meta.json")
    if plan_meta_file.exists():
        try:
            with open(plan_meta_file, 'r') as f:
                meta = json.load(f)
                session_id = meta.get("session_id")
                if session_id:
                    logger.info(f"Found session_id {session_id} from plan_meta.json for plan {plan_id}")
                    return session_id
        except Exception as e:
            logger.warning(f"Failed to read plan_meta.json for plan {plan_id}: {e}")
    
    # 策略 2: 从最近的 session 目录中查找
    if runtime_dir.exists():
        session_dirs = sorted(
            [d for d in runtime_dir.iterdir() if d.is_dir() and d.name.startswith("session_")],
            key=lambda d: d.stat().st_mtime,
            reverse=True
        )
        for session_dir in session_dirs:
            session_artifacts_dir = session_dir / "artifacts" / f"plan_{plan_id}"
            if session_artifacts_dir.exists():
                session_id = session_dir.name.replace("session_", "")
                logger.info(f"Found existing session-scoped artifacts for plan {plan_id} in session {session_id}")
                return session_id
            
            jobs_db = session_dir / "plan_jobs.db"
            if jobs_db.exists():
                # TODO: 这里可以添加更复杂的逻辑来检查 jobs_db
                pass
    
    return None


def migrate_plan_artifacts(
    plan_id: int,
    source_dir: Path,
    runtime_dir: Path,
    session_id: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, any]:
    """迁移单个 plan 的 artifacts
    
    Returns:
        迁移结果字典
    """
    result = {
        "plan_id": plan_id,
        "source": str(source_dir),
        "session_id": session_id,
        "target": None,
        "files_copied": 0,
        "errors": []
    }
    
    if not source_dir.exists():
        result["errors"].append(f"Source directory does not exist: {source_dir}")
        return result
    
    if not session_id:
        session_id = find_session_id_for_plan(plan_id, runtime_dir)
    
    if not session_id:
        result["errors"].append(f"Could not find session_id for plan {plan_id}")
        return result
    
    result["session_id"] = session_id
    
    target_dir = runtime_dir / f"session_{session_id}" / "artifacts" / f"plan_{plan_id}"
    result["target"] = str(target_dir)
    
    if dry_run:
        logger.info(f"[DRY RUN] Would migrate plan {plan_id} from {source_dir} to {target_dir}")
        file_count = sum(1 for _ in source_dir.rglob("*") if _.is_file())
        result["files_copied"] = file_count
        return result
    
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for item in source_dir.rglob("*"):
            if item.is_file():
                rel_path = item.relative_to(source_dir)
                target_file = target_dir / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target_file)
                result["files_copied"] += 1
        
        logger.info(f"Migrated {result['files_copied']} files for plan {plan_id} to {target_dir}")
    except Exception as e:
        result["errors"].append(f"Migration failed: {e}")
        logger.error(f"Failed to migrate plan {plan_id}: {e}")
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="迁移项目级 artifacts 到 session-scoped 路径"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，不实际执行迁移"
    )
    parser.add_argument(
        "--plan-id",
        type=int,
        help="只迁移指定的 plan ID（可以多次指定）",
        action="append"
    )
    parser.add_argument(
        "--session-id",
        type=str,
        help="指定 session_id（用于所有迁移的 plan）"
    )
    
    args = parser.parse_args()
    
    plans_dir = Path("results/plans")
    if not plans_dir.exists():
        logger.error(f"Plans directory does not exist: {plans_dir}")
        return 1
    
    runtime_dir = Path("runtime")
    
    plan_dirs = sorted([
        d for d in plans_dir.iterdir()
        if d.is_dir() and d.name.startswith("plan_")
    ])
    
    if args.plan_id:
        plan_dirs = [d for d in plan_dirs if int(d.name.replace("plan_", "")) in args.plan_id]
    
    logger.info(f"Found {len(plan_dirs)} plan directories to migrate")
    
    results = []
    for plan_dir in plan_dirs:
        plan_id = int(plan_dir.name.replace("plan_", ""))
        result = migrate_plan_artifacts(
            plan_id=plan_id,
            source_dir=plan_dir,
            runtime_dir=runtime_dir,
            session_id=args.session_id,
            dry_run=args.dry_run
        )
        results.append(result)
    
    total_files = sum(r["files_copied"] for r in results)
    total_errors = sum(len(r["errors"]) for r in results)
    
    logger.info(f"\nMigration Summary:")
    logger.info(f"  Total plans: {len(results)}")
    logger.info(f"  Total files copied: {total_files}")
    logger.info(f"  Total errors: {total_errors}")
    
    if total_errors > 0:
        logger.warning(f"\nErrors encountered:")
        for result in results:
            if result["errors"]:
                logger.warning(f"  Plan {result['plan_id']}: {', '.join(result['errors'])}")
    
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    exit(main())

#!/usr/bin/env python3
"""
数据库和缓存管理命令

提供数据库维护、缓存管理、性能监控等功能的CLI接口
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.database import DB_PATH, get_db
from app.services.cache import get_embedding_cache
from app.services.evaluation_cache import get_cache_stats, get_evaluation_cache

from ..interfaces import CLICommand


class DatabaseCommands(CLICommand):
    """数据库和缓存管理命令类"""

    @property
    def name(self) -> str:
        return "database"

    @property
    def description(self) -> str:
        return "数据库和缓存管理命令"

    def __init__(self):
        self.evaluation_cache = get_evaluation_cache()
        self.embedding_cache = get_embedding_cache()

    def add_arguments(self, parser) -> None:
        """添加数据库管理相关的CLI参数"""
        # 这个方法在当前架构中不需要实现，因为参数已经在主解析器中定义
        # 但为了满足接口要求，我们提供一个空实现
        pass

    def execute(self, args) -> int:
        """执行数据库管理命令"""
        return handle_database_command(args)

    def show_database_info(self) -> Dict[str, Any]:
        """显示数据库基本信息"""
        info = {"databases": {}, "total_size_mb": 0}

        # 主数据库信息
        if os.path.exists(DB_PATH):
            size = os.path.getsize(DB_PATH) / (1024 * 1024)
            info["databases"]["main"] = {"path": DB_PATH, "size_mb": round(size, 2), "exists": True}
            info["total_size_mb"] += size

            # 获取表统计信息
            try:
                with get_db() as conn:
                    tables_info = {}

                    # 获取所有表名
                    cursor = conn.execute(
                        """
                        SELECT name FROM sqlite_master 
                        WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    """
                    )
                    tables = [row[0] for row in cursor.fetchall()]

                    # 获取每个表的行数
                    for table in tables:
                        try:
                            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                            count = cursor.fetchone()[0]
                            tables_info[table] = count
                        except Exception as e:
                            tables_info[table] = f"Error: {e}"

                    info["databases"]["main"]["tables"] = tables_info

            except Exception as e:
                info["databases"]["main"]["error"] = str(e)
        else:
            info["databases"]["main"] = {"path": DB_PATH, "exists": False}

        # 嵌入缓存数据库信息
        embedding_cache_path = self.embedding_cache.cache_db_path
        if os.path.exists(embedding_cache_path):
            size = os.path.getsize(embedding_cache_path) / (1024 * 1024)
            info["databases"]["embedding_cache"] = {
                "path": embedding_cache_path,
                "size_mb": round(size, 2),
                "exists": True,
            }
            info["total_size_mb"] += size
        else:
            info["databases"]["embedding_cache"] = {"path": embedding_cache_path, "exists": False}

        # 评估缓存数据库信息
        eval_cache_path = self.evaluation_cache.cache_db_path
        if os.path.exists(eval_cache_path):
            size = os.path.getsize(eval_cache_path) / (1024 * 1024)
            info["databases"]["evaluation_cache"] = {"path": eval_cache_path, "size_mb": round(size, 2), "exists": True}
            info["total_size_mb"] += size
        else:
            info["databases"]["evaluation_cache"] = {"path": eval_cache_path, "exists": False}

        info["total_size_mb"] = round(info["total_size_mb"], 2)
        return info

    def show_cache_stats(self) -> Dict[str, Any]:
        """显示缓存统计信息"""
        stats = {"evaluation_cache": {}, "embedding_cache": {}, "summary": {}}

        # 评估缓存统计
        try:
            eval_stats = get_cache_stats()
            stats["evaluation_cache"] = eval_stats
        except Exception as e:
            stats["evaluation_cache"]["error"] = str(e)

        # 嵌入缓存统计
        try:
            embed_stats = self.embedding_cache.get_stats()
            stats["embedding_cache"] = embed_stats
        except Exception as e:
            stats["embedding_cache"]["error"] = str(e)

        # 汇总信息
        try:
            eval_hit_rate = stats["evaluation_cache"].get("hit_rate", 0)
            embed_memory_size = stats["embedding_cache"].get("memory_cache_size", 0)
            embed_persistent_size = stats["embedding_cache"].get("persistent_cache_size", 0)

            stats["summary"] = {
                "evaluation_cache_hit_rate": eval_hit_rate,
                "embedding_memory_entries": embed_memory_size,
                "embedding_persistent_entries": embed_persistent_size,
                "overall_health": "good" if eval_hit_rate > 0.3 else "needs_attention",
            }
        except Exception as e:
            stats["summary"]["error"] = str(e)

        return stats

    def clear_caches(self, cache_type: str = "all", evaluation_method: Optional[str] = None) -> Dict[str, Any]:
        """清理缓存"""
        result = {"cleared": {}, "errors": {}}

        if cache_type in ["all", "evaluation"]:
            try:
                cleared_count = self.evaluation_cache.clear_cache(evaluation_method)
                result["cleared"]["evaluation_cache"] = cleared_count
            except Exception as e:
                result["errors"]["evaluation_cache"] = str(e)

        if cache_type in ["all", "embedding"]:
            try:
                # 清理嵌入缓存
                self.embedding_cache.clear_memory()
                if hasattr(self.embedding_cache, "clear_persistent"):
                    self.embedding_cache.clear_persistent()
                result["cleared"]["embedding_cache"] = "memory and persistent cleared"
            except Exception as e:
                result["errors"]["embedding_cache"] = str(e)

        return result

    def optimize_databases(self) -> Dict[str, Any]:
        """优化数据库"""
        result = {"optimized": {}, "errors": {}}

        # 优化评估缓存
        try:
            optimization_result = self.evaluation_cache.optimize_cache()
            result["optimized"]["evaluation_cache"] = optimization_result
        except Exception as e:
            result["errors"]["evaluation_cache"] = str(e)

        # 优化嵌入缓存
        try:
            if hasattr(self.embedding_cache, "cleanup_old_entries"):
                cleaned = self.embedding_cache.cleanup_old_entries(days=30)
                result["optimized"]["embedding_cache"] = f"cleaned {cleaned} old entries"
        except Exception as e:
            result["errors"]["embedding_cache"] = str(e)

        # 优化主数据库
        try:
            with get_db() as conn:
                conn.execute("VACUUM")
                conn.execute("ANALYZE")
                result["optimized"]["main_database"] = "vacuumed and analyzed"
        except Exception as e:
            result["errors"]["main_database"] = str(e)

        return result

    def backup_database(self, backup_path: Optional[str] = None) -> Dict[str, Any]:
        """备份主数据库"""
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"backup_tasks_{timestamp}.db"

        result = {"backup_path": backup_path, "success": False, "error": None}

        try:
            if not os.path.exists(DB_PATH):
                result["error"] = f"Source database {DB_PATH} does not exist"
                return result

            # 使用SQLite的备份API
            source = sqlite3.connect(DB_PATH)
            backup = sqlite3.connect(backup_path)

            source.backup(backup)

            source.close()
            backup.close()

            # 验证备份文件
            if os.path.exists(backup_path):
                backup_size = os.path.getsize(backup_path)
                original_size = os.path.getsize(DB_PATH)

                result["success"] = True
                result["backup_size_mb"] = round(backup_size / (1024 * 1024), 2)
                result["original_size_mb"] = round(original_size / (1024 * 1024), 2)
            else:
                result["error"] = "Backup file was not created"

        except Exception as e:
            result["error"] = str(e)

        return result

    def analyze_performance(self) -> Dict[str, Any]:
        """分析数据库性能"""
        analysis = {"database_performance": {}, "cache_performance": {}, "recommendations": []}

        # 数据库性能分析
        try:
            with get_db() as conn:
                # 检查数据库大小
                cursor = conn.execute("PRAGMA page_count")
                page_count = cursor.fetchone()[0]
                cursor = conn.execute("PRAGMA page_size")
                page_size = cursor.fetchone()[0]
                db_size_mb = (page_count * page_size) / (1024 * 1024)

                # 检查索引使用情况
                cursor = conn.execute(
                    """
                    SELECT name FROM sqlite_master 
                    WHERE type='index' AND name NOT LIKE 'sqlite_%'
                """
                )
                indexes = [row[0] for row in cursor.fetchall()]

                analysis["database_performance"] = {
                    "size_mb": round(db_size_mb, 2),
                    "page_count": page_count,
                    "page_size": page_size,
                    "index_count": len(indexes),
                    "indexes": indexes,
                }

                # 性能建议
                if db_size_mb > 100:
                    analysis["recommendations"].append("数据库较大，建议定期清理旧数据")
                if len(indexes) < 10:
                    analysis["recommendations"].append("考虑添加更多索引以提高查询性能")

        except Exception as e:
            analysis["database_performance"]["error"] = str(e)

        # 缓存性能分析
        try:
            cache_stats = self.show_cache_stats()
            eval_hit_rate = cache_stats.get("evaluation_cache", {}).get("hit_rate", 0)

            analysis["cache_performance"] = {
                "evaluation_hit_rate": eval_hit_rate,
                "status": "good" if eval_hit_rate > 0.5 else "needs_improvement",
            }

            if eval_hit_rate < 0.3:
                analysis["recommendations"].append("评估缓存命中率较低，考虑增加缓存大小或调整缓存策略")
            if eval_hit_rate < 0.1:
                analysis["recommendations"].append("评估缓存几乎无效，检查缓存配置和使用模式")

        except Exception as e:
            analysis["cache_performance"]["error"] = str(e)

        if not analysis["recommendations"]:
            analysis["recommendations"].append("系统性能良好，无需特殊优化")

        return analysis

    def reset_database(self) -> Dict[str, Any]:
        """重置数据库 - 清空所有数据"""
        result = {"reset": {}, "errors": {}, "warning": "This operation will permanently delete all data!"}

        try:
            # 重置主数据库
            with get_db() as conn:
                # 获取所有表名
                cursor = conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """
                )
                tables = [row[0] for row in cursor.fetchall()]

                # 清空所有表
                deleted_counts = {}
                for table in tables:
                    try:
                        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                        count_before = cursor.fetchone()[0]

                        conn.execute(f"DELETE FROM {table}")
                        deleted_counts[table] = count_before
                    except Exception as e:
                        deleted_counts[table] = f"Error: {e}"

                # 重置自增ID
                conn.execute("DELETE FROM sqlite_sequence")
                conn.commit()

                result["reset"]["main_database"] = {"tables_cleared": deleted_counts, "sequences_reset": True}

        except Exception as e:
            result["errors"]["main_database"] = str(e)

        # 重置评估缓存
        try:
            cleared_eval = self.evaluation_cache.clear_cache()
            result["reset"]["evaluation_cache"] = f"cleared {cleared_eval} entries"
        except Exception as e:
            result["errors"]["evaluation_cache"] = str(e)

        # 重置嵌入缓存
        try:
            self.embedding_cache.clear_memory()
            if hasattr(self.embedding_cache, "clear_persistent"):
                self.embedding_cache.clear_persistent()
            result["reset"]["embedding_cache"] = "memory and persistent cleared"
        except Exception as e:
            result["errors"]["embedding_cache"] = str(e)

        return result


def add_database_commands(subparsers):
    """添加数据库管理相关的CLI命令"""

    # 数据库信息命令
    db_info_parser = subparsers.add_parser("db-info", help="显示数据库基本信息")

    # 缓存统计命令
    cache_stats_parser = subparsers.add_parser("cache-stats", help="显示缓存统计信息")

    # 清理缓存命令
    clear_cache_parser = subparsers.add_parser("clear-cache", help="清理缓存数据")
    clear_cache_parser.add_argument(
        "--type", choices=["all", "evaluation", "embedding"], default="all", help="要清理的缓存类型"
    )
    clear_cache_parser.add_argument("--method", help="清理特定评估方法的缓存")

    # 优化数据库命令
    optimize_parser = subparsers.add_parser("db-optimize", help="优化数据库性能")

    # 备份数据库命令
    backup_parser = subparsers.add_parser("db-backup", help="备份主数据库")
    backup_parser.add_argument("--path", help="备份文件路径")

    # 性能分析命令
    analyze_parser = subparsers.add_parser("db-analyze", help="分析数据库性能")


def handle_database_command(args) -> int:
    """处理数据库管理命令"""
    db_commands = DatabaseCommands()

    # 从args中推断命令类型
    if getattr(args, "db_info", False):
        command = "db-info"
    elif getattr(args, "cache_stats", False):
        command = "cache-stats"
    elif getattr(args, "clear_cache", False):
        command = "clear-cache"
    elif getattr(args, "db_optimize", False):
        command = "db-optimize"
    elif getattr(args, "db_backup", False):
        command = "db-backup"
    elif getattr(args, "db_analyze", False):
        command = "db-analyze"
    elif getattr(args, "db_reset", False):
        command = "db-reset"
    else:
        print("未指定数据库管理命令")
        return 1

    try:
        if command == "db-info":
            result = db_commands.show_database_info()
            print("\n=== 数据库信息 ===")
            print(f"总大小: {result['total_size_mb']} MB")
            print("\n数据库详情:")
            for db_name, db_info in result["databases"].items():
                print(f"\n{db_name}:")
                print(f"  路径: {db_info['path']}")
                print(f"  存在: {db_info['exists']}")
                if db_info["exists"]:
                    print(f"  大小: {db_info['size_mb']} MB")
                    if "tables" in db_info:
                        print("  表统计:")
                        for table, count in db_info["tables"].items():
                            print(f"    {table}: {count} 行")

        elif command == "cache-stats":
            result = db_commands.show_cache_stats()
            print("\n=== 缓存统计信息 ===")

            if "evaluation_cache" in result:
                eval_cache = result["evaluation_cache"]
                print(f"\n评估缓存:")
                print(f"  命中率: {eval_cache.get('hit_rate', 0):.2%}")
                print(f"  内存缓存大小: {eval_cache.get('memory_cache_size', 0)}")
                print(f"  最大缓存大小: {eval_cache.get('max_cache_size', 0)}")
                if "persistent_cache" in eval_cache:
                    persistent = eval_cache["persistent_cache"]
                    print(f"  持久化缓存条目: {persistent.get('total_entries', 0)}")

            if "embedding_cache" in result:
                embed_cache = result["embedding_cache"]
                print(f"\n嵌入缓存:")
                print(f"  内存缓存大小: {embed_cache.get('memory_cache_size', 0)}")
                print(f"  内存缓存限制: {embed_cache.get('memory_cache_limit', 0)}")
                print(f"  持久化缓存大小: {embed_cache.get('persistent_cache_size', 0)}")
                print(f"  持久化启用: {embed_cache.get('persistent_enabled', False)}")

            if "summary" in result:
                summary = result["summary"]
                print(f"\n总体状态: {summary.get('overall_health', 'unknown')}")

        elif command == "clear-cache":
            cache_type = getattr(args, "cache_type", "all")
            method = getattr(args, "cache_method", None)
            result = db_commands.clear_caches(cache_type, method)

            print(f"\n=== 清理缓存 (类型: {cache_type}) ===")
            if result["cleared"]:
                print("已清理:")
                for cache_name, count in result["cleared"].items():
                    print(f"  {cache_name}: {count}")

            if result["errors"]:
                print("错误:")
                for cache_name, error in result["errors"].items():
                    print(f"  {cache_name}: {error}")

        elif command == "db-optimize":
            result = db_commands.optimize_databases()
            print("\n=== 数据库优化 ===")

            if result["optimized"]:
                print("优化完成:")
                for db_name, info in result["optimized"].items():
                    print(f"  {db_name}: {info}")

            if result["errors"]:
                print("优化错误:")
                for db_name, error in result["errors"].items():
                    print(f"  {db_name}: {error}")

        elif command == "db-backup":
            backup_path = getattr(args, "backup_path", None)
            result = db_commands.backup_database(backup_path)

            print(f"\n=== 数据库备份 ===")
            print(f"备份路径: {result['backup_path']}")
            print(f"备份成功: {result['success']}")

            if result["success"]:
                print(f"备份大小: {result['backup_size_mb']} MB")
                print(f"原始大小: {result['original_size_mb']} MB")
            elif result["error"]:
                print(f"错误: {result['error']}")

        elif command == "db-analyze":
            result = db_commands.analyze_performance()
            print("\n=== 数据库性能分析 ===")

            if "database_performance" in result:
                db_perf = result["database_performance"]
                print(f"\n数据库性能:")
                print(f"  大小: {db_perf.get('size_mb', 0)} MB")
                print(f"  页数: {db_perf.get('page_count', 0)}")
                print(f"  页大小: {db_perf.get('page_size', 0)} bytes")
                print(f"  索引数量: {db_perf.get('index_count', 0)}")

            if "cache_performance" in result:
                cache_perf = result["cache_performance"]
                print(f"\n缓存性能:")
                print(f"  评估缓存命中率: {cache_perf.get('evaluation_hit_rate', 0):.2%}")
                print(f"  状态: {cache_perf.get('status', 'unknown')}")

            if result.get("recommendations"):
                print(f"\n建议:")
                for i, rec in enumerate(result["recommendations"], 1):
                    print(f"  {i}. {rec}")

        elif command == "db-reset":
            print(f"\n⚠️  警告: 这将永久删除所有数据库内容!")
            print("请输入 'YES' 确认执行此操作:")

            try:
                confirmation = input().strip()
                if confirmation != "YES":
                    print("操作已取消")
                    return 0

                result = db_commands.reset_database()
                print(f"\n=== 数据库重置 ===")

                if result["reset"]:
                    print("已重置:")
                    for db_name, info in result["reset"].items():
                        if isinstance(info, dict):
                            print(f"  {db_name}:")
                            if "tables_cleared" in info:
                                for table, count in info["tables_cleared"].items():
                                    print(f"    {table}: 删除 {count} 行")
                            if info.get("sequences_reset"):
                                print(f"    自增序列已重置")
                        else:
                            print(f"  {db_name}: {info}")

                if result["errors"]:
                    print("错误:")
                    for db_name, error in result["errors"].items():
                        print(f"  {db_name}: {error}")

                print(f"\n✅ 数据库重置完成!")

            except KeyboardInterrupt:
                print(f"\n操作已取消")
                return 0
            except Exception as e:
                print(f"确认输入时出错: {e}")
                return 1

        else:
            print(f"未知命令: {command}")
            return 1

        return 0

    except Exception as e:
        print(f"执行命令时出错: {e}")
        import traceback

        traceback.print_exc()
        return 1

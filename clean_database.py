#!/usr/bin/env python
"""
清理数据库脚本 - 创建干净的开发环境
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

def main():
    print("🧹 清理数据库 - 创建干净的开发环境")
    print("=" * 40)
    
    # 备份现有数据库文件
    backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 查找并备份数据库文件
    db_files = [
        "tasks.db", 
        "evaluation_cache.db",
        "data/databases/main/tasks.db"
    ]
    
    backup_dir = Path("data/databases/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    for db_file in db_files:
        if Path(db_file).exists():
            backup_name = f"{Path(db_file).stem}_backup_{backup_timestamp}.db"
            backup_path = backup_dir / backup_name
            shutil.copy2(db_file, backup_path)
            print(f"✅ 备份: {db_file} -> {backup_path}")
            
            # 删除原文件
            os.remove(db_file)
            print(f"🗑️  删除: {db_file}")
    
    # 删除相关的临时文件
    temp_files = [
        "tasks.db-shm", 
        "tasks.db-wal",
        "evaluation_cache.db-shm",
        "evaluation_cache.db-wal"
    ]
    
    for temp_file in temp_files:
        if Path(temp_file).exists():
            os.remove(temp_file)
            print(f"🗑️  删除临时文件: {temp_file}")
    
    # 重新初始化数据库
    print("\n🔧 重新初始化数据库...")
    try:
        from app.database import init_db
        init_db()
        print("✅ 数据库重新初始化完成")
        
        # 验证新数据库
        from app.database_pool import get_db
        with get_db() as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            print(f"📊 新数据库包含 {len(tables)} 个表")
            
            # 检查任务表是否为空
            if any("tasks" in table[0] for table in tables):
                task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                print(f"📋 任务表: {task_count} 条记录（应该为0）")
        
    except Exception as e:
        print(f"❌ 数据库初始化错误: {e}")
        return False
    
    print("\n🎉 数据库清理完成！现在是完全干净的环境")
    return True

if __name__ == "__main__":
    main()

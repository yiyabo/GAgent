#!/usr/bin/env python3
"""
检查 Bio Tools 数据库和运行结果
==============================
验证数据库配置和展示已有的运行结果
"""

import os
from pathlib import Path

# 数据库路径配置
DATABASES = {
    "bakta": "/home/zczhao/GAgent/data/databases/bio_tools/bakta/db",
    "checkm": "/home/zczhao/GAgent/data/databases/bio_tools/checkm_data",
    "checkv": "/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5",
    "genomad": "/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db",
    "gtdbtk": "/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data",
    "virsorter2": "/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db",
}

# 运行结果路径
RESULT_DIRS = {
    "genomad": "/home/zczhao/GAgent/data/experiment_nature/experiment_A/genomad_results_fixed",
    "virsorter2": "/home/zczhao/GAgent/data/experiment_nature/experiment_A/virsorter2_results",
}


def check_database(name, path):
    """检查数据库是否存在并计算大小"""
    db_path = Path(path)
    if not db_path.exists():
        return False, 0, "不存在"
    
    # 计算目录大小
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(db_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total_size += os.path.getsize(fp)
    except Exception as e:
        return True, 0, f"无法计算大小: {e}"
    
    size_gb = total_size / (1024**3)
    return True, size_gb, "正常"


def check_results(name, path):
    """检查运行结果"""
    result_path = Path(path)
    if not result_path.exists():
        return 0, []
    
    # 获取所有子目录（样本结果）
    samples = [d for d in result_path.iterdir() if d.is_dir()]
    return len(samples), [s.name for s in samples[:10]]  # 只显示前10个


def main():
    print("=" * 70)
    print("🗄️  Bio Tools 数据库状态检查")
    print("=" * 70)
    
    print("\n📂 数据库配置:\n")
    print(f"{'工具名':<15} {'路径':<60} {'状态':<10} {'大小':<10}")
    print("-" * 100)
    
    for name, path in DATABASES.items():
        exists, size_gb, status = check_database(name, path)
        size_str = f"{size_gb:.1f} GB" if exists else "N/A"
        status_icon = "✅" if exists else "❌"
        print(f"{name:<15} {path:<60} {status_icon} {status:<8} {size_str:<10}")
    
    print("\n" + "=" * 70)
    print("📊 运行结果统计")
    print("=" * 70)
    
    for name, path in RESULT_DIRS.items():
        count, samples = check_results(name, path)
        print(f"\n{name}:")
        print(f"  结果目录: {path}")
        print(f"  已处理样本数: {count}")
        if samples:
            print(f"  示例: {', '.join(samples[:5])}")
    
    print("\n" + "=" * 70)
    print("✅ 数据库检查完成!")
    print("=" * 70)
    
    # 总结
    all_exist = all(check_database(name, path)[0] for name, path in DATABASES.items())
    if all_exist:
        print("\n🎉 所有数据库均已安装!")
    else:
        print("\n⚠️  部分数据库缺失，请检查配置")


if __name__ == "__main__":
    main()

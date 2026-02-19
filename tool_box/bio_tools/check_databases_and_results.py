#!/usr/bin/env python3
"""
 Bio Tools 
==============================

"""

import os
from pathlib import Path

# 
DATABASES = {
    "bakta": "/home/zczhao/GAgent/data/databases/bio_tools/bakta/db",
    "checkm": "/home/zczhao/GAgent/data/databases/bio_tools/checkm_data",
    "checkv": "/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5",
    "genomad": "/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db",
    "gtdbtk": "/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data",
    "virsorter2": "/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db",
}

# 
RESULT_DIRS = {
    "genomad": "/home/zczhao/GAgent/data/experiment_nature/experiment_A/genomad_results_fixed",
    "virsorter2": "/home/zczhao/GAgent/data/experiment_nature/experiment_A/virsorter2_results",
}


def check_database(name, path):
    """"""
    db_path = Path(path)
    if not db_path.exists():
        return False, 0, ""
    
    # 
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(db_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total_size += os.path.getsize(fp)
    except Exception as e:
        return True, 0, f": {e}"
    
    size_gb = total_size / (1024**3)
    return True, size_gb, ""


def check_results(name, path):
    """"""
    result_path = Path(path)
    if not result_path.exists():
        return 0, []
    
    # （）
    samples = [d for d in result_path.iterdir() if d.is_dir()]
    return len(samples), [s.name for s in samples[:10]]  # 10


def main():
    print("=" * 70)
    print("🗄️  Bio Tools ")
    print("=" * 70)
    
    print("\n📂 :\n")
    print(f"{'':<15} {'':<60} {'':<10} {'':<10}")
    print("-" * 100)
    
    for name, path in DATABASES.items():
        exists, size_gb, status = check_database(name, path)
        size_str = f"{size_gb:.1f} GB" if exists else "N/A"
        status_icon = "✅" if exists else "❌"
        print(f"{name:<15} {path:<60} {status_icon} {status:<8} {size_str:<10}")
    
    print("\n" + "=" * 70)
    print("📊 ")
    print("=" * 70)
    
    for name, path in RESULT_DIRS.items():
        count, samples = check_results(name, path)
        print(f"\n{name}:")
        print(f"  : {path}")
        print(f"  : {count}")
        if samples:
            print(f"  : {', '.join(samples[:5])}")
    
    print("\n" + "=" * 70)
    print("✅ !")
    print("=" * 70)
    
    # 
    all_exist = all(check_database(name, path)[0] for name, path in DATABASES.items())
    if all_exist:
        print("\n🎉 !")
    else:
        print("\n⚠️  ，")


if __name__ == "__main__":
    main()

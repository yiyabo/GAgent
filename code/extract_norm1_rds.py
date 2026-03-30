#!/usr/bin/env python3
"""
提取 GSE184880 质控后未分群文件.zip 中的 norm1.rds 文件
并创建提取清单 TSV
"""

import os
import zipfile
import shutil
import stat

# 配置路径
DATA_DIR = '/Users/apple/LLM/agent/data/张老师卵巢癌单细胞数据/张老师卵巢癌单细胞数据'
SOURCE_ZIP = 'GSE184880 质控后未分群文件.zip'
ZIP_MEMBER = 'GSE184880 质控后未分群文件/norm1.rds'

OUTPUT_BASE = '/Users/apple/LLM/agent/results/ovarian_scRNA_project/03_qc_rds_files'
EXTRACTED_DIR = os.path.join(OUTPUT_BASE, 'extracted')
OUTPUT_RDS = os.path.join(EXTRACTED_DIR, 'norm1.rds')
MANIFEST_TSV = os.path.join(OUTPUT_BASE, 'extraction_manifest_norm1.tsv')


def ensure_dirs():
    """创建必要的目录"""
    os.makedirs(EXTRACTED_DIR, exist_ok=True)
    print(f"已创建输出目录：{EXTRACTED_DIR}")


def extract_zip_member(zip_path, member_name, dest_path):
    """从 zip 中提取指定文件到目标路径"""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # 动态查找成员（避免编码问题）
        actual_member = None
        for name in zf.namelist():
            if name.endswith('norm1.rds'):
                actual_member = name
                break

        if actual_member is None:
            raise KeyError(f"未找到包含 norm1.rds 的成员，可用：{zf.namelist()[:15]}")

        print(f"  使用 zip 成员：{repr(actual_member)}")

        # 读取压缩文件内容
        with zf.open(actual_member) as src:
            # 写入目标文件
            with open(dest_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
    print(f"已提取 {actual_member} -> {dest_path}")


def get_file_size(path):
    """获取文件大小（字节）"""
    return os.path.getsize(path)


def write_manifest(manifest_path, sample_id, source_zip, zip_member, extracted_path, size_bytes, exists):
    """写入清单 TSV 文件"""
    with open(manifest_path, 'w', encoding='utf-8') as f:
        # 写入表头
        f.write("sample_id\tsource_zip\tzip_member\textracted_path\tsize_bytes\texists\n")
        # 写入数据行
        f.write(f"{sample_id}\t{source_zip}\t{zip_member}\t{extracted_path}\t{size_bytes}\t{exists}\n")
    print(f"已写入清单：{manifest_path}")


def read_manifest(manifest_path):
    """读取并打印清单内容"""
    print("\n" + "="*60)
    print("清单文件内容:")
    print("="*60)
    with open(manifest_path, 'r', encoding='utf-8') as f:
        content = f.read()
        print(content)
    print("="*60)


def list_extracted_dir(dir_path):
    """列出提取目录内容"""
    print(f"\n提取目录内容 ({dir_path}):")
    if os.path.exists(dir_path):
        for item in os.listdir(dir_path):
            full_path = os.path.join(dir_path, item)
            size = get_file_size(full_path)
            print(f"  {item} ({size:,} 字节)")
    else:
        print("  目录不存在!")


def main():
    print("="*60)
    print("GSE184880 norm1.rds 提取脚本")
    print("="*60)

    # 1. 创建目录
    ensure_dirs()

    # 2. 构建源 zip 路径（使用 listdir 避免编码问题）
    zip_files = os.listdir(DATA_DIR)
    source_zip_actual = None
    for f in zip_files:
        if 'GSE184880' in f and '质控后未分群' in f:
            source_zip_actual = f
            break

    if not source_zip_actual:
        # 尝试精确匹配
        for f in zip_files:
            if f == SOURCE_ZIP:
                source_zip_actual = f
                break

    if not source_zip_actual:
        print(f"目录内容：{zip_files}")
        raise FileNotFoundError(f"未找到源 zip 文件：{SOURCE_ZIP}")

    zip_path = os.path.join(DATA_DIR, source_zip_actual)
    print(f"源 zip 文件：{zip_path}")

    # 3. 提取文件
    extract_zip_member(zip_path, ZIP_MEMBER, OUTPUT_RDS)

    # 4. 验证提取并获取大小
    exists = os.path.exists(OUTPUT_RDS)
    size_bytes = get_file_size(OUTPUT_RDS) if exists else 0

    print(f"提取完成：exists={exists}, size={size_bytes:,} 字节")

    # 5. 写入清单
    write_manifest(
        manifest_path=MANIFEST_TSV,
        sample_id='norm1',
        source_zip=source_zip_actual,
        zip_member=ZIP_MEMBER,
        extracted_path=OUTPUT_RDS,
        size_bytes=size_bytes,
        exists=exists
    )

    # 6. 读取并打印清单
    read_manifest(MANIFEST_TSV)

    # 7. 列出提取目录确认文件可见
    list_extracted_dir(EXTRACTED_DIR)

    print("\n提取任务完成!")


if __name__ == '__main__':
    main()

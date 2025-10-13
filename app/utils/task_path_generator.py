"""
任务路径生成器 - 自动根据任务层级生成文件路径

根据任务层级自动生成文件目录结构：
- ROOT任务 → results/[root_name]/
- COMPOSITE任务 → results/[root_name]/[composite_name]/
- ATOMIC任务 → results/[root_name]/[composite_name]/[atomic_name].md
"""

import os
import re
from pathlib import Path
from typing import Optional, Tuple


def slugify(text: str) -> str:
    """将文本转换为安全的文件名/目录名"""
    # 移除特殊前缀 (ROOT:, COMPOSITE:, ATOMIC:)
    text = re.sub(r'^(ROOT|COMPOSITE|ATOMIC):\s*', '', text, flags=re.IGNORECASE)
    
    # 转换为小写并替换空格和特殊字符
    text = text.strip().lower()
    text = re.sub(r'[^\w\s-]', '', text)  # 移除非字母数字字符
    text = re.sub(r'[\s_]+', '_', text)   # 空格转下划线
    text = re.sub(r'-+', '-', text)        # 多个连字符转单个
    
    # 限制长度
    if len(text) > 50:
        text = text[:50]
    
    return text or 'unnamed'


def get_task_file_path(task: dict, repo=None) -> str:
    """
    根据任务层级自动生成文件路径
    
    Args:
        task: 任务对象 (dict或tuple)
        repo: 任务仓库（用于查询父任务）
    
    Returns:
        文件路径字符串
        
    Examples:
        ROOT任务: "results/root_task_name/"
        COMPOSITE任务: "results/root_name/composite_name/"
        ATOMIC任务: "results/root_name/composite_name/atomic_name.md"
    """
    # 解析任务信息
    if isinstance(task, dict):
        task_id = task.get('id')
        task_name = task.get('name', 'unnamed')
        task_type = task.get('task_type', 'atomic')
        parent_id = task.get('parent_id')
        root_id = task.get('root_id')
    else:
        # tuple format: (id, name, status, ...)
        task_id = task[0] if len(task) > 0 else None
        task_name = task[1] if len(task) > 1 else 'unnamed'
        task_type = task[7] if len(task) > 7 else 'atomic'
        parent_id = task[5] if len(task) > 5 else None
        root_id = task[10] if len(task) > 10 else None
    
    # 清理任务名称
    clean_name = slugify(task_name)
    
    # ROOT任务 - 创建根目录
    if task_type == 'root':
        return f"results/{clean_name}/"
    
    # COMPOSITE和ATOMIC任务 - 需要构建完整路径
    path_parts = [clean_name]
    
    # 向上追溯父任务
    if repo and parent_id:
        try:
            parent_task = repo.get_task_info(parent_id)
            if parent_task:
                parent_name = parent_task.get('name') if isinstance(parent_task, dict) else parent_task[1]
                parent_type = parent_task.get('task_type') if isinstance(parent_task, dict) else parent_task[7]
                parent_clean = slugify(parent_name)
                
                # 如果父任务是ROOT，它是第一层
                if parent_type == 'root':
                    path_parts.insert(0, parent_clean)
                # 如果父任务是COMPOSITE，继续向上找ROOT
                elif parent_type == 'composite':
                    path_parts.insert(0, parent_clean)
                    # 找到ROOT
                    if root_id and root_id != parent_id:
                        root_task = repo.get_task_info(root_id)
                        if root_task:
                            root_name = root_task.get('name') if isinstance(root_task, dict) else root_task[1]
                            root_clean = slugify(root_name)
                            path_parts.insert(0, root_clean)
        except Exception as e:
            print(f"Warning: Failed to resolve parent task: {e}")
    
    # 如果没有parent_id但有root_id，直接使用root
    if repo and root_id and not parent_id:
        try:
            root_task = repo.get_task_info(root_id)
            if root_task:
                root_name = root_task.get('name') if isinstance(root_task, dict) else root_task[1]
                root_clean = slugify(root_name)
                if root_clean not in path_parts:
                    path_parts.insert(0, root_clean)
        except Exception:
            pass
    
    # 如果路径部分只有一个，添加默认根目录
    if len(path_parts) == 1:
        path_parts.insert(0, 'default_project')
    
    # COMPOSITE任务 - 返回目录
    if task_type == 'composite':
        return f"results/{'/'.join(path_parts)}/"
    
    # ATOMIC任务 - 返回.md文件
    return f"results/{'/'.join(path_parts)}.md"


def ensure_task_directory(file_path: str) -> bool:
    """
    确保任务文件的目录存在
    
    Args:
        file_path: 文件路径
        
    Returns:
        是否成功创建/确认目录
    """
    try:
        path = Path(file_path)
        # 如果是目录路径（以/结尾），直接创建
        if file_path.endswith('/'):
            path.mkdir(parents=True, exist_ok=True)
        else:
            # 如果是文件路径，创建父目录
            path.parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        print(f"Failed to create directory for {file_path}: {e}")
        return False


def get_task_output_path(task: dict, repo=None) -> Tuple[str, str]:
    """
    获取任务输出路径（同时返回目录和完整路径）
    
    Returns:
        (directory_path, full_file_path)
    """
    full_path = get_task_file_path(task, repo)
    
    if full_path.endswith('/'):
        # 目录路径
        return (full_path, full_path)
    else:
        # 文件路径
        directory = str(Path(full_path).parent) + '/'
        return (directory, full_path)

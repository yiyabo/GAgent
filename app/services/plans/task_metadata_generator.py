"""
Deterministic acceptance_criteria and artifact_contract generator for tasks.

This module provides structured generation of task metadata when LLM-generated
metadata is missing or incomplete. Unlike regex-based fallback (which runs at
execution time), this runs at task creation time and uses explicit pattern
matching to avoid false positives.
"""
import re
from typing import Dict, List, Optional, Any


_OUTPUT_PATH_PATTERNS = [
    r"保存到\s*[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"输出到\s*[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"写入到?\s*[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"下载到\s*[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"生成.*?到\s*[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"创建.*?到\s*[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"save\s+to\s+[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"output\s+to\s+[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"write\s+to\s+[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"download\s+to\s+[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"generate.*?to\s+[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
    r"create.*?to\s+[`'\"]?([^\s`'\"，。！？；：]+)[`'\"]?",
]

_OUTPUT_KEYWORDS = [
    "生成", "创建", "输出", "导出", "写入", "保存",
    "generate", "create", "output", "export", "write", "save",
]

_ANALYSIS_KEYWORDS = [
    "分析", "计算", "评估", "统计", "比较",
    "analyze", "compute", "evaluate", "calculate", "compare",
]

_FETCH_KEYWORDS = [
    "下载", "获取", "抓取", "拉取",
    "download", "fetch", "retrieve", "pull",
]


def _extract_explicit_output_paths(text: str) -> List[str]:
    """
    Extract explicit output paths from instruction text.
    
    Only matches clear patterns like "保存到 X", "output to X", etc.
    Does NOT scan for arbitrary file paths (which causes false positives).
    """
    paths = []
    
    for pattern in _OUTPUT_PATH_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        paths.extend(matches)
    
    seen = set()
    unique_paths = []
    for path in paths:
        path = path.strip()
        if path and path not in seen:
            seen.add(path)
            unique_paths.append(path)
    
    return unique_paths


def generate_acceptance_criteria(
    task_name: str,
    instruction: str,
) -> Optional[Dict[str, Any]]:
    """
    Generate acceptance_criteria based on task name and instruction.
    
    Uses explicit pattern matching instead of scanning for arbitrary file paths.
    Returns None if no clear output pattern is found (conservative approach).
    
    Args:
        task_name: The task's display name
        instruction: The task's detailed instruction
        
    Returns:
        A dict with 'category', 'blocking', and 'checks' keys, or None if
        no clear acceptance criteria can be determined.
    """
    name_lower = task_name.lower()
    instr_lower = instruction.lower()
    
    checks = []
    
    if any(kw in name_lower for kw in _OUTPUT_KEYWORDS):
        output_paths = _extract_explicit_output_paths(instruction)
        for path in output_paths:
            checks.append({"type": "file_nonempty", "path": path})
    
    elif any(kw in name_lower for kw in _ANALYSIS_KEYWORDS):
        return None
    
    elif any(kw in name_lower for kw in _FETCH_KEYWORDS):
        output_paths = _extract_explicit_output_paths(instruction)
        for path in output_paths:
            checks.append({"type": "file_exists", "path": path})
    
    else:
        return None
    
    if not checks:
        return None
    
    return {
        "category": "file_data",
        "blocking": True,
        "checks": checks,
    }


def generate_artifact_contract(
    task_name: str,
    instruction: str,
    acceptance_criteria: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate artifact_contract based on task name and acceptance_criteria.
    
    Args:
        task_name: The task's display name
        instruction: The task's detailed instruction
        acceptance_criteria: Optional acceptance_criteria dict (if already generated)
        
    Returns:
        A dict with 'requires' and/or 'publishes' keys, or None if no
        clear contract can be determined.
    """
    contract = {
        "requires": [],
        "publishes": [],
    }
    
    if acceptance_criteria and "checks" in acceptance_criteria:
        for check in acceptance_criteria["checks"]:
            if check.get("type") in ("file_exists", "file_nonempty"):
                path = check.get("path", "")
                if path:
                    filename = path.split("/")[-1] if "/" in path else path
                    alias = f"output.{filename}"
                    contract["publishes"].append(alias)
    
    if not contract["publishes"]:
        return None
    
    return contract


def ensure_task_metadata(
    metadata: Optional[Dict[str, Any]],
    task_name: str,
    instruction: str,
) -> Dict[str, Any]:
    """
    Ensure metadata contains acceptance_criteria and artifact_contract.
    
    If LLM already generated these fields and they're valid, preserve them.
    Otherwise, generate them deterministically.
    
    Args:
        metadata: Existing metadata dict (may be None)
        task_name: The task's display name
        instruction: The task's detailed instruction
        
    Returns:
        Updated metadata dict with acceptance_criteria and artifact_contract
    """
    if metadata is None:
        metadata = {}
    else:
        metadata = dict(metadata)
    
    if "acceptance_criteria" in metadata:
        ac = metadata["acceptance_criteria"]
        if not isinstance(ac, dict) or "checks" not in ac or not isinstance(ac["checks"], list):
            metadata.pop("acceptance_criteria")
    
    if "acceptance_criteria" not in metadata:
        ac = generate_acceptance_criteria(task_name, instruction)
        if ac:
            metadata["acceptance_criteria"] = ac
    
    if "artifact_contract" in metadata:
        contract = metadata["artifact_contract"]
        if not isinstance(contract, dict):
            metadata.pop("artifact_contract")
        elif not contract.get("requires") and not contract.get("publishes"):
            metadata.pop("artifact_contract")
    
    if "artifact_contract" not in metadata:
        contract = generate_artifact_contract(
            task_name,
            instruction,
            acceptance_criteria=metadata.get("acceptance_criteria"),
        )
        if contract:
            metadata["artifact_contract"] = contract
    
    return metadata

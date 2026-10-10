"""
Deterministic acceptance_criteria and artifact_contract generator for tasks.

This module provides structured generation of task metadata when LLM-generated
metadata is missing or incomplete. Unlike regex-based fallback (which runs at
execution time), this runs at task creation time and uses explicit pattern
matching to avoid false positives.
"""
import re
from typing import Dict, List, Optional, Any


INFERRED_TEXT_SOURCE = "inferred_text"


def is_inferred_task_spec(spec: Any) -> bool:
    """Only an explicit provenance marker identifies a text-derived spec.

    Historical unmarked blocks stay structured/explicit: filename similarity
    cannot distinguish intentional requirements from old generated metadata.
    """
    return isinstance(spec, dict) and spec.get("source") == INFERRED_TEXT_SOURCE


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
        "source": INFERRED_TEXT_SOURCE,
    }


def generate_artifact_contract(
    task_name: str,
    instruction: str,
    acceptance_criteria: Optional[Dict[str, Any]] = None,
    required_outputs: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate artifact_contract from the task's declared output paths.

    Aliases come from ``required_outputs[].target_path`` and from
    ``acceptance_criteria.checks`` file paths, in the registrable dynamic form
    ``output.<stem>_<ext>`` (LOCAL_INFRA §119). The previous ``output.<file.ext>``
    form carried a second dot, never matched the alias grammar, and was silently
    dropped by ``_extract_explicit_aliases`` — so text-derived contracts could
    never register a product. The contract stays ``source=inferred_text``:
    it registers the file when it is produced but does not fail the task when
    it is not (only explicit decomposer declarations are authoritative).

    Args:
        task_name: The task's display name
        instruction: The task's detailed instruction
        acceptance_criteria: Optional acceptance_criteria dict (if already generated)
        required_outputs: Optional ``required_outputs`` declarations from the planner

    Returns:
        A dict with 'requires' and 'publishes' keys, or None if no
        registrable output path is declared.
    """
    from .artifact_contracts import dynamic_artifact_alias  # lazy: artifact_contracts imports this module

    contract: Dict[str, Any] = {
        "requires": [],
        "publishes": [],
        "source": INFERRED_TEXT_SOURCE,
    }
    seen: set = set()

    def _add(path: Any) -> None:
        alias = dynamic_artifact_alias("output", str(path or ""))
        if alias and alias not in seen:
            seen.add(alias)
            contract["publishes"].append(alias)

    if isinstance(required_outputs, list):
        for item in required_outputs:
            if isinstance(item, dict) and item.get("target_path"):
                _add(item.get("target_path"))

    if acceptance_criteria and "checks" in acceptance_criteria:
        for check in acceptance_criteria["checks"]:
            if isinstance(check, dict) and check.get("type") in ("file_exists", "file_nonempty"):
                _add(check.get("path", ""))

    if not contract["publishes"]:
        return None

    return contract


def _declared_required_outputs(metadata: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Planner ``required_outputs`` live at the top level or inside ``output_spec``."""
    direct = metadata.get("required_outputs")
    if isinstance(direct, list):
        return direct
    spec = metadata.get("output_spec")
    if isinstance(spec, dict) and isinstance(spec.get("required_outputs"), list):
        return spec["required_outputs"]
    return None


def ensure_task_metadata(
    metadata: Optional[Dict[str, Any]],
    task_name: str,
    instruction: str,
) -> Dict[str, Any]:
    """
    Ensure metadata contains acceptance_criteria and artifact_contract.
    
    If LLM already generated these fields and they're valid, preserve them.
    Otherwise, generate them deterministically.
    Text-derived blocks retain ``source=inferred_text`` when saved or copied;
    unmarked existing structured blocks are preserved as explicit requirements.
    
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
            required_outputs=_declared_required_outputs(metadata),
        )
        if contract:
            metadata["artifact_contract"] = contract
    
    return metadata

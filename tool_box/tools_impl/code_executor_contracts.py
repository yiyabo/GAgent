"""Task contracts and execution-spec helpers for :mod:`code_executor`."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.services.plans.acceptance_criteria import (
    derive_acceptance_criteria_from_text,
    derive_expected_deliverables,
    resolve_glob_min_count,
    resolve_glob_pattern,
)
from app.services.plans.artifact_validation import get_artifact_validation_prompt_specs
from app.services.session_paths import get_runtime_session_dir
from app.services.resources.resource_registry import resolve_resources as _resolve_registered_resources

logger = logging.getLogger(__name__)


def _ce():
    from tool_box.tools_impl import code_executor

    return code_executor


def _facade_path(name: str):
    return getattr(_ce(), name)


_SUPERVISED_ML_PROMPT_ALIASES = {
    "ml_traditional.validation_metrics_json",
    "ml_traditional.model_checkpoints_dir",
    "phage_ml.cv_metrics_json",
    "phage_ml.trained_models_dir",
    "phage_ml.training_metadata_parquet",
    "phage_ml.feature_row_ids_json",
    "phage_ml.label_alignment_json",
}


def _format_cli_acceptance_checks(criteria: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(criteria, dict):
        return []
    checks = criteria.get("checks")
    if not isinstance(checks, list):
        return []

    formatted: List[str] = []
    for raw_check in checks[:12]:
        if not isinstance(raw_check, dict):
            continue
        check_type = str(raw_check.get("type") or "").strip()
        if check_type == "file_exists":
            formatted.append(f"file must exist: {raw_check.get('path')}")
        elif check_type == "file_nonempty":
            formatted.append(f"file must be non-empty: {raw_check.get('path')}")
        elif check_type == "glob_count_at_least":
            pattern = resolve_glob_pattern(raw_check)
            min_count = resolve_glob_min_count(raw_check)
            formatted.append(
                f"at least {min_count} matches for glob: {pattern}"
            )
        elif check_type == "text_contains":
            formatted.append(
                f"text file {raw_check.get('path')} must contain: {raw_check.get('pattern')}"
            )
        else:
            formatted.append(json.dumps(raw_check, ensure_ascii=False))
    return formatted


def _normalize_resolved_resources(value: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize resource context by re-resolving IDs through the trusted registry."""
    if not isinstance(value, dict):
        return {}
    resource_ids = [str(raw_id or "").strip() for raw_id in value.keys() if str(raw_id or "").strip()]
    resolved, missing = _resolve_registered_resources(resource_ids)
    if missing:
        logger.warning("Ignoring unresolved code_executor resource IDs: %s", missing)
    return resolved


def _resource_read_dirs(resolved_resources: Dict[str, Dict[str, Any]]) -> List[str]:
    dirs: List[str] = []
    seen: set[str] = set()
    for info in resolved_resources.values():
        candidates: List[Any] = [info.get("root"), info.get("resolved_root")]
        required_paths = info.get("required_paths")
        if isinstance(required_paths, list):
            candidates.extend(required_paths)
        for raw_path in candidates:
            token = str(raw_path or "").strip()
            if not token or token in seen:
                continue
            try:
                path = Path(token).absolute()
                if not path.exists() or not path.is_dir():
                    continue
            except OSError:
                continue
            text = str(path)
            if text not in seen:
                seen.add(text)
                dirs.append(text)
    return dirs


def _format_resolved_resources_for_prompt(resolved_resources: Dict[str, Dict[str, Any]]) -> str:
    if not resolved_resources:
        return ""
    lines: List[str] = ["", "Available external resources:"]
    for resource_id, info in sorted(resolved_resources.items()):
        name = str(info.get("name") or resource_id).strip()
        root = str(info.get("root") or "").strip()
        resolved_root = str(info.get("resolved_root") or "").strip()
        lines.append(f"- resource:{resource_id} ({name})")
        if root:
            lines.append(f"  root: {root}")
        if resolved_root and resolved_root != root:
            lines.append(f"  resolved_root: {resolved_root}")
        required_paths = info.get("required_paths")
        if isinstance(required_paths, list) and required_paths:
            lines.append("  required paths:")
            for path in required_paths[:8]:
                lines.append(f"    - {path}")
        hints = info.get("format_hints")
        if isinstance(hints, list) and hints:
            lines.append("  format hints:")
            for hint in hints[:8]:
                lines.append(f"    - {hint}")
        metadata = info.get("metadata")
        if isinstance(metadata, dict):
            primary = str(metadata.get("primary_subdir") or "").strip()
            file_glob = str(metadata.get("file_glob") or "").strip()
            if primary:
                lines.append(f"  primary_subdir: {primary}")
            if file_glob:
                lines.append(f"  file_glob: {file_glob}")
    return "\n".join(lines)


def _format_supervised_ml_contract_for_prompt(
    artifact_contract: Optional[Dict[str, Any]],
    resolved_inputs: Optional[Dict[str, str]],
) -> str:
    if not isinstance(artifact_contract, dict):
        return ""
    aliases = {
        str(item).strip()
        for item in [
            *(artifact_contract.get("requires") or []),
            *(artifact_contract.get("publishes") or []),
        ]
        if str(item).strip()
    }
    if not aliases.intersection(_SUPERVISED_ML_PROMPT_ALIASES):
        return ""
    resolved = resolved_inputs if isinstance(resolved_inputs, dict) else {}
    lines = [
        "",
        "Supervised ML contract requirements:",
        "- Use only real labels from required label/metadata artifacts. Do NOT synthesize, randomize, balance-fabricate, or dummy-generate labels.",
        "- Align labels to feature rows using the required row-id/alignment artifacts; if alignment cannot be proven, fail explicitly instead of training.",
        "- Metrics must record label_source, label_alignment, and training_samples so downstream verification can audit provenance.",
    ]
    for alias in ("phage_ml.training_metadata_parquet", "phage_ml.feature_row_ids_json"):
        if alias in aliases:
            path = str(resolved.get(alias) or "").strip()
            if path:
                lines.append(f"- Required supervised input {alias}: {path}")
            else:
                lines.append(f"- Required supervised input {alias} must resolve before valid training; report BLOCKED_DEPENDENCY if unavailable.")
    if "phage_ml.label_alignment_json" in aliases:
        lines.append("- Publish phage_ml.label_alignment_json with real label provenance and row-alignment evidence.")
    return "\n".join(lines)


def _build_cli_task_contract(
    task: str,
    execution_spec: Optional[Dict[str, Any]],
    resolved_resources: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    task_text = str(task or "").strip()
    if not isinstance(execution_spec, dict):
        return task_text

    lines: List[str] = ["[BOUND TASK CONTEXT]"]
    is_verification_only = _is_verification_only_task(task_text)
    task_id = execution_spec.get("task_id")
    task_name = str(execution_spec.get("task_name") or "").strip()
    task_instruction = str(execution_spec.get("task_instruction") or "").strip()
    dependency_outputs = execution_spec.get("dependency_outputs")

    if task_id is not None:
        lines.append(f"Task ID: {task_id}")
    if task_name:
        lines.append(f"Task Name: {task_name}")

    if task_instruction and not is_verification_only:
        lines.extend(["", "Atomic task objective:", task_instruction])

    if task_text and task_text != task_instruction:
        lines.extend(["", "Requested execution action:", task_text])
        if is_verification_only:
            lines.extend([
                "",
                "Verification-only mode:",
                "- Execute only the requested inspection of existing files/artifacts.",
                "- Do not regenerate task outputs, rerun the original task objective, or create replacement deliverables.",
                "- Report the observed file headers, row counts, columns, and any validation failures.",
            ])

    if isinstance(dependency_outputs, list) and dependency_outputs:
        lines.extend(["", "Upstream dependencies:"])
        for dep in dependency_outputs[:6]:
            if not isinstance(dep, dict):
                continue
            dep_name = str(dep.get("task_name") or dep.get("task_id") or "unknown").strip()
            dep_status = str(dep.get("status") or "unknown").strip()
            artifact_paths = dep.get("artifact_paths")
            if isinstance(artifact_paths, list) and artifact_paths:
                joined = "; ".join(
                    str(item).strip() for item in artifact_paths[:4] if str(item).strip()
                )
                if len(artifact_paths) > 4:
                    joined += "; ..."
                lines.append(f"- {dep_name} [{dep_status}] -> {joined}")
            else:
                lines.append(f"- {dep_name} [{dep_status}]")

    artifact_contract = execution_spec.get("artifact_contract")
    if isinstance(artifact_contract, dict):
        requires = [str(item).strip() for item in artifact_contract.get("requires") or [] if str(item).strip()]
        publishes = [str(item).strip() for item in artifact_contract.get("publishes") or [] if str(item).strip()]
        if requires or publishes:
            lines.extend(["", "Artifact contract aliases:"])
            if requires:
                lines.append("- requires: " + ", ".join(requires))
            if publishes:
                lines.append("- publishes: " + ", ".join(publishes))
    resolved_inputs = execution_spec.get("resolved_input_artifacts")
    if isinstance(resolved_inputs, dict) and resolved_inputs:
        lines.extend(["", "Resolved required input artifacts:"])
        for alias, path in list(resolved_inputs.items())[:12]:
            lines.append(f"- {alias}: {path}")
    dependency_paths = execution_spec.get("dependency_artifact_paths")
    if isinstance(dependency_paths, list) and dependency_paths:
        lines.extend(["", "Dependency artifact paths:"])
        for path in dependency_paths[:12]:
            text = str(path or "").strip()
            if text:
                lines.append(f"- {text}")

    formatted_checks = _format_cli_acceptance_checks(
        execution_spec.get("acceptance_criteria")
    )
    if formatted_checks:
        lines.extend(["", "Deterministic acceptance criteria:"])
        lines.extend(f"- {item}" for item in formatted_checks)
        lines.extend([
            "- The plan contract is authoritative: required deliverables must match these criteria exactly.",
            "- Extra outputs are allowed, but they do NOT substitute for missing required outputs.",
        ])

    artifact_contract = execution_spec.get("artifact_contract")
    publishes = artifact_contract.get("publishes") if isinstance(artifact_contract, dict) else None
    schema_specs = get_artifact_validation_prompt_specs(publishes or [])
    if schema_specs:
        lines.extend(["", "Expected artifact schemas:"])
        for alias, spec in sorted(schema_specs.items()):
            lines.append(f"- {alias}: {json.dumps(spec, ensure_ascii=False)}")
        lines.extend([
            "- These schemas are authoritative: artifacts must be structurally loadable, not merely present/non-empty.",
            "- For sparse_npz outputs, write a SciPy sparse matrix using scipy.sparse.save_npz and ensure shape rows/columns are non-zero.",
            "- For numpy_npy outputs, write a non-empty NumPy array with numpy.save.",
            "- For directory_glob outputs, create the directory and at least the required checkpoint/metric files inside it.",
        ])

    supervised_text = _format_supervised_ml_contract_for_prompt(artifact_contract, resolved_inputs if isinstance(resolved_inputs, dict) else {})
    if supervised_text:
        lines.append(supervised_text)

    resource_text = _format_resolved_resources_for_prompt(resolved_resources or {})
    if resource_text:
        lines.append(resource_text)

    return "\n".join(lines).strip() or task_text


def _final_response_contract_prompt() -> str:
    return (
        "Final response contract:\n"
        "- End with a JSON object in a fenced ```json block.\n"
        "- Use this exact shape:\n"
        "  {\n"
        "    \"status\": \"COMPLETED | BLOCKED_DEPENDENCY | FAILED | PARTIAL\",\n"
        "    \"summary\": \"short human-readable summary\",\n"
        "    \"produced_files\": [\n"
        "      {\n"
        "        \"path\": \"absolute-or-workspace-relative path\",\n"
        "        \"artifact_alias\": \"alias-or-null\",\n"
        "        \"description\": \"what this file contains\",\n"
        "        \"deliverable\": true | false,\n"
        "        \"module\": \"image_tabular | paper | code | docs | refs | null\"\n"
        "      }\n"
        "    ],\n"
        "    \"missing_inputs\": [\n"
        "      {\"artifact_alias\": \"alias-or-null\", \"reason\": \"why unavailable\"}\n"
        "    ],\n"
        "    \"acceptance_check\": {\"passed\": true, \"notes\": \"brief verification notes\"}\n"
        "  }\n"
        "- For BLOCKED_DEPENDENCY, also include the exact two-line marker before the JSON block:\n"
        "  STATUS: BLOCKED_DEPENDENCY\n"
        "  DETAIL: <which upstream task/data is missing>\n"
        "- For completed tasks, produced_files must list actual files you created or verified.\n"
        "- Mark files as deliverable=true ONLY for final outputs (visualizations, reports, papers).\n"
        "  Do NOT mark intermediate data, logs, or raw outputs as deliverables.\n"
        "- Module types: image_tabular (charts/figures), paper (manuscripts), code (scripts), docs (reports), refs (references).\n"
    )


def _rerun_update_mode_prompt() -> str:
    return (
        "Rerun/update mode:\n"
        "- If previous outputs already exist, inspect them first when useful.\n"
        "- Reuse valid existing work where it satisfies the current contract.\n"
        "- Regenerate or overwrite only files needed to satisfy the current task contract.\n"
        "- Do not treat pre-existing files alone as success unless you verified they satisfy the acceptance criteria.\n"
    )


def _format_contract_diff_for_cli(contract_diff: Optional[Dict[str, Any]]) -> str:
    if not isinstance(contract_diff, dict):
        return ""

    def _join(key: str, limit: int = 6) -> str:
        values = contract_diff.get(key)
        if not isinstance(values, list) or not values:
            return ""
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if not cleaned:
            return ""
        if len(cleaned) > limit:
            cleaned = cleaned[:limit] + ["..."]
        return ", ".join(cleaned)

    lines: List[str] = []
    for label, key in (
        ("Expected deliverables", "expected_deliverables"),
        ("Missing required outputs", "missing_required_outputs"),
        ("Invalid artifacts", "invalid_artifacts"),
        ("Wrong-format outputs", "wrong_format_outputs"),
        ("Unexpected extra outputs", "unexpected_outputs"),
        ("Actual outputs observed", "actual_outputs"),
    ):
        joined = _join(key)
        if joined:
            lines.append(f"- {label}: {joined}")
    return "\n".join(lines)


def _is_verification_only_task(task_text: str) -> bool:
    """Detect tool calls that should inspect existing artifacts, not rerun the task."""
    text = " ".join(str(task_text or "").lower().split())
    if not text:
        return False
    verification_terms = (
        "verify", "validate", "check", "inspect", "read the first", "count total",
        "count rows", "header", "columns exist", "列是否存在", "验证", "检查",
    )
    artifact_terms = (
        ".tsv", ".csv", ".json", ".parquet", ".txt", ".fasta", ".fa", ".h5ad",
        "file", "artifact", "output", "文件", "产物", "输出",
    )
    generation_terms = (
        "generate", "create", "produce", "write", "save", "map ", "compute",
        "train", "run analysis", "生成", "创建", "产出",
    )
    has_verification = any(term in text for term in verification_terms)
    has_artifact = any(term in text for term in artifact_terms)
    has_generation = any(term in text for term in generation_terms)
    return has_verification and has_artifact and not has_generation


def _build_cli_contract_repair_task(
    task: str,
    execution_spec: Optional[Dict[str, Any]],
    *,
    contract_diff: Optional[Dict[str, Any]],
    guidance: str,
) -> str:
    lines: List[str] = [
        "[STRICT CONTRACT REPAIR]",
        "The previous execution ran, but the required deliverables did not match the authoritative task contract.",
        "Do NOT change task scope, task meaning, methods, thresholds, or upstream/downstream responsibilities.",
        "Preserve useful extra outputs if you want, but they do NOT replace missing required outputs.",
        "Regenerate or supplement outputs so that the required deliverables exist exactly at the expected paths/patterns.",
    ]
    contract_text = _format_contract_diff_for_cli(contract_diff)
    if contract_text:
        lines.extend(["", "Contract mismatch:", contract_text])
    if guidance:
        lines.extend(["", "Verification guidance:", guidance.strip()])
    if task:
        lines.extend(["", "Original execution request:", str(task).strip()])
    if execution_spec:
        lines.extend([
            "",
            "Use the bound task context below as the single source of truth. Do not patch the plan.",
        ])
    return "\n".join(lines).strip()


def _validate_scope_contract(
    *,
    plan_id: Optional[int],
    task_id: Optional[int],
    require_task_context: bool,
) -> Optional[str]:
    if not require_task_context:
        return None
    if plan_id is None:
        return "Missing plan_id for strict atomic execution."
    if task_id is None:
        return "Missing task_id for strict atomic execution."
    return None


def _is_path_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _is_path_within_lexical(child: Path, parent: Path) -> bool:
    """Return True when *child* is lexically under *parent* without resolving symlinks."""
    try:
        child.absolute().relative_to(parent.absolute())
        return True
    except Exception:
        return False


def _is_allowed_task_read_path(path: Path, session_dir: Path) -> bool:
    """Allow paths in the project/session, including project-local symlink aliases."""
    project_root = _facade_path("_PROJECT_ROOT")
    external_roots = _facade_path("_DEFAULT_EXTERNAL_READ_ROOTS")
    return (
        _is_path_within(path, project_root)
        or _is_path_within(path, session_dir)
        or any(_is_path_within(path, root) for root in external_roots)
        or _is_path_within_lexical(path, project_root)
        or _is_path_within_lexical(path, session_dir)
        or any(_is_path_within_lexical(path, root) for root in external_roots)
    )


def _extract_task_referenced_read_dirs(
    task: str,
    *,
    execution_spec: Optional[Dict[str, Any]],
    session_dir: Path,
) -> List[str]:
    texts: List[str] = [str(task or "")]
    if isinstance(execution_spec, dict) and execution_spec:
        try:
            texts.append(json.dumps(execution_spec, ensure_ascii=False))
        except Exception:
            logger.debug("Failed to serialize execution_spec for task path inference.")

    if not any(text.strip() for text in texts):
        return []

    project_root = _facade_path("_PROJECT_ROOT")
    task_path_token_re = _facade_path("_TASK_PATH_TOKEN_RE")
    task_read_dir_prefixes = _facade_path("_TASK_READ_DIR_PREFIXES")
    escaped_root = re.escape(str(project_root))
    absolute_pattern = re.compile(rf"{escaped_root}(?:/{task_path_token_re})+")
    relative_roots = "|".join(re.escape(prefix) for prefix in task_read_dir_prefixes)
    relative_pattern = re.compile(
        rf"(?<![\w.-])(?:{relative_roots})(?:/{task_path_token_re})+"
    )

    inferred_dirs: List[str] = []
    seen: set[str] = set()

    def _register(raw_path: str) -> None:
        token = str(raw_path or "").strip()
        if not token or len(token) > 1024:
            return
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        try:
            lexical = candidate.absolute()
            target_dir = lexical if lexical.is_dir() else lexical.parent
            if not target_dir.exists() or not target_dir.is_dir():
                return
        except OSError:
            return
        if not _is_allowed_task_read_path(target_dir, session_dir):
            return
        dir_str = str(target_dir)
        if dir_str in seen:
            return
        seen.add(dir_str)
        inferred_dirs.append(dir_str)

    for text in texts:
        for match in absolute_pattern.finditer(text):
            _register(match.group(0))
        for match in relative_pattern.finditer(text):
            _register(match.group(0))

    return inferred_dirs


def _sanitize_task_dir_component(value: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return "llm_task"

    normalized_chars: List[str] = []
    prev_is_sep = False
    for ch in token:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            normalized_chars.append(ch)
            prev_is_sep = False
            continue
        if ch in {"_", "-", " ", "/", "\\", ".", ":"}:
            if not prev_is_sep:
                normalized_chars.append("_")
                prev_is_sep = True
            continue
        if not prev_is_sep:
            normalized_chars.append("_")
            prev_is_sep = True

    sanitized = "".join(normalized_chars).strip("_")
    if not sanitized:
        return "llm_task"
    if len(sanitized) > 80:
        sanitized = sanitized[:80].rstrip("_")
    return sanitized or "llm_task"


async def _generate_task_dir_name_llm(task: str) -> str:
    try:
        from app.llm import get_default_client

        client = get_default_client()
        prompt = f"""Analyze the following task and generate a concise directory name.

Task: {task}

Requirements:
1. Extract the core semantic meaning of the task
2. Generate 2-4 English words that capture the essence
3. Use lowercase with underscores (e.g., train_model, analyze_data)
4. Be specific and descriptive
5. Return ONLY the directory name, nothing else

Examples:
- Task: " data/code_task ， baseline ，" → analyze_train_baseline
- Task: "Generate a report on user behavior" → user_behavior_report
- Task: "Debug the authentication system" → debug_authentication

Directory name:"""
        llm_response = await asyncio.to_thread(client.chat, prompt)
        dir_name = llm_response.strip().lower()
        dir_name = dir_name.split("\n")[0].strip()
        for prefix in ["directory name:", "name:", "output:", "→", "-", ">", "*"]:
            if dir_name.startswith(prefix):
                dir_name = dir_name[len(prefix):].strip()
        dir_name = _sanitize_task_dir_component(dir_name)
        if not dir_name or len(dir_name) < 3:
            logger.warning(f"LLM generated invalid directory name: '{llm_response}', using semantic fallback")
            dir_name = "llm_task"
        task_hash = hashlib.md5(task.encode("utf-8")).hexdigest()[:6]
        return f"{dir_name}_{task_hash}"
    except Exception as e:
        logger.error(f"LLM-based directory name generation failed: {e}")
        task_hash = hashlib.md5(task.encode("utf-8")).hexdigest()[:6]
        return f"task_{task_hash}"


_COMPLETED_TASK_STATUSES = {"completed", "done", "success"}


def _extract_acceptance_criteria_from_node(node: Any) -> Optional[Dict[str, Any]]:
    metadata = getattr(node, "metadata", None)
    if isinstance(metadata, dict):
        criteria = metadata.get("acceptance_criteria")
        if isinstance(criteria, dict):
            return json.loads(json.dumps(criteria, ensure_ascii=False))

    raw_execution_result = getattr(node, "execution_result", None)
    if isinstance(raw_execution_result, str):
        try:
            raw_execution_result = json.loads(raw_execution_result)
        except (TypeError, json.JSONDecodeError):
            raw_execution_result = None
    if isinstance(raw_execution_result, dict):
        payload_meta = raw_execution_result.get("metadata")
        if isinstance(payload_meta, dict):
            criteria = payload_meta.get("acceptance_criteria")
            if isinstance(criteria, dict):
                return json.loads(json.dumps(criteria, ensure_ascii=False))
    derived = derive_acceptance_criteria_from_text(getattr(node, "instruction", None))
    if isinstance(derived, dict) and derived.get("checks"):
        return derived
    return None


def _build_ad_hoc_execution_spec(task_text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(task_text, str) or not task_text.strip():
        return None

    acceptance_criteria = derive_acceptance_criteria_from_text(task_text)
    checks = acceptance_criteria.get("checks") if isinstance(acceptance_criteria, dict) else None
    if not isinstance(checks, list) or not checks:
        return None

    task_name = "Ad-hoc execution task"
    for raw_line in task_text.splitlines():
        line = " ".join(str(raw_line or "").split()).strip()
        if not line:
            continue
        task_name = line[:93] + "..." if len(line) > 96 else line
        break

    return {
        "plan_id": None,
        "task_id": None,
        "task_name": task_name,
        "task_instruction": task_text.strip(),
        "acceptance_criteria": acceptance_criteria,
        "dependency_outputs": [],
        "dependency_artifact_paths": [],
        "dependency_blockers": [],
    }


def _build_execution_spec(
    plan_id: Optional[int],
    task_id: Optional[int],
    *,
    task_text: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if plan_id is None and task_id is None:
        return _build_ad_hoc_execution_spec(task_text)
    if plan_id is None or task_id is None:
        return None

    try:
        from app.routers.chat.code_executor_helpers import extract_task_artifact_paths
        from app.routers.chat.services import plan_repository
        from app.services.plans.artifact_contracts import (
            load_artifact_manifest,
            resolve_artifact_contract_with_provenance,
            resolve_manifest_aliases,
        )
    except Exception as exc:
        logger.warning("Failed to load plan-aware execution context: %s", exc)
        return None

    try:
        tree = plan_repository.get_plan_tree(int(plan_id))
    except Exception as exc:
        logger.warning("Failed to load plan tree %s for code executor: %s", plan_id, exc)
        return None

    if not tree.has_node(int(task_id)):
        return None

    node = tree.get_node(int(task_id))
    node_metadata = node.metadata if isinstance(getattr(node, "metadata", None), dict) else {}
    artifact_contract = resolve_artifact_contract_with_provenance(
        task_name=str(node.display_name()).strip(),
        instruction=str(getattr(node, "instruction", "") or ""),
        metadata=node_metadata,
    ).as_contract_dict()
    manifest = load_artifact_manifest(int(plan_id), session_id)
    resolved_input_artifacts = resolve_manifest_aliases(
        manifest,
        list(artifact_contract.get("requires") or []),
    )
    dependency_outputs: List[Dict[str, Any]] = []
    dependency_artifact_paths: List[str] = []
    dependency_blockers: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    for path in resolved_input_artifacts.values():
        text = str(path or "").strip()
        if text and text not in seen_paths:
            seen_paths.add(text)
            dependency_artifact_paths.append(text)

    for dep_id in list(getattr(node, "dependencies", []) or []):
        try:
            dep_id_int = int(dep_id)
        except (TypeError, ValueError):
            continue
        if not tree.has_node(dep_id_int):
            continue
        dep_node = tree.get_node(dep_id_int)
        dep_status = str(getattr(dep_node, "status", "") or "").strip().lower()
        dep_artifacts = extract_task_artifact_paths(dep_node)
        for path in dep_artifacts:
            text = str(path or "").strip()
            if not text or text in seen_paths:
                continue
            seen_paths.add(text)
            dependency_artifact_paths.append(text)
        dep_entry = {
            "task_id": dep_id_int,
            "task_name": str(dep_node.display_name()).strip(),
            "status": dep_status,
            "artifact_paths": dep_artifacts,
            "execution_result": str(getattr(dep_node, "execution_result", "") or "").strip(),
        }
        dependency_outputs.append(dep_entry)
        if dep_status not in _COMPLETED_TASK_STATUSES:
            dependency_blockers.append(dep_entry)

    return {
        "plan_id": int(plan_id),
        "task_id": int(task_id),
        "task_name": str(node.display_name()).strip(),
        "task_instruction": str(getattr(node, "instruction", "") or "").strip(),
        "acceptance_criteria": _extract_acceptance_criteria_from_node(node),
        "artifact_contract": artifact_contract,
        "resolved_input_artifacts": resolved_input_artifacts,
        "dependency_outputs": dependency_outputs,
        "dependency_artifact_paths": dependency_artifact_paths,
        "dependency_blockers": dependency_blockers,
    }


def _summarize_dependency_blockers(execution_spec: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(execution_spec, dict):
        return None
    blockers = execution_spec.get("dependency_blockers")
    if not isinstance(blockers, list) or not blockers:
        return None

    details: List[str] = []
    for blocker in blockers[:4]:
        if not isinstance(blocker, dict):
            continue
        name = str(blocker.get("task_name") or blocker.get("task_id") or "unknown").strip()
        status = str(blocker.get("status") or "unknown").strip()
        details.append(f"{name} [{status}]")
    if not details:
        return "Blocked by incomplete upstream dependencies."
    return "Blocked by incomplete upstream dependencies: " + ", ".join(details)


def _extract_code_workspace_metadata(
    *,
    run_dir: Path,
    produced_files: Sequence[str],
) -> tuple[Optional[str], Optional[str]]:
    code_dir = (run_dir / "code").resolve()
    code_dir_value = str(code_dir) if code_dir.exists() and code_dir.is_dir() else None
    primary_code_file: Optional[str] = None
    for item in produced_files:
        try:
            candidate = Path(str(item)).resolve()
        except Exception:
            continue
        if not candidate.is_file():
            continue
        if code_dir_value is not None:
            try:
                candidate.relative_to(code_dir)
            except ValueError:
                continue
        if candidate.suffix.lower() in {".py", ".r", ".sh", ".js", ".ts", ".tsx"}:
            primary_code_file = str(candidate)
            break
    return code_dir_value, primary_code_file


def _contract_required_artifact_records(
    *,
    execution_spec: Optional[Dict[str, Any]],
    task_work_dir: Path,
    produced_files: Sequence[str],
    max_items: int = 100,
) -> List[Dict[str, Any]]:
    """Return authoritative records for contract-required output files."""
    criteria = execution_spec.get("acceptance_criteria") if isinstance(execution_spec, dict) else None
    expected_deliverables = derive_expected_deliverables(criteria)
    if not expected_deliverables:
        return []

    produced_paths: List[Path] = []
    for raw_path in produced_files:
        text = str(raw_path or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        try:
            if path.exists() and path.is_file():
                produced_paths.append(path.resolve())
        except OSError:
            continue

    records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _normalize(value: Any) -> str:
        return str(value or "").strip().replace(chr(92), "/").strip("/")

    for expected in expected_deliverables:
        expected_text = _normalize(expected)
        if not expected_text or any(token in expected_text for token in ("*", "?", "[")):
            continue
        expected_path = Path(str(expected or "").strip()).expanduser()
        direct = expected_path if expected_path.is_absolute() else (task_work_dir / expected_path)
        candidates: List[Path] = [direct]
        if not expected_path.is_absolute():
            prefixed_source = _ce()._find_unique_run_prefixed_contract_source(task_work_dir, expected_path)
            if prefixed_source is not None:
                candidates.append(prefixed_source)
        for produced in produced_paths:
            produced_norm = _normalize(str(produced))
            if produced_norm == expected_text or produced_norm.endswith(f"/{expected_text}"):
                candidates.append(produced)
        selected: Optional[Path] = None
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    selected = candidate.resolve()
                    break
            except OSError:
                continue
        if selected is None:
            continue
        key = f"{expected_text}|{selected}"
        if key in seen:
            continue
        seen.add(key)
        try:
            size = selected.stat().st_size
        except OSError:
            size = None
        try:
            relative_to_task = str(selected.relative_to(task_work_dir.resolve())).replace(chr(92), "/")
        except Exception:
            relative_to_task = None
        records.append({
            "expected": expected_text,
            "path": str(selected),
            "size": size,
            "exists": True,
            "relative_to_task": relative_to_task,
            "verification_source": "contract_required_output",
        })
        if len(records) >= max_items:
            break
    return records


def _append_contract_artifact_paths(
    verification_artifact_paths: List[str],
    contract_artifacts: Sequence[Dict[str, Any]],
) -> None:
    """Expose contract-required file paths to deterministic verification."""
    seen = set(verification_artifact_paths)
    for record in contract_artifacts:
        if not isinstance(record, dict):
            continue
        path = str(record.get("path") or "").strip()
        if not path or path in seen:
            continue
        verification_artifact_paths.append(path)
        seen.add(path)


def _build_verification_artifact_paths(
    *,
    task_work_dir: Path,
    subdirs: Sequence[str],
    produced_files: Sequence[str],
    session_artifact_paths: Sequence[str],
    session_dir: Path,
    max_items: int = 200,
) -> List[str]:
    """Return artifact hints that deterministic verification can trust."""
    ordered: List[str] = []
    seen: set[str] = set()

    def _append(value: Optional[str]) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if not text or text in seen:
            return
        seen.add(text)
        ordered.append(text)

    _append(str(task_work_dir.resolve()))
    for name in subdirs:
        root = (task_work_dir / str(name)).resolve()
        _append(str(root))
    for path in produced_files:
        _append(str(path))
    for rel in session_artifact_paths:
        try:
            abs_path = (session_dir / str(rel)).resolve()
        except Exception:
            continue
        _append(str(abs_path))
    return ordered[:max_items]

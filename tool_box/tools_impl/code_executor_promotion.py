"""Result promotion and deliverable reconciliation for :mod:`code_executor`."""

from __future__ import annotations

import fnmatch as _fnmatch
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.services.plans.acceptance_criteria import derive_expected_deliverables
from app.services.session_paths import get_runtime_session_dir

logger = logging.getLogger(__name__)


def _ce():
    from tool_box.tools_impl import code_executor

    return code_executor


def _is_path_within(child: Path, parent: Path) -> bool:
    return _ce()._is_path_within(child, parent)


def _default_task_subdirectories():
    return _ce()._DEFAULT_TASK_SUBDIRECTORIES


_MAX_SESSION_PROMOTE_FILE_BYTES = 250 * 1024 * 1024
_MAX_SESSION_PROMOTE_FILES = 500
_MAX_STALE_SESSION_ROOT_FILE_BYTES = 1
_UNIFIED_PROMOTE_EXCLUDE_PATTERNS = {"*_code_executor.log", "*_debug.*", "*_claude_debug.*", "*.pyc"}
_RUN_PREFIX_RE = re.compile(r"^run_\d{8}_\d{6}_\d+_[0-9a-f]+_")


def _resolve_runtime_session_dir(session_id: Optional[str]) -> Path:
    token = str(session_id or "").strip()
    if not token:
        adhoc_dir = (_ce()._RUNTIME_DIR / "session_adhoc").resolve()
        adhoc_dir.mkdir(parents=True, exist_ok=True)
        return adhoc_dir
    return get_runtime_session_dir(token, create=True)


def _prune_stale_session_root_results(
    *,
    session_dir: Path,
    max_bytes: int = _MAX_STALE_SESSION_ROOT_FILE_BYTES,
) -> List[str]:
    session_resolved = session_dir.resolve()
    results_root = (session_resolved / "results").resolve()
    if not results_root.is_dir() or not _is_path_within(results_root, session_resolved):
        return []
    removed: List[str] = []
    for path in sorted(results_root.iterdir()):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            continue
        try:
            relative = str(path.relative_to(session_resolved)).replace("\\", "/")
        except ValueError:
            continue
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Failed to remove stale session-root artifact %s: %s", path, exc)
            continue
        removed.append(relative)
    if removed:
        logger.info("Pruned %s stale flat session-root artifact(s): %s", len(removed), removed)
    return removed


def _has_hidden_path_component(path: Path, *, relative_to: Path) -> bool:
    try:
        parts = path.relative_to(relative_to).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)


def _collect_non_semantic_run_files(*, run_dir: Path, semantic_roots: Sequence[Path]) -> List[Path]:
    if not run_dir.exists() or not run_dir.is_dir():
        return []
    collected: List[Path] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(_is_path_within(path, root) for root in semantic_roots):
            continue
        if _has_hidden_path_component(path, relative_to=run_dir):
            continue
        collected.append(path)
    return collected


def _should_skip_unified_promoted_file(path: Path, *, root: Path) -> bool:
    if _has_hidden_path_component(path, relative_to=root):
        return True
    return any(_fnmatch.fnmatch(path.name, pattern) for pattern in _UNIFIED_PROMOTE_EXCLUDE_PATTERNS)


def _iter_promotable_run_files(*, scratch_dir: Path, subdirs: Sequence[str]) -> List[tuple[Path, Path]]:
    results: List[tuple[Path, Path]] = []
    seen_sources: set[str] = set()
    results_dir = (scratch_dir / "results").resolve()
    source_dirs = [results_dir] if results_dir.is_dir() else []
    for subdir_name in subdirs:
        if subdir_name == "results":
            continue
        candidate = (scratch_dir / subdir_name).resolve()
        if candidate.is_dir():
            source_dirs.append(candidate)
    for source_dir in source_dirs:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or _should_skip_unified_promoted_file(path, root=scratch_dir):
                continue
            source_key = str(path.resolve())
            if source_key in seen_sources:
                continue
            try:
                rel = path.relative_to(source_dir)
            except ValueError:
                continue
            try:
                subdir_rel = source_dir.relative_to(scratch_dir)
                dest_subdir = str(subdir_rel)
            except ValueError:
                dest_subdir = ""
            dest_rel = rel if dest_subdir == "results" else Path(dest_subdir) / rel if dest_subdir else rel
            seen_sources.add(source_key)
            results.append((path, dest_rel))
    for path in _collect_non_semantic_run_files(run_dir=scratch_dir, semantic_roots=source_dirs):
        if _should_skip_unified_promoted_file(path, root=scratch_dir):
            continue
        source_key = str(path.resolve())
        if source_key in seen_sources:
            continue
        try:
            rel = path.relative_to(scratch_dir)
        except ValueError:
            continue
        seen_sources.add(source_key)
        results.append((path, rel))
    return results


def _collapse_rooted_rel_path(*, rel: Path, output_dir: Path, session_dir: Path) -> Path:
    try:
        prefix = output_dir.resolve().relative_to(session_dir.resolve())
    except (ValueError, OSError):
        return rel
    try:
        return rel.relative_to(prefix)
    except ValueError:
        pass
    prefix_parts = prefix.parts
    parts = list(rel.parts)
    changed = False
    for _ in range(len(prefix_parts)):
        head = 0
        limit = min(len(prefix_parts), len(parts))
        while head < limit and parts[head] == prefix_parts[head]:
            head += 1
        if head:
            parts = parts[head:]
            changed = True
            if not parts:
                return rel
            continue
        dropped = False
        max_block = min(len(prefix_parts), len(parts))
        for size in range(max_block, 0, -1):
            block = prefix_parts[:size]
            idx: Optional[int] = None
            for start in range(0, len(parts) - size + 1):
                if tuple(parts[start : start + size]) == block:
                    idx = start
                    break
            if idx is None:
                continue
            parts = parts[idx + size :]
            changed = True
            dropped = True
            if not parts:
                return rel
            break
        if not dropped:
            break
    if not changed or not parts:
        return rel
    return Path(*parts)


def _promote_results_to_unified_dir(
    *,
    scratch_dir: Path,
    output_dir: Path,
    subdirs: Sequence[str],
    session_dir: Path,
    max_files: int = 500,
) -> List[str]:
    promoted: List[str] = []
    count = 0
    for path, rel in _iter_promotable_run_files(scratch_dir=scratch_dir, subdirs=subdirs):
        if count >= max_files:
            logger.warning("Unified promotion stopped after %s files (cap=%s)", count, max_files)
            break
        dest = output_dir / _collapse_rooted_rel_path(rel=rel, output_dir=output_dir, session_dir=session_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, dest)
        except OSError as exc:
            logger.warning("Failed to promote %s -> %s: %s", path, dest, exc)
            continue
        try:
            rel_to_session = str(dest.relative_to(session_dir)).replace("\\", "/")
        except ValueError:
            rel_to_session = str(dest).replace("\\", "/")
        promoted.append(rel_to_session)
        count += 1

    if not promoted:
        session_results = (session_dir / "results").resolve()
        if session_results.is_dir() and session_results != (scratch_dir / "results").resolve():
            for path in sorted(session_results.rglob("*")):
                if not path.is_file() or _should_skip_unified_promoted_file(path, root=session_results):
                    continue
                if count >= max_files:
                    logger.warning("Unified promotion (session fallback) stopped after %s files (cap=%s)", count, max_files)
                    break
                try:
                    rel = path.relative_to(session_results)
                except ValueError:
                    continue
                dest = output_dir / _collapse_rooted_rel_path(rel=rel, output_dir=output_dir, session_dir=session_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(path, dest)
                except OSError as exc:
                    logger.warning("Failed to promote (session fallback) %s -> %s: %s", path, dest, exc)
                    continue
                try:
                    rel_to_session = str(dest.relative_to(session_dir)).replace("\\", "/")
                except ValueError:
                    rel_to_session = str(dest).replace("\\", "/")
                promoted.append(rel_to_session)
                count += 1
            if promoted:
                logger.info("Promoted %s file(s) from session results/ fallback to unified output dir %s", len(promoted), output_dir)
    if promoted:
        logger.info("Promoted %s file(s) to unified output dir %s", len(promoted), output_dir)
    return promoted


def _promote_task_results_to_session_root(
    *,
    session_dir: Path,
    task_work_dir: Path,
    subdirs: Optional[Sequence[str]] = None,
    max_files: int = _MAX_SESSION_PROMOTE_FILES,
) -> tuple[List[str], List[str]]:
    session_resolved = session_dir.resolve()
    task_resolved = task_work_dir.resolve()
    effective_subdirs = tuple(subdirs) if subdirs else _default_task_subdirectories()
    promotable_files = _iter_promotable_run_files(scratch_dir=task_resolved, subdirs=effective_subdirs)
    if not promotable_files:
        return [], []
    try:
        task_scope_rel = task_resolved.relative_to(session_resolved)
    except ValueError:
        task_scope_rel = Path(task_resolved.parent.name) / task_resolved.name
    dst_root = (session_resolved / "results" / task_scope_rel).resolve()
    if not _is_path_within(dst_root, session_resolved):
        return [], []
    dst_root.mkdir(parents=True, exist_ok=True)
    promoted: List[str] = []
    skipped_large: List[str] = []
    count = 0
    for path, rel in promotable_files:
        if count >= max_files:
            logger.warning("Session results promotion stopped after %s files (cap=%s)", count, max_files)
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_SESSION_PROMOTE_FILE_BYTES:
            logger.info("Skipping large file for session results promotion: %s (%s bytes)", path, size)
            skipped_large.append(str(path.resolve()))
            continue
        dest = (dst_root / rel).resolve()
        if not _is_path_within(dest, dst_root):
            logger.warning("Skipping promotion path outside results/: %s", rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, dest)
        except OSError as exc:
            logger.warning("Failed to promote %s -> %s: %s", path, dest, exc)
            continue
        promoted.append(str(dest.relative_to(session_resolved)).replace("\\", "/"))
        count += 1
    if promoted:
        logger.info("Promoted %s file(s) from %s/ to session results/%s for artifact URLs", len(promoted), task_resolved.name, str(task_scope_rel).replace("\\", "/"))
    return promoted, skipped_large


def _promote_external_contract_artifacts(
    *,
    contract_artifacts: List[Dict[str, Any]],
    task_work_dir: Path,
    unified_output_dir: Optional[Path],
) -> List[str]:
    if not unified_output_dir or not contract_artifacts:
        return []
    promoted: List[str] = []
    workspace_resolved = task_work_dir.resolve()
    for artifact in contract_artifacts:
        if not isinstance(artifact, dict) or not artifact.get("exists"):
            continue
        path_str = str(artifact.get("path") or "").strip()
        if not path_str:
            continue
        try:
            artifact_path = Path(path_str).resolve()
        except OSError:
            continue
        if not artifact_path.exists() or not artifact_path.is_file():
            continue
        try:
            artifact_path.relative_to(workspace_resolved)
            continue
        except ValueError:
            pass
        dest = unified_output_dir / artifact_path.name
        if dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(artifact_path), str(dest))
            promoted.append(str(dest))
            logger.info("Promoted external contract artifact %s -> %s", path_str, dest)
        except OSError as exc:
            logger.warning("Failed to promote external artifact %s: %s", path_str, exc)
    return promoted


def _promote_project_level_strays(
    *,
    contract_artifacts: List[Dict[str, Any]],
    unified_output_dir: Optional[Path],
    project_root: Path,
) -> List[str]:
    if not unified_output_dir or not contract_artifacts:
        return []
    promoted: List[str] = []
    workspace_resolved = unified_output_dir.resolve()
    project_resolved = project_root.resolve()
    for artifact in contract_artifacts:
        if not isinstance(artifact, dict) or not artifact.get("exists"):
            continue
        path_str = str(artifact.get("path") or "").strip()
        if not path_str:
            continue
        try:
            artifact_path = Path(path_str).resolve()
        except OSError:
            continue
        if not artifact_path.exists() or not artifact_path.is_file():
            continue
        try:
            rel = artifact_path.relative_to(project_resolved)
        except ValueError:
            continue
        top_level = rel.parts[0] if rel.parts else ""
        if top_level not in ("results", "output"):
            continue
        try:
            artifact_path.relative_to(workspace_resolved)
            continue
        except ValueError:
            pass
        sub_rel = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path(rel.name)
        dest = unified_output_dir / sub_rel
        if dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(artifact_path), str(dest))
            promoted.append(str(dest))
            logger.info("Promoted project-level stray %s -> %s", path_str, dest)
        except OSError as exc:
            logger.warning("Failed to promote project-level stray %s: %s", path_str, exc)
    return promoted


def _reconcile_deliverables(
    *,
    execution_spec: Optional[Dict[str, Any]],
    task_work_dir: Path,
    unified_output_dir: Optional[Path] = None,
    session_results_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {"aligned": [], "missing": [], "already_ok": []}
    if not isinstance(execution_spec, dict):
        return report
    criteria = execution_spec.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        return report
    expected_names = derive_expected_deliverables(criteria, include_globs=False, relative_only=True)
    if not expected_names:
        return report
    results_dir = task_work_dir / "results"
    if not results_dir.is_dir():
        return report
    actual_files: Dict[str, Path] = {}
    for path in sorted(results_dir.rglob("*")):
        if path.is_file():
            actual_files[path.name] = path
    search_dirs: List[Path] = [results_dir]
    if unified_output_dir and unified_output_dir.is_dir():
        search_dirs.append(unified_output_dir)
    if session_results_dir and session_results_dir.is_dir():
        search_dirs.append(session_results_dir)
    for raw_expected in expected_names:
        expected_name = Path(raw_expected).name
        expected_suffix = Path(expected_name).suffix
        found = any((search_dir / expected_name).exists() for search_dir in search_dirs)
        if found:
            report["already_ok"].append(expected_name)
            continue
        matched_source: Optional[Path] = None
        for actual_name, actual_path in actual_files.items():
            if actual_name == expected_name or _RUN_PREFIX_RE.sub("", actual_name) == expected_name or (actual_name.endswith(expected_suffix) and actual_name.endswith("_" + expected_name)):
                matched_source = actual_path
                break
        if matched_source is None:
            report["missing"].append(expected_name)
            continue
        for target_dir in search_dirs:
            link_path = target_dir / expected_name
            if link_path.exists() or link_path.is_symlink():
                continue
            try:
                link_path.parent.mkdir(parents=True, exist_ok=True)
                link_path.symlink_to(matched_source.resolve())
                logger.info("[RECONCILE] %s -> %s (in %s)", expected_name, matched_source.name, target_dir.name)
            except OSError as exc:
                logger.warning("[RECONCILE] Failed to create symlink %s: %s", link_path, exc)
        report["aligned"].append({"expected": expected_name, "actual": matched_source.name})
    if report["aligned"] or report["missing"]:
        logger.info("[RECONCILE] task_work_dir=%s aligned=%d already_ok=%d missing=%d", task_work_dir.name, len(report["aligned"]), len(report["already_ok"]), len(report["missing"]))
    return report


def _build_search_and_generate_prompt(
    missing_files: List[str],
    session_dir: Path,
    execution_spec: Optional[Dict[str, Any]],
    *,
    is_timeout: bool = False,
) -> str:
    task_name = ""
    task_instruction = ""
    if isinstance(execution_spec, dict):
        task_name = str(execution_spec.get("task_name") or "")
        task_instruction = str(execution_spec.get("task_instruction") or "")
    file_list = "\n".join(f"  - {f}" for f in missing_files)
    if is_timeout:
        return (
            "DELIVERABLE RECOVERY TASK (PREVIOUS EXECUTION TIMED OUT OR CLI TOOL CALL FAILED)\n\n"
            "The previous execution was killed due to a timeout/no output or a fatal CLI tool-call failure. "
            "The analysis code may have been partially written or not written at all.\n\n"
            f"Missing files:\n{file_list}\n\n"
            f"Task context:\n  Name: {task_name}\n  Instruction: {task_instruction[:500]}\n\n"
            f"Step 1 — CHECK: Look in {session_dir} for any existing code or partial results from the previous run. Check code/ and results/ directories.\n\n"
            "Step 2 — SEARCH: Use file_operations to search for each missing file in other task outputs (plan*/task*/run_*/results/). Files may exist with a run_*_ prefix.\n\n"
            "Step 3 — COPY: If you find a file, copy it to results/ with the exact expected name (no prefix).\n\n"
            "Step 4 — RE-EXECUTE: If files are truly not found anywhere, you MUST re-run the analysis. Write the Python script based on the task instruction above, save it to code/, and execute it with code_executor. Output must go to results/.\n"
            "When writing scripts, avoid one huge write_file call. Create a small skeleton first, then append or edit in focused chunks so tool-call output is never truncated.\n\n"
            "IMPORTANT: This is a recovery task after a timeout. You need to complete the work that was interrupted. Do NOT just report files as missing."
        )
    return (
        "DELIVERABLE SEARCH TASK\n\n"
        "The main analysis already ran. Do NOT re-run it. Your ONLY job is to find the missing deliverable files listed below and copy them to results/.\n\n"
        f"Missing files:\n{file_list}\n\n"
        f"Task context:\n  Name: {task_name}\n  Instruction: {task_instruction[:500]}\n\n"
        f"Step 1 — SEARCH: Use file_operations to search {session_dir} for each missing file. Check all subdirectories including other task outputs (plan*/task*/run_*/results/). Files may exist with a run_*_ prefix or in a different task's results/ directory.\n\n"
        "Step 2 — COPY: If you find a file, copy it to results/ with the exact expected name (no prefix). Use file_operations copy.\n\n"
        "If a file is truly not found anywhere, just report it as missing. Do NOT attempt to generate or re-run any analysis.\n\n"
        "Finish quickly. This is a file search and copy operation, not an analysis task."
    )


def _collect_run_artifacts(*, run_dir: Path, subdirs: Sequence[str], max_files: int = 2000) -> List[str]:
    collected: List[str] = []
    seen = set()
    semantic_roots: List[Path] = []
    for name in subdirs:
        root = (run_dir / str(name)).resolve()
        if not root.exists() or not root.is_dir():
            continue
        semantic_roots.append(root)
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            collected.append(resolved)
            if len(collected) >= max_files:
                return collected
    for path in _collect_non_semantic_run_files(run_dir=run_dir, semantic_roots=semantic_roots):
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        collected.append(resolved)
        if len(collected) >= max_files:
            return collected
    return collected


def _recover_files_from_historical_runs(
    *,
    task_root_dir: Path,
    current_run_dir: Path,
    execution_spec: Optional[Dict[str, Any]],
    task_subdirs: Sequence[str],
) -> List[str]:
    if not execution_spec or not task_root_dir.exists():
        return []
    criteria = execution_spec.get("acceptance_criteria")
    expected_deliverables = derive_expected_deliverables(criteria)
    if not expected_deliverables:
        return []
    historical_runs = [run_dir for run_dir in sorted(task_root_dir.glob("run_*")) if run_dir.is_dir() and run_dir.resolve() != current_run_dir.resolve()]
    if not historical_runs:
        return []
    recovered_files = []
    for expected in expected_deliverables:
        expected_text = str(expected or "").strip().replace("\\", "/")
        if not expected_text:
            continue
        expected_path = Path(expected_text)
        has_glob = any(token in expected_text for token in ("*", "?", "["))
        for hist_run in reversed(historical_runs):
            found = False
            if not has_glob:
                candidate = hist_run / expected_path
                if candidate.exists() and candidate.is_file():
                    dest = current_run_dir / expected_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(candidate, dest)
                        recovered_files.append(str(dest.resolve()))
                        logger.info(f"[CODE_EXECUTOR] Recovered {expected_text} from historical run {hist_run.name}")
                        found = True
                    except Exception as exc:
                        logger.warning(f"Failed to recover {expected_text}: {exc}")
            if has_glob:
                for match in list(hist_run.glob(str(expected_path))):
                    if match.is_file():
                        rel_path = match.relative_to(hist_run)
                        dest = current_run_dir / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(match, dest)
                            recovered_files.append(str(dest.resolve()))
                            logger.info(f"[CODE_EXECUTOR] Recovered {rel_path} from historical run {hist_run.name}")
                            found = True
                        except Exception as exc:
                            logger.warning(f"Failed to recover {rel_path}: {exc}")
            if found:
                break
    return recovered_files


def _resolve_promoted_output_files(
    promoted: Sequence[str],
    *,
    session_dir: Path,
    output_dir: Optional[Path] = None,
) -> List[str]:
    prefix: Optional[Path] = None
    if output_dir is not None:
        try:
            prefix = output_dir.resolve().relative_to(session_dir.resolve())
        except (ValueError, OSError):
            prefix = None
    resolved: List[str] = []
    for rel in promoted:
        rel_path = Path(str(rel))
        if rel_path.is_absolute():
            resolved.append(str(rel_path))
            continue
        if prefix is not None and output_dir is not None:
            try:
                remainder: Optional[Path] = rel_path.relative_to(prefix)
            except ValueError:
                remainder = None
            if remainder is not None:
                rel_path = prefix / _collapse_rooted_rel_path(rel=remainder, output_dir=output_dir, session_dir=session_dir)
            else:
                collapsed = _collapse_rooted_rel_path(rel=rel_path, output_dir=output_dir, session_dir=session_dir)
                if collapsed != rel_path:
                    rel_path = prefix / collapsed
        resolved.append(str((session_dir / rel_path).resolve()))
    return resolved

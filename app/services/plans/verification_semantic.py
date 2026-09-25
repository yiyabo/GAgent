"""Semantic verification cluster of ``TaskVerificationService``.

Moved verbatim out of ``task_verification.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (TV cluster ⑤):
LLM arbitration of semantically-equivalent outputs (``_llm_arbitrate_verification``,
the ``app.llm`` lazy import preserved verbatim) and the semantic
materialization family (``_materialize_semantic_expected_deliverables`` …
``_semantic_candidate_score``): candidate discovery, tokenization, topic aliases
and scoring.  Composed into ``TaskVerificationService`` as the
``_SemanticMethods`` mixin, so every ``self.*``/``cls.*`` call site is unchanged
and **no body deviates from byte-verbatim**.

Patch surface: ``app/tests/tools/test_task_verification.py:1087`` patches
``monkeypatch.setattr(TaskVerificationService, "_llm_arbitrate_verification", ...)``.
That is a class-attribute patch; the mixin only supplies the method below the
class in the MRO, so the patched attribute still wins for every
``self._llm_arbitrate_verification(...)`` call.

The module uses its own ``logging.getLogger(__name__)`` (split precedent).
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .plan_models import PlanNode
from .verification_cues import (
    _SEMANTIC_DELIVERABLE_KEYWORDS,
    _SEMANTIC_DELIVERABLE_SUFFIXES,
    _SEMANTIC_FILENAME_STOPWORDS,
    _SEMANTIC_SINGLETON_FALLBACK_GENERIC_TOKENS,
    _SEMANTIC_TOPIC_ALIASES,
)

logger = logging.getLogger(__name__)


class _SemanticMethods:
    """Semantic verification cluster of ``TaskVerificationService`` (mixin)."""

    def _llm_arbitrate_verification(
        self,
        *,
        node: Any,
        failures: List[Dict[str, Any]],
        artifact_paths: Sequence[str],
        payload: Dict[str, Any],
    ) -> Optional[bool]:
        """Use a lightweight LLM call to judge whether actual outputs satisfy the task.

        Returns:
            True  — LLM judged outputs are sufficient (override failure)
            False — LLM judged outputs are insufficient (keep failure)
            None  — LLM call failed (caller should use fallback logic)
        """
        try:
            from app.llm import get_default_client
        except Exception:
            logger.warning("[Verification] Cannot import LLM client for arbitration.")
            return None

        task_instruction = str(getattr(node, "instruction", "") or "").strip()
        if not task_instruction:
            task_instruction = str(getattr(node, "name", "") or "").strip()
        if not task_instruction:
            return None

        # Build a concise list of actual output files (name + size)
        actual_files: List[str] = []
        for raw_path in artifact_paths:
            p = Path(raw_path)
            try:
                if p.exists() and p.is_file():
                    size_kb = p.stat().st_size / 1024
                    actual_files.append(f"{p.name} ({size_kb:.0f} KB)")
                else:
                    actual_files.append(p.name)
            except OSError:
                actual_files.append(p.name)
        # Deduplicate by name while preserving order
        seen_names: set[str] = set()
        deduped_files: List[str] = []
        for item in actual_files:
            name_part = item.split(" (")[0]
            if name_part not in seen_names:
                seen_names.add(name_part)
                deduped_files.append(item)
        actual_files_text = "\n".join(f"  - {f}" for f in deduped_files[:20]) or "  (none)"

        # Build failure summary
        failure_descriptions = []
        for f in failures[:5]:
            check_type = f.get("type", "unknown")
            message = f.get("message", "")
            path = f.get("path", "")
            failure_descriptions.append(f"  - [{check_type}] {path}: {message}")
        failures_text = "\n".join(failure_descriptions)

        # Extract execution stdout summary if available
        exec_stdout = ""
        metadata = payload.get("metadata", {})
        content = str(payload.get("content", "")).strip()
        if content and len(content) > 20:
            exec_stdout = content[:1500]

        prompt = (
            "You are a task verification judge. A task has been executed and produced output files, "
            "but the automated file-name check failed. Your job is to determine whether the actual "
            "outputs semantically satisfy the task requirements, even if the filenames differ.\n\n"
            f"## Task Instruction\n{task_instruction}\n\n"
            f"## Automated Check Failures\n{failures_text}\n\n"
            f"## Actual Output Files\n{actual_files_text}\n\n"
        )
        if exec_stdout:
            prompt += f"## Execution Summary\n{exec_stdout[:1000]}\n\n"
        prompt += (
            "## Your Judgment\n"
            "Based on the task instruction and actual outputs, do the outputs satisfy the task requirements?\n"
            "Consider: format equivalence (csv≈parquet≈tsv), naming variations, and whether the data content "
            "matches what was requested.\n\n"
            'Respond with EXACTLY one line in this format:\n'
            'VERDICT: pass\n'
            'or\n'
            'VERDICT: fail\n\n'
            'Then on the next line, briefly explain your reasoning (one sentence).'
        )

        try:
            client = get_default_client()
            response = client.chat(prompt, max_tokens=256, timeout=15)
            response_text = str(response or "").strip()

            # Parse verdict
            for line in response_text.splitlines():
                line_stripped = line.strip().upper()
                if line_stripped.startswith("VERDICT:"):
                    verdict_value = line_stripped[len("VERDICT:"):].strip()
                    if verdict_value == "PASS":
                        # Extract reasoning
                        reasoning_lines = [
                            l.strip() for l in response_text.splitlines()
                            if l.strip() and not l.strip().upper().startswith("VERDICT:")
                        ]
                        reasoning = reasoning_lines[0] if reasoning_lines else ""
                        logger.info(
                            "[Verification] LLM arbitration PASS for task %s: %s",
                            getattr(node, "id", "?"),
                            reasoning[:200],
                        )
                        return True
                    elif verdict_value == "FAIL":
                        reasoning_lines = [
                            l.strip() for l in response_text.splitlines()
                            if l.strip() and not l.strip().upper().startswith("VERDICT:")
                        ]
                        reasoning = reasoning_lines[0] if reasoning_lines else ""
                        logger.info(
                            "[Verification] LLM arbitration FAIL for task %s: %s",
                            getattr(node, "id", "?"),
                            reasoning[:200],
                        )
                        return False

            # Could not parse verdict
            logger.warning(
                "[Verification] LLM arbitration returned unparseable response for task %s: %s",
                getattr(node, "id", "?"),
                response_text[:200],
            )
            return None

        except Exception as exc:
            logger.warning(
                "[Verification] LLM arbitration call failed for task %s: %s",
                getattr(node, "id", "?"),
                exc,
            )
            return None

    def _materialize_semantic_expected_deliverables(
        self,
        *,
        node: PlanNode,
        criteria: Optional[Dict[str, Any]],
        artifact_paths: Sequence[str],
        base_dir: Path,
    ) -> List[str]:
        updated_paths: List[str] = []
        seen: set[str] = set()
        for raw in artifact_paths:
            text = str(raw or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            updated_paths.append(text)

        for expected in self._expected_deliverables(criteria):
            if not self._should_semantically_materialize_expected(expected):
                continue
            target = self._resolve_semantic_materialization_target(expected, base_dir)
            if target is None:
                logger.info(
                    "Skipping semantic materialization for unsafe target %r (task=%s, base_dir=%s)",
                    expected,
                    node.id,
                    base_dir,
                )
                continue
            if target.exists() and target.is_file() and target.stat().st_size > 0:
                target_text = str(target)
                if target_text not in seen:
                    seen.add(target_text)
                    updated_paths.append(target_text)
                continue

            candidate = self._select_semantic_expected_candidate(
                node=node,
                expected=expected,
                artifact_paths=updated_paths,
            )
            if candidate is None:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if candidate.resolve() != target.resolve():
                    shutil.copy2(candidate, target)
            except Exception as exc:
                logger.warning(
                    "Failed to materialize semantic deliverable %s from %s for task %s: %s",
                    target,
                    candidate,
                    node.id,
                    exc,
                )
                continue

            target_text = str(target)
            if target_text not in seen:
                seen.add(target_text)
                updated_paths.append(target_text)

        return updated_paths[:80]

    @staticmethod
    def _should_semantically_materialize_expected(expected: str) -> bool:
        text = str(expected or "").strip().replace("\\", "/")
        if not text or any(token in text for token in ("*", "?", "[")):
            return False
        path = Path(text)
        if path.suffix.lower() not in _SEMANTIC_DELIVERABLE_SUFFIXES:
            return False
        lowered = path.stem.lower()
        if any(keyword in lowered for keyword in _SEMANTIC_DELIVERABLE_KEYWORDS):
            return True
        return any(
            keyword in part.lower()
            for part in path.parts[:-1]
            for keyword in _SEMANTIC_DELIVERABLE_KEYWORDS
        )

    @staticmethod
    def _resolve_semantic_materialization_target(expected: str, base_dir: Path) -> Optional[Path]:
        text = str(expected or "").strip()
        if not text:
            return None

        raw_target = Path(text).expanduser()
        if raw_target.is_absolute():
            return None

        try:
            resolved_base = base_dir.expanduser().resolve()
        except Exception:
            resolved_base = base_dir.expanduser()

        try:
            resolved_target = (resolved_base / raw_target).resolve()
        except Exception:
            resolved_target = resolved_base / raw_target

        try:
            resolved_target.relative_to(resolved_base)
        except ValueError:
            return None
        return resolved_target

    def _select_semantic_expected_candidate(
        self,
        *,
        node: PlanNode,
        expected: str,
        artifact_paths: Sequence[str],
    ) -> Optional[Path]:
        candidates = self._semantic_candidate_files(node=node, artifact_paths=artifact_paths)
        if not candidates:
            return None

        expected_name = Path(str(expected or "")).name.lower()
        expected_core = self._semantic_core_tokens(expected_name)
        task_core = self._semantic_task_tokens(node)
        topic_core = self._semantic_topic_tokens(
            expected_core=expected_core,
            task_core=task_core,
        )

        best_score: Optional[tuple[int, int, int, int]] = None
        best_path: Optional[Path] = None
        for candidate in candidates:
            score = self._semantic_candidate_score(
                expected_name=expected_name,
                expected_core=expected_core,
                candidate_name=candidate.name.lower(),
                candidate_count=len(candidates),
                topic_core=topic_core,
            )
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_path = candidate
        return best_path

    def _semantic_candidate_files(
        self,
        *,
        node: PlanNode,
        artifact_paths: Sequence[str],
    ) -> List[Path]:
        all_files: List[Path] = []
        current_task_files: List[Path] = []
        seen: set[str] = set()
        task_marker = f"/task_{node.id}/"

        for raw in artifact_paths:
            path = Path(str(raw)).expanduser()
            if not path.exists() or not path.is_file():
                continue
            if self._is_internal_artifact_path(str(path)):
                continue
            lowered_name = path.name.lower()
            if lowered_name.endswith(".analysis.md") or lowered_name.endswith(".partial.md"):
                continue
            if path.suffix.lower() not in _SEMANTIC_DELIVERABLE_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            all_files.append(resolved)
            if task_marker in str(resolved).replace("\\", "/"):
                current_task_files.append(resolved)

        return current_task_files or all_files

    @staticmethod
    def _semantic_text_tokens(text: str) -> set[str]:
        tokens = [
            token
            for token in re.split(r"[^a-z0-9]+", str(text or "").lower())
            if token and not token.isdigit()
        ]
        return {
            token
            for token in tokens
            if token not in _SEMANTIC_FILENAME_STOPWORDS
        }

    @classmethod
    def _semantic_core_tokens(cls, file_name: str) -> set[str]:
        stem = Path(str(file_name or "")).stem.lower()
        return cls._semantic_text_tokens(stem)

    @classmethod
    def _semantic_task_tokens(cls, node: PlanNode) -> set[str]:
        return cls._semantic_text_tokens(
            " ".join(
                part
                for part in (
                    str(getattr(node, "name", "") or "").strip(),
                    str(getattr(node, "instruction", "") or "").strip(),
                )
                if part
            )
        )

    @classmethod
    def _semantic_topic_tokens(
        cls,
        *,
        expected_core: set[str],
        task_core: set[str],
    ) -> set[str]:
        topic_core = set(expected_core) | set(task_core)
        expanded = set(topic_core)
        for token in list(topic_core):
            expanded.update(_SEMANTIC_TOPIC_ALIASES.get(token, set()))
        return expanded

    @staticmethod
    def _allow_singleton_semantic_fallback(
        *,
        candidate_core: set[str],
        topic_core: set[str],
    ) -> bool:
        informative_core = candidate_core - _SEMANTIC_SINGLETON_FALLBACK_GENERIC_TOKENS
        return bool(informative_core & topic_core)

    def _semantic_candidate_score(
        self,
        *,
        expected_name: str,
        expected_core: set[str],
        candidate_name: str,
        candidate_count: int,
        topic_core: set[str],
    ) -> Optional[tuple[int, int, int, int]]:
        if candidate_name == expected_name:
            return (1000, 0, 0, 0)

        candidate_core = self._semantic_core_tokens(candidate_name)
        overlap = len(expected_core & candidate_core)
        extras = len(candidate_core - expected_core)
        missing = len(expected_core - candidate_core)

        base_score = 0
        if expected_core:
            if overlap == 0:
                if candidate_count == 1:
                    if not self._allow_singleton_semantic_fallback(
                        candidate_core=candidate_core,
                        topic_core=topic_core,
                    ):
                        return None
                    base_score = 1
                else:
                    return None
            else:
                base_score = overlap * 10 - extras - missing
        elif candidate_count == 1:
            base_score = 1
        else:
            return None

        if "evidence" in expected_name and "evidence" in candidate_name:
            base_score += 3
        if candidate_name.endswith("_summary.md"):
            base_score += 1
        if re.search(r"(?:^|[_-])v\d+$", Path(candidate_name).stem):
            base_score -= 1

        if base_score <= 0 and candidate_count > 1:
            return None

        return (base_score, overlap, -extras, -len(candidate_name))

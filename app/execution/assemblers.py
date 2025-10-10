"""Assemblers responsible for composing outputs from atomic tasks."""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..repository.tasks import default_repo
from ..services.llm.llm_service import get_llm_service
from ..utils.task_path_generator import get_task_file_path, ensure_task_directory
import os

logger = logging.getLogger(__name__)


class AssemblyStrategy(str, Enum):
    """Supported assembly strategies."""

    LLM = "llm"
    CONCAT = "concat"


class _BaseAssembler:
    """Shared helpers for composite/root assemblers."""

    def __init__(self, repo=None, llm_service=None):
        self.repo = repo or default_repo
        self.llm_service = llm_service or get_llm_service()

    @staticmethod
    def _normalise_strategy(strategy: Optional[str]) -> AssemblyStrategy:
        if not strategy:
            return AssemblyStrategy.LLM
        try:
            return AssemblyStrategy(strategy.lower())
        except ValueError:
            logger.warning("Unknown assembly strategy '%s', falling back to LLM", strategy)
            return AssemblyStrategy.LLM

    @staticmethod
    def _simple_concat(sections: List[Dict[str, Any]], separator: str) -> str:
        fragments: List[str] = []
        for section in sections:
            content = (section.get("content") or "").strip()
            if content:
                fragments.append(content)
        return separator.join(fragments)

    def _build_prompt(self, task: Dict[str, Any], sections: List[Dict[str, Any]], *, header: str, instructions: str) -> str:
        lines: List[str] = [header.strip(), "", f"[Target Task]\n{task.get('name', 'Untitled Task')}"]
        description = task.get("artifacts") or task.get("context_refs")
        if description:
            lines.append("[Additional Notes]")
            lines.append(str(description))
        lines.append("")
        lines.append("[Child Results]")
        if not sections:
            lines.append("- No child outputs are available yet.")
        else:
            for idx, section in enumerate(sections, start=1):
                title = section.get("title") or section.get("name") or f"Child Task {idx}"
                content = section.get("content") or "(no content yet)"
                lines.append(f"### {title}")
                lines.append(content.strip())
                lines.append("")
        lines.append("[Assembly Requirements]")
        lines.append(instructions.strip())
        lines.append("")
        lines.append("Maintain the dominant language used in the child outputs and return a well-structured, ready-to-use result.")
        return "\n".join(lines).strip()

    def _llm_assemble(
        self,
        task: Dict[str, Any],
        sections: List[Dict[str, Any]],
        *,
        header: str,
        instructions: str,
        force_real: bool,
    ) -> Tuple[Optional[str], str]:
        prompt = self._build_prompt(task, sections, header=header, instructions=instructions)
        try:
            response = self.llm_service.chat(prompt, force_real=force_real)
            return response, prompt
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning(
                "LLM assembly failed for task %s (%s): %s",
                task.get("id"),
                task.get("name"),
                exc,
            )
            return None, prompt


class CompositeAssembler(_BaseAssembler):
    """Combine outputs from child atomic tasks into a composite artifact."""

    def assemble(
        self,
        composite_task_id: int,
        *,
        strategy: str = AssemblyStrategy.LLM.value,
        force_real: bool = True,
    ) -> Dict[str, Any]:
        composite_task = self.repo.get_task_info(composite_task_id)
        if not composite_task:
            raise ValueError(f"Composite task {composite_task_id} not found")

        children = self.repo.get_children(composite_task_id)
        sections: List[Dict[str, Any]] = []
        child_ids: List[int] = []
        for child in children:
            if child.get("task_type") != "atomic":
                continue
            output = self.repo.get_task_output_content(child["id"])
            if not output:
                continue
            sections.append(
                {
                    "task_id": child["id"],
                    "name": child.get("name"),
                    "title": child.get("name"),
                    "content": output,
                }
            )
            child_ids.append(child["id"])

        strategy_enum = self._normalise_strategy(strategy)
        used_llm = strategy_enum == AssemblyStrategy.LLM
        assembled_content: str
        prompt_used: Optional[str] = None
        fallback_used = False

        if sections and used_llm:
            header = "You are an experienced execution coordinator who must consolidate multiple atomic task results."
            instructions = (
                "Combine the child deliverables into a coherent section, avoid duplication, and add transitions when needed."
                " Resolve any contradictions with a single authoritative description."
            )
            llm_output, prompt = self._llm_assemble(
                composite_task,
                sections,
                header=header,
                instructions=instructions,
                force_real=force_real,
            )
            prompt_used = prompt
            if llm_output:
                assembled_content = llm_output.strip()
            else:
                assembled_content = self._simple_concat(sections, "\n\n")
                fallback_used = True
        else:
            if not sections:
                assembled_content = "No child outputs are available yet; re-run assembly after all atomic tasks complete."
            else:
                assembled_content = self._simple_concat(sections, "\n\n")
            fallback_used = not sections or not used_llm

        artifacts = {
            "children": child_ids,
            "strategy": strategy_enum.value,
            "fallback_used": fallback_used,
            "child_summaries": [
                {"task_id": section["task_id"], "name": section.get("name")}
                for section in sections
            ],
        }

        # 写入到文件系统：results/<root>/<composite>/summary.md
        try:
            comp_dir = get_task_file_path(composite_task, self.repo)  # ends with '/'
            if ensure_task_directory(comp_dir):
                comp_summary_path = os.path.join(comp_dir, "summary.md")
                with open(comp_summary_path, "w", encoding="utf-8") as f:
                    f.write(assembled_content)
                logger.info("Composite summary written to %s", comp_summary_path)
        except Exception as e:
            logger.warning("Failed to write composite summary.md: %s", e)

        self.repo.upsert_task_output(composite_task_id, assembled_content)
        self.repo.update_task_context(
            composite_task_id,
            artifacts=json.dumps(artifacts, ensure_ascii=False),
        )
        self.repo.update_task_status(composite_task_id, "completed")
        self.repo.append_execution_log(
            composite_task_id,
            workflow_id=composite_task.get("workflow_id"),
            step_type="composite_assembly",
            content=assembled_content,
            metadata={
                "children": child_ids,
                "strategy": strategy_enum.value,
                "fallback_used": fallback_used,
                "prompt": prompt_used,
            },
        )
        return {
            "task_id": composite_task_id,
            "output": assembled_content,
            "children": child_ids,
            "strategy": strategy_enum.value,
            "fallback_used": fallback_used,
        }


class RootAssembler(_BaseAssembler):
    """Assemble the final deliverable from composite task outputs."""

    def assemble(
        self,
        root_task_id: int,
        *,
        strategy: str = AssemblyStrategy.LLM.value,
        force_real: bool = True,
    ) -> Dict[str, Any]:
        root_task = self.repo.get_task_info(root_task_id)
        if not root_task:
            raise ValueError(f"Root task {root_task_id} not found")

        composites = self.repo.get_children(root_task_id)
        sections: List[Dict[str, Any]] = []
        composite_ids: List[int] = []
        for task in composites:
            if task.get("task_type") != "composite":
                continue
            output = self.repo.get_task_output_content(task["id"])
            if not output:
                continue
            sections.append(
                {
                    "task_id": task["id"],
                    "name": task.get("name"),
                    "title": task.get("name"),
                    "content": output,
                }
            )
            composite_ids.append(task["id"])

        strategy_enum = self._normalise_strategy(strategy)
        used_llm = strategy_enum == AssemblyStrategy.LLM
        fallback_used = False
        prompt_used: Optional[str] = None

        if sections and used_llm:
            header = "You are a senior delivery expert who must merge several composite sections into a unified final deliverable."
            instructions = (
                "Produce a structured final result with clear sections or bullet points while keeping the overall narrative coherent."
                " Highlight any gaps and suggest follow-up actions when necessary."
            )
            llm_output, prompt = self._llm_assemble(
                root_task,
                sections,
                header=header,
                instructions=instructions,
                force_real=force_real,
            )
            prompt_used = prompt
            final_report = (llm_output or "").strip()
            if not final_report:
                final_report = self._simple_concat(sections, "\n\n---\n\n")
                fallback_used = True
        else:
            if not sections:
                final_report = "No composite outputs are available, so the final deliverable cannot yet be generated."
            else:
                final_report = self._simple_concat(sections, "\n\n---\n\n")
            fallback_used = not sections or not used_llm

        artifacts = {
            "composites": composite_ids,
            "strategy": strategy_enum.value,
            "fallback_used": fallback_used,
        }
        # 写入到文件系统：results/<root>/summary.md
        try:
            root_dir = get_task_file_path(root_task, self.repo)  # ends with '/'
            if ensure_task_directory(root_dir):
                root_summary_path = os.path.join(root_dir, "summary.md")
                with open(root_summary_path, "w", encoding="utf-8") as f:
                    f.write(final_report)
                logger.info("Root summary written to %s", root_summary_path)
        except Exception as e:
            logger.warning("Failed to write root summary.md: %s", e)

        self.repo.upsert_task_output(root_task_id, final_report)
        self.repo.update_task_context(
            root_task_id,
            artifacts=json.dumps(artifacts, ensure_ascii=False),
        )
        self.repo.update_task_status(root_task_id, "completed")
        self.repo.append_execution_log(
            root_task_id,
            workflow_id=root_task.get("workflow_id"),
            step_type="root_assembly",
            content=final_report,
            metadata={
                "composites": composite_ids,
                "strategy": strategy_enum.value,
                "fallback_used": fallback_used,
                "prompt": prompt_used,
            },
        )
        return {
            "task_id": root_task_id,
            "output": final_report,
            "composites": composite_ids,
            "strategy": strategy_enum.value,
            "fallback_used": fallback_used,
        }

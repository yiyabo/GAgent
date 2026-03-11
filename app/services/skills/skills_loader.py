"""Skills loader and runtime selection helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


VALID_CATEGORIES = {"router", "policy", "template", "writer", "analysis", "generic"}
VALID_SCOPES = {"plan", "task", "both"}
VALID_INJECTION_MODES = {"full", "summary", "summary_with_references"}


@dataclass(frozen=True)
class SkillSelectionConfig:
    keywords: List[str] = field(default_factory=list)
    path_suffixes: List[str] = field(default_factory=list)
    tool_hints: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillInjectionConfig:
    mode: str = "full"
    max_chars: int = 4000


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    content: str
    directory: str
    category: str = "generic"
    scope: str = "both"
    priority: int = 0
    selection: SkillSelectionConfig = field(default_factory=SkillSelectionConfig)
    injection: SkillInjectionConfig = field(default_factory=SkillInjectionConfig)
    references: List[str] = field(default_factory=list)
    scripts: List[str] = field(default_factory=list)
    has_config: bool = False
    has_references: bool = False


Skill = SkillSpec


@dataclass(frozen=True)
class SkillSelectionResult:
    candidate_skill_ids: List[str] = field(default_factory=list)
    selected_skill_ids: List[str] = field(default_factory=list)
    selection_source: str = "disabled"
    selection_latency_ms: float = 0.0


@dataclass(frozen=True)
class SkillInjectionResult:
    content: str = ""
    injection_mode_by_skill: Dict[str, str] = field(default_factory=dict)
    injected_chars: int = 0


class SkillsLoader:
    """Load skills from disk and provide selection/injection helpers."""

    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

    def __init__(
        self,
        skills_dir: Optional[str] = None,
        project_skills_dir: Optional[str] = None,
        auto_sync: bool = True,
    ) -> None:
        if skills_dir is None:
            self.skills_dir = Path.home() / ".claude" / "skills"
        else:
            self.skills_dir = Path(skills_dir)

        if project_skills_dir is None:
            self.project_skills_dir = self._PROJECT_ROOT / "skills"
        else:
            self.project_skills_dir = Path(project_skills_dir)

        self._loaded_skills: Set[str] = set()
        self._available_skills: Dict[str, SkillSpec] = {}
        self._validation_errors: Dict[str, List[str]] = {}

        logger.info("SkillsLoader initialized:")
        logger.info("  Project skills dir: %s", self.project_skills_dir)
        logger.info("  Runtime skills dir: %s", self.skills_dir)

        if auto_sync:
            self.sync_from_project()

        self._scan_skills()

    def sync_from_project(self) -> bool:
        """Sync project skills into the runtime skills directory."""
        if not self.project_skills_dir.exists():
            logger.warning(
                "Project skills directory not found: %s", self.project_skills_dir
            )
            return False

        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)

            synced_count = 0
            for item in self.project_skills_dir.iterdir():
                if not item.is_dir():
                    continue

                skill_file = item / "SKILL.md"
                if not skill_file.exists():
                    continue

                target_dir = self.skills_dir / item.name
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(item, target_dir)
                synced_count += 1

            logger.info("Synced %d skills to %s", synced_count, self.skills_dir)
            return True
        except Exception as exc:
            logger.error("Failed to sync skills: %s", exc)
            return False

    def _scan_skills(self) -> None:
        self._available_skills.clear()
        self._validation_errors.clear()

        if not self.skills_dir.exists():
            logger.warning("Skills directory not found: %s", self.skills_dir)
            return

        discovered_names: Set[str] = set()
        for item in self.skills_dir.iterdir():
            if not item.is_dir():
                continue

            skill_file = item / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                content = skill_file.read_text(encoding="utf-8")
            except Exception as exc:
                self._validation_errors[item.name] = [f"Failed to read SKILL.md: {exc}"]
                logger.warning("Failed to read skill %s: %s", item.name, exc)
                continue

            metadata = self._extract_frontmatter(content)
            skill_name = str(metadata.get("name") or item.name).strip() or item.name
            description = self._extract_description(content, metadata)

            errors: List[str] = []
            if skill_name in discovered_names:
                errors.append(f"Duplicate skill name: {skill_name}")

            config_path = item / "config.json"
            config_payload: Dict[str, Any] = {}
            has_config = config_path.exists()
            if has_config:
                try:
                    raw_payload = json.loads(config_path.read_text(encoding="utf-8"))
                    if not isinstance(raw_payload, dict):
                        errors.append("config.json must contain a JSON object")
                    else:
                        config_payload = raw_payload
                except Exception as exc:
                    errors.append(f"Failed to parse config.json: {exc}")

            references_dir = item / "references"
            has_references_dir = references_dir.exists() and references_dir.is_dir()
            spec, config_errors = self._build_skill_spec(
                skill_name=skill_name,
                description=description,
                content=content,
                skill_dir=item,
                config_payload=config_payload,
                has_config=has_config,
                has_references_dir=has_references_dir,
            )
            errors.extend(config_errors)

            if errors:
                self._validation_errors[item.name] = errors
                logger.warning("Skipping invalid skill %s: %s", item.name, "; ".join(errors))
                continue

            self._available_skills[spec.name] = spec
            discovered_names.add(spec.name)

        if self._validation_errors:
            logger.warning(
                "Skills validation completed with %d invalid skill(s)",
                len(self._validation_errors),
            )
        logger.info("Total skills available: %d", len(self._available_skills))

    def _build_skill_spec(
        self,
        *,
        skill_name: str,
        description: str,
        content: str,
        skill_dir: Path,
        config_payload: Dict[str, Any],
        has_config: bool,
        has_references_dir: bool,
    ) -> Tuple[SkillSpec, List[str]]:
        errors: List[str] = []

        version = config_payload.get("version", 1)
        if has_config and version != 1:
            errors.append("config.json version must be 1")

        category = str(config_payload.get("category", "generic")).strip() or "generic"
        if category not in VALID_CATEGORIES:
            errors.append(f"Invalid category: {category}")
            category = "generic"

        scope = str(config_payload.get("scope", "both")).strip() or "both"
        if scope not in VALID_SCOPES:
            errors.append(f"Invalid scope: {scope}")
            scope = "both"

        priority_raw = config_payload.get("priority", 0)
        try:
            priority = int(priority_raw)
        except Exception:
            errors.append(f"Invalid priority: {priority_raw!r}")
            priority = 0

        selection_payload = config_payload.get("selection", {})
        if selection_payload is None:
            selection_payload = {}
        if not isinstance(selection_payload, dict):
            errors.append("selection must be an object")
            selection_payload = {}

        injection_payload = config_payload.get("injection", {})
        if injection_payload is None:
            injection_payload = {}
        if not isinstance(injection_payload, dict):
            errors.append("injection must be an object")
            injection_payload = {}

        keywords = self._coerce_string_list(
            selection_payload.get("keywords"),
            field_name="selection.keywords",
            errors=errors,
        )
        path_suffixes = self._coerce_string_list(
            selection_payload.get("path_suffixes"),
            field_name="selection.path_suffixes",
            errors=errors,
        )
        tool_hints = self._coerce_string_list(
            selection_payload.get("tool_hints"),
            field_name="selection.tool_hints",
            errors=errors,
        )

        injection_mode = str(injection_payload.get("mode", "full")).strip() or "full"
        if injection_mode not in VALID_INJECTION_MODES:
            errors.append(f"Invalid injection.mode: {injection_mode}")
            injection_mode = "full"

        injection_max_chars_raw = injection_payload.get("max_chars", 4000)
        try:
            injection_max_chars = int(injection_max_chars_raw)
            if injection_max_chars <= 0:
                raise ValueError
        except Exception:
            errors.append(
                f"Invalid injection.max_chars: {injection_max_chars_raw!r}"
            )
            injection_max_chars = 4000

        references = self._validate_relative_files(
            skill_dir,
            config_payload.get("references"),
            field_name="references",
            errors=errors,
        )
        scripts = self._validate_relative_files(
            skill_dir,
            config_payload.get("scripts"),
            field_name="scripts",
            errors=errors,
        )

        spec = SkillSpec(
            name=skill_name,
            description=description,
            content=content,
            directory=str(skill_dir),
            category=category,
            scope=scope,
            priority=priority,
            selection=SkillSelectionConfig(
                keywords=keywords,
                path_suffixes=path_suffixes,
                tool_hints=tool_hints,
            ),
            injection=SkillInjectionConfig(
                mode=injection_mode,
                max_chars=injection_max_chars,
            ),
            references=references,
            scripts=scripts,
            has_config=has_config,
            has_references=bool(references) or has_references_dir,
        )
        return spec, errors

    def _coerce_string_list(
        self,
        value: Any,
        *,
        field_name: str,
        errors: List[str],
    ) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            errors.append(f"{field_name} must be a list of strings")
            return []
        result: List[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            result.append(text)
        return result

    def _validate_relative_files(
        self,
        skill_dir: Path,
        value: Any,
        *,
        field_name: str,
        errors: List[str],
    ) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            errors.append(f"{field_name} must be a list of relative file paths")
            return []

        validated: List[str] = []
        for raw_path in value:
            rel_path = str(raw_path).strip()
            if not rel_path:
                continue
            candidate = Path(rel_path)
            if candidate.is_absolute():
                errors.append(f"{field_name} entry must be relative: {rel_path}")
                continue
            normalized = candidate.as_posix()
            if normalized.startswith("../") or "/../" in normalized or normalized == "..":
                errors.append(f"{field_name} entry escapes skill directory: {rel_path}")
                continue
            resolved = (skill_dir / candidate).resolve()
            try:
                resolved.relative_to(skill_dir.resolve())
            except ValueError:
                errors.append(f"{field_name} entry escapes skill directory: {rel_path}")
                continue
            if not resolved.exists() or not resolved.is_file():
                errors.append(f"{field_name} entry does not exist: {rel_path}")
                continue
            validated.append(normalized)
        return validated

    def _extract_frontmatter(self, content: str) -> Dict[str, str]:
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}

        payload: Dict[str, str] = {}
        for line in lines[1:]:
            stripped = line.strip()
            if stripped == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            text = value.strip()
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            elif text.startswith("'") and text.endswith("'"):
                text = text[1:-1]
            payload[key.strip()] = text
        return payload

    def _extract_description(
        self, content: str, metadata: Optional[Dict[str, str]] = None
    ) -> str:
        if metadata and metadata.get("description"):
            return metadata["description"][:500]

        lines = content.splitlines()
        in_frontmatter = bool(lines and lines[0].strip() == "---")
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if in_frontmatter:
                if idx == 0:
                    continue
                if stripped == "---":
                    in_frontmatter = False
                continue
            if stripped.startswith("#"):
                continue
            return stripped[:500]
        return "No description available"

    def list_skills(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        skills_info: List[Dict[str, Any]] = []
        for skill in sorted(self._available_skills.values(), key=lambda item: item.name):
            if category and skill.category != category:
                continue
            info = asdict(skill)
            info["loaded"] = skill.name in self._loaded_skills
            skills_info.append(info)
        return skills_info

    def validate_skills(self) -> Dict[str, Any]:
        return {
            "valid_skills": sorted(self._available_skills.keys()),
            "invalid_skills": {
                name: list(errors) for name, errors in sorted(self._validation_errors.items())
            },
        }

    def is_skill_loaded(self, skill_name: str) -> bool:
        return skill_name in self._loaded_skills

    def get_skill(self, skill_name: str) -> Optional[SkillSpec]:
        return self._available_skills.get(skill_name)

    def load_skill(self, skill_name: str) -> Optional[str]:
        if skill_name in self._loaded_skills:
            logger.info("Skill %s already loaded, skipping", skill_name)
            return None

        skill = self._available_skills.get(skill_name)
        if skill is None:
            logger.warning(
                "Skill %s not found. Available: %s",
                skill_name,
                ", ".join(sorted(self._available_skills.keys())),
            )
            return None

        self._loaded_skills.add(skill_name)
        return self._format_full_skill(skill)

    def load_multiple_skills(self, skill_names: List[str]) -> str:
        loaded_contents: List[str] = []
        for skill_name in skill_names:
            content = self.load_skill(skill_name)
            if content:
                loaded_contents.append(content)
        return "\n\n---\n\n".join(loaded_contents)

    def get_skill_content(self, skill_name: str) -> Optional[str]:
        skill = self._available_skills.get(skill_name)
        if skill is None:
            return None
        return skill.content

    def build_skill_context(
        self,
        skill_names: List[str],
        *,
        max_chars: int = 8000,
    ) -> SkillInjectionResult:
        if not skill_names or max_chars <= 0:
            return SkillInjectionResult()

        selected_specs = [
            self._available_skills[name]
            for name in skill_names
            if name in self._available_skills
        ]
        selected_specs.sort(key=lambda item: (-item.priority, item.name))

        parts: List[str] = []
        modes: Dict[str, str] = {}
        used = 0

        for skill in selected_specs:
            remaining = max_chars - used
            if remaining <= 0:
                break

            rendered, mode = self._render_skill_with_budget(skill, remaining)
            if not rendered:
                continue

            joiner = "\n\n" if parts else ""
            addition = joiner + rendered
            if len(addition) > remaining:
                break

            parts.append(rendered)
            modes[skill.name] = mode
            used += len(addition)
            self._loaded_skills.add(skill.name)

        content = "\n\n".join(parts)
        return SkillInjectionResult(
            content=content,
            injection_mode_by_skill=modes,
            injected_chars=len(content),
        )

    def load_skills_within_budget(
        self,
        skill_names: List[str],
        max_chars: int = 8000,
    ) -> str:
        return self.build_skill_context(skill_names, max_chars=max_chars).content

    def _render_skill_with_budget(
        self,
        skill: SkillSpec,
        remaining_global: int,
    ) -> Tuple[str, str]:
        max_chars = min(skill.injection.max_chars, remaining_global)
        if max_chars <= 0:
            return "", skill.injection.mode

        preferred_mode = skill.injection.mode
        if preferred_mode == "full":
            full = self._format_full_skill(skill)
            if len(full) <= max_chars:
                return full, "full"
            return self._fit_summary(skill, max_chars)

        if preferred_mode == "summary":
            return self._fit_summary(skill, max_chars)

        summary, summary_mode = self._fit_summary(skill, max_chars)
        if not summary:
            return "", summary_mode

        parts = [summary]
        used = len(summary)
        included_reference = False
        for rel_path in skill.references:
            ref_text = self._format_reference(skill, rel_path)
            addition = "\n\n" + ref_text
            if used + len(addition) > max_chars:
                break
            parts.append(ref_text)
            used += len(addition)
            included_reference = True
        return "\n\n".join(parts), (
            "summary_with_references" if included_reference else summary_mode
        )

    def _fit_summary(self, skill: SkillSpec, max_chars: int) -> Tuple[str, str]:
        summary = self._format_summary_skill(skill)
        if len(summary) <= max_chars:
            return summary, "summary"

        minimal = f"- {skill.name}: {skill.description}"
        if len(minimal) <= max_chars:
            return minimal, "summary"
        return minimal[:max_chars].rstrip(), "summary"

    def _format_full_skill(self, skill: SkillSpec) -> str:
        lines = [f"[Skill: {skill.name}]"]
        if skill.references:
            lines.append(f"References: {', '.join(skill.references)}")
        if skill.scripts:
            lines.append(f"Scripts: {', '.join(skill.scripts)}")
        lines.append(f"Base directory: {skill.directory}")
        lines.append("")
        lines.append(skill.content)
        return "\n".join(lines)

    def _format_summary_skill(self, skill: SkillSpec) -> str:
        lines = [
            f"[Skill: {skill.name}]",
            f"Summary: {skill.description}",
        ]
        constraints = self._extract_key_constraints(skill.content, limit=3)
        if constraints:
            lines.append("Key constraints:")
            lines.extend(f"- {item}" for item in constraints)
        if skill.scripts:
            lines.append(f"Scripts: {', '.join(skill.scripts)}")
        return "\n".join(lines)

    def _format_reference(self, skill: SkillSpec, rel_path: str) -> str:
        ref_path = Path(skill.directory) / rel_path
        try:
            body = ref_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            body = f"(failed to read reference: {exc})"
        return f"[Reference: {rel_path}]\n{body}"

    def _extract_key_constraints(self, content: str, *, limit: int = 3) -> List[str]:
        constraints: List[str] = []
        in_frontmatter = False
        in_code_block = False

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "---" and not constraints:
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            if stripped.startswith("- "):
                constraints.append(stripped[2:].strip())
            elif stripped[0].isdigit() and ". " in stripped:
                constraints.append(stripped.split(". ", 1)[1].strip())
            if len(constraints) >= limit:
                break
        return constraints

    def _eligible_skills(self, scope: str) -> List[SkillSpec]:
        if scope == "plan":
            allowed_scopes = {"plan", "both"}
        elif scope == "task":
            allowed_scopes = {"task", "both"}
        else:
            allowed_scopes = VALID_SCOPES
        return [
            skill
            for skill in self._available_skills.values()
            if skill.scope in allowed_scopes
        ]

    def _deterministic_candidates(
        self,
        *,
        eligible: Sequence[SkillSpec],
        task_title: str,
        task_description: str,
        dependency_paths: Optional[Sequence[str]],
        tool_hints: Optional[Sequence[str]],
        preferred_skills: Optional[Sequence[str]],
    ) -> List[str]:
        normalized_text = f"{task_title}\n{task_description}".lower()
        normalized_paths = [str(path).lower() for path in (dependency_paths or []) if path]
        normalized_hints = {
            str(hint).strip().lower()
            for hint in (tool_hints or [])
            if str(hint).strip()
        }
        preferred = set(preferred_skills or [])

        scored: List[Tuple[int, int, str]] = []
        for skill in eligible:
            score = 0
            matched = False

            for keyword in skill.selection.keywords:
                if keyword.lower() in normalized_text:
                    score += 10
                    matched = True

            for suffix in skill.selection.path_suffixes:
                suffix_norm = suffix.lower()
                if any(path.endswith(suffix_norm) for path in normalized_paths):
                    score += 20
                    matched = True
                    break

            for hint in skill.selection.tool_hints:
                if hint.lower() in normalized_hints:
                    score += 15
                    matched = True

            if skill.name in preferred:
                score += 5
                matched = True

            if matched:
                scored.append((score, skill.priority, skill.name))

        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [name for _, _, name in scored]

    async def select_skills(
        self,
        *,
        task_title: str,
        task_description: str,
        llm_service: Any,
        dependency_paths: Optional[Sequence[str]] = None,
        tool_hints: Optional[Sequence[str]] = None,
        preferred_skills: Optional[Sequence[str]] = None,
        selection_mode: str = "hybrid",
        max_skills: int = 3,
        scope: str = "task",
    ) -> SkillSelectionResult:
        started = time.perf_counter()
        if max_skills <= 0:
            return SkillSelectionResult(selection_source="disabled")

        eligible = self._eligible_skills(scope)
        if not eligible:
            return SkillSelectionResult(selection_source="disabled")

        max_skills = max(1, max_skills)
        selection_mode = selection_mode if selection_mode in {"hybrid", "llm_only"} else "hybrid"

        candidate_ids: List[str] = []
        selected_ids: List[str] = []
        source = "disabled"

        if selection_mode == "llm_only":
            llm_pool = self._sort_by_priority(eligible)[:5] or self._sort_by_priority(eligible)
            candidate_ids = [skill.name for skill in llm_pool]
            selected_ids, source = await self._llm_rank_skills(
                llm_service=llm_service,
                candidates=llm_pool,
                task_title=task_title,
                task_description=task_description,
                dependency_paths=dependency_paths,
                tool_hints=tool_hints,
                max_skills=max_skills,
                fallback_candidates=[],
            )
        else:
            deterministic_ids = self._deterministic_candidates(
                eligible=eligible,
                task_title=task_title,
                task_description=task_description,
                dependency_paths=dependency_paths,
                tool_hints=tool_hints,
                preferred_skills=preferred_skills,
            )
            candidate_ids = list(deterministic_ids)

            if 0 < len(deterministic_ids) <= max_skills:
                source = "deterministic"
                selected_ids = [
                    skill.name
                    for skill in self._sort_by_priority(
                        [self._available_skills[name] for name in deterministic_ids]
                    )[:max_skills]
                ]
            else:
                if deterministic_ids:
                    llm_pool = self._sort_by_priority(
                        [self._available_skills[name] for name in deterministic_ids]
                    )[:5]
                    fallback_candidates = self._sort_by_priority(
                        [self._available_skills[name] for name in deterministic_ids]
                    )
                    candidate_ids = [skill.name for skill in llm_pool]
                else:
                    llm_pool = self._sort_by_priority(eligible)[:5] or self._sort_by_priority(eligible)
                    fallback_candidates = []
                    candidate_ids = [skill.name for skill in llm_pool]

                selected_ids, source = await self._llm_rank_skills(
                    llm_service=llm_service,
                    candidates=llm_pool,
                    task_title=task_title,
                    task_description=task_description,
                    dependency_paths=dependency_paths,
                    tool_hints=tool_hints,
                    max_skills=max_skills,
                    fallback_candidates=fallback_candidates,
                )

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return SkillSelectionResult(
            candidate_skill_ids=candidate_ids,
            selected_skill_ids=selected_ids,
            selection_source=source,
            selection_latency_ms=latency_ms,
        )

    async def _llm_rank_skills(
        self,
        *,
        llm_service: Any,
        candidates: Sequence[SkillSpec],
        task_title: str,
        task_description: str,
        dependency_paths: Optional[Sequence[str]],
        tool_hints: Optional[Sequence[str]],
        max_skills: int,
        fallback_candidates: Sequence[SkillSpec],
    ) -> Tuple[List[str], str]:
        if not candidates:
            return [], "llm_fallback"

        candidate_summaries = "\n".join(
            f"- {skill.name}: {skill.description} "
            f"(category={skill.category}, priority={skill.priority}, scope={skill.scope})"
            for skill in candidates
        )
        dependency_summary = ", ".join(str(path) for path in (dependency_paths or [])[:10]) or "(none)"
        tool_summary = ", ".join(str(hint) for hint in (tool_hints or [])[:10]) or "(none)"
        prompt = f"""You are ranking runtime skills for a task.

Select up to {max_skills} skills from the candidate list. Return JSON only.

## Candidates
{candidate_summaries}

## Task
Title: {task_title}
Description: {task_description}
Dependency paths: {dependency_summary}
Tool hints: {tool_summary}

## Rules
1. Choose only skills that materially improve task execution.
2. Prefer fewer skills when uncertain.
3. Return JSON only in this format:
{{"selected_skills": ["skill-a", "skill-b"]}}
"""

        try:
            if llm_service is None or not hasattr(llm_service, "chat"):
                raise ValueError("llm_service.chat is unavailable")

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, llm_service.chat, prompt)
            response_text = str(response).strip()
            if response_text.startswith("```"):
                lines = response_text.splitlines()
                if lines:
                    lines.pop(0)
                if lines and lines[-1].strip().startswith("```"):
                    lines.pop()
                response_text = "\n".join(lines).strip()

            start = response_text.find("{")
            end = response_text.rfind("}")
            if start == -1 or end == -1:
                raise ValueError("No JSON object found in LLM skill selection response")

            payload = json.loads(response_text[start : end + 1])
            selected = payload.get("selected_skills", [])
            if not isinstance(selected, list):
                raise ValueError("selected_skills must be a list")

            candidate_names = {skill.name for skill in candidates}
            chosen: List[str] = []
            for item in selected:
                name = str(item).strip()
                if not name or name not in candidate_names or name in chosen:
                    continue
                chosen.append(name)
                if len(chosen) >= max_skills:
                    break

            if chosen:
                return chosen, "llm_ranked"
            raise ValueError("LLM returned no valid skills")
        except Exception as exc:
            logger.warning("LLM skill selection failed: %s", exc)
            fallback = [
                skill.name
                for skill in self._sort_by_priority(fallback_candidates)[:max_skills]
            ]
            return fallback, "llm_fallback"

    def _sort_by_priority(self, skills: Sequence[SkillSpec]) -> List[SkillSpec]:
        return sorted(skills, key=lambda item: (-item.priority, item.name))

    async def select_skills_for_task(
        self,
        task_title: str,
        task_description: str,
        llm_service: Any,
    ) -> List[str]:
        result = await self.select_skills(
            task_title=task_title,
            task_description=task_description,
            llm_service=llm_service,
        )
        return result.selected_skill_ids

    async def select_plan_skill_candidates(
        self,
        *,
        plan_title: str,
        plan_description: str,
        llm_service: Any,
        max_skills: int = 5,
        selection_mode: str = "hybrid",
    ) -> SkillSelectionResult:
        return await self.select_skills(
            task_title=plan_title,
            task_description=plan_description,
            llm_service=llm_service,
            selection_mode=selection_mode,
            max_skills=max_skills,
            scope="plan",
        )

    def reset_loaded_skills(self) -> None:
        self._loaded_skills.clear()
        logger.info("Reset loaded skills")

    def get_skills_summary_for_llm(self) -> str:
        if not self._available_skills:
            return "No skills available"

        summary_lines = ["Available skills:"]
        for skill_name, skill in sorted(self._available_skills.items()):
            summary_lines.append(f"- {skill_name}: {skill.description}")
        return "\n".join(summary_lines)


_global_skills_loader: Optional[SkillsLoader] = None


def get_skills_loader(
    skills_dir: Optional[str] = None,
    project_skills_dir: Optional[str] = None,
    auto_sync: bool = True,
) -> SkillsLoader:
    global _global_skills_loader
    if _global_skills_loader is None:
        _global_skills_loader = SkillsLoader(
            skills_dir=skills_dir,
            project_skills_dir=project_skills_dir,
            auto_sync=auto_sync,
        )
    return _global_skills_loader


def validate_skills(
    skills_dir: Optional[str] = None,
    project_skills_dir: Optional[str] = None,
    auto_sync: bool = False,
) -> Dict[str, Any]:
    loader = SkillsLoader(
        skills_dir=skills_dir,
        project_skills_dir=project_skills_dir,
        auto_sync=auto_sync,
    )
    return loader.validate_skills()

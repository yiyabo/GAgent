"""
Skills Loader - loadanalysis

Skills : 
- file:  skills/ ()
- : ~/.claude/skills/(Claude Code load)

,  Claude Code load skills. 
"""

import json
import logging
import shutil
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """ Skill """
    name: str
    description: str
    content: str  # SKILL.md content
    directory: str  # Skill 
    has_config: bool = False
    has_references: bool = False


class SkillsLoader:
    """
    Skills load - loadanalysis

    : 
    1.  skills  ~/.claude/skills/
    2. loadavailable skills
    3.  LLM related skills
    """

    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

    def __init__(
        self,
        skills_dir: Optional[str] = None,
        project_skills_dir: Optional[str] = None,
        auto_sync: bool = True
    ):
        """
        Skills Loader

        Args:
            skills_dir:  skills path, default ~/.claude/skills/
            project_skills_dir:  skills , default skills/
            auto_sync:  skills 
        """
        if skills_dir is None:
            self.skills_dir = Path.home() / ".claude" / "skills"
        else:
            self.skills_dir = Path(skills_dir)

        if project_skills_dir is None:
            self.project_skills_dir = self._PROJECT_ROOT / "skills"
        else:
            self.project_skills_dir = Path(project_skills_dir)

        self._loaded_skills: Set[str] = set()  # load skills()
        self._available_skills: Dict[str, Skill] = {}  # available skills 

        logger.info(f"SkillsLoader initialized:")
        logger.info(f"  Project skills dir: {self.project_skills_dir}")
        logger.info(f"  Runtime skills dir: {self.skills_dir}")

        if auto_sync:
            self.sync_from_project()

        self._scan_skills()

    def sync_from_project(self) -> bool:
        """
        skills  ~/.claude/skills/

        Returns:
            bool: success
        """
        if not self.project_skills_dir.exists():
            logger.warning(f"Project skills directory not found: {self.project_skills_dir}")
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
                logger.debug(f"Synced skill: {item.name}")

            logger.info(f"Synced {synced_count} skills to {self.skills_dir}")
            return True

        except Exception as e:
            logger.error(f"Failed to sync skills: {e}")
            return False

    def _scan_skills(self) -> None:
        """ skills , available skills"""
        self._available_skills.clear()

        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return

        for item in self.skills_dir.iterdir():
            if not item.is_dir():
                continue

            skill_file = item / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                content = skill_file.read_text(encoding='utf-8')

                description = self._extract_description(content)

                config_file = item / "config.json"
                references_dir = item / "references"
                has_config = config_file.exists()
                has_references = references_dir.exists() and references_dir.is_dir()

                skill = Skill(
                    name=item.name,
                    description=description,
                    content=content,
                    directory=str(item),
                    has_config=has_config,
                    has_references=has_references
                )

                self._available_skills[item.name] = skill
                logger.debug(f"Discovered skill: {item.name}")

            except Exception as e:
                logger.warning(f"Failed to load skill {item.name}: {e}")

        logger.info(f"Total skills available: {len(self._available_skills)}")

    def _extract_description(self, content: str) -> str:
        """
        SKILL.md contentmedium

        support YAML frontmatter : 
        ---
        name: skill-name
        description: skill description
        ---
        """
        lines = content.split('\n')

        if lines and lines[0].strip() == '---':
            in_frontmatter = True
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == '---':
                    break
                if line.startswith('description:'):
                    desc = line[len('description:'):].strip()
                    if desc.startswith('"') and desc.endswith('"'):
                        desc = desc[1:-1]
                    elif desc.startswith("'") and desc.endswith("'"):
                        desc = desc[1:-1]
                    return desc[:500]  # 

        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('---'):
                return line[:500]

        return "No description available"

    def list_skills(self, category: Optional[str] = None) -> List[Dict[str, any]]:
        """
        available skills

        Args:
            category: 

        Returns:
            skills 
        """
        skills_info = []
        for skill_name, skill in self._available_skills.items():
            skills_info.append({
                "name": skill_name,
                "description": skill.description,
                "has_config": skill.has_config,
                "has_references": skill.has_references,
                "loaded": skill_name in self._loaded_skills
            })
        return skills_info

    def is_skill_loaded(self, skill_name: str) -> bool:
        """ skill load()"""
        return skill_name in self._loaded_skills

    def load_skill(self, skill_name: str) -> Optional[str]:
        """
        load skill, content

        Args:
            skill_name: skill name

        Returns:
            skill content(), ifloaddoes not exist None
        """
        if skill_name in self._loaded_skills:
            logger.info(f"Skill {skill_name} already loaded, skipping")
            return None

        if skill_name not in self._available_skills:
            available = ', '.join(self._available_skills.keys())
            logger.warning(f"Skill {skill_name} not found. Available: {available}")
            return None

        skill = self._available_skills[skill_name]
        self._loaded_skills.add(skill_name)

        skill_marker = f"[Skill: {skill_name}]"
        injected_content = f"""{skill_marker}
Base directory: {skill.directory}

{skill.content}
"""

        logger.info(f"Loaded skill: {skill_name}")
        return injected_content

    def load_multiple_skills(self, skill_names: List[str]) -> str:
        """
        load skills

        Args:
            skill_names: skill name

        Returns:
            skills content
        """
        loaded_contents = []
        loaded_names = []
        failed_names = []

        for skill_name in skill_names:
            content = self.load_skill(skill_name)
            if content:
                loaded_contents.append(content)
                loaded_names.append(skill_name)
            else:
                if skill_name not in self._available_skills:
                    failed_names.append(skill_name)

        logger.info(f"Loaded {len(loaded_names)} skills: {loaded_names}")
        if failed_names:
            logger.warning(f"Failed to load skills: {failed_names}")

        return "\n\n---\n\n".join(loaded_contents)

    def get_skill_content(self, skill_name: str) -> Optional[str]:
        """get skill content(load)"""
        if skill_name not in self._available_skills:
            return None
        return self._available_skills[skill_name].content

    def load_skills_within_budget(
        self,
        skill_names: List[str],
        max_chars: int = 8000,
    ) -> str:
        """Load skill contents respecting a character budget.

        Skills are loaded in order.  Once the budget is exhausted, remaining
        skills are represented by a one-line name + description summary so
        no information is completely lost.

        Args:
            skill_names: Ordered list of skill names to load.
            max_chars: Maximum total character count for the returned text.

        Returns:
            Concatenated skill content (full or summarized).
        """
        if not skill_names:
            return ""

        parts: List[str] = []
        used = 0
        budget_exceeded = False

        for name in skill_names:
            if name not in self._available_skills:
                logger.debug(f"Skill '{name}' not found, skipping in budget loader")
                continue

            skill = self._available_skills[name]

            if budget_exceeded:
                summary = f"- {skill.name}: {skill.description}"
                parts.append(summary)
                continue

            formatted = f"[Skill: {skill.name}]\n{skill.content}"
            if used + len(formatted) <= max_chars:
                parts.append(formatted)
                used += len(formatted)
            else:
                remaining = max_chars - used
                if remaining > 200:
                    parts.append(formatted[:remaining] + "\n... (truncated)")
                    used = max_chars
                else:
                    summary = f"- {skill.name}: {skill.description}"
                    parts.append(summary)
                budget_exceeded = True

        result = "\n\n".join(parts)
        logger.info(
            f"Loaded {len(skill_names)} skills within budget "
            f"({used}/{max_chars} chars used)"
        )
        return result

    def reset_loaded_skills(self) -> None:
        """load skills """
        self._loaded_skills.clear()
        logger.info("Reset loaded skills")

    def get_skills_summary_for_llm(self) -> str:
        """
        LLM  skills summary()

        Returns:
            skills 
        """
        if not self._available_skills:
            return "No skills available"

        summary_lines = ["Available skills:"]
        for skill_name, skill in self._available_skills.items():
            summary_lines.append(f"- {skill_name}: {skill.description}")

        return "\n".join(summary_lines)

    async def select_skills_for_task(
        self,
        task_title: str,
        task_description: str,
        llm_service
    ) -> List[str]:
        """Use semantic LLM reasoning to select relevant skills for a task."""
        if not self._available_skills:
            logger.info("No skills available for selection")
            return []

        # Build summary of available skills.
        skills_summary = self.get_skills_summary_for_llm()

        prompt = f"""You are a skill selector. Choose the most relevant skills for the current task.

## Available Skills
{skills_summary}

## Current Task
Title: {task_title}
Description: {task_description}

## Requirements
1. Analyze the task semantics and objective.
2. Select only skills that materially help task completion.
3. You may select multiple skills, or none.
4. Return JSON only; do not include extra text.

## Response Format
{{"selected_skills": ["skill-name-1", "skill-name-2"]}}

If no skills are relevant, return:
{{"selected_skills": []}}
"""

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                llm_service.chat,
                prompt
            )

            response_text = response.strip()

            if response_text.startswith("```"):
                lines = response_text.splitlines()
                if lines:
                    lines.pop(0)
                if lines and lines[-1].strip() == "```":
                    lines.pop()
                response_text = "\n".join(lines).strip()

            start = response_text.find("{")
            end = response_text.rfind("}")
            if start != -1 and end != -1:
                json_str = response_text[start:end + 1]
                result = json.loads(json_str)
                selected = result.get("selected_skills", [])

                valid_skills = [s for s in selected if s in self._available_skills]
                if len(valid_skills) != len(selected):
                    invalid = set(selected) - set(valid_skills)
                    logger.warning(f"LLM selected invalid skills: {invalid}")

                logger.info(f"LLM selected skills for task '{task_title}': {valid_skills}")
                return valid_skills

        except json.JSONDecodeError as e:
            logger.warning(f"LLM skill selection JSON parse failed: {e}, response: {response_text}")
        except Exception as e:
            logger.error(f"LLM skill selection failed: {e}")

        return []


_global_skills_loader: Optional[SkillsLoader] = None


def get_skills_loader(
    skills_dir: Optional[str] = None,
    project_skills_dir: Optional[str] = None,
    auto_sync: bool = True
) -> SkillsLoader:
    """get SkillsLoader """
    global _global_skills_loader
    if _global_skills_loader is None:
        _global_skills_loader = SkillsLoader(
            skills_dir=skills_dir,
            project_skills_dir=project_skills_dir,
            auto_sync=auto_sync
        )
    return _global_skills_loader

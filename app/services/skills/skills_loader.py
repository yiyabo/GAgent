"""
Skills Loader - 加载和管理分析技能

Skills 采用双目录架构：
- 源文件: 项目 skills/ 目录（版本控制）
- 运行时: ~/.claude/skills/（Claude Code 加载位置）

启动时自动同步，确保 Claude Code 能够加载最新的 skills。
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
    """单个 Skill 的定义"""
    name: str
    description: str
    content: str  # SKILL.md 的完整内容
    directory: str  # Skill 所在目录
    has_config: bool = False
    has_references: bool = False


class SkillsLoader:
    """
    Skills 加载器 - 管理和加载预定义的分析技能
    
    功能：
    1. 从项目目录同步 skills 到 ~/.claude/skills/
    2. 扫描并加载可用的 skills
    3. 使用 LLM 智能选择相关 skills
    """

    # 项目根目录
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

    def __init__(
        self,
        skills_dir: Optional[str] = None,
        project_skills_dir: Optional[str] = None,
        auto_sync: bool = True
    ):
        """
        初始化 Skills Loader

        Args:
            skills_dir: 运行时 skills 目录路径，默认为 ~/.claude/skills/
            project_skills_dir: 项目 skills 源目录，默认为项目根目录的 skills/
            auto_sync: 是否自动同步项目 skills 到运行时目录
        """
        # 运行时目录（Claude Code 加载位置）
        if skills_dir is None:
            self.skills_dir = Path.home() / ".claude" / "skills"
        else:
            self.skills_dir = Path(skills_dir)

        # 项目源目录（版本控制）
        if project_skills_dir is None:
            self.project_skills_dir = self._PROJECT_ROOT / "skills"
        else:
            self.project_skills_dir = Path(project_skills_dir)

        self._loaded_skills: Set[str] = set()  # 已加载的 skills（防止重复）
        self._available_skills: Dict[str, Skill] = {}  # 可用的 skills 缓存

        logger.info(f"SkillsLoader initialized:")
        logger.info(f"  Project skills dir: {self.project_skills_dir}")
        logger.info(f"  Runtime skills dir: {self.skills_dir}")

        # 自动同步
        if auto_sync:
            self.sync_from_project()

        # 扫描可用 skills
        self._scan_skills()

    def sync_from_project(self) -> bool:
        """
        从项目目录同步 skills 到 ~/.claude/skills/

        Returns:
            bool: 同步是否成功
        """
        if not self.project_skills_dir.exists():
            logger.warning(f"Project skills directory not found: {self.project_skills_dir}")
            return False

        try:
            # 确保目标目录存在
            self.skills_dir.mkdir(parents=True, exist_ok=True)

            # 遍历项目 skills 目录
            synced_count = 0
            for item in self.project_skills_dir.iterdir():
                if not item.is_dir():
                    continue

                skill_file = item / "SKILL.md"
                if not skill_file.exists():
                    continue

                # 目标目录
                target_dir = self.skills_dir / item.name

                # 使用 shutil.copytree 同步整个 skill 目录
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
        """扫描 skills 目录，发现所有可用的 skills"""
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
                # 读取 skill 内容
                content = skill_file.read_text(encoding='utf-8')

                # 提取描述（从 YAML frontmatter）
                description = self._extract_description(content)

                # 检查是否有 config 文件和 references 目录
                config_file = item / "config.json"
                references_dir = item / "references"
                has_config = config_file.exists()
                has_references = references_dir.exists() and references_dir.is_dir()

                # 创建 Skill 对象
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
        从 SKILL.md 内容中提取描述

        支持 YAML frontmatter 格式：
        ---
        name: skill-name
        description: skill description
        ---
        """
        lines = content.split('\n')

        # 检查是否有 YAML frontmatter
        if lines and lines[0].strip() == '---':
            in_frontmatter = True
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == '---':
                    # frontmatter 结束
                    break
                if line.startswith('description:'):
                    # 提取 description 值
                    desc = line[len('description:'):].strip()
                    # 移除引号
                    if desc.startswith('"') and desc.endswith('"'):
                        desc = desc[1:-1]
                    elif desc.startswith("'") and desc.endswith("'"):
                        desc = desc[1:-1]
                    return desc[:500]  # 限制长度

        # 如果没有 frontmatter，取第一个非标题段落
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('---'):
                return line[:500]

        return "No description available"

    def list_skills(self, category: Optional[str] = None) -> List[Dict[str, any]]:
        """
        列出所有可用的 skills

        Args:
            category: 可选的分类过滤

        Returns:
            skills 信息列表
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
        """检查 skill 是否已加载（防止重复）"""
        return skill_name in self._loaded_skills

    def load_skill(self, skill_name: str) -> Optional[str]:
        """
        加载单个 skill，返回要注入到上下文的内容

        Args:
            skill_name: skill 名称

        Returns:
            skill 内容（带标记），如果已加载或不存在则返回 None
        """
        # 检查是否已加载
        if skill_name in self._loaded_skills:
            logger.info(f"Skill {skill_name} already loaded, skipping")
            return None

        # 检查 skill 是否存在
        if skill_name not in self._available_skills:
            available = ', '.join(self._available_skills.keys())
            logger.warning(f"Skill {skill_name} not found. Available: {available}")
            return None

        # 加载 skill
        skill = self._available_skills[skill_name]
        self._loaded_skills.add(skill_name)

        # 构建注入内容（带标记）
        skill_marker = f"[Skill: {skill_name}]"
        injected_content = f"""{skill_marker}
Base directory: {skill.directory}

{skill.content}
"""

        logger.info(f"Loaded skill: {skill_name}")
        return injected_content

    def load_multiple_skills(self, skill_names: List[str]) -> str:
        """
        加载多个 skills

        Args:
            skill_names: skill 名称列表

        Returns:
            所有 skills 的组合内容
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
        """获取 skill 的完整内容（不标记为已加载）"""
        if skill_name not in self._available_skills:
            return None
        return self._available_skills[skill_name].content

    def reset_loaded_skills(self) -> None:
        """重置已加载 skills 列表"""
        self._loaded_skills.clear()
        logger.info("Reset loaded skills")

    def get_skills_summary_for_llm(self) -> str:
        """
        生成给 LLM 看的 skills 摘要（用于智能选择）

        Returns:
            格式化的 skills 列表字符串
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
            # 使用 LLM 进行语义判断
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                llm_service.chat,
                prompt
            )

            # 解析 LLM 返回的 JSON
            response_text = response.strip()

            # 清理可能的 markdown 标记
            if response_text.startswith("```"):
                lines = response_text.splitlines()
                if lines:
                    lines.pop(0)
                if lines and lines[-1].strip() == "```":
                    lines.pop()
                response_text = "\n".join(lines).strip()

            # 尝试找到 JSON
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start != -1 and end != -1:
                json_str = response_text[start:end + 1]
                result = json.loads(json_str)
                selected = result.get("selected_skills", [])

                # 验证选中的 skills 是否存在
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


# 全局单例
_global_skills_loader: Optional[SkillsLoader] = None


def get_skills_loader(
    skills_dir: Optional[str] = None,
    project_skills_dir: Optional[str] = None,
    auto_sync: bool = True
) -> SkillsLoader:
    """获取全局 SkillsLoader 实例"""
    global _global_skills_loader
    if _global_skills_loader is None:
        _global_skills_loader = SkillsLoader(
            skills_dir=skills_dir,
            project_skills_dir=project_skills_dir,
            auto_sync=auto_sync
        )
    return _global_skills_loader

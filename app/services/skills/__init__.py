"""
Skills 管理模块

提供 Skills 的扫描、加载、同步和选择功能。
Skills 源文件存放在项目 skills/ 目录，运行时同步到 ~/.claude/skills/。
"""

from .skills_loader import SkillsLoader, Skill, get_skills_loader

__all__ = ["SkillsLoader", "Skill", "get_skills_loader"]

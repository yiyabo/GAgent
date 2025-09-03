"""
Prompt management system for centralized prompt template management.
"""

from .en_US import PROMPTS_EN_US
from .manager import PromptManager

# Default prompt manager instance with English as default
prompt_manager = PromptManager(default_lang="en_US")

__all__ = ["PromptManager", "prompt_manager", "PROMPTS_EN_US"]

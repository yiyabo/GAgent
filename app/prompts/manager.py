"""
Centralized prompt template manager for multi-language support and A/B testing.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class PromptManager:
    """
    Manages prompt templates with support for:
    - Multi-language prompts
    - Template variable substitution
    - A/B testing different prompt versions
    - Caching for performance
    """

    def __init__(self, default_lang: str = "en_US"):
        """
        Initialize the prompt manager.

        Args:
            default_lang: Default language for prompts
        """
        self.default_lang = default_lang
        self._prompts_cache: Dict[str, Dict[str, Any]] = {}
        self._load_prompts()

    def _load_prompts(self):
        """Load all available prompt templates."""
        # Import language-specific prompts
        from . import en_US

        self._prompts_cache["en_US"] = en_US.PROMPTS_EN_US

        # English-only prompts.

    def get(self, key: str, lang: Optional[str] = None, **kwargs) -> str:
        """
        Get a prompt template by key.

        Args:
            key: Prompt template key (e.g., 'evaluation.quality')
            lang: Language code (defaults to self.default_lang)
            **kwargs: Variables to substitute in the template

        Returns:
            Formatted prompt string

        Raises:
            KeyError: If prompt key not found
        """
        lang = lang or self.default_lang

        if lang not in self._prompts_cache:
            raise ValueError(f"Language '{lang}' not supported")

        # Navigate nested keys (e.g., 'evaluation.quality')
        keys = key.split(".")
        prompt_dict = self._prompts_cache[lang]

        for k in keys:
            if isinstance(prompt_dict, dict) and k in prompt_dict:
                prompt_dict = prompt_dict[k]
            else:
                raise KeyError(f"Prompt key '{key}' not found for language '{lang}'")

        # Handle string templates with variable substitution
        if isinstance(prompt_dict, str):
            return prompt_dict.format(**kwargs) if kwargs else prompt_dict

        return str(prompt_dict)

    def get_category(self, category: str, lang: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all prompts in a category.

        Args:
            category: Category name (e.g., 'evaluation', 'expert_roles')
            lang: Language code

        Returns:
            Dictionary of prompts in the category
        """
        lang = lang or self.default_lang

        if lang not in self._prompts_cache:
            raise ValueError(f"Language '{lang}' not supported")

        if category not in self._prompts_cache[lang]:
            raise KeyError(f"Category '{category}' not found for language '{lang}'")

        return self._prompts_cache[lang][category]

    def list_categories(self, lang: Optional[str] = None) -> list:
        """
        List all available prompt categories.

        Args:
            lang: Language code

        Returns:
            List of category names
        """
        lang = lang or self.default_lang

        if lang not in self._prompts_cache:
            return []

        return list(self._prompts_cache[lang].keys())

    def list_languages(self) -> list:
        """
        List all supported languages.

        Returns:
            List of language codes
        """
        return list(self._prompts_cache.keys())

    def set_default_language(self, lang: str):
        """
        Set the default language.

        Args:
            lang: Language code
        """
        if lang not in self._prompts_cache:
            raise ValueError(f"Language '{lang}' not supported")

        self.default_lang = lang

    def add_prompt(self, key: str, value: str, lang: Optional[str] = None):
        """
        Add or update a prompt template (useful for A/B testing).

        Args:
            key: Prompt key
            value: Prompt template
            lang: Language code
        """
        lang = lang or self.default_lang

        if lang not in self._prompts_cache:
            self._prompts_cache[lang] = {}

        # Handle nested keys
        keys = key.split(".")
        current = self._prompts_cache[lang]

        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]

        current[keys[-1]] = value

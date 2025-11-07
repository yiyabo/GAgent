from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError

from ...config.decomposer_config import DecomposerSettings, get_decomposer_settings
from ...llm import LLMClient
from .llm_service import LLMService

logger = logging.getLogger(__name__)


class DecompositionChild(BaseModel):
    """Single child node description returned by the decomposition LLM."""

    name: str
    instruction: str
    dependencies: list[int] = Field(default_factory=list)
    leaf: bool = False

    # Optional context payloads
    context_combined: Optional[str] = None
    context_sections: list[Dict[str, Any]] = Field(default_factory=list)
    context_meta: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "DecompositionChild":
        """Normalise payload that may contain nested `context` objects."""
        data = dict(payload)
        context = data.pop("context", None) or {}
        normalized_sections: list[Dict[str, Any]] = []
        if isinstance(context, dict):
            data.setdefault("context_combined", context.get("combined"))
            raw_sections = context.get("sections") or []
            if isinstance(raw_sections, list):
                for index, item in enumerate(raw_sections, start=1):
                    if isinstance(item, dict):
                        title = item.get("title")
                        content = item.get("content")
                        normalized_sections.append(
                            {
                                "title": str(title).strip()
                                if title is not None
                                else f"Section {index}",
                                "content": str(content).strip()
                                if content is not None
                                else "",
                            }
                        )
                    elif item is None:
                        continue
                    else:
                        normalized_sections.append(
                            {
                                "title": f"Section {index}",
                                "content": str(item).strip(),
                            }
                        )
            data.setdefault("context_sections", normalized_sections)
            raw_meta = context.get("meta")
            if isinstance(raw_meta, dict):
                data.setdefault("context_meta", raw_meta)
            else:
                data.setdefault("context_meta", {})
        else:
            data.setdefault("context_sections", normalized_sections)
            data.setdefault("context_meta", {})

        sections_value = data.get("context_sections", normalized_sections)
        normalised_section_list: list[Dict[str, Any]] = []
        if isinstance(sections_value, list):
            for index, item in enumerate(sections_value, start=1):
                if isinstance(item, dict):
                    title = item.get("title")
                    content = item.get("content")
                    normalised_section_list.append(
                        {
                            "title": str(title).strip()
                            if title is not None
                            else f"Section {index}",
                            "content": str(content).strip()
                            if content is not None
                            else "",
                        }
                    )
                elif item is None:
                    continue
                else:
                    normalised_section_list.append(
                        {"title": f"Section {index}", "content": str(item).strip()}
                    )
        data["context_sections"] = normalised_section_list

        if not isinstance(data.get("context_meta"), dict):
            data["context_meta"] = {}

        deps_raw = data.get("dependencies")
        if deps_raw is not None:
            if not isinstance(deps_raw, list):
                data["dependencies"] = []
            else:
                coerced: list[int] = []
                for value in deps_raw:
                    try:
                        coerced.append(int(value))
                    except (TypeError, ValueError):
                        continue
                data["dependencies"] = coerced
        return cls(**data)


class DecompositionResponse(BaseModel):
    """Top-level schema for the decomposition LLM response."""

    target_node_id: Optional[int]
    mode: str
    should_stop: bool = False
    reason: Optional[str] = None
    children_raw: list[Dict[str, Any]] = Field(default_factory=list)

    @property
    def children(self) -> list[DecompositionChild]:
        return [DecompositionChild.from_payload(item) for item in self.children_raw]

    @classmethod
    def model_validate_json(cls, json_data: str) -> "DecompositionResponse":
        try:
            parsed = json.loads(json_data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid decomposition JSON: {exc}") from exc
        if isinstance(parsed, dict) and "children" in parsed and "children_raw" not in parsed:
            parsed = dict(parsed)
            parsed["children_raw"] = parsed.pop("children") or []
        return super().model_validate(parsed)


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # drop opening fence
        while lines and lines[0].startswith("```"):
            lines.pop(0)
        # drop trailing fence
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        cleaned = "\n".join(lines).strip()
    return cleaned


class PlanDecomposerLLMService:
    """Wrapper around LLMService dedicated to task decomposition prompts."""

    def __init__(
        self,
        *,
        llm: Optional[LLMService] = None,
        settings: Optional[DecomposerSettings] = None,
    ) -> None:
        self._settings = settings or get_decomposer_settings()
        if llm is not None:
            self._llm = llm
        else:
            client: Optional[LLMClient] = None
            if any((self._settings.provider, self._settings.api_url, self._settings.api_key)):
                client = LLMClient(
                    provider=self._settings.provider,
                    api_key=self._settings.api_key,
                    url=self._settings.api_url,
                    model=self._settings.model,
                )
            self._llm = LLMService(client)

    def generate(self, prompt: str) -> DecompositionResponse:
        """Send prompt to LLM and parse the structured decomposition response."""
        response = self._llm.chat(
            prompt,
            model=self._settings.model,
        )
        cleaned = strip_code_fences(response)
        try:
            return DecompositionResponse.model_validate_json(cleaned)
        except ValidationError:
            logger.error("Failed to parse decomposition response: %s", cleaned)
            raise

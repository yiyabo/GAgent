"""Code generation helpers for analysis tasks."""

import json
import logging
from typing import Optional, List

from pydantic import BaseModel, Field

from .metadata import DatasetMetadata
from .prompts.coder_prompt import (
    CODER_SYSTEM_PROMPT,
    CODER_USER_PROMPT_TEMPLATE,
    CODER_FIX_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

_CODEGEN_REQUEST_TIMEOUT_SEC = 180
_CODEGEN_SERVICE_ATTEMPTS = 2
_CODEGEN_CLIENT_RETRIES = 0


class CodeTaskResponse(BaseModel):
    """Structured response returned by code generation prompts."""
    code: str = Field(..., description="Python code to execute")
    description: str = Field(..., description="Short description of generated code")
    has_visualization: bool = Field(default=False, description="Whether visualization output is expected")
    visualization_purpose: Optional[str] = Field(None, description="Intended purpose of visualization")
    visualization_analysis: Optional[str] = Field(None, description="Guidance for interpreting visualization output")

    @classmethod
    def parse_from_llm_output(cls, text: str) -> "CodeTaskResponse":
        """Helper to robustly parse JSON from LLM output that might contain Markdown."""
        cleaned_text = text.strip()

        # Strip markdown code blocks if present
        if cleaned_text.startswith("```"):
            lines = cleaned_text.splitlines()
            # remove first line (```json or ```)
            if lines:
                lines.pop(0)
            # remove last line if it is ```
            if lines and lines[-1].strip() == "```":
                lines.pop()
            cleaned_text = "\n".join(lines).strip()

        try:
            return cls.model_validate_json(cleaned_text)
        except Exception as e:
            logger.error(f"Failed to parse LLM JSON: {e}. Raw text: {text}")
            # Fallback: try to find JSON substring
            try:
                start = cleaned_text.find("{")
                end = cleaned_text.rfind("}")
                if start != -1 and end != -1:
                    json_str = cleaned_text[start:end + 1]
                    return cls.model_validate_json(json_str)
            except Exception:
                pass

            return cls(code="", description="Failed to parse LLM output")


class CodeGenerator:
    """Generate and repair Python analysis code with LLM prompts."""

    def __init__(self, llm_service=None, system_prompt: Optional[str] = None):
        """
        Args:
            llm_service: Optional LLM service implementation providing `chat()`.
            system_prompt: Optional override for the code-generation system prompt.
        """
        if llm_service:
            self.llm = llm_service
        else:
            from app.services.llm.llm_service import get_llm_service
            self.llm = get_llm_service()
        self.system_prompt = system_prompt or CODER_SYSTEM_PROMPT

    def _format_columns_for_metadata(self, metadata: DatasetMetadata) -> str:
        """Format a compact column summary for prompt context."""
        cols_summary = []
        cols = getattr(metadata, 'columns', [])
        for col in cols[:20]:  # Limit column context
            c_name = getattr(col, 'name', str(col))
            c_type = getattr(col, 'dtype', 'unknown')
            c_sample = getattr(col, 'sample_values', [])
            cols_summary.append(f"  - {c_name} ({c_type}): {c_sample}")

        cols_text = "\n".join(cols_summary)
        if len(cols) > 20:
            cols_text += f"\n  ... ({len(cols) - 20} more columns)"
        return cols_text

    def _format_datasets(self, metadata_list: List[DatasetMetadata]) -> str:
        """Format dataset metadata blocks for prompt input."""
        datasets_info = []
        for i, metadata in enumerate(metadata_list, 1):
            cols_text = self._format_columns_for_metadata(metadata)
            dataset_info = f"""### Dataset {i}: {metadata.filename}
- Format: {metadata.file_format}
- Total Rows: {metadata.total_rows}
- Total Columns: {metadata.total_columns}
- Columns:
{cols_text}"""
            datasets_info.append(dataset_info)
        return "\n\n".join(datasets_info)

    def _chat_with_codegen_budget(self, prompt: str) -> str:
        kwargs = {
            "timeout": _CODEGEN_REQUEST_TIMEOUT_SEC,
            "retry_attempts": _CODEGEN_SERVICE_ATTEMPTS,
            "retries": _CODEGEN_CLIENT_RETRIES,
        }
        try:
            return self.llm.chat(prompt=prompt, **kwargs)
        except TypeError:
            return self.llm.chat(prompt=prompt)

    def generate(
        self,
        metadata_list: List[DatasetMetadata],
        task_title: str,
        task_description: str
    ) -> CodeTaskResponse:
        """
        Generate analysis code.

        Args:
            metadata_list: Dataset metadata list.
            task_title: Task title.
            task_description: Task description.

        Returns:
            Parsed code generation result.
        """
        if isinstance(metadata_list, DatasetMetadata):
            metadata_list = [metadata_list]

        datasets_text = self._format_datasets(metadata_list)

        user_prompt = CODER_USER_PROMPT_TEMPLATE.format(
            datasets_info=datasets_text,
            task_title=task_title,
            task_description=task_description
        )

        full_prompt = f"{self.system_prompt}\n\n{user_prompt}"

        response_text = self._chat_with_codegen_budget(full_prompt)
        return CodeTaskResponse.parse_from_llm_output(response_text)

    def fix_code(
        self,
        metadata_list: List[DatasetMetadata],
        task_title: str,
        task_description: str,
        code: str,
        error: str,
        max_retries: int = 5
    ) -> CodeTaskResponse:
        """
        Repair previously generated code using execution error feedback.

        Args:
            metadata_list: Dataset metadata list.
            task_title: Task title.
            task_description: Task description.
            code: Previous code.
            error: Execution error message.
            max_retries: Maximum retry attempts.

        Returns:
            Parsed code repair result.
        """
        if isinstance(metadata_list, DatasetMetadata):
            metadata_list = [metadata_list]

        datasets_text = self._format_datasets(metadata_list)
        current_code = code
        current_error = error

        for attempt in range(1, max_retries + 1):
            logger.info("Code repair attempt %s/%s", attempt, max_retries)

            user_prompt = CODER_FIX_PROMPT_TEMPLATE.format(
                datasets_info=datasets_text,
                task_title=task_title,
                task_description=task_description,
                code=current_code,
                error=current_error
            )

            full_prompt = f"{self.system_prompt}\n\n{user_prompt}"

            try:
                response_text = self._chat_with_codegen_budget(full_prompt)
                result = CodeTaskResponse.parse_from_llm_output(response_text)

                if result.code and result.code.strip():
                    logger.info("Code repair succeeded on attempt %s/%s", attempt, max_retries)
                    return result

            except Exception as e:
                logger.warning("Code repair attempt %s failed: %s", attempt, e)
                current_error = f"{error}\n\n {attempt} failed: {e}"

        logger.error("Code repair failed after %s attempts", max_retries)
        return CodeTaskResponse(code=code, description=f"Code repair failed after {max_retries} attempts")

    def generate_visualization(
        self,
        metadata_list: List[DatasetMetadata],
        task_title: str,
        task_description: str
    ) -> CodeTaskResponse:
        """
        Generate visualization-oriented code.

        Args:
            metadata_list: Dataset metadata list.
            task_title: Task title.
            task_description: Task description.

        Returns:
            Parsed code generation result.
        """
        return self.generate(metadata_list, task_title, task_description)

    def fix_visualization_code(
        self,
        metadata_list: List[DatasetMetadata],
        task_title: str,
        task_description: str,
        code: str,
        error: str,
        max_retries: int = 3
    ) -> CodeTaskResponse:
        """
        Repair visualization-oriented code.

        Args:
            metadata_list: Dataset metadata list.
            task_title: Task title.
            task_description: Task description.
            code: Previous code.
            error: Execution error message.
            max_retries: Maximum retry attempts.

        Returns:
            Parsed code repair result.
        """
        return self.fix_code(metadata_list, task_title, task_description, code, error, max_retries)

"""
Unified LLM Service

This module provides a centralized LLM service that eliminates duplicate code
across different executors and provides consistent error handling and retry logic.
"""

import asyncio
import functools
import json
import logging
import time
from typing import Any, Dict, List, Optional, Union

from ...llm import get_default_client
from ...interfaces import LLMProvider
from app.services.foundation.settings import get_settings

logger = logging.getLogger(__name__)


class LLMService:
    """Unified service for all LLM interactions with consistent error handling"""
    
    def __init__(self, client: Optional[LLMProvider] = None):
        """
        Initialize the LLM service
        
        Args:
            client: Optional LLM client, defaults to system default
        """
        self.client = client or get_default_client()
        # Centralize retry/backoff from settings for consistency with LLM client
        try:
            s = get_settings()
            retries = int(getattr(s, "llm_retries", 2))
            self._retry_attempts = max(1, retries + 1)  # attempts = first try + retries
            self._retry_delay = float(getattr(s, "llm_backoff_base", 0.5))
        except Exception:
            self._retry_attempts = 3
            self._retry_delay = 1.0  # seconds
        
    def chat(self, prompt: str, **kwargs) -> str:
        """
        Execute synchronous chat with robust error handling and retry logic
        
        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters for the LLM
            
        Returns:
            str: The LLM response content
            
        Raises:
            RuntimeError: If all retry attempts fail
        """
        for attempt in range(self._retry_attempts):
            try:
                return self._execute_chat(prompt, **kwargs)
            except Exception as e:
                logger.warning(f"LLM chat attempt {attempt + 1} failed: {e}")
                if attempt < self._retry_attempts - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                else:
                    logger.error(f"All LLM chat attempts failed for prompt: {prompt[:100]}...")
                    raise RuntimeError(f"LLM chat failed after {self._retry_attempts} attempts: {e}")
        
        # This should never be reached
        raise RuntimeError("Unexpected error in LLM chat")
    
    async def chat_async(self, prompt: str, **kwargs) -> str:
        """
        Execute asynchronous chat with robust error handling and retry logic
        
        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters for the LLM
            
        Returns:
            str: The LLM response content
            
        Raises:
            RuntimeError: If all retry attempts fail
        """
        for attempt in range(self._retry_attempts):
            try:
                return await self._execute_chat_async(prompt, **kwargs)
            except Exception as e:
                logger.warning(f"Async LLM chat attempt {attempt + 1} failed: {e}")
                if attempt < self._retry_attempts - 1:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                else:
                    logger.error(f"All async LLM chat attempts failed for prompt: {prompt[:100]}...")
                    raise RuntimeError(f"Async LLM chat failed after {self._retry_attempts} attempts: {e}")
        
        # This should never be reached
        raise RuntimeError("Unexpected error in async LLM chat")
    
    def _execute_chat(self, prompt: str, **kwargs) -> str:
        """
        Internal method to execute chat with unified response extraction
        
        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters for the LLM
            
        Returns:
            str: Extracted text content from LLM response
        """
        try:
            # Try modern API style: client.chat.completions.create(messages=[...])
            chat_obj = getattr(self.client, "chat", None)
            if chat_obj is not None:
                completions_obj = getattr(chat_obj, "completions", None)
                create_fn = getattr(completions_obj, "create", None) if completions_obj is not None else None
                if callable(create_fn):
                    messages = kwargs.get("messages", [{"role": "user", "content": prompt}])
                    resp = create_fn(messages=messages, **{k: v for k, v in kwargs.items() if k != "messages"})
                    return self._extract_content(resp)
            
            # Legacy/simple API: client.chat(prompt) -> str or response-like
            resp = self.client.chat(prompt, **kwargs)
            if isinstance(resp, str):
                return resp
            return self._extract_content(resp)
            
        except Exception as e:
            logger.error(f"LLM chat execution failed: {e}")
            raise
    
    async def _execute_chat_async(self, prompt: str, **kwargs) -> str:
        """
        Internal async method to execute chat with unified response extraction
        
        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters for the LLM
            
        Returns:
            str: Extracted text content from LLM response
        """
        try:
            # Check if client has async support
            if hasattr(self.client, "chat_async"):
                resp = await self.client.chat_async(prompt, **kwargs)
                if isinstance(resp, str):
                    return resp
                return self._extract_content(resp)
            
            # Fallback to sync execution in thread pool
            loop = asyncio.get_event_loop()
            bound = functools.partial(self._execute_chat, prompt, **kwargs)
            return await loop.run_in_executor(None, bound)
            
        except Exception as e:
            logger.error(f"Async LLM chat execution failed: {e}")
            raise
    
    def _extract_content(self, response: Any) -> str:
        """
        Extract text content from various response formats
        
        Args:
            response: The LLM response object
            
        Returns:
            str: Extracted text content
        """
        # Try to extract from OpenAI-style response
        try:
            if hasattr(response, "choices") and response.choices:
                choice = response.choices[0]
                if hasattr(choice, "message") and hasattr(choice.message, "content"):
                    return str(choice.message.content)
                if hasattr(choice, "text"):
                    return str(choice.text)
        except (AttributeError, IndexError, TypeError):
            pass
        
        # Try direct content attribute
        if hasattr(response, "content"):
            return str(response.content)
        
        # Try to extract from dict response
        if isinstance(response, dict):
            if "content" in response:
                return str(response["content"])
            if "choices" in response and response["choices"]:
                choice = response["choices"][0]
                if isinstance(choice, dict):
                    if "message" in choice and "content" in choice["message"]:
                        return str(choice["message"]["content"])
                    if "text" in choice:
                        return str(choice["text"])
        
        # Fallback to string representation
        return str(response)
    
    def parse_json_response(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Parse JSON from LLM response with robust extraction
        
        Args:
            content: Raw LLM response content
            
        Returns:
            Parsed JSON object or None if parsing fails
        """
        try:
            # Clean markdown code fences
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            # Try to parse JSON
            return json.loads(content)
            
        except json.JSONDecodeError as e:
            # Try to extract JSON from mixed content
            try:
                # Find JSON-like structure
                import re
                json_pattern = r'\{[^{}]*\}'
                matches = re.findall(json_pattern, content, re.DOTALL)
                if matches:
                    # Try to parse the largest match
                    for match in sorted(matches, key=len, reverse=True):
                        try:
                            return json.loads(match)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass
            
            logger.warning(f"Failed to parse JSON from LLM response: {e}")
            return None
    
    async def batch_chat_async(self, prompts: List[str], max_concurrent: int = 3) -> List[str]:
        """
        Execute multiple chat requests concurrently with rate limiting
        
        Args:
            prompts: List of prompts to process
            max_concurrent: Maximum number of concurrent requests
            
        Returns:
            List of response contents in the same order as prompts
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def limited_chat(prompt: str) -> str:
            async with semaphore:
                return await self.chat_async(prompt)
        
        tasks = [limited_chat(prompt) for prompt in prompts]
        return await asyncio.gather(*tasks)


class TaskPromptBuilder:
    """Builder for consistent task prompts across the system"""
    
    @staticmethod
    def build_initial_prompt(task_name: str, context: Optional[str] = None, **kwargs) -> str:
        """
        Build initial task execution prompt
        
        Args:
            task_name: Name of the task
            context: Optional context information
            **kwargs: Additional parameters
            
        Returns:
            Formatted prompt string
        """
        prompt_parts = [
            f"Write a comprehensive section that fulfills the following task:",
            f"Task: {task_name}",
        ]
        
        if context:
            prompt_parts.append(f"\nContext:\n{context}")
        
        # Add optional parameters
        length = kwargs.get("length", "~200 words")
        tone = kwargs.get("tone", "neutral, professional")
        
        prompt_parts.extend([
            f"\nLength: {length}",
            f"Tone: {tone}",
            "\nProvide clear, actionable content with proper structure."
        ])
        
        return "\n".join(prompt_parts)
    
    @staticmethod
    def build_revision_prompt(
        task_name: str, 
        current_content: str, 
        feedback: Optional[List[str]] = None,
        **kwargs
    ) -> str:
        """
        Build revision prompt for content improvement
        
        Args:
            task_name: Name of the task
            current_content: Current content to revise
            feedback: Optional feedback/suggestions
            **kwargs: Additional parameters
            
        Returns:
            Formatted revision prompt
        """
        prompt_parts = [
            f"Please improve the following content for the task: {task_name}",
            f"\nCurrent Content:\n{current_content}",
        ]
        
        if feedback:
            prompt_parts.append(f"\nFeedback to address:")
            for item in feedback:
                prompt_parts.append(f"- {item}")
        
        prompt_parts.append("\nProvide an improved version that is more comprehensive, accurate, and well-structured.")
        
        return "\n".join(prompt_parts)
    
    @staticmethod
    def build_evaluation_prompt(content: str, task_name: str, **kwargs) -> str:
        """
        Build evaluation prompt for content quality assessment
        
        Args:
            content: Content to evaluate
            task_name: Name of the task
            **kwargs: Additional parameters
            
        Returns:
            Formatted evaluation prompt
        """
        dimensions = kwargs.get("dimensions", [
            "relevance", "completeness", "accuracy", 
            "clarity", "coherence", "scientific_rigor"
        ])
        
        prompt = f"""
        Evaluate the following content for the task "{task_name}":
        
        Content:
        {content}
        
        Please evaluate on these dimensions (0.0-1.0 scale):
        {', '.join(dimensions)}
        
        Return a JSON object with:
        {{
            "overall_score": float,
            "dimensions": {{{', '.join([f'"{d}": float' for d in dimensions])}}},
            "suggestions": ["improvement 1", "improvement 2", ...],
            "needs_revision": boolean
        }}
        """
        
        return prompt.strip()


# Global service instance
_llm_service: Optional[LLMService] = None


def get_llm_service(client: Optional[LLMProvider] = None) -> LLMService:
    """
    Get or create the global LLM service instance
    
    Args:
        client: Optional LLM client to use
        
    Returns:
        LLMService instance
    """
    global _llm_service
    if _llm_service is None or client is not None:
        _llm_service = LLMService(client)
    return _llm_service


# Async context manager for batch operations
class AsyncLLMContext:
    """Context manager for async LLM operations with proper cleanup"""
    
    def __init__(self, service: Optional[LLMService] = None):
        self.service = service or get_llm_service()
        
    async def __aenter__(self):
        return self.service
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Clean up any pending operations if needed
        pass

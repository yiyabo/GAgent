#!/usr/bin/env python
"""
A-mem (Agentic Memory) Client Service

Provides integration with the A-mem service for:
- querying historical execution experiences
- saving new execution outcomes
- accumulating Claude Code execution knowledge
"""

import logging
from typing import List, Dict, Optional, Any
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)


class AMemClient:
    """Client for the A-mem memory service via HTTP API."""
    
    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        timeout: float = 10.0,
        enabled: bool = True
    ):
        """Initialize the A-mem client.

        Args:
            base_url: Base URL of the A-mem API.
            timeout: Request timeout in seconds.
            enabled: Whether A-mem integration is enabled.
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.enabled = enabled
        self._client: Optional[httpx.AsyncClient] = None
        
        if self.enabled:
            logger.info(f"A-mem client initialized: {self.base_url}")
        else:
            logger.info("A-mem client disabled")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or lazily create the HTTP client."""
        if self._client is None:
            # Use explicit transport settings to reduce connection instability.
            transport = httpx.AsyncHTTPTransport(retries=0)
            self._client = httpx.AsyncClient(
                transport=transport,
                timeout=self.timeout,
                http2=False,  # Force HTTP/1.1 for compatibility.
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._client
    
    async def close(self):
        """Close and release the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
    
    async def health_check(self) -> bool:
        """Check whether the A-mem service is healthy."""
        if not self.enabled:
            return False
        
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"A-mem health check failed: {e}")
            return False
    
    async def query_experiences(
        self,
        query: str,
        top_k: int = 3,
        context_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Query semantically similar execution experiences."""
        if not self.enabled:
            return []
        
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/query_memory",
                json={
                    "query": query,
                    "top_k": min(top_k, 10)  # Cap returned results.
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                logger.info(f"A-mem query returned {len(results)} experiences for: {query[:50]}...")
                return results
            else:
                logger.warning(f"A-mem query failed with status {response.status_code}")
                return []
                
        except Exception as e:
            logger.warning(f"Failed to query A-mem: {e}")
            return []
    
    async def save_execution(
        self,
        task: str,
        result: Dict[str, Any],
        session_id: Optional[str] = None,
        plan_id: Optional[int] = None,
        **metadata
    ) -> Optional[str]:
        """Persist execution output to A-mem."""
        if not self.enabled:
            return None
        
        try:
            # Build structured memory content.
            content = self._format_execution_memory(task, result, session_id, plan_id, metadata)
            
            # Base tags.
            tags = ["code_executor", "execution"]
            if result.get("success"):
                tags.append("success")
            else:
                tags.append("failure")
            
            # Merge custom tags.
            if "tags" in metadata:
                tags.extend(metadata["tags"])
            
            # Build memory context.
            context = metadata.get("context", "Code execution experience")
            
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/add_memory",
                json={
                    "content": content,
                    "tags": tags,
                    "context": context,
                    "timestamp": datetime.now().strftime("%Y%m%d%H%M")
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                memory_id = data.get("memory_id")
                logger.info(f"Saved execution to A-mem: {memory_id}")
                return memory_id
            else:
                logger.warning(f"Failed to save to A-mem: status {response.status_code}")
                return None
                
        except Exception as e:
            logger.warning(f"Failed to save execution to A-mem: {e}")
            return None
    
    def _format_execution_memory(
        self,
        task: str,
        result: Dict[str, Any],
        session_id: Optional[str],
        plan_id: Optional[int],
        metadata: Dict[str, Any]
    ) -> str:
        """Format execution memory content into a structured record."""
        lines = [
            "# Claude Code Execution Record",
            "",
            "## Task Description",
            task,
            "",
            "## Execution Result",
            f"Status: {'✅ Success' if result.get('success') else '❌ Failed'}",
        ]
        
        # Attach working-directory metadata.
        if "working_directory" in result:
            lines.append(f"Working directory: {result['working_directory']}")
        
        if "task_directory" in result:
            lines.append(f"Task directory: {result['task_directory']}")
        
        # Attach output streams.
        if result.get("stdout"):
            stdout = result["stdout"]
            if len(stdout) > 500:
                stdout = stdout[:500] + "...(truncated)"
            lines.extend([
                "",
                "## Standard Output",
                stdout
            ])
        
        # Attach error details.
        if result.get("error"):
            lines.extend([
                "",
                "## Error Message",
                str(result["error"])
            ])
        
        if result.get("stderr"):
            stderr = result["stderr"]
            if len(stderr) > 300:
                stderr = stderr[:300] + "...(truncated)"
            lines.extend([
                "",
                "## Standard Error",
                stderr
            ])
        
        # Attach metadata.
        if session_id:
            lines.append(f"\nSession ID: {session_id}")
        if plan_id:
            lines.append(f"Plan ID: {plan_id}")
        
        # Attach key findings.
        if "key_findings" in metadata:
            lines.extend([
                "",
                "## Key Findings",
                metadata["key_findings"]
            ])
        
        return "\n".join(lines)
    
    def format_experiences_for_llm(self, experiences: List[Dict[str, Any]]) -> str:
        """Format historical experiences for LLM reference.
        
        Args:
            experiences: Experience entries returned by A-mem.
        
        Returns:
            Formatted context text for LLM prompts.
        """
        if not experiences:
            return ""
        
        lines = ["Relevant historical execution experiences for reference:", ""]
        
        for i, exp in enumerate(experiences, 1):
            lines.append(f"### Experience {i} (similarity: {exp.get('score', 0):.2f})")
            lines.append(exp.get("content", ""))
            
            # Append keywords and tags.
            keywords = exp.get("keywords", [])
            tags = exp.get("tags", [])
            if keywords:
                lines.append(f"\nKeywords: {', '.join(keywords)}")
            if tags:
                lines.append(f"Tags: {', '.join(tags)}")
            
            lines.append("\n---\n")
        
        return "\n".join(lines)


# Global singleton A-mem client instance.
_amem_client: Optional[AMemClient] = None


def get_amem_client() -> AMemClient:
    """Get the global singleton A-mem client instance."""
    global _amem_client
    
    if _amem_client is None:
        # Read configuration.
        from app.services.foundation.settings import get_settings
        settings = get_settings()
        
        # Resolve A-mem settings.
        amem_enabled = getattr(settings, "amem_enabled", False)
        amem_url = getattr(settings, "amem_url", "http://localhost:8001")
        
        _amem_client = AMemClient(
            base_url=amem_url,
            enabled=amem_enabled
        )
    
    return _amem_client


async def close_amem_client():
    """Close and clear the global A-mem client."""
    global _amem_client
    if _amem_client is not None:
        await _amem_client.close()
        _amem_client = None

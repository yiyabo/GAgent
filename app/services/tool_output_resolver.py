"""Centralized output directory resolver for all tools.

This module provides a single source of truth for resolving where tools
should write their outputs, replacing 6+ duplicate ``_resolve_output_dir()``
implementations scattered across ``tool_box/tools_impl/``.

Resolution priority:
1. Explicit directory (if provided and valid)
2. PathRouter task output (if session + task available)
3. Tool-specific subdirectory under session tool_outputs (backward compat)
4. PathRouter tmp output (session only, no tool_name)
5. Project-level fallback (legacy, to be deprecated)

Usage::

    from app.services.tool_output_resolver import ToolOutputResolver
    from tool_box.context import ToolContext

    resolver = ToolOutputResolver()

    # With ToolContext (preferred)
    async def my_tool_handler(query: str, tool_context: ToolContext | None = None):
        output_dir = resolver.resolve(
            tool_context=tool_context,
            tool_name="my_tool",
        )
        # output_dir is now guaranteed to be a valid, created directory

    # With explicit parameters (for testing or legacy code)
    output_dir = resolver.resolve(
        session_id="session_abc",
        task_id=42,
        tool_name="sequence_fetch",
    )
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from tool_box.context import ToolContext

logger = logging.getLogger(__name__)


class ToolOutputResolver:
    """Resolve output directories for tools with consistent priority logic.

    This class centralizes the path resolution logic that was previously
    duplicated across multiple tool implementations. It ensures all tools
    write to session-scoped directories under ``runtime/session_X/`` rather
    than polluting project-level ``results/`` or ``output/``.
    """

    def __init__(self) -> None:
        self._path_router: Optional[object] = None

    def _get_path_router(self) -> object:
        """Lazy-load PathRouter to avoid circular imports."""
        if self._path_router is None:
            from app.services.path_router import get_path_router
            self._path_router = get_path_router()
        return self._path_router

    def resolve(
        self,
        *,
        explicit_dir: Optional[str] = None,
        tool_context: Optional[ToolContext] = None,
        session_id: Optional[str] = None,
        task_id: Optional[int] = None,
        ancestor_chain: Optional[List[int]] = None,
        tool_name: Optional[str] = None,
        create: bool = True,
    ) -> Path:
        """Resolve output directory with consistent priority.

        Args:
            explicit_dir: Caller-provided directory (highest priority).
            tool_context: ToolContext from orchestrator (extracts session/task).
            session_id: Session identifier (if not using tool_context).
            task_id: Task ID (if not using tool_context).
            ancestor_chain: PlanTree ancestor chain for hierarchical paths.
            tool_name: Tool name for creating tool-specific subdirectories.
            create: Create the directory if it doesn't exist.

        Returns:
            Resolved absolute Path to output directory.

        Raises:
            ValueError: If no valid resolution path is available.
        """
        # Extract from ToolContext if provided
        if tool_context is not None:
            session_id = session_id or tool_context.session_id
            task_id = task_id if task_id is not None else tool_context.task_id

        # Priority 1: Explicit directory
        if explicit_dir:
            path = Path(explicit_dir).expanduser()
            if not path.is_absolute():
                # Resolve relative to project root
                project_root = Path(__file__).resolve().parents[2]
                path = (project_root / path).resolve()
            if create:
                path.mkdir(parents=True, exist_ok=True)
            return path

        # Priority 1.5: ToolContext.work_dir (if provided by orchestrator)
        if tool_context is not None:
            work_dir = str(getattr(tool_context, "work_dir", "") or "").strip()
            if work_dir:
                path = Path(work_dir).expanduser().resolve(strict=False)
                if create:
                    path.mkdir(parents=True, exist_ok=True)
                return path

        router = self._get_path_router()

        # Priority 2: Task-scoped output (if session + task available)
        if session_id and task_id is not None:
            try:
                task_dir = router.get_task_output_dir(
                    session_id,
                    task_id,
                    ancestor_chain,
                    create=create,
                )
                if tool_name:
                    tool_dir = task_dir / tool_name
                    if create:
                        tool_dir.mkdir(parents=True, exist_ok=True)
                    return tool_dir
                return task_dir
            except Exception as exc:
                logger.warning(
                    "Failed to resolve task output dir (session=%s, task=%s): %s",
                    session_id, task_id, exc,
                )

        # Priority 3: Tool-specific subdirectory under session tool_outputs (backward compat)
        if session_id and tool_name:
            try:
                from app.services.session_paths import get_session_tool_outputs_dir
                tool_outputs_root = get_session_tool_outputs_dir(session_id, create=create)
                tool_dir = tool_outputs_root / tool_name
                if create:
                    tool_dir.mkdir(parents=True, exist_ok=True)
                return tool_dir
            except Exception as exc:
                logger.warning(
                    "Failed to resolve tool_outputs dir (session=%s, tool=%s): %s",
                    session_id, tool_name, exc,
                )

        # Priority 4: Session-scoped tmp output (if session only, no tool_name)
        if session_id:
            try:
                tmp_dir = router.get_tmp_output_dir(session_id, create=create)
                if tool_name:
                    tool_dir = tmp_dir / tool_name
                    if create:
                        tool_dir.mkdir(parents=True, exist_ok=True)
                    return tool_dir
                return tmp_dir
            except Exception as exc:
                logger.warning(
                    "Failed to resolve tmp output dir (session=%s): %s",
                    session_id, exc,
                )

        # Priority 5: Project-level fallback (legacy, to be deprecated)
        # This is the problematic path we're trying to eliminate
        project_root = Path(__file__).resolve().parents[2]
        if tool_name:
            fallback = (project_root / "runtime" / tool_name).resolve()
        else:
            fallback = (project_root / "runtime" / "tool_outputs").resolve()

        logger.warning(
            "Using project-level fallback directory %s. "
            "This indicates missing session_id or task_id. "
            "Please ensure ToolContext is properly injected.",
            fallback,
        )
        if create:
            fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def resolve_for_tool(
        self,
        tool_name: str,
        *,
        tool_context: Optional[ToolContext] = None,
        explicit_dir: Optional[str] = None,
    ) -> Path:
        """Convenience method for tools to resolve their output directory.

        This is the primary entry point for tool implementations.

        Args:
            tool_name: Name of the tool (used for subdirectory).
            tool_context: ToolContext from orchestrator.
            explicit_dir: Caller-provided directory override.

        Returns:
            Resolved absolute Path to tool's output directory.

        Example::

            async def sequence_fetch_handler(
                accessions: List[str],
                tool_context: ToolContext | None = None,
            ):
                resolver = ToolOutputResolver()
                output_dir = resolver.resolve_for_tool(
                    "sequence_fetch",
                    tool_context=tool_context,
                )
                # Write files to output_dir
        """
        return self.resolve(
            explicit_dir=explicit_dir,
            tool_context=tool_context,
            tool_name=tool_name,
        )


_default_resolver: Optional[ToolOutputResolver] = None


def get_tool_output_resolver() -> ToolOutputResolver:
    """Get the global ToolOutputResolver instance."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = ToolOutputResolver()
    return _default_resolver


__all__ = ["ToolOutputResolver", "get_tool_output_resolver"]

"""Unified path routing for all tool outputs.

This module provides a centralized PathRouter that generates hierarchical
output paths under ``raw_files/`` mirroring the PlanTree's parent-child
structure.  All tools should use PathRouter instead of computing paths
independently.

Directory layout::

    runtime/session_{normalized_id}/
    ├── raw_files/
    │   ├── task_{root_id}/
    │   │   ├── task_{child_id}/        # leaf → result files here
    │   │   └── task_{child_id}/
    │   │       └── task_{grandchild}/   # deeper nesting possible
    │   └── tmp/
    │       └── {run_id}/
    ├── deliverables/
    │   └── latest/
    └── ...
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from app.services.session_paths import get_runtime_root, normalize_session_base

if TYPE_CHECKING:
    from app.services.plans.plan_models import PlanTree

logger = logging.getLogger(__name__)

_MAX_ANCESTOR_DEPTH = 50  # Safety limit to detect circular parent chains


@dataclass
class PathRouterConfig:
    """Configuration for PathRouter behaviour."""

    runtime_root: Path = field(default_factory=get_runtime_root)
    legacy_fallback: bool = True
    warn_on_legacy: bool = True
    warn_on_override: bool = True


class PathRouter:
    """Centralized path resolution for all tool outputs.

    Builds hierarchical paths under ``raw_files/`` that mirror the PlanTree's
    parent-child structure.  The ``ancestor_chain`` parameter determines the
    nesting depth.
    """

    def __init__(self, config: Optional[PathRouterConfig] = None) -> None:
        self._config = config or PathRouterConfig()

    # ------------------------------------------------------------------
    # Core path resolution
    # ------------------------------------------------------------------

    def get_task_output_dir(
        self,
        session_id: str,
        task_id: int,
        ancestor_chain: Optional[List[int]] = None,
        *,
        create: bool = True,
    ) -> Path:
        """Return absolute hierarchical path for a task's output.

        Args:
            session_id: Session identifier (will be normalized).
            task_id: Target task ID.
            ancestor_chain: Ordered list of ancestor IDs from root to
                immediate parent (not including *task_id* itself).
                ``[4, 7]`` means grandparent=4, parent=7.
                If ``None`` or empty, returns ``raw_files/task_{task_id}/``.
            create: Create the directory on disk if it doesn't exist.

        Returns:
            Absolute path, e.g.
            ``runtime/session_abc/raw_files/task_4/task_7/task_9/``

        Raises:
            ValueError: If *session_id* is empty/invalid or *task_id* is None.
        """
        if task_id is None:
            raise ValueError("task_id is required for plan execution")

        raw_root = self.get_raw_files_root(session_id, create=False)

        # Build hierarchical path segments
        segments: List[str] = []
        if ancestor_chain:
            for ancestor_id in ancestor_chain:
                segments.append(f"task_{ancestor_id}")
        segments.append(f"task_{task_id}")

        output_dir = raw_root
        for seg in segments:
            output_dir = output_dir / seg

        if create:
            output_dir.mkdir(parents=True, exist_ok=True)

        return output_dir.resolve()

    def get_tmp_output_dir(
        self,
        session_id: str,
        *,
        create: bool = True,
        run_id: Optional[str] = None,
    ) -> Path:
        """Return absolute path for temporary (non-plan) outputs.

        Returns:
            ``runtime/session_{norm}/raw_files/tmp/`` or
            ``runtime/session_{norm}/raw_files/tmp/{run_id}/``
        """
        raw_root = self.get_raw_files_root(session_id, create=False)
        tmp_dir = raw_root / "tmp"
        if run_id:
            tmp_dir = tmp_dir / run_id
        if create:
            tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir.resolve()

    def get_deliverables_dir(
        self,
        session_id: str,
        *,
        create: bool = True,
    ) -> Path:
        """Return absolute path for deliverables.

        Returns:
            ``runtime/session_{norm}/deliverables/latest/``
        """
        session_dir = self._get_session_dir(session_id, create=False)
        deliverables_dir = session_dir / "deliverables" / "latest"
        if create:
            deliverables_dir.mkdir(parents=True, exist_ok=True)
        return deliverables_dir.resolve()

    def get_raw_files_root(
        self,
        session_id: str,
        *,
        create: bool = False,
    ) -> Path:
        """Return absolute path to the raw_files root.

        Returns:
            ``runtime/session_{norm}/raw_files/``
        """
        session_dir = self._get_session_dir(session_id, create=False)
        raw_root = session_dir / "raw_files"
        if create:
            raw_root.mkdir(parents=True, exist_ok=True)
        return raw_root.resolve()

    # ------------------------------------------------------------------
    # Legacy fallback resolution
    # ------------------------------------------------------------------

    # Regex patterns for legacy path formats
    _RE_PLAN_TASK = re.compile(
        r"plan(\d+)_task(\d+)/run_[^/]+/results?/(.*)"
    )
    _RE_LIT_PIPELINE = re.compile(
        r"tool_outputs/literature_pipeline/review_pack_(\d{8}_\d{6}[^/]*)/(.*)"
    )
    _RE_MANUSCRIPT = re.compile(
        r"\.manuscript_writer_(\d{8}_\d{6}[^/]*)/(.*)"
    )
    _RE_FLAT_RAW = re.compile(
        r"raw_files/task_(\d+)/(.*)"
    )

    def resolve_with_fallback(
        self,
        session_id: str,
        relative_path: str,
        task_id: Optional[int] = None,
        ancestor_chain: Optional[List[int]] = None,
    ) -> Optional[Path]:
        """Resolve a path checking unified hierarchical location first, then legacy.

        Resolution order:
        1. Hierarchical path: ``raw_files/task_{a1}/.../task_{id}/<relative_path>``
        2. Flat legacy path: ``raw_files/task_{id}/<relative_path>``
        3. Old legacy paths: ``plan{X}_task{Y}/...``, ``tool_outputs/...``, etc.

        Returns:
            Resolved absolute path if found, else ``None``.
        """
        if not self._config.legacy_fallback:
            # Only check hierarchical path
            if task_id is not None:
                candidate = self.get_task_output_dir(
                    session_id, task_id, ancestor_chain, create=False
                ) / relative_path
                if candidate.exists():
                    return candidate
            return None

        session_dir = self._get_session_dir(session_id, create=False)

        # 1. Check hierarchical path
        if task_id is not None:
            hierarchical = self.get_task_output_dir(
                session_id, task_id, ancestor_chain, create=False
            ) / relative_path
            if hierarchical.exists():
                return hierarchical

        # 2. Check flat raw_files/task_{id}/ (old unified format)
        if task_id is not None:
            flat_path = (
                self.get_raw_files_root(session_id, create=False)
                / f"task_{task_id}"
                / relative_path
            )
            if flat_path.exists():
                if self._config.warn_on_legacy:
                    logger.warning(
                        "File found at flat legacy path %s; "
                        "expected hierarchical path. Please migrate.",
                        flat_path,
                    )
                return flat_path

        # 3. Check older legacy paths
        legacy_candidates = [
            session_dir / "tool_outputs" / "literature_pipeline" / relative_path,
            session_dir / relative_path,
        ]
        if task_id is not None:
            # Try plan{X}_task{Y} patterns (scan for matching dirs)
            for item in session_dir.iterdir() if session_dir.exists() else []:
                if item.is_dir() and item.name.startswith("plan"):
                    candidate = item / relative_path
                    if candidate.exists():
                        legacy_candidates.insert(0, candidate)
                        break

        for candidate in legacy_candidates:
            if candidate.exists():
                if self._config.warn_on_legacy:
                    logger.warning(
                        "File found at legacy path %s; "
                        "expected unified path. Please migrate.",
                        candidate,
                    )
                return candidate

        return None

    def map_legacy_path(
        self,
        legacy_path: str,
        session_id: str,
    ) -> Optional[Path]:
        """Map a legacy path pattern to its unified equivalent.

        Without ancestor_chain info, maps to flat ``raw_files/task_{Y}/`` path.
        Full hierarchical resolution requires :meth:`resolve_with_fallback`.

        Examples:
            - ``plan1_task2/run_123/results/foo.csv`` → ``raw_files/task_2/foo.csv``
            - ``tool_outputs/literature_pipeline/review_pack_20250101_120000/lib.jsonl``
              → ``raw_files/tmp/20250101_120000/lib.jsonl``
            - ``raw_files/task_5/data.csv`` → ``raw_files/task_5/data.csv`` (unchanged)

        Returns:
            Mapped path (absolute) or ``None`` if pattern not recognized.
        """
        raw_root = self.get_raw_files_root(session_id, create=False)

        # Pattern: plan{X}_task{Y}/run_<ts>/results/<file>
        m = self._RE_PLAN_TASK.search(legacy_path)
        if m:
            task_num = int(m.group(2))
            remainder = m.group(3)
            return raw_root / f"task_{task_num}" / remainder

        # Pattern: tool_outputs/literature_pipeline/review_pack_<ts>/<file>
        m = self._RE_LIT_PIPELINE.search(legacy_path)
        if m:
            ts = m.group(1)
            remainder = m.group(2)
            return raw_root / "tmp" / ts / remainder

        # Pattern: .manuscript_writer_<ts>/<file>
        m = self._RE_MANUSCRIPT.search(legacy_path)
        if m:
            # Without task context, map to tmp
            ts = m.group(1)
            remainder = m.group(2)
            return raw_root / "tmp" / ts / remainder

        # Pattern: raw_files/task_{Y}/<file> (already in flat unified format)
        m = self._RE_FLAT_RAW.search(legacy_path)
        if m:
            task_num = int(m.group(1))
            remainder = m.group(2)
            return raw_root / f"task_{task_num}" / remainder

        return None

    # ------------------------------------------------------------------
    # PlanTree convenience methods
    # ------------------------------------------------------------------

    def get_task_output_dir_from_tree(
        self,
        session_id: str,
        task_id: int,
        plan_tree: "PlanTree",
        *,
        create: bool = True,
    ) -> Path:
        """Convenience wrapper that builds ancestor_chain from PlanTree.

        Walks ``parent_id`` links from *task_id* up to root, builds the
        ancestor_chain, then delegates to :meth:`get_task_output_dir`.
        """
        chain = self.build_ancestor_chain(task_id, plan_tree)
        return self.get_task_output_dir(session_id, task_id, chain, create=create)

    @staticmethod
    def build_ancestor_chain(task_id: int, plan_tree: "PlanTree") -> List[int]:
        """Build ancestor chain by walking parent_id links.

        Returns an ordered list from root ancestor to immediate parent
        (not including *task_id* itself).

        Examples:
            - Root node (no parent): returns ``[]``
            - task_5 with parent_3: returns ``[3]``
            - task_9 with parent_7, grandparent_4: returns ``[4, 7]``

        Raises:
            ValueError: If *task_id* is not found in *plan_tree*.
            ValueError: If a circular parent_id chain is detected.
        """
        if not plan_tree.has_node(task_id):
            raise ValueError(f"node {task_id} not found in plan")

        chain: List[int] = []
        current_id = task_id
        visited: set = set()

        # Walk up the parent chain
        node = plan_tree.get_node(current_id)
        current_parent = node.parent_id

        while current_parent is not None:
            if current_parent in visited:
                raise ValueError(
                    f"circular parent chain detected at node {current_parent}"
                )
            if len(chain) >= _MAX_ANCESTOR_DEPTH:
                raise ValueError(
                    f"ancestor chain exceeds maximum depth ({_MAX_ANCESTOR_DEPTH}), "
                    f"possible circular reference"
                )
            visited.add(current_parent)
            chain.append(current_parent)

            if not plan_tree.has_node(current_parent):
                # Parent referenced but not in tree — stop here
                break
            parent_node = plan_tree.get_node(current_parent)
            current_parent = parent_node.parent_id

        # Reverse so root is first, immediate parent is last
        chain.reverse()
        return chain

    # ------------------------------------------------------------------
    # Directory structure validation
    # ------------------------------------------------------------------

    _RE_TASK_DIR = re.compile(r"^task_(\d+)$")
    _RE_TIMESTAMP_DIR = re.compile(r"^\d{8}_\d{6}")
    _DEBUG_LOG_PATTERNS = {"*.log", "*_debug.*", "*_claude_debug.*"}

    def validate_raw_files_structure(
        self,
        session_id: str,
    ) -> List[str]:
        """Validate that raw_files/ follows the hierarchical structure constraints.

        Returns a list of violation messages (empty if valid).

        Checks:
        - Top-level only contains task_{id}/ dirs and tmp/
        - Container (non-leaf) task dirs only contain task_{id}/ subdirs
        - Leaf task dirs don't contain debug/log files
        - tmp/ children are timestamp-patterned directories
        """
        violations: List[str] = []
        raw_root = self.get_raw_files_root(session_id, create=False)
        if not raw_root.exists():
            return violations

        # Check top-level entries
        for entry in sorted(raw_root.iterdir()):
            name = entry.name
            if name == "tmp":
                if not entry.is_dir():
                    violations.append(f"raw_files/tmp should be a directory, found file")
                else:
                    # Validate tmp/ children are timestamp dirs
                    for tmp_child in entry.iterdir():
                        if not tmp_child.is_dir():
                            violations.append(
                                f"raw_files/tmp/{tmp_child.name} should be a directory"
                            )
                        elif not self._RE_TIMESTAMP_DIR.match(tmp_child.name):
                            violations.append(
                                f"raw_files/tmp/{tmp_child.name} doesn't match timestamp pattern"
                            )
            elif self._RE_TASK_DIR.match(name):
                if not entry.is_dir():
                    violations.append(f"raw_files/{name} should be a directory")
                else:
                    self._validate_task_dir(entry, f"raw_files/{name}", violations)
            else:
                violations.append(
                    f"raw_files/{name} is not a valid entry "
                    f"(expected task_<int>/ or tmp/)"
                )

        return violations

    def _validate_task_dir(
        self,
        task_dir: Path,
        path_prefix: str,
        violations: List[str],
    ) -> None:
        """Recursively validate a task directory."""
        import fnmatch

        has_child_task_dirs = False
        has_result_files = False

        for entry in sorted(task_dir.iterdir()):
            if entry.is_dir() and self._RE_TASK_DIR.match(entry.name):
                has_child_task_dirs = True
                # Recurse into child task dirs
                self._validate_task_dir(
                    entry, f"{path_prefix}/{entry.name}", violations
                )
            elif entry.is_file():
                has_result_files = True
                # Check for debug/log files in leaf dirs
                for pattern in self._DEBUG_LOG_PATTERNS:
                    if fnmatch.fnmatch(entry.name, pattern):
                        violations.append(
                            f"{path_prefix}/{entry.name} is a debug/log file "
                            f"that should not be in raw_files"
                        )
                        break
            elif entry.is_dir():
                # Non-task subdirs (like sections/, reviews/) are allowed in leaf dirs
                has_result_files = True

        # Container dirs should not have result files mixed with child task dirs
        if has_child_task_dirs and has_result_files:
            violations.append(
                f"{path_prefix} mixes child task directories with result files; "
                f"container dirs should only contain task_<int>/ subdirs"
            )

    # ------------------------------------------------------------------
    # Explicit override support
    # ------------------------------------------------------------------

    def resolve_override(
        self,
        override_path: str,
    ) -> Path:
        """Resolve an explicit work_dir or output_path override.

        When a tool passes an explicit path, this returns it resolved to
        absolute.  A warning is logged about non-standard path usage.

        Args:
            override_path: The explicit path provided by the tool.

        Returns:
            The override path resolved to absolute.
        """
        resolved = Path(override_path).resolve()
        if self._config.warn_on_override:
            logger.warning(
                "Non-standard output path override used: %s. "
                "Consider using PathRouter for consistent path management.",
                resolved,
            )
        return resolved

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session_dir(self, session_id: str, *, create: bool = False) -> Path:
        """Resolve and validate session directory path."""
        norm = normalize_session_base(session_id)
        if not norm:
            raise ValueError("session_id is required")
        session_dir = self._config.runtime_root / f"session_{norm}"
        if create:
            session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_default_router: Optional[PathRouter] = None


def get_path_router(config: Optional[PathRouterConfig] = None) -> PathRouter:
    """Return the module-level PathRouter singleton.

    Creates one on first call.  Pass *config* to override defaults (useful
    in tests).
    """
    global _default_router
    if _default_router is None or config is not None:
        _default_router = PathRouter(config)
    return _default_router


__all__ = [
    "PathRouter",
    "PathRouterConfig",
    "get_path_router",
]

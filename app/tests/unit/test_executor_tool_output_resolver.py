"""Tests for ToolOutputResolver - centralized output directory resolution."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

from app.services.tool_output_resolver import ToolOutputResolver, get_tool_output_resolver


@dataclass
class MockToolContext:
    """Minimal ToolContext mock for testing."""
    session_id: str | None = None
    plan_id: int | None = None
    task_id: int | None = None
    task_name: str | None = None
    job_id: str | None = None
    owner_id: str | None = None
    work_dir: str = ""
    data_dir: str = ""


class TestToolOutputResolver:
    """Test suite for ToolOutputResolver."""

    def setup_method(self):
        """Reset global resolver before each test."""
        import app.services.tool_output_resolver as module
        module._default_resolver = None

    def test_resolve_with_explicit_dir_highest_priority(self, tmp_path):
        """Explicit directory should override all other resolution paths."""
        resolver = ToolOutputResolver()
        explicit = tmp_path / "explicit_output"
        
        result = resolver.resolve(
            explicit_dir=str(explicit),
            tool_context=MockToolContext(session_id="session_123", task_id=42),
            tool_name="some_tool",
        )
        
        assert result == explicit
        assert explicit.exists()

    def test_resolve_with_explicit_dir_relative_path(self, tmp_path):
        """Relative explicit_dir should resolve against project root."""
        resolver = ToolOutputResolver()
        
        with patch('pathlib.Path.resolve') as mock_resolve:
            mock_resolve.return_value = tmp_path / "resolved"
            result = resolver.resolve(explicit_dir="relative/path")
            
            assert result == tmp_path / "resolved"

    def test_resolve_with_tool_context_work_dir(self, tmp_path):
        """ToolContext.work_dir should take priority after explicit_dir."""
        resolver = ToolOutputResolver()
        work_dir = tmp_path / "custom_work_dir"
        
        context = MockToolContext(
            session_id="session_abc",
            task_id=42,
            work_dir=str(work_dir),
        )
        result = resolver.resolve(tool_context=context, tool_name="test_tool")
        
        assert result == work_dir
        assert work_dir.exists()

    def test_resolve_with_tool_context_session_and_task(self, tmp_path):
        """With session + task in ToolContext, should use PathRouter.get_task_output_dir."""
        resolver = ToolOutputResolver()
        expected_dir = tmp_path / "raw_files" / "task_1" / "task_42"
        
        mock_router = Mock()
        mock_router.get_task_output_dir.return_value = expected_dir
        resolver._path_router = mock_router
        
        context = MockToolContext(session_id="session_abc", task_id=42)
        result = resolver.resolve(tool_context=context, tool_name="test_tool")
        
        mock_router.get_task_output_dir.assert_called_once_with(
            "session_abc", 42, None, create=True
        )
        assert result == expected_dir / "test_tool"
        assert (expected_dir / "test_tool").exists()

    def test_resolve_with_explicit_parameters(self, tmp_path):
        """Explicit session_id/task_id should work without ToolContext."""
        resolver = ToolOutputResolver()
        expected_dir = tmp_path / "raw_files" / "task_99"
        
        mock_router = Mock()
        mock_router.get_task_output_dir.return_value = expected_dir
        resolver._path_router = mock_router
        
        result = resolver.resolve(
            session_id="session_xyz",
            task_id=99,
            tool_name="my_tool",
        )
        
        mock_router.get_task_output_dir.assert_called_once_with(
            "session_xyz", 99, None, create=True
        )
        assert result == expected_dir / "my_tool"

    def test_resolve_with_ancestor_chain(self, tmp_path):
        """Should pass ancestor_chain to PathRouter for hierarchical paths."""
        resolver = ToolOutputResolver()
        expected_dir = tmp_path / "raw_files" / "task_1" / "task_5" / "task_42"
        
        mock_router = Mock()
        mock_router.get_task_output_dir.return_value = expected_dir
        resolver._path_router = mock_router
        
        result = resolver.resolve(
            session_id="session_abc",
            task_id=42,
            ancestor_chain=[1, 5],
            tool_name="analysis",
        )
        
        mock_router.get_task_output_dir.assert_called_once_with(
            "session_abc", 42, [1, 5], create=True
        )
        assert result == expected_dir / "analysis"

    def test_resolve_session_only_uses_tool_outputs(self, tmp_path):
        """With session but no task, should use tool_outputs subdirectory."""
        resolver = ToolOutputResolver()
        expected_dir = tmp_path / "tool_outputs"
        
        mock_router = Mock()
        mock_router.get_tmp_output_dir.return_value = tmp_path / "tmp"
        resolver._path_router = mock_router
        
        with patch('app.services.session_paths.get_session_tool_outputs_dir') as mock_get:
            mock_get.return_value = expected_dir
            result = resolver.resolve(
                session_id="session_abc",
                tool_name="test_tool",
            )
            
            assert result == expected_dir / "test_tool"
            assert (expected_dir / "test_tool").exists()

    def test_resolve_for_tool_convenience_method(self, tmp_path):
        """resolve_for_tool should be a convenient wrapper."""
        resolver = ToolOutputResolver()
        expected_dir = tmp_path / "raw_files" / "task_42"
        
        mock_router = Mock()
        mock_router.get_task_output_dir.return_value = expected_dir
        resolver._path_router = mock_router
        
        context = MockToolContext(session_id="session_abc", task_id=42)
        result = resolver.resolve_for_tool("my_tool", tool_context=context)
        
        assert result == expected_dir / "my_tool"

    def test_get_tool_output_resolver_returns_singleton(self):
        """get_tool_output_resolver should return the same instance."""
        resolver1 = get_tool_output_resolver()
        resolver2 = get_tool_output_resolver()
        
        assert resolver1 is resolver2

    def test_resolve_with_none_tool_context(self, tmp_path):
        """Should handle None ToolContext gracefully."""
        resolver = ToolOutputResolver()
        
        with patch('pathlib.Path.resolve') as mock_resolve:
            mock_resolve.return_value = tmp_path / "runtime" / "tool"
            result = resolver.resolve(tool_context=None, tool_name="tool")
            
            assert "runtime" in str(result)

    def test_resolve_idempotent_calls(self, tmp_path):
        """Multiple calls with same parameters should return same path."""
        resolver = ToolOutputResolver()
        expected_dir = tmp_path / "raw_files" / "task_42"
        
        mock_router = Mock()
        mock_router.get_task_output_dir.return_value = expected_dir
        resolver._path_router = mock_router
        
        result1 = resolver.resolve(session_id="session_abc", task_id=42, tool_name="tool")
        result2 = resolver.resolve(session_id="session_abc", task_id=42, tool_name="tool")
        
        assert result1 == result2

    def test_resolve_tool_outputs_subdirectory_fallback(self, tmp_path):
        """Should use tool_outputs subdirectory as Priority 4 fallback."""
        resolver = ToolOutputResolver()
        tool_outputs_dir = tmp_path / "tool_outputs" / "my_tool"
        
        mock_router = Mock()
        mock_router.get_task_output_dir.side_effect = Exception("No task")
        mock_router.get_tmp_output_dir.side_effect = Exception("No session")
        resolver._path_router = mock_router
        
        with patch('app.services.session_paths.get_session_tool_outputs_dir') as mock_get:
            mock_get.return_value = tmp_path / "tool_outputs"
            result = resolver.resolve(
                session_id="session_abc",
                tool_name="my_tool",
            )
            
            assert result == tool_outputs_dir

    def test_resolve_all_fallbacks_fail_raises_error(self):
        """When all resolution paths fail, should raise ValueError."""
        resolver = ToolOutputResolver()
        
        mock_router = Mock()
        mock_router.get_task_output_dir.side_effect = Exception("Fail 1")
        mock_router.get_tmp_output_dir.side_effect = Exception("Fail 2")
        resolver._path_router = mock_router
        
        with patch('app.services.session_paths.get_session_tool_outputs_dir') as mock_get:
            mock_get.side_effect = Exception("Fail 3")
            
            with patch('pathlib.Path.resolve') as mock_resolve:
                mock_resolve.side_effect = Exception("Fail 4")
                
                with pytest.raises(Exception):
                    resolver.resolve(session_id="session_abc", tool_name="tool")


class TestToolOutputResolverIntegration:
    """Integration tests with real PathRouter (requires runtime directory)."""

    def test_resolve_creates_real_directory(self, tmp_path, monkeypatch):
        """Should create actual directory structure."""
        monkeypatch.setenv("PHAGE_AGENT_RUNTIME_DIR", str(tmp_path / "runtime"))
        
        resolver = ToolOutputResolver()
        resolver._path_router = None
        
        from app.services.path_router import get_path_router
        resolver._path_router = get_path_router()
        
        result = resolver.resolve(
            session_id="test_session",
            task_id=1,
            tool_name="test_tool",
        )
        
        assert result.exists()
        assert result.is_dir()
        assert "raw_files" in str(result)
        assert "task_1" in str(result)
        assert "test_tool" in str(result)

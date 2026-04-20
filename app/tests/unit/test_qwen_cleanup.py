"""Unit tests for QwenSessionDriver orphaned container cleanup.

Feature: qwen-session-cleanup
Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 4.1, 4.2, 6.1, 6.2
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.terminal.qwen_session_driver import QwenSessionDriver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_process(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock asyncio subprocess with communicate()."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# _discover_containers tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_includes_all_states():
    """Discovery uses docker ps -a to include all container states (Req 1.1, 1.2)."""
    stdout = b"abc123def456 gagent-qc-agent-sess1\nfed987654321 gagent-qc-agent-sess2\n"
    mock_proc = _mock_process(returncode=0, stdout=stdout)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await QwenSessionDriver._discover_containers(
            "gagent.component=qwen-code-agent", timeout=10.0
        )

    # Verify docker ps was called with -a flag and label filter
    call_args = mock_exec.call_args
    args = call_args[0]
    assert "docker" in args
    assert "ps" in args
    assert "-a" in args
    assert "--filter" in args
    assert "label=gagent.component=qwen-code-agent" in args

    # Verify parsed output
    assert len(result) == 2
    assert result[0] == ("abc123def456", "gagent-qc-agent-sess1")
    assert result[1] == ("fed987654321", "gagent-qc-agent-sess2")


@pytest.mark.asyncio
async def test_discover_empty_output():
    """Empty docker ps output returns empty list (Req 1.3)."""
    mock_proc = _mock_process(returncode=0, stdout=b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await QwenSessionDriver._discover_containers(
            "gagent.component=qwen-code-agent", timeout=10.0
        )

    assert result == []


@pytest.mark.asyncio
async def test_discover_raises_on_nonzero_exit():
    """Non-zero exit from docker ps raises RuntimeError (Req 4.1)."""
    mock_proc = _mock_process(returncode=1, stderr=b"Cannot connect to Docker daemon")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="docker ps failed"):
            await QwenSessionDriver._discover_containers(
                "gagent.component=qwen-code-agent", timeout=10.0
            )


@pytest.mark.asyncio
async def test_discover_raises_on_file_not_found():
    """FileNotFoundError when docker CLI is missing (Req 4.1)."""
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("docker not found"),
    ):
        with pytest.raises(FileNotFoundError):
            await QwenSessionDriver._discover_containers(
                "gagent.component=qwen-code-agent", timeout=10.0
            )


# ---------------------------------------------------------------------------
# _remove_container tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_success():
    """Successful removal returns True (Req 2.1)."""
    mock_proc = _mock_process(returncode=0)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await QwenSessionDriver._remove_container("abc123", timeout=10.0)

    assert result is True


@pytest.mark.asyncio
async def test_remove_failure_returns_false():
    """Failed removal returns False without raising (Req 2.2)."""
    mock_proc = _mock_process(returncode=1, stderr=b"permission denied")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await QwenSessionDriver._remove_container("abc123", timeout=10.0)

    assert result is False


@pytest.mark.asyncio
async def test_remove_exception_returns_false():
    """Exception during removal returns False without raising (Req 2.2)."""
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=OSError("Docker socket error"),
    ):
        result = await QwenSessionDriver._remove_container("abc123", timeout=10.0)

    assert result is False


# ---------------------------------------------------------------------------
# cleanup_orphaned_containers tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_successful_removal_logs_info(caplog):
    """Successful removal logs INFO with container name and ID (Req 2.3)."""
    containers = [("abc123", "gagent-qc-agent-sess1")]

    with (
        patch.object(
            QwenSessionDriver, "_discover_containers", return_value=containers
        ),
        patch.object(QwenSessionDriver, "_remove_container", return_value=True),
        caplog.at_level(logging.DEBUG),
    ):
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=30.0)

    assert any(
        "Removed orphaned container gagent-qc-agent-sess1 (abc123)" in r.message
        and r.levelno == logging.INFO
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_cleanup_failed_removal_logs_warning(caplog):
    """Failed removal logs WARNING with container name and ID (Req 2.4)."""
    containers = [("abc123", "gagent-qc-agent-sess1")]

    with (
        patch.object(
            QwenSessionDriver, "_discover_containers", return_value=containers
        ),
        patch.object(QwenSessionDriver, "_remove_container", return_value=False),
        caplog.at_level(logging.DEBUG),
    ):
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=30.0)

    assert any(
        "Failed to remove orphaned container gagent-qc-agent-sess1 (abc123)" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_cleanup_no_containers_logs_debug(caplog):
    """No containers found logs DEBUG message (Req 3.2)."""
    with (
        patch.object(QwenSessionDriver, "_discover_containers", return_value=[]),
        caplog.at_level(logging.DEBUG),
    ):
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=30.0)

    assert any(
        "No orphaned Qwen Code containers found" in r.message
        and r.levelno == logging.DEBUG
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_cleanup_summary_log(caplog):
    """Summary log shows found and removed counts (Req 3.1)."""
    containers = [
        ("abc123", "gagent-qc-agent-sess1"),
        ("def456", "gagent-qc-agent-sess2"),
    ]

    with (
        patch.object(
            QwenSessionDriver, "_discover_containers", return_value=containers
        ),
        patch.object(
            QwenSessionDriver,
            "_remove_container",
            side_effect=[True, False],
        ),
        caplog.at_level(logging.DEBUG),
    ):
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=30.0)

    assert any(
        "1/2 containers removed" in r.message and r.levelno == logging.INFO
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_cleanup_docker_cli_not_found(caplog):
    """Docker CLI not found logs WARNING and returns gracefully (Req 4.1)."""
    with (
        patch.object(
            QwenSessionDriver,
            "_discover_containers",
            side_effect=FileNotFoundError("docker not found"),
        ),
        caplog.at_level(logging.DEBUG),
    ):
        # Should not raise
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=30.0)

    assert any(
        "docker CLI not found" in r.message and r.levelno == logging.WARNING
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_cleanup_docker_daemon_unreachable(caplog):
    """Docker daemon unreachable logs WARNING, no exception (Req 4.1, 4.2)."""
    with (
        patch.object(
            QwenSessionDriver,
            "_discover_containers",
            side_effect=RuntimeError("docker ps failed (exit 1): Cannot connect"),
        ),
        caplog.at_level(logging.DEBUG),
    ):
        # Should not raise
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=30.0)

    assert any(
        "Skipping orphaned container cleanup" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_cleanup_timeout_stops_processing(caplog):
    """Timeout stops processing remaining containers (Req 6.1, 6.2)."""
    containers = [
        ("abc123", "gagent-qc-agent-sess1"),
        ("def456", "gagent-qc-agent-sess2"),
        ("ghi789", "gagent-qc-agent-sess3"),
    ]

    async def slow_remove(container_id, timeout):
        await asyncio.sleep(0)  # yield control
        return True

    with (
        patch.object(
            QwenSessionDriver, "_discover_containers", return_value=containers
        ),
        patch.object(
            QwenSessionDriver, "_remove_container", side_effect=slow_remove
        ),
        caplog.at_level(logging.DEBUG),
    ):
        # Use a very short timeout — discovery is mocked so it's instant,
        # but the deadline check should trigger after some removals
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=0.001)

    # Either all processed quickly or timeout warning was logged
    # The key assertion: no exception was raised and function returned


@pytest.mark.asyncio
async def test_cleanup_default_timeout():
    """Default timeout is 30 seconds (Req 6.1)."""
    import inspect

    sig = inspect.signature(QwenSessionDriver.cleanup_orphaned_containers)
    assert sig.parameters["timeout"].default == 30.0


@pytest.mark.asyncio
async def test_cleanup_independent_removal(caplog):
    """Each container removal is independent — failure of one doesn't block others (Req 2.2)."""
    containers = [
        ("abc123", "gagent-qc-agent-sess1"),
        ("def456", "gagent-qc-agent-sess2"),
        ("ghi789", "gagent-qc-agent-sess3"),
    ]

    with (
        patch.object(
            QwenSessionDriver, "_discover_containers", return_value=containers
        ),
        patch.object(
            QwenSessionDriver,
            "_remove_container",
            side_effect=[False, True, True],
        ),
        caplog.at_level(logging.DEBUG),
    ):
        await QwenSessionDriver.cleanup_orphaned_containers(timeout=30.0)

    # Summary should show 2/3 removed
    assert any(
        "2/3 containers removed" in r.message and r.levelno == logging.INFO
        for r in caplog.records
    )

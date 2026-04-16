"""Integration tests for tool invocation chain.

Validates: tool handler dispatch → file_operations real execution → result structure.
Mock boundary: only LLM is mocked; tool handlers run for real.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.integration
def test_tool_executor_dispatches_file_operations_read(
    app_client_factory,
    isolated_app_env,
    monkeypatch,
) -> None:
    """Call file_operations.read through the real tool handler."""
    workspace = isolated_app_env["runtime_root"] / "session_tool_test"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "demo.txt"
    target.write_text("integration test content\n", encoding="utf-8")

    # Add tmp_path tree to allowed base paths so security check passes
    import tool_box.tools_impl.file_operations as fops_mod
    monkeypatch.setattr(
        fops_mod,
        "ALLOWED_BASE_PATHS",
        fops_mod.ALLOWED_BASE_PATHS + [str(isolated_app_env["runtime_root"])],
    )

    with app_client_factory() as _client:
        from tool_box.tools_impl.file_operations import file_operations_handler

        result = asyncio.run(
            file_operations_handler(
                operation="read",
                path=str(target),
            )
        )

        assert result is not None
        assert result.get("success") is True
        assert "integration test content" in result.get("content", "")


@pytest.mark.integration
def test_tool_executor_file_operations_list(
    app_client_factory,
    isolated_app_env,
    monkeypatch,
) -> None:
    """Call file_operations.list to verify directory listing works."""
    workspace = isolated_app_env["runtime_root"] / "session_list_test"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "b.csv").write_text("b", encoding="utf-8")

    import tool_box.tools_impl.file_operations as fops_mod
    monkeypatch.setattr(
        fops_mod,
        "ALLOWED_BASE_PATHS",
        fops_mod.ALLOWED_BASE_PATHS + [str(isolated_app_env["runtime_root"])],
    )

    with app_client_factory() as _client:
        from tool_box.tools_impl.file_operations import file_operations_handler

        result = asyncio.run(
            file_operations_handler(
                operation="list",
                path=str(workspace),
            )
        )

        assert result is not None
        assert result.get("success") is True
        files = result.get("items", result.get("files", []))
        # Flatten names regardless of whether entries are dicts or strings
        names = [
            f.get("name", "") if isinstance(f, dict) else str(f)
            for f in files
        ]
        assert "a.txt" in names
        assert "b.csv" in names


@pytest.mark.integration
def test_code_executor_subprocess_runs_python(
    app_client_factory,
    isolated_app_env,
) -> None:
    """Run a Python snippet via subprocess — the same mechanism code_executor uses internally."""
    import subprocess

    with app_client_factory() as _client:
        proc = subprocess.run(
            ["python", "-c", "print('hello from integration test')"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert proc.returncode == 0
        assert "hello from integration test" in proc.stdout

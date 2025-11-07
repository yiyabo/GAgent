from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.execution import workspace_manager
from tool_box.tools_impl.shell_execution import shell_execute_handler


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_manager, "_DEFAULT_ROOT", root)
    return root


@pytest.fixture()
def test_client(workspace_root) -> TestClient:
    return TestClient(app)


def test_execute_shell_success(test_client: TestClient, workspace_root: Path):
    response = test_client.post(
        "/execution/shell",
        json={
            "owner": "session-success",
            "command": ["python", "-c", "print('hello')"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["stdout"].strip() == "hello"
    assert payload["timed_out"] is False
    assert (workspace_root / "session-success").exists()


def test_execute_shell_timeout(test_client: TestClient):
    response = test_client.post(
        "/execution/shell",
        json={
            "owner": "session-timeout",
            "command": ["python", "-c", "import time; time.sleep(2)"],
            "timeout": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["timed_out"] is True


def test_execute_shell_blacklisted_command(test_client: TestClient):
    response = test_client.post(
        "/execution/shell",
        json={
            "owner": "session-blocked",
            "command": "rm",
        },
    )
    assert response.status_code == 400
    payload = response.json()
    fields = []
    for key in ("detail", "message", "error"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested = value.get("message") or value.get("error")
            if nested:
                fields.append(nested)
            fields.append(str(value))
        elif isinstance(value, str):
            fields.append(value)
    detail_text = "\n".join(fields)
    assert "not permitted" in detail_text


def test_write_and_list_workspace_files(test_client: TestClient, workspace_root: Path):
    response = test_client.post(
        "/execution/workspaces/demo/files",
        json={
            "owner": "demo",
            "relative_path": "script.py",
            "content": "print('demo')\n",
        },
    )
    assert response.status_code == 200

    list_response = test_client.get("/execution/workspaces/demo")
    assert list_response.status_code == 200
    listing = list_response.json()
    assert any(item["name"] == "script.py" for item in listing["items"])
    assert (workspace_root / "demo" / "script.py").exists()


@pytest.mark.asyncio
async def test_shell_execution_tool_writes_files(workspace_root: Path):
    result = await shell_execute_handler(
        owner="tool-session",
        command=["python", "-c", "print('tool')"],
        files={"main.py": "print('tool')\n"},
    )
    assert result["stdout"].strip() == "tool"
    assert "main.py" in result["files_written"]
    workspace_path = Path(result["workspace"])
    assert workspace_path.exists()
    assert (workspace_path / "main.py").exists()

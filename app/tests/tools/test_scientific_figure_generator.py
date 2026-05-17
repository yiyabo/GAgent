from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.routers.chat.action_handlers import handle_tool_action
from app.config.deliverable_config import DeliverableSettings
from app.routers.chat.models import AgentStep
from app.services.deliverables.publisher import DeliverablePublisher
from app.services.llm.structured_response import LLMAction
from app.services.execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor
import app.services.execution.tool_executor as tool_executor_module
from app.routers.chat.tool_results import drop_callables
from app.services.tool_schemas import EXECUTOR_AVAILABLE_TOOLS, build_tool_schemas
from tool_box.context import ToolContext
from tool_box.tool_registry import get_tool_orchestration_metadata, register_all_tools
from tool_box.tools import get_tool_registry
from tool_box.tools_impl.scientific_figure_generator import scientific_figure_generator_handler


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, dict)
    return cast(Mapping[str, object], value)


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _string_list(value: object) -> list[str]:
    items = _object_list(value)
    assert all(isinstance(item, str) for item in items)
    return cast(list[str], items)


def _string(value: object) -> str:
    assert isinstance(value, str)
    return value


def _json_mapping(path: Path) -> Mapping[str, object]:
    payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
    return _mapping(payload)


def test_scientific_figure_generator_creates_qa_provenance_and_deliverable_payload(tmp_path: Path) -> None:
    raw_result: object = asyncio.run(
        scientific_figure_generator_handler(
            title="Communication Overview",
            datasets=[
                {
                    "name": "pathways",
                    "rows": [
                        {"pathway": "MIF", "total_prob": 0.41, "rank": 1},
                        {"pathway": "MK", "total_prob": 0.38, "rank": 2},
                        {"pathway": "CypA", "total_prob": 0.12, "rank": 3},
                    ],
                }
            ],
            panels=[{"dataset": "pathways", "type": "bar", "x": "pathway", "y": "total_prob", "title": "Top Pathways"}],
            output_dir=str(tmp_path),
            output_basename="communication_overview",
            tool_context=ToolContext(work_dir=str(tmp_path)),
        )
    )

    result = _mapping(raw_result)
    assert result["success"] is True
    payload = _mapping(result["result"])
    for key in ("figure_png", "figure_pdf", "legend_md", "provenance_tsv", "qa_json"):
        path = Path(_string(payload[key]))
        assert path.is_file(), key
        assert path.stat().st_size > 0, key
    qa = _json_mapping(Path(_string(payload["qa_json"])))
    assert qa["passed"] is True
    deliverable_submit = _mapping(result["deliverable_submit"])
    assert deliverable_submit["publish"] is True
    assert len(_object_list(deliverable_submit["artifacts"])) == 5


def test_scientific_figure_generator_accepts_direct_row_list_from_llm(tmp_path: Path) -> None:
    raw_result: object = asyncio.run(
        scientific_figure_generator_handler(
            title="Direct Row List Figure",
            datasets=[
                {"pathway": "MIF", "total_prob": 0.4103, "rank": 1},
                {"pathway": "MK", "total_prob": 0.3803, "rank": 2},
                {"pathway": "CypA", "total_prob": 0.1200, "rank": 3},
            ],
            panels=[{"type": "bar", "x": "pathway", "y": "total_prob"}],
            output_dir=str(tmp_path),
            output_basename="direct_row_list",
            tool_context=ToolContext(work_dir=str(tmp_path)),
        )
    )

    result = _mapping(raw_result)
    assert result["success"] is True
    payload = _mapping(result["result"])
    figure_png = Path(_string(payload["figure_png"]))
    assert figure_png.is_file()
    assert _string(payload["figure_png"]).endswith("direct_row_list.png")


def test_scientific_figure_generator_rejects_output_dir_outside_work_dir(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    outside_dir = tmp_path / "outside"
    work_dir.mkdir()

    raw_result: object = asyncio.run(
        scientific_figure_generator_handler(
            datasets=[{"rows": [{"label": "A", "value": 1}]}],
            output_dir=str(outside_dir),
            tool_context=ToolContext(work_dir=str(work_dir)),
        )
    )

    result = _mapping(raw_result)
    assert result["success"] is False
    assert "work directory" in _string(result["error"])
    assert not outside_dir.exists()


def test_scientific_figure_generator_rejects_dataset_path_outside_work_dir(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    outside_dir = tmp_path / "outside"
    work_dir.mkdir()
    outside_dir.mkdir()
    secret = outside_dir / "secret.csv"
    secret.write_text("label,value\nA,1\n", encoding="utf-8")

    raw_result: object = asyncio.run(
        scientific_figure_generator_handler(
            datasets=[{"name": "secret", "path": str(secret)}],
            tool_context=ToolContext(work_dir=str(work_dir)),
        )
    )

    result = _mapping(raw_result)
    assert result["success"] is False
    assert "work directory" in _string(result["error"])


def test_scientific_figure_generator_registered_with_metadata() -> None:
    register_all_tools()
    tool = get_tool_registry().get_tool("scientific_figure_generator")

    assert tool is not None
    assert tool.category == "visualization"
    assert tool.is_concurrent_safe is True
    assert "figure" in tool.search_hint
    metadata_obj: object = get_tool_orchestration_metadata("scientific_figure_generator")
    metadata = _mapping(metadata_obj)
    assert metadata["is_concurrent_safe"] is True


def test_scientific_figure_generator_exposed_to_native_tool_calling() -> None:
    assert "scientific_figure_generator" in EXECUTOR_AVAILABLE_TOOLS
    schemas_obj: object = build_tool_schemas(["scientific_figure_generator"])
    schemas = [_mapping(item) for item in _object_list(schemas_obj)]
    names = [_string(_mapping(schema["function"])["name"]) for schema in schemas]

    assert "scientific_figure_generator" in names
    figure_schema = next(
        schema
        for schema in schemas
        if _string(_mapping(schema["function"])["name"]) == "scientific_figure_generator"
    )
    figure_function = _mapping(figure_schema["function"])
    description = _string(figure_function["description"])
    params = _mapping(figure_function["parameters"])
    assert "Preferred tool" in description
    assert "code_executor" in description
    assert _string_list(params["required"]) == ["datasets"]
    assert "panels" in _mapping(params["properties"])


def test_scientific_figure_generator_publishes_deliverables_through_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_root = tmp_path
    work_dir = project_root / "work"
    work_dir.mkdir(parents=True)
    publisher = DeliverablePublisher(
        settings=DeliverableSettings(ingest_mode="explicit"),
        project_root=project_root,
        runtime_dir=runtime_dir,
    )
    monkeypatch.setattr(tool_executor_module, "get_deliverable_publisher", lambda: publisher)
    executor = UnifiedToolExecutor()
    raw_result: object = executor.execute_sync(
        "scientific_figure_generator",
        {
            "title": "Deliverable Figure",
            "datasets": [
                {
                    "name": "signals",
                    "rows": [
                        {"cell_type": "Tumor", "signal": 2.0},
                        {"cell_type": "Macrophage", "signal": 1.5},
                    ],
                }
            ],
            "panels": [{"dataset": "signals", "type": "bar", "x": "cell_type", "y": "signal", "title": "Signal Strength"}],
            "output_dir": str(work_dir),
            "output_basename": "deliverable_figure",
        },
        context=ToolExecutionContext(session_id="scientific-figure-test", work_dir=str(work_dir)),
    )

    result = _mapping(raw_result)
    assert result["success"] is True
    deliverables = result.get("deliverables")
    deliverables_map = _mapping(deliverables)
    published_count = deliverables_map["published_files_count"]
    assert isinstance(published_count, int)
    assert published_count >= 4
    manifest_path = Path(_string(deliverables_map["manifest_path"]))
    assert manifest_path.is_file()
    manifest = _json_mapping(manifest_path)
    paths = {
        _string(_mapping(item)["path"])
        for item in _object_list(manifest["items"])
    }
    assert any(path.endswith("deliverable_figure.png") for path in paths)
    assert any(path.endswith("deliverable_figure_qa.json") for path in paths)
    assert any(path.endswith("summary.md") for path in paths)


def test_scientific_figure_generator_action_handler_executes_not_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_root = tmp_path

    publisher = DeliverablePublisher(
        settings=DeliverableSettings(ingest_mode="explicit"),
        project_root=project_root,
        runtime_dir=runtime_dir,
    )
    monkeypatch.setattr("app.routers.chat.action_handlers.get_deliverable_publisher", lambda: publisher)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_dir))

    class AgentStub:
        session_id: str = "chat-scientific-figure-test"
        conversation_id: str = "conversation-test"

        def __init__(self) -> None:
            self.extra_context: dict[str, object] = {}
            self.plan_session: SimpleNamespace = SimpleNamespace(plan_id=None, repo=None)
            self._dirty: bool = False

        def _sync_task_status_after_tool_execution(self, **_: object) -> None:
            return None

    agent = AgentStub()

    action = LLMAction(
        kind="tool_operation",
        name="scientific_figure_generator",
        parameters={
            "title": "Chat Figure",
            "datasets": [
                {
                    "name": "pathways",
                    "rows": [
                        {"pathway": "MIF", "total_prob": 0.41},
                        {"pathway": "MK", "total_prob": 0.38},
                    ],
                }
            ],
            "panels": [{"dataset": "pathways", "type": "bar", "x": "pathway", "y": "total_prob"}],
            "output_basename": "chat_figure",
            "publish": True,
        },
    )

    step = asyncio.run(handle_tool_action(agent, action))

    assert isinstance(step, AgentStep)
    assert step.success is True
    details_obj: object = step.details
    details = _mapping(details_obj)
    result_details = _mapping(details["result"])
    deliverables = _mapping(details["deliverables"])
    published_files_count = deliverables["published_files_count"]
    assert isinstance(published_files_count, int)
    assert details["tool"] == "scientific_figure_generator"
    assert result_details["success"] is True
    assert _string(result_details["figure_png"]).endswith("chat_figure.png")
    assert published_files_count >= 4
    assert result_details.get("error") != "unsupported_tool"


def test_scientific_figure_generator_action_handler_recovers_dataset_name_from_user_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_root = tmp_path
    publisher = DeliverablePublisher(
        settings=DeliverableSettings(ingest_mode="explicit"),
        project_root=project_root,
        runtime_dir=runtime_dir,
    )
    monkeypatch.setattr("app.routers.chat.action_handlers.get_deliverable_publisher", lambda: publisher)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_dir))

    class AgentStub:
        session_id: str = "chat-scientific-figure-string-dataset-test"
        conversation_id: str = "conversation-test"
        _current_user_message: str = (
            "Generate a scientific figure. Dataset name pathways has rows: "
            "MIF total_prob 0.4103 rank 1; MK total_prob 0.3803 rank 2; "
            "CypA total_prob 0.1200 rank 3. Make one bar panel."
        )

        def __init__(self) -> None:
            self.extra_context: dict[str, object] = {}
            self.plan_session: SimpleNamespace = SimpleNamespace(plan_id=None, repo=None)
            self._dirty: bool = False

        def _sync_task_status_after_tool_execution(self, **_: object) -> None:
            return None

    action = LLMAction(
        kind="tool_operation",
        name="scientific_figure_generator",
        parameters={
            "title": "Recovered Rows Figure",
            "datasets": "pathways",
            "panels": [{"type": "bar", "x": "pathway", "y": "total_prob"}],
            "output_basename": "recovered_rows",
            "formats": ["png", "pdf"],
            "publish": True,
        },
    )

    step = asyncio.run(handle_tool_action(AgentStub(), action))

    assert step.success is True
    details = _mapping(step.details)
    result_details = _mapping(details["result"])
    assert result_details["success"] is True
    assert _string(result_details["figure_png"]).endswith("recovered_rows.png")


def test_scientific_figure_generator_action_result_is_json_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_root = tmp_path
    publisher = DeliverablePublisher(
        settings=DeliverableSettings(ingest_mode="explicit"),
        project_root=project_root,
        runtime_dir=runtime_dir,
    )
    monkeypatch.setattr("app.routers.chat.action_handlers.get_deliverable_publisher", lambda: publisher)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_dir))

    class AgentStub:
        session_id: str = "chat-scientific-figure-json-safe-test"
        conversation_id: str = "conversation-test"

        def __init__(self) -> None:
            self.extra_context: dict[str, object] = {}
            self.plan_session: SimpleNamespace = SimpleNamespace(plan_id=None, repo=None)
            self._dirty: bool = False

        def _sync_task_status_after_tool_execution(self, **_: object) -> None:
            return None

    action = LLMAction(
        kind="tool_operation",
        name="scientific_figure_generator",
        parameters={
            "title": "JSON Safe Figure",
            "datasets": [
                {
                    "name": "pathways",
                    "rows": [
                        {"pathway": "MIF", "total_prob": 0.41},
                        {"pathway": "MK", "total_prob": 0.38},
                    ],
                }
            ],
            "panels": [{"dataset": "pathways", "type": "bar", "x": "pathway", "y": "total_prob"}],
            "output_basename": "json_safe_figure",
            "publish": True,
        },
    )

    step = asyncio.run(handle_tool_action(AgentStub(), action))
    raw_payload: object = step.model_dump()
    safe_payload = cast(object, drop_callables(raw_payload))

    serialized = json.dumps(safe_payload, ensure_ascii=False)
    assert serialized

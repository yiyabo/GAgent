"""Durable HTTP/SSE research journeys with only the provider boundary scripted.

The controller, tool dispatch, Python kernel, verification, message persistence,
and artifact download routes remain real. Expected data stays in test assertions.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import textwrap
import time
from pathlib import Path

import pytest

from app.database import get_db
from app.llm import LLMClient, NativeStreamResult, NativeToolCall
from app.repository import chat_runs


INPUT_CSV = "id,group,score\na,A,10\nb,A,20\na,A,90\nc,B,30\nd,B,50\ne,A,\nf,B,bad\n"
HEADERS = {"X-Forwarded-User": "journey-owner"}


def _sse_events(text: str) -> list[tuple[int, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        if "data" in fields:
            events.append((int(fields["id"].strip()), json.loads(fields["data"].strip())))
    return events


class _CleaningProvider:
    """Generate ordinary input-driven code; do not read the independent oracle."""

    def __init__(self) -> None:
        self.input_path: Path | None = None
        self.output_root: Path | None = None
        self.responses = 0
        self.interrupt_after_write = False
        self.submitted = False

    @property
    def answer(self) -> str:
        return "Cleaned data and verified group statistics are ready: " + ", ".join(
            f"[{name}]({self.output_root / name})" for name in ("clean.csv", "summary.json")
        )

    async def native(self, _client, **kwargs):
        offered = {tool["function"]["name"] for tool in kwargs["tools"]}
        assert "execute_code" in offered
        return self.next_response()

    def next_response(self):
        self.responses += 1
        if self.interrupt_after_write and self.responses == 2:
            raise asyncio.CancelledError("scripted provider interruption after the verified write")
        if self.responses > 1:
            if not self.submitted:
                self.submitted = True
                return NativeStreamResult(content="Publish the requested final files.", finish_reason="tool_calls", tool_calls=[
                    NativeToolCall(id="publish-once", name="deliverable_submit", arguments={
                        "artifacts": [{"path": str(self.output_root / name), "module": "image_tabular"}
                                      for name in ("clean.csv", "summary.json")],
                    }),
                ])
            return NativeStreamResult(content=self.answer, finish_reason="tool_calls", tool_calls=[
                NativeToolCall(id="deliver-once", name="submit_final_answer", arguments={"answer": self.answer, "confidence": 1.0}),
            ])
        code = textwrap.dedent(f"""
            import csv, json
            from pathlib import Path
            source = Path({str(self.input_path)!r})
            output = Path({str(self.output_root)!r})
            output.mkdir(parents=True, exist_ok=True)
            counter = output / '_writes.txt'
            counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
            cleaned, seen, groups = [], set(), {{}}
            with source.open() as stream:
                for row in csv.DictReader(stream):
                    try:
                        score = float(row['score'])
                    except (TypeError, ValueError):
                        continue
                    if row['id'] in seen:
                        continue
                    seen.add(row['id'])
                    cleaned.append({{'id': row['id'], 'group': row['group'], 'score': score}})
                    groups.setdefault(row['group'], []).append(score)
            with (output / 'clean.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['id', 'group', 'score'])
                writer.writeheader()
                writer.writerows(cleaned)
            (output / 'summary.json').write_text(json.dumps({{
                group: {{'count': len(values), 'mean': sum(values) / len(values)}}
                for group, values in groups.items()
            }}))
            print('Wrote clean.csv and summary.json')
        """)
        return NativeStreamResult(
            content="Clean the uploaded records and calculate group statistics.",
            tool_calls=[NativeToolCall(id="clean-once", name="execute_code", arguments={"code": code})],
            finish_reason="tool_calls",
        )


@pytest.fixture(params=[("native", False), ("native", True), ("strict", False), ("strict", True)],
                ids=["native-baseline", "native-runtime-v2", "strict-baseline", "strict-runtime-v2"])
def cleaning_provider(isolated_app_env, monkeypatch, request):
    from app.services.deliverables import publisher
    from app.services import path_router
    from app.services.llm import llm_service
    from app.services.memory import chat_memory_middleware
    from tool_box.tools_impl.execute_code.kernel import shutdown_kernels_for_session

    for flag in ("AGENT_RUNTIME_V2_ENABLED", "ARTIFACT_VERSIONING_ENABLED", "SKILL_RECOMMENDATION_V2_ENABLED", "SKILL_CONTEXT_PROGRESSIVE_ENABLED"):
        monkeypatch.setenv(flag, "0")
    protocol, runtime_v2 = request.param
    monkeypatch.setenv("AGENT_RUNTIME_V2_ENABLED", "1" if runtime_v2 else "0")
    for name, value in {
        "CODE_MODE_ENABLED": "1", "CODE_MODE_CELL_TIMEOUT_SECONDS": "15",
        "CHAT_RUN_BUDGET_SECONDS": "30", "CHAT_RUN_CLOSE_RESERVE_SECONDS": "3",
        "CHAT_RUN_SYNTHESIS_RESERVE_SECONDS": "0", "QUALITY_EVALUATION_ENABLED": "0",
        "PLAN_TASK_EXECUTION_BACKEND": "internal", "LLM_RETRIES": "0",
    }.items():
        monkeypatch.setenv(name, value)
    provider = _CleaningProvider()

    async def native(client, **kwargs):
        return await provider.native(client, **kwargs)

    def chat(_client, *args, **kwargs):
        return provider.answer if provider.output_root else "Research journey"

    async def chat_async(client, *args, **kwargs):
        return chat(client, *args, **kwargs)

    async def stream(client, *args, **kwargs):
        if protocol == "strict" and kwargs.get("messages"):
            response = provider.next_response()
            action = None
            if response.tool_calls and response.tool_calls[0].name != "submit_final_answer":
                call = response.tool_calls[0]
                action = {"tool": call.name, "params": call.arguments}
            yield json.dumps({"thinking": response.content, "action": action,
                              "final_answer": None if action else {"answer": response.content, "confidence": 1.0}})
        else:
            yield chat(client, *args, **kwargs)

    monkeypatch.setattr(LLMClient, "stream_chat_with_tools_async", native)
    monkeypatch.setattr(LLMClient, "chat", chat)
    monkeypatch.setattr(LLMClient, "chat_async", chat_async)
    monkeypatch.setattr(LLMClient, "stream_chat", lambda client, *a, **kw: iter([chat(client, *a, **kw)]))
    monkeypatch.setattr(LLMClient, "stream_chat_async", stream)
    if protocol == "strict":
        # Advertise a provider without native function calling; the real
        # controller selects its strict JSON path from this capability.
        monkeypatch.setattr(llm_service.LLMService, "stream_chat_with_tools_async", None)
    monkeypatch.setattr(llm_service, "_llm_service", None)
    monkeypatch.setattr(path_router, "_default_router", None)
    # The request disables recall; also disable its unrelated background memory
    # writer so this integration test cannot start embedding/provider work.
    memory_writer = chat_memory_middleware.ChatMemoryMiddleware.__new__(chat_memory_middleware.ChatMemoryMiddleware)
    memory_writer.enabled = False
    monkeypatch.setattr(chat_memory_middleware, "_chat_memory_middleware", memory_writer)
    monkeypatch.setattr(publisher, "_publisher", publisher.DeliverablePublisher(
        project_root=isolated_app_env["runtime_root"].parent,
        runtime_dir=isolated_app_env["runtime_root"],
    ))
    yield provider
    shutdown_kernels_for_session("research-journey")


def _prepare_cleaning_request(client, provider):
    session_id = "research-journey"
    assert client.patch(f"/chat/sessions/{session_id}", json={"name": "Research journey"}, headers=HEADERS).status_code == 200
    uploaded = client.post("/upload/file", data={"session_id": session_id},
                           files={"file": ("input.csv", INPUT_CSV, "text/csv")}, headers=HEADERS)
    assert uploaded.status_code == 200, uploaded.text
    provider.input_path = Path(uploaded.json()["file_path"])
    from app.services.path_router import get_path_router
    output = get_path_router().get_session_dir(session_id, create=True) / "workspace"
    output.mkdir(exist_ok=True)
    provider.output_root = output
    return {
        "session_id": session_id, "client_message_id": "cleaning-turn-once",
        "message": f"Use Python to clean {provider.input_path}: discard missing/non-numeric scores and duplicate IDs keeping the first. Write clean.csv and summary.json with count and mean for each group in {output}. Return download links.",
        "context": {"memory_enabled": False,
                    "output_spec_base_dir": str(output), "output_spec": {
                        "source": "explicit", "required_outputs": [
                            {"kind": "data", "extensions": [Path(name).suffix], "target_path": str(output / name)}
                            for name in ("clean.csv", "summary.json")
                        ]}},
    }, output


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_cleaning_run_delivers_files_over_http_and_replays_without_reexecution(
    app_client_factory, isolated_app_env, cleaning_provider,
):
    session_id = "research-journey"
    with app_client_factory() as client:
        request, output = _prepare_cleaning_request(client, cleaning_provider)
        created = client.post("/chat/runs", json=request, headers=HEADERS)
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]
        streamed = client.get(created.json()["events_stream_url"], params={"session_id": session_id}, headers=HEADERS)
        assert streamed.status_code == 200, streamed.text
        events = _sse_events(streamed.text)
        terminal = [(seq, event) for seq, event in events if event["type"] in {"final", "error"}]
        assert len(terminal) == 1, events
        final = terminal[0][1]
        assert final["type"] == "final", final
        assert final["payload"]["metadata"]["status"] == "completed", final
        assert final["payload"]["metadata"]["output_verification"]["status"] == "passed", final
        assert all(name in final["payload"]["response"] for name in ("clean.csv", "summary.json"))
        assert "execute_code" in final["payload"]["metadata"]["tools_used"], final
        assert "deliverable_submit" in final["payload"]["metadata"]["tools_used"], final
        assert any(event["type"] == "thinking_step" for _, event in events), events
        assert [seq for seq, _ in events] == sorted({seq for seq, _ in events})

        downloaded = {}
        for name in ("clean.csv", "summary.json"):
            response = client.get(f"/artifacts/sessions/{session_id}/file", params={"path": f"workspace/{name}"}, headers=HEADERS)
            assert response.status_code == 200, response.text
            assert response.content == (output / name).read_bytes()
            downloaded[name] = response.text
        rows = list(csv.DictReader(io.StringIO(downloaded["clean.csv"])))
        assert [(r["id"], r["group"], float(r["score"])) for r in rows] == [
            ("a", "A", 10), ("b", "A", 20), ("c", "B", 30), ("d", "B", 50),
        ]
        assert json.loads(downloaded["summary.json"]) == {"A": {"count": 2, "mean": 15}, "B": {"count": 2, "mean": 40}}
        published = client.get(f"/artifacts/sessions/{session_id}/deliverables", headers=HEADERS)
        assert published.status_code == 200, published.text
        assert {item["path"] for item in published.json()["items"]} == {
            "image_tabular/clean.csv", "image_tabular/summary.json",
        }, published.json()
        for name in ("clean.csv", "summary.json"):
            response = client.get(f"/artifacts/sessions/{session_id}/deliverables/file",
                                  params={"path": f"image_tabular/{name}"}, headers=HEADERS)
            assert response.status_code == 200 and response.content == (output / name).read_bytes()

        # A dropped SSE connection resumes from its durable cursor, not a new run.
        cursor = next(seq for seq, event in events if event["type"] == "thinking_step")
        replay = client.get(created.json()["events_stream_url"], params={"session_id": session_id, "after_seq": cursor}, headers=HEADERS)
        replayed = _sse_events(replay.text)
        assert replayed == [(seq, event) for seq, event in events if seq > cursor]
        duplicate = client.post("/chat/runs", json=request, headers=HEADERS)
        assert duplicate.status_code == 200 and duplicate.json()["run_id"] == run_id
        assert (output / "_writes.txt").read_text() == "1"

        # Final-event delivery precedes the worker's persisted history closeout.
        deadline = time.monotonic() + 5
        while True:
            history = client.get(f"/chat/history/{session_id}", headers=HEADERS).json()["messages"]
            assistants = [row for row in history if row["role"] == "assistant"]
            if assistants or time.monotonic() > deadline:
                break
            time.sleep(.02)
        assert len(assistants) == 1, history
        assert all(name in assistants[0]["content"] for name in ("clean.csv", "summary.json"))
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM chat_runs WHERE session_id=?", (session_id,)).fetchone()[0] == 1
        assert chat_runs.get_chat_run(run_id)["status"] == "succeeded"
        assert not chat_runs.is_chat_run_lease_live(run_id)


@pytest.mark.integration
@pytest.mark.timeout(60)
@pytest.mark.parametrize("cleaning_provider", [("native", False), ("native", True)],
                         indirect=True, ids=["native-baseline", "native-runtime-v2"])
def test_interrupted_run_continues_through_http_without_repeating_verified_write(
    app_client_factory, cleaning_provider,
):
    session_id = "research-journey"
    cleaning_provider.interrupt_after_write = True
    with app_client_factory() as client:
        request, output = _prepare_cleaning_request(client, cleaning_provider)
        created = client.post("/chat/runs", json=request, headers=HEADERS)
        assert created.status_code == 200, created.text
        source_id = created.json()["run_id"]
        source_stream = client.get(created.json()["events_stream_url"], params={"session_id": session_id}, headers=HEADERS)
        source_events = _sse_events(source_stream.text)
        assert source_events[-1][1]["type"] == "error", source_events
        assert chat_runs.get_chat_run(source_id)["status"] == "cancelled"
        assert (output / "_writes.txt").read_text() == "1"

        info = client.get(f"/chat/runs/{source_id}/resume", params={"session_id": session_id}, headers=HEADERS)
        assert info.status_code == 200 and info.json()["can_resume"], info.text
        resume_request = {"session_id": session_id, "client_message_id": "resume-cleaning-once", "memory_enabled": False}
        continued = client.post(f"/chat/runs/{source_id}/resume", json=resume_request, headers=HEADERS)
        assert continued.status_code == 200, continued.text
        child_id = continued.json()["run_id"]
        assert child_id != source_id
        child_stream = client.get(f"/chat/runs/{child_id}/events", params={"session_id": session_id}, headers=HEADERS)
        child_events = _sse_events(child_stream.text)
        terminal = [event for _, event in child_events if event["type"] in {"final", "error"}]
        assert len(terminal) == 1 and terminal[0]["type"] == "final", child_events
        assert terminal[0]["payload"]["metadata"]["output_verification"]["status"] == "passed"
        assert chat_runs.get_chat_run(child_id)["status"] == "succeeded"
        assert chat_runs.get_chat_run(source_id)["status"] == "cancelled"
        duplicate = client.post(f"/chat/runs/{source_id}/resume", json=resume_request, headers=HEADERS)
        assert duplicate.status_code == 200 and duplicate.json()["run_id"] == child_id
        assert (output / "_writes.txt").read_text() == "1"
        downloaded = client.get(f"/artifacts/sessions/{session_id}/file", params={"path": "workspace/summary.json"}, headers=HEADERS)
        assert downloaded.status_code == 200
        assert downloaded.json() == {"A": {"count": 2, "mean": 15}, "B": {"count": 2, "mean": 40}}
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM chat_runs WHERE session_id=?", (session_id,)).fetchone()[0] == 2

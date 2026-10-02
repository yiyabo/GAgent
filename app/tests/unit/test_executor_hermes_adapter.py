import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.services.harness_eval.config import EvalSuiteConfig
from app.services.harness_eval.hermes_adapter import HermesRunConfig, dry_run_hermes_trial, isolated_environment, run_hermes_trial
from app.services.harness_eval.hermes_gateway import ObservedGateway
from app.services.harness_eval.hermes_worker import execute


@pytest.fixture
def upstream():
    calls = []
    replies = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            calls.append({"payload": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                          "authorization": self.headers.get("Authorization"), "path": self.path})
            reply = replies.pop(0) if replies else {"choices": [{"finish_reason": "stop"}],
                                                  "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
            content = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if isinstance(reply, bytes) else "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/v1", calls=calls, replies=replies)
    server.shutdown()
    server.server_close()
    thread.join()


def post(url, model="test-model"):
    return urlopen(Request(url + "/chat/completions", data=json.dumps({"model": model, "messages": [{"role": "user", "content": "public task"}], "max_tokens": 4096}).encode(), headers={"Content-Type": "application/json"}), timeout=5).read()


def test_relay_observes_provider_usage_without_logging_key_or_prompts(upstream, tmp_path):
    journal = tmp_path / "calls.jsonl"
    with ObservedGateway(upstream.url, "test-secret", "test-model", attempt_limit=2, deadline=time.monotonic()+10, journal=journal) as relay:
        post(relay.url)
    assert upstream.calls[0]["authorization"] == "Bearer test-secret"
    assert upstream.calls[0]["path"] == "/v1/chat/completions"
    assert upstream.calls[0]["payload"]["max_tokens"] == 4096
    assert relay.accounting()["total_tokens"] == 7
    assert relay.events[-1]["finish_reasons"] == ["stop"]
    assert "test-secret" not in journal.read_text()
    assert "public task" not in journal.read_text()


def test_stream_usage_and_missing_usage_remain_distinct(upstream):
    upstream.replies.extend([b'data: {"choices":[{"finish_reason":"length"}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}\n\ndata: [DONE]\n\n',
                             {"choices": [{"finish_reason": "stop"}]}])
    with ObservedGateway(upstream.url, "key", "test-model", attempt_limit=3, deadline=time.monotonic()+10) as relay:
        post(relay.url)
        assert relay.accounting()["total_tokens"] == 14
        assert relay.events[-1]["stream_complete"] is True
        post(relay.url)
        with pytest.raises(HTTPError):
            post(relay.url)
    accounting = relay.accounting()
    assert accounting["total_tokens"] is None
    assert accounting["usage_source"] == "missing"
    assert accounting["known_usage"]["total_tokens"] == 14
    assert accounting["usage_missing_attempts"] == 1
    assert len(upstream.calls) == 2


def test_complete_missing_usage_receipt_stops_subsequent_calls(upstream):
    events = []
    upstream.replies.append({"choices": [{"finish_reason": "stop"}]})
    with ObservedGateway(upstream.url, "key", "test-model", attempt_limit=12, deadline=time.monotonic()+10, event_hook=events.append) as relay:
        post(relay.url)
        for _ in range(3):
            with pytest.raises(HTTPError):
                post(relay.url)
    receipt = [e for e in events if e.get("response_complete")][0]
    assert receipt["kind"] == "attempt" and receipt["usage"] is None
    assert receipt["usage_source"] == "missing"
    assert len(upstream.calls) == 1


def test_limits_and_route_change_deny_before_upstream(upstream):
    with ObservedGateway(upstream.url, "key", "test-model", attempt_limit=1, deadline=time.monotonic()+10) as relay:
        with pytest.raises(HTTPError):
            post(relay.url, model="different-model")
        post(relay.url)
        with pytest.raises(HTTPError):
            post(relay.url)
    assert len(upstream.calls) == 1
    assert relay.accounting()["provider_attempts"] == 1


def test_campaign_hook_can_refuse_request_without_leaking_exception(upstream):
    def reject(event):
        if event["kind"] == "attempt":
            raise RuntimeError("private secret exception")
    with ObservedGateway(upstream.url, "key", "test-model", attempt_limit=2, deadline=time.monotonic()+10, event_hook=reject) as relay:
        with pytest.raises(HTTPError) as error:
            post(relay.url)
        assert "private secret" not in error.value.read().decode()
    assert not upstream.calls
    assert relay.accounting()["provider_attempts"] == 0


def test_campaign_receipts_use_the_same_accounting_identity(upstream):
    events = []
    with ObservedGateway(upstream.url, "key", "test-model", attempt_limit=2, deadline=time.monotonic()+10, event_hook=events.append) as relay:
        post(relay.url)
    assert [event["kind"] for event in events] == ["attempt", "attempt"]
    assert events[0]["logical_call_id"] == events[1]["logical_call_id"]
    assert events[0]["attempt_no"] == events[1]["attempt_no"]
    assert events[1]["usage_source"] == "provider"
    assert events[1]["usage"]["total_tokens"] == 7


@pytest.mark.parametrize("limits", [{}, {"max_tokens": 8192}, {"max_tokens": 4096, "max_completion_tokens": 8192}])
def test_output_caps_are_enforced_before_model_request(upstream, limits):
    with ObservedGateway(upstream.url, "key", "test-model", attempt_limit=2, deadline=time.monotonic()+10) as relay:
        request = Request(relay.url + "/chat/completions", data=json.dumps({"model": "test-model", **limits}).encode())
        with pytest.raises(HTTPError):
            urlopen(request, timeout=5)
    assert not upstream.calls


def test_worker_environment_does_not_inherit_user_configuration_or_keys(tmp_path):
    env = isolated_environment(tmp_path, "http://127.0.0.1:1234/v1", "model", parent={"HOME": "/private", "HERMES_HOME": "/private/hermes", "PATH": "/usr/bin", "OPENAI_API_KEY": "private", "PYTHONPATH": "/private/oracle", "HTTP_PROXY": "private"})
    assert env["HOME"] == str(tmp_path / "home")
    assert env["HERMES_HOME"] == str(tmp_path / "profile")
    assert env["OPENAI_API_KEY"] == "isolated-evaluation"
    assert env["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    assert "PYTHONPATH" not in env and "HTTP_PROXY" not in env
    assert "/private/hermes" not in env.values() and "private" not in env.values()


@pytest.fixture
def fake_install(tmp_path):
    root = tmp_path / "hermes-source"
    root.mkdir()
    (root / "hermes_bootstrap.py").write_text("")
    (root / "hermes_state_registry.py").write_text("class DB:\n def close(self): pass\ndef acquire(): return DB()\n")
    (root / "tools").mkdir()
    (root / "tools/__init__.py").touch()
    (root / "tools/process_registry.py").write_text("class Registry:\n def list_sessions(self): return []\nprocess_registry=Registry()\n")
    (root / "run_agent.py").write_text('''import json, os
from pathlib import Path
from urllib.request import Request, urlopen
class AIAgent:
 def __init__(self, **kwargs): self.kwargs=kwargs
 def run_conversation(self, prompt, **kwargs):
  request=Request(self.kwargs['base_url']+'/chat/completions',data=json.dumps({'model':self.kwargs['model'],'messages':[{'role':'user','content':prompt}],'max_tokens':self.kwargs['max_tokens']}).encode(),headers={'Content-Type':'application/json'})
  with urlopen(request) as response: response.read()
  output=Path(self.kwargs['cwd'])/'deliverables'
  (output/'clean.csv').write_text('id,group,score\\na,A,10\\n')
  (output/'summary.json').write_text('{}')
  return {'completed':True,'final_response':'[clean.csv](deliverables/clean.csv) [summary.json](deliverables/summary.json)','turn_exit_reason':'final_response'}
 def close(self): pass
 def hard_interrupt(self, *a, **kw): pass
''')
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return root, revision


def config_for(fake_install, upstream):
    root, revision = fake_install
    return HermesRunConfig(str(root), sys.executable, upstream.url, "test-model", expected_revision=revision)


def test_dry_run_keeps_only_public_case_inputs_and_rejects_changed_source(fake_install, upstream, tmp_path):
    hermes = config_for(fake_install, upstream)
    root = tmp_path / "trial"
    snapshot = dry_run_hermes_trial("fasta", root, EvalSuiteConfig(), hermes)
    assert snapshot["entry"] == "hermes-sdk"
    assert {p.name for p in (root / "workspace/inputs").iterdir()} == {"sequences.fasta"}
    assert not (root / "hermes-started.json").exists()
    assert not (root / "profile/state.db").exists()
    assert not upstream.calls
    (fake_install[0] / ".env").write_text("KEY=should-not-be-read")
    with pytest.raises(ValueError, match="ambient credential"):
        hermes.inspect()


def test_actual_worker_protocol_with_fake_hermes_and_fake_provider(fake_install, upstream, tmp_path):
    hermes = config_for(fake_install, upstream)
    root = tmp_path / "trial"
    result = run_hermes_trial("table_clean", root, EvalSuiteConfig(trial_wall_seconds=20), hermes, api_key="test-secret")
    assert result["production_status"] == "succeeded"
    assert result["answer_completion_passed"] is True
    assert result["declared_verification"]["status"] == "unchecked"
    assert result["total_tokens"] == 7
    assert result["provider_attempts"] == 1
    assert len(result["artifacts"]) == 2
    assert result["cleanup_status"]["agent_closed"] is True
    assert result["cleanup_status"]["remaining_pids"] == []
    assert "test-secret" not in (root / "hermes-worker.log").read_text()
    assert "test-secret" not in (root / "profile/config.yaml").read_text()
    with pytest.raises(FileExistsError):
        run_hermes_trial("table_clean", root, EvalSuiteConfig(trial_wall_seconds=20), hermes, api_key="test-secret")
    assert len(upstream.calls) == 1


def test_worker_records_failure_and_cleans_persisted_jobs(monkeypatch):
    monkeypatch.setenv("HERMES_HARNESS_BASE_URL", "http://127.0.0.1:1/v1")
    closed = []
    running = [{"session_id": "persistent-job", "status": "running", "persist_on_release": True}]
    class Registry:
        def list_sessions(self): return running
        def kill_process(self, identity, **kwargs):
            closed.append(identity)
            running.clear()
    class Agent:
        def __init__(self, **kwargs): pass
        def run_conversation(self, *_args, **_kwargs): raise RuntimeError("private exception body")
        def close(self): closed.append("agent")
    db = SimpleNamespace(close=lambda: closed.append("database"))
    request = {"model": "m", "max_iterations": 3, "max_tokens": 4096, "toolsets": ["terminal"],
               "session_id": "s", "active_seconds": 10, "workspace": "/tmp", "prompt": "public"}
    result = execute(request, agent_factory=Agent, db_factory=lambda: db, registry=Registry())
    assert result["failed"] is True
    assert result["error_type"] == "RuntimeError"
    assert "private" not in json.dumps(result)
    assert closed == ["persistent-job", "agent", "database"]
    assert result["cleanup"]["registry_remaining"] == 0


def test_supervisor_interrupt_cleans_owned_process(monkeypatch, tmp_path):
    from app.services.harness_eval import hermes_adapter
    root = tmp_path / "trial"
    (root / "workspace").mkdir(parents=True)
    created = []
    original = subprocess.Popen
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        return process
    monkeypatch.setattr(hermes_adapter.subprocess, "Popen", spawn)
    def interrupt(_event):
        raise KeyboardInterrupt()
    result = hermes_adapter._supervise([sys.executable, "-c", "import time; time.sleep(30)"], root,
                                      isolated_environment(root, "http://127.0.0.1:1", "model"),
                                      time.monotonic()+5, time.monotonic()+10, interrupt)
    assert result["supervisor_error"] == "KeyboardInterrupt"
    assert len(created) == 1 and created[0].poll() is not None
    assert result["remaining_pids"] == []
    saved = json.loads((root / "hermes-process-state.json").read_text())
    assert saved["terminal"] is True
    assert saved["worker"]["pid"] == created[0].pid
    assert saved["worker"]["create_time"] > 0


def test_normal_exit_also_cleans_detached_child(tmp_path):
    from app.services.harness_eval import hermes_adapter
    import psutil
    root = tmp_path / "trial"
    (root / "workspace").mkdir(parents=True)
    code = ("import subprocess,sys,time; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True); "
            "Path('child.pid').write_text(str(p.pid)); time.sleep(.4)")
    result = hermes_adapter._supervise([sys.executable, "-c", code], root,
                                      isolated_environment(root, "http://127.0.0.1:1", "model"),
                                      time.monotonic()+5, time.monotonic()+10, None)
    assert result["worker_returncode"] == 0 and result["remaining_pids"] == []
    child_pid = int((root / "workspace/child.pid").read_text())
    assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE


def test_recovery_without_durable_identity_never_claims_clean(tmp_path):
    from app.services.harness_eval.hermes_adapter import recover_hermes_processes
    (tmp_path / "hermes-started.json").write_text("{}")
    (tmp_path / "result.json").write_text('{"production_status":"succeeded"}')
    result = recover_hermes_processes(tmp_path)
    assert result["status"] == "cleanup_unverified" and result["cleanup_verified"] is False


def test_recovery_does_not_kill_recycled_pid(monkeypatch, tmp_path):
    import psutil
    from app.services.harness_eval.hermes_adapter import recover_hermes_processes
    from app.services.harness_eval.hermes_recovery import write_process_state
    process = SimpleNamespace(pid=12345, create_time=lambda: 200)
    monkeypatch.setattr(psutil, "Process", lambda pid: process)
    write_process_state(tmp_path, {"schema_version": 1, "worker": {"pid": 12345, "create_time": 100},
                                  "children": [], "terminal": False, "elapsed_seconds": 3})
    result = recover_hermes_processes(tmp_path)
    assert result["cleanup_verified"] is True and result["recovered_processes"] == 0
    assert json.loads((tmp_path / "hermes-process-state.json").read_text())["terminal"] is True


def test_existing_result_recovery_checks_terminal_state_and_preserves_result(tmp_path):
    from app.services.harness_eval.hermes_adapter import recover_hermes_processes
    from app.services.harness_eval.hermes_recovery import write_process_state
    root = tmp_path / "trial"
    root.mkdir()
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    import psutil
    owner = psutil.Process(process.pid)
    record = {"pid": owner.pid, "create_time": owner.create_time()}
    process.wait(timeout=5)
    original = '{"production_status":"succeeded","total_tokens":7}'
    (root / "result.json").write_text(original)
    write_process_state(root, {"schema_version": 1, "worker": record, "children": [], "terminal": True, "elapsed_seconds": 2})
    recovered = recover_hermes_processes(root)
    assert recovered["cleanup_verified"] is True and recovered["result_present"] is True
    assert recovered["elapsed_seconds"] == 2
    assert (root / "result.json").read_text() == original


def test_live_worker_can_be_recovered_from_persisted_identity(tmp_path):
    import psutil
    from app.services.harness_eval.hermes_adapter import recover_hermes_processes
    from app.services.harness_eval.hermes_recovery import write_process_state
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    owner = psutil.Process(process.pid)
    try:
        write_process_state(tmp_path, {"schema_version": 1, "worker": {"pid": owner.pid, "create_time": owner.create_time()},
                                       "children": [], "terminal": False, "elapsed_seconds": 1})
        result = recover_hermes_processes(tmp_path)
        assert result["cleanup_verified"] is True and result["recovered_processes"] == 1
        assert result["remaining_pids"] == []
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_token_limited_trial_requires_shared_hook(fake_install, upstream, tmp_path):
    cfg = EvalSuiteConfig(per_trial_token_stop_threshold=1000)
    with pytest.raises(ValueError, match="event_hook"):
        run_hermes_trial("table_clean", tmp_path / "trial", cfg, config_for(fake_install, upstream), api_key="key")
    assert not upstream.calls


def test_client_write_failure_does_not_discard_received_usage(monkeypatch):
    import io
    from app.services.harness_eval import hermes_gateway
    payload = {"model": "test-model", "max_tokens": 4096}
    body = json.dumps(payload).encode()
    response = io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}).encode())
    response.status = 200
    response.headers = {"Content-Type": "application/json"}
    class BrokenOutput:
        def write(self, _): raise BrokenPipeError()
    handler = SimpleNamespace(path="/v1/chat/completions", headers={"Content-Length": len(body)}, rfile=io.BytesIO(body),
                              wfile=BrokenOutput(), send_response=lambda *_: None, send_header=lambda *_: None,
                              end_headers=lambda: None)
    monkeypatch.setattr(hermes_gateway, "build_opener", lambda *_: SimpleNamespace(open=lambda *_args, **_kwargs: response))
    relay = ObservedGateway("http://localhost:1/v1", "key", "test-model", attempt_limit=1, deadline=time.monotonic()+2)
    relay._forward_request(handler)
    assert relay.accounting()["total_tokens"] == 7
    assert relay.events[-1]["error_type"] == "BrokenPipeError"


def test_gateway_waits_for_handler_receipt_before_returning(upstream):
    entered = threading.Event()
    finished = threading.Event()
    failures = []
    def hook(event):
        if event.get("response_complete"):
            entered.set()
            time.sleep(.7)
            finished.set()
    relay = ObservedGateway(upstream.url, "key", "test-model", attempt_limit=2, deadline=time.monotonic()+4, event_hook=hook)
    with relay:
        def request():
            try:
                post(relay.url)
            except Exception as exc:
                failures.append(type(exc).__name__)
        thread = threading.Thread(target=request)
        thread.start()
        assert entered.wait(timeout=2)
    thread.join(timeout=2)
    assert finished.is_set()
    assert relay.handler_cleanup_verified is True
    assert relay.accounting()["total_tokens"] == 7
    assert not relay._handlers

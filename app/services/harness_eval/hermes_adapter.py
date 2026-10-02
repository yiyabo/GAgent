"""Pinned, isolated Hermes SDK entry for the public harness workflow corpus.

This is a controller-level comparison, not a claim of HTTP/UI or feature parity.
The private oracle belongs to the caller and is never imported by the worker.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from .fixtures import CORPUS_VERSION, prepare
from .hermes_gateway import ObservedGateway
from .conversation import file_hash
from .hermes_recovery import identity, recover_hermes_processes, write_process_state

PINNED_HERMES_REVISION = "663362680b6ffa4fbffeb58f6682564239a1953b"


@dataclass(frozen=True)
class HermesRunConfig:
    source_root: str
    python_executable: str
    base_url: str
    model: str
    expected_revision: str = PINNED_HERMES_REVISION
    toolsets: tuple[str, ...] = ("terminal", "file", "code_execution", "todo")

    def inspect(self):
        root = Path(self.source_root).resolve()
        def git(*args):
            return subprocess.check_output(["git", "-c", "gc.auto=0", *args], cwd=root, text=True, timeout=10).strip()
        revision = git("rev-parse", "HEAD")
        if revision != self.expected_revision:
            raise ValueError("Hermes revision changed; freeze a new comparison first")
        if git("status", "--porcelain", "--untracked-files=no"):
            raise ValueError("Hermes tracked source is dirty")
        if any((root / name).exists() for name in (".env", ".op.env")):
            raise ValueError("Hermes source contains ambient credential files; use an isolated source checkout")
        if not (root / "run_agent.py").is_file() or not Path(self.python_executable).is_file():
            raise ValueError("Hermes source or Python runtime is unavailable")
        if not self.model.strip() or not self.toolsets:
            raise ValueError("explicit model and toolsets are required")
        allowed = {"terminal", "file", "code_execution", "todo", "skills", "memory", "session_search"}
        if set(self.toolsets) - allowed:
            raise ValueError("toolset is outside the frozen local comparison surface")
        python_version = subprocess.check_output([self.python_executable, "-I", "--version"], text=True, timeout=10).strip()
        lock_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                       for name in ("pyproject.toml", "uv.lock", "pm/lock.json") if (root / name).is_file()}
        return {"revision": revision, "source_root": str(root), "python_executable": self.python_executable,
                "python_version": python_version, "dependency_lock_hashes": lock_hashes,
                "api_mode": "chat_completions", "provider_adapter": "custom", "model": self.model,
                "toolsets": list(self.toolsets), "scope": "AIAgent.run_conversation (SDK controller)",
                "differences": ["Hermes terminal/file tools versus GAgent code kernel and publisher",
                                "No Hermes plan DAG or GAgent publication receipt is invented",
                                "Isolated empty profile; background review and automatic titles disabled",
                                "Only explicit local toolsets; no browser, live web, delegation or personal Skills"]}


def isolated_environment(root, base_url, model, parent=None):
    """Allowlist non-secret host settings; credentials stay in the relay process."""
    parent = os.environ if parent is None else parent
    env = {key: parent[key] for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR") if key in parent}
    env.update(HOME=str(root / "home"), HERMES_HOME=str(root / "profile"),
               XDG_CONFIG_HOME=str(root / "home/.config"), XDG_CACHE_HOME=str(root / "home/.cache"),
               TMPDIR=str(root / "tmp"), TMP=str(root / "tmp"), TEMP=str(root / "tmp"),
               HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               HERMES_HARNESS_BASE_URL=base_url, HERMES_INFERENCE_MODEL=model,
               OPENAI_BASE_URL=base_url, OPENAI_API_KEY="isolated-evaluation",
               HERMES_INFERENCE_PROVIDER="custom", HERMES_YOLO_MODE="1",
               TERMINAL_ENV="local", TERMINAL_CWD=str(root / "workspace"),
               HERMES_SESSION_SOURCE="harness-eval", NO_PROXY="127.0.0.1,localhost")
    return env


def dry_run_hermes_trial(case_id, root, cfg, hermes):
    """Freeze non-secret configuration and public inputs; does not launch Hermes."""
    cfg.validate()
    root = Path(root).resolve()
    installation = hermes.inspect()
    root.mkdir(parents=True, exist_ok=True)
    snapshot = {"case": case_id, "entry": "hermes-sdk", "corpus_version": CORPUS_VERSION,
                "installation": installation, "limits": {"wall_seconds": cfg.trial_wall_seconds,
                "close_reserve_seconds": cfg.close_reserve_seconds, "max_tokens": cfg.output_max_tokens,
                "max_iterations": cfg.native_max_iterations, "provider_attempt_limit": cfg.provider_attempt_limit,
                "campaign_wall_semantics": bool(getattr(cfg, "campaign_root", None)),
                "cleanup_grace_seconds": getattr(cfg, "cleanup_grace_seconds", 10)},
                "route_fingerprint": hashlib.sha256(hermes.base_url.rstrip("/").encode()).hexdigest()}
    config_path = root / "hermes-config.json"
    if config_path.exists():
        if json.loads(config_path.read_text()) != snapshot:
            raise ValueError("Hermes trial configuration changed")
        return snapshot
    for name in ("workspace", "workspace/inputs", "workspace/deliverables", "home", "profile/skills", "tmp"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "profile/.no-bundled-skills").touch()
    prepare(case_id, root / "workspace/inputs")
    config_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    return snapshot


def _profile(root, model, relay_url, cfg):
    # JSON is YAML-compatible. All routes are explicit, including compression;
    # no saved provider/model fallbacks or personal memory providers are inherited.
    route = {"provider": "custom", "model": model, "base_url": relay_url,
             "api_key": "isolated-evaluation", "api_mode": "chat_completions"}
    profile = {"model": {**route, "default": model},
               "agent": {"max_turns": cfg.native_max_iterations, "max_tokens": cfg.output_max_tokens},
               "terminal": {"backend": "local", "cwd": str(root / "workspace")},
               "fallback_providers": [], "mcp_servers": {}, "plugins": {},
               "auxiliary": {"compression": route, "session_search": route,
                             "title_generation": {"enabled": False, "model_upgrade_enabled": False}}}
    (root / "profile/config.yaml").write_text(json.dumps(profile, indent=2) + "\n")


def _alive(process):
    import psutil
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _cleanup_descendants(known, timeout=2):
    import psutil
    live, unknown = [], set()
    for process in known.values():
        try:
            if _alive(process):
                live.append(process)
        except psutil.Error:
            unknown.add(process.pid)
    for process in live:
        try:
            process.kill()  # psutil checks PID creation time before signalling.
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            unknown.add(process.pid)
    _, remaining = psutil.wait_procs(live, timeout=max(0, timeout))
    for process in remaining:
        try:
            if _alive(process):
                unknown.add(process.pid)
        except psutil.Error:
            unknown.add(process.pid)
    return sorted(unknown)


def _supervise(command, root, env, active_deadline, hard_deadline, event_hook):
    """Teardown also runs when startup, observation, wait or the caller fails."""
    import psutil
    process = owner = None
    known = {}
    timed_out = forced = False
    error = None
    remaining = []
    last_tick = 0
    supervisor_started = time.monotonic()
    worker_identity = None
    tracking_failed = False
    def checkpoint(terminal=False, phase="running", cleanup=None):
        write_process_state(root, {"schema_version": 1, "worker": worker_identity,
                                  "children": [{"pid": pid, "create_time": created} for pid, created in known],
                                  "elapsed_seconds": round(time.monotonic() - supervisor_started, 3),
                                  "observed_at": time.time(), "terminal": terminal, "phase": phase,
                                  "cleanup_status": cleanup})
    try:
        checkpoint(phase="launching")
        with (root / "hermes-worker.log").open("w") as log:
            process = subprocess.Popen(command, cwd=root / "workspace", env=env,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            owner = psutil.Process(process.pid)
            worker_identity = identity(owner)
            checkpoint()
            while process.poll() is None:
                try:
                    for child in owner.children(recursive=True):
                        known[(child.pid, child.create_time())] = child
                except psutil.NoSuchProcess:
                    pass
                now = time.monotonic()
                if now - last_tick >= .5:
                    checkpoint()
                    if event_hook:
                        event_hook({"kind": "heartbeat", "monotonic": now})
                    last_tick = now
                if now >= active_deadline and not timed_out:
                    timed_out = True
                    process.terminate()
                if now >= hard_deadline:
                    forced = True
                    process.kill()
                    break
                time.sleep(.1)
            process.wait(timeout=2)
    except BaseException as exc:
        error = type(exc).__name__
    finally:
        if owner:
            try:
                for child in owner.children(recursive=True):
                    known[(child.pid, child.create_time())] = child
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                tracking_failed = True
                error = error or type(exc).__name__
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=max(0, min(2, hard_deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                forced = True
                process.kill()
                process.wait(timeout=2)
        remaining = _cleanup_descendants(known, timeout=min(2, hard_deadline - time.monotonic()))
    result = {"worker_returncode": process.returncode if process else None,
            "supervisor_timeout": timed_out, "forced_kill": forced, "remaining_pids": remaining,
            "supervisor_error": error, "tracking_scope": "observed_descendants_with_pid_creation_time"}
    verified = not tracking_failed and not remaining and (process is None or (worker_identity is not None and process.poll() is not None))
    checkpoint(terminal=verified, phase="launch_failed" if process is None else "terminal" if verified else "cleanup_incomplete", cleanup=result)
    result["cleanup_verified"] = verified
    return result


def run_hermes_trial(case_id, root, cfg, hermes, *, api_key, event_hook=None):
    """Run once; callback sees each allowed physical attempt before forwarding.

    ``event_hook`` may reject an attempt by raising. Neither its exception text nor
    the key is written. Already-started trial roots are never silently reexecuted.
    """
    import psutil  # noqa: F401 — fail before launch if process ownership tracking is unavailable
    if event_hook is None and (getattr(cfg, "campaign_root", None) or getattr(cfg, "per_trial_token_stop_threshold", None) is not None):
        raise ValueError("campaign/token-limited Hermes runs require the shared accounting event_hook")
    if not api_key:
        raise ValueError("explicit gateway credential required")
    root = Path(root).resolve()
    snapshot = dry_run_hermes_trial(case_id, root, cfg, hermes)
    with (root / "hermes-started.json").open("x") as handle:
        json.dump({"started_at": time.time()}, handle)
    case = prepare(case_id, root / "workspace/inputs")
    input_hash_before=file_hash(root/'workspace/inputs/input.csv') if case.get('turns') else None
    output = root / "workspace/deliverables"
    session = "harness-" + uuid4().hex
    prompt = (case["prompt"] + "\nUse only the provided local inputs. Deliver all required files and include their links in the answer. "
              "JSON values must be numbers. Group summaries use {group: {count: number, mean/median: number}}."
              f"\nRead inputs from: {root / 'workspace/inputs'}\nWrite all required output files in: {output}")
    request = {"source_root": str(Path(hermes.source_root).resolve()), "model": hermes.model,
               "session_id": session, "toolsets": list(hermes.toolsets), "prompt": prompt,
               "max_iterations": cfg.native_max_iterations, "max_tokens": cfg.output_max_tokens,
               "active_seconds": cfg.trial_wall_seconds if getattr(cfg, "campaign_root", None) else cfg.trial_wall_seconds - cfg.close_reserve_seconds,
               "workspace": str(root / "workspace")}
    if case.get('turns'):
        request['turns']=[{'prompt':prompt if i==0 else turn['prompt']+f'\nInputs: {root / "workspace/inputs"}\nOutputs: {output}',
                          'outputs':turn['outputs']} for i,turn in enumerate(case['turns'])]
        request['output_root']=str(output)
    request_path = root / "hermes-request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    started = time.monotonic()
    active_deadline = started + request["active_seconds"]
    hard_deadline = (active_deadline + getattr(cfg, "cleanup_grace_seconds", 10)
                     if getattr(cfg, "campaign_root", None) else started + cfg.trial_wall_seconds)
    with ObservedGateway(hermes.base_url, api_key, hermes.model, attempt_limit=cfg.provider_attempt_limit,
                         deadline=active_deadline, journal=root / "hermes-calls.jsonl", event_hook=event_hook,
                         max_tokens=cfg.output_max_tokens, cleanup_deadline=hard_deadline) as relay:
        _profile(root, hermes.model, relay.url, cfg)
        command = [hermes.python_executable, "-I", str(Path(__file__).with_name("hermes_worker.py")), str(request_path)]
        supervision = _supervise(command, root, isolated_environment(root, relay.url, hermes.model),
                                 active_deadline, hard_deadline, event_hook)
    result_path = root / "hermes-result.json"
    try:
        raw = json.loads(result_path.read_text()) if result_path.exists() else {}
    except (ValueError, OSError):
        raw = {}
    cleanup = {**(raw.get("cleanup") or {}), **supervision, "gateway_handlers_closed": relay.handler_cleanup_verified}
    completed = bool(raw.get("completed") is True and not any(raw.get(k) for k in ("failed", "partial", "interrupted"))
                     and cleanup["worker_returncode"] == 0 and not cleanup["supervisor_timeout"] and not cleanup["remaining_pids"]
                     and not cleanup["supervisor_error"]
                     and cleanup.get("agent_closed") and cleanup.get("registry_remaining") == 0 and not cleanup.get("errors")
                     and cleanup["gateway_handlers_closed"] and cleanup["cleanup_verified"])
    answer = raw.get("final_response") or ""
    artifacts = []
    for name in case["outputs"]:
        path = output / name
        if path.is_file() and output in path.resolve().parents:
            artifacts.append({"name": name, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "size": path.stat().st_size})
    error = cleanup["supervisor_error"] or raw.get("error_type") or ("worker_stopped_without_report" if not raw else None)
    if relay.observer_error:
        error = "observation_failed:" + relay.observer_error
        completed = False
    status = "succeeded" if completed else "cancelled" if cleanup["supervisor_timeout"] or raw.get("interrupted") else "failed"
    report = {"case": case_id, "entry": "hermes-sdk", "entry_implementation": "AIAgent.run_conversation",
              "input_unchanged": file_hash(root/'workspace/inputs/input.csv')==input_hash_before if input_hash_before else None,
              "session_id": session, "revision": hermes.expected_revision, "model": hermes.model, "provider": "custom",
              "production_status": status, "termination_reason": raw.get("turn_exit_reason") or error or ("supervisor_timeout" if cleanup["supervisor_timeout"] else None),
              "declared_verification": {"status": "unchecked", "source": "Hermes has no GAgent OutputSpec verdict"},
              "answer": answer, "answer_completion_passed": completed and all(n in answer for n in case["outputs"]) and len(artifacts) == len(case["outputs"]),
              "artifacts": artifacts, "turn_results":raw.get('turn_results',[]), "output_root": str(output), "duration_seconds": round(time.monotonic() - started, 3),
              "external_launches": 0, "worker_process_launches": 1, "call_events": relay.events, **relay.accounting(), "cost_usd": None,
              "cleanup_status": cleanup, "error": error, "adapter_metadata": snapshot["installation"]}
    (root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report

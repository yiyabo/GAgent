"""Real PTY/socket round-trip; Docker creation is replaced by a local runner."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.terminal.sandbox_pty_backend import SandboxPTYBackend


@pytest.mark.asyncio
async def test_terminal_write_resize_interrupt_and_cleanup(monkeypatch, tmp_path):
    from app.services.terminal import sandbox_pty_backend as module
    root = tmp_path / 'runtime';cwd = root / 'session_demo' / 'raw_files' / 'task_2'
    cwd.mkdir(parents=True)
    monkeypatch.setenv('APP_RUNTIME_ROOT', str(root))
    monkeypatch.delenv('HOST_RUNTIME_ROOT', raising=False)
    monkeypatch.setenv('CODE_MODE_RPC_BIND', '127.0.0.1')
    monkeypatch.setenv('CODE_MODE_RPC_ADVERTISE', '127.0.0.1')
    captured = {}
    processes = []
    def docker(argv, **kwargs):
        if argv[1] == 'run':
            captured['argv'] = argv
            env = os.environ.copy()
            for i, arg in enumerate(argv):
                if arg == '-e':
                    key, value = argv[i+1].split('=', 1);env[key] = value
            proc = subprocess.Popen([sys.executable, argv[-1]], cwd=cwd, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(proc)
            return subprocess.CompletedProcess(argv, 0, 'synthetic-container', '')
        assert argv[:3] == ['docker', 'rm', '-f']
        for proc in processes:
            proc.wait(timeout=5)
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(module.subprocess, 'run', docker)
    backend = SandboxPTYBackend();backend.session_id = 'session_demo'
    async def until(token):
        async def collect():
            output = b''
            while token not in output:
                chunk = await backend.read()
                assert chunk, output
                output += chunk
            return output
        return await asyncio.wait_for(collect(), 8)
    try:
        await backend.spawn(cwd=str(cwd), exec_args=['/bin/bash'])
        assert not any('passwd' in arg for arg in captured['argv'])
        assert f'{root}/session_demo:{root}/session_demo:ro' in captured['argv']
        await backend.write(b"printf 'real_%s\\n' pty_ok\n")
        assert b'real_pty_ok' in await until(b'real_pty_ok')
        await backend.resize(101, 37)
        await backend.write(b'stty size\n')
        assert b'37 101' in await until(b'37 101')
        await backend.write(b'sleep 30\n')
        await asyncio.sleep(.1)
        await backend.write(b'\x03')
        await backend.write(b"printf 'after_%s\\n' interrupt\n")
        assert b'after_interrupt' in await until(b'after_interrupt')
    finally:
        await backend.terminate()
        for proc in processes:
            if proc.poll() is None:
                proc.kill();proc.wait()
    assert backend.is_closed and all(p.poll() is not None for p in processes)


@pytest.mark.asyncio
async def test_create_failure_releases_channel_and_attempts_container_cleanup(monkeypatch, tmp_path):
    from app.services.terminal import sandbox_pty_backend as module
    monkeypatch.delenv('HOST_RUNTIME_ROOT', raising=False)
    calls = []
    def docker(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if argv[1] == 'run' else 0, '', 'fixture create failure')
    monkeypatch.setattr(module.subprocess, 'run', docker)
    backend = SandboxPTYBackend()
    with pytest.raises(RuntimeError, match='fixture create failure'):
        await backend.spawn(cwd=str(tmp_path))
    assert backend.channel is None and backend.is_closed
    assert calls[-1][:3] == ['docker', 'rm', '-f']


@pytest.mark.asyncio
async def test_task_terminal_binding_uses_trusted_task_workspace(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from tool_box.context import ToolContext
    from tool_box.tools_impl import terminal_session as module
    manager = SimpleNamespace(ensure_session_for_chat=AsyncMock(return_value=SimpleNamespace(
        terminal_id='fixture', session_id='demo', mode='sandbox', state='active', cwd=str(tmp_path))))
    monkeypatch.setattr(module, 'terminal_session_manager', manager)
    await module.terminal_session_handler('ensure', tool_context=ToolContext(session_id='demo', work_dir=str(tmp_path)))
    manager.ensure_session_for_chat.assert_awaited_once_with('demo', mode='sandbox', work_dir=str(tmp_path))

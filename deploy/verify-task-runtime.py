"""Isolated real-container smoke: upstream read, output write, PTY, cleanup.

Run through stdin in the app container. Uses a fresh session directory, no
model requests, no registered Plan or user task, and removes its own fixtures.
"""
import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid


async def main():
    from app.services.session_paths import get_runtime_session_dir
    session = 'runtime_probe_' + uuid.uuid4().hex
    root = get_runtime_session_dir(session, create=True)
    os.environ['TERMINAL_AUDIT_ROOT'] = str(root / 'scratch' / 'audit')
    os.environ['EXECUTION_WORKSPACES_ROOT'] = str(root / 'workspaces')
    from app.llm import LLMClient
    def forbidden(*args, **kwargs):
        raise AssertionError('Runtime smoke must not call a model')
    for method in ('chat', 'chat_async', 'stream_chat', 'stream_chat_async', 'stream_chat_with_tools_async'):
        setattr(LLMClient, method, forbidden)
    from tool_box.context import ToolContext
    from tool_box.tools_impl.execute_code.tool import execute_code_handler
    from tool_box.tools_impl.execute_code.kernel import shutdown_kernels_for_session
    from tool_box.tools_impl.terminal_session import terminal_session_handler, terminal_session_manager
    upstream = root / 'raw_files' / 'task_1' / 'input.jsonl'
    upstream.parent.mkdir(parents=True)
    upstream.write_text(''.join(json.dumps({'value': n}) + '\n' for n in range(5)))
    task = root / 'raw_files' / 'task_2';task.mkdir()
    context = ToolContext(session_id=session, work_dir=str(task), owner_id='runtime-smoke')
    terminal = None
    try:
        code = (
            'import json\nfrom pathlib import Path\n'
            f'values=[json.loads(line)["value"] for line in Path({str(upstream)!r}).read_text().splitlines()]\n'
            'Path("results").mkdir(exist_ok=True)\n'
            'Path("results/summary.json").write_text(json.dumps({"sum":sum(values)}))\n'
            'print("UPSTREAM_SUM",sum(values))\n'
            f'try:\n Path({str(upstream)!r}).write_text("bad")\n raise AssertionError("upstream should be read-only")\n'
            'except (PermissionError, OSError):\n print("UPSTREAM_READ_ONLY")\n'
        )
        result = await execute_code_handler(code=code, tool_context=context)
        assert result.get('success'), result
        assert json.loads((task / 'results/summary.json').read_text()) == {'sum': 10}
        terminal = await terminal_session_handler(operation='ensure', tool_context=context)
        assert terminal['cwd'] == str(task), terminal
        terminal_output = task / 'results/terminal-read.json'
        snippet = (f'import json; from pathlib import Path; n=sum(json.loads(x)["value"] for x in open({str(upstream)!r})); '
                   f'Path({str(terminal_output)!r}).write_text(json.dumps({{"sum":n}})); print("TERMINAL_"+"SUM",n)')
        command = 'python -c ' + shlex.quote(snippet) + '\n'
        outcome = await terminal_session_handler(operation='write', terminal_id=terminal['terminal_id'],
                                                  data=command, tool_context=context)
        # PTY idle/echo is not command completion; wait for the command's own receipt.
        async def wait_for_output():
            while True:
                try:
                    return json.loads(terminal_output.read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
                await asyncio.sleep(.05)
        assert await asyncio.wait_for(wait_for_output(), 15) == {'sum': 10}, outcome
        assert upstream.read_text().splitlines()[0] == '{"value": 0}'
        print(json.dumps({'upstream_read': True, 'upstream_read_only': True, 'output_written': True,
                          'terminal_workdir': terminal['cwd'], 'terminal_read': True, 'model_calls': 0}))
    finally:
        if terminal:
            await terminal_session_manager.close_session(terminal['terminal_id'])
        shutdown_kernels_for_session(session)
        leftovers = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label=gagent.session=' + session], text=True).strip()
        assert not leftovers, 'Runtime smoke left a container: ' + leftovers
        shutil.rmtree(root)
        print(json.dumps({'cleanup': 'complete', 'session': session}))


asyncio.run(main())

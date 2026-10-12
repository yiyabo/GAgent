"""Exercise the gate and shell entrypoint without talking to a Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.ops.restart_gate import probe


def database(path, table, statuses=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(f'CREATE TABLE {table}(status TEXT)')
        conn.executemany(f'INSERT INTO {table} VALUES(?)', [(s,) for s in statuses])


def test_gate_includes_system_jobs_and_unknown_is_not_idle(tmp_path):
    database(tmp_path/'main/plan_registry.db', 'chat_runs')
    database(tmp_path/'plans/plan_1.sqlite', 'decomposition_jobs', ['succeeded'])
    database(tmp_path/'jobs/system_jobs.sqlite', 'decomposition_jobs', ['running'])
    assert probe(tmp_path)['state'] == 'busy'
    (tmp_path/'plans/broken.sqlite').write_text('not a database')
    state = probe(tmp_path)
    assert state['state'] == 'unknown' and state['active_plan_jobs'] is None
    assert state['errors'][0]['database'] == 'broken.sqlite'


@pytest.fixture
def shell_stack(tmp_path):
    root = Path(__file__).resolve().parents[3]
    (tmp_path/'deploy').mkdir()
    (tmp_path/'app/ops').mkdir(parents=True)
    (tmp_path/'bin').mkdir()
    (tmp_path/'runtime').mkdir()
    shutil.copy2(root/'deploy/gagentctl', tmp_path/'deploy/gagentctl')
    shutil.copy2(root/'app/ops/restart_gate.py', tmp_path/'app/ops/restart_gate.py')
    db = tmp_path/'db'
    database(db/'main/plan_registry.db', 'chat_runs', ['running'])
    (tmp_path/'deploy/.env').write_text(f'DB_ROOT={db}\nGAGENT_RUNTIME_DIR={tmp_path}/runtime\n')
    fake = tmp_path/'bin/docker'
    fake.write_text(f'#!{sys.executable}\n' + '''import os,sys,subprocess
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['DOCKER_TEST_CALLS'],'a') as f:f.write(' '.join(args)+'\\n')
if 'ps' in args:
    if os.getenv('DOCKER_TEST_PS_FAIL'):sys.exit(4)
    print('app');sys.exit(0)
if 'exec' in args:
    sys.exit(subprocess.run([sys.executable,'-'],input=sys.stdin.read(),text=True).returncode)
sys.exit(0)
''')
    fake.chmod(0o755)
    env={**os.environ, 'PATH':str(tmp_path/'bin')+os.pathsep+os.environ['PATH'],
         'DOCKER_TEST_CALLS':str(tmp_path/'calls')}
    return tmp_path, env


def test_down_refuses_busy_stack_and_force_is_reachable_and_audited(shell_stack):
    root, env = shell_stack
    script = root/'deploy/gagentctl'
    denied = subprocess.run(['bash',str(script),'down'],env=env,capture_output=True,text=True)
    assert denied.returncode != 0
    assert not any(line.endswith(' down') for line in (root/'calls').read_text().splitlines())
    forced = subprocess.run(['bash',str(script),'down','--force'],env=env,capture_output=True,text=True)
    assert forced.returncode == 0, forced.stderr
    assert any(line.endswith(' down') for line in (root/'calls').read_text().splitlines())
    assert 'force-restart gate_rc=1' in (root/'restarts.log').read_text()


def test_docker_ps_failure_does_not_mean_no_app(shell_stack):
    root, env = shell_stack
    env['DOCKER_TEST_PS_FAIL']='1'
    result = subprocess.run(['bash',str(root/'deploy/gagentctl'),'down'],env=env,capture_output=True,text=True)
    assert result.returncode != 0
    assert 'unknown' in result.stderr
    assert len((root/'calls').read_text().splitlines()) == 1

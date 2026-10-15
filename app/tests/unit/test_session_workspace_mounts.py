from app.ops.workspace_mounts import session_mounts
from app.ops.sandbox import translate_mount


def test_upstream_session_is_readable_but_only_current_task_is_writable(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    task = root / 'session_demo' / 'raw_files' / 'task_2'
    task.mkdir(parents=True)
    monkeypatch.setenv('APP_RUNTIME_ROOT', str(root))
    monkeypatch.setenv('HOST_RUNTIME_ROOT', '/host/runtime')
    mounts = session_mounts(str(task), 'session_demo')
    assert mounts == [(str(root / 'session_demo'), 'ro'), (str(task), 'rw')]
    assert translate_mount(mounts[0][0], mode=mounts[0][1]) == f'/host/runtime/session_demo:{root}/session_demo:ro'
    assert all(str(root) != path for path, _ in mounts)


def test_session_root_workspace_is_not_double_mounted(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_RUNTIME_ROOT', str(tmp_path))
    root = tmp_path / 'session_demo';root.mkdir()
    assert session_mounts(str(root), 'session_demo') == [(str(root), 'rw')]

import os
import sys


def _run_cli_with_args(args):
    # agent_cli has been removed, use cli.main directly
    old_argv = sys.argv[:]
    try:
        sys.argv = ["cli.main"] + list(args)
        from cli.main import main

        main()
    finally:
        sys.argv = old_argv


def test_generate_index_overview_stage_brief_and_dependencies(tmp_path, monkeypatch):
    # Work in temp dir; DB and INDEX.md live here
    monkeypatch.chdir(tmp_path)

    from app.database import init_db
    from app.repository.tasks import SqliteTaskRepository
    from app.services.index_root import generate_index

    init_db()
    repo = SqliteTaskRepository()

    # PlanA with 3 tasks (one done) and a requires cycle
    a1 = repo.create_task("[PlanA] Task1", status="pending", priority=10)
    a2 = repo.create_task("[PlanA] Task2", status="done", priority=20)
    a3 = repo.create_task("[PlanA] Task3", status="pending", priority=30)

    repo.create_link(a1, a2, "requires")
    repo.create_link(a2, a3, "requires")
    repo.create_link(a3, a1, "requires")  # cycle

    # Snapshot for last_updated presence
    repo.upsert_task_context(a1, combined="x", sections=[], meta={}, label="latest")

    # PlanB fully done
    b1 = repo.create_task("[PlanB] Alpha", status="done", priority=10)
    b2 = repo.create_task("[PlanB] Beta", status="done", priority=20)

    res = generate_index(repo=repo)
    content = res["content"]

    # Plans Overview table headers
    assert "| Plan | Owner | Stage | Done/Total | Last Updated |" in content

    # Stage computation and counts
    assert "| PlanA | — | Executing | 1/3 |" in content
    assert "| PlanB | — | Complete | 2/2 |" in content

    # Budget sidebar includes index-first priority ordering with hierarchy types
    assert "index → dep:requires → dep:refers → ancestor → retrieved → h_sibling → sibling → manual" in content

    # Dependency summary for PlanA
    assert "## Dependency Summary" in content
    assert "### PlanA" in content
    assert "- requires edges: 3" in content
    assert "- cycles: yes" in content

    # Plan details: Brief & task ordering
    plan_hdr = "### [Plan] PlanA"
    i = content.find(plan_hdr)
    assert i != -1
    # Take the remainder to avoid false matches of '## ' inside the same '### ' line
    sect = content[i:]
    assert "- Brief: Task1; Task2; Task3" in sect
    assert f"  - [#{a1} p=10" in sect
    assert f"  - [#{a2} p=20" in sect


def test_write_index_and_changelog_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    from app.services.index_root import generate_index, write_index

    # Two history entries with increasing timestamps (minutes resolution)
    meta1 = {"generated_at": "2025-08-17 01:00:00", "plans": 1, "tasks_total": 3, "done_total": 1}
    meta2 = {"generated_at": "2025-08-17 01:01:00", "plans": 1, "tasks_total": 4, "done_total": 2}

    write_index("# Project Index\n\n", meta=meta1)
    write_index("# Project Index\n\n", meta=meta2)

    res = generate_index()
    content = res["content"]

    pos2 = content.find("- 2025-08-17 01:01")
    pos1 = content.find("- 2025-08-17 01:00")
    assert pos2 != -1 and pos1 != -1 and pos2 < pos1  # newest first


def test_cli_index_preview_export_and_run_root(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    # Ensure deterministic target path
    monkeypatch.setenv("GLOBAL_INDEX_PATH", "root/INDEX.md")

    from app.database import init_db

    init_db()

    # 1) Preview (dry-run)
    _run_cli_with_args(["--index-preview"])
    out = capsys.readouterr().out
    assert "INDEX preview (resolved path: root/INDEX.md)" in out
    assert "# Project Index" in out

    # 2) Export (dry-run write)
    _run_cli_with_args(["--index-export", "exported.md"])
    with open("exported.md", "r", encoding="utf-8") as f:
        txt = f.read()
    assert "# Project Index" in txt
    # No history yet
    assert not os.path.exists("root/INDEX.md.history.jsonl")

    # 3) Run root (write + history)
    _run_cli_with_args(["--index-run-root"])
    assert os.path.exists("root/INDEX.md")
    hist = os.path.join("root", "INDEX.md.history.jsonl")
    assert os.path.exists(hist)
    with open(hist, "r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    assert len(lines) >= 1

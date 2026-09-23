"""scripts/skill_add.py — structural validation, risk scan, local-dir install."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "skill_add.py"
_spec = importlib.util.spec_from_file_location("skill_add", _SCRIPT)
skill_add = importlib.util.module_from_spec(_spec)
sys.modules["skill_add"] = skill_add
_spec.loader.exec_module(skill_add)

_SANDBOX = Path("runtime/test_skill_add_sandbox")


@pytest.fixture()
def sandbox():
    root = _SANDBOX.resolve()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _make_skill(root: Path, name: str = "demo-skill", *, with_config: bool = True) -> Path:
    skill_dir = root / name
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: demo skill for tests\n---\n\n# Demo\n\n"
        "See https://example.com/docs and run `pip install demo-pkg` first.\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    if with_config:
        (skill_dir / "config.json").write_text(
            '{"version": 1, "category": "generic", "scope": "both", "priority": 10}',
            encoding="utf-8",
        )
    return skill_dir


def test_validate_missing_skill_md(sandbox: Path) -> None:
    empty = sandbox / "empty"
    empty.mkdir()
    problems = skill_add._validate_skill_dir(empty)
    assert any("SKILL.md missing" in p for p in problems)


def test_validate_ok(sandbox: Path) -> None:
    skill_dir = _make_skill(sandbox)
    assert skill_add._validate_skill_dir(skill_dir) == []


def test_risk_report_collects_executables_urls_deps(sandbox: Path) -> None:
    skill_dir = _make_skill(sandbox)
    (skill_dir / "scripts").mkdir(exist_ok=True)
    (skill_dir / "scripts" / "helper.py").write_text("print('hi')\n", encoding="utf-8")
    report = skill_add._collect_risks(skill_dir, "demo-skill", sandbox / "target")
    assert "scripts/helper.py" in report.executables
    assert any("example.com" in url for url in report.urls)
    assert any("pip install demo-pkg" in hint for hint in report.dependency_hints)
    assert report.file_count >= 3


def test_install_local_dir_preserves_structure(sandbox: Path, monkeypatch) -> None:
    skill_dir = _make_skill(sandbox)
    target_root = sandbox / "skills_root"
    monkeypatch.setattr(skill_add, "SKILLS_ROOT", target_root)
    rc = skill_add.main(["--dir", str(skill_dir), "--yes"])
    assert rc == 0
    installed = target_root / "demo-skill"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "references" / "guide.md").is_file()
    assert (installed / "config.json").is_file()


def test_dry_run_writes_nothing(sandbox: Path, monkeypatch) -> None:
    skill_dir = _make_skill(sandbox)
    target_root = sandbox / "skills_root"
    monkeypatch.setattr(skill_add, "SKILLS_ROOT", target_root)
    rc = skill_add.main(["--dir", str(skill_dir), "--dry-run"])
    assert rc == 0
    assert not target_root.exists()


def test_name_conflict_flagged_and_overwritten_with_yes(sandbox: Path, monkeypatch) -> None:
    skill_dir = _make_skill(sandbox)
    target_root = sandbox / "skills_root"
    existing = target_root / "demo-skill"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("old", encoding="utf-8")
    monkeypatch.setattr(skill_add, "SKILLS_ROOT", target_root)
    report = skill_add._collect_risks(skill_dir, "demo-skill", existing)
    assert report.name_conflict is True
    rc = skill_add.main(["--dir", str(skill_dir), "--yes"])
    assert rc == 0
    assert "demo skill for tests" in (existing / "SKILL.md").read_text(encoding="utf-8")


def test_invalid_name_rejected(sandbox: Path, monkeypatch) -> None:
    skill_dir = _make_skill(sandbox)
    monkeypatch.setattr(skill_add, "SKILLS_ROOT", sandbox / "skills_root")
    rc = skill_add.main(["--dir", str(skill_dir), "--name", "../escape", "--yes"])
    assert rc == 2

"""SkillsLoader hot-reload (L2) and lazy-body (L1) tests.

Sandbox: runtime/test_skills_hot_reload_sandbox/ (cleaned by fixture).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from app.services.skills import SkillsLoader

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SANDBOX = PROJECT_ROOT / "runtime" / "test_skills_hot_reload_sandbox"


@pytest.fixture()
def skill_roots():
    project_root = _SANDBOX / "project_skills"
    runtime_root = _SANDBOX / "runtime_skills"
    shutil.rmtree(_SANDBOX, ignore_errors=True)
    project_root.mkdir(parents=True)
    runtime_root.mkdir(parents=True)
    yield project_root, runtime_root
    shutil.rmtree(_SANDBOX, ignore_errors=True)


def _write_skill(
    root: Path,
    dir_name: str,
    body: str,
    *,
    config: dict | None = None,
    mtime: float | None = None,
) -> Path:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(body, encoding="utf-8")
    if config is not None:
        (skill_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if mtime is not None:
        os.utime(skill_file, (mtime, mtime))
    return skill_file


def _make_loader(project_root: Path, runtime_root: Path) -> SkillsLoader:
    return SkillsLoader(
        skills_dir=str(runtime_root),
        project_skills_dir=str(project_root),
        auto_sync=False,
    )


def _body(name: str, text: str) -> str:
    return f"---\nname: {name}\ndescription: {name} desc\n---\n\n{text}\n"


class TestHotReload:
    def test_reload_on_skill_md_mtime_bump(self, skill_roots) -> None:
        project_root, runtime_root = skill_roots
        base = time.time() - 100
        skill_file = _write_skill(
            project_root, "alpha", _body("alpha", "ORIGINAL BODY"), mtime=base
        )
        loader = _make_loader(project_root, runtime_root)

        spec = loader.get_skill("alpha")
        assert spec is not None
        assert "ORIGINAL BODY" in loader.get_skill_body(spec)

        # No change -> no reload
        assert loader.reload_if_changed() is False

        skill_file.write_text(_body("alpha", "UPDATED BODY"), encoding="utf-8")
        os.utime(skill_file, (base + 10, base + 10))

        assert loader.reload_if_changed() is True
        spec = loader.get_skill("alpha")
        assert spec is not None
        assert "UPDATED BODY" in loader.get_skill_body(spec)
        assert "ORIGINAL BODY" not in loader.get_skill_body(spec)

        # Settled again
        assert loader.reload_if_changed() is False

    def test_new_skill_dir_triggers_reload(self, skill_roots) -> None:
        project_root, runtime_root = skill_roots
        loader = _make_loader(project_root, runtime_root)
        assert loader.reload_if_changed() is False

        _write_skill(project_root, "beta", _body("beta", "BETA BODY"))
        assert loader.reload_if_changed() is True
        assert loader.get_skill("beta") is not None


class TestEnabledFlag:
    def test_disabled_skill_excluded_and_tracked(self, skill_roots) -> None:
        project_root, runtime_root = skill_roots
        _write_skill(
            project_root,
            "off-skill",
            _body("off-skill", "OFF BODY"),
            config={"enabled": False},
        )
        _write_skill(project_root, "on-skill", _body("on-skill", "ON BODY"))
        loader = _make_loader(project_root, runtime_root)

        assert loader.get_skill("off-skill") is None
        assert loader.get_skill("on-skill") is not None
        assert "OFF BODY" not in loader.load_skills_within_budget(["off-skill"], 4000)
        assert "off-skill" in loader._disabled_skills
        assert "on-skill" not in loader._disabled_skills


class TestDualRootPrecedence:
    def test_project_root_wins_over_runtime_root(self, skill_roots) -> None:
        project_root, runtime_root = skill_roots
        _write_skill(project_root, "shared", _body("shared", "PROJECT VERSION"))
        _write_skill(runtime_root, "shared", _body("shared", "RUNTIME VERSION"))
        _write_skill(runtime_root, "runtime-only", _body("runtime-only", "RUNTIME ONLY BODY"))
        loader = _make_loader(project_root, runtime_root)

        shared = loader.get_skill("shared")
        assert shared is not None
        assert loader.get_skill_body(shared) == _body("shared", "PROJECT VERSION")

        # runtime-only skills remain available (overlay, not replacement)
        runtime_only = loader.get_skill("runtime-only")
        assert runtime_only is not None
        assert "RUNTIME ONLY BODY" in loader.get_skill_body(runtime_only)


class TestLazyBody:
    def test_body_not_cached_after_scan_and_cache_invalidation(self, skill_roots) -> None:
        project_root, runtime_root = skill_roots
        base = time.time() - 100
        skill_file = _write_skill(
            project_root, "lazy", _body("lazy", "LAZY V1"), mtime=base
        )
        loader = _make_loader(project_root, runtime_root)

        # Scan must not hold full bodies in memory
        assert loader._body_cache == {}

        spec = loader.get_skill("lazy")
        assert spec is not None
        assert "LAZY V1" in loader.get_skill_body(spec)
        assert "lazy" in loader._body_cache

        # mtime bump invalidates the body cache on next read
        skill_file.write_text(_body("lazy", "LAZY V2"), encoding="utf-8")
        os.utime(skill_file, (base + 10, base + 10))
        assert "LAZY V2" in loader.get_skill_body(spec)

    def test_missing_body_returns_empty_string(self, skill_roots) -> None:
        project_root, runtime_root = skill_roots
        skill_file = _write_skill(project_root, "ghost", _body("ghost", "GHOST"))
        loader = _make_loader(project_root, runtime_root)
        spec = loader.get_skill("ghost")
        assert spec is not None
        skill_file.unlink()
        assert loader.get_skill_body(spec) == ""


class TestGeneration:
    def test_generation_increments_per_scan(self, skill_roots) -> None:
        project_root, runtime_root = skill_roots
        _write_skill(project_root, "gen", _body("gen", "GEN BODY"))
        loader = _make_loader(project_root, runtime_root)

        initial = loader.generation
        assert initial >= 1

        loader._scan_skills()
        assert loader.generation == initial + 1

        _write_skill(project_root, "gen2", _body("gen2", "GEN2 BODY"))
        assert loader.reload_if_changed() is True
        assert loader.generation == initial + 2

        assert loader.reload_if_changed() is False
        assert loader.generation == initial + 2

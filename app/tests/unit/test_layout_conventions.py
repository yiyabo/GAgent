from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_APP_TESTS = _ROOT / "app" / "tests"
_UNIT_DIR = _APP_TESTS / "unit"
_ALLOWED_UNIT_PREFIXES = {
    "audit",
    "auth",
    "command",
    "context",
    "executor",
    "layout",
    "llm",
    "memory",
    "protocol",
    "realtime",
    "resource",
    "semantic",
    "session",
    "upload",
}


def test_no_root_level_python_tests_remain_under_app_tests() -> None:
    root_level = sorted(
        path.name
        for path in _APP_TESTS.glob("test*.py")
        if path.is_file()
    )
    assert root_level == []


def test_unit_test_files_use_approved_domain_prefixes() -> None:
    invalid: list[str] = []
    for path in sorted(_UNIT_DIR.glob("test*.py")):
        if path.name == "test_layout_conventions.py":
            continue
        stem = path.stem.removeprefix("test_")
        prefix = stem.split("_", 1)[0]
        if prefix not in _ALLOWED_UNIT_PREFIXES:
            invalid.append(path.name)

    assert invalid == []

"""Unit tests for the log-only moderation engine."""

from pathlib import Path

import pytest

pytest.importorskip("ahocorasick", reason="pyahocorasick not installed")

from app.services.moderation.engine import ModerationEngine  # noqa: E402


@pytest.fixture()
def keyword_file(tmp_path: Path) -> Path:
    path = tmp_path / "keywords.tsv"
    path.write_text(
        "keyword\trisk\tcategory\n"
        "煽动颠覆国家政权\tA.1 包含违反社会主义核心价值观的内容\tA.1(a) 煽动颠覆国家政权\n"
        "冰毒\tA.1 包含违反社会主义核心价值观的内容\tA.1(h) 其他法律禁止内容\n"
        "致死\tA.4 侵犯他人合法权益\tA.4(g) 侵犯他人其他合法权益\n"
        "鸡\tA.2 包含歧视性内容\tA.2(e) 性别歧视内容\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def whitelist_file(tmp_path: Path) -> Path:
    path = tmp_path / "whitelist.txt"
    path.write_text("# comment\n致死\n", encoding="utf-8")
    return path


def _build(keyword_file: Path, whitelist_file: Path = None) -> ModerationEngine:
    engine = ModerationEngine()
    engine.load(keyword_file, whitelist_file)
    assert engine.ready
    return engine


def test_scan_reports_hit_with_taxonomy(keyword_file, whitelist_file):
    engine = _build(keyword_file, whitelist_file)
    hits = engine.scan("这段文字包含煽动颠覆国家政权的内容")
    assert len(hits) == 1
    assert hits[0]["keyword"] == "煽动颠覆国家政权"
    assert hits[0]["category"].startswith("A.1(a)")


def test_scan_clean_text_returns_empty(keyword_file, whitelist_file):
    engine = _build(keyword_file, whitelist_file)
    assert engine.scan("噬菌体侵染宿主细菌的裂解周期") == []


def test_whitelisted_keyword_is_excluded(keyword_file, whitelist_file):
    engine = _build(keyword_file, whitelist_file)
    assert engine.scan("该菌株的致死率较高") == []


def test_single_char_keyword_is_skipped(keyword_file, whitelist_file):
    # 单字词不入库：中文无词边界，子串匹配必然误伤（"鸡" in "鸡蛋"）
    engine = _build(keyword_file, whitelist_file)
    assert engine.scan("桌上有一只鸡。") == []
    assert engine.scan("早上吃了鸡蛋") == []


def test_duplicate_hits_are_deduplicated(keyword_file, whitelist_file):
    engine = _build(keyword_file, whitelist_file)
    hits = engine.scan("冰毒" * 10)
    assert len(hits) == 1


def test_unloaded_engine_scans_nothing(keyword_file):
    engine = ModerationEngine()
    assert engine.scan("冰毒") == []

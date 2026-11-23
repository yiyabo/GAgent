"""Weekly dissatisfaction digest: downvote reasons, auto-evaluated cases, ops write."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config.database_config import get_database_config
from app.database import get_db
from app.repository import chat_runs, skill_learning
from app.routers.chat.models import ChatRequest
from app.services import feedback_digest


def _finished_run(run_id: str, session_id: str, question: str, answer: str, owner: str = "alice") -> None:
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO chat_sessions(id, owner_id, name) VALUES (?, ?, ?)", (session_id, owner, session_id))
        conn.execute("INSERT OR IGNORE INTO users(id, email, password_hash) VALUES (?, ?, 'x')", (owner, owner + "@example.com"))
        user_id = conn.execute(
            "INSERT INTO chat_messages(session_id, role, content) VALUES (?, 'user', ?)", (session_id, question)
        ).lastrowid
        assistant_id = conn.execute(
            "INSERT INTO chat_messages(session_id, role, content) VALUES (?, 'assistant', ?)", (session_id, answer)
        ).lastrowid
    chat_runs.create_chat_run(run_id, session_id, ChatRequest(message=question, session_id=session_id).model_dump_json(), owner_id=owner)
    with get_db() as conn:
        conn.execute(
            "UPDATE chat_runs SET status='succeeded', user_message_id=?, assistant_message_id=? WHERE run_id=?",
            (user_id, assistant_id, run_id),
        )


def _evaluation(run_id: str, session_id: str, level: str, owner: str = "alice") -> None:
    evaluation = {"failure_modes": ["wrong_tool_result"], "evidence": [{"source": "user_follow_up", "quote": "还是不对", "explanation": "x"}]}
    with get_db() as conn:
        conn.execute(
            """INSERT INTO conversation_quality_evaluations(target_run_id, session_id, owner_id, status, observed_until,
               snapshot_json, satisfaction_level, confidence, evaluation_json, evaluated_at)
               VALUES (?, ?, ?, 'final', CURRENT_TIMESTAMP, '{}', ?, 0.8, ?, CURRENT_TIMESTAMP)""",
            (run_id, session_id, owner, level, json.dumps(evaluation)),
        )


@pytest.fixture
def seeded(isolated_app_env):
    from app.database import init_db

    init_db()
    _finished_run("run-bad", "s1", "帮我注释这个噬菌体基因组", "注释结果如下……")
    _finished_run("run-good", "s1", "列出宿主", "宿主是 E. coli")
    _finished_run("run-auto", "s2", "找相似蛋白", "没有找到")
    _finished_run("run-both", "s2", "画进化树", "图已生成")
    skill_learning.save_feedback("run-bad", "needs_work", "基因数量不对", ["wrong", "incomplete"])
    skill_learning.save_feedback("run-good", "useful", "")
    skill_learning.save_feedback("run-both", "needs_work", "", [])
    _evaluation("run-auto", "s2", "negative")
    _evaluation("run-both", "s2", "angry")
    _evaluation("run-good", "s1", "satisfied")
    return Path(get_database_config().get_main_db_path())


def test_feedback_reasons_round_trip(seeded) -> None:
    stored = skill_learning.feedback("run-bad")
    assert stored["reasons"] == ["wrong", "incomplete"] and stored["comment"] == "基因数量不对"
    assert skill_learning.feedback("run-good")["reasons"] == []


def test_digest_lists_downvotes_then_auto_cases_without_duplicates(seeded) -> None:
    digest = feedback_digest.load_digest(seeded, now=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert digest["ratings"] == {"useful": 1, "needs_work": 2}
    assert {case["run_id"] for case in digest["downvotes"]} == {"run-bad", "run-both"}
    bad = next(case for case in digest["downvotes"] if case["run_id"] == "run-bad")
    assert bad["question"] == "帮我注释这个噬菌体基因组" and bad["answer"].startswith("注释结果")
    assert bad["owner_email"] == "alice@example.com"
    assert [case["run_id"] for case in digest["auto_cases"]] == ["run-auto"]
    assert digest["auto_levels"] == {"negative": 1, "angry": 1, "satisfied": 1}

    markdown = feedback_digest.render_markdown(digest)
    assert "点赞 1 次，点踩 2 次" in markdown
    assert "原因：结果有误、不完整" in markdown and "补充：基因数量不对" in markdown
    assert "找相似蛋白" in markdown and "依据：还是不对" in markdown


def test_digest_window_excludes_older_feedback(seeded) -> None:
    digest = feedback_digest.load_digest(seeded, now=datetime.now(timezone.utc) + timedelta(days=8))
    assert digest["ratings"] == {"useful": 0, "needs_work": 0}
    assert digest["downvotes"] == [] and digest["auto_cases"] == []
    assert "本周没有点踩" in feedback_digest.render_markdown(digest)


def test_weekly_digest_writes_markdown_and_json(seeded, tmp_path) -> None:
    now = datetime(2026, 11, 30, 3, 0, tzinfo=timezone(timedelta(hours=8)))
    with get_db() as conn:
        conn.execute("UPDATE skill_run_feedback SET updated_at = '2026-11-28 10:00:00'")
    result = feedback_digest.write_weekly_digest(main_db=seeded, runtime_root=tmp_path, now=now)
    assert result["downvotes"] == 2
    markdown = (tmp_path / "ops" / "feedback-digest" / "feedback-digest-2026-11-30.md").read_text(encoding="utf-8")
    assert "帮我注释这个噬菌体基因组" in markdown
    assert json.loads((tmp_path / "ops" / "feedback-digest" / "feedback-digest-2026-11-30.json").read_text())["ratings"]["needs_work"] == 2


def test_weekly_digest_skips_missing_database(tmp_path) -> None:
    assert feedback_digest.write_weekly_digest(main_db=tmp_path / "none.db", runtime_root=tmp_path) == {"skipped": "main database missing"}


def test_digest_tolerates_feedback_table_without_reasons(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE chat_runs(run_id TEXT, session_id TEXT, owner_id TEXT, status TEXT, user_message_id INT,
               assistant_message_id INT, request_json TEXT, created_at TEXT);
           CREATE TABLE users(id TEXT, email TEXT);
           CREATE TABLE chat_messages(id INT, content TEXT);
           CREATE TABLE skill_run_feedback(run_id TEXT, rating TEXT, comment TEXT, updated_at TEXT);
           INSERT INTO chat_runs VALUES('r','s','o','succeeded',NULL,NULL,'{"message":"旧问题"}','2026-11-20 00:00:00');
           INSERT INTO skill_run_feedback VALUES('r','needs_work','旧','2026-11-20 00:00:00');"""
    )
    conn.commit()
    conn.close()
    digest = feedback_digest.load_digest(path, now=datetime(2026, 11, 21, tzinfo=timezone.utc))
    assert digest["downvotes"][0]["reasons"] == [] and digest["downvotes"][0]["question"] == "旧问题"


def test_feedback_digest_route_is_admin_only(app_client_factory, monkeypatch) -> None:
    monkeypatch.setenv("QUALITY_ANALYTICS_ADMIN_IDS", "quality-admin")
    with app_client_factory() as client:
        _finished_run("run-route", "route-s", "问题一", "回答一")
        skill_learning.save_feedback("run-route", "needs_work", "不准", ["wrong"])
        assert client.get("/quality/feedback-digest").status_code == 401
        assert client.get("/quality/feedback-digest", headers={"X-Forwarded-User": "alice"}).status_code == 403
        admin = {"X-Forwarded-User": "quality-admin"}
        body = client.get("/quality/feedback-digest", headers=admin).json()
        assert [case["run_id"] for case in body["downvotes"]] == ["run-route"]
        markdown = client.get("/quality/feedback-digest", params={"format": "markdown"}, headers=admin)
        assert markdown.status_code == 200 and markdown.headers["content-type"].startswith("text/markdown")
        assert "原因：结果有误" in markdown.text


def test_feedback_reasons_over_http(app_client_factory) -> None:
    alice = {"X-Forwarded-User": "alice"}
    with app_client_factory() as client:
        assert client.patch("/chat/sessions/http-s", json={"name": "http-s"}, headers=alice).status_code == 200
        _finished_run("run-http", "http-s", "问题", "回答")
        url = "/skill-learning/runs/run-http/feedback"
        bad = client.post(url, json={"session_id": "http-s", "rating": "needs_work", "reasons": ["nope"]}, headers=alice)
        assert bad.status_code == 422
        saved = client.post(
            url,
            json={"session_id": "http-s", "rating": "needs_work", "comment": " 漏了基因 ", "reasons": ["incomplete", "incomplete"]},
            headers=alice,
        )
        assert saved.status_code == 200
        stored = client.get(url, params={"session_id": "http-s"}, headers=alice).json()["feedback"]
        assert stored["reasons"] == ["incomplete"] and stored["comment"] == "漏了基因"


def test_ops_cron_writes_digest_on_monday_only(isolated_app_env, monkeypatch) -> None:
    import app.ops.backup as backup_module
    import app.ops.retention as retention_module
    from app.ops.cron import run_once
    from app.services.foundation.settings import get_settings

    monkeypatch.setenv("BACKUP_DIR", str(isolated_app_env["db_root"] / "bk"))
    get_settings.cache_clear()
    monkeypatch.setattr(backup_module, "run_backup", lambda **kwargs: {})
    monkeypatch.setattr(retention_module, "run_retention", lambda **kwargs: {})
    written: list = []
    monkeypatch.setattr(feedback_digest, "write_weekly_digest", lambda **kwargs: written.append(kwargs) or {"path": "x"})

    monday = datetime(2026, 11, 30, 3, 0, tzinfo=timezone(timedelta(hours=8)))
    assert run_once(now=monday)["feedback_digest"] == {"path": "x"}
    assert written[0]["runtime_root"] == isolated_app_env["runtime_root"].resolve()
    assert "feedback_digest" not in run_once(now=monday + timedelta(days=1))

    def boom(**kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(feedback_digest, "write_weekly_digest", boom)
    assert run_once(now=monday)["feedback_digest"] == {"error": "failed"}

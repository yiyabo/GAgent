from pathlib import Path

from app.services.terminal.audit_logger import AuditLogger


def test_audit_logger_query_and_replay(tmp_path: Path) -> None:
    logger = AuditLogger("term-test", audit_root=tmp_path)
    logger.log_event("input", data=b"ls\\n", timestamp=1000.0)
    logger.log_event("output", data=b"file.txt\\n", timestamp=1000.2)
    logger.log_event("output", data=b"done\\n", timestamp=1000.5)

    rows = logger.query_events(limit=10)
    assert len(rows) == 3
    assert rows[0]["event_type"] == "input"

    replay = logger.build_replay(limit=10, include_input=True)
    assert len(replay) == 3
    assert replay[0]["type"] == "i"
    assert replay[1]["type"] == "o"

    deleted = logger.prune_older_than(days=0)
    assert deleted >= 0

    logger.close()

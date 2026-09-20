"""Tests for the P3 sentinel log scanner (scripts/sentinel_scan.py)."""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "sentinel_scan.py"
_spec = importlib.util.spec_from_file_location("sentinel_scan", _SCRIPT)
sentinel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sentinel)


HEALTHY_LOG = [
    '{"ts": "2026-09-20T01:00:00", "message": "[DEEP_THINK_NATIVE] Starting for: 画图"}',
    '{"ts": "2026-09-20T01:00:10", "message": "[DEEP_THINK][progress] iteration=3 new_deliverables=a.png verified_total=1"}',
    '{"ts": "2026-09-20T01:01:00", "message": "[DEEP_THINK_NATIVE] Forced synthesis succeeded (980 chars)"}',
]

ANOMALY_LOG = [
    '{"message": "[DEEP_THINK_NATIVE] Starting for: task A"}',
    '{"message": "[DEEP_THINK][acceptance] iteration=18 missing=image -> break with gaps"}',
    '{"message": "LLM HTTP 429: Concurrency limit exceeded for account"}',
    '{"message": "[DEEP_THINK_NATIVE] Forced synthesis failed: TimeoutError()"}',
    '{"message": "[DEEP_THINK_NATIVE] Forced synthesis failed: TimeoutError()"}',
]


class TestScanLines:
    def test_healthy_counts(self) -> None:
        result = sentinel.scan_lines(HEALTHY_LOG)
        assert result["counts"]["runs"] == 1
        assert result["counts"]["progress"] == 1
        assert result["counts"]["synth_ok"] == 1
        assert result["counts"]["http_429"] == 0
        assert sentinel.decide_alerts(result["counts"], result["samples"]) == []

    def test_anomaly_counts_and_alerts(self) -> None:
        result = sentinel.scan_lines(ANOMALY_LOG)
        counts = result["counts"]
        assert counts["acceptance_gap"] == 1
        assert counts["http_429"] == 1
        assert counts["synth_fail"] == 2
        signatures = {sig for sig, _, _ in sentinel.decide_alerts(counts, result["samples"])}
        assert "quota-429" in signatures
        assert "acceptance-gap" in signatures
        assert "synthesis-failing" in signatures


class TestRunPass:
    def test_report_state_and_alert_files(self, tmp_path) -> None:
        out = tmp_path / "sentinel"
        window, fresh, all_alerts = sentinel.run(out, ANOMALY_LOG, now_ts=1_000_000.0)
        assert window["counts"]["runs"] == 1
        assert {sig for sig, _, _ in fresh} >= {"quota-429", "acceptance-gap"}

        report = (out / "report.md").read_text(encoding="utf-8")
        assert "# P3 Sentinel Report" in report
        assert "quota-429" in report

        alerts = (out / "alerts.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(alerts) == len(fresh)
        first = json.loads(alerts[0])
        assert first["signature"] in {"quota-429", "acceptance-gap", "synthesis-failing"}

        state = json.loads((out / "state.json").read_text(encoding="utf-8"))
        assert state["runs_scanned"] == 1
        assert state["totals"]["http_429"] == 1

    def test_cooldown_suppresses_repeat_alerts(self, tmp_path) -> None:
        out = tmp_path / "sentinel"
        _, fresh1, _ = sentinel.run(out, ANOMALY_LOG, now_ts=1_000_000.0)
        _, fresh2, all2 = sentinel.run(out, ANOMALY_LOG, now_ts=1_000_100.0)
        assert fresh1
        assert fresh2 == []  # same signatures inside cooldown
        assert all2  # anomalies still detected, just not re-alerted
        # after cooldown they fire again
        _, fresh3, _ = sentinel.run(out, ANOMALY_LOG, now_ts=1_000_100.0 + 5 * 3600)
        assert {sig for sig, _, _ in fresh3} == {sig for sig, _, _ in fresh1}

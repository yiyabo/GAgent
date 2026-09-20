#!/usr/bin/env python3
"""P3 sentinel: consume deep-think heartbeat log lines, emit report + alerts.

Self-contained (stdlib only) so it can run on the .8 host directly:

    docker logs phage-agent --since 15m 2>&1 \
        | python3 /data/phage-agent/scripts/sentinel_scan.py \
            --dir /data/phage-agent/data/sentinel

Outputs (fixed names inside --dir): state.json, report.md, alerts.jsonl.

Heartbeat line families consumed (see app/services/deep_think_agent.py):
  [DEEP_THINK][iter] / [progress] / [trap] / [endgame] / [acceptance]
  [DEEP_THINK_NATIVE] Starting for: / Forced synthesis ... / Stopped repeated tool loop
  LLM HTTP 429 / Concurrency limit exceeded / HTTP 5xx
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time

STATE_FILENAME = "state.json"
REPORT_FILENAME = "report.md"
ALERTS_FILENAME = "alerts.jsonl"

LINE_PATTERNS = [
    ("runs", re.compile(r"\[DEEP_THINK_NATIVE\] Starting for:")),
    ("progress", re.compile(r"\[DEEP_THINK\]\[progress\]")),
    ("trap_warn", re.compile(r"\[DEEP_THINK\]\[trap\]")),
    ("endgame_nudge", re.compile(r"\[DEEP_THINK\]\[endgame\]")),
    ("acceptance_expected", re.compile(r"\[DEEP_THINK\]\[acceptance\] expected deliverable types")),
    (
        "acceptance_extend",
        re.compile(r"\[DEEP_THINK\]\[acceptance\].*extension granted"),
    ),
    (
        "acceptance_gap",
        re.compile(r"\[DEEP_THINK\]\[acceptance\].*(?:break with gaps|identical-cycle stop with missing)"),
    ),
    ("synth_ok", re.compile(r"Forced synthesis succeeded")),
    ("synth_fail", re.compile(r"Forced synthesis failed")),
    ("repetition_stop", re.compile(r"Stopped repeated tool loop")),
    ("http_429", re.compile(r"HTTP 429|Concurrency limit exceeded", re.IGNORECASE)),
    ("http_5xx", re.compile(r"HTTPStatusError[^\d]*5\d\d|HTTP 5\d\d")),
]

MAX_SAMPLES = 3


def scan_lines(lines):
    """Count heartbeat families and keep a few sample lines per family."""
    counts = {key: 0 for key, _ in LINE_PATTERNS}
    samples = {key: [] for key, _ in LINE_PATTERNS}
    total = 0
    for raw in lines:
        line = str(raw).rstrip()
        if not line:
            continue
        total += 1
        for key, pattern in LINE_PATTERNS:
            if pattern.search(line):
                counts[key] += 1
                if len(samples[key]) < MAX_SAMPLES:
                    samples[key].append(line.strip()[:300])
    return {"total_lines": total, "counts": counts, "samples": samples}


def decide_alerts(counts, samples):
    """Alert candidates for this window: (signature, severity, detail)."""
    alerts = []

    if counts.get("http_429", 0) >= 1:
        alerts.append((
            "quota-429",
            "critical",
            f"{counts['http_429']} upstream 429 / concurrency-limit line(s) — batch load may be starving interactive traffic",
        ))
    if counts.get("acceptance_gap", 0) >= 1:
        detail = "; ".join(samples.get("acceptance_gap") or [])[:240]
        alerts.append((
            "acceptance-gap",
            "warning",
            f"{counts['acceptance_gap']} run(s) closed with missing deliverables: {detail}",
        ))
    if counts.get("trap_warn", 0) >= 5:
        alerts.append((
            "trap-storm",
            "warning",
            f"{counts['trap_warn']} repeated-failure trap warnings in window",
        ))
    synth_fail = counts.get("synth_fail", 0)
    if synth_fail >= 2 and synth_fail > counts.get("synth_ok", 0):
        alerts.append((
            "synthesis-failing",
            "warning",
            f"forced synthesis failing ({synth_fail} failures vs {counts.get('synth_ok', 0)} successes)",
        ))
    if counts.get("repetition_stop", 0) >= 3:
        alerts.append((
            "repetition-stops",
            "warning",
            f"{counts['repetition_stop']} identical-cycle stops in window",
        ))
    if counts.get("http_5xx", 0) >= 3:
        alerts.append((
            "upstream-5xx",
            "warning",
            f"{counts['http_5xx']} upstream 5xx lines in window",
        ))
    return alerts


def load_state(out_dir: pathlib.Path):
    state_path = out_dir / STATE_FILENAME
    try:
        with state_path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
        if isinstance(state, dict):
            return state
    except Exception:
        pass
    return {"alerts": {}, "totals": {}, "runs_scanned": 0}


def save_state(out_dir: pathlib.Path, state):
    tmp_path = out_dir / (STATE_FILENAME + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path.replace(out_dir / STATE_FILENAME)


def filter_fresh_alerts(alerts, state, now_ts, cooldown_seconds):
    """Drop alerts that fired within the cooldown window."""
    fresh = []
    last_seen = state.setdefault("alerts", {})
    for sig, severity, detail in alerts:
        last = float(last_seen.get(sig, 0.0) or 0.0)
        if now_ts - last >= cooldown_seconds:
            fresh.append((sig, severity, detail))
            last_seen[sig] = now_ts
    return fresh


def render_report(window_result, fresh_alerts, all_alerts, state, generated_at):
    counts = window_result["counts"]
    lines = [
        "# P3 Sentinel Report",
        "",
        f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(generated_at))}",
        f"- Window log lines scanned: {window_result['total_lines']}",
        f"- Deep-think runs started: {counts.get('runs', 0)}",
        f"- Progress events: {counts.get('progress', 0)}",
        f"- Synthesis ok/fail: {counts.get('synth_ok', 0)}/{counts.get('synth_fail', 0)}",
        f"- Endgame nudges: {counts.get('endgame_nudge', 0)}",
        f"- Failure-trap warnings: {counts.get('trap_warn', 0)}",
        f"- Repetition stops: {counts.get('repetition_stop', 0)}",
        f"- Acceptance expected/extended/gap: "
        f"{counts.get('acceptance_expected', 0)}/{counts.get('acceptance_extend', 0)}/{counts.get('acceptance_gap', 0)}",
        f"- Upstream 429: {counts.get('http_429', 0)}; upstream 5xx: {counts.get('http_5xx', 0)}",
        "",
        "## Alerts",
    ]
    if not all_alerts:
        lines.append("- none this window")
    else:
        fresh_set = {(sig, detail) for sig, _, detail in fresh_alerts}
        for sig, severity, detail in all_alerts:
            marker = "NEW" if (sig, detail) in fresh_set else "cooldown"
            lines.append(f"- [{severity}][{marker}] {sig}: {detail}")
    totals = state.get("totals") or {}
    if totals:
        lines += [
            "",
            "## Cumulative (since sentinel start)",
        ]
        for key in sorted(totals):
            lines.append(f"- {key}: {totals[key]}")
    lines.append("")
    return "\n".join(lines)


def run(out_dir, lines, cooldown_hours=4.0, now_ts=None):
    """One sentinel pass over the given log lines; returns (window, fresh, all_alerts)."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    window = scan_lines(lines)
    now_ts = time.time() if now_ts is None else float(now_ts)
    state = load_state(out_dir)
    state["runs_scanned"] = int(state.get("runs_scanned", 0)) + 1

    totals = state.setdefault("totals", {})
    for key, value in window["counts"].items():
        totals[key] = int(totals.get(key, 0)) + int(value)

    all_alerts = decide_alerts(window["counts"], window["samples"])
    fresh = filter_fresh_alerts(all_alerts, state, now_ts, cooldown_hours * 3600.0)

    if fresh:
        alerts_path = out_dir / ALERTS_FILENAME
        with alerts_path.open("a", encoding="utf-8") as fh:
            for sig, severity, detail in fresh:
                fh.write(json.dumps({
                    "ts": now_ts,
                    "signature": sig,
                    "severity": severity,
                    "detail": detail,
                }, ensure_ascii=False) + "\n")

    report = render_report(window, fresh, all_alerts, state, now_ts)
    report_path = out_dir / REPORT_FILENAME
    with report_path.open("w", encoding="utf-8") as fh:
        fh.write(report)

    save_state(out_dir, state)
    return window, fresh, all_alerts


def main(argv=None):
    parser = argparse.ArgumentParser(description="P3 sentinel log scanner")
    parser.add_argument("--dir", default=".", help="output directory for state.json/report.md/alerts.jsonl")
    parser.add_argument("--cooldown-hours", type=float, default=4.0)
    args = parser.parse_args(argv)

    window, fresh, all_alerts = run(
        args.dir,
        sys.stdin.read().splitlines(),
        cooldown_hours=args.cooldown_hours,
    )
    print(
        f"[sentinel] lines={window['total_lines']} runs={window['counts'].get('runs', 0)} "
        f"alerts={len(all_alerts)} fresh={len(fresh)}"
    )
    for sig, severity, detail in fresh:
        print(f"[sentinel][{severity}] {sig}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

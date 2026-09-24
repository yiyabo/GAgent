#!/usr/bin/env python3
"""Local structural self-test for the expanded harness benchmark (no container, no LLM).

Runs on the Mac with the system python3 (stdlib only):

    python3 evals/harness_benchmark/selftest.py

Covers:
  1. task-definition parsing: every discovered task dir (pipeline_benchmark's
     50 + tasks_code's code-mode tasks) has task.md, valid meta.json
     (category + timeout_seconds), and a compilable check.py;
  2. runner plumbing: mac_runner.py / suite_driver.py / container_driver.py
     compile; mac_runner --dry-run validates the full manifest end-to-end;
  3. check.py unit tests: each tasks_code check is executed (literal argv
     subprocess) against synthetic result dirs — a passing fixture, deliberate
     failing variants, and netfail variants for the network-dependent tasks.

Truth used to build the synthetic answers is recomputed from the task
fixtures here, never hardcoded — the same discipline the checks follow.
"""

from __future__ import annotations

import csv
import json
import math
import py_compile
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parents[1]
CODE_TASKS = BASE / "tasks_code"
PIPELINE_TASKS = REPO_ROOT / "evals" / "pipeline_benchmark" / "tasks"

FAILURES = []
CHECKS = [0]


def record(ok: bool, label: str, detail: str = "") -> None:
    CHECKS[0] += 1
    if not ok:
        FAILURES.append(f"{label}: {detail}")
    print(f"{'PASS' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail and not ok else ""))


# --------------------------------------------------------------------------
# 1. task-definition parsing
# --------------------------------------------------------------------------

def test_task_parsing() -> list[Path]:
    task_dirs = []
    for root in (PIPELINE_TASKS, CODE_TASKS):
        for path in sorted(root.iterdir()):
            if path.is_dir() and (path / "task.md").exists():
                task_dirs.append(path)
    record(len(task_dirs) == 57, "task count == 57", f"got {len(task_dirs)}")
    for task_dir in task_dirs:
        label = f"parse {task_dir.name}"
        problems = []
        if not (task_dir / "task.md").read_text(encoding="utf-8").strip():
            problems.append("task.md empty")
        try:
            meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
            if not meta.get("category"):
                problems.append("meta.category missing")
            if not float(meta.get("timeout_seconds") or 0):
                problems.append("meta.timeout_seconds missing")
            turns = meta.get("turns")
            if turns is not None and (
                not isinstance(turns, list)
                or not all(isinstance(t, str) and t.strip() for t in turns)
            ):
                problems.append("meta.turns malformed")
        except json.JSONDecodeError as exc:
            problems.append(f"meta.json corrupt: {exc}")
        check = task_dir / "check.py"
        if not check.is_file():
            problems.append("check.py missing")
        else:
            try:
                py_compile.compile(str(check), doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"check.py does not compile: {exc}")
        record(not problems, label, "; ".join(problems))
    return task_dirs


def test_runner_files_compile() -> None:
    for name in ("mac_runner.py", "suite_driver.py", "container_driver.py", "selftest.py"):
        path = BASE / name
        try:
            py_compile.compile(str(path), doraise=True)
            record(True, f"compile {name}")
        except py_compile.PyCompileError as exc:
            record(False, f"compile {name}", str(exc))


def test_mac_runner_dry_run() -> None:
    proc = subprocess.run(
        ["python3", str(BASE / "mac_runner.py"), "--dry-run", "--label", "selftest"],
        capture_output=True, text=True, shell=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    record(proc.returncode == 0 and "plan OK" in out, "mac_runner --dry-run",
           f"rc={proc.returncode} tail={out[-300:]}")


# --------------------------------------------------------------------------
# 2. synthetic result dirs + check execution
# --------------------------------------------------------------------------

def num(value: float, nd: int = 2) -> str:
    return f"{round(float(value), nd):.{nd}f}"


def make_result_dir(task_dir: Path, tmp: Path, *, final_answer: str,
                    tools_used: list, run_ids=None, turns=None,
                    events: list = None, ok_run: bool = True) -> Path:
    result_dir = tmp / f"res_{task_dir.name}"
    if result_dir.exists():
        shutil.rmtree(result_dir)
    result_dir.mkdir(parents=True)
    session = result_dir / "session"
    uploads = session / "uploads"
    uploads.mkdir(parents=True)
    fixtures = task_dir / "fixtures"
    if fixtures.is_dir():
        for fixture in sorted(fixtures.iterdir()):
            if fixture.is_file():
                shutil.copy2(fixture, uploads / fixture.name)
    truth = task_dir / "truth"
    if truth.is_dir():
        shutil.copytree(truth, result_dir / "truth")
    record = {
        "task": task_dir.name,
        "session_id": "hbSELFTEST_000001",
        "session_dir": str(session),
        "run_ids": run_ids if run_ids is not None else ["run_0"],
        "ok_run": ok_run,
        "seconds": 1.0,
        "final_answer": final_answer,
        "answer_len": len(final_answer),
        "fallback_used": False,
        "bailout_phrase": False,
        "total_iterations": 3,
        "tools_used": tools_used,
        "acceptance_missing": [],
        "produced_paths": [],
        "turns": turns if turns is not None else [{"run_id": "run_0", "status": "succeeded"}],
        "error": None,
    }
    (result_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    if events is not None:
        (result_dir / "events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
            encoding="utf-8",
        )
    return result_dir


def run_check(task_dir: Path, result_dir: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["python3", str(task_dir / "check.py"), str(result_dir)],
        capture_output=True, text=True, shell=False, timeout=60,
    )
    return proc.returncode, ((proc.stderr or "") + (proc.stdout or ""))[-300:]


def expect(task_dir: Path, result_dir: Path, rc: int, label: str) -> None:
    got, out = run_check(task_dir, result_dir)
    record(got == rc, label, f"expected rc={rc} got rc={got}: {out}")


def final_event(turn: int, response: str, tool_results=None) -> dict:
    metadata = {}
    if tool_results is not None:
        metadata["tool_results"] = tool_results
    return {"run_id": f"run_{turn}", "turn": turn, "seq": 10 + turn,
            "payload": {"type": "final", "payload": {"response": response, "metadata": metadata}}}


def tool_step(turn: int, tool: str) -> dict:
    return {"run_id": f"run_{turn}", "turn": turn, "seq": turn * 10 + 1,
            "payload": {"type": "thinking_step", "step": {"iteration": 1, "action": tool, "status": "done"}}}


# --------------------------------------------------------------------------
# 3. per-task unit tests (truth recomputed from fixtures)
# --------------------------------------------------------------------------

def test_tm01(task_dir: Path, tmp: Path) -> None:
    answer = ("汇总：lysin 检索 10 条、endolysin 检索 8 条、phage 检索 9 条，"
              "去重域名 21 个。链接：https://example.com/a https://pubmed.ncbi.nlm.nih.gov/x "
              "https://doi.org/10.1/abc")
    good = make_result_dir(task_dir, tmp, final_answer=answer, tools_used=["execute_code"])
    expect(task_dir, good, 0, "tm01 pass")
    bad = make_result_dir(task_dir, tmp, final_answer="lysin endolysin phage 没有链接",
                          tools_used=["execute_code"])
    expect(task_dir, bad, 1, "tm01 fail-no-url")
    net = make_result_dir(task_dir, tmp, final_answer="搜索失败：网络错误，暂时无法完成检索",
                          tools_used=[])
    expect(task_dir, net, 3, "tm01 netfail")


def test_tm02(task_dir: Path, tmp: Path) -> None:
    uploads = task_dir / "fixtures"
    total, kept = 0, 0
    groups = {}
    for path in sorted(uploads.glob("batch_*.csv")):
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                total += 1
                if row["status"] == "ok" and float(row["value"]) > 50:
                    kept += 1
                    groups.setdefault(row["group"], []).append(float(row["value"]))
    parts = [f"总行数 {total}，保留 {kept} 条。"]
    for group, values in sorted(groups.items()):
        parts.append(f"{group} 均值 {num(sum(values) / len(values))}")
    good = make_result_dir(task_dir, tmp, final_answer="；".join(parts), tools_used=["execute_code"])
    expect(task_dir, good, 0, "tm02 pass")
    bad = make_result_dir(task_dir, tmp, final_answer=f"总行数 {total}，保留 {kept + 5} 条",
                          tools_used=["execute_code"])
    expect(task_dir, bad, 1, "tm02 fail-wrong-count")
    no_tool = make_result_dir(task_dir, tmp, final_answer="；".join(parts), tools_used=["file_operations"])
    expect(task_dir, no_tool, 1, "tm02 fail-no-execute_code")


def test_tm03(task_dir: Path, tmp: Path) -> None:
    with (task_dir / "fixtures" / "series.csv").open(newline="", encoding="utf-8") as fh:
        values = [float(row["value"]) for row in csv.DictReader(fh)]
    mean_v = statistics.mean(values)
    outside = sum(1 for v in values if abs(v - mean_v) > statistics.pstdev(values))
    reuse_code = "out = sum(1 for v in vals if abs(v - mean_v) > std_v)\nprint(out)"
    events = [
        tool_step(0, "execute_code"),
        final_event(0, f"mean_v = {num(mean_v)}"),
        tool_step(1, "execute_code"),
        final_event(1, f"区间外共有 {outside} 个点",
                    tool_results=[{"name": "execute_code", "parameters": {"code": reuse_code}, "result": {}}]),
    ]
    good = make_result_dir(task_dir, tmp, final_answer=f"区间外共有 {outside} 个点",
                           tools_used=["execute_code"], run_ids=["run_0", "run_1"],
                           turns=[{"run_id": "run_0", "status": "succeeded"},
                                  {"run_id": "run_1", "status": "succeeded"}],
                           events=events)
    expect(task_dir, good, 0, "tm03 pass")
    reread = [e for e in events]
    reread[-1] = final_event(1, f"区间外共有 {outside} 个点",
                             tool_results=[{"name": "execute_code",
                                            "parameters": {"code": "vals = read('uploads/series.csv')"}, "result": {}}])
    bad = make_result_dir(task_dir, tmp, final_answer=f"区间外共有 {outside} 个点",
                          tools_used=["execute_code"], run_ids=["run_0", "run_1"],
                          turns=[{"run_id": "run_0", "status": "succeeded"},
                                 {"run_id": "run_1", "status": "succeeded"}],
                          events=reread)
    expect(task_dir, bad, 1, "tm03 fail-reread-fixture")
    wrong = make_result_dir(task_dir, tmp, final_answer=f"区间外共有 {outside + 7} 个点",
                            tools_used=["execute_code"], run_ids=["run_0", "run_1"],
                            turns=[{"run_id": "run_0", "status": "succeeded"},
                                   {"run_id": "run_1", "status": "succeeded"}],
                            events=events)
    expect(task_dir, wrong, 1, "tm03 fail-wrong-count")


def test_tm04(task_dir: Path, tmp: Path) -> None:
    xs, ys = [], []
    with (task_dir / "fixtures" / "measurements.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            xs.append(float(row["x"]))
            ys.append(float(row["y"]))
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    pearson = sxy / math.sqrt(sxx * syy)
    slope = sxy / sxx
    answer = f"相关系数 {num(pearson, 3)}，y 均值 {num(my, 2)}，斜率 {num(slope, 3)}"
    good = make_result_dir(task_dir, tmp, final_answer=answer, tools_used=["execute_code"])
    expect(task_dir, good, 0, "tm04 pass")
    bad = make_result_dir(task_dir, tmp, final_answer=f"相关系数 {num(pearson, 3)}，y 均值 {num(my, 2)}",
                          tools_used=["execute_code"])
    expect(task_dir, bad, 1, "tm04 fail-missing-slope")


def test_tm05(task_dir: Path, tmp: Path) -> None:
    answer = "项目代号 蓝藻-7；阈值设定 42.75；负责人 林澈。"
    good = make_result_dir(task_dir, tmp, final_answer=answer,
                           tools_used=["execute_code", "file_operations"])
    expect(task_dir, good, 0, "tm05 pass")
    forced = make_result_dir(task_dir, tmp, final_answer=answer, tools_used=["execute_code"])
    expect(task_dir, forced, 1, "tm05 fail-no-regular-tool")
    empty = make_result_dir(task_dir, tmp, final_answer="读不到文件内容。",
                            tools_used=["execute_code", "file_operations"])
    expect(task_dir, empty, 1, "tm05 fail-missing-content")


def test_tm06(task_dir: Path, tmp: Path) -> None:
    with (task_dir / "fixtures" / "sensors.csv").open(newline="", encoding="utf-8") as fh:
        readings = [float(row["reading"]) for row in csv.DictReader(fh)]
    mean = sum(readings) / len(readings)
    answer = f"MEAN={num(mean, 4)}"
    good = make_result_dir(task_dir, tmp, final_answer=answer, tools_used=["execute_code"])
    spill_dir = good / "session" / "scratch" / "code_mode" / "spill"
    spill_dir.mkdir(parents=True)
    spill_dir.joinpath("stdout-abcdef123456.txt").write_text(
        "BEGIN-DUMP\n" + ("sensor-0001,12.345\n" * 4000) + "END-DUMP\n", encoding="utf-8")
    expect(task_dir, good, 0, "tm06 pass")
    nospill = make_result_dir(task_dir, tmp, final_answer=answer, tools_used=["execute_code"])
    expect(task_dir, nospill, 1, "tm06 fail-no-spill")
    wrong = make_result_dir(task_dir, tmp, final_answer=f"MEAN={num(mean + 1.5, 4)}",
                            tools_used=["execute_code"])
    expect(task_dir, wrong, 1, "tm06 fail-wrong-mean")


def test_tm07(task_dir: Path, tmp: Path) -> None:
    truth = json.loads((task_dir / "truth" / "expected.json").read_text(encoding="utf-8"))
    seqs = truth["sequences"]
    threshold = float(truth["length_threshold"])
    kept = [s for s in seqs if s["length"] > threshold]
    mean_kept = sum(s["length"] for s in kept) / len(kept)
    parts = [f"{s['id']} 长度 {s['length']}" for s in seqs]
    parts.append(f"P61626 被过滤（≤{int(threshold)}）；保留序列长度均值 {num(mean_kept, 1)}")
    answer = "；".join(parts)
    good = make_result_dir(task_dir, tmp, final_answer=answer, tools_used=["execute_code"])
    expect(task_dir, good, 0, "tm07 pass")
    bad = make_result_dir(task_dir, tmp, final_answer="；".join(parts[:-1]),
                          tools_used=["execute_code"])
    expect(task_dir, bad, 1, "tm07 fail-missing-mean")
    net = make_result_dir(task_dir, tmp, final_answer="sequence_fetch 请求超时，无法获取序列",
                          tools_used=[])
    expect(task_dir, net, 3, "tm07 netfail")


TM_TESTS = {
    "tm01_batch_search_digest": test_tm01,
    "tm02_batch_file_filter": test_tm02,
    "tm03_kernel_state_reuse": test_tm03,
    "tm04_scientific_imports": test_tm04,
    "tm05_allowlist_boundary": test_tm05,
    "tm06_stdout_spill": test_tm06,
    "tm07_batch_sequence_fetch": test_tm07,
}


def main() -> None:
    print("== harness_benchmark self-test ==", flush=True)
    test_task_parsing()
    test_runner_files_compile()
    test_mac_runner_dry_run()
    tmp = Path(tempfile.mkdtemp(prefix="hb_selftest_"))
    try:
        for name, fn in TM_TESTS.items():
            fn(CODE_TASKS / name, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"== {CHECKS[0]} checks, {len(FAILURES)} failures ==", flush=True)
    if FAILURES:
        for failure in FAILURES:
            print(f"  FAIL {failure}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

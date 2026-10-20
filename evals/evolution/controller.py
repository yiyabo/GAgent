#!/usr/bin/env python3
"""Mac-side controller for the self-evolution harness.

For each selected scenario it builds a manifest (base64 fixtures, check
oracles, rubrics), streams driver.py into the evaluation app container on
620b over SSH, pulls the scenario outputs back, and appends the token usage to
a ledger. The ledger enforces the program's stop line before each scenario.

  python3 evals/evolution/controller.py list
  python3 evals/evolution/controller.py run --round smoke --only S01 --dry-run
  python3 evals/evolution/controller.py run --round smoke --only S01
  python3 evals/evolution/controller.py summarize --round smoke

Blind scenarios live outside the repository (--blind-dir). Their outputs go to
--private-out, never into evals/evolution/runs/ or the handoff package.
"""

import argparse
import base64
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenarios  # noqa: E402  (scenario builders import the local fixtures module)

HOST = "phage-server"
CONTAINER = "gagent-eval-app-1"
REMOTE_OUT = "~/gagent-eval/evo-out"
DRIVER_PATH = HERE / "driver.py"
PLACEHOLDER = "MANIFEST_B64 = None  # replaced by controller.py"
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=30",
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=~/.ssh/cm-evo-%C",
    "-o", "ControlPersist=30m",
]
STOP_TOKENS_DEFAULT = 5_400_000  # authorized by the user on 2026-10-20: pilot, blind set and all public formal scenarios
RUNS_DIR = HERE / "runs"
PRIVATE_OUT_DEFAULT = Path("/Users/apple/code/evolution-private/runs")
BLIND_DIR_DEFAULT = Path("/Users/apple/code/evolution-private/blind")


def load_blind(blind_dir):
    builders = {}
    for path in sorted(Path(blind_dir).glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"blind_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for scenario_id, builder in module.BUILDERS.items():
            builders[scenario_id] = builder
    return builders


def registry(blind_dir):
    public = dict(scenarios.BUILDERS)
    blind = load_blind(blind_dir) if blind_dir else {}
    overlap = sorted(set(public) & set(blind))
    if overlap:
        raise SystemExit(f"scenario ids clash between public and blind sets: {overlap}")
    combined = dict(public)
    combined.update(blind)
    return combined, set(blind)


def build_spec(builder, blind_ids):
    spec = builder()
    spec["blind"] = spec["id"] in blind_ids
    return spec


def build_manifest(spec, round_id):
    fixtures_b64 = {
        name: base64.b64encode(data).decode("ascii") for name, data in spec["fixtures"].items()
    }
    turns = spec["user_turns"]
    return {
        "round": round_id,
        "scenario": {
            "id": spec["id"],
            "lang": spec.get("lang", "en"),
            "persona": spec.get("persona", ""),
            "user_turns": turns,
            "fixtures": fixtures_b64,
        },
        "checks": spec.get("checks", []),
        "judge": spec.get("judge", []),
        "facts": spec.get("facts", ""),
        "budget_tokens": int(spec.get("budget_tokens", 150000)),
        "timeout_seconds": int(spec.get("timeout_seconds", 900)),
        "max_turns": int(spec.get("max_turns", len(turns))),
    }


def compose_script(manifest):
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    if driver.count(PLACEHOLDER) != 1:
        raise SystemExit("driver placeholder must appear exactly once")
    encoded = base64.b64encode(json.dumps(manifest, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return driver.replace(PLACEHOLDER, f'MANIFEST_B64 = "{encoded}"', 1).encode("utf-8")


def ledger_path():
    return RUNS_DIR / "ledger.jsonl"


def ledger_spent():
    path = ledger_path()
    if not path.exists():
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += int(json.loads(line).get("total_tokens") or 0)
    return total


def ledger_append(row):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with ledger_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_remote(script, timeout):
    command = ["ssh", *SSH_OPTS, HOST, f"docker exec -i {CONTAINER} python3 -"]
    return subprocess.run(command, input=script, capture_output=True, timeout=timeout)


def pull_outputs(round_id, scenario_id, dest_root):
    command = ["ssh", *SSH_OPTS, HOST, f"tar -C {REMOTE_OUT}/{round_id} -cf - {scenario_id}"]
    proc = subprocess.run(command, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"pull failed: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
    dest = dest_root / round_id
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        archive.extractall(dest)  # the source is our own evaluation container
    return dest / scenario_id


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_one(spec, round_id, stop_tokens, dry_run, private_out):
    manifest = build_manifest(spec, round_id)
    cap = manifest["budget_tokens"]
    spent = ledger_spent()
    if spent + cap > stop_tokens:
        print(f"SKIP {spec['id']}: spent {spent} + cap {cap} would pass the stop line {stop_tokens}", flush=True)
        return None
    script = compose_script(manifest)
    if dry_run:
        print(
            f"DRY {spec['id']} blind={spec['blind']} checks={len(manifest['checks'])} "
            f"judge={len(manifest['judge'])} turns={len(manifest['scenario']['user_turns'])} "
            f"cap={cap} script_bytes={len(script)}",
            flush=True,
        )
        return None
    started = time.monotonic()
    try:
        proc = run_remote(script, timeout=manifest["timeout_seconds"] + 900)
    except subprocess.TimeoutExpired:
        proc = None
    summary_line = ""
    if proc is not None:
        summary_line = next(
            (line for line in proc.stdout.decode("utf-8", "replace").splitlines() if line.startswith("EVO ")),
            "",
        )
    dest_root = private_out if spec["blind"] else RUNS_DIR
    result = None
    try:
        scenario_dir = pull_outputs(round_id, spec["id"], dest_root)
        result = json.loads((scenario_dir / "result.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - record the failure and reserve the cap
        print(f"WARN {spec['id']}: could not read result ({exc})", flush=True)
    if result is not None and not (result.get("usage") or {}).get("incomplete"):
        tokens = int(result["usage"]["total_tokens"])
        status = result.get("status")
    else:
        tokens = cap  # usage unknown: reserve the whole cap rather than record zero
        status = "unknown"
    ledger_append({
        "ts": utc_now(),
        "round": round_id,
        "scenario": spec["id"],
        "blind": spec["blind"],
        "status": status,
        "total_tokens": tokens,
        "usage_known": status != "unknown",
        "seconds": round(time.monotonic() - started, 1),
    })
    print(
        f"{summary_line or 'EVO ' + spec['id'] + ' status=' + str(status)} | ledger+{tokens} "
        f"total={spent + tokens}",
        flush=True,
    )
    if proc is not None and proc.returncode != 0:
        print("driver stderr tail:", proc.stderr.decode("utf-8", "replace")[-400:], flush=True)
    return result


def cmd_list(args):
    builders, blind_ids = registry(args.blind_dir)
    for sc_id in sorted(builders):
        spec = build_spec(builders[sc_id], blind_ids)
        print(f"{sc_id} blind={spec['blind']} category={spec.get('category')} "
              f"turns={len(spec['user_turns'])} checks={len(spec.get('checks', []))} "
              f"judge={len(spec.get('judge', []))} cap={spec.get('budget_tokens', 150000)}")
    print(f"ledger spent so far: {ledger_spent()} tokens (stop line {args.stop_tokens})")


def cmd_run(args):
    builders, blind_ids = registry(args.blind_dir)
    wanted = [item.strip() for item in args.only.split(",") if item.strip()]
    unknown = [item for item in wanted if item not in builders]
    if unknown:
        raise SystemExit(f"unknown scenario ids: {unknown}")
    for sc_id in wanted:
        spec = build_spec(builders[sc_id], blind_ids)
        run_one(spec, args.round, args.stop_tokens, args.dry_run, Path(args.private_out))


def cmd_summarize(args):
    base = RUNS_DIR / args.round
    rows = []
    for path in sorted(base.glob("*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        checks = result.get("checks") or []
        passed = sum(1 for check in checks if check.get("passed"))
        judges = [str(j.get("score")) for j in result.get("judge") or []]
        usage = result.get("usage") or {}
        rows.append(
            f"| {result.get('scenario')} | {result.get('status')} | {passed}/{len(checks)} | "
            f"{','.join(judges) or '-'} | {usage.get('total_tokens')} | {result.get('seconds')} |"
        )
    text = "\n".join(
        ["| scenario | status | checks | judge | tokens | seconds |", "|---|---|---|---|---|---|"] + rows
    ) + "\n"
    base.mkdir(parents=True, exist_ok=True)
    (base / "summary.md").write_text(text, encoding="utf-8")
    print(text)


FAILURE_CLASS_BY_KIND = {
    "run_ok": "run_error",
    "no_fallback": "fallback_or_bailout",
    "number_line": "wrong_result",
    "number_any": "wrong_result",
    "consistent_count": "wrong_result",
    "consistent_n50": "wrong_result",
    "file_records": "wrong_result",
    "file_exists": "missing_or_unexpected_file",
    "no_new_files": "missing_or_unexpected_file",
    "tool_used": "tool_not_used",
    "terms_any": "expected_statement_missing",
    "terms_none": "forbidden_claim_present",
    "turns_max": "too_many_turns",
}


def classify_failures(result):
    problems = []
    for check in result.get("checks") or []:
        if check.get("passed"):
            continue
        problems.append({
            "class": FAILURE_CLASS_BY_KIND.get(check.get("kind"), "check_error"),
            "source": "check",
            "label": check.get("label") or check.get("kind"),
            "detail": str(check.get("observed") or ""),
        })
    for judge in result.get("judge") or []:
        score = judge.get("score")
        if score == 2:
            continue
        problems.append({
            "class": {1: "judge_partial", 0: "judge_fail"}.get(score, "judge_unparsed"),
            "source": "judge",
            "label": judge.get("id"),
            "detail": str(judge.get("reason") or ""),
        })
    if result.get("status") == "error" and not problems:
        problems.append({"class": "run_error", "source": "run", "label": "run", "detail": str(result.get("error") or "")})
    return problems


def load_results(base):
    if not base.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(base.glob("*/result.json"))]


def md_cell(value):
    return str(value if value is not None else "-").replace("|", "/").replace("\n", " ")[:160]


def cmd_handoff(args):
    round_id = args.round
    results = load_results(RUNS_DIR / round_id)
    public = sorted((r for r in results if not str(r.get("scenario", "")).startswith("B")), key=lambda r: r["scenario"])
    blind = [r for r in load_results(Path(args.private_out) / round_id) if str(r.get("scenario", "")).startswith("B")]
    package = HERE / round_id
    (package / "transcripts").mkdir(parents=True, exist_ok=True)
    (package / "results").mkdir(parents=True, exist_ok=True)
    categories = {sc_id: build_spec(builder, set())["category"] for sc_id, builder in scenarios.BUILDERS.items()}
    for result in public:
        sid = result["scenario"]
        (package / "transcripts" / f"{sid}.md").write_bytes((RUNS_DIR / round_id / sid / "transcript.md").read_bytes())
        (package / "results" / f"{sid}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    table = ["| scenario | category | status | checks | judge | tokens | seconds |", "|---|---|---|---|---|---|---|"]
    for r in public:
        checks = r.get("checks") or []
        passed = sum(1 for c in checks if c.get("passed"))
        scores = ",".join(str(j.get("score")) for j in r.get("judge") or []) or "-"
        usage = r.get("usage") or {}
        table.append(
            f"| {r['scenario']} | {md_cell(categories.get(r['scenario']))} | {r.get('status')} | "
            f"{passed}/{len(checks)} | {scores} | {usage.get('total_tokens')} | {r.get('seconds')} |"
        )
    (package / "scenarios.md").write_text("\n".join(table) + "\n", encoding="utf-8")

    grouped = {}
    for r in public:
        for item in classify_failures(r):
            grouped.setdefault(item["class"], []).append((r["scenario"], item))
    problem_lines = ["# 问题清单（按失败类别）", ""]
    for cls in sorted(grouped):
        problem_lines += [f"## {cls}", ""]
        for sid, item in grouped[cls]:
            problem_lines.append(f"- {sid} · {item['label']}: {item['detail'][:300]}（转录：transcripts/{sid}.md）")
        problem_lines.append("")
    if not grouped:
        problem_lines.append("本轮公开场景没有失败项。")
    (package / "problems.md").write_text("\n".join(problem_lines) + "\n", encoding="utf-8")

    observation_lines = ["# 观察（非检查失败，供策略设计参考）", ""]
    for r in public:
        usage = r.get("usage") or {}
        runs = r.get("runs") or []
        tools = {tool for run in runs for tool in run.get("tools_used") or []}
        iterations = sum(int(run.get("iterations") or 0) for run in runs)
        if "execute_code" in tools and "bio_tools" in tools:
            observation_lines.append(
                f"- {r['scenario']} · 同时用到 execute_code 与 bio_tools（代码兜底），迭代 {iterations} 次，用量 {usage.get('total_tokens')} token"
            )
        if int(usage.get("total_tokens") or 0) > 120000:
            observation_lines.append(f"- {r['scenario']} · 用量 {usage.get('total_tokens')} token，迭代 {iterations} 次")
    if len(observation_lines) == 2:
        observation_lines.append("本轮没有需要单独记录的观察。")
    (package / "observations.md").write_text("\n".join(observation_lines) + "\n", encoding="utf-8")
    review_path = package / "review.md"
    if not review_path.exists():
        review_path.write_text("# 人工复核\n\n（由人工填写；重新生成交接包时不会覆盖本文件。）\n", encoding="utf-8")

    status_counts = {}
    for r in public:
        status_counts[r.get("status")] = status_counts.get(r.get("status"), 0) + 1
    blind_status = {}
    blind_classes = {}
    for r in blind:
        blind_status[r.get("status")] = blind_status.get(r.get("status"), 0) + 1
        for item in classify_failures(r):
            blind_classes[item["class"]] = blind_classes.get(item["class"], 0) + 1
    blind_lines = ["# 盲测汇总（只含计数，不含场景内容）", "", f"- 场景数：{len(blind)}"]
    blind_lines += [f"- 状态 {key}：{value}" for key, value in sorted(blind_status.items(), key=lambda kv: str(kv[0]))]
    blind_lines += [f"- 失败类别 {key}：{value}" for key, value in sorted(blind_classes.items())]
    (package / "blind_summary.md").write_text("\n".join(blind_lines) + "\n", encoding="utf-8")

    ledger = []
    if ledger_path().exists():
        ledger = [json.loads(line) for line in ledger_path().read_text(encoding="utf-8").splitlines() if line.strip()]
    round_rows = [row for row in ledger if row.get("round") == round_id]
    round_total = sum(int(row.get("total_tokens") or 0) for row in round_rows)
    cumulative = sum(int(row.get("total_tokens") or 0) for row in ledger)
    ledger_lines = [
        "# 预算账本",
        "",
        f"- 本轮：{round_total} token，{len(round_rows)} 次运行",
        f"- 累计（全部轮次）：{cumulative} token",
        f"- 停止线：{args.stop_tokens} token",
        "",
        "| scenario | status | tokens | usage known | seconds |",
        "|---|---|---|---|---|",
    ]
    for row in round_rows:
        sid = "blind" if row.get("blind") else row.get("scenario")
        ledger_lines.append(
            f"| {sid} | {row.get('status')} | {row.get('total_tokens')} | {row.get('usage_known')} | {row.get('seconds')} |"
        )
    (package / "ledger.md").write_text("\n".join(ledger_lines) + "\n", encoding="utf-8")

    readme = [
        f"# 评测轮次 {round_id}",
        "",
        "自我进化评测的交接包，供后续策略设计使用。盲测只保留计数（见 blind_summary.md）。",
        "",
        "## 概况",
        "",
        f"- 公开场景 {len(public)} 个：通过 {status_counts.get('pass', 0)}，失败 {status_counts.get('fail', 0)}，运行错误 {status_counts.get('error', 0)}。",
        f"- 盲测 {len(blind)} 个（只报计数）。",
        f"- 本轮 token {round_total}；累计 {cumulative}；停止线 {args.stop_tokens}。",
        "- 环境：620b 隔离实例 `gagent-eval`，镜像 `gagent-app:p11`（主干 a6fb1111），Agent 模型 qwen3.8-flash；模拟用户 qwen3.8-flash；判分 qwen3.7-max。",
        "- 关闭：长期记忆召回（按请求）、技能学习、后台质量评分、AMEM、LightRAG、MCP 扩展。",
        "",
        "## 文件",
        "",
        "- scenarios.md：公开场景结果表。",
        "- problems.md：按失败类别整理的问题，附转录位置。",
        "- observations.md：非检查失败的观察（代码兜底、用量偏高等）。",
        "- review.md：人工复核记录（重新生成时保留）。",
        "- transcripts/：公开场景的可见转录与检查明细。",
        "- results/：公开场景的结构化结果（不含夹具与 oracle）。",
        "- blind_summary.md：盲测计数。",
        "- ledger.md：本轮用量与停止线。",
        "",
        "## 局限",
        "",
        "- 每个场景通常只跑一次，单次结果有随机性。",
        "- 判分与模拟用户都是模型，需要结合转录复核。",
        "- 确定性检查只覆盖数值、文件、工具使用和关键词，不能替代对回复质量的阅读。",
    ]
    (package / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(f"handoff written to {package}: {len(public)} public, {len(blind)} blind, {round_total} tokens this round")


def cmd_rejudge(args):
    """Re-run only the judge items on a recorded transcript (no agent run, a few thousand tokens)."""
    builders, blind_ids = registry(args.blind_dir)
    sc_id = args.only.strip()
    if sc_id not in builders or sc_id.startswith("B"):
        raise SystemExit("rejudge supports public scenarios only")
    spec = build_spec(builders[sc_id], blind_ids)
    recorded_path = RUNS_DIR / args.from_round / sc_id / "result.json"
    recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
    history = []
    for run in recorded.get("runs") or []:
        history.append({"role": "user", "text": run["user"]})
        history.append({"role": "assistant", "text": run["answer"]})
    manifest = {
        "mode": "rejudge",
        "tag": args.tag,
        "round": args.from_round,
        "scenario": {"id": sc_id},
        "history": history,
        "judge": spec.get("judge", []),
        "facts": spec.get("facts", ""),
    }
    proc = run_remote(compose_script(manifest), timeout=900)
    summary = next(
        (line for line in proc.stdout.decode("utf-8", "replace").splitlines() if line.startswith("REJUDGE ")),
        "",
    )
    if not summary:
        raise SystemExit(f"rejudge produced no verdict: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
    fetch = subprocess.run(
        ["ssh", *SSH_OPTS, HOST, f"cat {REMOTE_OUT}/{args.from_round}/{sc_id}/rejudge_{args.tag}.json"],
        capture_output=True,
        timeout=120,
    )
    if fetch.returncode != 0:
        raise SystemExit("could not read the rejudge file")
    verdict = json.loads(fetch.stdout.decode("utf-8"))
    dest = RUNS_DIR / args.from_round / sc_id / f"rejudge_{args.tag}.json"
    dest.write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    tokens = int(verdict["usage"]["total_tokens"])
    ledger_append({
        "ts": utc_now(),
        "round": f"{args.from_round}-rejudge-{args.tag}",
        "scenario": sc_id,
        "blind": False,
        "status": "rejudge",
        "total_tokens": tokens,
        "usage_known": True,
        "seconds": 0,
    })
    print(f"{summary} | ledger+{tokens} total={ledger_spent()}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blind-dir", default=str(BLIND_DIR_DEFAULT) if BLIND_DIR_DEFAULT.exists() else None)
    parser.add_argument("--stop-tokens", type=int, default=STOP_TOKENS_DEFAULT)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run_p = sub.add_parser("run")
    run_p.add_argument("--round", required=True)
    run_p.add_argument("--only", required=True, help="comma-separated scenario ids")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--private-out", default=str(PRIVATE_OUT_DEFAULT))
    sum_p = sub.add_parser("summarize")
    sum_p.add_argument("--round", required=True)
    rej_p = sub.add_parser("rejudge")
    rej_p.add_argument("--from-round", required=True)
    rej_p.add_argument("--only", required=True)
    rej_p.add_argument("--tag", required=True)
    hand_p = sub.add_parser("handoff")
    hand_p.add_argument("--round", required=True)
    hand_p.add_argument("--private-out", default=str(PRIVATE_OUT_DEFAULT))
    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "summarize":
        cmd_summarize(args)
    elif args.cmd == "handoff":
        cmd_handoff(args)
    elif args.cmd == "rejudge":
        cmd_rejudge(args)


if __name__ == "__main__":
    main()

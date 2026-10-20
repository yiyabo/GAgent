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
import driver  # noqa: E402  (stdlib-only at import time; used for the judge evidence digest)
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
STOP_TOKENS_DEFAULT = 0  # 0 = no stop line: the user lifted the token budget on 2026-10-20 (it was 5.4M); the ledger still records usage
GUARD_TOKENS_DEFAULT = 1_500_000  # per-scenario runaway guard between turns; raises any smaller scenario cap
ROUTE_KINDS = {"tool_used"}  # which route the agent took is not graded (user decision, 2026-10-20)
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


def build_manifest(spec, round_id, guard_tokens=0):
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
        "budget_tokens": max(int(spec.get("budget_tokens", 150000)), int(guard_tokens)),
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


def over_stop_line(spent, cap, stop_tokens):
    return bool(stop_tokens) and spent + cap > stop_tokens


def run_one(spec, round_id, stop_tokens, dry_run, private_out, guard_tokens=0):
    manifest = build_manifest(spec, round_id, guard_tokens)
    cap = manifest["budget_tokens"]
    spent = ledger_spent()
    if over_stop_line(spent, cap, stop_tokens):
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
    print(f"ledger spent so far: {ledger_spent()} tokens (stop line {args.stop_tokens or 'none'})")


def cmd_run(args):
    builders, blind_ids = registry(args.blind_dir)
    wanted = [item.strip() for item in args.only.split(",") if item.strip()]
    unknown = [item for item in wanted if item not in builders]
    if unknown:
        raise SystemExit(f"unknown scenario ids: {unknown}")
    for sc_id in wanted:
        spec = build_spec(builders[sc_id], blind_ids)
        run_one(spec, args.round, args.stop_tokens, args.dry_run, Path(args.private_out), args.guard_tokens)


def cmd_summarize(args):
    base = RUNS_DIR / args.round
    rows = []
    for path in sorted(base.glob("*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        checks = graded_checks(result)
        passed = sum(1 for check in checks if check.get("passed"))
        judges = [str(j.get("score")) for j in result.get("judge") or []]
        usage = result.get("usage") or {}
        rows.append(
            f"| {result.get('scenario')} | {effective_status(result)} | {passed}/{len(checks)} | "
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


def graded_checks(result):
    return [check for check in result.get("checks") or [] if check.get("kind") not in ROUTE_KINDS]


def effective_status(result):
    """Recorded status with the route checks left out (results recorded before the route policy)."""
    status = result.get("status")
    if status not in ("pass", "fail"):
        return status
    checks_ok = all(check.get("passed") for check in graded_checks(result))
    judge_ok = all(item.get("score") == 2 for item in result.get("judge") or [])
    return "pass" if checks_ok and judge_ok else "fail"


def v2_status(result, round_id):
    """Status after the route policy, an offline rescore or a rerun; a rerun wins over a rescore."""
    sid = result["scenario"]
    rerun = RUNS_DIR / f"{round_id}-v2" / sid / "result.json"
    if rerun.exists():
        return f"{effective_status(json.loads(rerun.read_text(encoding='utf-8')))}（重跑）"
    rescore = RUNS_DIR / result.get("round", round_id) / sid / "rescore_v2.json"
    if rescore.exists():
        return f"{json.loads(rescore.read_text(encoding='utf-8')).get('v2_status')}（离线重算）"
    return str(effective_status(result))


def classify_failures(result):
    problems = []
    for check in graded_checks(result):
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

    v2_state = {r["scenario"]: v2_status(r, round_id) for r in public}
    table = [
        "| scenario | category | 记录状态 | v2 状态 | checks | judge | tokens | seconds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in public:
        checks = graded_checks(r)
        passed = sum(1 for c in checks if c.get("passed"))
        scores = ",".join(str(j.get("score")) for j in r.get("judge") or []) or "-"
        usage = r.get("usage") or {}
        table.append(
            f"| {r['scenario']} | {md_cell(categories.get(r['scenario']))} | {r.get('status')} | "
            f"{v2_state[r['scenario']]} | {passed}/{len(checks)} | {scores} | {usage.get('total_tokens')} | {r.get('seconds')} |"
        )
    table += ["", "记录状态是运行当时的原始结果；v2 状态在路线不计分、离线重算或重跑之后得出，两者都保留。"]
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
        if "bio_tools" not in tools and "execute_code" in tools:
            observation_lines.append(
                f"- {r['scenario']} · 只用代码（没有用 bio_tools），迭代 {iterations} 次，用量 {usage.get('total_tokens')} token（路线不计分）"
            )
        if "execute_code" in tools and "bio_tools" in tools:
            observation_lines.append(
                f"- {r['scenario']} · 同时用到 execute_code 与 bio_tools（代码兜底），迭代 {iterations} 次，用量 {usage.get('total_tokens')} token"
            )
        if int(usage.get("total_tokens") or 0) > 120000:
            observation_lines.append(f"- {r['scenario']} · 用量 {usage.get('total_tokens')} token，迭代 {iterations} 次")
    if len(observation_lines) == 2:
        observation_lines.append("本轮没有需要单独记录的观察。")
    (package / "observations.md").write_text("\n".join(observation_lines) + "\n", encoding="utf-8")
    v2_lines = [
        "# v1 到 v2 的离线重算与重判",
        "",
        "离线重算与重判不重跑 Agent，只修正检查或判分的口径问题；重跑（v2 场景）单独标明并计入账本。v1 原始结果保留在 results/ 与 transcripts/ 中。",
        "",
    ]
    found_v2 = False
    for r in public:
        sid = r["scenario"]
        scenario_dir = RUNS_DIR / r.get("round", round_id) / sid
        rescore = scenario_dir / "rescore_v2.json"
        if rescore.exists():
            found_v2 = True
            data = json.loads(rescore.read_text(encoding="utf-8"))
            v2_lines.append(f"## {sid}")
            v2_lines.append(f"- 方法：{data.get('method', '')}")
            v2_lines.append("- v1 检查：" + ", ".join(
                f"{c['label']}={'通过' if c['passed'] else '未过'}" for c in data.get("original_checks", [])))
            v2_lines.append("- v2 检查：" + ", ".join(
                f"{c['label']}={'通过' if c['passed'] else '未过'}" for c in data.get("v2_checks", [])))
            v2_lines.append(f"- v2 状态：{data.get('v2_status')}")
            if data.get("note"):
                v2_lines.append(f"- 说明：{data['note']}")
            v2_lines.append("")
        for rejudge in sorted(scenario_dir.glob("rejudge_*.json")):
            found_v2 = True
            data = json.loads(rejudge.read_text(encoding="utf-8"))
            v2_lines.append(f"## {sid} · 重判 {data.get('tag')}")
            for j in data.get("judge", []):
                v2_lines.append(f"- {j.get('id')}: {j.get('score')} — {str(j.get('reason') or '')[:240]}")
            v2_lines.append(f"- 用量：{(data.get('usage') or {}).get('total_tokens')} token（只含重判）")
            v2_lines.append("")
        rerun_path = RUNS_DIR / f"{round_id}-v2" / sid / "result.json"
        if rerun_path.exists():
            found_v2 = True
            rerun = json.loads(rerun_path.read_text(encoding="utf-8"))
            (package / "transcripts" / f"{sid}_v2.md").write_bytes((rerun_path.parent / "transcript.md").read_bytes())
            rerun_checks = graded_checks(rerun)
            v2_lines += [
                f"## {sid} · 重跑（v2 场景，原结果保留）",
                f"- 状态：{effective_status(rerun)}（记录 {rerun.get('status')}）",
                "- 检查：" + ", ".join(f"{c['label']}={'通过' if c['passed'] else '未过'}" for c in rerun_checks),
                "- 判分：" + (", ".join(f"{j.get('id')}={j.get('score')}" for j in rerun.get("judge") or []) or "-"),
                f"- 用量：{(rerun.get('usage') or {}).get('total_tokens')} token（计入账本）",
                f"- 转录：transcripts/{sid}_v2.md",
                "",
            ]
    if not found_v2:
        v2_lines.append("本轮没有离线重算或重判记录。")
    (package / "v2_rescores.md").write_text("\n".join(v2_lines) + "\n", encoding="utf-8")
    review_path = package / "review.md"
    if not review_path.exists():
        review_path.write_text("# 人工复核\n\n（由人工填写；重新生成交接包时不会覆盖本文件。）\n", encoding="utf-8")

    status_counts = {}
    v2_counts = {}
    for r in public:
        status_counts[r.get("status")] = status_counts.get(r.get("status"), 0) + 1
        key = v2_state[r["scenario"]].split("（")[0]
        v2_counts[key] = v2_counts.get(key, 0) + 1
    blind_status = {}
    blind_classes = {}
    blind_rejudged = 0
    for r in blind:
        merged = dict(r)
        rejudges = sorted((Path(args.private_out) / round_id / r["scenario"]).glob("rejudge_*.json"))
        if rejudges:
            blind_rejudged += 1
            merged["judge"] = json.loads(rejudges[-1].read_text(encoding="utf-8")).get("judge", r.get("judge"))
        state = effective_status(merged)
        blind_status[state] = blind_status.get(state, 0) + 1
        for item in classify_failures(merged):
            blind_classes[item["class"]] = blind_classes.get(item["class"], 0) + 1
    blind_lines = ["# 盲测汇总（只含计数，不含场景内容）", "", f"- 场景数：{len(blind)}"]
    if blind_rejudged:
        blind_lines.append(f"- 其中 {blind_rejudged} 个按带工具证据的判分重判，计数取重判结果")
    blind_lines += [f"- 状态 {key}：{value}" for key, value in sorted(blind_status.items(), key=lambda kv: str(kv[0]))]
    blind_lines += [f"- 失败类别 {key}：{value}" for key, value in sorted(blind_classes.items())]
    (package / "blind_summary.md").write_text("\n".join(blind_lines) + "\n", encoding="utf-8")

    ledger = []
    if ledger_path().exists():
        ledger = [json.loads(line) for line in ledger_path().read_text(encoding="utf-8").splitlines() if line.strip()]
    round_rows = [
        row for row in ledger
        if str(row.get("round")) == round_id or str(row.get("round")).startswith(f"{round_id}-")
    ]
    round_total = sum(int(row.get("total_tokens") or 0) for row in round_rows)
    cumulative = sum(int(row.get("total_tokens") or 0) for row in ledger)
    stop_text = f"{args.stop_tokens} token" if args.stop_tokens else "无（用户 2026-10-20 取消预算上限，账本仍记录用量）"
    ledger_lines = [
        "# 用量账本",
        "",
        f"- 本轮（含重跑）：{round_total} token，{len(round_rows)} 次运行",
        f"- 累计（全部轮次）：{cumulative} token",
        f"- 停止线：{stop_text}",
        "",
        "| scenario | round | status | tokens | usage known | seconds |",
        "|---|---|---|---|---|---|",
    ]
    for row in round_rows:
        sid = "blind" if row.get("blind") else row.get("scenario")
        ledger_lines.append(
            f"| {sid} | {row.get('round')} | {row.get('status')} | {row.get('total_tokens')} | "
            f"{row.get('usage_known')} | {row.get('seconds')} |"
        )
    (package / "ledger.md").write_text("\n".join(ledger_lines) + "\n", encoding="utf-8")

    readme = [
        f"# 评测轮次 {round_id}",
        "",
        "自我进化评测的交接包，供后续策略设计使用。盲测只保留计数（见 blind_summary.md）。",
        "",
        "## 概况",
        "",
        f"- 公开场景 {len(public)} 个。记录状态：通过 {status_counts.get('pass', 0)}，失败 {status_counts.get('fail', 0)}，运行错误 {status_counts.get('error', 0)}。",
        f"- v2 状态（路线不计分、离线重算或重跑之后）：通过 {v2_counts.get('pass', 0)}，失败 {v2_counts.get('fail', 0)}，运行错误 {v2_counts.get('error', 0)}。",
        f"- 盲测 {len(blind)} 个（只报计数）。",
        f"- 本轮 token {round_total}；累计 {cumulative}；停止线：{stop_text}。",
        "- 评分口径：答案对就算对，Agent 用 bio_tools 还是自己写代码不计分（用户 2026-10-20 决定）；路线只作为观察记录在 observations.md。",
        "- 环境：620b 隔离实例 `gagent-eval`，镜像 `gagent-app:p11`（主干 a6fb1111），Agent 模型 qwen3.8-flash；模拟用户 qwen3.8-flash；判分 qwen3.7-max。",
        "- 关闭：长期记忆召回（按请求）、技能学习、后台质量评分、AMEM、LightRAG、MCP 扩展。",
        "",
        "## 文件",
        "",
        "- scenarios.md：公开场景结果表。",
        "- problems.md：按失败类别整理的问题，附转录位置。",
        "- observations.md：非检查失败的观察（代码兜底、用量偏高等）。",
        "- review.md：人工复核记录（重新生成时保留）。",
        "- v2_rescores.md：v1 到 v2 的离线重算、重判与重跑记录。",
        "- transcripts/：公开场景的可见转录与检查明细。",
        "- results/：公开场景的结构化结果（不含夹具与 oracle）。",
        "- blind_summary.md：盲测计数。",
        "- ledger.md：本轮用量。",
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
    if sc_id not in builders:
        raise SystemExit(f"unknown scenario id: {sc_id}")
    is_blind = sc_id in blind_ids
    root = Path(args.private_out) if is_blind else RUNS_DIR
    spec = build_spec(builders[sc_id], blind_ids)
    recorded_path = root / args.from_round / sc_id / "result.json"
    recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
    events_path = recorded_path.parent / "events.jsonl"
    evidence = ""
    if events_path.exists():
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        evidence = driver.evidence_digest(events)
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
        "evidence": evidence,
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
    dest = root / args.from_round / sc_id / f"rejudge_{args.tag}.json"
    dest.write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
    tokens = int(verdict["usage"]["total_tokens"])
    ledger_append({
        "ts": utc_now(),
        "round": f"{args.from_round}-rejudge-{args.tag}",
        "scenario": sc_id,
        "blind": is_blind,
        "status": "rejudge",
        "total_tokens": tokens,
        "usage_known": True,
        "seconds": 0,
    })
    print(f"{summary} | ledger+{tokens} total={ledger_spent()}", flush=True)


def cmd_rescore_route(args):
    """Offline: write rescore_v2.json for recorded results that fail only on a route check."""
    written = []
    for path in sorted((RUNS_DIR / args.round).glob("*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        route_failed = [c for c in result.get("checks") or [] if c.get("kind") in ROUTE_KINDS and not c.get("passed")]
        target = path.parent / "rescore_v2.json"
        v2 = effective_status(result)
        if not route_failed or v2 == result.get("status") or target.exists():
            continue
        tools = sorted({tool for run in result.get("runs") or [] for tool in run.get("tools_used") or []})
        target.write_text(json.dumps({
            "method": "route policy: the route check is left out; no agent run, no tokens",
            "original_checks": [{"label": c.get("label"), "passed": bool(c.get("passed"))} for c in result.get("checks") or []],
            "v2_checks": [{"label": c.get("label"), "passed": bool(c.get("passed"))} for c in graded_checks(result)],
            "v2_status": v2,
            "note": f"Every graded check passed; only the route check failed (tools used: {', '.join(tools)}). "
                    "A right answer counts as right whichever route the agent took (user decision, 2026-10-20).",
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(result.get("scenario"))
    print(f"route rescore written for: {', '.join(written) or 'none'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blind-dir", default=str(BLIND_DIR_DEFAULT) if BLIND_DIR_DEFAULT.exists() else None)
    parser.add_argument("--stop-tokens", type=int, default=STOP_TOKENS_DEFAULT, help="0 = no stop line")
    parser.add_argument("--guard-tokens", type=int, default=GUARD_TOKENS_DEFAULT,
                        help="per-scenario runaway guard between turns (0 keeps each scenario's own cap)")
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
    rej_p.add_argument("--private-out", default=str(PRIVATE_OUT_DEFAULT))
    resc_p = sub.add_parser("rescore-route")
    resc_p.add_argument("--round", required=True)
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
    elif args.cmd == "rescore-route":
        cmd_rescore_route(args)


if __name__ == "__main__":
    main()

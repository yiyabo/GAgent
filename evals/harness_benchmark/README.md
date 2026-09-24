# Harness Benchmark: 容器内真实链路评测基线（~50+ 任务裁判）

**扩充后的评测基线：57 个任务、机器判定、跑 .8 容器内真实 chat-run 链路。**
用途：每轮大改前后各跑一次，对比 report.md 判定有无退化（成功率 / 耗时 /
token / 兜底率 / 交付物率）。

> 旧的 pi-vs-qwen 委派小套件（8 任务 + `runner.py` + `tasks/`）保留在本文
> 末尾「Legacy」一节，与本套件无关。

## 任务集（57 = 50 + 7）

| 类别 | 编号 | 数量 | 难度分层 | 来源 |
|---|---|---|---|---|
| read_file 读文件 | t01–t08 | 8 | 简单直答 | `evals/pipeline_benchmark/tasks/`（复用，零拷贝） |
| data_analysis 数据分析 | t09–t18 | 10 | 单工具/多工具 | 同上 |
| plotting 画图（含 3 个 CJK 标签） | t19–t28 | 10 | 多工具编排 | 同上 |
| literature 文献检索 | t29–t34 | 6 | 单工具（外网，netfail 单列） | 同上 |
| long_report 长报告 | t35–t40 | 6 | 长程 | 同上 |
| multi_step 多轮深想 | t41–t50 | 10 | 长程 | 同上 |
| **code_mode（execute_code）** | tm01–tm07 | 7 | 单工具→长程 | `tasks_code/`（本目录新增） |

code_mode 任务一览（容器已开 `CODE_MODE_ENABLED=1`）：

| 任务 | 一句话 | check 要点 |
|---|---|---|
| tm01_batch_search_digest | execute_code 循环 web_search ×3，按域名去重汇总 | execute_code 已用 + ≥3 URL + 主题词；netfail |
| tm02_batch_file_filter | execute_code 批量读 6 个 csv，条件过滤 + 分组均值 | 重算总数/保留数/组均值；execute_code 已用；无兜底话术 |
| tm03_kernel_state_reuse | **两轮** execute_code 复用 kernel 变量（mean_v/std_v） | 两轮答案数值重算；第 2 轮 cell 源码可观测时禁止重读 fixture |
| tm04_scientific_imports | kernel 内正常 import numpy/pandas 做相关/拟合 | 重算 Pearson/均值/斜率；import 守卫文案不外泄 |
| tm05_allowlist_boundary | execute_code 内无 file_operations，应改走常规工具 | 笔记三条要点全转述 + 常规读文件工具已用 |
| tm06_stdout_spill | 3200 行打印触发 >50KB 截断，spill 文件含全文 | MEAN 重算 + session spill 文件存在且含 BEGIN/END-DUMP |
| tm07_batch_sequence_fetch | execute_code 循环 sequence_fetch ×3，长度过滤汇总 | 真值在 `truth/expected.json`（UniProt 快照，不进会话）；netfail |

## 架构

```
mac_runner.py            (Mac 侧，唯一直接碰 ssh 的一环)
  │  发现任务(pipeline 50 + tasks_code 7) → manifest(base64 JSON)
  │  一条 ssh（限速纪律：批量进一条连接）:
  │    ssh -o ControlPath=~/.ssh/cm-gagent8 -o BatchMode=yes root@10.110.107.8 \
  │      'docker exec -i -e HB_MANIFEST_B64=<b64> phage-agent python3 -' < suite_driver.py
  │  ControlPath 不在/ssh 失败 → 停下报告，绝不自己重建 master
  ▼
suite_driver.py          (容器内，self-contained 仅 stdlib，经 stdin 管道注入)
  │  逐任务 subprocess（字面量 argv，timeout = meta.timeout_seconds×scale + 240s）
  ▼
container_driver.py      (容器内 /app，每任务一个进程，崩任务不炸套件)
  │  真实链路：start_background_chat_run() → execute_chat_run worker
  │  （无 HTTP 鉴权的内部函数调用；fixtures → session uploads/
  │    + attachments 注入，与生产一致；支持 meta.turns 多轮同会话）
  │  轮询 chat_runs 行到终态 → 捞全部 chat_run_events →
  │  result.json（pipeline 兼容形状）+ events.jsonl
  ▼
suite_driver 每任务后处理:
  session 目录 symlink 进结果目录（pipeline check 约定）→
  truth/（如有）拷入结果目录 → check.py 跑完才拷入（agent 全程看不到判分）→
  按 session_id 从 llm_usage_log 求和 token → results.jsonl + report.md
```

产物：容器侧 `/app/data/evals_harness/<label>/`（= .8
`/data/phage-agent/data/evals_harness/<label>/`）；mac_runner 跑完自动把
`report.md` + `results.jsonl` 拉回 `evals/reports/harness_benchmark/<label>/`
（gitignore 已配：仅 `.gitkeep` 入库）。

## 用法

```sh
# 结构性自测（Mac 本地，无容器无 LLM）：任务定义解析 + check 单测 + dry-run
python3 evals/harness_benchmark/selftest.py

# dry-run：验证 manifest + 打印 ssh argv，不碰 ssh
python3 evals/harness_benchmark/mac_runner.py --dry-run --label baseline_pre

# 首跑（部署窗口内由主代理统一安排；57 任务约数小时，nohup 后台）
nohup python3 evals/harness_benchmark/mac_runner.py --label baseline_pre \
    > /tmp/hb_baseline_pre.log 2>&1 &

# 子集 / 续跑 / 拉细节
python3 evals/harness_benchmark/mac_runner.py --label smoke_tm --category code_mode
python3 evals/harness_benchmark/mac_runner.py --label t02only --only tm02_batch_file_filter
python3 evals/harness_benchmark/mac_runner.py --label baseline_pre --resume     # 断点续跑
python3 evals/harness_benchmark/mac_runner.py --label baseline_pre --pull-details
```

两次跑分对比 `report.md` 即可：成功率（剔除 netfail）、mean/p50/p95 耗时、
兜底率（final 事件 metadata.fallback_used）、兜底话术率（bailout-phrase）、
交付物率、总 token。

## 任务格式（tasks_code/tmXX\_\*/；pipeline 任务见各自 README）

| 文件 | 说明 |
|---|---|
| `task.md` | 用户消息原文，明确产出要求，不泄露判分细节 |
| `meta.json` | `category`/`tier`/`timeout_seconds` 必填；`difficulty`、`session_prefix`、`turns`（多轮文本列表，给出则替代 task.md 逐轮发送）可选 |
| `check.py` | 自包含，argv[1]=结果目录；真值从 `session/uploads/` fixture 或 `truth/` 重算，**禁止硬编码**；stdlib 即可（本地自测无 pandas） |
| `fixtures/` | 拷入会话 uploads/ 并以 attachments 告知模型（生产同款） |
| `truth/` | 判分专用真值（如 tm07 的 UniProt 快照），**只在 check 阶段**拷进结果目录，agent 永远看不到 |

result.json 形状（与 pipeline_benchmark 兼容 + 扩展）：`task, session_id,
session_dir, run_ids[], ok_run, seconds, final_answer, fallback_used,
bailout_phrase, total_iterations, tools_used[], produced_paths[], turns[],
error`。

## check 返回码与判定纪律

- `0` pass；其他 fail；`3` netfail（仅外网任务，网络信号词触发，单独统计不计入分母）
- 判定全部确定性：交付物/文件存在性、数值重算匹配、execute_code 使用证据
  （final 事件 metadata.tools_used + thinking_step 事件计数）、兜底话术标记
  （`BAILOUT_MARKERS`：LLM 不可用族 + "暂未形成结构化结论"族，见
  container_driver.py）、spill 物理文件。无 LLM 评判。
- token 归因：`/app/data/databases/main/plan_registry.db` 的
  `llm_usage_log` 按任务唯一 session_id 求和（chat-run 链路里
  `build_agent_for_chat_request` 自动 `set_usage_context`）。

## 首跑操作手册（部署窗口执行，跑分期间容器不重启）

1. 主代理把本分支同步到 .8 仓库 checkout（bind-mount 即时生效，无需重启容器）。
2. 确认 SSH master 通道在：`ls ~/.ssh/cm-gagent8`；不在则按
   `docs/LOCAL_INFRA.md`「.8 SSH 实操」手动建（runner 不会自建）。
3. Mac 上先跑 `python3 evals/harness_benchmark/selftest.py`（应 0 failures）。
4. 冒烟：`python3 evals/harness_benchmark/mac_runner.py --label smoke_tm02 --only tm02_batch_file_filter`
   —— 单任务打通 ssh → docker exec → chat-run → check → 报告回拉全链路。
   预期：`evals/reports/harness_benchmark/smoke_tm02/report.md` 生成，
   1/1 或按真实表现记录；容器侧 `/app/data/evals_harness/smoke_tm02/` 有
   result.json/events.jsonl/session 链接。
5. 全量：`nohup python3 evals/harness_benchmark/mac_runner.py --label baseline_pre > /tmp/hb_baseline_pre.log 2>&1 &`
   预期 57 任务、数小时；中断后用 `--resume` 续跑。
6. 大改后再跑 `--label baseline_post --resume`，对比两份 report.md。

已知限制：literature/tm01/tm07 依赖外网（netfail 机制）；tm03 的 kernel
复用证据依赖 final metadata 里的 tool_results 参数快照，被卫生裁剪时自动
降级为数值判定（check 输出注明）；tm06 依赖 ToolContext.work_dir=会话目录
（生产链路成立）。

---

## Legacy: pi vs qwen-code（2026-09-19，已定论）

Measures the two nested-delegation CLI backends on identical small tasks.
Result at the time: **pi 8/8, qwen-code 0/8**; code_executor has since been
switched to the pi shim (docs/LOCAL_INFRA.md).

### Run (on .8, inside the runtime container)

```sh
docker run --rm -v /data/phage-agent:/app -w /app \
  gagent-qwen-code-runtime:pi-0.85 \
  sh -c "set -a; . /app/.env; set +a; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy; \
         python3 evals/harness_benchmark/runner.py --harness pi --out /app/data/tools/bench_pi.json"
# then the same with --harness qwen --out /app/data/tools/bench_qwen.json
```

### Legacy tasks

| task | skill |
|---|---|
| t1_csv_count | read csv, count rows, write result |
| t2_json_extract | filter+sort json fields |
| t3_fix_bug | fix off-by-one so provided test passes |
| t4_name_swap | text transform |
| t5_sum_log | parse log ignoring comments, float sum |
| t6_dedup | dedupe+sort |
| t7_md_titles | multi-file aggregation |
| t8_top_words | frequency analysis |

Legacy tasks live in `tasks/tN_name/{task_prompt.md, fixtures/, check.py}` —
`runner.py` picks up any directory containing `task_prompt.md`.

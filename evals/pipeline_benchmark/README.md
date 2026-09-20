# Pipeline Benchmark: DeepThinkAgent 真实链路回归跑分

50 个标准任务，在容器内用**生产同款构造**的 DeepThinkAgent 跑完整链路
（LLM → 工具执行 → 交付物），量出成功率 / 耗时 / token / 兜底率 /
交付物存在性，用于 `refactor/deep-think-split` 拆分前后两次跑分对比，
证明 god class 拆分无行为退化。

## 架构

```
runner.py  (套件编排，容器内 python3 直接跑)
  │  逐任务 subprocess（字面量 argv，proxy 环境变量已剥离）
  ▼
driver.py  (单任务驱动器)
  │  1. APP_RUNTIME_ROOT=<work_root>/runtime，拷 fixtures → session/uploads/
  │  2. init_db() + init_llm_usage_table()        # 复刻 app 启动，token 落主库
  │  3. set_usage_context(session_id=benchTNN_xxxxxx)
  │  4. DeepThinkAgent(llm_client=get_llm_service(),
  │       available_tools=get_all_tools(),          # app/routers/chat/request_routing.py
  │       tool_executor=async (name, params) ->     # tool_box.execute_tool + 显式 ToolContext
  │           execute_tool(name, tool_context=ToolContext(sid, work_dir=session_dir), **params),
  │       max_iterations=64(默认), tool_timeout=120,
  │       request_profile={session_id, request_tier, intent_type})
  │  5. asyncio.wait_for(agent.think(query, context={session_id, chat_history: []}))
  │  6. 任何结果（含异常/超时）都写 result.json，exit 0 —— 单任务崩溃不炸套件
  ▼
check.py   (任务跑完才拷入结果目录 —— agent 全程看不到判分逻辑)
  │  argv[1]=结果目录（内含 result.json + session/ 符号链接）
  │  从 session/uploads/ 的原始 fixture 独立重算真值，与 agent 交付物对比
  │  exit 0=pass / 其他=fail / 3=netfail（外网故障，不算退化）
  ▼
runner 汇总：按 session_id 从 llm_usage_log 求和 token →
  results.jsonl + report.md + 每任务子目录（result.json/check.py 副本/session 链接/driver stdout 尾部）
```

## 运行（.8 容器）

```sh
docker exec phage-agent python3 /app/evals/pipeline_benchmark/runner.py \
    --out /app/data/evals/before
# 切到拆分后分支再跑一次
docker exec phage-agent python3 /app/evals/pipeline_benchmark/runner.py \
    --out /app/data/evals/after

# 子集 / 调超时
docker exec phage-agent python3 /app/evals/pipeline_benchmark/runner.py \
    --out /app/data/evals/smoke --only t01,t19,t29 --timeout-scale 1.5
```

两次跑分对比 `report.md` 即可：成功率（剔除 netfail）、平均/p50/p95 耗时、
兜底率、交付物率、总 token。

## 任务格式

`tasks/tNN_<slug>/`：

| 文件 | 说明 |
|---|---|
| `task.md` | 用户消息原文（中文为主），明确指定产出文件名与目录（`deliverables/` 或 `results/`），**不泄露判分细节** |
| `meta.json` | `{"category", "tier", "timeout_seconds"}` 必填；`"max_iterations"`（默认 64）、`"tools"`（默认 `get_all_tools()` 全集）、`"session_prefix"` 可选 |
| `check.py` | 自包含判分脚本，`argv[1]`=结果目录；真值一律从 `session/uploads/` 的 fixture 重算 |
| `fixtures/` | 自造小数据（几十到几百行），xlsx 为真实二进制（pandas+openpyxl 生成） |

六类配比：

| 类别 | 编号 | tier / timeout | 判分要点 |
|---|---|---|---|
| read_file 读文件 | t01–t08 | standard / 600s | final_answer 含重算事实串（csv/md/json/log/py(AST)/fasta/xlsx/双文件） |
| data_analysis 数据分析 | t09–t18 | execute / 900s | pandas 重算对比 `results/*.csv`（两位小数后容差 1e-6） |
| plotting 画图 | t19–t28 | execute / 900s | PNG 存在、>10KB、魔数、PIL 像素方差>100 防空图；t26–t28 三个 CJK 任务另需 final_answer 内联 `![...](...)` |
| literature 文献检索 | t29–t34 | research / 600s | 关键词（含中/英 any-group）+ 至少一个 http URL；外网故障 exit 3=netfail |
| long_report 长报告 | t35–t40 | execute / 1500s | md ≥1500 非空白字符、必需小节标题、Markdown 表格、重算关键数字 |
| multi_step 多轮深想 | t41–t50 | execute / 1500s | 分析 csv 重算对比 + PNG 校验 + 报告≥600 字且数字与分析一致 |

## check 返回码约定

- `0` pass
- 其他（assert 失败等）fail
- `3` netfail：仅文献检索类使用。final_answer 或 error 中含明确网络/超时信号
  （"搜索失败/网络错误/timed out/connection error/..."）时返回；runner 单独统计，
  **不计入成功率分母**，两次跑分对比时排除。驱动器崩溃（非网络信号）仍记 fail，
  不会被 netfail 掩盖。

## 指标定义（report.md / results.jsonl）

- `ok`：check.py 返回 0；`ok_run`：driver 无未捕获异常跑完 agent.think
- `seconds`：driver 墙钟（构造 agent + think 全程）
- `tokens`：按任务唯一 session_id（`benchTNN_xxxxxx`）从 `llm_usage_log`
  求和 `total_tokens`（另记 prompt/completion/calls）
- `fallback_used` / `iterations` / `tools_used`：DeepThinkResult 字段
- `n_produced`：`agent._produced_deliverable_paths` 长度（交付物率 = n_produced>0 占比）
- `acceptance_missing`：`agent._acceptance_missing` 快照
- 聚合：成功率（剔除 netfail）、mean/p50/p95 耗时、兜底率、交付物率、总 token

## token 归因实现结论

- `llm_usage_log` 在**主库** `$DB_ROOT/main/plan_registry.db`
  （默认 `data/databases/main/plan_registry.db`，容器内即
  `/app/data/databases/main/plan_registry.db`；施工单里说的 `tasks.db`
  只是 `database_pool` 未初始化时的自动回退名，生产 app 启动时
  `init_db()` 已把连接池指到主库——driver 复刻了这一步）。
- 关键列：`session_id`、`total_tokens`、`prompt_tokens`、`completion_tokens`；
  过滤键 `WHERE session_id = ?`（参数化单行 SQL，见 runner.py `sum_tokens`）。
- driver 在 `agent.think` 前 `set_usage_context(session_id=...)`（生产同款，
  见 `app/routers/chat/stream_context.py`），deep_think 内部
  `update_usage_context(call_purpose="deep_think_iteration")` 只合并字段，
  不会覆盖 session_id。
- DB 查找顺序：`$LLM_USAGE_DB` → `$DB_ROOT/main/plan_registry.db` →
  `cwd/data/databases/main/plan_registry.db` → `/app/...` → `/app/tasks.db`。
  找不到含 `llm_usage_log` 的库时 tokens 记 `null`（report 标注）。

## 如何加任务

1. `mkdir tasks/t51_<slug>`，写 `task.md`（只说用户视角的要求与产出位置）、
   `meta.json`（category/tier/timeout_seconds）、`check.py`（从
   `session/uploads/` 重算真值），按需放 `fixtures/`。
2. runner 自动发现任何含 `task.md`+`meta.json` 的目录。
3. check.py 必须是自包含脚本（会被拷到结果目录独立运行），顶多用
   pandas/PIL（容器已有）；真值禁止硬编码——从 fixture 重算。

## 已知限制

- **literature 类依赖外网**：搜索引擎/目标站不可达时 check 返回 3（netfail），
  两次跑分对比应排除；极端情况下"答错但提到网络词"会误判 netfail，属可接受噪音。
- **token 归因依赖 llm_usage_log**：driver 若没走到 `set_usage_context`
  （如 import 即崩溃），该任务 tokens=null；多轮共享会话的调用（无）
  不会串账，因为每任务 session_id 唯一。
- **数值对比口径**：金额类统一"保留两位小数后容差 1e-6"；t37 标准差同时接受
  样本（ddof=1）与总体（ddof=0）两种合法约定；t12/t45 离群点按 pandas 默认
  线性插值四分位，fixture 边距设计得足够大，换插值方法不改集合。
- **画图类**不校验图表语义（无法 OCR），只校验非空/非空白图 + CJK 任务的内联引用。
- driver 固定 `intent_type` 映射（standard→chat，其余→execute_task）；
  `intent_type` 在 deep_think 内部无消费，仅作 request_profile 元数据。
- 本地 venv 只能做 py_compile/--help 级冒烟；真实跑分必须在容器内
  （/app/.env 提供 LLM 凭据，driver 不自行读 .env）。

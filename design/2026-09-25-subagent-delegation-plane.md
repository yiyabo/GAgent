# 子 Agent 委派面设计（把 pi 逐渐变成一等子 Agent）

日期：2026-09-25。状态：**提案（未实现）**。基线：`13b7b45e`。
动机：现有委派能力分散在三条专用路径上，其中计划域已经是成熟的子 Agent 形态，但 chat 路径只能"以代码形式"表达子目标。本档记录现状、缺口、三步走方案，以及一处必须先想清楚的设计张力。

---

## 1. 现状：已经有"子 Agent"，但它是专业化且按域分裂的

| 路径 | 入口 | 契约 | 生产配置 |
|---|---|---|---|
| chat 的 `code_executor` | 模型交一段"编码任务"字符串 | 返回代码执行结果（stdout / 产物路径） | `CODE_EXECUTION_BACKEND=qwen_code`（钉死，绕过 auto） |
| 计划任务委派 | `app/services/plans/task_delegate_executor.py` → `CodeAgentTaskDelegateExecutor` | `TaskDelegationSpec` 进 / `TaskDelegationResult` 出 | `PLAN_TASK_EXECUTION_BACKEND=external_agent`、`PLAN_TASK_AGENT_BACKEND=qwen_code` |
| 审计修复委派 | `app/services/plans/audit_repair_loop.py`（`action="delegate_repair"`） | 同上 + `delegated_repair` 标记 | 同上 |

**计划域那条已经是标准子 Agent 契约**（`task_delegate_executor.py:82-105`）：

- spec 字段：`task_instruction` / `acceptance_criteria` / `artifact_contract` / `resolved_input_artifacts` / `readable_dirs` / `resolved_resources` / `work_dir` / `ancestor_chain`
- result 字段：`status` / `summary` / `artifact_paths` / `stdout` / `stderr`
- 提示词里的三条边界（`_build_delegate_prompt`）：
  1. "Complete only this task; do not create or modify the plan." —— 范围限定
  2. "The orchestration system, not you, decides final task completion after deterministic verification." —— **裁决权归父**
  3. "If inputs are missing, report BLOCKED_DEPENDENCY with a concise DETAIL." —— 失败显式上报
- 上游差异化验收：`task_verification.py:100` 读 `metadata["delegated_task_execution"]` 走不同核验路径

**结论**：pi 在计划域**已经是子 Agent**。缺的不是"子 Agent 能力"，而是**通用性**与**一等公民身份**。

全仓 `grep -rniE "sub-?agent"` 命中 **0** —— 这个概念在代码与文档里都不存在。

---

## 2. 缺口

1. **没有通用委派工具**：chat 路径只能"以代码形式"表达子目标（"写段代码干这个"），不能"以目标形式"表达（"把这件事办了"）。模型被迫把子任务翻译成代码，翻译本身就是失真来源。
2. **spec 是计划形状的**：`plan_id` / `task_id` 必填，chat 侧无法复用这套契约。
3. **路由不统一**：chat 侧后端被 `CODE_EXECUTION_BACKEND=qwen_code` 钉死；计划侧另有 `PLAN_TASK_*` 两个旋钮；代码里那条"分析类走本地快路"的 `auto` + `CODE_EXECUTION_AUTO_STRATEGY=split` 策略**在生产是关闭的**。
4. **子 Agent 不是一等公民**：没有独立 run 行 / 事件流；且 **pi 的 `duration_ms` 不记账**（`llm_usage_log` 里为 0），代价无法观测。
5. **返回面没有硬上限**：现路径返回 stdout / 代码结果，子 Agent 的 trace 有灌回父上下文的风险。（本项目已因这类泄漏打过"全量打印补网"守卫，见 `LOCAL_INFRA` §39。）

---

## 3. 三步走（每步独立可上线、独立回滚）

### S1｜统一委派契约（内部重构，不加新工具）

- 把 `TaskDelegationSpec` 的 `plan_id` / `task_id` 变为**可选**；`CodeAgentTaskDelegateExecutor` 从计划域提升为中立服务。
- chat 与计划域共用**同一条**委派代码路径。
- 行为零变更：计划域调用方式不变，只多一个"无 plan 上下文"的入口。
- 收益：后续所有委派改动只有一个落点。

### S2｜加一个通用工具 `delegate_task`

- 入参：`goal` / `deliverable` / `tool_allowlist` / `budget` / `deadline`。
- 返回**只有**：`{summary, artifact_paths, usage, trace_ref}`，且**文本长度有硬上限**——子 Agent 的 trace 一律不入父上下文（只给 `trace_ref`）。
- **提示词即路由策略**（本项目的既有事实：模型选哪条路，取决于工具描述怎么写，见 §4）。描述文本需明确写清：
  - 适合委派：长程自包含、上下文 churn 高、需要独立试错的工作流（文献扫描、多文件改造、审计修复）
  - 不适合：一次查询；需要父亲眼审阅全部中间结果；需要复用当前 kernel 状态（那用 `execute_code`）

### S3｜子 Agent 成为一等公民 + 用评测测量

- 独立 run 行 + 事件流 + 用量归因（**含补上 pi 的 `duration_ms`**）。
- 然后做 A/B：「模型自己决定」vs「强制委派」，用 `evals/harness_benchmark`（57 任务）对比成功率 / 耗时 / token。
- 只有在有数据之后，"逐渐把 pi 变成 sub-agent"才是可测量的演进，而不是感觉。

---

## 4. 事实依据：提示词就是当前的路由器

模型选择哪条执行路径，**当前完全由工具描述驱动**（不是代码里的 gating）：

- `code_executor` 的描述（`tool_box/native_tool_schemas.py:972`）：
  "Division of labor: code_executor DELEGATES an agentic coding task to the pi coding harness (it writes and debugs the code); execute_code is YOU writing Python directly…"
- `execute_code` 的描述（`tool_box/tools_impl/execute_code/tool.py:37`，自陈 "mirroring the Hermes description"）：
  "Use when you need **3+ tool calls with logic between them**… **Use a normal tool call for a single call** or results you must reason over in full… prefer it for programmatic fan-out over tool results, **not for general software tasks**."

评测观察（`post_refactor` 前 30 条，详见 §6）：8 条任务自发使用 `execute_code`，其中 2 条（`t29`/`t30` 文献类）**完全未用 `code_executor`**，纯靠 code mode 完成；24 条使用 `code_executor`（含 `t01_read_csv_rows` 这种数行数的简单任务——本该走本地快路或一次普通调用）。**这印证两点**：① 描述文本确实是路由；② 路由目前不够精细，简单任务也在往 pi 走。

---

## 5. 设计张力：code mode 与子 Agent 是反向取舍

| | `execute_code`（Hermes 式 code mode） | 子 Agent |
|---|---|---|
| 上下文 | **同一个**，扇出压进 kernel | **独立**，只回摘要 |
| 省 context | 好（大输出在 kernel 里被归约） | 更好（trace 完全不进父） |
| 过程可见性 | 父**全程可见** trace | 父**看不到**过程，只有摘要 |
| 适合 | 数据整理、扇出、需要边看边调 | 长程 + 高 churn + 父本就不打算逐行看 |

两者**不是替代关系**。code mode 已经吃掉了"数据整理类"子 Agent 场景；剩下真正值得开子 Agent 的是长程自包含工作流。这条边界若不在提示词里写清，模型会拿子 Agent 去干本该两行搞定的事。

---

## 6. 决策记录与后续

- 用户 2026-09-25 判断："用 pi 没关系的，类似于一个子 Agent"，并提出"逐渐把 pi 变成 sub-agent"。
- 待办：S1 排在 `post_refactor` 评测跑完之后开工（纯内部、不动行为，是 S2/S3 的地基）。
- 前置测量缺口：pi（`qwen_code_cli`）`duration_ms` 不记账 —— S3 之前必须补，否则子 Agent 的成本治理无数据支撑。

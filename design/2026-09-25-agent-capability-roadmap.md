# Agent 能力路线图（子 Agent / 学习闭环 / 路由策略 / 语义单源）

日期：2026-09-25。状态：**待开工**。基线：`13b7b45e`（评测数据：`post_refactor` 部分跑，35/57）。
前置背景：Hermes 对齐分析见 `2026-09-25-subagent-delegation-plane.md`。

## 0. 为什么是这四件事（证据）

2026-09-25 的 57 任务评测（跑到 35 条主动停止，因为系统仍在迭代中、基线会立刻过期）给出三条硬信号：

1. **同 35 任务对比 09-21 那次**：PASS 28→33、FAIL 6→2、NETFAIL 1→0；两个超时任务（900s / 1020s）现在 162s / 304s 通过。**系统整体在变好**，但这是非受控对比（驱动不同、思考开关不同、上游检索已改造），只能算趋势证据。
2. **唯一薄弱环节是文献检索**：`fallback` 率按类别严重不均——read_file 0/8、data_analysis 0/10、plotting 0/10、**literature 5/6**。2 条失败都是"拿不到精确标识符 + `url present: False` + 走兜底"。
3. **同为文献任务，行为差异极大**：`t30`/`t31` 深挖 23/20 轮后通过；`t32`/`t34` 只跑 5/3 轮就放弃并失败（`t34` 在旧版跑了 15 轮才拿到）。→ **失败模式是"搜不到就早退"，不是"能力不足"**。

## 1. 子 Agent + 并行（最高优先）

**动机**：Hermes 的 "spawn isolated subagents for parallel workstreams" 我们完全没有（全仓 `sub-agent` 零命中；`parallel` 只出现在提示词里，无任何机制）。
**现状**：仅有代码委派（pi）与计划任务委派；chat 侧无法"以目标形式"委派。
**方案**：见 `2026-09-25-subagent-delegation-plane.md` 的 S1/S2/S3。

**前置（本路线图的第一件事）**：pi（`qwen_code_cli`）的 `duration_ms` 不记账（`llm_usage_log` 里为 0）——子 Agent 的成本治理无数据支撑。必须先补。

**判据**：委派调用在 `llm_usage_log` 里有真实 duration；父子 run 可关联；`delegate_task` 返回文本有硬上限（trace 不入父上下文）。

## 2. 学习闭环最小版（把"消费 skill"补成"生产 skill"）

**动机**：Hermes 是 "self-improving"：复杂任务后自主创建 skill、使用中自我改进、跨会话检索召回。我们只有 `app/services/skills/skills_loader.py`——**没有任何 create/save skill 路径**（唯一的 `_write_skill_assets` 是 bio_tools 构建脚本）。原料齐备：skills 热插拔 + 会话历史库 + 评测套件。
**最小闭环**：复杂任务成功 → 生成 skill 草稿 → **闸门评审** → 入库生效。
**闸门用什么**：复杂任务的成功轨迹本身（工具序列 + 产物 + 验收结论）是候选，评测套件是验收闸门（新 skill 不得降低成功率）。
**判据**：至少 1 个由 Agent 产出、经评审入库、并在后续任务里被 `load_skill` 命中的 skill；全程可审计（谁产出、依据哪次任务、谁批准）。

## 3. 路由策略层化 + A/B（把"提示词即路由器"变成可测策略）

**动机**：当前"何时用哪条路"**完全**由工具描述文本驱动，散落在 4 处描述 + `_ENGINEERING_TASK_SUBSTRINGS` 关键词表里，不可测、不可回归。
**证据**：评测里 `t01_read_csv_rows`（数行数这种简单任务）也起了 pi；代码里存在"分析类走本地快路"的 `CODE_EXECUTION_AUTO_STRATEGY=split`，但生产被 `CODE_EXECUTION_BACKEND=qwen_code` 短路，从未生效过。
**方案**：
1. 把路由判定收敛为一个**可调用、可单测的策略函数**（入参：任务文本/上下文/成本预算 → 出参：lane + reason），提示词只负责"告知模型有哪些选择"，不再承担策略。
2. 用评测套件做第一次受控 A/B：`qwen_code`（现状）vs `auto + split`（分析类走本地快路）。这是评测套件**最合适的用法**——比"整体跑分"有意义得多。
**判据**：同一任务集在两个 lane 策略下的成功率/耗时/token 三方对比，且 `execution_lane_reason` 可解释每一次选择。

## 4. 语义单源层（次级，但账已经很清楚）

**动机**：登记册里剩下的"保持现状"项全是**同一类病**：横切语义（状态归一、内部路径判定、验收）没有"单一所有人"，于是散成副本并各自漂移。
**证据**（全量实测）：
- `_normalize_status`：**5 套语义**（TV 权威 + `status_resolver` + `audit_repair_loop` + `job_routes` 四态机 + 一对平凡实现）
- `_is_internal_artifact_path`：**3 份**，其中 `code_executor_helpers.py` 那份**缺 manifest 分支**（真差异，chat 侧会把 `.../deliverables/manifest_latest.json` 判为非内部产物）
- `_ensure_plan_access`：2 份且分叉（audit-repair 版多一道认证门，测试断言 401）
- `_DEFAULT_SECTIONS` / `_DEFAULT_LOCAL_DRAFT_SECTIONS`：双源差 1 项（`experiment`），且 `local_draft.py` 与 `rubrics.py` 的 `draft_only` 策略不一致
**方案**：给状态/路径/验收建共享语义模块，把**已知差异显式枚举**（而不是靠副本各自漂移）。
**判据**：每类语义只有一个定义处；差异以显式参数或枚举表达；每条差异有测试钉住。

## 5. 轨迹导出（可选，管道已完成 90%）

**动机**：Hermes 有 batch trajectory generation + trajectory compression（为训练下一代工具调用模型）。我们的 `events.jsonl`（每任务全事件：思考/工具调用/结果/终态）**只用于判分**。
**方案**：把 events + usage + 判分结论导出为可复盘/可训练格式。
**判据**：给定 label 能一键导出结构化轨迹；能按任务类型/成败切片。

---

## 6. 建议的执行顺序与理由

| 顺序 | 事项 | 理由 |
|---|---|---|
| **1** | pi `duration_ms` 记账 | 小、独立、是所有委派治理的测量前置 |
| **2** | S1 统一委派契约（内部重构） | 不动行为、可立即上线，是 S2/S3 地基 |
| **3** | S2 `delegate_task` + 并行 | 对齐 Hermes 的核心缺口；S1 完成后成本最低 |
| **4** | S3 子 Agent 一等公民（run/事件/归因） | 让 S2 可被观测与治理 |
| **5** | 路由策略层化 + 首次 A/B（split） | 直接回答"简单任务该不该走 pi"；证据已在手 |
| **6** | 学习闭环最小版 | 需要稳定的评测闸门，放在路由 A/B 之后更稳 |
| **7** | 语义单源层 | 价值高但不阻塞其它；可作为穿插项 |
| **8** | 轨迹导出 | 可选，独立 |

**明确不做**：继续跑满 57 任务评测。原因：系统仍在快速迭代，整体跑分基线会立刻过期，投入产出不划算。**改为"按能力点做定向 A/B"**（如 §3），每次只回答一个问题。

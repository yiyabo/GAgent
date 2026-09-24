# 后端巨无霸文件解耦重构主规划

日期：2026-09-24。状态：**待评审（规划，不含代码变更）**。基线：`646ab4e7`。
数据基础：7 路 file:line 级结构解剖（agent-17..23）+ 全量 wc -l 体检 + 重复块 diff 实证。

---

## 0. 结论摘要（TL;DR）

后端 172k 行 Python 中存在 **13 个 ≥2000 行的巨无霸文件**（合计 ~44k 行，占 26%）。它们有**同一个病灶**：每个文件都是"协议翻译层"，在逐 feature 层积中把协议数据、元数据、编排状态机、领域特化逻辑缝进单一文件。其中 **6 个文件拆分条件已成熟**（星型调用拓扑 + 厚测试网 + 已验证的门面迁移模式可整体复用），**4 处同语义重复已实证到字节级**，**3 类层级倒置**需在拆分中一并消除。

推荐节奏：**五个波次、每波独立上线独立回滚**，风险升序：code_executor → phagescope/publisher → manuscript_writer → plan_routes/artifact_routes → plan_executor+task_verification → agent.py 收尾。bio_tools_handler 按用户拍板**冻结不投入**（本平台永不上线 bio-tools）。全部走 §3 的"拆分八步法"（deep_think 拆透已验证的同一套打法）。

---

## 1. 目标 / 非目标

**目标**
- 单文件 ≤2500 行（原 god class 拆分完成定义的同一标尺）；门面/facade 文件 ≤800 行。
- 行为零变更：每步 Mac 全量基线 51=51 逐字节 diff 为空 + chat+unit 全绿 + 容器 import 冒烟。
- 消除层级倒置：tool_box→app.routers 反向边、tool→router 引用、router→router 引用（保留 lazy 形式但登记造册）。
- 每波完成后 AGENTS.md 与 LOCAL_INFRA.md 同步更新。

**非目标**
- 不改任何 LLM 可见行为（schema/prompt/事件载荷/响应 payload 键名）。
- 不做"顺手优化"（逻辑改写、语义合并、性能微调）——重构只做代码搬移；语义问题登记后单独立项（见 §6 重复登记册）。
- 不追求一次到位：允许门面长期存在（兼容层不是技术债，是迁移工具）。

## 2. 盘点（按风险/价值排序）

| # | 文件 | 行数 | 拓扑 | 直属测试 | 拆分成熟度 | 复杂度归因 |
|---|------|------|------|---------|-----------|-----------|
| 1 | `tool_box/tools_impl/code_executor.py` | 6402 | 星型（handler→61/125 fn） | ~270 用例 | ✅ 成熟 | "Claude CLI 薄封装"长成三后端+契约状态机+协议翻译层 |
| 2 | `app/routers/chat/agent.py` | 5671 | 模块级助手星型 + 1857 行流式巨方法 | ~250 用例 | ⚠️ 最后做 | 五系统唯一汇合点，extra_context 事实数据总线 |
| 3 | `app/services/plans/plan_executor.py` | 5493 | god class（4530 行类体） | ~110 用例 | ✅ 成熟（artifact 簇先补 golden） | 执行协议翻译层逐 feature 层积 |
| 4 | `app/routers/chat/action_handlers.py` | 4480 | 星型（1804 行 dispatcher 单函数） | ~173 用例 | ✅ 成熟 | 21 工具 if/elif 参数校正的层积 |
| 5 | `app/services/plans/task_verification.py` | 4279 | 线性管道 | 111 直测 | ✅ 成熟 | 验证权威的"兜底竞速"层积 |
| 6 | `tool_box/tools_impl/manuscript_writer.py` | 3629 | 线性管道 + 1388 行 handler | ~100 用例 | ✅ 成熟 | 管道阶段层积 + 三重身份叠加 |
| 7 | `app/routers/plan_routes.py` | 3523 | 三簇成网 | ~90 用例 | ⚠️ 中（执行引擎最后） | 四种执行模式各加一间房，下沉只做了一半 |
| 8 | `tool_box/tools_impl/phagescope.py` | 3307 | 星型（1339 行 handler 15 分支） | 67 用例 | ✅ 成熟 | 远端 API 怪异 payload 的防御性归一化层积 |
| 9 | `app/routers/chat/action_execution.py` | 2767 | 星型（1258 行后台状态机） | ~60 用例 | ⚠️ 中（cascade patch 面 35 处） | 多租户后台动作状态机 + 混住 HTTP 处理器 |
| 10 | `app/services/deep_think/controller.py` | 2337 | 已拆一轮（编排 221 行 + 8 阶段帮手） | 全量 chat | 🔍 观察 | 本轮刚拆透，暂不再动 |
| 11 | `app/routers/chat/request_routing.py` | 2160 | 大函数簇 | 79 用例 | 🔍 下一批评估 | 路由/意图判定层积 |
| 12 | `app/services/deliverables/publisher.py` | 2036 | 线性管道 | ~50 用例 | ✅ 成熟（内部自由度最高） | 多来源发布状态机 |
| 13 | `tool_box/bio_tools/bio_tools_handler.py` | 2068 | 星型 | 44 用例 | ⛔ **冻结**（用户拍板：bio-tools 本平台永不上线） | — |

第二梯队（1300-2000，规划外备查）：`guardrail_handlers` 1937、`literature_pipeline` 1862、`llm.py` 1809、`deep_think_agent.py` 1785（门面，健康）、`file_operations` 1763、`plan_tools` 1714、`plan_repository` 1708、`artifact_routes` 1707（并入 #7 一并处理）。

## 3. 迁移模式（"拆分八步法"——已两轮验证的打法）

deep_think gating.py（2729→122 门面+5 子模块）与本次侦察报告全部收敛到同一套操作法：

1. **定簇**：按职责把文件切成 3-7 个簇（纯函数/纯数据簇优先）。
2. **建兄弟模块**：簇搬入同目录兄弟文件（同包平铺，不建子目录，避免与既有包名冲突）。
3. **门面 re-export 全量名字**：原文件变门面，`from .sibling import _name` 全量导出——import 站点与测试零改动。
4. **monkeypatch 面晚绑定**：被测试 patch 的名字（常量/函数/类）留在门面定义；兄弟模块调用点经门面**运行时迟绑定**读取（`_dta()` 模式：`def _dta(): from app.services import deep_think_agent; return deep_think_agent`），禁止在兄弟模块顶层 `from .facade import X`。
5. **lazy import 逐字保留**：现状靠 lazy import 维持的循环平衡，一个都不提顶层。
6. **类方法留薄 wrapper**：被外部实例调用的方法留在类上，一行委托给模块函数。
7. **每抽一簇跑测试**：chat+unit 全绿才 commit；小步多 commit（每 commit 可独立 revert）。
8. **收尾全量基线**：Mac 全量 51=51 归一化行号 diff 为空 + 容器 import 冒烟 + AGENTS.md 更新。

三条铁律：**同语义两处先登记不合并**（§6 走拍板）；**payload/schema/事件字面量一个标点不动**；**门面只进不出**（兄弟模块禁止回引门面顶层名字，只可迟绑定）。

## 4. 分文件蓝图（浓缩自解剖报告，全表含行区间与 patch 面）

### 4.1 code_executor.py（6402 → 门面 ~1500 + 6 兄弟）【波次 1 旗舰】

星型拓扑是对拆分最有利的事实；对外契约仅 `code_executor_tool` + `code_executor_handler` + task_executer 的 2 个 qwen env 函数。

| 顺序 | 新模块 | 内容 | 行数 | 风险 |
|---|---|---|---|---|
| ① | `code_executor_semantic.py` | 语义失败协议+成败分类+载荷改写（C2+C3） | ~400 | 最低 |
| ① | `code_executor_cli_parse.py` | JSONL/stderr 解析（C4） | ~380 | 低 |
| ② | `code_executor_qwen.py` | qwen 进程生命周期+命令/挂载/env（C5+C7半） | ~1400 | 中 |
| ③ | `code_executor_contracts.py` | 任务契约/执行 spec（C8+C10半） | ~900 | 中（4316 lazy 反向边保留） |
| ③ | `code_executor_promotion.py` | 结果提升状态机（C9+C10半） | ~800 | 中高（`_RUNTIME_DIR`/`_PROJECT_ROOT` patch 面 ×12） |
| ④ | `code_executor_backend.py` | 后端配置+local 后端（C6+C11+C7半） | ~1200 | 高（`_execute_task_locally`×6、`_generate_task_dir_name_llm`×5 patch 面） |

硬约束：53 个私有名被测试直接 import（门面全量 re-export）；30 处 monkeypatch 全打门面命名空间（晚绑定强制）；`_detect_partial_completion` 与 gating_probe 同名不同层——**借拆分给本文件侧改名留注，禁止同空间 re-export**；`_is_path_within`/`_is_path_within_lexical` 是有意成对，勿合并。
错位处置：用量记账（→ 评估归 `app/services/llm` 或保留 lazy）、prompt 模板（→ 本包 prompts 簇）、技能指引（保持 lazy 依赖 skills）。
验证门：`pytest app/tests/tools app/tests/plan -q` 逐步全绿（tool_box/AGENTS.md:57 口径）。

### 4.2 phagescope.py（3307 → 门面 ≤700 + 6 兄弟）【波次 2】

① `phagescope_protocol.py`（9 张协议映射表+payload/响应判定 ~410，零风险）→ ② `phagescope_normalize.py`（LLM 入参归一化 ~400）→ ③ `phagescope_transport.py`（~130）→ ④ `phagescope_artifacts.py`（~180）→ ⑤ `phagescope_taskid.py`（~180；与 session_helpers 的重复**只标注共存**，第三期才合并）→ ⑥ `phagescope_batch.py`（~520；**batch 递归调 handler 必须经门面属性调用**——test_phagescope_batch 直接 patch `ph.phagescope_handler`）。handler 15 个 action 分支逐个下沉 `actions_*.py`。尾部 147 行 `phagescope_tool` schema dict 迁 tool_registry 登记（与其他 tool 统一）。

### 4.3 publisher.py（2036 → 门面 ~300 + 6 兄弟）【波次 2】

内部自由度最高（patch 面全在消费方命名空间）。顺序：① `policy.py`（EXTS 常量 12 组+路径政策 ~360；`projector.py:28` 直接 import，re-export 保）② `report.py`（~70）③ `manifest.py`（~220）④ `file_ops.py`（复制/水印/图片重写/归属/冲突/atomic_write ~450，lazy watermark 保留）⑤ `submit_payload.py`（~230）⑥ `manuscript.py`（元数据/bibtex/release/稿件发布 ~540）。`MANUSCRIPT_PDF_STEMS` 与 scripts/archive 修复脚本的手工同步契约登记造册。

### 4.4 manuscript_writer.py（3629 → 包 + 门面 ~150）【波次 3】

管道线性 + review 链自洽 + 测试网厚，条件最成熟之一。目标 `tool_box/tools_impl/manuscript_writer/` 包：
① `schema.py`（纯数据）→ ② `config.py`（评测配置表+旋钮；保持 import 时读取语义）→ ③ `prompts.py`（5 构建器；`_build_merge_prompt` 生产零调用仅测试用——登记不删）→ ④ `rubrics.py` → ⑤ `evidence.py`（review 证据链自洽）→ ⑥ `local_draft.py` → ⑦ `paths.py`（⚠️ `_PROJECT_ROOT`/`_RUNTIME_DIR` patch 面 ×39）→ ⑧ `llm_bridge.py`（⚠️ `_chat`/`update_usage_context` patch 面 ×17，晚绑定）→ ⑨ `pipeline.py`（1388 行 handler 嵌套函数提升为模块级；~50 键成功 payload 与 `.manuscript_writer_<ts>` 目录名两个字符串契约不可动）。
顺带登记：`_env_enabled`/`_resolve_project_path` 与 review_pack_writer 重复（§6）；`_DEFAULT_SECTIONS` 双源问题（§6）。

### 4.5 plan_routes.py + artifact_routes.py（3523+1707 → 两个包）【波次 3】

**plan_routes → 包**（import path 不变是注册契约）：
① DTO→`schemas.py`（纯数据）→ ② 删 `_looks_like_*` router 副本改 import status_resolver（**字节级重复已实证**，机械消重安全）→ ③ `state.py`（6 单例+锁，对象同一性是 patch 兼容关键）→ ④ `effective_state.py`、`dependency_plan.py` → ⑤ `execution_jobs.py`（最后：`_run_full_plan_job` 被 tool_box plan_tools.py:1381 反向引用 + 6 测试文件直调；**顺带把 plan_tools 的 import 改指真实住址，消除 tool→router 层级倒置**）。
**artifact_routes → 包**：`rendering.py`（LaTeX subprocess，先补 1 保底测试）→ `schemas.py` → `session_dirs.py`/`deliverable_store.py`（**patch 门面名+共享 globals 模式是最大陷阱：端点最后一批动，或经 `_facade` 迟绑定**）→ `batch.py`。对 `.chat.*` 的两个 router→router import 改函数内或上移共享 service。
先补测试：SSE job stream（仅 1 用例）、LaTeX 渲染（0 覆盖）。

### 4.6 plan_executor.py + task_verification.py（9772 → 各自门面+兄弟群）【波次 4】

**先消重再拆**（§6 拍板后）：`_normalize_status` 映射分歧（executor 缺 done/error）、`_is_internal_artifact_path` 特例分歧、`_PATH_KEYS` 双源、publish 新旧两路（`artifact_event_stream_enabled` 开关）。
executor 顺序：① `executor_models.py`（数据类零风险）→ ② `executor_text_utils.py` → ③ `executor_prompts.py`/`executor_llm.py` → ④ `executor_artifacts.py`（~1100 引力中心，**拆前补 2-3 个 golden 测试**——artifact 富化/发布簇直接断言少）→ ⑤ `executor_deepthink.py`（605 行委派+qwen 翻译层）→ ⑥ `executor_delegate.py`。**DeepThinkAgent 必须以模块属性留门面**（7 处直接赋值式 patch + 2 处 setattr patch）。
TV 顺序：`verification_cues.py`（词表）→ `verification_paths.py` → `verification_checks.py` → `verification_discovery.py` → `verification_semantic.py` → `verification_records.py`；facade 留主管线 ~1200 行。私有面外泄 3 名（`_has_checks`/`_is_local_path`/`_build_generated_criteria` 被 action_handlers 直调）门面保留。

### 4.7 action_handlers.py + action_execution.py（7247 → 各自门面+兄弟群）【波次 4】

handlers：① `action_analysis.py`（数学修复/验证分析/LLM 总结，无 patch 面）→ ② `action_runtime_context.py`（803-1145 整簇，**顺带吸收 agent.py:1209 的字节级重复副本，消重#1**）→ ③ `action_tool_params.py`（21 工具参数校正段组织为 `normalize_<tool>_params`+注册表；**`execute_tool`/`get_tool_policy`/`is_tool_allowed` 模块级绑定与调用点必须留在 action_handlers 命名空间**——agent.py:5498-5545 compat 桥与 19 处测试 patch 依赖）→ ④ `action_plan_ops.py`/`action_task_ops.py`（子分发；先补 rerun 簇与 task 子分发的定向测试）。
execution：① `action_analysis.py`（与 handlers 的③可同批）→ ② `action_run_retry.py`（retry 簇，DeepThinkAgent 迟绑定）→ ③ 路由处理器迁回 routes.py（2452-2767 错位）→ ④ `_execute_action_run` 本体**不迁只迁被调辅助**（35 处 cascade patch 全按模块属性打 `PlanSession`/job 三件套/`asyncio`/`_CASCADE_MAX_TASKS`）。

### 4.8 agent.py（5671 → 门面 + 10 兄弟）【波次 5 收官，最高风险】

模块级 8 簇先走（全低风险起）：① `continuation_hints.py` ② `phagescope_rewrite.py` ③ `review_loop.py` ④ `response_metadata.py` ⑤ `task_context.py` ⑥ `deterministic_execute.py` ⑦ `code_executor_bridge.py` ⑧ `full_plan_runner.py` ⑨ `action_loop.py` ⑩ **最后**：`unified_stream.py`——1857 行 `process_unified_stream` 按 9 个相位（路由前导→full-plan→图片直出→rerun 订阅→DT 作业创建→工具事件回调机器→实例化+compat→画廊回调→控制器注册→finalize）切 phase 函数，类上留入口方法。
硬约束：49 处 patch（`execute_tool`/`plan_decomposition_jobs`/job 三件套/conftest 全局）全部经迟绑定保住；两个 compat bridge（:4403-4412、:5504-5541）原样保留；15 种 SSE 事件字面量与时序不动；`handle()` vs `get_structured_response()` 的 7 步护栏链重复（§6 拍板后再消）。

## 5. 波次计划（每波独立上线、独立回滚）

| 波次 | 内容 | 前置 | 工作量（agent 工时） | 风险 |
|---|---|---|---|---|
| **W0 准备** | 补测试缺口（SSE stream×1、LaTeX×1、executor artifacts golden×2-3、rerun/task 子分发定向×3）；§6 重复登记册逐条拍板；metrics 基线（行数/测试数存档） | — | 0.5-1 天 | 低 |
| **W1 旗舰** | code_executor.py（§4.1） | W0 | 1-2 天 | 中（patch 面 30 处，模式成熟） |
| **W2 工具组** | phagescope（§4.2）+ publisher（§4.3） | W1 | 1-2 天 | 中低 |
| **W3 管道+路由包** | manuscript_writer（§4.4）+ plan_routes/artifact_routes 包化（§4.5） | W2 | 2-3 天 | 中 |
| **W4 计划域** | plan_executor+TV 消重后拆分（§4.6）+ action 双子（§4.7） | W3 + §6 拍板 | 3-4 天 | 中高 |
| **W5 收官** | agent.py 模块级 9 簇 + unified_stream 相位切（§4.8） | W4 | 3-5 天 | 高（留到最后不是没把握，是依赖它稳定其余面） |

每波验收（同一道门）：逐步 chat+unit 全绿 → 全量基线 51=51 → bundle 部署 .8 → 容器 import 冒烟 + HTTP 200 + 零 traceback → 五方同步 → AGENTS.md/LOCAL_INFRA 更新。**任何一步红了：revert 该 commit，门面回退即恢复原状**（每 commit 自包含）。

## 6. 同语义重复登记册（拆前拍板，禁止自行合并）

| # | 位置 | 关系 | 实证 | 建议处置 |
|---|---|---|---|---|
| D1 | `agent.py:1209` vs `action_handlers.py:803` `_persist_runtime_context` + `_RUNTIME_CONTEXT_KEYS` | **字节级重复** | diff 为空 | 机械消重：留 action_runtime_context，agent 侧 re-export（test_no_fallback_policy 5 patch 经门面保住） |
| D2 | `plan_routes.py:464-522` vs `status_resolver.py:77-135` `_looks_like_*` | **字节级重复** | diff 为空 | 机械消重：删 router 副本改 import |
| D3 | `agent.py` `handle()` vs `get_structured_response()` 7 步护栏链 | 逐字重复 + 每条管线各调 rewrite 2 次 | 报告§agent | 合并为私有管线方法两入口复用（W5 内做） |
| D4 | `plan_executor:5468` vs `TV:4236` `_normalize_status` | **映射分歧**（executor 缺 done/error） | 报告§plan | 以 TV 为准收敛 + 补 executor 侧 done/error 回归测试（行为变化：executor 状态归一补齐，需明示） |
| D5 | `plan_executor:4838` vs `TV:4108` `_is_internal_artifact_path` | **特例分歧**（TV 多 manifest 特例） | 报告§plan | 以 TV 为准收敛 + 补 executor 侧特例测试 |
| D6 | `plan_executor` publish 新路 4581 vs 旧路 4728 | 开关并存（artifact_event_stream_enabled） | 报告§plan | 生产查开关状态：稳定在新路则删旧路（单独 commit 单独可回滚） |
| D7 | `execute_full_plan` 同步循环 vs `_run_full_plan_job` | 双执行引擎 | 报告§routes | 合并为单一 runner 两模式复用（W3 末步） |
| D8 | `_ensure_plan_access`×3（plan_routes vs plan_audit_repair_routes，含语义分叉） | 三份重复+细微分叉 | 报告§routes | 收敛到 services/plans 中立层（W3；分叉点逐个对账） |
| D9 | `code_executor._detect_partial_completion` vs `gating_probe._detect_partial_completion_in_tool_results` | 同名不同层 | 报告§code_exec | 不合并；code_executor 侧改名留注（防同空间 re-export 冲突） |
| D10 | `manuscript_writer` `_env_enabled`/`_resolve_project_path` vs `review_pack_writer:36,90` | 重复实现 | 报告§manuscript | 抽共享小工具（tool_box 内中立位置），两import |
| D11 | `manuscript_writer` `_DEFAULT_SECTIONS` vs `_DEFAULT_LOCAL_DRAFT_SECTIONS` 双源 + `_default_section_list` 取值不一致 | 双源分歧 | 报告§manuscript | 合并单源 + 回归测试（行为变化需明示） |
| D12 | `phagescope` taskid DB 反查 vs `session_helpers.py:409-580` | 跨层同语义 | 报告§phage | 第三期合并到中立服务层（tool_box→app 层界，先共存） |
| D13 | `_run_coroutine_sync` 模块级 vs 同名委托方法（plan_executor） | 同名两形 | 报告§plan | 委托方法改名或删除其一（W4 内做） |

## 7. 层级倒置登记册（拆分中消除，保留 lazy 但登记）

| # | 边 | 处置 |
|---|---|---|
| L1 | `code_executor.py:4316-4317` → `app.routers.chat.*`（lazy） | 保持 lazy；评估上移中立服务（W1 后单独评估） |
| L2 | `plan_tools.py:1381` → `plan_routes`（tool→router） | W3 改指 `plan_routes/state.py`/`execution_jobs.py` 真实住址 |
| L3 | `artifact_routes` → `.chat.artifact_gallery`/`.chat.subject_identity`（router→router） | W3 改函数内 import 或上移共享 service |
| L4 | `phagescope.py` → `app.database`/`session_paths`/`tool_output_resolver`（tool_box→app） | 保持 lazy（层界铁律：tool_box 只可 lazy 依赖 app） |
| L5 | `manuscript_writer` 顶层 `from app.llm import` | W3 拆分时不加剧；`llm_bridge.py` 集中管理 |

## 8. 验证框架

- **每步门**：`.venv/bin/python -m pytest app/tests/chat app/tests/unit -q` 全绿（当前 821）。
- **每波门**：Mac 全量 `app/tests -q` 失败清单 vs 基线（51，归一化行号）diff 为空；容器 `python3 -c "import app.main"` + HTTP 200 + 零 traceback。
- **特殊安全网**：code_executor 有直属 186 用例+53 私有名 import 面；TV 有 111 直测；manuscript 有 38 paper pipeline + 13 resilience；plan_routes 有 25 全链路。**先补后拆**：SSE job stream、LaTeX render、executor artifacts 富化、rerun/task 子分发、runtime-context 簇（W0 完成）。
- **生产观测**：每波部署后观察一周 llm_usage 与 run 时长分布（拆分不得引入可测的性能回归；import 时间经容器冒烟旁证）。

## 9. 风险登记册

| 风险 | 等级 | 缓解 |
|---|---|---|
| monkeypatch 面静默失效（patch 打门面、实现搬走、调用点绑死兄弟模块） | **高** | 铁律#4 晚绑定 + 每文件 patch 面清单进 commit message + 全量测试逐步跑 |
| 循环导入引爆（lazy 提顶层） | 中 | 铁律#5；每波容器 import 冒烟 |
| 事件/载荷字面量漂移（SSE、payload ~50 键、协议 STATUS 行） | 中 | 铁律#2；chat_run_events 喉道校验 STRICT 兜底（§33 机制） |
| executor artifacts 簇测试薄导致拆错不发现 | 中 | W0 先补 golden |
| agent.py unified_stream 相位切改变事件时序 | 高 | 放 W5；切前把 SSE 事件序写成黄金测试（chat_run_events canonical 已有机制） |
| byoryn 侧 barwe 工作再次分叉（deploy/ 容器化在演进中） | 低 | 每波前五方对齐检查；拆分避开 deploy/ |
| 多 agent 并发同仓干扰（本轮已两次坐实） | 低 | 每波只开一个手术 agent；其他工作流避开该文件集 |

## 10. 治理（防再长出来）

- AGENTS.md 每个拆完的包更新"WHERE TO LOOK"与行数预算（单文件 ≤2500 作为 review 红线写进对应 AGENTS.md ANTI-PATTERNS）。
- 新职责归属规则（写进 app/AGENTS.md）：协议数据表随协议所有方；prompt 模板进 prompts 模块；schemas dict 归 tool_registry/native_tool_schemas；router 只留薄端点（业务引擎必下沉 services）；tool_box 对 app 只可 lazy 依赖。
- 每季度跑一次本规划 §2 的体检命令（find+wc 排行），新文件进入 2000 行警戒线即评审。

---

### 附：各文件完整解剖报告

7 份 file:line 级报告存于本会话 wire 日志（AgentSwarm 输出），关键数据已全部并入 §4。如需复查：`agents/main/tool-results/AgentSwarm-AgentSwarm_482_*.txt`（session 727751c2）。

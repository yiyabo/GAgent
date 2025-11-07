# 总体流程概览

  - 会话绑定：前端呼叫 POST /chat/message，若携带 session_id，后端会在 chat_sessions 里确保该会话存在并读取绑定的 plan_id。若没有绑定，就尝试使用请求上下文中的 plan_id，找不到则保持未绑定状态，仅允许 create_plan 或 list_plans。
  - 计划树加载：StructuredChatAgent 通过 PlanSession 读取主库中的 plans 元信息，并打开对应的 `plan_{id}.sqlite` 文件加载任务表，构建 PlanTree（节点包含名称、指令、父子关系、依赖等），并将计划概览（或计划列表）写入系统提示；历史
    消息被裁剪至最近 10 条一同提供。
  - 提示构建：提示里包含当前模式、会话 ID、Plan 概览/目录、动作清单（包括 `web_search` 与 `graph_rag`）、结构化 JSON Schema 与操作指引。LLM 只需返回 {"llm_reply": {"message": ...}, "actions": [...]}。
  - 动作解析与执行：LLM 输出经 Pydantic 校验后，按 order 顺序执行。计划类动作（创建/列出/删除/执行）以及任务类动作（增删改、移动、更新说明、子图请求、重跑等）通过 PlanRepository 直接改写数据库：
      - 每次成功修改都会标记 _dirty。
      - execute_plan 会遍历当前 PlanTree，实时写入节点 `status`（`running` → `completed`/`failed`/`skipped`）与 `execution_result`；`rerun_task` 针对指定节点重置状态并重写结果。
      - request_subgraph 直接基于内存 PlanTree 生成子图摘要与节点详情，避免额外数据库往返。
      - tool_operation（`web_search` / `graph_rag`）通过 ToolBox 执行后，其结果写入消息 `metadata.tool_results`，并同步保存在 `recent_tool_results` 供后续提示引用。
  - 整体持久化：对话结束时（即一轮动作全部执行后），若 _dirty 为真，PlanSession.persist_current_tree 会重建当前 PlanTree 并调用 upsert_plan_tree：
      - 更新主库中的 plans 元信息（标题、描述、metadata、更新时间），
      - 重写对应 `plan_{id}.sqlite` 中的 tasks / task_dependencies 表（保证树结构与内存一致），
      - 可选地在同级 snapshots 表记录本轮快照。
  - 会话命名：PlanSession 完成后，SessionTitleService 会综合 plan/title、最近若干条用户消息生成会话标题；若用户已手动命名，则不会覆盖。前端在接收到助手首轮回复或绑定 Plan 后，会调用 `POST /chat/sessions/{id}/autotitle` 更新列表标题，侧边栏支持手动“重新生成标题”。
  - 响应返回：后端记录用户与助手消息，最终 ChatResponse 包含：
      - response: LLM 给用户的自然语言；
      - actions: 每个动作的执行结果与详情；
      - metadata: intent、success、errors、当前 plan_id、最新计划概要、`tool_results`、`plan_persisted` 标记以及原始动作列表（便于前端渲染执行详情）。
  - 后续可扩展点：plan_snapshots 可用于版本回滚；tool_results 可扩展为链接图谱可视化、持久化搜索卡片等。

  若要在本地验证，激活 conda 环境后运行相应测试或 python -m compileall app，确保主库 plans/会话表已初始化，同时在 `data/plans/` 下生成对应计划文件。

## 初始化示例数据

  - 运行 `python example/generate_demo_plan.py --db-root data/demo_db` 可快速生成包含上下文与执行结果的示例计划。
  - `python example/list_plans.py --db-root data/demo_db` 查看主库计划列表，`python example/show_plan_tree.py --plan <id>` 可打印指定计划的任务树。

## 分解配置

  - 通过 `.env` 或环境变量控制分解行为：
      - `DECOMP_MODEL`：指定使用的 LLM 模型。
      - `DECOMP_MAX_DEPTH` / `DECOMP_MAX_CHILDREN` / `DECOMP_TOTAL_NODE_BUDGET`：限制分解深度、每层子任务数量和整次会话的节点预算。
  - `DECOMP_AUTO_ON_CREATE`：是否在 `create_plan` 后自动分解（默认开启）。
  - 修改 `.env` 后重启服务即可生效，`app/config/decomposer_config.py` 会自动加载这些配置。

## 执行配置

- `app/config/executor_config.py` 负责加载执行器专属配置，可通过 `.env` 覆盖：
  - `PLAN_EXECUTOR_MODEL`：执行任务所用模型（默认沿用主模型）。
  - `PLAN_EXECUTOR_PROVIDER` / `PLAN_EXECUTOR_API_URL` / `PLAN_EXECUTOR_API_KEY`：为执行器绑定独立的 LLM 服务端点。
  - `PLAN_EXECUTOR_MAX_RETRIES`、`PLAN_EXECUTOR_TIMEOUT`、`PLAN_EXECUTOR_USE_CONTEXT` 等用于控制重试、超时、上下文注入。
- 当这些变量缺省时，执行器会退回通用 `LLMService` 配置；如需隔离流量，可在部署环境中单独指定。

## 测试与验证

- 后端：`pytest test/test_chat_routes_integration.py` 覆盖 “创建任务 + 执行计划” 正向场景和异常路径；`test_plan_executor.py` 验证状态写回与重试逻辑。
- 数据层：`pytest test/test_plan_repository_*.py` 覆盖节点 CRUD、依赖、上下文及执行结果字段。
- 前端：`npm run build` / `npm run dev` 验证；计划/执行结果面板通过 `/plans/{plan_id}/tree`、`/plans/{plan_id}/results`、`/plans/{plan_id}/execution/summary` 加载数据，`ToolResultCard` 会展示 `web_search` 与 `graph_rag` 的返回值。
  
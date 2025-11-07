# Chat Prompt Rework: Unbound Session Flow

## 摘要
- 目的：允许聊天会话在未绑定计划前保持纯交流/调研语境，避免 LLM 过早触发计划/任务相关动作。  
- 方法：重构 `StructuredChatAgent._build_prompt`，按会话是否绑定计划拼装差异化提示、动作白名单与行为守则。  
- 结果：用户明确提出“创建计划/选择计划”之前，LLM 仅能使用系统帮助、搜索、知识库等工具；一旦绑定计划则恢复全部操作能力。  
- 额外：保持后端约束（`_require_plan_bound()` 等）与 JSON schema 不变，确保兼容现有执行流水线。

## 背景
- 现状：`_build_prompt` 在未绑定计划时仍强行提醒 “只能使用 create_plan 或 list_plans”，但动作白名单里包含 `execute_plan` / `create_task` 等完整操作。  
  - 实际效果：LLM 往往立刻返回 `create_plan`，缺乏澄清和探索；用户无法长时间停留在“纯对话”状态。  
  - 业务需求：允许用户先对话、调研或让 LLM 使用工具搜集信息；只有在明确需求后再启动计划相关动作。

## 目标
1. Prompt 在未绑定计划时鼓励对话、调研、澄清需求，而不是引导立即创建计划。  
2. 仅当用户明确指示或会话上下文已绑定计划时，才允许 LLM 使用 plan/task 系列动作。  
3. 保留现有 schema 验证方式，减少对执行器流水线的影响。  
4. 维持后端兜底：即使 prompt 失效，`_require_plan_bound()` 等逻辑仍阻止越界操作。

## 修改范围
- 主要代码：`app/routers/chat_routes.py::StructuredChatAgent._build_prompt`。  
- 文档：`docs/plans/chat_prompt_pipeline.md` 需同步说明未绑定计划的提示流。  
- 可选（如需辅助重构）：新增 `_compose_action_catalog`、`_compose_guidelines` 等私有帮助方法。

## 解决方案概览

### 1. Prompt 分层构建

拆分 `_build_prompt` 中的提示文本，按“公共部分 + 场景差异”组合：

| 模块 | 公共内容 | 未绑定会话追加 | 已绑定会话追加 |
| --- | --- | --- | --- |
| 头部元信息 | 模式、会话 ID、额外上下文、最近历史 | 无 | 无 |
| Plan 视图 | `plan_outline`，未绑定时替换为占位文本 | `(未绑定计划)`，鼓励继续理解需求；附上可选计划（≤10 条） | 保持现有 outline；可追加最近执行摘要 |
| Schema 宣告 | `self.schema_json` | 公共部分 | 公共部分 |
| Action Catalog | `system_operation: help`、`tool_operation: web_search`、`tool_operation: graph_rag` | 仅列出 `plan_operation: create_plan` 与 `plan_operation: list_plans`，并声明“须经用户明确要求后再调用” | 列出全部 plan/task/context 动作（沿用现状） |
| 指南 | JSON 输出要求 | - “未绑定计划时不要创建/修改任务；若用户仅在探索，请继续提问或总结。”<br>- “只有当用户明确表达想要创建/选择计划时，才调用 plan 相关动作。” | - “执行计划/任务前需确认依赖已满足”等现有守则 |

### 2. 实现细节

1. **动作白名单拆分**  
   ```python
   base_actions = [
       "- system_operation: help",
       "- tool_operation: web_search (用于实时检索)",
       "- tool_operation: graph_rag (用于知识库问答)",
   ]
   plan_creation_actions = [
       "- plan_operation: create_plan  # 仅在用户明确请求建立新计划时调用",
       "- plan_operation: list_plans  # 可列举候选计划，但禁止直接执行/修改",
   ]
   plan_bound_actions = [
       "- plan_operation: create_plan, list_plans, execute_plan, delete_plan",
       "- task_operation: create_task, update_task, complete_task, delete_task",
       "- context_operation: add_context, list_context",
       "...",
   ]
   ```
   根据 `session.plan_id` 和 `requested_plan` 拼接 `"\n".join([...])`。

2. **指南拆分与合并**  
   - `common_guidelines`：JSON schema、`llm_reply` 需自然语言、解释理由等通用规则。  
   - `unbound_guidelines`：强调“只在明确请求时使用 plan 操作”、“可多问问题或总结”、“不要擅自创建或修改任务”。  
   - `bound_guidelines`：复用现有计划执行守则、依赖检查提醒等。  
   - `final_guidelines = "\n".join([...])`，顺序为公共 → 场景特化。

3. **提示文案与上下文**  
   - `plan_status`：  
     - 未绑定：`"当前会话未绑定计划。请继续理解需求、提供建议或使用工具。只有用户明确要求创建计划或接管现有计划时才发起动作。"`  
     - 已绑定：沿用原文案，保留 Plan 标识与当前阶段。  
   - `plan_catalog`：未绑定时附上最近/热门计划（≤10 条）以及 `“若用户想接管现有计划，可建议其指定编号。”`；已绑定时可省略或缩短说明。  
   - `plan_outline`：未绑定使用 `(未绑定计划)` 占位文本，避免误导。

4. **结构化输出保持不变**  
   - 继续使用 `self.schema_json` 提示 JSON 结构。  
   - 无需调整返回模型 `LLMStructuredResponse`，避免破坏解析逻辑。

### 3. 详细实现步骤

1. **重构 `_build_prompt`**
   - 拆分现有大段字符串为多个列表片段（元信息、状态、动作、指南）。  
   - 引入私有辅助方法（如 `_compose_action_catalog`, `_compose_guidelines`, `_format_plan_outline`）提高可读性。  
   - 在未绑定分支调用 `self.plan_repository.list_available_plans(limit=10)`（或等价函数），组装 `plan_catalog` 片段。  
   - 确保最终拼接使用 `"\n".join(blocks)`，方便单元测试断言。

2. **调整调用上下文**
   - 保留 `session.plan_id` 作为主判定条件；若请求 payload 中已有 `target_plan_id` 也视为“已锁定计划”。  
   - 容错：若计划信息加载失败，未绑定分支仍返回“未绑定”提示，避免阻塞对话。

3. **更新文档与注释**
   - 在 `_build_prompt` 顶部增加注释说明双分支设计目的。  
   - 更新 `docs/plans/chat_prompt_pipeline.md`，描绘“纯聊天 → 绑定计划 → 执行计划”的流程与状态转换。

4. **测试**
   - 新增单元测试（例如 `tests/unit/test_structured_chat_agent_prompt.py`）覆盖未绑定/已绑定两种情形。  
     - 断言未绑定输出中不存在 `execute_plan`、`create_task` 等关键字。  
     - 断言未绑定输出包含 “未绑定计划” 文案与 `plan_operation: create_plan` 的限制说明。  
     - 断言绑定输出保持原有动作目录，并包含 plan outline。  
   - 端到端冒烟：  
     - 启动后端，创建无计划聊天，连续发送多轮消息，确认不会自动触发 `create_plan`。  
     - 在聊天中明确指示“请帮我创建计划”，验证 LLM 返回 `create_plan` 动作。  
     - 对已有计划会话发起执行请求，确认提示未被削弱。

5. **回归检查**
   - 重点关注 `_require_plan_bound()`、`_handle_plan_operation()` 等路径，确保即使 prompt 分支错误仍由后端阻挡。  
   - 观察日志：确认 prompt 重构后未增加显著延时或异常。

### 4. 风险与兼容性
- Prompt 长度增加有限，不影响现有模型配额。  
- 若 LLM 依旧越权，后端 `_require_plan_bound` 会报错，保证安全。  
- 前端无需变动：schema 未调整，JSON 结构一致。  
- 需注意与未来“多计划并行”功能兼容（通过传入 `requested_plan_id` 可扩展更多分支）。

### 5. 验收标准
1. 单元测试覆盖新旧提示差异并通过。  
2. 手动冒烟确认未绑定会话不会自动建计划，绑定会话仍可执行全部动作。  
3. 相关文档（`chat_prompt_pipeline.md`）同步更新，开发同事阅读即可理解新流程。  
4. 日志中无新增告警；若出现错误响应，应能追踪到后端兜底逻辑。

### 6. 推广计划
1. 在开发环境实现并通过测试。  
2. 提交 PR，邀请聊天/计划领域维护者评审（重点关注提示文案与动作白名单）。  
3. 合并后部署测试环境，邀请产品/运营体验纯对话流程。  
4. 观察 1~2 日用户反馈，如无异常再部署生产。  
5. 后续评估是否需要更细粒度的 prompt 参数（例如“探索模式”“执行模式”开关）。

## 下一步
1. 按上述步骤调整 `_build_prompt` 代码并提交单元测试。  
2. 更新 `chat_prompt_pipeline.md` 描述新的未绑定会话流。  
3. 回归测试创建/执行计划路径，确保未受影响。  
4. 发布后收集用户体验反馈，必要时微调提示文案或动作策略。  
5. 计划性复盘：两周后评估 LLM 在未绑定会话中的行为是否稳定、是否仍需额外约束。

# 项目大文件重构计划

## 项目概况

- **项目路径**: `/Users/apple/LLM/agent/.claude/worktrees/flamboyant-chandrasekhar/`
- **项目类型**: AI 驱动的任务编排系统 — Python FastAPI 后端 + React/TypeScript 前端
- **总代码量**: ~97,000 行
- **目标**: 将超大文件拆分为职责单一的模块，每个文件 300-800 行

## 当前代码状态

### 测试基线

```
57 passed, 6 failed (6 个失败为预先存在的，与重构无关)
```

运行命令：`python -m pytest app/tests/ -q --tb=no`

### 已完成的工作

已创建 `app/routers/chat/` 子包，从 `chat_routes.py`（原 8,676 行）中抽取出 6 个模块：

| 文件 | 行数 | 内容 |
|------|------|------|
| `chat/models.py` | 354 | Pydantic 数据模型（ChatMessage, ChatRequest 等 16 个类） |
| `chat/session_helpers.py` | 942 | 会话辅助函数（34 个函数：DB 查询、PhageScope、序列化等） |
| `chat/tool_results.py` | 575 | 工具结果处理（sanitize、summarize、truncate、drop_callables 等 6 个函数） |
| `chat/background.py` | 63 | 后台任务分类常量与函数 |
| `chat/confirmation.py` | 71 | 确认机制（confirmation ID 生成与管理） |
| `chat/__init__.py` | 95 | 包初始化，re-export 所有公共符号 |

**向后兼容机制**：`chat_routes.py` 头部通过 `from .chat.xxx import ...` 重新导入所有符号，确保外部代码 `from app.routers.chat_routes import ChatMessage` 仍然有效。

### 当前 chat_routes.py 结构（6,937 行）

```
行 1-190:     import 与 re-export（~190 行）
行 191-601:   路由端点 handlers — session CRUD、confirm（~410 行）
行 602-737:   内部辅助函数 _execute_confirmed_actions 等（~135 行）
行 738-1130:  chat_message 路由 handler（~392 行）
行 1131-1607: chat_stream 路由 handler（~477 行）
行 1608-2538: 分析生成函数 _generate_tool_analysis 等 + _execute_action_run（~930 行）
行 2539-2851: action status 路由 + _build_action_status_payloads（~312 行）
行 2852-6937: StructuredChatAgent 类（~4,085 行，53 个方法）
```

### 待重构的前端大文件

| 文件 | 行数 |
|------|------|
| `web-ui/src/components/chat/ChatMessage.tsx` | 1,103 |
| `web-ui/src/components/dag/DAG3DView.tsx` | 1,067 |
| `web-ui/src/components/chat/JobLogPanel.tsx` | 1,043 |
| `web-ui/src/store/slices/createMessageSlice.ts` | 884 |
| `web-ui/src/components/tasks/TaskDetailDrawer.tsx` | 828 |
| `web-ui/src/types/index.ts` | 783 |

### 待重构的工具箱文件

| 文件 | 行数 |
|------|------|
| `tool_box/integration.py` | 422（大量重复 register_tool 调用） |

---

## 重构原则

1. **向后兼容**：所有拆分都通过 `__init__.py` re-export 保持外部 import 不变
2. **逐步验证**：每个步骤完成后运行 `python -m pytest app/tests/ -q --tb=short`，确保 57 pass / 6 fail 不变
3. **懒导入避免循环依赖**：子模块中使用 `from ...database import get_db` 等按需导入
4. **方法抽取模式**：从类中抽取 `@staticmethod` 和不依赖 `self` 的方法为独立函数，类中保留 `attr = staticmethod(fn)` 委托
5. **SOLID 原则**：单一职责，每个模块只做一件事

---

## 阶段 2：StructuredChatAgent 守护栏与判断方法抽取

**目标**：将 StructuredChatAgent 中的守护栏（guardrail）、静态判断方法抽取到独立模块。

### 步骤 2.1：创建 `app/routers/chat/guardrails.py`

抽取以下方法为独立函数：

```python
# 以下方法都是 @staticmethod 或不依赖 self 的纯判断方法
# 从 chat_routes.py StructuredChatAgent 类中抽取

_extract_task_id_from_text      # line 3205, @staticmethod
_is_status_query_only           # line 3224, @staticmethod
_reply_promises_execution       # line 3258, @staticmethod
_looks_like_completion_claim    # line 3349, @staticmethod
_extract_declared_absolute_paths # line 3367, @staticmethod
_is_task_executable_status      # line 3403, @staticmethod
_is_generic_plan_confirmation   # line 3473, @staticmethod
_explicit_manuscript_request    # line 2990, @staticmethod
```

**操作步骤**：
1. 读取 chat_routes.py 中上述每个方法的完整实现（包括方法体）
2. 在 `app/routers/chat/guardrails.py` 中以独立函数形式写入（去掉 `self` 参数）
3. 在 chat_routes.py 中删除原始方法定义，替换为：
   ```python
   _extract_task_id_from_text = staticmethod(extract_task_id_from_text)
   ```
4. 更新 chat_routes.py 头部 import 添加新模块
5. 更新 `chat/__init__.py` 添加 re-export
6. 运行测试验证

### 步骤 2.2：创建 `app/routers/chat/guardrail_handlers.py`

抽取需要 `self` 的守护栏方法。这些方法使用了 `self.plan_session`、`self.extra_context` 等实例属性，抽取为独立函数时需要将 `self` 替换为显式参数：

```python
# 以下方法需要显式传入 agent 实例属性作为参数
_apply_phagescope_fallback                    # line 3007
_apply_task_execution_followthrough_guardrail  # line 3289
_resolve_followthrough_target_task_id          # line 3303
_apply_completion_claim_guardrail              # line 3392
_first_executable_atomic_descendant            # line 3407
_match_atomic_task_by_keywords                 # line 3426
_infer_plan_seed_message                       # line 3503
_apply_plan_first_guardrail                    # line 3522
_should_force_plan_first                       # line 3533
```

**注意**：这些方法由 `get_structured_response` 和 `execute_structured` 调用。保留在类上的薄委托方法要正确传递 `self` 的属性。推荐模式：

```python
# guardrail_handlers.py 中
def apply_completion_claim_guardrail(structured, plan_session, extra_context, ...):
    ...

# chat_routes.py 类中保留
def _apply_completion_claim_guardrail(self, structured):
    return apply_completion_claim_guardrail(
        structured, self.plan_session, self.extra_context, ...
    )
```

**或者更简洁的做法**：传入整个 agent 实例，让函数内部访问属性：

```python
# guardrail_handlers.py
def apply_completion_claim_guardrail(agent, structured):
    # 内部用 agent.plan_session 等
    ...

# chat_routes.py 类中
def _apply_completion_claim_guardrail(self, structured):
    return apply_completion_claim_guardrail(self, structured)
```

选择后一种模式，因为这些守护栏方法访问的 agent 属性太多，逐个传参不现实。

**操作步骤**：
1. 读取上述每个方法的完整实现
2. 创建 `guardrail_handlers.py`，将每个方法转为接受 `agent` 参数的独立函数
3. 在类中保留一行委托：`def _xxx(self, ...): return xxx(self, ...)`
4. 更新 import 和 re-export
5. 运行测试验证

---

## 阶段 3：StructuredChatAgent 动作处理器抽取

**目标**：将各类 `_handle_*_action` 方法抽取到独立模块。

### 步骤 3.1：创建 `app/routers/chat/action_handlers.py`

抽取以下处理器方法：

```python
_handle_tool_action       # line 4953, async — 最大的处理器（~1200 行）
_handle_plan_action       # line 6150, async — 计划操作处理（~220 行）
_handle_task_action       # line 6372, async — 任务操作处理（~380 行）
_handle_context_request   # line 6751, async — 上下文请求处理（~30 行）
_handle_system_action     # line 6781, async — 系统动作处理（~10 行）
_handle_unknown_action    # line 6790, async — 未知动作处理（~5 行）
```

**模式与阶段 2.2 相同**：传入 `agent` 实例。

```python
# action_handlers.py
async def handle_tool_action(agent, action: LLMAction) -> AgentStep:
    ...

# chat_routes.py 类中
async def _handle_tool_action(self, action):
    return await handle_tool_action(self, action)
```

### 步骤 3.2：创建 `app/routers/chat/plan_helpers.py`

抽取计划管理辅助方法：

```python
_require_plan_bound       # line 6813
_refresh_plan_tree        # line 6821
_auto_decompose_plan      # line 6843（async）
_persist_if_dirty         # line 6920
_coerce_int               # line 6835, @staticmethod
_build_suggestions        # line 6794
```

### 步骤 3.3：创建 `app/routers/chat/prompt_builder.py`

抽取提示词构建方法：

```python
_build_prompt             # line 4623
_format_memories          # line 4669
_compose_plan_status      # line 4685
_compose_plan_catalog     # line 4696
_compose_action_catalog   # line 4706
_compose_guidelines       # line 4720
_get_structured_agent_prompts  # line 4731, @staticmethod
_format_history           # line 4836
_strip_code_fence         # line 4846, @staticmethod
```

**操作步骤**：
1. 逐一读取并抽取，创建 3 个新文件
2. 类中保留薄委托
3. 更新 import 和 `__init__.py`
4. 运行测试验证

---

## 阶段 4：路由层整理

**目标**：将 `chat_routes.py` 中的路由端点 handler 和模块级辅助函数移到专用文件。

### 步骤 4.1：创建 `app/routers/chat/routes.py`

移动所有 `@router.*` 路由 handler（不含 stream）：

```python
# 以下都是 @router.xxx 装饰的 async 函数
list_chat_sessions            # line 192, GET /sessions
update_chat_session           # line 264, PATCH /sessions/{id}
autotitle_chat_session        # line 388, POST
bulk_autotitle_chat_sessions  # line 419, POST
head_chat_session             # line 452, HEAD
delete_chat_session           # line 472, DELETE /sessions/{id}
confirm_pending_action        # line 522, POST /confirm
get_pending_confirmation_status # line 584, GET /confirm/{id}
_execute_confirmed_actions    # line 602（内部辅助，随 confirm 一起移动）
chat_status                   # line 642, GET /status
get_chat_history              # line 706, GET /history/{id}
chat_message                  # line 738, POST /message
```

**注意**：`router = APIRouter(prefix="/chat", tags=["Chat"])` 需在 routes.py 中定义或从 chat_routes.py 传入。

### 步骤 4.2：创建 `app/routers/chat/stream.py`

移动 SSE 流式端点：

```python
chat_stream                   # line 1131, POST /stream （~477 行）
```

### 步骤 4.3：创建 `app/routers/chat/action_execution.py`

移动动作执行与分析函数：

```python
_generate_tool_analysis       # line 1608
_generate_tool_summary        # line 1713
_collect_created_tasks_from_steps  # line 1747
_generate_action_analysis     # line 1762
_build_brief_action_summary   # line 1837
_execute_action_run           # line 1867
get_action_status             # line 2540, GET /actions/{id}
retry_action_run              # line 2638, POST /actions/{id}/retry
_build_action_status_payloads # line 2754
```

### 步骤 4.4：创建 `app/routers/chat/claude_code_helpers.py`

抽取 Claude Code 相关方法：

```python
_resolve_claude_code_task_context      # line 3623
_normalize_csv_arg                     # line 3664, @staticmethod
_summarize_amem_experiences_for_cc     # line 3701, @staticmethod
_compose_claude_code_atomic_task_prompt # line 3747, @staticmethod
_resolve_previous_path                 # line 3799
_resolve_placeholders_in_value         # line 3827
_resolve_action_placeholders           # line 3851
```

### 步骤 4.5：更新路由注册

修改 `app/routers/__init__.py` 中的 auto-load 逻辑：
- 原来加载 `app.routers.chat_routes`
- 改为加载 `app.routers.chat.routes`（或保留 chat_routes.py 作为空壳只做 router 注册）

**推荐**：保留 `chat_routes.py` 作为极简入口（< 50 行），只做：
```python
from .chat.routes import router  # noqa: F401
```

### 阶段 4 完成后的文件结构

```
app/routers/chat/
├── __init__.py              # re-export 所有公共符号
├── models.py                # Pydantic 模型 (~354 行)
├── session_helpers.py       # 会话辅助函数 (~942 行)
├── tool_results.py          # 工具结果处理 (~575 行)
├── background.py            # 后台任务分类 (~63 行)
├── confirmation.py          # 确认机制 (~71 行)
├── guardrails.py            # 静态守护栏判断 (~300 行) [新]
├── guardrail_handlers.py    # 实例守护栏方法 (~500 行) [新]
├── action_handlers.py       # 动作处理器 (~1,800 行) [新]
├── plan_helpers.py          # 计划管理辅助 (~200 行) [新]
├── prompt_builder.py        # 提示词构建 (~300 行) [新]
├── claude_code_helpers.py   # Claude Code 辅助 (~300 行) [新]
├── routes.py                # 路由端点 handlers (~600 行) [新]
├── stream.py                # SSE 流式端点 (~500 行) [新]
└── action_execution.py      # 动作执行与分析 (~1,000 行) [新]

app/routers/chat_routes.py   # 极简入口 + StructuredChatAgent 核心 (~800 行)
```

**chat_routes.py 最终应仅包含**：
- StructuredChatAgent 类定义（`__init__`、`handle`、`get_structured_response`、`execute_structured`、`_invoke_llm`、`_execute_action` 等核心编排方法）
- 类上的薄委托属性/方法
- 预估 ~800-1200 行

---

## 阶段 5：tool_box/integration.py 重构

**目标**：将重复的 `register_tool()` 调用改为声明式配置。

**当前状态**：`tool_box/integration.py`（422 行），包含大量类似的：
```python
register_tool("tool_name", handler_fn, description="...", parameters={...})
```

### 步骤 5.1：创建声明式工具注册表

创建 `tool_box/tool_registry.py`：

```python
# tool_registry.py
TOOL_DEFINITIONS = [
    {
        "name": "web_search",
        "handler": "tool_box.tools.web_search:handle",
        "description": "Search the web...",
        "parameters": {...}
    },
    # ... 所有工具声明
]

def register_all_tools():
    for defn in TOOL_DEFINITIONS:
        handler = _resolve_handler(defn["handler"])
        register_tool(defn["name"], handler, ...)
```

### 步骤 5.2：精简 integration.py

将 `integration.py` 简化为：
```python
from .tool_registry import register_all_tools
register_all_tools()
```

**验证**：运行 `python -c "from tool_box import execute_tool; print('OK')"`

---

## 阶段 6：前端 types/index.ts 拆分

**目标**：将 `web-ui/src/types/index.ts`（783 行）拆分为按业务域组织的类型文件。

### 步骤 6.1：分析类型定义

读取 `types/index.ts`，将类型按业务域分组：
- `types/chat.ts` — 聊天消息、会话相关类型
- `types/task.ts` — 任务、计划相关类型
- `types/tool.ts` — 工具调用、执行结果相关类型
- `types/dag.ts` — DAG 可视化相关类型
- `types/settings.ts` — 配置、设置相关类型
- `types/common.ts` — 通用类型（分页、响应包装等）

### 步骤 6.2：创建类型文件并迁移

每个文件 100-200 行。

### 步骤 6.3：更新 index.ts 为 re-export hub

```typescript
export * from './chat';
export * from './task';
export * from './tool';
// ...
```

**验证**：运行 `cd web-ui && npx tsc --noEmit`（如果 tsc 可用）或检查 import 引用。

---

## 阶段 7：ChatMessage.tsx 拆分

**目标**：将 `web-ui/src/components/chat/ChatMessage.tsx`（1,103 行）拆分为 ~10 个子组件。

### 步骤 7.1：分析组件结构

读取文件，识别：
- 主组件与子渲染函数
- 条件渲染的内容区块
- 可复用的 UI 片段

### 步骤 7.2：创建子组件目录

```
web-ui/src/components/chat/message/
├── index.tsx                # 主 ChatMessage 组件（骨架，< 200 行）
├── MessageBubble.tsx        # 消息气泡容器
├── MessageContent.tsx       # 纯文本/Markdown 渲染
├── ToolCallCard.tsx         # 工具调用展示卡片
├── ActionStepList.tsx       # 动作步骤列表
├── CodeBlock.tsx            # 代码块渲染
├── ImagePreview.tsx         # 图片预览
├── FileAttachment.tsx       # 文件附件展示
├── ThinkingIndicator.tsx    # 思考中指示器
├── MessageActions.tsx       # 消息操作栏（复制、重试等）
└── hooks/
    └── useMessageState.ts   # 消息状态管理 hook
```

### 步骤 7.3：更新导入

原来的 `import { ChatMessage } from '../ChatMessage'` 改为从 `./message` 导入，或在原位置保留 re-export：
```typescript
// ChatMessage.tsx（保留为兼容入口）
export { default as ChatMessage } from './message';
```

**验证**：`cd web-ui && npm run build`（或 `npx tsc --noEmit`）

---

## 阶段 8：DAG3DView.tsx 拆分

**目标**：将 `web-ui/src/components/dag/DAG3DView.tsx`（1,067 行）拆分。

### 分析要点

- 3D 渲染逻辑（Three.js / React Three Fiber）
- 节点/边数据处理
- 交互事件处理
- 布局算法

### 建议拆分

```
web-ui/src/components/dag/
├── DAG3DView.tsx            # 主组件（< 300 行）
├── DAGNode.tsx              # 节点组件
├── DAGEdge.tsx              # 边组件
├── DAGControls.tsx          # 交互控制面板
├── hooks/
│   ├── useDAGLayout.ts      # 布局计算 hook
│   └── useDAGInteraction.ts # 交互逻辑 hook
└── utils/
    └── dagHelpers.ts        # 数据转换工具函数
```

---

## 阶段 9：JobLogPanel.tsx 拆分

**目标**：将 `web-ui/src/components/chat/JobLogPanel.tsx`（1,043 行）拆分。

### 建议拆分

```
web-ui/src/components/chat/job-log/
├── index.tsx                # 主面板组件（< 300 行）
├── LogEntry.tsx             # 单条日志组件
├── LogFilter.tsx            # 日志过滤器
├── LogTimeline.tsx          # 时间线视图
├── LogDetail.tsx            # 日志详情展开
└── hooks/
    └── useLogStream.ts      # 日志流处理 hook
```

---

## 阶段 10：createMessageSlice.ts 拆分

**目标**：将 `web-ui/src/store/slices/createMessageSlice.ts`（884 行）拆分。

### 建议拆分

```
web-ui/src/store/slices/message/
├── index.ts                 # 主 slice 定义（< 200 行）
├── actions.ts               # action creators（异步操作）
├── selectors.ts             # 选择器
├── helpers.ts               # 数据转换辅助函数
└── types.ts                 # slice 专用类型
```

---

## 阶段 11：TaskDetailDrawer.tsx 拆分

**目标**：将 `web-ui/src/components/tasks/TaskDetailDrawer.tsx`（828 行）拆分。

### 建议拆分

```
web-ui/src/components/tasks/detail/
├── index.tsx                # 主 Drawer 组件（< 200 行）
├── TaskHeader.tsx           # 任务头部信息
├── TaskProgress.tsx         # 进度展示
├── TaskActions.tsx          # 操作按钮组
├── TaskLogs.tsx             # 任务日志区
└── SubtaskList.tsx          # 子任务列表
```

---

## 执行注意事项

### 通用验证流程

每完成一个步骤后：

**后端**：
```bash
python -m pytest app/tests/ -q --tb=short
# 期望：57 passed, 6 failed（不能新增失败）
```

**前端**：
```bash
cd web-ui && npx tsc --noEmit
# 或 npm run build
```

### 关键约束

1. **不要修改任何函数/方法的逻辑**，只做代码搬移
2. **保留所有注释**
3. **import 路径注意层级**：`chat/` 子模块中引用数据库用 `from ...database import get_db`（三个点，因为多了一层目录）
4. **re-export 确保完整**：每个被抽取的符号都必须在 `__init__.py` 中 re-export
5. **类方法委托模式**：对于使用 `self` 的方法，类中保留一行委托调用，不要删除类上的方法签名
6. **chat_routes.py 的 router 对象**不能移动，因为 `app/routers/__init__.py` 通过 `importlib.import_module("app.routers.chat_routes")` 自动发现并注册
7. **执行顺序**：严格按阶段 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 的顺序执行

### StructuredChatAgent 方法抽取参考

完整的方法清单与行号（基于 6,937 行版本的 chat_routes.py）：

```
行号    方法名                                        类型          目标模块
------  --------------------------------------------  -----------   -------------------
2858    __init__                                      实例方法      保留在类中
2947    handle                                        async         保留在类中
2956    get_structured_response                       async         保留在类中
2965    _apply_experiment_fallback                    async         guardrail_handlers
2990    _explicit_manuscript_request                  @staticmethod guardrails
3007    _apply_phagescope_fallback                    实例方法      guardrail_handlers
3205    _extract_task_id_from_text                    @staticmethod guardrails
3224    _is_status_query_only                         @staticmethod guardrails
3258    _reply_promises_execution                     @staticmethod guardrails
3289    _apply_task_execution_followthrough_guardrail  实例方法      guardrail_handlers
3303    _resolve_followthrough_target_task_id          实例方法      guardrail_handlers
3349    _looks_like_completion_claim                  @staticmethod guardrails
3367    _extract_declared_absolute_paths              @staticmethod guardrails
3392    _apply_completion_claim_guardrail              实例方法      guardrail_handlers
3403    _is_task_executable_status                    @staticmethod guardrails
3407    _first_executable_atomic_descendant            实例方法      guardrail_handlers
3426    _match_atomic_task_by_keywords                 实例方法      guardrail_handlers
3473    _is_generic_plan_confirmation                 @staticmethod guardrails
3503    _infer_plan_seed_message                       实例方法      guardrail_handlers
3522    _apply_plan_first_guardrail                    实例方法      guardrail_handlers
3533    _should_force_plan_first                       实例方法      guardrail_handlers
3623    _resolve_claude_code_task_context              实例方法      claude_code_helpers
3664    _normalize_csv_arg                            @staticmethod claude_code_helpers
3701    _summarize_amem_experiences_for_cc            @staticmethod claude_code_helpers
3747    _compose_claude_code_atomic_task_prompt       @staticmethod claude_code_helpers
3799    _resolve_previous_path                         实例方法      claude_code_helpers
3827    _resolve_placeholders_in_value                 实例方法      claude_code_helpers
3851    _resolve_action_placeholders                   实例方法      claude_code_helpers
3864    execute_structured                            async         保留在类中
4075    _maybe_synthesize_phagescope_saveall_analysis  async         action_handlers
4244    _should_use_deep_think                         实例方法      prompt_builder
4277    process_deep_think_stream                     async         保留在类中
4613    _invoke_llm                                   async         保留在类中
4623    _build_prompt                                  实例方法      prompt_builder
4669    _format_memories                               实例方法      prompt_builder
4685    _compose_plan_status                           实例方法      prompt_builder
4696    _compose_plan_catalog                          实例方法      prompt_builder
4706    _compose_action_catalog                        实例方法      prompt_builder
4720    _compose_guidelines                            实例方法      prompt_builder
4731    _get_structured_agent_prompts                 @staticmethod prompt_builder
4738    _extract_tool_name                            @staticmethod 保留在类中（小工具方法）
4744    _resolve_job_meta                              实例方法      action_execution
4776    _log_action_event                             @staticmethod action_execution
4811    _truncate_summary_text                        @staticmethod action_execution
4816    _build_actions_summary                         实例方法      action_execution
4829    _append_summary_to_reply                       实例方法      action_execution
4836    _format_history                                实例方法      prompt_builder
4846    _strip_code_fence                             @staticmethod prompt_builder
4865    _execute_action                               async         保留在类中
4953    _handle_tool_action                           async         action_handlers
6150    _handle_plan_action                           async         action_handlers
6372    _handle_task_action                           async         action_handlers
6751    _handle_context_request                       async         action_handlers
6781    _handle_system_action                         async         action_handlers
6790    _handle_unknown_action                        async         action_handlers
6794    _build_suggestions                             实例方法      plan_helpers
6813    _require_plan_bound                            实例方法      plan_helpers
6821    _refresh_plan_tree                             实例方法      plan_helpers
6835    _coerce_int                                   @staticmethod plan_helpers
6843    _auto_decompose_plan                          async         plan_helpers
6920    _persist_if_dirty                              实例方法      plan_helpers
6929-37 staticmethod 委托行（已完成的 tool_results）   委托         已完成
```

### 保留在 StructuredChatAgent 类中的核心方法

以下方法构成 Agent 的编排骨架，不应抽出：

```python
__init__                    # 构造函数
handle                      # 主入口
get_structured_response     # 获取 LLM 结构化响应（含守护栏调用链）
execute_structured          # 执行结构化响应（含动作调度）
process_deep_think_stream   # 深度思考流处理
_invoke_llm                 # LLM 调用
_execute_action             # 动作路由分发
_extract_tool_name          # 小工具（1行）
```

抽取完成后，StructuredChatAgent 类应减至 ~800-1000 行。

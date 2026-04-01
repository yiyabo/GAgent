# GAgent 架构演进路线图

> 基于 Claude Code 源码分析，结合 GAgent 现状，制定渐进式架构优化计划。
> 原则：**每一步都能独立上线、立即受益**，不搞大爆炸重构。

---

## 现状对比总览

| 维度 | Claude Code | GAgent 现状 | 差距 |
|------|------------|-------------|------|
| 工具框架 | 强类型接口，16+ 元数据字段，Zod schema | 字典注册，6 个字段，raw dict schema | 大 |
| 并发执行 | 工具声明 `isConcurrencySafe()`，安全工具并行 | 串行执行，无并发信号 | 大 |
| 流式架构 | AsyncGenerator yield，工具边到边执行 | SSE 回调，等全部工具完成才返回 | 大 |
| 权限系统 | 3 层规则 + 通配符 + ML 分类器 + UI 对话框 | 无 | 大 |
| 上下文管理 | 主动压缩 + 多策略 + memory 文件持久化 | 字符截断，无摘要，无持久化 | 大 |
| 工具进度 | `onProgress` 回调，实时推送 | 无中间进度 | 中 |
| 工具校验 | `validateInput()` + `checkPermissions()` 钩子 | 无 | 中 |
| 工具搜索 | `searchHint` + 懒加载 + ToolSearchTool | 全量加载，子串匹配 | 小 |

---

## Phase 1：工具框架升级（最高优先级）

### 为什么先做这个

工具框架是所有后续优化的基础。并发执行需要 `is_concurrent_safe` 元数据；权限系统需要 `check_permissions` 钩子；进度推送需要 `on_progress` 回调。不升级工具框架，后面的事全做不了。

### 1.1 从字典注册升级为 dataclass 接口

**现状** (`tool_box/tool_registry.py`)：
```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    category: str
    parameters_schema: Dict[str, Any]  # raw dict
    handler: Callable
    tags: List[str]
```

**目标**：
```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    category: str
    parameters_schema: Dict[str, Any]
    handler: Callable
    tags: List[str] = field(default_factory=list)

    # --- 新增元数据 ---
    is_read_only: bool = False          # 只读工具（grep, glob, web_search）
    is_concurrent_safe: bool = False    # 可与其他工具并发执行
    is_destructive: bool = False        # 有破坏性操作（删除文件、SSH 命令等）
    search_hint: str = ""               # 语义搜索关键词

    # --- 新增钩子（可选） ---
    validate_input: Optional[Callable] = None    # async (params, context) -> ValidationResult
    check_permissions: Optional[Callable] = None # async (params, context) -> PermissionDecision
    on_progress: Optional[Callable] = None       # async (progress_data) -> None
```

**关键设计决策**：
- 保持向后兼容 — 新字段全有默认值，现有工具不需要改
- 不引入 Pydantic model 做 schema（工具参数已经用 JSON Schema 给 LLM，Pydantic 会引入第二套 schema 机制）
- 钩子用 `Optional[Callable]` 而非抽象方法 — 大多数工具不需要权限检查

**改动范围**：
- `tool_box/tool_registry.py` — 扩展 `ToolDefinition`
- `tool_box/tools_impl/*.py` — 逐个工具补充元数据（可分批）
- `tool_box/tools.py` — 注册逻辑兼容新字段

**验收标准**：
- [ ] 所有现有工具正常注册和执行（向后兼容）
- [ ] `web_search`, `grep_search`, `read_file` 标记 `is_read_only=True, is_concurrent_safe=True`
- [ ] `terminal_session`, `code_executor` 标记 `is_destructive=True`
- [ ] `pytest app/tests/ -v` 全部通过

---

### 1.2 为工具执行注入 ToolContext

**现状**：工具 handler 只收到 `params: Dict`，无法感知调用环境。

**Claude Code 做法** (`ToolUseContext`):
```typescript
type ToolUseContext = {
  options: { tools, mcpClients, commands, ... }
  readFileState: FileStateCache
  messages: Message[]
  toolDecisions: Map<string, ToolDecision[]>
  abortController: AbortController
  // ... 20+ 字段
}
```

**我们的目标**（轻量版）：
```python
@dataclass
class ToolContext:
    session_id: str
    work_dir: str                         # 当前工作目录
    data_dir: str                         # 数据文件目录
    tool_history: List[Dict[str, Any]]    # 本轮已调用工具及结果摘要
    abort_signal: Optional[asyncio.Event] # 取消信号
    metadata: Dict[str, Any]              # 扩展元数据
```

**改动方式**：
- `tool_executor.py` 构建 `ToolContext`，传入 handler
- handler 签名：`async def handler(params: Dict, context: ToolContext) -> Dict`
- 兼容旧签名：executor 检测 handler 是否接受 `context` 参数，不接受就只传 `params`

**改动范围**：
- `app/services/execution/tool_executor.py` — 构建 context，注入 handler
- 逐个工具迁移到新签名（可分批）

---

## Phase 2：并发工具执行

### 为什么第二做

这是用户体感提升最大的改动。当前 agent 调用 3 个只读工具（web_search + grep + read_file）需要串行等待，改为并发后体感提速 2-3x。

### 2.1 实现 AsyncToolExecutor

**Claude Code 参考** (`StreamingToolExecutor.ts`)：
- 维护 `TrackedTool[]` 状态机：queued → executing → completed → yielded
- `isConcurrencySafe` 的工具并行，非安全工具独占执行
- 结果按到达顺序排列（不是完成顺序）

**我们的实现**：
```python
class AsyncToolExecutor:
    """并发安全的工具执行器。"""

    def __init__(self, tool_registry, context: ToolContext):
        self._registry = tool_registry
        self._context = context
        self._pending: List[TrackedTool] = []

    async def submit(self, tool_name: str, params: Dict) -> str:
        """提交工具调用，返回 tracking_id。"""
        tool_def = self._registry.get_tool(tool_name)
        tracked = TrackedTool(
            id=uuid4().hex,
            tool_def=tool_def,
            params=params,
            is_concurrent_safe=tool_def.is_concurrent_safe,
            status="queued",
        )
        self._pending.append(tracked)
        return tracked.id

    async def execute_batch(self) -> List[ToolResult]:
        """执行所有 pending 工具，安全工具并发，非安全工具串行。"""
        safe = [t for t in self._pending if t.is_concurrent_safe]
        unsafe = [t for t in self._pending if not t.is_concurrent_safe]

        results = []
        if safe:
            # 并发执行所有安全工具
            tasks = [self._run_one(t) for t in safe]
            results.extend(await asyncio.gather(*tasks, return_exceptions=True))
        for t in unsafe:
            # 串行执行非安全工具
            results.append(await self._run_one(t))

        self._pending.clear()
        return results
```

**集成点**：
- `deep_think_agent.py` `_think_native_tool_calling()` 中，当 LLM 返回多个 tool_use 块时，用 `AsyncToolExecutor.execute_batch()` 替代逐个 `await`
- 单个 tool_use 块退化为串行，无额外开销

**改动范围**：
- 新增 `app/services/execution/async_tool_executor.py`
- `deep_think_agent.py` — 替换工具执行调用
- 无需改动现有工具实现

**验收标准**：
- [ ] 3 个并发安全工具（web_search + grep + read_file）的执行时间 ≈ max(单个时间)，而非 sum
- [ ] 非安全工具仍然串行，行为不变
- [ ] 测试覆盖：并发成功、部分失败、全部失败

---

## Phase 3：工具进度推送

### 为什么第三做

并发执行后，长时间运行的工具（code_executor、phagescope）需要向用户汇报进度，否则用户看到的是空白等待。

### 3.1 进度回调协议

**Claude Code 参考**：
```typescript
type ToolCallProgress<P> = {
  type: 'progress'
  data: P  // Tool-specific progress data
}
```

**我们的实现**：
```python
@dataclass
class ToolProgress:
    tool_name: str
    tracking_id: str
    stage: str          # "started" | "running" | "completed" | "failed"
    message: str        # 人类可读的进度信息
    percent: Optional[float] = None  # 0.0 - 1.0
    detail: Optional[Dict] = None    # 工具特定的进度数据
```

**集成**：
- `ToolContext` 包含 `on_progress: Callable[[ToolProgress], Awaitable[None]]`
- 工具 handler 内部调用 `await context.on_progress(ToolProgress(...))`
- `deep_think_agent.py` 的 `on_thinking` 回调桥接到 SSE 推送
- 前端接收 SSE progress 事件，渲染工具执行状态

**改动范围**：
- 新增 `app/services/execution/tool_progress.py`（ToolProgress dataclass）
- `tool_executor.py` — 将 progress callback 注入 context
- `deep_think_agent.py` — 桥接到 SSE
- 优先改造 `code_executor` 和 `phagescope`（最长运行时间的工具）

---

## Phase 4：上下文管理升级

### 为什么排第四

前三个 Phase 解决的是"工具执行效率"和"用户体感"问题。上下文管理解决的是"长对话可用性"问题 — 重要但不紧急，且实现复杂度高。

### 4.1 Token 感知的上下文预算

**现状**：字符截断，无 token 计算。

**目标**：
```python
class ContextBudgetManager:
    def __init__(self, model: str, max_context_tokens: int):
        self.tokenizer = get_tokenizer(model)  # tiktoken
        self.max_tokens = max_context_tokens
        self.warning_threshold = 0.8  # 80% 时预警

    def calculate_usage(self, messages: List[Dict]) -> ContextUsage:
        total = sum(self.tokenizer.count(m["content"]) for m in messages)
        return ContextUsage(
            used=total,
            limit=self.max_tokens,
            ratio=total / self.max_tokens,
            warning=total > self.max_tokens * self.warning_threshold,
        )

    async def compact_if_needed(self, messages, llm_service) -> List[Dict]:
        usage = self.calculate_usage(messages)
        if usage.ratio < self.warning_threshold:
            return messages
        # 用 LLM 摘要旧消息
        summary = await self._summarize_old_messages(messages, llm_service)
        return [summary_message] + recent_messages
```

### 4.2 会话记忆持久化

**Claude Code 参考**：CLAUDE.md 文件存储跨会话记忆。

**我们的目标**：
- 压缩时将旧对话摘要写入 `agentic_memory`（我们已有 A-mem 系统）
- 新会话自动加载相关记忆
- 不创建新文件机制 — 复用现有 A-mem

---

## Phase 5：权限与安全层（远期）

### 5.1 工具权限规则引擎

```python
class ToolPermissionEngine:
    def __init__(self):
        self.allow_rules: List[PermissionRule] = []  # e.g. "web_search(*)"
        self.deny_rules: List[PermissionRule] = []   # e.g. "terminal_session(rm -rf *)"
        self.ask_rules: List[PermissionRule] = []

    async def check(self, tool_name: str, params: Dict) -> PermissionDecision:
        # 1. 检查 deny 规则
        # 2. 检查 allow 规则
        # 3. 检查 ask 规则
        # 4. 调用 tool.check_permissions() 钩子
        # 5. 默认：allow（当前行为不变）
        ...
```

**注意**：我们是后端 API 服务，没有终端 UI 对话框。"ask" 模式需要通过 WebSocket 推送审批请求到前端。`terminal_session` 已有类似的 approval flow，可以复用。

---

## 实施顺序与预估

| Phase | 内容 | 预估工作量 | 依赖 | 用户体感提升 |
|-------|------|-----------|------|-------------|
| **1.1** | ToolDefinition 扩展 + 元数据标注 | ~~1-2 天~~ ✅ 已完成 | 无 | 无（基础设施） |
| **1.2** | ToolContext 注入 | ~~1 天~~ ✅ 已完成 | 1.1 | 无（基础设施） |
| **2.1** | AsyncToolExecutor 并发执行 | ~~2-3 天~~ ✅ 已完成 | 1.1 | **高** — 多工具查询提速 2-3x |
| **3.1** | 工具进度推送 | ~~2 天~~ ✅ 已完成 | 1.2 | **高** — 长任务不再黑屏等待 |
| **4.1** | Token 感知上下文预算 | ~~2-3 天~~ ✅ 已完成 | 无 | **中** — 长对话不再丢失上下文 |
| **4.2** | 会话记忆持久化 | 1-2 天 | 4.1 | **中** — 跨会话记忆延续 |
| **5.1** | 权限规则引擎 | 3-4 天 | 1.1 | **低**（安全加固，非功能提升） |

**总计**：约 12-17 天，分 5 个 Phase，每个 Phase 独立可上线。

---

## 不做的事（以及为什么）

| Claude Code 特性 | 不做原因 |
|-----------------|---------|
| React + Ink 终端 UI | 我们是 Web UI，不是 CLI |
| Bun 运行时 | 我们是 Python 栈 |
| Commander.js CLI 框架 | 我们是 API 服务，不需要 CLI 框架 |
| MCP 协议支持 | 目前工具数量可控，无需外部工具协议 |
| ML 权限分类器 | 过度工程，规则引擎足够 |
| Feature Flag + 死代码消除 | 工程规模不需要 |
| Git worktree 隔离 | 我们的 code_executor 已有独立 subprocess |
| Sub-agent 多模型切换 | 我们的 Plan 系统已覆盖此场景 |

---

## 参考文件索引

| 参考来源 | 路径 | 核心知识点 |
|---------|------|-----------|
| Claude Code Agent Loop | `/Users/apple/work/claude-code/src/QueryEngine.ts` | AsyncGenerator 流式、多轮循环 |
| Claude Code Tool 接口 | `/Users/apple/work/claude-code/src/Tool.ts` | 工具元数据、权限钩子、进度回调 |
| Claude Code 流式执行 | `/Users/apple/work/claude-code/src/services/tools/StreamingToolExecutor.ts` | 并发/串行分类、结果排序 |
| Claude Code 上下文压缩 | `/Users/apple/work/claude-code/src/services/compact/` | 多策略压缩、memory 持久化 |
| Claude Code 权限系统 | `/Users/apple/work/claude-code/src/hooks/useCanUseTool.tsx` | 规则引擎、通配符匹配 |
| GAgent 工具注册 | `tool_box/tool_registry.py` | 当前 ToolDefinition |
| GAgent Agent 循环 | `app/services/deep_think_agent.py` | 当前 think loop |
| GAgent 工具执行 | `app/services/execution/tool_executor.py` | 当前串行执行 |
| GAgent 上下文 | `app/services/context/context_budget.py` | 当前字符截断 |

---

## Phase 6：code_executor 透明化（✅ 已完成）

### 问题
code_executor 是黑箱：内部调 LLM 生成代码 → 执行 → 失败则再调 LLM 修复 → 最多 3 次。Agent 只看到最终结果。

### 解决方案
- `auto_fix` 参数控制重试行为：`True`（plan executor 路径，自动重试）/ `False`（DeepThink 路径，透明返回）
- 失败时返回 `generated_code` + `error_category` + `fix_guidance`，agent 自己决定下一步
- 输出截断：>4000 字符写文件 + 返回首尾预览，避免上下文爆炸

### 数据适用性检查（待做）
LLM 在运行 bio tools 或 code_executor 前应验证输入格式是否匹配工具预期。例如用短肽 FASTA 跑 CheckV（病毒基因组工具）虽然不报错，但结果无意义。可在 system prompt 中加入指导。

### 远期方向：细粒度工具拆分
参考 Claude Code 的 BashTool + FileWriteTool + FileEditTool 模式，将 code_executor 拆分为独立的 bash_tool、file_write_tool、file_edit_tool，让 LLM 直接控制每一步。当前 auto_fix=False 是迈向这个方向的第一步。

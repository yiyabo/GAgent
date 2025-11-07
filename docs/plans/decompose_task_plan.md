# PlanDecomposer 需求梳理（独立 LLM × BFS）

## 1. 背景与目标

- **背景**：对话代理已能通过结构化动作维护 PlanTree，但缺少自动分解能力；历史递归模块已移除。
- **目标**：实现全新的 `PlanDecomposer`，使用独立 LLM 会话（不共享主对话上下文），在 BFS 顺序下生成子任务，并将结果写入计划。
- **新增要求**：模块既要支持“整棵计划的 BFS 自动分解”，也要支持“针对单个任务的按需分解”，两种模式共用核心逻辑。
- **保持约束**：PlanNode 不引入 `priority`、`status` 等字段；使用结构化 JSON 交互；对话 LLM 与分解 LLM 彼此独立。

## 2. 使用场景

1. **Plan 创建后自动初始化**  
   - 当 `StructuredChatAgent` 处理 `plan_operation/create_plan` 成功时，自动触发 `PlanDecomposer` 对新计划执行 BFS 分解（可配置是否启用）。
2. **用户/LLM 手动触发单节点分解**  
   - 在对话流程中，若 LLM 返回 `task_operation/decompose_task`（或后端接口直接调用），可指定某个节点重新/首次分解；该操作会调用 `PlanDecomposer` 的单节点模式，可选继续 BFS 深入。

## 3. 模块职责划分

| 组件 | 路径建议 | 职责 |
|------|----------|------|
| `PlanDecomposer` | `app/services/plans/plan_decomposer.py` | 对外主入口，统一管理配置、BFS 队列、写库与结果归档；提供 `run_plan(plan_id, opts)` 与 `decompose_node(plan_id, node_id, opts)` 两个公开方法。 |
| `BFSQueue`（内部） | 同文件内部类/函数 | 封装队列结构，支持按层级入队、预算控制；单节点模式可复用（队列仅含一个节点）。 |
| `DecompositionPromptBuilder` | 同文件或子模块 | 构造分解 LLM 的 prompt/payload：包含目标节点信息、计划概览、约束提示、示例等。 |
| `PlanDecomposerLLMService` | `app/services/llm/decomposer.py` | 调用独立 LLM 模型；校验/解析 JSON Schema，返回 `DecompositionPayload` 数据结构。 |
| `StructuredChatAgent` 扩展 | `app/routers/chat_routes.py` | 1) 在 `create_plan` 成功后调用 `run_plan`; 2) 新增处理 `decompose_task` 动作，调用 `decompose_node`。 |
| 文档与配置 | `docs/`, `app/config` | 记录配置项与流程，允许关闭自动分解或调整预算。 |

## 4. LLM 交互与 Schema
### 4.1 请求要素

- `target_task`：被分解节点的名称、instruction、路径、已有子节点概览。
- `plan_outline`：截断后的计划全局图，用于保持一致性。
- `constraints`：最大子任务数、命名/格式要求、禁止字段等。
- `mode_hint`：指明是“plan_bfs”或“single_node”，便于 LLM 调整策略。

### 4.2 响应 Schema（建议 Pydantic 模型）
```json
{
  "target_node_id": 12,
  "mode": "single_node",
  "should_stop": false,
  "reason": "若停止可提供说明",
  "children": [
    {
      "name": "子任务名称",
      "instruction": "执行说明",
      "dependencies": [4, 7],
      "context": {
        "combined": "可选的上下文摘要",
        "sections": [],
        "meta": {}
      },
      "leaf": false
    }
  ]
}
```

- `children` 顺序即写库顺序；允许为空。
- `leaf=true` 表示子任务无需继续分解（即便在 plan BFS 模式下也不入队）。
- `mode` 帮助我们在日志/统计中区分调用类型。

## 5. 执行流程
### 5.1 公共逻辑

1. `PlanDecomposer` 获取最新 `PlanTree`（通过 `PlanRepository.get_plan_tree`）。
2. 初始化队列（整计划模式：所有根节点；单节点模式：仅指定节点）。
3. 循环：
   - 出队 `(node_id, depth)`，构造 LLM 请求。
   - 调用 LLM，解析并校验 JSON。
   - 使用 `PlanRepository.create_task` 写入子任务，若有上下文则再 `update_task`。
   - 根据 `leaf` 标记与深度限制决定是否入队。
4. 累计统计信息，记录成功/失败节点。
5. 刷新 `PlanSession`，返回 `DecompositionResult`。

### 5.2 模式差异

- **plan BFS**：默认入队所有根节点，遵循 `MAX_BFS_DEPTH`、`TOTAL_NODE_BUDGET`，可配置在自动触发时是否跳过已存在子任务的节点。
- **single node**：仅处理一个节点，默认深度 1；可传 `expand_depth` 控制是否继续对子节点 BFS。若用户只想生成一层子任务，可将 `expand_depth=1` 且强制所有生成子任务 `leaf=true`。

## 6. 配置项（建议）

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `DECOMP_MAX_DEPTH` | BFS 最大深度（相对根/指定节点）。 | 3 |
| `DECOMP_MAX_CHILDREN` | 单节点最多生成子任务数。 | 6 |
| `DECOMP_TOTAL_NODE_BUDGET` | 每次调用最多新增节点数。 | 50 |
| `DECOMP_MODEL` | 独立 LLM 模型名称。 | 与主 LLM 可不同 |
| `DECOMP_AUTO_ON_CREATE` | 是否在 plan 创建后自动调用。 | True |
| `DECOMP_STOP_ON_EMPTY` | LLM 返回空子任务时是否直接停止。 | True |
| `DECOMP_RETRY_LIMIT` | LLM 调用失败重试次数。 | 1 |

## 7. 错误与边界处理

- **LLM 响应格式错误**：记录 `failed_nodes`，可按配置重试；仍失败则跳过。
- **写库异常**：捕获并回滚当前节点已写的子任务，记录错误并终止或继续后续节点（可配置）。
- **预算触发**：超过深度或节点预算时停止后续分解，在结果中注明 `stopped_reason`。
- **单节点模式特例**：若该节点已存在子任务，可配置是否覆盖（先删除旧子任务）或直接追加。

## 8. 返回结果结构
```python
class DecompositionResult(BaseModel):
    plan_id: int
    mode: Literal["plan_bfs", "single_node"]
    root_node_id: Optional[int]
    processed_nodes: List[int]
    created_tasks: List[PlanNode]
    failed_nodes: List[int]
    stopped_reason: Optional[str]
    stats: Dict[str, Any]  # 如调用次数、耗时、总新增节点等
```

- `StructuredChatAgent` 将该结果转成自然语言反馈，例如：“计划 #8 已生成 10 个子任务，其中节点 15 因 LLM 无响应未处理。”

## 9. 需要更新的文档与代码

1. **本文档**：作为设计说明，后续实现后补充示例与链接。
2. `docs/workflow.md`：在 plan 创建流程加入自动分解步骤；在手动操作章节补充“单节点分解”。
3. `docs/LLM_ACTIONS.md`：记录 `decompose_task` 动作（由系统/LLM 触发）以及参数结构。
4. 若保留旧文档 `RECURSIVE_DECOMPOSITION_GUIDE.md`，需标明已废弃或重写。
5. `example/`：提供脚本示例，例如 `example/run_decomposer.py`，演示两种模式。

## 10. 实施顺序建议

1. 定义 Pydantic schema 与 prompt 模板，完成 `PlanDecomposerLLMService`。
2. 实现 `PlanDecomposer` 核心逻辑（队列、写库、统计）。
3. 在 `StructuredChatAgent` 挂接两类入口：plan 创建自动调用、对话动作触发单节点分解。
4. 补充单元测试（空计划、单节点模式、预算限制、LLM 异常等）。
5. 更新文档/示例并进行端到端验证。

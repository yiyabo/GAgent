# PlanTree 有序插入方案

## 背景

当前 `task_operation.create_task` 未显式指定位置时，会调用 `PlanRepository.create_task` 的默认逻辑，将新节点追加到父节点子列表末尾。用户或 LLM 需要把任务插入到中间位置时只能先创建再移动，过程冗长，且 LLM 无法一步完成。

## 目标

1. 支持 LLM/前端在创建任务时准确选择插入位置。
2. 维持树结构语义，避免直接暴露脆弱的绝对下标。
3. 与现有 API 兼容：未指定位置时仍默认追加到末尾。
4. 前端 UI 能够提供“在 X 之前/之后创建任务”等操作。

## 方案概览

### Action 参数扩展

为 `task_operation.create_task` 增加可选参数：

| 参数             | 类型     | 说明 |
| ---------------- | -------- | ---- |
| `parent_id`      | int?     | 目标父节点。缺省表示挂在计划根节点。 |
| `anchor_task_id` | int?     | 同级兄弟节点 ID，用作参考锚点。 |
| `anchor_position`| enum     | `{before, after, first_child, last_child}`。与 `anchor_task_id` 搭配决定插入位置。 |
| `position`       | int?     | 高级参数，直接指定绝对下标（保留兼容性）。 |

约定优先级：`position` > (`anchor_task_id` + `anchor_position`) > 默认末尾。

### 后端实现

1. **路由层 (`chat_routes.py`)**  
   - 在解析 `create_task` 时读取上述参数。  
   - 校验组合是否合法，例如 `before/after` 必须提供 `anchor_task_id`。  
   - 当未提供 `position` 时，调用辅助函数根据锚点计算最终插入下标。  
   - 错误场景（锚点不存在、父节点不匹配）返回 422，或fallback至末尾并记录 warning。  
   - 调用 `PlanRepository.create_task(..., position=resolved_position)`。
   - 为兼容旧模型输出，继续支持 `insert_before` / `insert_after` 字段，并在路由层映射为新的锚点语义。

2. **仓库层 (`PlanRepository`)**  
   - 现有 `create_task` 已支持 `position`，继续复用 `_resequence_children` 保证顺序连续。  
   - 新增 `compute_insert_position(plan_id, parent_id, anchor_task_id, anchor_position)` 帮助函数，集中处理数据库查询与校验。

3. **移动任务扩展（可选）**  
   - `move_task` 可复用同样的锚点语义，实现统一的“插在 X 前后”表达。

### LLM 提示与 Schema

1. 更新 `LLMStructuredResponse` 模式：  
   - `CreateTaskParameters` 增加 `anchor_task_id`、`anchor_position` 字段并使用枚举校验。  
   - schema 示例中展示“在任务 12 之后插入”的用法。

2. 调整 `_build_prompt` 中的 action catalog / guidelines：  
   - 说明“如需插入特定位置，可提供 anchor 参数；缺省时追加到末尾”。  
   - 强调只引用上下文里已有的任务 ID（来自 plan outline / show_tasks / 历史消息），避免虚构。

3. 在 `docs/LLM_ACTIONS.md` 和相关提示文档补充描述及示例。

### 前端配合

1. **交互**  
   - 在任务树 UI 中提供“在…之前/之后创建任务”操作。  
   - 将用户选择转换为 chat action 的 `anchor_task_id` + `anchor_position` 参数，并复用现有聊天通道提交。

2. **状态刷新**  
   - 现有 PlanTree 刷新逻辑保持不变，服务器返回的节点顺序会反映插入结果。  
   - 在聊天动作日志中显示“插入位置”，便于用户确认（例如 `details.anchor_position`）。

### 测试

1. **单元测试**  
   - `PlanRepository.create_task` 在 `first_child`、`last_child`、`before`、`after` 情况下的插入顺序。  
   - 错误锚点（不存在、父节点不一致）应抛出异常。  
   - `position` 越界或负数时的处理。

2. **集成测试**  
   - `test_chat_routes` 构造含 anchor 参数的 `create_task` Action，验证响应和 PlanTree 顺序。  
   - 若 `move_task` 也支持锚点，追加对应测试用例。

3. **前端 E2E**（计划内，待 UI 实现）  
   - 模拟通过 UI 在某节点前后插入任务，并校验渲染顺序与服务器一致。

### 回滚策略

- 若 LLM 在新参数下频繁出错，可在提示中临时禁用锚点指令或在后端降级为“忽略锚点直接追加”并附带 `metadata.warning`。  
- 保留 `position` 字段，方便工具/脚本在需要时绕过锚点逻辑。

## 开发任务概述

1. 后端：实现锚点位置计算、参数校验、错误处理。  
2. LLM Schema/提示：更新模型配置、文档。  
3. 前端（可分阶段）：新增插入位置 UI、拼装参数。  
4. 文档与测试：覆盖新增语义，保持计划文档同步。

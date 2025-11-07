# StructuredChatAgent 动作链路测试指引

本文档描述了如何验证 `StructuredChatAgent` 在接收不同 LLM 动作时的完整执行流程，保证后端处理逻辑与自动分解/执行等能力按预期工作。

## 目标

1. 在不依赖真实 LLM 输出的情况下，确认代理能够正确解析并执行结构化动作（create_plan、create_task、move_task、execute_plan 等）。
2. 在需要真实联网测试时，指导如何构造请求并观察数据库（PlanTree）与聊天回执的变化。

## 推荐方式

### 1. 单元测试（强烈推荐）

仓库内的 `test/test_structured_agent_actions.py` 已经覆盖了典型动作序列：

| 用例 | 模拟动作 | 验证点 |
| --- | --- | --- |
| `test_create_plan_and_auto_decompose` | `plan_operation:create_plan` | 计划创建 + `_auto_decompose_plan` 被调用（测试中 stub 出自动添加的子任务） |
| `test_create_task_and_update` | `create_task`、`update_task_instruction` | 节点新增与说明更新 |
| `test_move_and_delete_task` | `move_task`、`delete_task` | 节点移动到根、节点删除 |
| `test_execute_plan_action` | `plan_operation:execute_plan` | 使用 stub 执行器将结果写回 PlanTree |

这些测试通过 `monkeypatch` 替换 `_invoke_llm`，直接注入结构化 JSON 响应，因此不依赖外部 LLM，执行稳定快速：

```bash
conda run -n agent pytest test/test_structured_agent_actions.py
```

### 2. 脚本驱动（需要真实 LLM）

若需联调实际模型，可以编写一个小脚本模拟 `/chat/message` 请求：

1. 构造 `LLMStructuredResponse` 格式的字符串作为 LLM 输出；
2. 将 `StructuredChatAgent._invoke_llm` 替换成返回该字符串的协程；
3. 调用 `agent.handle("用户指令")`，观察 `AgentResult`、数据库中的 PlanTree，以及 `plan_session` 状态。

脚本示例（伪代码）：

```python
from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMStructuredResponse, LLMReply, LLMAction
from app.services.plans.plan_session import PlanSession

async def main():
    session = PlanSession()
    agent = StructuredChatAgent(plan_session=session)

    response = LLMStructuredResponse(
        llm_reply=LLMReply(message="已创建计划"),
        actions=[LLMAction(kind="plan_operation", name="create_plan", parameters={"title": "测试计划"})],
    )

    agent._invoke_llm = lambda *_: response  # mock
    result = await agent.handle("请创建一个计划")
    print(result.steps, result.bound_plan_id)
```

如果要调用真实 LLM，则需要确保模型确实返回符合 schema 的 JSON；否则 Agent 会因解析失败或缺少动作而直接返回。

### 3. 前端/接口验证

在前端或通过 API 调用 `/chat/message`：
1. 提示 LLM 「请严格按照 JSON schema 输出」，并包含一个 `create_plan` 动作；
2. 观察后端日志，确认 `PlanDecomposer` 被触发；
3. 用 `PlanTree` 端点或数据库查看是否新增了自动生成的子任务。

## 注意事项

- 自动分解依赖 `DECOMP_MODEL`、`DECOMP_PROVIDER` 等配置，请确保环境变量正确设置，并使用支持同步模式的模型。
- 若模型返回普通文本而非结构化 JSON，`StructuredChatAgent` 不会执行任何动作；此时需要改用单元测试或 mock。
- `plan_executor` 在测试中可以使用 `_StubExecutorLLM`，避免真实执行耗时或产生外部依赖。

## 相关文件

- `app/routers/chat_routes.py` — 结构化动作分发与自动分解、执行逻辑。
- `test/test_structured_agent_actions.py` — 各主要动作的单元测试。
- `scripts/verify_llm_connectivity.py` — 联通性脚本（未走 StructuredChatAgent，但可用于验证模型可用性）。

通过以上流程，可以覆盖 StructuredChatAgent 的主要动作路径，确保后端在不同 LLM 响应下均能正确处理。

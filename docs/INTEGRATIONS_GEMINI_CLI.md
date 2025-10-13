# Gemini CLI 集成方案（调研与接入计划）

本项目将引入一个“像 Gemini CLI 一样的实时对话体验”，并与现有任务/计划/评估链路打通。

## 目标
- 终端实时对话（文本流式立即可用；语音实时可选）
- 支持函数调用（tool calls）→ 映射为本地 Todo/Task 操作
- 会话持久化与多轮上下文
- 与现有 Plan/Tasks 表打通（会话生成的 Todo → 任务入库 → 可执行）

## 现状
- 上游源码已放置：`gemini-cli/`（Node/TS；bin: `gemini`）
- 我们项目 Python 侧已具备：
  - 配置与日志（`services/foundation`）
  - 任务/计划/评估/上下文全链路

## 运行（上游 CLI 验证）
> Node.js ≥ 20；需配置 `GOOGLE_API_KEY`。

```bash
cd gemini-cli
npm ci
npm run build
# 方式1：npx 直接调用
npx gemini --help
# 方式2：本地 bin
node bundle/gemini.js --help
```

## 两阶段接入路线

### Phase 1（纯 Python 文本流式对话，1 周）
- CLI 新增：`python -m cli.main --chat --provider gemini --model gemini-1.5-flash`
- 功能：
  - 流式输出（SSE/事件迭代）
  - 会话持久化（SQLite 新表或沿用 tasks/inputs/outputs 快照）
  - TODO 工具：定义 JSON Schema 输出或函数调用协议 → 映射 `add/list/check/del` 到任务库
- 优点：无 Node 依赖；与现有链路直连

### Phase 2（Realtime/语音低时延，1–2 周）
- 方案B：Node Realtime 子进程桥接
  - 新增 `tools/realtime-node/bridge.ts`（或直接复用 `gemini-cli`），实现 stdio JSON-RPC：
    - `start`, `send_text`, `send_audio`, `tool_result`, `stop`
  - Python 侧增加 `GeminiRealtimeBridge`：
    - 启动 Node 子进程、路由事件、管理会话
  - CLI 增加：`--chat-rt`（Realtime 模式）支持语音/工具回调
- 优点：较好复用上游 Realtime 能力（WebRTC/WS），快速获得“可用”体验

## Tool Calls（与 Todo/Task 集成）
- 定义工具：
  - `todo.add(text)` 新增一条（入库到 tasks，status=pending）
  - `todo.list()` 列出当日/当前会话的 todo（来自 tasks）
  - `todo.check(id)` 勾选完成（status=done）
  - `todo.delete(id)` 删除或归档
- 会话中触发 tool_call：
  - Realtime/Streaming 事件转为 JSON 回传 Python → 调用我们的仓储接口 → 返回结果 → 再回发给 LLM

## 配置
- `services/foundation/settings.py` 增加：
  - `gemini_api_key`（读 GOOGLE_API_KEY）、`gemini_model`、`gemini_realtime_enable`
- CLI 统一：
  - `--provider {gemini,openai,anthropic,xai}`
  - `--model <name>`

## 里程碑
- M0：上游 CLI 跑通（本地）
- M1：Python 流式 chat MVP（文本 + 会话 + 简易 todo）
- M2：Realtime 子进程桥接（语音可选）
- M3：计划/评估联动（会话产出的 todo → 计划 approve → 执行）

## 风险与处理
- Realtime 依赖 WebRTC/wrtc，Node 环境需装原生依赖
- JSON 协议需固定 Schema（工具调用/事件），避免解析歧义
- 速率/限流：对消息长度与调用频度做节流

---

维护人：
- 集成负责人：你（或指定）
- 我负责：桥接协议、CLI 参数与与任务系统对接方案


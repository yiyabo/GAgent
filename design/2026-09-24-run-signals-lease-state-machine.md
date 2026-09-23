# 运行态信号落库 + Worker Lease + Turn 状态机化（W3+W4）

日期：2026-09-24。状态：实施中。基线：1709922b + W1（7ec5ce85）。

## 背景与现状

多实例部署的两个前置缺口（审计 §32 清单第 5、6 项）：

1. **运行态信号不落库**：chat run 的 cancel/steer 信号只有两个传输通道——
   进程内 hub（`chat_run_hub.py`，内存 dict）与 realtime_bus 路由
   （`route_control_message`，带 ack，2s 超时）。.8 生产无 Redis，bus 退化为
   进程内；即使配置了 Redis，owning worker 宕机时路由返回 False，信号**直接丢失**，
   run 只能靠 wall-clock 守卫自然死亡。
2. **chat_runs 无 worker lease**：`fix_stale_chat_runs_on_startup` 假设单进程——
   启动时把**所有** queued/running 行判死。多实例下 worker B 启动会误杀 worker A
   的活 run。`_resume_idempotent_run` 同理：`has_live_worker_task` 是进程内检查，
   会把别的 worker 持有的 queued run 重复派发。
3. **状态机隐式**：status 写点散落（create/mark_started/mark_finished/stale 修复），
   无迁移合法性概念，终态可被覆写。

已有可复用件：realtime_bus 的 owner lease（内存/Redis，TTL 60s/续期 20s）、
`get_worker_id()` 公开实例标识、`route_control_message` 跨 worker 快速通道。

## 设计

### W4：turn 状态机（先行，W3 的写点都走它）

新模块 `app/services/chat_run_state.py`：

- 状态集：active = {queued, running}；terminal = {succeeded, failed, cancelled}。
- 迁移表：`queued → {running, failed, cancelled}`；`running → {succeeded, failed,
  cancelled}`；终态无出边；自环视为幂等 no-op（成功，不覆写 finished_at）。
- 喉道 `transition_chat_run_status(...)`：**原子** UPDATE（`WHERE run_id=? AND
  status IN <合法前态>`），rowcount=0 时 SELECT 现态：等于目标态→幂等成功；
  否则记 warning（`CHAT_RUN_STATE_STRICT=1` 时抛 `IllegalChatRunTransition`），
  返回 False。与事件 schema 的 STRICT 模式（§33）同一屋风格。
- repository 的 `mark_chat_run_started` / `mark_chat_run_finished` 改走喉道，
  函数签名与 COALESCE 语义不变（调用方零改动）。

### W3：信号表 + lease 列 + 泵/心跳/清扫

**schema**（`app/database.py`，沿用 `_ensure_chat_run_columns` 增量迁移模式）：

```sql
CREATE TABLE IF NOT EXISTS chat_run_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- 'cancel' | 'steer'
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMP
);
CREATE INDEX idx_chat_run_signals_run_pending
    ON chat_run_signals(run_id, consumed_at);

ALTER TABLE chat_runs ADD COLUMN worker_id TEXT;
ALTER TABLE chat_runs ADD COLUMN heartbeat_at TIMESTAMP;
ALTER TABLE chat_runs ADD COLUMN lease_expires_at TIMESTAMP;
```

**写侧（routes）**：`cancel_run` / `steer_run` 在 owner 校验后**先落信号行**
（持久保证），再走路由/进程内快速通道（低延迟）。steer 的验收语义从
"快速通道收才算"改为"run 活跃 + 行落库即收"（快速通道失败不再 409；
run 非活跃仍 409，不存在仍 404，越权仍 403）。响应体不变。

**读侧（worker 泵）**：`app/services/chat_run_signals.py`——`execute_chat_run`
启动一个 pump task，每 `CHAT_RUN_SIGNAL_POLL_SECONDS`（默认 1.5s）拉该 run 的
未消费信号：cancel→`hub.request_cancel`，steer→`hub.push_steer_message`，
然后标 consumed_at。DB 异常 fail-open（快速通道仍在）。run 结束泵停。
单实例下快速通道本就全覆盖，泵是纯增量保障，**行为零变更**。

**lease**：`execute_chat_run` 启动时原子认领
（`worker_id=get_worker_id(), lease_expires_at=datetime('now','+N秒')`，
N=`CHAT_RUN_LEASE_TTL_SECONDS` 默认 30），心跳 task 每 N/3 秒续期，
finally 释放（清 lease_expires_at，worker_id 留档取证）。

**清扫（替代 fix_stale 的全杀语义）**：`reap_expired_chat_runs()`——
- `status IN (queued,running) AND lease_expires_at < now`（租约过期：worker 死了）
- 或 `lease_expires_at IS NULL AND created_at < now - TTL`（认领前崩窗/历史行；
  新建的 NULL-lease 行在 TTL 窗口内**不杀**，保护测试与正常派发窗）

判死动作与旧版一致（补 error 事件 + 置 failed）。启动时与周期
（`CHAT_RUN_SWEEP_INTERVAL_SECONDS` 默认 30s）各跑一次，挂在 main.py lifespan。
单 worker 重启语义：崩溃超 TTL→启动即收；快速重启→周期清扫 TTL 内收，
error 事件保证 SSE 客户端不挂死（仅晚 ≤TTL，keepalive 覆盖）。

**幂等重入修缝**：`_resume_idempotent_run` 重派发条件加 lease 判定——
queued 且 lease 未过期且属于**其他** worker 时不重复派发（多实例防双跑）；
lease 过期/NULL/本进程无活 worker 时才补派发。

## 不做的事

- 不改 pause/skip：计划级暂停（§24）已在 plans 表落库；chat run 级无 pause 端点。
- 不引入 Redis 强依赖：bus 保持现状，信号表是 DB 兜底而非替代快速通道。
- plan/job 执行器的 lease（job_runs 等）后续另行立项。

## 验证

- 新测试：状态机迁移合法性（合法/非法/自环/终态/STRICT）；信号表读写消费；
  cancel/steer 端点落行；泵应用信号；lease 认领/心跳/过期清扫/NULL-lease 宽限；
  幂等重入 lease 判定。
- 回归：app/tests/chat + unit 全绿；全量基线 51=51。
- 部署：.8 现有数据 worker_id 全 NULL、无活跃 run（重启前查证），迁移零冲突。

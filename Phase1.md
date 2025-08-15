# Phase 1 实施说明：上下文图谱基础

## 1. 概述
- 目标：为任务系统引入轻量的“任务关系图（Context Graph）”，并基于该图在执行前拼装最小可用上下文。
- 范围：数据层新增任务链接表、仓库 CRUD 与查询、上下文服务、API 路由、执行器可选集成，以及基础测试。
- 成果：可创建/删除/查询任务间有向链接；可预览任务上下文；执行时可按需在提示前加入上下文。

## 2. 代码改动总览
- 仓库层（SQLite） `app/repository/tasks.py`
  - 新增表：`task_links(from_id, to_id, kind)`（按需懒创建，向后兼容）
  - 新增方法：
    - `create_link(from_id, to_id, kind)`
    - `delete_link(from_id, to_id, kind)`
    - `list_links(from_id=None, to_id=None, kind=None)`
    - `list_dependencies(task_id)`：返回上游依赖（`requires` 优先于 `refers`），再按任务优先级排序
- 接口层 `app/interfaces/__init__.py`
  - `TaskRepository` 扩展上述方法（非抽象，默认抛 `NotImplementedError`，不破坏现有 FakeRepo 测试）
- 服务层 `app/services/context.py`
  - `gather_context(task_id, include_plan=True, include_deps=True, k=5, manual=None)`
  - 汇集：依赖任务（`requires`/`refers`）、同计划任务（同前缀）、手动指定任务；返回结构化 sections 与合并后的 `combined` 文本
- API 路由 `app/main.py`
  - `POST /context/links` 创建链接
  - `DELETE /context/links` 删除链接
  - `GET /context/links/{task_id}` 查询入/出边
  - `POST /tasks/{task_id}/context/preview` 预览上下文包
  - `POST /run` 接收 `use_context` 标志并传给执行器
- 执行器 `app/executor.py`
  - `execute_task(task, repo=None, use_context=False)`：当 `use_context=True` 时调用 `gather_context()`，将 `[Context] ... [Task Instruction]` 拼接到提示前
- 测试 `tests/test_context.py`
  - 覆盖链接 CRUD 与依赖排序、`gather_context()`、端到端 API（含 `/run` 的 `use_context`）

## 3. 使用指南（API 快速参考）
- 创建链接
```http
POST /context/links
Content-Type: application/json
{
  "from_id": 1,
  "to_id": 2,
  "kind": "requires"  // 或 "refers"
}
```
- 删除链接
```http
DELETE /context/links
{
  "from_id": 1,
  "to_id": 2,
  "kind": "requires"
}
```
- 查询某任务的链接
```http
GET /context/links/{task_id}
// 返回 { inbound: [...], outbound: [...] }
```
- 预览上下文
```http
POST /tasks/{task_id}/context/preview
{
  "include_deps": true,
  "include_plan": true,
  "manual_ids": [3,4]
}
```
- 运行并启用上下文
```http
POST /run
{
  "title": "计划名（可选）",
  "use_context": true
}
```
- 本地开发建议
  - 设置 `LLM_MOCK=1` 环境变量可启用可预测的 mock 响应，便于测试与演示。

## 4. 设计决策与默认值
- 链接类型：`requires`（硬依赖）、`refers`（软引用）
- 依赖排序：`requires` 优先，其次按任务 `priority` 升序
- `gather_context()` 默认包含依赖与同计划任务，软上限每类 5 条（`k=5`）
- 计划同名判定：沿用 `utils.plan_prefix()` 与 `utils.split_prefix()`

## 5. 兼容性与迁移
- 无破坏性变更：未修改既有表结构；`task_links` 通过 `CREATE TABLE IF NOT EXISTS` 懒创建
- 旧数据无须迁移即可运行；仅在首次创建链接/查询依赖时建表

## 6. 测试与质量
- 新增 `tests/test_context.py`，覆盖：
  - 链接 CRUD、`list_dependencies()` 排序
  - `gather_context()` 基本汇集能力
  - API 端到端：创建链接、上下文预览、携带上下文执行
- 当前结果：7 个测试全部通过

## 7. 已知限制（Phase 1 范围外）
- 未引入语义检索/嵌入；上下文选择未做预算与摘要
- 调度仍为 BFS，未对 `requires` 做拓扑调度/环检测
- 上下文模板与打分/裁剪策略尚未参数化

## 8. 下一步建议（Phase 2 提要）
- 上下文预算与摘要：字符/Token 配额、长内容截断与摘要
- 调度与依赖：基于 `requires` 的 DAG 调度、环检测与报错
- 选择策略：优先级分层，依赖 > 计划同级 > 手动 > 软引用
- 可观测性：记录执行所用上下文快照与配置，用于复现
- 模板化：将执行提示模板抽离为可配置模板

## 9. 相关文件一览
- `app/repository/tasks.py`
- `app/interfaces/__init__.py`
- `app/services/context.py`
- `app/main.py`
- `app/executor.py`
- `tests/test_context.py`

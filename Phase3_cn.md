# Phase 3（已完成）— 依赖感知调度、上下文快照与 TF‑IDF 阈值

本阶段聚焦三类能力：
 
 - 依赖感知调度（DAG）与环检测
- 上下文快照的保存、列举与导出（API/CLI）
- TF‑IDF 检索阈值全面暴露（API/CLI），并补充预算元数据与 LLM 客户端重试/退避

## 1. 交付内容概览
 
 - 调度策略
  - 新增 DAG 调度（`requires` 依赖），提供稳定拓扑顺序（对同层使用 `(priority ASC, id ASC)` 打破平局）。
  - `/run` 支持 `schedule`: `bfs|dag`；CLI 对应 `--schedule bfs|dag`。
  - 检测并报告环：当存在环返回 400，`detail.error = "cycle_detected"`，附带 `nodes`/`edges`/`names`。
- 上下文快照
  - `/run` 可在执行时保存上下文快照：`context_options.save_snapshot=true`，并以 `context_options.label` 标记。
  - 新增快照 API：
    - `GET /tasks/{task_id}/context/snapshots`（列举）
    - `GET /tasks/{task_id}/context/snapshots/{label}`（按标签获取）
  - CLI 支持列举与导出：`--list-snapshots`、`--export-snapshot --task-id ... --label ...`。
- TF‑IDF 阈值
  - 通过 API/CLI 覆盖环境变量：
    - `tfidf_min_score`（float，最低分阈值）
    - `tfidf_max_candidates`（int，最大候选条目数，用于评分前的粗筛）
  - 与 `tfidf_k`、`include_deps`、`include_plan`、预算选项协同工作。
- 预算元数据（`apply_budget`）
  - 每段新增元信息：`allowed`、`allowed_by_total`、`allowed_by_per_section`、`truncated_reason`（`none|per_section|total|both`）、`group`、`index`。
  - 若未提供任何上限（`max_chars`/`per_section_max`），函数返回原始 bundle，不附加 `budget` 字段。
- LLM 客户端重试/退避（`app/llm.py`）
  - 可配置重试与指数退避，环境变量：
    - `LLM_RETRIES`（默认 2）
    - `LLM_BACKOFF_BASE`（秒，默认 0.5）
  - 重试策略：5xx 与网络错误重试；4xx 不重试。

## 2. API 用法
 
 - 运行任务（带上下文与调度）

```json
POST /run
{
  "title": "Gene Editing Whitepaper",
  "schedule": "dag",
  "use_context": true,
  "context_options": {
    "include_deps": true,
    "include_plan": true,
    "tfidf_k": 2,
    "tfidf_min_score": 0.15,
    "tfidf_max_candidates": 200,
    "max_chars": 1200,
    "per_section_max": 300,
    "strategy": "sentence",
    "save_snapshot": true,
    "label": "exp-ctx"
  }
}
```

 - 上下文快照（列举与按标签获取）

```text
GET /tasks/{task_id}/context/snapshots
GET /tasks/{task_id}/context/snapshots/{label}
```

 - 错误示例（DAG 检测到环）

```json
{
  "detail": {
    "error": "cycle_detected",
    "nodes": [1, 2, 3],
    "edges": [{"from":1,"to":2},{"from":2,"to":3},{"from":3,"to":1}],
    "names": {"1":"A","2":"B","3":"C"}
  }
}
```

## 3. CLI 用法
 
 - 执行某计划（DAG 调度 + 上下文 + TF‑IDF 阈值 + 快照保存）

```bash
conda run -n LLM python agent_cli.py --execute-only --title Demo \
  --schedule dag --use-context \
  --tfidf-k 2 --tfidf-min-score 0.15 --tfidf-max-candidates 200 \
  --max-chars 1200 --per-section-max 300 --strategy sentence \
  --save-snapshot --label demo-ctx
```

 - 普通执行（不指定 title 将按调度执行 pending）

```bash
conda run -n LLM python agent_cli.py --use-context \
  --tfidf-k 2 --tfidf-min-score 0.2 --tfidf-max-candidates 100
```

 - 快照工具

```bash
# 列举某任务的快照
conda run -n LLM python agent_cli.py --list-snapshots --task-id 12

# 导出某个标签的快照到 output.md（可通过 --output 指定）
conda run -n LLM python agent_cli.py --export-snapshot --task-id 12 --label L1 --output snapshot.md
```

## 4. 环境变量与配置
 
 - LLM / 网络
  - `GLM_API_KEY`、`GLM_API_URL`、`GLM_MODEL`
  - `LLM_MOCK=1`：Mock 模式（无需外部 API）。
  - `LLM_RETRIES`、`LLM_BACKOFF_BASE`：重试次数与指数退避基数（秒）。
- TF‑IDF 默认值（可被 API/CLI 覆盖）
  - `TFIDF_MAX_CANDIDATES`（默认 500）
  - `TFIDF_MIN_SCORE`（默认 0.0）
- 预算与调试
  - `CTX_DEBUG`/`CONTEXT_DEBUG`/`BUDGET_DEBUG`：输出结构化调试日志

## 5. 测试
 
```bash
# 使用 Mock LLM，快速跑完所有测试
conda run -n LLM python -m pytest -q
```

## 6. 兼容性与确定性
 
 - DAG 调度提供稳定顺序，便于复现。
- 预算裁剪在相同输入下是确定性的；无上限时不附加 `budget` 字段。
- Mock 模式下 `LLM` 行为稳定，适合 CI。

## 7. 关联文档
 
 - 总览与快速开始：见 `README.md`
- 未来规划（中文）：`Future_Plan_cn.md`

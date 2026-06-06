# 文件存储架构重构实施计划

## 目标

将文件存储从"项目级混合目录"重构为"session 隔离 + raw_files 单一真相源 + deliverables 精选"的三层架构。

## 当前架构（问题）

```
{PROJECT_ROOT}/
├── results/              ← 多 session 混合污染（phage + plan126 + 其他项目）
├── output/               ← 同上
├── results/plans/plan_X/ ← artifact_contracts 硬编码的项目级路径
│
runtime/session_X/
├── raw_files/            ← PathRouter 管理（正确但 LLM 经常绕过）
├── _scratch/             ← 执行沙箱
├── results/              ← session 级 results（冗余副本）
├── deliverables/         ← 从 deliverable_submit 显式提交
└── tool_outputs/         ← 工具中间输出
```

**核心矛盾**：
1. LLM 把 `results/xxx.csv` 解析为项目级绝对路径，绕过沙箱
2. `artifact_contracts.canonical_plan_root()` 硬编码写入 `{PROJECT_ROOT}/results/plans/`
3. 6+ 个工具各自实现 `_resolve_output_dir()`，逻辑不一致
4. 双重 promotion（unified + session results）产生冗余副本
5. 验证器在 failed 任务上找不到文件（已部分修复）

## 目标架构

```
runtime/session_X/
├── raw_files/                 ← 所有任务产出的单一真相源
│   ├── task_1/task_2/task_6/  ← PathRouter 层级结构
│   ├── task_1/task_18/
│   └── tmp/                   ← 非计划临时输出
├── artifacts/                 ← 从 raw_files 提升的规范化产物
│   └── plan_{id}/
│       ├── artifacts_manifest.json
│       └── {namespace}/...
├── deliverables/              ← 从 raw_files 中精选的产出
│   ├── manifest_latest.json
│   └── latest/{module}/
├── _scratch/                  ← 执行沙箱（临时，可清理）
│   └── plan{X}_task{Y}/run_{ts}/
└── tool_outputs/              ← 工具中间输出
```

**项目级 `results/` 和 `output/` 不再被任何代码写入。**

---

## 实施阶段

### Phase 1: 基础设施 — ToolOutputResolver（低风险）

**目标**：统一 6+ 个 `_resolve_output_dir()` 实现为一个中央解析器。

#### 1.1 创建 `app/services/tool_output_resolver.py`

```python
class ToolOutputResolver:
    """Central output directory resolver for all tools.
    
    Replaces the 6+ duplicate _resolve_output_dir() implementations
    across tool_box/tools_impl/.
    """
    
    def resolve(
        self,
        *,
        explicit_dir: Optional[str],
        tool_context: Optional[ToolContext],
        session_id: Optional[str],
        task_id: Optional[int],
        ancestor_chain: Optional[List[int]],
    ) -> Path:
        """Resolve output directory with consistent priority:
        1. explicit_dir (if provided and valid)
        2. PathRouter.get_task_output_dir() (if session + task available)
        3. PathRouter.get_tmp_output_dir() (session only)
        4. Fallback to project root / output
        """
```

#### 1.2 替换各工具的 `_resolve_output_dir()`

| 文件 | 当前实现 | 替换为 |
|------|---------|--------|
| `phagescope.py:539` | 自定义 `_resolve_output_dir()` | `ToolOutputResolver.resolve()` |
| `phagescope_research.py:102` | 自定义 | 同上 |
| `phagescope_bulk_download.py:365` | 自定义 | 同上 |
| `literature_pipeline.py:1260` | 自定义 | 同上 |
| `scientific_figure_generator.py:71` | 自定义 | 同上 |
| `sequence_fetch.py:135` | 自定义 | 同上 |
| `url_fetch.py:290` | 自定义 | 同上 |
| `result_interpreter.py:666` | `output_dir or "./results"` | 同上 |

**验证**：每个工具替换后运行对应测试。

---

### Phase 2: code_executor 路径指令重构（中风险）

**目标**：让 LLM 的产出文件自然落入 `raw_files/` 而非项目级目录。

#### 2.1 修改 LLM 指令（`code_executor.py` ~4109 行）

**当前**：
```python
results_dir = os.path.join(work_dir, "results")
task_desc = f"Save outputs to: {results_dir}"
```

**改为**：
```python
# work_dir 已经是 raw_files/task_X/ 的 scratch 映射
# 但 LLM 的 cwd 是 _scratch/.../run_ts/
# 关键：告诉 LLM 使用相对路径，并明确禁止项目级绝对路径
results_dir = os.path.join(work_dir, "results")
task_desc = (
    f"Working directory: {work_dir}\n"
    f"Save outputs to: {results_dir}\n"
    f"IMPORTANT: Use RELATIVE paths from working directory. "
    f"Do NOT use absolute paths like /home/.../results/."
)
```

#### 2.2 增强 post-execution promotion（`code_executor.py`）

**当前 promotion 链**：
1. `_promote_results_to_unified_dir()` → `raw_files/task_X/`
2. `_promote_task_results_to_session_root()` → `session/results/`（冗余）
3. `_promote_external_contract_artifacts()` → `unified_output_dir`

**改为**：
1. `_promote_results_to_unified_dir()` → `raw_files/task_X/`（保留）
2. ~~`_promote_task_results_to_session_root()`~~（**删除**，标记为 deprecated）
3. `_promote_external_contract_artifacts()` → `raw_files/task_X/`（改目标）
4. **新增**：`_promote_project_level_strays()` — 扫描项目级 `results/` 和 `output/` 中属于当前 session 的文件，移动到 `raw_files/`

#### 2.3 修改 `_promote_external_contract_artifacts()`

**当前**：复制到 `unified_output_dir`（已经是 `raw_files/task_X/`）
**确认**：这个函数的目标已经是正确的，只需确保它处理所有外部路径情况。

#### 2.4 新增 `_promote_project_level_strays()`

```python
def _promote_project_level_strays(
    *,
    session_dir: Path,
    unified_output_dir: Path,
    contract_artifacts: List[Dict[str, Any]],
    project_root: Path,
) -> List[str]:
    """Move files that LLM wrote to project-level results/output/ into raw_files/.
    
    Scans contract_artifacts for paths under project_root/results/ or 
    project_root/output/, copies them to unified_output_dir, and records
    the mapping for future reference.
    """
```

**验证**：
- 单元测试：模拟 LLM 写入项目级路径，验证 promotion 正确移动文件
- 集成测试：运行一个简单 plan，验证产出在 `raw_files/` 中

---

### Phase 3: artifact_contracts 路径迁移（高风险）

**目标**：将 `results/plans/plan_X/` 迁移到 `runtime/session_X/artifacts/plan_X/`。

#### 3.1 修改 `canonical_plan_root()`（`artifact_contracts.py:233`）

**当前**：
```python
def canonical_plan_root(plan_id: int) -> Path:
    return PROJECT_ROOT / "results" / "plans" / f"plan_{plan_id}"
```

**改为**：
```python
def canonical_plan_root(plan_id: int, *, session_id: Optional[str] = None) -> Path:
    if session_id:
        router = get_path_router()
        session_dir = router._get_session_dir(session_id, create=True)
        return session_dir / "artifacts" / f"plan_{plan_id}"
    # Backward compat: fall back to project-level for legacy sessions
    return PROJECT_ROOT / "results" / "plans" / f"plan_{plan_id}"
```

#### 3.2 修改 `artifact_manifest_path()`

同步更新，使用 session-scoped 路径。

#### 3.3 修改 `publish_artifact()`

更新复制目标为 session-scoped 路径。

#### 3.4 修改所有调用方

搜索 `canonical_plan_root`、`artifact_manifest_path`、`publish_artifact` 的所有调用方，传入 `session_id`。

**验证**：
- 单元测试：验证新旧路径格式都能正确解析
- 集成测试：运行 plan，验证 manifest 写入 session 目录
- 回归测试：验证旧 session 的 manifest 仍可读取

---

### Phase 4: LLM Prompt 更新（中风险）

**目标**：消除 LLM 指令中对项目级 `results/` 的硬编码引用。

#### 4.1 `plan_decomposer.py`（~100 行）

**当前**：告诉 LLM 使用 `{project_root}/results/...` 或 `{project_root}/output/...`
**改为**：使用 `{session_dir}/raw_files/...` 或相对路径

#### 4.2 `coder_prompt.py`（~156, 186 行）

**当前**：`"save to results/ directory"`
**改为**：`"save to the results/ subdirectory of your working directory"`

#### 4.3 `deep_think_agent.py`（~937, 948, 3481 行）

**当前**：引用 `results/` 和 `results/plans/plan_{id}/artifacts_manifest.json`
**改为**：引用 session-scoped 路径

#### 4.4 `prompt_builder.py`（~310 行）

**当前**：`results/plans/plan_{id}/artifacts_manifest.json`
**改为**：session-scoped manifest 路径

#### 4.5 `interpreter.py` / `plan_execute.py`

**当前**：`output_dir="./results"` 默认值
**改为**：`output_dir=None`，由 ToolOutputResolver 决定

**验证**：
- 运行 plan 分解，检查生成的 instruction 中不包含项目级路径
- 运行 code_executor，检查 LLM 不再写入项目级目录

---

### Phase 5: DeliverablePublisher 重构（中风险）

**目标**：deliverables 从 `raw_files/` 中挑选，而非从任意路径提交。

#### 5.1 修改 `publish_from_tool_result()` 的路径解析

**当前**（`publisher.py`）：
```python
# _resolve_path 优先级: session_dir/raw_files/ > session_dir/ > project_root/
```

**改为**：
```python
# 优先级: raw_files/ > session deliverables/ > (reject project-level paths)
```

#### 5.2 修改 `deliverable_submit` 工具

**当前**：LLM 提供任意路径，publisher 尝试解析
**改为**：LLM 提供文件名或相对路径，publisher 在 `raw_files/` 中查找

#### 5.3 新增自动 promote 机制

在 `tool_executor.py` 的 `publish_from_tool_result()` 调用前，自动将 `raw_files/` 中匹配 `artifact_contract.publishes` 的文件标记为 deliverable 候选。

**验证**：
- 单元测试：验证 deliverable_submit 从 raw_files 正确解析路径
- 集成测试：运行 plan，验证 deliverables 面板正确显示

---

### Phase 6: 验证器简化（低风险）

**目标**：因为所有产出都在 `raw_files/` 中，验证器的路径发现逻辑可以大幅简化。

#### 6.1 简化 `_augment_artifact_paths_with_discovered_outputs()`

**当前**：需要扫描多个目录、处理 legacy 路径
**改为**：主要扫描 `raw_files/task_X/` 及其子目录

#### 6.2 保留回退发现机制

Phase 0 中添加的 `_fallback_discover_outputs_for_failed_task()` 保留作为过渡期兼容，但添加 deprecation warning。

#### 6.3 更新 `_resolve_base_dir()`

优先使用 `raw_files/task_X/` 作为 base_dir。

**验证**：运行完整 `test_task_verification.py` 和 `test_plan_routes_verification.py`。

---

### Phase 7: 清理与迁移（低风险）

#### 7.1 删除 legacy promotion

删除 `_promote_task_results_to_session_root()`（Phase 2 中标记 deprecated）。

#### 7.2 删除 `task_path_generator.py`

这个 legacy 模块生成 `results/{name}/` 路径，不再需要。

#### 7.3 迁移脚本

创建 `scripts/migrate_storage_v2.py`：
- 扫描现有 session 的项目级 `results/` 文件
- 按 session 归属移动到 `raw_files/`
- 更新 manifest 引用

#### 7.4 更新 AGENTS.md 和文档

更新项目文档中的目录结构说明。

---

## 风险评估

| 阶段 | 风险 | 影响范围 | 缓解措施 |
|------|------|---------|---------|
| Phase 1 | 低 | 工具输出目录解析 | 逐个替换，每个工具独立测试 |
| Phase 2 | 中 | code_executor 行为变化 | 保留 fallback promotion 一个月 |
| Phase 3 | 高 | artifact manifest 路径变化 | 双写过渡期，新旧路径都可读 |
| Phase 4 | 中 | LLM 行为不可控 | 增加 post-execution 路径审计 |
| Phase 5 | 中 | deliverable 发布逻辑 | 保留 explicit 模式作为 fallback |
| Phase 6 | 低 | 验证器简化 | 保留回退机制 |
| Phase 7 | 低 | 清理 | 最后执行，确认无回归 |

## 依赖关系

```
Phase 1 (ToolOutputResolver)
    ↓
Phase 2 (code_executor) ← Phase 4 (LLM Prompts) 可并行
    ↓
Phase 3 (artifact_contracts)
    ↓
Phase 5 (DeliverablePublisher)
    ↓
Phase 6 (验证器简化)
    ↓
Phase 7 (清理)
```

## 测试策略

每个 Phase 完成后运行：
1. `pytest app/tests/plan/ -v` — plan 执行测试
2. `pytest app/tests/tools/ -v` — 工具测试
3. `pytest app/tests/unit/ -v` — 单元测试
4. 手动验证：运行一个小型 plan（3-5 个 task），检查文件位置

## 时间估算

| 阶段 | 预计工作量 | 文件数 |
|------|-----------|--------|
| Phase 1 | 2-3 小时 | 8 文件 |
| Phase 2 | 3-4 小时 | 2 文件 |
| Phase 3 | 4-5 小时 | 5 文件 |
| Phase 4 | 2-3 小时 | 5 文件 |
| Phase 5 | 3-4 小时 | 3 文件 |
| Phase 6 | 1-2 小时 | 1 文件 |
| Phase 7 | 2-3 小时 | 3 文件 |
| **总计** | **17-24 小时** | **~27 文件** |

## 回滚方案

每个 Phase 独立可回滚：
- Phase 1: 恢复各工具的原始 `_resolve_output_dir()`
- Phase 2: 恢复原始 promotion 链
- Phase 3: `canonical_plan_root()` 保留 backward compat fallback
- Phase 4: 恢复原始 prompt 文本
- Phase 5: 恢复原始 publisher 路径解析
- Phase 6: 保留回退发现机制

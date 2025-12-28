# Chinese to English Translation Plan

## Overview

This plan outlines the systematic approach to convert all Chinese content (comments, prompts, UI strings, documentation) to English throughout the GAgent codebase.

**Approach**: Direct replacement (no i18n system)
**Goal**: Full English-only codebase

---

## Summary of Chinese Content Found

| Category | File Count | Instances | Priority |
|----------|------------|-----------|----------|
| Python Backend | ~15 files | 251 | High |
| TypeScript/TSX Frontend | ~20 files | 300+ | High |
| Markdown Documentation | 4 files | 50 | Medium |
| Deprecated Prompt Files | 1 file | 270 lines | Low (delete) |

---

## Phase 1: Python Backend Files

### 1.1 Tool Implementations (`tool_box/tools_impl/`)

| File | Content Type | Action |
|------|--------------|--------|
| `mermaid_diagram.py` | Comments | Translate comments to English |
| `file_operations.py` | Comments, descriptions, examples | Translate all Chinese strings |
| `web_search.py` | Comments, prompts, error messages | Translate all content |
| `web_search/__init__.py` | Tool descriptions, examples | Translate descriptions |
| `web_search/handler.py` | Comments | Translate comments |
| `web_search/exceptions.py` | Docstrings | Translate to English |
| `web_search/providers/builtin.py` | Comments, prompts | Translate all |
| `database_query.py` | Comments, descriptions, log messages | Translate all |
| `claude_code.py` | Comments, task examples | Translate all |
| `shell_execution.py` | Descriptions | Translate tool descriptions |
| `document_reader.py` | Error messages, descriptions | Translate all |
| `internal_api.py` | Descriptions, examples | Translate all |
| `graph_rag/__init__.py` | Descriptions, error messages | Translate all |
| `graph_rag/service.py` | Error messages, log messages | Translate all |
| `graph_rag/exceptions.py` | Docstrings | Translate to English |
| `graph_rag/graph_rag.py` | Example queries | Update to English |

### 1.2 Error System (`app/errors/`)

| File | Content Type | Action |
|------|--------------|--------|
| `messages.py` | Error messages, descriptions, suggestions | Translate all Language.ZH_CN entries OR remove ZH_CN and keep only EN_US |
| `handlers.py` | Comments | Translate comments |
| `helpers.py` | Error message templates, default values | Translate all Chinese strings |

### 1.3 Integration Layer (`tool_box/`)

| File | Content Type | Action |
|------|--------------|--------|
| `integration.py` | Prompt templates | Translate Chinese prompt at line 302-313 |

### 1.4 Scripts (`scripts/`)

| File | Content Type | Action |
|------|--------------|--------|
| `init_memory_system.py` | Sample data, log messages | Translate all Chinese content |

### 1.5 External Memory Module (`execute_memory/A-mem-main/`)

| File | Content Type | Action |
|------|--------------|--------|
| `api.py` | Comments | Translate comments |
| `example_client.py` | Output messages, sample data | Translate all |

---

## Phase 2: TypeScript/TSX Frontend Files

### 2.1 Pages (`web-ui/src/pages/`)

| File | Content to Translate |
|------|---------------------|
| `Tasks.tsx` | "任务管理" -> "Task Management" |
| `Plans.tsx` | All UI labels, statistics titles, status messages |
| `System.tsx` | "系统设置" -> "System Settings" |
| `Dashboard.tsx` | Check for any Chinese content |
| `Memory.tsx` | Check for any Chinese content |

### 2.2 DAG Components (`web-ui/src/components/dag/`)

| File | Content to Translate |
|------|---------------------|
| `PlanTreeVisualization.tsx` | Status labels, loading messages, tooltips |
| `PlanDagVisualization.tsx` | Tooltip content, loading messages |
| `TreeVisualization.tsx` | All UI text, filter labels, button text |
| `DAGVisualization.tsx` | Comments, tooltip content, legend labels |

### 2.3 Layout Components (`web-ui/src/components/layout/`)

| File | Content to Translate |
|------|---------------------|
| `AppHeader.tsx` | Status labels, tooltips |
| `AppSider.tsx` | Menu labels, tooltips |
| `ChatLayout.tsx` | Comments, tooltips |
| `ChatMainArea.tsx` | All UI text, placeholders, quick actions |
| `ChatSidebar.tsx` | Title hints, button labels, modal content |
| `DAGSidebar.tsx` | All labels, statistics, tooltips |
| `ArtifactsPanel.tsx` | All UI text, empty states, tooltips |
| `ExecutorPanel.tsx` | Action labels, empty states |

### 2.4 Chat Components (`web-ui/src/components/chat/`)

| File | Content to Translate |
|------|---------------------|
| `ChatPanel.tsx` | All UI text, placeholders, quick actions |
| `ChatMessage.tsx` | Check for any Chinese content |
| `ToolResultCard.tsx` | Labels, status messages, error text |
| `FileUploadButton.tsx` | Error messages, tooltip |
| `UploadedFilesList.tsx` | Error messages |

### 2.5 Task Components (`web-ui/src/components/tasks/`)

| File | Content to Translate |
|------|---------------------|
| `TaskDetailDrawer.tsx` | Status labels, section titles, descriptions, button text |

### 2.6 Memory Components (`web-ui/src/components/memory/`)

| File | Content to Translate |
|------|---------------------|
| `SaveMemoryModal.tsx` | All form labels, options, placeholders, tips |
| `MemoryDetailDrawer.tsx` | Check for any Chinese content |
| `MemoryGraph.tsx` | Check for any Chinese content |

### 2.7 Common Components (`web-ui/src/components/common/`)

| File | Content to Translate |
|------|---------------------|
| `ErrorBoundary.tsx` | Error titles, descriptions, button labels |

---

## Phase 3: Markdown Documentation

| File | Action |
|------|--------|
| `todo.md` | Translate entire content to English |
| `tool_box/README.md` | Translate entire documentation to English |
| `execute_memory/A-mem-main/API_README.md` | Translate entire documentation to English |
| `tool_box/tools_impl/graph_rag/GRAPH_RAG_USAGE.md` | Translate example queries to English |

---

## Phase 4: Cleanup

### 4.1 Delete Files

- [ ] Delete `app/prompts/zh_CN.py`

### 4.2 Clean Up References

- [ ] Remove commented Chinese prompt loading code in `app/prompts/manager.py` (lines 38-43)
- [ ] Update `app/errors/messages.py` to remove `Language.ZH_CN` enum if not needed

---

## Phase 5: Testing and Validation

### 5.1 Verification Steps

- [ ] Run grep/search for remaining Chinese characters: `grep -rP '[\x{4e00}-\x{9fff}]' --include="*.py" --include="*.tsx" --include="*.ts" --include="*.md" .`
- [ ] Build frontend successfully: `cd web-ui && npm run build`
- [ ] Run backend without errors: `python -m app.main`
- [ ] Verify all UI displays correctly in English
- [ ] Check error messages display correctly

### 5.2 Manual Testing Checklist

- [ ] Navigate through all frontend pages
- [ ] Trigger various error states to verify error messages
- [ ] Use all tools to verify tool descriptions
- [ ] Check tooltips and hover states
- [ ] Verify loading states and empty states

---

## Implementation Notes

### Translation Guidelines

1. **Comments**: Translate to clear, concise English technical comments
2. **Error Messages**: Use standard error message patterns (e.g., "Failed to X: reason")
3. **UI Text**: Use standard UI terminology consistent with modern web applications
4. **Tool Descriptions**: Keep professional and clear for LLM understanding

### Common Translations Reference

| Chinese | English |
|---------|---------|
| 任务 | Task |
| 计划 | Plan |
| 执行 | Execute |
| 加载中 | Loading |
| 暂无 | No data / None |
| 失败 | Failed |
| 成功 | Success |
| 待执行 | Pending |
| 执行中 | Running |
| 已完成 | Completed |
| 已跳过 | Skipped |
| 刷新 | Refresh |
| 删除 | Delete |
| 保存 | Save |
| 取消 | Cancel |
| 确定 | Confirm / OK |
| 搜索 | Search |
| 记忆 | Memory |
| 对话 | Chat / Conversation |
| 工具 | Tool |
| 系统 | System |
| 设置 | Settings |

---

## Execution Order

1. **Start with Backend** - Less risk, easier to test
2. **Move to Frontend** - Higher visibility, more complex
3. **Documentation** - Can be done in parallel
4. **Cleanup** - Final step after verification
5. **Testing** - Throughout and at the end

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking changes in error handling | Medium | Test all error paths |
| UI layout issues from text length changes | Low | Review UI after translation |
| Missing translations | Medium | Use regex search to verify |
| Build failures | Low | Test builds incrementally |

---

## Estimated Scope

- **Total files to modify**: ~40 files
- **Total Chinese instances**: ~600+
- **Complexity**: Medium (mostly string replacements)

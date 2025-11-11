# Memory系统集成任务清单

> **分支**: `feature/memory-system-integration`  
> **开始时间**: 2025-11-11  
> **预计完成**: 1-2天

## 🎯 快速开始

### 第一步：执行器集成（最重要！）

#### ✅ 任务1: AtomicExecutor集成
**文件**: `app/execution/atomic_executor.py`

- [ ] 在文件顶部添加导入：
```python
from app.services.memory.memory_hooks import get_memory_hooks
```

- [ ] 在`__init__`方法中初始化：
```python
self.memory_hooks = get_memory_hooks()
```

- [ ] 在任务执行成功后添加（execute方法末尾）：
```python
# 保存任务执行记忆
try:
    await self.memory_hooks.on_task_complete(
        task_id=task_id,
        task_name=task_row.get('name', ''),
        task_content=task_row.get('content', ''),
        task_result=output,
        success=True
    )
except Exception as e:
    logger.warning(f"Failed to save task memory: {e}")
```

- [ ] 在异常处理中添加：
```python
except Exception as e:
    # 保存错误记忆
    try:
        await self.memory_hooks.on_error_occurred(
            error_message=str(e),
            error_type=type(e).__name__,
            task_id=task_id
        )
    except Exception as mem_err:
        logger.warning(f"Failed to save error memory: {mem_err}")
    raise
```

#### ✅ 任务2: 测试执行器集成
- [ ] 启动后端服务
- [ ] 创建并执行一个简单任务
- [ ] 检查数据库：`SELECT COUNT(*) FROM memories;`
- [ ] 应该看到至少1条记忆

---

### 第二步：聊天路由集成

#### ✅ 任务3: ChatRoutes集成
**文件**: `app/routers/chat_routes.py`

- [ ] 在文件顶部添加导入：
```python
from app.services.memory.chat_memory_middleware import get_chat_memory_middleware
```

- [ ] 在模块级别初始化：
```python
chat_memory_middleware = get_chat_memory_middleware()
```

- [ ] 在主要的聊天端点中（找到处理用户消息的地方）：
```python
# 保存用户消息
try:
    await chat_memory_middleware.process_message(
        content=user_message,
        role="user",
        session_id=session_id
    )
except Exception as e:
    logger.warning(f"Failed to save user message memory: {e}")

# ... LLM处理 ...

# 保存助手响应
try:
    await chat_memory_middleware.process_assistant_response(
        content=assistant_response,
        session_id=session_id
    )
except Exception as e:
    logger.warning(f"Failed to save assistant memory: {e}")
```

#### ✅ 任务4: 测试聊天集成
- [ ] 在前端发送几条消息
- [ ] 检查数据库记忆数量增加
- [ ] 查看Memory页面是否显示对话记忆

---

### 第三步：上下文系统集成

#### ✅ 任务5: 上下文构建器集成
**文件**: 查找上下文构建相关文件（可能在`app/services/context/`）

- [ ] 添加导入：
```python
from app.services.memory.memory_service import get_memory_service
from app.models_memory import QueryMemoryRequest, MemoryType
```

- [ ] 在构建上下文的方法中添加记忆查询：
```python
# 查询相关记忆
memory_service = get_memory_service()
try:
    relevant_memories = await memory_service.query_memory(
        QueryMemoryRequest(
            search_text=task_description,
            memory_types=[MemoryType.EXPERIENCE, MemoryType.KNOWLEDGE],
            limit=5,
            min_similarity=0.7
        )
    )
    
    # 添加到上下文
    if relevant_memories.memories:
        context_parts.append("\n## 相关历史经验：")
        for mem in relevant_memories.memories:
            context_parts.append(f"- {mem.content} (相似度: {mem.similarity:.1%})")
except Exception as e:
    logger.warning(f"Failed to query memories for context: {e}")
```

---

### 第四步：初始化数据

#### ✅ 任务6: 运行初始化脚本
```bash
conda run -n LLM python scripts/init_memory_system.py
```

- [ ] 运行脚本
- [ ] 确认导入了示例数据
- [ ] 检查嵌入向量生成状态

---

### 第五步：验证和测试

#### ✅ 任务7: 端到端测试
- [ ] 执行一个完整的任务流程
- [ ] 发送几条聊天消息
- [ ] 访问前端Memory页面
- [ ] 验证记忆显示正确
- [ ] 测试搜索功能
- [ ] 检查记忆统计信息

#### ✅ 任务8: 数据验证
```bash
# 检查记忆总数
sqlite3 data/databases/main/plan_registry.db "SELECT COUNT(*) FROM memories;"

# 检查记忆类型分布
sqlite3 data/databases/main/plan_registry.db "SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type;"

# 检查嵌入向量覆盖率
sqlite3 data/databases/main/plan_registry.db "SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN embedding_generated = 1 THEN 1 ELSE 0 END) as with_embedding,
    ROUND(100.0 * SUM(CASE WHEN embedding_generated = 1 THEN 1 ELSE 0 END) / COUNT(*), 2) as coverage_percent
FROM memories;"
```

---

## 📊 进度跟踪

### 核心集成状态
- [ ] AtomicExecutor集成完成
- [ ] AsyncExecutor集成完成（可选）
- [ ] ChatRoutes集成完成
- [ ] 上下文系统集成完成
- [ ] 初始化数据完成
- [ ] 端到端测试通过

### 数据指标
- [ ] 记忆总数 > 10
- [ ] 嵌入覆盖率 > 80%
- [ ] 至少3种记忆类型
- [ ] 前端正常显示

---

## 🔍 调试技巧

### 查看日志
```bash
# 查看后端日志中的memory相关信息
tail -f logs/app.log | grep -i memory
```

### 检查Memory Hooks状态
```bash
# 通过API检查hooks统计
curl http://localhost:9000/mcp/memory/hooks/stats
```

### 检查Memory统计
```bash
# 通过API检查memory统计
curl http://localhost:9000/mcp/memory/stats
```

### 手动保存测试记忆
```bash
curl -X POST http://localhost:9000/mcp/save_memory \
  -H "Content-Type: application/json" \
  -d '{
    "content": "测试记忆内容",
    "memory_type": "experience",
    "importance": "medium",
    "tags": ["测试"]
  }'
```

---

## ⚠️ 注意事项

1. **异步处理**: 所有memory操作都是async的，确保使用await
2. **错误处理**: Memory失败不应影响主流程，用try-except包装
3. **性能**: Memory保存应该快速，不阻塞主流程
4. **日志**: 添加适当的日志以便调试

---

## 📝 提交规范

完成后提交代码：

```bash
# 添加修改的文件
git add app/execution/atomic_executor.py
git add app/routers/chat_routes.py
git add app/services/context/...

# 提交
git commit -m "feat: integrate memory system into core workflows

- Add memory hooks to AtomicExecutor for task completion tracking
- Integrate chat memory middleware into chat routes
- Add memory query to context builder
- Initialize sample memory data

Closes #XXX"

# 推送到远程
git push origin feature/memory-system-integration
```

---

## 🎉 完成标志

当以下所有条件满足时，任务完成：

✅ 执行任务后数据库中自动出现记忆  
✅ 聊天后数据库中自动出现对话记忆  
✅ 前端Memory页面能看到所有记忆  
✅ 搜索功能正常工作  
✅ 嵌入向量覆盖率 >80%  
✅ 所有测试通过  

---

**详细计划**: 参见 `docs/plans/memory_system_integration_plan.md`

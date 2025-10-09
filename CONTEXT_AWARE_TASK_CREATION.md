# 上下文感知任务创建系统

## 🎯 问题描述

**之前的问题：**
用户在对话中说："我想学习有关双曲空间应用于人类蛋白质互作用预测的研究"
→ 系统创建ROOT任务 ✅

然后用户说："帮我写一个**相关的**文档，放在results文件夹里面"
→ 系统又创建了新的ROOT任务："撰写并保存文档至results文件夹" ❌

**核心问题：**
- 系统没有理解用户说的"相关的"是指现有ROOT任务
- 使用关键词匹配判断是否创建任务，违反了"完全使用LLM智能路由"的要求
- 没有追踪session中的ROOT任务上下文

## ✅ 修复方案

### 1. 上下文感知判断

新增 `_should_create_new_workflow()` 函数，使用LLM智能判断：

```python
async def _should_create_new_workflow(
    message: str, 
    session_id: Optional[str], 
    context: Optional[Dict[str, Any]],
    context_messages: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """
    使用LLM智能判断是否需要创建新ROOT任务，或在现有ROOT下添加子任务
    """
```

**判断逻辑：**

1. **检查现有ROOT任务**
   ```sql
   SELECT id, name, status FROM tasks 
   WHERE session_id = ? AND task_type = 'root' 
   ORDER BY created_at DESC LIMIT 1
   ```

2. **LLM分析用户意图**
   
   如果有现有ROOT任务：
   ```
   判断用户意图：
   A) 创建全新的、独立的ROOT任务（新项目）
   B) 在现有ROOT任务下添加子任务或补充内容
   C) 普通对话，不需要创建任务
   
   判断标准：
   - 用户说"新的"、"另一个" → A
   - 用户说"相关的"、"这个"、"补充"、"写一个文档" → B
   - 普通问答 → C
   ```

3. **返回决策**
   ```python
   {
       "create_new_root": bool,      # 是否创建新ROOT
       "add_to_existing": bool,      # 是否在现有ROOT下添加
       "existing_root_id": int,      # 现有ROOT的ID
       "reasoning": str              # LLM推理过程
   }
   ```

### 2. 在现有ROOT下添加子任务

新增 `_handle_add_subtask_to_existing()` 函数：

```python
async def _handle_add_subtask_to_existing(
    request: ChatRequest, 
    workflow_decision: Dict[str, Any],
    context_messages: Optional[List[Dict[str, str]]] = None
) -> ChatResponse:
    """在现有ROOT任务下添加子任务"""
```

**工作流程：**

1. 获取现有ROOT任务信息
2. 使用LLM生成子任务名称
3. 创建COMPOSITE任务
   ```python
   task_id = default_repo.create_task(
       name=f"COMPOSITE: {task_name}",
       parent_id=existing_root_id,
       root_id=existing_root_id,
       task_type="composite",
       session_id=request.session_id
   )
   ```
4. 返回友好的响应

### 3. 移除关键词匹配

**DEPRECATED:** `_is_agent_workflow_intent()` 函数
- 之前使用正则表达式和关键词匹配
- 违反了"完全使用LLM智能路由"的要求

**现在：** 完全使用LLM语义理解

## 📋 使用场景

### 场景1：创建新ROOT任务

```
用户: "我想学习双曲空间应用于蛋白质互作用预测"

系统判断:
- 没有现有ROOT任务
- LLM分析：这是一个学习/研究请求
- 决策：create_new_root = True

结果:
→ 创建新ROOT任务
→ 自动分解为COMPOSITE和ATOMIC任务
→ 生成文件结构: results/学习双曲空间.../
```

### 场景2：在现有ROOT下添加子任务

```
用户: "我想学习双曲空间..."
→ 创建ROOT任务: "学习双曲空间应用于蛋白质互作用预测"

用户: "帮我写一个相关的文档"

系统判断:
- 发现现有ROOT任务: "学习双曲空间..."
- LLM分析：用户说"相关的" → 指现有任务
- 决策：add_to_existing = True

结果:
→ 在现有ROOT下添加COMPOSITE任务: "撰写相关文档"
→ parent_id = ROOT任务ID
→ 文件会生成在: results/学习双曲空间.../撰写相关文档/
```

### 场景3：创建完全独立的新ROOT

```
用户: "我想学习双曲空间..."
→ ROOT任务A

用户: "现在我想开发一个新的Web应用"

系统判断:
- 发现现有ROOT任务A
- LLM分析：这是完全不同的新项目
- 决策：create_new_root = True

结果:
→ 创建新的ROOT任务B
→ 两个独立的项目共存
```

### 场景4：普通对话

```
用户: "我想学习双曲空间..."
→ ROOT任务

用户: "这个领域有什么经典论文吗？"

系统判断:
- 发现现有ROOT任务
- LLM分析：这是信息查询，不是任务需求
- 决策：create_new_root = False, add_to_existing = False

结果:
→ 使用普通LLM回复
→ 不创建任何任务
```

## 🔧 技术实现

### 修改的文件

**app/routers/chat_routes.py**

1. **修改入口逻辑** (第75-90行)
   ```python
   workflow_decision = await _should_create_new_workflow(
       request.message, 
       request.session_id, 
       request.context,
       context_messages
   )
   
   if workflow_decision.get("create_new_root"):
       return await _handle_agent_workflow_creation(...)
   elif workflow_decision.get("add_to_existing"):
       return await _handle_add_subtask_to_existing(...)
   ```

2. **新增函数** (第1453-1677行)
   - `_should_create_new_workflow()` - LLM智能判断
   - `_handle_add_subtask_to_existing()` - 添加子任务

3. **标记废弃** (第1680行)
   - `_is_agent_workflow_intent()` - 旧的关键词匹配方法

### 核心优势

1. **完全LLM驱动** ✅
   - 移除所有关键词匹配
   - 使用LLM语义理解
   - 符合科研项目要求

2. **上下文感知** ✅
   - 追踪session中的ROOT任务
   - 理解"相关的"、"这个"等指代词
   - 智能判断用户意图

3. **灵活性** ✅
   - 支持在现有项目下添加子任务
   - 支持创建独立新项目
   - 自动区分任务创建 vs 普通对话

4. **层级文件结构** ✅
   - 子任务自动归属到正确的ROOT目录
   - results/[root]/[composite]/[atomic].md
   - 保持项目组织清晰

## 🧪 测试验证

### 测试用例1：添加子任务

```
1. 用户: "我想学习双曲空间应用于蛋白质互作用预测"
   预期: 创建ROOT任务

2. 用户: "帮我写一个相关的文档"
   预期: 在ROOT任务下添加子任务，不创建新ROOT

3. 检查数据库:
   SELECT * FROM tasks WHERE session_id = '...' AND task_type = 'root'
   应该只有1条ROOT任务
```

### 测试用例2：创建新ROOT

```
1. 用户: "我想学习双曲空间..."
   预期: 创建ROOT任务A

2. 用户: "我还想开发一个Web应用"
   预期: 创建新的独立ROOT任务B

3. 检查数据库:
   应该有2条ROOT任务
```

### 测试用例3：普通对话

```
1. 用户: "我想学习双曲空间..."
   预期: 创建ROOT任务

2. 用户: "这个领域有哪些经典论文？"
   预期: LLM回复，不创建任务

3. 检查数据库:
   只有1条ROOT任务，没有新增
```

## 📊 日志输出

系统会输出详细日志：

```
📋 发现现有ROOT任务: 学习双曲空间应用于蛋白质互作用预测 (ID: 421)
📎 在现有ROOT任务下添加子任务: 帮我写一个相关的文档
✅ 已在现有项目下添加子任务！
```

## 💡 用户体验

**用户看到的响应：**

```
✅ **已在现有项目下添加子任务！**

📋 **父任务**: 学习双曲空间应用于蛋白质互作用预测
📝 **新任务**: 撰写相关文档
🆔 **任务ID**: 435
📊 **状态**: pending

🎯 该任务已加入您的项目计划中。系统会在执行时自动：
• 在 `results/学习双曲空间.../` 目录下创建相应的文件结构
• ATOMIC子任务会生成为 .md 文件

💡 你可以继续补充更多需求，或者说"开始执行任务"来运行它们。
```

## 🎯 总结

✅ **问题已修复**
- 不会随意创建新ROOT任务
- 理解"相关的"等上下文指代
- 完全使用LLM智能路由

✅ **符合要求**
- 移除关键词匹配
- 完全基于LLM语义理解
- 科研项目级别的精准度

✅ **用户友好**
- 自动追踪项目上下文
- 智能判断用户意图
- 清晰的文件组织结构

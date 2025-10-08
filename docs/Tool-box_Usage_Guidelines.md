# Tool-box 使用规范

## 📖 概述

本文档提供 Tool-box 系统的使用规范和最佳实践，确保项目中所有外部操作都通过统一的工具接口进行。

## 🔧 核心原则

### 1. 统一接口原则
- **所有外部API调用**（数据库、网络搜索、内部服务）必须通过 tool-box
- **禁止直接使用** `requests`, `httpx`, `aiohttp` 等HTTP客户端
- **禁止直接调用**外部服务API，必须创建相应的tool-box工具

### 2. 单次初始化原则
- Tool-box **只在 `app/main.py` 中初始化一次**
- 其他模块**只获取已初始化的实例**，不重复初始化
- 使用 `get_smart_router()` 获取路由器实例，内部会检查初始化状态

### 3. 专事专办原则
- 所有数据库查询必须**包含会话过滤条件**
- 使用 `session_id` 实现不同对话的数据隔离
- 系统会自动检测并修复无会话过滤的SQL查询

## 🛠️ 标准使用方式

### 导入和基本使用

```python
from tool_box import execute_tool, list_available_tools, get_smart_router

# ✅ 正确：执行工具
result = await execute_tool("web_search", query="Python教程", max_results=5)

# ✅ 正确：获取工具列表
tools = await list_available_tools()

# ✅ 正确：获取路由器实例
router = await get_smart_router()
```

### 常用工具接口

#### 1. 数据库查询 (`database_query`)
```python
# 自动添加会话过滤的查询
result = await execute_tool("database_query",
    database="data/databases/main/tasks.db", 
    sql="SELECT * FROM tasks WHERE status = 'pending'",  # 系统会自动添加session_id过滤
    operation="query"
)
```

#### 2. 网络搜索 (`web_search`)
```python
result = await execute_tool("web_search",
    query="机器学习最新进展",
    max_results=3
)
```

#### 3. 内部API调用 (`internal_api`)
```python
# 替代直接的httpx调用
result = await execute_tool("internal_api",
    endpoint="/agent/create-workflow",
    method="POST", 
    data={"goal": "学习Python", "context": {}},
    timeout=60.0
)
```

#### 4. 文件操作 (`file_operations`)
```python
result = await execute_tool("file_operations",
    operation="write",
    file_path="/tmp/output.txt",
    content="处理结果"
)
```

## ❌ 禁止的用法

### 直接HTTP调用
```python
# ❌ 禁止：直接使用httpx
async with httpx.AsyncClient() as client:
    response = await client.post("http://api.example.com/data")

# ❌ 禁止：直接使用requests  
response = requests.get("http://api.example.com/data")

# ❌ 禁止：直接使用aiohttp
async with aiohttp.ClientSession() as session:
    response = await session.get("http://api.example.com/data")
```

### 重复初始化
```python
# ❌ 禁止：在业务代码中重复初始化
await initialize_toolbox()  # 只能在main.py中调用

# ❌ 禁止：重复获取router而不检查状态
self.router = await get_smart_router()  # 应该检查是否已存在
```

### 无会话过滤的数据库查询
```python
# ❌ 禁止：无会话过滤的查询（会被系统自动修正）
sql = "SELECT * FROM tasks WHERE status = 'pending'"

# ✅ 正确：让系统自动添加会话过滤，或手动包含session_id
sql = "SELECT * FROM tasks WHERE status = 'pending' AND session_id = 'xxx'"
```

## 🔍 工具注册

### 添加新工具

1. **创建工具实现** (`tool_box/tools_impl/your_tool.py`)：
```python
async def your_tool_handler(**kwargs) -> Dict[str, Any]:
    # 工具实现
    return {"success": True, "result": "..."}

your_tool = {
    "name": "your_tool",
    "description": "工具描述",
    "category": "工具分类",
    "parameters_schema": {...},
    "handler": your_tool_handler,
    "tags": ["tag1", "tag2"],
    "examples": ["示例用法"]
}
```

2. **注册工具** (更新 `tool_box/tools_impl/__init__.py`):
```python
from .your_tool import your_tool
__all__ = [..., "your_tool"]
```

3. **集成到系统** (更新 `tool_box/integration.py`):
```python
from .tools_impl import ..., your_tool

# 在 _register_builtin_tools 方法中添加
register_tool(
    name=your_tool["name"],
    description=your_tool["description"],
    # ... 其他参数
)
```

## 📊 监控和日志

### 工具调用日志
- Tool-box会自动记录所有工具调用
- 包括调用参数、执行时间、结果状态
- SQL查询修正会生成警告日志

### 性能监控
- 使用 `get_cache_stats()` 查看缓存统计
- 监控工具调用频率和响应时间

## 🚨 故障排除

### 常见问题

1. **工具未找到**
   ```python
   # 检查工具是否正确注册
   tools = await list_available_tools()
   print([tool.name for tool in tools])
   ```

2. **初始化失败**
   ```python
   # 检查API密钥配置
   echo $GLM_API_KEY
   ```

3. **SQL查询无结果**
   ```python
   # 检查session_id是否正确传递
   # 查看日志中的SQL修正信息
   ```

## ✅ 检查清单

在提交代码前，请确认：

- [ ] 没有直接的HTTP客户端调用 (`requests`, `httpx`, `aiohttp`)
- [ ] 所有数据库查询都通过tool-box
- [ ] 没有重复的tool-box初始化
- [ ] 新增工具已正确注册
- [ ] 会话过滤逻辑正确实现

## 📝 版本更新

### v1.0.0 (当前版本)
- 基础工具系统
- 统一API接口
- 自动会话过滤
- 内部API调用支持

---

遵循这些规范可以确保系统的统一性、安全性和可维护性。

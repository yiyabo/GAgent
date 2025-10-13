# Tool Box - AI Agent Tools System

Tool Box 是一个为AI Agent提供工具调用能力的MCP兼容系统，支持智能工具发现、路由和缓存。

## 🚀 快速开始

### 安装依赖

```bash
pip install aiohttp  # 用于网页搜索
```

### 基本使用

```python
import asyncio
from tool_box import initialize_toolbox, execute_tool, route_user_request

async def main():
    # 初始化工具箱
    await initialize_toolbox()

    # 直接调用工具
    result = await execute_tool("web_search", query="AI news", max_results=5)
    print(result)

    # 智能路由用户请求
    routing = await route_user_request("帮我搜索最新的AI新闻")
    print(routing)

asyncio.run(main())
```

## 🛠️ 核心组件

### 1. MCP服务器 (server.py)
- 实现MCP协议的服务器
- 支持工具注册和调用
- 提供标准的JSON-RPC接口

### 2. 工具注册系统 (tools.py)
- 统一的工具注册和发现机制
- 支持工具分类和搜索
- 提供工具元数据管理

### 3. 工具实现 (tools_impl/)
- **web_search**: 网页搜索工具
- **file_operations**: 文件操作工具
- **database_query**: 数据库查询工具

### 4. 智能路由器 (router.py)
- 自动分析用户请求
- 智能选择合适工具
- 支持上下文感知路由

### 5. 缓存系统 (cache.py)
- 内存缓存和持久化缓存
- LRU淘汰策略
- 自动过期清理

### 6. 集成层 (integration.py)
- 与现有LLM系统的集成
- 统一的API接口
- 错误处理和监控

## 📚 API参考

### 初始化

```python
from tool_box.integration import initialize_toolbox

# 初始化所有组件
await initialize_toolbox()
```

### 工具调用

```python
from tool_box.integration import execute_tool

# 调用网页搜索工具
result = await execute_tool("web_search", query="AI news", max_results=5)

# 调用文件操作工具
result = await execute_tool("file_operations",
                          operation="read",
                          path="config.json")

# 调用数据库查询工具
result = await execute_tool("database_query",
                          database="data.db",
                          sql="SELECT * FROM users",
                          operation="query")
```

### 智能路由

```python
from tool_box.router import route_user_request

# 智能分析和路由用户请求
result = await route_user_request("帮我搜索AI新闻并保存到文件中")

print(result["selected_tools"])  # 选择的工具
print(result["tool_calls"])      # 工具调用序列
print(result["confidence"])      # 置信度
```

### 缓存管理

```python
from tool_box.cache import get_cache_stats, cleanup_all_caches

# 查看缓存统计
stats = await get_cache_stats()
print(f"缓存命中率: {stats['memory_cache']['hit_rate']:.2%}")

# 清理过期缓存
cleaned = await cleanup_all_caches()
print(f"清理了 {cleaned['total_cleaned']} 个过期条目")
```

## 🔧 工具开发

### 创建新工具

```python
from tool_box.tools import register_tool

# 定义工具处理函数
async def my_custom_tool(param1: str, param2: int = 10) -> dict:
    """自定义工具处理函数"""
    # 实现工具逻辑
    return {"result": f"处理了 {param1}，参数2为 {param2}"}

# 注册工具
register_tool(
    name="my_custom_tool",
    description="我的自定义工具",
    category="custom",
    parameters_schema={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "第一个参数"},
            "param2": {"type": "integer", "description": "第二个参数", "default": 10}
        },
        "required": ["param1"]
    },
    handler=my_custom_tool,
    tags=["custom", "example"],
    examples=["使用自定义工具处理数据"]
)
```

### MCP服务器使用

```python
from tool_box.server import ToolBoxMCPServer

# 创建服务器
server = ToolBoxMCPServer()

# 启动服务器
await server.run_stdio()
```

## 🧪 测试

运行测试套件：

```bash
python test_toolbox.py
```

## 📊 性能优化

### 缓存配置

```python
from tool_box.cache import ToolCache

# 自定义缓存配置
cache = ToolCache(
    max_size=2000,      # 最大缓存条目数
    default_ttl=7200    # 默认TTL 2小时
)
```

### 并发控制

```python
import asyncio
from tool_box.integration import execute_tool

# 并发执行多个工具
async def batch_execute():
    tasks = [
        execute_tool("web_search", query="AI"),
        execute_tool("web_search", query="ML"),
        execute_tool("file_operations", operation="list", path=".")
    ]

    results = await asyncio.gather(*tasks)
    return results
```

## 🔍 监控和调试

### 启用调试日志

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("tool_box")
```

### 查看系统状态

```python
from tool_box.integration import list_available_tools
from tool_box.cache import get_cache_stats

# 查看可用工具
tools = await list_available_tools()
print(f"可用工具数量: {len(tools)}")

# 查看缓存状态
stats = await get_cache_stats()
print(f"缓存性能: {stats}")
```

## 🏗️ 架构特点

1. **模块化设计**: 各组件独立，可单独使用或组合
2. **MCP兼容**: 遵循Model Context Protocol标准
3. **智能路由**: 自动分析请求并选择最佳工具
4. **缓存优化**: 多层缓存提升性能
5. **错误处理**: 完善的异常处理和恢复机制
6. **扩展性**: 易于添加新工具和功能

## 📝 许可证

本项目采用 MIT 许可证。

## 🤝 贡献

欢迎提交Issue和Pull Request来改进Tool Box！

---

**Tool Box** - 让AI Agent更智能的工具系统 🚀
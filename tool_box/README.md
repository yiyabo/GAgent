# Tool Box - AI Agent Tools System

Tool Box AI AgentMCP，

## 🚀 

### 

```bash
pip install aiohttp  # 
```

### 

```python
import asyncio
from tool_box import initialize_toolbox, execute_tool, route_user_request

async def main():
    # 
    await initialize_toolbox()

    # 
    result = await execute_tool("web_search", query="AI news", max_results=5)
    print(result)

    # 
    routing = await route_user_request("AI")
    print(routing)

asyncio.run(main())
```

## 🛠️ 

### 1. MCP (server.py)
- MCP
- 
- JSON-RPC

### 2.  (tools.py)
- 
- 
- 

### 3.  (tools_impl/)
- **web_search**: 
- **file_operations**: 

### 4.  (router.py)
- 
- 
- 

### 5.  (cache.py)
- 
- LRU
- 

### 6.  (integration.py)
- LLM
- API
- 

## 📚 API

### 

```python
from tool_box.integration import initialize_toolbox

# 
await initialize_toolbox()
```

### 

```python
from tool_box.integration import execute_tool

# 
result = await execute_tool("web_search", query="AI news", max_results=5)

# 
result = await execute_tool("file_operations",
                          operation="read",
                          path="config.json")


```

### 

```python
from tool_box.router import route_user_request

# 
result = await route_user_request("AI")

print(result["selected_tools"])  # 
print(result["tool_calls"])      # 
print(result["confidence"])      # 
```

### 

```python
from tool_box.cache import get_cache_stats, cleanup_all_caches

# 
stats = await get_cache_stats()
print(f": {stats['memory_cache']['hit_rate']:.2%}")

# 
cleaned = await cleanup_all_caches()
print(f" {cleaned['total_cleaned']} ")
```

## 🔧 

### 

```python
from tool_box.tools import register_tool

# 
async def my_custom_tool(param1: str, param2: int = 10) -> dict:
    """"""
    # 
    return {"result": f" {param1}，2 {param2}"}

# 
register_tool(
    name="my_custom_tool",
    description="",
    category="custom",
    parameters_schema={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": ""},
            "param2": {"type": "integer", "description": "", "default": 10}
        },
        "required": ["param1"]
    },
    handler=my_custom_tool,
    tags=["custom", "example"],
    examples=[""]
)
```

### MCP

```python
from tool_box.server import ToolBoxMCPServer

# 
server = ToolBoxMCPServer()

# 
await server.run_stdio()
```

## 🧪 

：

```bash
python test_toolbox.py
```

## 📊 

### 

```python
from tool_box.cache import ToolCache

# 
cache = ToolCache(
    max_size=2000,      # 
    default_ttl=7200    # TTL 2
)
```

### 

```python
import asyncio
from tool_box.integration import execute_tool

# 
async def batch_execute():
    tasks = [
        execute_tool("web_search", query="AI"),
        execute_tool("web_search", query="ML"),
        execute_tool("file_operations", operation="list", path=".")
    ]

    results = await asyncio.gather(*tasks)
    return results
```

## 🔍 

### 

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("tool_box")
```

### 

```python
from tool_box.integration import list_available_tools
from tool_box.cache import get_cache_stats

# 
tools = await list_available_tools()
print(f": {len(tools)}")

# 
stats = await get_cache_stats()
print(f": {stats}")
```

## 🏗️ 

1. ****: ，
2. **MCP**: Model Context Protocol
3. ****: 
4. ****: 
5. ****: 
6. ****: 

## 📝 

 MIT 

## 🤝 

IssuePull RequestTool Box！

---

**Tool Box** - AI Agent 🚀
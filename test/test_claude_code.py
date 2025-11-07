"""
Tests for Claude Code integration
"""

import os
import pytest
from pathlib import Path

# 只在有 API key 时运行测试
pytestmark = pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")),
    reason="ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN not set"
)


@pytest.mark.asyncio
async def test_claude_code_simple_execution():
    """Test basic code execution"""
    from tool_box.tools_impl.claude_code import claude_code_handler
    
    result = await claude_code_handler(
        code="print('Hello from Claude Code')\nresult = 2 + 2\nprint(f'2 + 2 = {result}')",
        owner="test-session",
    )
    
    assert result["success"] is True
    assert "owner" in result
    assert result["language"] == "python"


@pytest.mark.asyncio
async def test_claude_code_with_file_access():
    """Test code execution with file access"""
    from tool_box.tools_impl.claude_code import claude_code_handler
    
    # 确保测试数据目录存在
    data_dir = Path("data/code_task")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建测试文件
    test_file = data_dir / "test_data.txt"
    test_file.write_text("Hello from test file\n")
    
    try:
        result = await claude_code_handler(
            code="""
with open('test_data.txt', 'r') as f:
    content = f.read()
print(f'File content: {content}')
""",
            owner="test-session",
            files=["test_data.txt"],
        )
        
        assert result["success"] is True
        assert "uploaded_files" in result
        assert "test_data.txt" in result["uploaded_files"]
        
    finally:
        # 清理测试文件
        if test_file.exists():
            test_file.unlink()


@pytest.mark.asyncio
async def test_claude_code_error_handling():
    """Test error handling for invalid code"""
    from tool_box.tools_impl.claude_code import claude_code_handler
    
    result = await claude_code_handler(
        code="",  # 空代码
        owner="test-session",
    )
    
    # 空代码应该被接受但可能没有输出
    assert "success" in result

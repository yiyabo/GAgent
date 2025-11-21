"""
测试文件上传功能

快速测试上传API是否正常工作
"""

import asyncio
import os
from pathlib import Path

# 测试用例
async def test_upload_routes():
    """测试上传路由是否正确注册"""
    from app.main import app
    
    # 检查路由是否注册
    routes = [route.path for route in app.routes]
    
    upload_routes = [r for r in routes if '/upload' in r]
    
    print("✅ 已注册的上传路由:")
    for route in upload_routes:
        print(f"   - {route}")
    
    assert any('/upload/file' in r for r in routes), "❌ /upload/file 路由未注册"
    assert any('/upload/image' in r for r in routes), "❌ /upload/image 路由未注册"
    
    print("\n✅ 所有上传路由已正确注册")


async def test_upload_directory():
    """测试上传目录是否存在"""
    upload_dir = Path("data/uploads")
    
    assert upload_dir.exists(), f"❌ 上传目录不存在: {upload_dir}"
    assert upload_dir.is_dir(), f"❌ {upload_dir} 不是目录"
    
    gitignore = upload_dir / ".gitignore"
    assert gitignore.exists(), f"❌ .gitignore 文件不存在"
    
    print(f"✅ 上传目录已创建: {upload_dir}")
    print(f"✅ .gitignore 已配置")


async def test_document_reader_tool():
    """测试document_reader工具是否可用"""
    from tool_box.tools_impl import document_reader_tool
    
    assert document_reader_tool is not None, "❌ document_reader_tool 未导入"
    assert "handler" in document_reader_tool, "❌ document_reader_tool 缺少 handler"
    
    print("✅ document_reader 工具已正确加载")


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("文件上传功能测试")
    print("=" * 60)
    print()
    
    try:
        print("1. 测试上传目录...")
        await test_upload_directory()
        print()
        
        print("2. 测试document_reader工具...")
        await test_document_reader_tool()
        print()
        
        print("3. 测试上传路由...")
        await test_upload_routes()
        print()
        
        print("=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        print()
        print("📝 下一步:")
        print("  1. 启动后端: uvicorn app.main:app --reload")
        print("  2. 启动前端: cd web-ui && npm run dev")
        print("  3. 在聊天界面测试上传功能")
        print()
        
    except AssertionError as e:
        print()
        print("=" * 60)
        print(f"❌ 测试失败: {e}")
        print("=" * 60)
        return 1
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ 测试出错: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

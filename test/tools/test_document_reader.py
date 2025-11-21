"""
测试文档读取工具
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tool_box.tools_impl.document_reader import (
    read_pdf,
    read_image,
    analyze_image_with_llm,
    document_reader_handler
)


async def test_read_pdf():
    """测试PDF读取"""
    print("\n" + "="*60)
    print("测试1: 读取PDF文件")
    print("="*60)
    
    # 创建测试PDF（需要有实际的PDF文件）
    test_pdf = "test_sample.pdf"
    
    if not os.path.exists(test_pdf):
        print(f"⚠️  测试PDF文件不存在: {test_pdf}")
        print("请提供一个PDF文件路径进行测试")
        return
    
    result = await read_pdf(test_pdf)
    
    if result["success"]:
        print(f"✅ 成功读取PDF")
        print(f"   文件名: {result['file_name']}")
        print(f"   页数: {result['num_pages']}")
        print(f"   文件大小: {result['file_size']}")
        print(f"   内容长度: {result['content_length']} 字符")
        print(f"\n前500个字符:")
        print(result['content'][:500])
    else:
        print(f"❌ 读取失败: {result['error']}")


async def test_read_image():
    """测试图片读取"""
    print("\n" + "="*60)
    print("测试2: 读取图片文件")
    print("="*60)
    
    # 创建测试图片（需要有实际的图片文件）
    test_image = "test_image.jpg"
    
    if not os.path.exists(test_image):
        print(f"⚠️  测试图片文件不存在: {test_image}")
        print("请提供一个图片文件路径进行测试")
        return
    
    result = await read_image(test_image, use_ocr=False)
    
    if result["success"]:
        print(f"✅ 成功读取图片")
        print(f"   文件名: {result['file_name']}")
        print(f"   尺寸: {result['image_info']['width']}x{result['image_info']['height']}")
        print(f"   格式: {result['image_info']['format']}")
        print(f"   文件大小: {result['file_size']}")
    else:
        print(f"❌ 读取失败: {result['error']}")


async def test_read_image_with_ocr():
    """测试图片OCR识别"""
    print("\n" + "="*60)
    print("测试3: 图片OCR文字识别")
    print("="*60)
    
    test_image = "test_image.jpg"
    
    if not os.path.exists(test_image):
        print(f"⚠️  测试图片文件不存在: {test_image}")
        return
    
    result = await read_image(test_image, use_ocr=True)
    
    if result["success"]:
        print(f"✅ 成功读取图片")
        if result.get("ocr_enabled"):
            print(f"   OCR识别文字:")
            print(f"   {result.get('ocr_text', '无文字')[:200]}")
        else:
            print(f"   OCR未启用: {result.get('ocr_error', '未知原因')}")
    else:
        print(f"❌ 读取失败: {result['error']}")


async def test_analyze_image():
    """测试LLM图片分析"""
    print("\n" + "="*60)
    print("测试4: LLM图片分析")
    print("="*60)
    
    test_image = "test_image.jpg"
    
    if not os.path.exists(test_image):
        print(f"⚠️  测试图片文件不存在: {test_image}")
        return
    
    result = await analyze_image_with_llm(
        test_image,
        prompt="请详细描述这张图片的内容"
    )
    
    if result["success"]:
        print(f"✅ 成功分析图片")
        print(f"   LLM分析结果:")
        print(f"   {result.get('analysis', '无分析结果')}")
    else:
        print(f"❌ 分析失败: {result['error']}")


async def test_handler():
    """测试工具处理器"""
    print("\n" + "="*60)
    print("测试5: 工具处理器接口")
    print("="*60)
    
    # 测试read_pdf操作
    print("\n测试 read_pdf 操作:")
    result = await document_reader_handler(
        operation="read_pdf",
        file_path="test_sample.pdf"
    )
    print(f"结果: {result.get('summary', result.get('error'))}")
    
    # 测试read_image操作
    print("\n测试 read_image 操作:")
    result = await document_reader_handler(
        operation="read_image",
        file_path="test_image.jpg",
        use_ocr=False
    )
    print(f"结果: {result.get('summary', result.get('error'))}")


async def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("文档读取工具测试")
    print("="*60)
    
    print("\n请确保已安装依赖:")
    print("  pip install PyPDF2 Pillow")
    print("  (可选) pip install pytesseract")
    print("")
    
    # 运行测试
    await test_read_pdf()
    await test_read_image()
    await test_read_image_with_ocr()
    await test_analyze_image()
    await test_handler()
    
    print("\n" + "="*60)
    print("✅ 测试完成")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())

"""
文档读取工具使用示例

展示如何使用document_reader工具读取PDF和图片
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tool_box.tools_impl.document_reader import (
    read_pdf,
    read_image,
    analyze_image_with_llm,
    document_reader_handler
)


async def example_1_read_pdf():
    """示例1: 读取PDF文件"""
    print("\n" + "="*60)
    print("示例1: 读取PDF文件")
    print("="*60)
    
    # 替换为你的PDF文件路径
    pdf_path = "your_document.pdf"
    
    result = await read_pdf(pdf_path)
    
    if result["success"]:
        print(f"✅ 成功读取PDF")
        print(f"   文件: {result['file_name']}")
        print(f"   页数: {result['num_pages']}")
        print(f"   大小: {result['file_size']}")
        print(f"\n前300个字符:")
        print(result['content'][:300])
        print("...")
    else:
        print(f"❌ 失败: {result['error']}")


async def example_2_read_image():
    """示例2: 读取图片文件"""
    print("\n" + "="*60)
    print("示例2: 读取图片文件")
    print("="*60)
    
    # 替换为你的图片文件路径
    image_path = "your_image.jpg"
    
    result = await read_image(image_path)
    
    if result["success"]:
        print(f"✅ 成功读取图片")
        print(f"   文件: {result['file_name']}")
        print(f"   尺寸: {result['image_info']['width']}x{result['image_info']['height']}")
        print(f"   格式: {result['image_info']['format']}")
        print(f"   大小: {result['file_size']}")
    else:
        print(f"❌ 失败: {result['error']}")


async def example_3_ocr():
    """示例3: OCR文字识别"""
    print("\n" + "="*60)
    print("示例3: OCR文字识别")
    print("="*60)
    
    # 替换为包含文字的图片路径
    image_path = "text_image.png"
    
    result = await read_image(image_path, use_ocr=True)
    
    if result["success"]:
        print(f"✅ 成功读取图片")
        if result.get("ocr_enabled"):
            print(f"   识别的文字:")
            print(f"   {result.get('ocr_text', '无文字')}")
        else:
            print(f"   OCR未启用: {result.get('ocr_error')}")
    else:
        print(f"❌ 失败: {result['error']}")


async def example_4_llm_analysis():
    """示例4: LLM图片分析"""
    print("\n" + "="*60)
    print("示例4: LLM图片分析")
    print("="*60)
    
    # 替换为你的图片文件路径
    image_path = "your_image.jpg"
    
    result = await analyze_image_with_llm(
        image_path,
        prompt="请详细描述这张图片中的内容，包括主要对象、场景、颜色等。"
    )
    
    if result["success"]:
        print(f"✅ 成功分析图片")
        print(f"   LLM分析结果:")
        print(f"   {result.get('analysis')}")
    else:
        print(f"❌ 失败: {result['error']}")
        if "不支持视觉输入" in result.get('error', ''):
            print("\n💡 提示: 需要使用支持视觉的模型（如qwen3-vl-plus）")


async def example_5_batch_processing():
    """示例5: 批量处理文件"""
    print("\n" + "="*60)
    print("示例5: 批量处理PDF文件")
    print("="*60)
    
    # 替换为包含PDF文件的文件夹路径
    folder_path = Path("pdf_folder")
    
    if not folder_path.exists():
        print(f"⚠️  文件夹不存在: {folder_path}")
        return
    
    pdf_files = list(folder_path.glob("*.pdf"))
    
    if not pdf_files:
        print(f"⚠️  文件夹中没有PDF文件")
        return
    
    print(f"找到 {len(pdf_files)} 个PDF文件")
    
    for pdf_file in pdf_files:
        result = await read_pdf(str(pdf_file))
        
        if result["success"]:
            print(f"✓ {result['file_name']}: {result['num_pages']}页, {result['content_length']}字符")
        else:
            print(f"✗ {pdf_file.name}: {result['error']}")


async def example_6_use_handler():
    """示例6: 使用统一处理器"""
    print("\n" + "="*60)
    print("示例6: 使用统一处理器接口")
    print("="*60)
    
    # 读取PDF
    print("\n1. 读取PDF:")
    result = await document_reader_handler(
        operation="read_pdf",
        file_path="document.pdf"
    )
    print(f"   {result.get('summary', result.get('error'))}")
    
    # 读取图片
    print("\n2. 读取图片:")
    result = await document_reader_handler(
        operation="read_image",
        file_path="image.jpg"
    )
    print(f"   {result.get('summary', result.get('error'))}")
    
    # LLM分析
    print("\n3. LLM分析:")
    result = await document_reader_handler(
        operation="analyze_image",
        file_path="image.jpg",
        prompt="这张图片的主题是什么？"
    )
    print(f"   {result.get('summary', result.get('error'))}")


async def main():
    """主函数"""
    print("\n" + "="*60)
    print("文档读取工具使用示例")
    print("="*60)
    
    print("\n请先安装依赖:")
    print("  pip install PyPDF2 Pillow")
    print("  (可选) pip install pytesseract")
    print("")
    
    # 运行示例
    # 注意: 请根据实际情况修改文件路径
    
    # await example_1_read_pdf()
    # await example_2_read_image()
    # await example_3_ocr()
    # await example_4_llm_analysis()
    # await example_5_batch_processing()
    # await example_6_use_handler()
    
    print("\n💡 提示: 请取消注释上面的示例函数并修改文件路径后运行")
    print("\n" + "="*60)


if __name__ == "__main__":
    asyncio.run(main())

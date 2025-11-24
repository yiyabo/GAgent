"""
Document Reader Tool Implementation

支持读取PDF文档和图片文件，提取文本和描述信息
"""

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .vision_reader import _call_qwen_vision_api

logger = logging.getLogger(__name__)


async def read_pdf(file_path: str) -> Dict[str, Any]:
    """
    读取PDF文件内容
    
    Args:
        file_path: PDF文件路径
        
    Returns:
        包含文本内容、页数等信息的字典
    """
    try:
        import PyPDF2
        
        abs_path = Path(file_path).resolve()
        
        if not abs_path.exists():
            return {
                "success": False,
                "error": f"文件不存在: {file_path}"
            }
        
        if not abs_path.suffix.lower() == '.pdf':
            return {
                "success": False,
                "error": f"不是PDF文件: {file_path}"
            }
        
        # 检查文件大小（限制50MB）
        file_size = abs_path.stat().st_size
        if file_size > 50 * 1024 * 1024:
            return {
                "success": False,
                "error": f"PDF文件过大 ({file_size / 1024 / 1024:.2f}MB > 50MB)"
            }
        
        # 读取PDF
        text_content = []
        metadata = {}
        
        with open(abs_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            
            # 获取元数据
            if pdf_reader.metadata:
                metadata = {
                    "title": pdf_reader.metadata.get('/Title', ''),
                    "author": pdf_reader.metadata.get('/Author', ''),
                    "subject": pdf_reader.metadata.get('/Subject', ''),
                    "creator": pdf_reader.metadata.get('/Creator', ''),
                }
            
            # 提取每页文本
            num_pages = len(pdf_reader.pages)
            for page_num in range(num_pages):
                page = pdf_reader.pages[page_num]
                text = page.extract_text()
                if text.strip():
                    text_content.append(f"--- 第 {page_num + 1} 页 ---\n{text}")
        
        full_text = "\n\n".join(text_content)
        
        return {
            "success": True,
            "file_path": str(abs_path),
            "file_name": abs_path.name,
            "file_size": f"{file_size / 1024:.2f} KB",
            "page_count": num_pages,
            "metadata": metadata,
            "text": full_text,
            "text_length": len(full_text),
            "summary": f"成功读取PDF文件，共{num_pages}页，提取了{len(full_text)}个字符"
        }
        
    except ImportError:
        return {
            "success": False,
            "error": "PyPDF2库未安装，请运行: pip install PyPDF2"
        }
    except Exception as e:
        logger.error(f"读取PDF失败: {e}")
        return {
            "success": False,
            "error": f"读取PDF时出错: {str(e)}"
        }


async def read_image(file_path: str, use_ocr: bool = False) -> Dict[str, Any]:
    """
    读取图片文件
    
    Args:
        file_path: 图片文件路径
        use_ocr: 是否使用OCR提取图片中的文字
        
    Returns:
        包含图片信息和内容的字典
    """
    try:
        from PIL import Image
        
        abs_path = Path(file_path).resolve()
        
        if not abs_path.exists():
            return {
                "success": False,
                "error": f"文件不存在: {file_path}"
            }
        
        # 支持的图片格式
        supported_formats = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff']
        if abs_path.suffix.lower() not in supported_formats:
            return {
                "success": False,
                "error": f"不支持的图片格式: {abs_path.suffix}，支持的格式: {', '.join(supported_formats)}"
            }
        
        # 检查文件大小（限制20MB）
        file_size = abs_path.stat().st_size
        if file_size > 20 * 1024 * 1024:
            return {
                "success": False,
                "error": f"图片文件过大 ({file_size / 1024 / 1024:.2f}MB > 20MB)"
            }
        
        # 读取图片
        img = Image.open(abs_path)
        
        # 获取图片信息
        image_info = {
            "format": img.format,
            "mode": img.mode,
            "size": img.size,
            "width": img.width,
            "height": img.height,
        }
        
        # 转换为base64（用于可能的API调用）
        with open(abs_path, 'rb') as img_file:
            image_base64 = base64.b64encode(img_file.read()).decode('utf-8')
        
        result = {
            "success": True,
            "file_path": str(abs_path),
            "file_name": abs_path.name,
            "file_size": f"{file_size / 1024:.2f} KB",
            "image_info": image_info,
            "base64": image_base64[:100] + "...",  # 只显示前100个字符
            "base64_full": image_base64,  # 完整的base64
            "summary": f"成功读取图片 {abs_path.name}，尺寸: {img.width}x{img.height}，格式: {img.format}"
        }
        
        # OCR文字识别（可选）
        if use_ocr:
            try:
                import pytesseract
                
                # 提取文字
                text = pytesseract.image_to_string(img, lang='chi_sim+eng')
                result["ocr_text"] = text
                result["ocr_enabled"] = True
                result["summary"] += f"\nOCR提取了{len(text)}个字符"
                
            except ImportError:
                result["ocr_enabled"] = False
                result["ocr_error"] = "pytesseract库未安装，无法进行OCR识别"
            except Exception as ocr_error:
                result["ocr_enabled"] = False
                result["ocr_error"] = f"OCR识别失败: {str(ocr_error)}"
        
        return result
        
    except ImportError:
        return {
            "success": False,
            "error": "Pillow库未安装，请运行: pip install Pillow"
        }
    except Exception as e:
        logger.error(f"读取图片失败: {e}")
        return {
            "success": False,
            "error": f"读取图片时出错: {str(e)}"
        }


async def analyze_image_with_llm(file_path: str, prompt: Optional[str] = None) -> Dict[str, Any]:
    """
    使用LLM分析图片内容（需要支持视觉的LLM）
    
    Args:
        file_path: 图片文件路径
        prompt: 分析提示词，默认为"请描述这张图片的内容"
        
    Returns:
        包含LLM分析结果的字典
    """
    try:
        # 首先读取图片
        image_result = await read_image(file_path, use_ocr=False)
        
        if not image_result["success"]:
            return image_result
        
        # 获取base64编码
        image_base64 = image_result["base64_full"]
        
        # 构建分析提示词
        if prompt is None:
            prompt = "请详细描述这张图片的内容，包括主要对象、场景、颜色、布局等信息。"

        # 使用与 vision_reader 相同的 Qwen 视觉接口，构造符合 OpenAI 兼容格式的请求
        try:
            analysis_text = await _call_qwen_vision_api(prompt, image_result["file_path"])

            return {
                "success": True,
                "file_path": image_result["file_path"],
                "file_name": image_result["file_name"],
                "image_info": image_result["image_info"],
                "text": analysis_text,
                "prompt": prompt,
                "summary": f"成功使用LLM分析图片: {image_result['file_name']}"
            }

        except Exception as llm_error:
            return {
                "success": False,
                "error": f"LLM分析失败: {str(llm_error)}",
                "note": "可能是模型不支持视觉输入，或API配置有误"
            }
        
    except Exception as e:
        logger.error(f"LLM图片分析失败: {e}")
        return {
            "success": False,
            "error": f"分析图片时出错: {str(e)}"
        }


async def document_reader_handler(
    operation: str,
    file_path: str,
    use_ocr: bool = False,
    prompt: Optional[str] = None
) -> Dict[str, Any]:
    """
    文档读取工具处理器
    
    Args:
        operation: 操作类型 ('read_pdf', 'read_image', 'analyze_image')
        file_path: 文件路径
        use_ocr: 是否使用OCR（仅用于read_image）
        prompt: 分析提示词（仅用于analyze_image）
        
    Returns:
        操作结果字典
    """
    try:
        if operation == "read_pdf":
            return await read_pdf(file_path)
        
        elif operation == "read_image":
            return await read_image(file_path, use_ocr=use_ocr)
        
        elif operation == "analyze_image":
            return await analyze_image_with_llm(file_path, prompt=prompt)
        
        else:
            return {
                "success": False,
                "error": f"不支持的操作: {operation}，支持的操作: read_pdf, read_image, analyze_image"
            }
            
    except Exception as e:
        logger.error(f"文档读取处理失败: {e}")
        return {
            "success": False,
            "error": f"处理请求时出错: {str(e)}"
        }


# 工具定义
document_reader_tool = {
    "name": "document_reader",
    "description": "读取和分析文档（PDF）和图片文件，支持文本提取、OCR识别和LLM图片分析",
    "category": "document_processing",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read_pdf", "read_image", "analyze_image"],
                "description": "操作类型：read_pdf(读取PDF), read_image(读取图片), analyze_image(LLM分析图片)"
            },
            "file_path": {
                "type": "string",
                "description": "文件路径（绝对路径或相对路径）"
            },
            "use_ocr": {
                "type": "boolean",
                "description": "是否使用OCR识别图片中的文字（仅用于read_image操作）",
                "default": False
            },
            "prompt": {
                "type": "string",
                "description": "图片分析提示词（仅用于analyze_image操作）",
                "default": None
            }
        },
        "required": ["operation", "file_path"]
    },
    "handler": document_reader_handler,
    "tags": ["document", "pdf", "image", "ocr", "vision"],
    "examples": [
        "读取PDF文件并提取文本内容",
        "识别图片中的文字信息",
        "使用AI分析图片内容"
    ]
}

"""
File Upload Routes

处理文件和图片上传，存储到按会话分组的目录中
"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from . import register_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["upload"])

# 上传配置
UPLOAD_BASE_DIR = "data/uploads"
ALLOWED_FILE_TYPES = {
    "pdf": ["application/pdf"],
    "image": [
        "image/jpeg",
        "image/jpg", 
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
    ],
}
MAX_FILE_SIZE = {
    "pdf": 50 * 1024 * 1024,  # 50MB
    "image": 20 * 1024 * 1024,  # 20MB
}

register_router(
    namespace="upload",
    version="v1",
    path="/upload",
    router=router,
    tags=["upload"],
    description="文件和图片上传服务",
)


class UploadResponse(BaseModel):
    """上传响应"""

    success: bool
    file_path: str
    file_name: str
    original_name: str
    file_size: str
    file_type: str
    uploaded_at: str
    session_id: Optional[str] = None


def _get_file_category(content_type: str) -> Optional[str]:
    """根据MIME类型判断文件类别"""
    for category, types in ALLOWED_FILE_TYPES.items():
        if content_type in types:
            return category
    return None


def _validate_file(file: UploadFile, category: Optional[str] = None) -> tuple[bool, str, str]:
    """
    验证文件类型和大小
    
    Returns:
        (is_valid, error_message, detected_category)
    """
    content_type = file.content_type or ""
    
    # 检测文件类别
    detected_category = _get_file_category(content_type)
    
    if not detected_category:
        return False, f"不支持的文件类型: {content_type}", ""
    
    # 如果指定了类别，检查是否匹配
    if category and detected_category != category:
        return False, f"文件类型不匹配，期望 {category}，实际 {detected_category}", ""
    
    # 检查文件大小（需要读取文件）
    # 注意：这里只是初步检查，实际大小在保存时再次验证
    max_size = MAX_FILE_SIZE.get(detected_category, 10 * 1024 * 1024)
    
    return True, "", detected_category


def _sanitize_filename(filename: str) -> str:
    """清理文件名，移除危险字符"""
    # 移除路径分隔符和特殊字符
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
    sanitized = "".join(c if c in safe_chars else "_" for c in filename)
    
    # 限制长度
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:190] + ext
    
    return sanitized


def _get_session_upload_dir(session_id: str) -> Path:
    """获取会话的上传目录"""
    base_path = Path(UPLOAD_BASE_DIR)
    session_dir = base_path / f"session-{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


async def _save_upload_file(
    file: UploadFile,
    session_id: str,
    category: str,
) -> Dict[str, Any]:
    """
    保存上传的文件
    
    Returns:
        文件信息字典
    """
    # 生成唯一文件名
    file_id = uuid.uuid4().hex[:12]
    original_name = file.filename or "unknown"
    safe_name = _sanitize_filename(original_name)
    
    # 构建文件路径
    session_dir = _get_session_upload_dir(session_id)
    file_path = session_dir / f"{file_id}_{safe_name}"
    
    # 保存文件并检查大小
    file_size = 0
    max_size = MAX_FILE_SIZE.get(category, 10 * 1024 * 1024)
    
    try:
        with open(file_path, "wb") as f:
            while chunk := await file.read(8192):  # 8KB chunks
                file_size += len(chunk)
                if file_size > max_size:
                    # 删除已写入的文件
                    f.close()
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大，最大允许 {max_size / 1024 / 1024:.1f}MB",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存文件失败: {e}")
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")
    
    # 格式化文件大小
    if file_size < 1024:
        size_str = f"{file_size} B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.2f} KB"
    else:
        size_str = f"{file_size / 1024 / 1024:.2f} MB"
    
    return {
        "file_id": file_id,
        "file_path": str(file_path),
        "file_name": f"{file_id}_{safe_name}",
        "original_name": original_name,
        "file_size": size_str,
        "file_size_bytes": file_size,
        "file_type": file.content_type,
        "category": category,
        "uploaded_at": datetime.now().isoformat(),
        "session_id": session_id,
    }


@router.post("/file", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
) -> UploadResponse:
    """
    上传文件（PDF等）
    
    Args:
        file: 上传的文件
        session_id: 会话ID（必需，用于隔离文件）
    
    Returns:
        上传结果信息
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    
    # 验证文件
    is_valid, error_msg, category = _validate_file(file, category="pdf")
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    
    logger.info(f"上传文件: {file.filename}, session: {session_id}, 类型: {category}")
    
    # 保存文件
    try:
        file_info = await _save_upload_file(file, session_id, category)
        
        return UploadResponse(
            success=True,
            file_path=file_info["file_path"],
            file_name=file_info["file_name"],
            original_name=file_info["original_name"],
            file_size=file_info["file_size"],
            file_type=file_info["file_type"],
            uploaded_at=file_info["uploaded_at"],
            session_id=session_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.post("/image", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    session_id: str = Form(...),
) -> UploadResponse:
    """
    上传图片
    
    Args:
        file: 上传的图片文件
        session_id: 会话ID（必需，用于隔离文件）
    
    Returns:
        上传结果信息
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    
    # 验证文件
    is_valid, error_msg, category = _validate_file(file, category="image")
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    
    logger.info(f"上传图片: {file.filename}, session: {session_id}, 类型: {category}")
    
    # 保存文件
    try:
        file_info = await _save_upload_file(file, session_id, category)
        
        return UploadResponse(
            success=True,
            file_path=file_info["file_path"],
            file_name=file_info["file_name"],
            original_name=file_info["original_name"],
            file_size=file_info["file_size"],
            file_type=file_info["file_type"],
            uploaded_at=file_info["uploaded_at"],
            session_id=session_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传图片失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.delete("/{file_id}")
async def delete_file(file_id: str, session_id: str) -> Dict[str, Any]:
    """
    删除上传的文件
    
    Args:
        file_id: 文件ID
        session_id: 会话ID（用于权限验证）
    
    Returns:
        删除结果
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    
    # 获取会话目录
    session_dir = _get_session_upload_dir(session_id)
    
    # 查找文件
    found_file = None
    for file_path in session_dir.glob(f"{file_id}_*"):
        found_file = file_path
        break
    
    if not found_file:
        raise HTTPException(status_code=404, detail="文件不存在或无权访问")
    
    # 删除文件
    try:
        found_file.unlink()
        logger.info(f"删除文件: {found_file}, session: {session_id}")
        return {"success": True, "message": "文件已删除"}
    except Exception as e:
        logger.error(f"删除文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@router.get("/list")
async def list_files(session_id: str) -> Dict[str, Any]:
    """
    列出会话的所有上传文件
    
    Args:
        session_id: 会话ID
    
    Returns:
        文件列表
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    
    session_dir = _get_session_upload_dir(session_id)
    
    files = []
    for file_path in sorted(session_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if file_path.name == ".gitignore":
            continue
        
        # 解析文件名
        file_name = file_path.name
        parts = file_name.split("_", 1)
        file_id = parts[0] if len(parts) > 0 else ""
        original_name = parts[1] if len(parts) > 1 else file_name
        
        # 获取文件信息
        stat = file_path.stat()
        file_size = stat.st_size
        
        if file_size < 1024:
            size_str = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size / 1024 / 1024:.2f} MB"
        
        files.append({
            "file_id": file_id,
            "file_path": str(file_path),
            "file_name": file_name,
            "original_name": original_name,
            "file_size": size_str,
            "uploaded_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    
    return {
        "success": True,
        "files": files,
        "total": len(files),
        "session_id": session_id,
    }

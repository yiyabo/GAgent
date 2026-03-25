"""
File Upload Routes

fileupload, sessionmedium
"""

import logging
import os
import shutil
import tarfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from . import register_router
from ..database import get_db
from ..services.request_principal import ensure_owner_access
from ..services.upload_storage import ensure_session_dir

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_MIME_TYPES = {
    "document": [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/rtf",
    ],
    "image": [
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
    ],
    "archive": [
        "application/zip",
        "application/x-zip-compressed",
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
    ],
    "data": [
        "application/x-hdf",
        "application/x-hdf5",
        "chemical/x-pdb",
        "chemical/pdb",
        "application/dicom",
        "application/dicom+json",
    ],
    "bioinformatics": [
        "text/plain",  # FASTA, FASTQ are often detected as plain text
        "application/octet-stream",  # Binary bio files
        "text/x-fasta",
        "text/x-fastq",
        "text/x-gff",
        "text/x-gtf",
        "text/x-vcf",
        "text/x-sam",
        "application/x-bam",
        "text/x-bed",
    ],
}

ALLOWED_EXTENSION_CATEGORIES = {
    ".pdf": "document",
    ".doc": "document",
    ".docx": "document",
    ".txt": "document",
    ".md": "document",
    ".rtf": "document",
    ".csv": "document",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".webp": "image",
    ".bmp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".zip": "archive",
    ".tar": "archive",
    ".tar.gz": "archive",
    ".tgz": "archive",
    ".tar.bz2": "archive",
    ".tbz": "archive",
    ".tbz2": "archive",
    ".gz": "archive",
    ".h5": "data",
    ".hdf5": "data",
    ".hdf": "data",
    ".hd5": "data",
    ".pdb": "data",
    ".dcm": "data",
    ".nii": "data",
    ".nii.gz": "data",
    ".npz": "data",
    ".npy": "data",
    # Bioinformatics file formats
    ".fasta": "bioinformatics",
    ".fa": "bioinformatics",
    ".fna": "bioinformatics",
    ".faa": "bioinformatics",
    ".ffn": "bioinformatics",
    ".frn": "bioinformatics",
    ".fastq": "bioinformatics",
    ".fq": "bioinformatics",
    ".fastq.gz": "bioinformatics",
    ".fq.gz": "bioinformatics",
    ".gff": "bioinformatics",
    ".gff3": "bioinformatics",
    ".gtf": "bioinformatics",
    ".vcf": "bioinformatics",
    ".vcf.gz": "bioinformatics",
    ".sam": "bioinformatics",
    ".bam": "bioinformatics",
    ".bed": "bioinformatics",
    ".bed.gz": "bioinformatics",
    ".genbank": "bioinformatics",
    ".gb": "bioinformatics",
    ".gbk": "bioinformatics",
    ".embl": "bioinformatics",
    ".phy": "bioinformatics",
    ".phylip": "bioinformatics",
    ".nwk": "bioinformatics",
    ".newick": "bioinformatics",
    ".aln": "bioinformatics",
    ".clustal": "bioinformatics",
}

DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10GB
MAX_FILE_SIZE = {
    "document": DEFAULT_MAX_FILE_SIZE,
    "image": DEFAULT_MAX_FILE_SIZE,
    "data": None,  # allow large data files
    "archive": None,  # allow large archives during testing
    "bioinformatics": None,  # allow large sequencing files (FASTQ can be huge)
}

UPLOAD_SUBDIR = "uploads"
EXTRACT_SUBDIR = "extracted"

register_router(
    namespace="upload",
    version="v1",
    path="/upload",
    router=router,
    tags=["upload"],
    description="fileuploadservice",
)


class UploadResponse(BaseModel):
    """upload"""

    success: bool
    file_path: str
    file_name: str
    original_name: str
    file_size: str
    file_type: str
    uploaded_at: str
    category: Optional[str] = None
    is_archive: Optional[bool] = None
    extracted_path: Optional[str] = None
    extracted_files: Optional[int] = None
    session_id: Optional[str] = None


SORTED_ALLOWED_EXTENSIONS = sorted(
    ALLOWED_EXTENSION_CATEGORIES.keys(),
    key=len,
    reverse=True,
)


def _normalize_content_type(content_type: str) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def _get_file_category(content_type: str, filename: str) -> Optional[str]:
    """fileMIMEtypefile"""
    lowered_name = (filename or "").lower()
    for ext in SORTED_ALLOWED_EXTENSIONS:
        if lowered_name.endswith(ext):
            return ALLOWED_EXTENSION_CATEGORIES[ext]

    normalized_type = _normalize_content_type(content_type)
    for category, types in ALLOWED_MIME_TYPES.items():
        if normalized_type in types:
            return category

    return None


def _validate_file(file: UploadFile, category: Optional[str] = None) -> tuple[bool, str, str]:
    """
    filetype

    Returns:
        (is_valid, error_message, detected_category)
    """
    content_type = _normalize_content_type(file.content_type or "")

    detected_category = _get_file_category(content_type, file.filename or "")

    if not detected_category:
        return False, f"unsupported file type: {content_type or 'unknown'}", ""

    if category and detected_category != category:
        return False, f"filetype,  {category},  {detected_category}", ""

    return True, "", detected_category


def _sanitize_filename(filename: str) -> str:
    """file, """
    safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
    sanitized = "".join(c if c in safe_chars else "_" for c in filename)

    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:190] + ext

    return sanitized


def _get_session_upload_dir(session_id: str) -> Path:
    """getsessionupload"""
    session_dir = ensure_session_dir(session_id)
    upload_dir = session_dir / UPLOAD_SUBDIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _ensure_session_access(session_id: str, request: Request) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT owner_id FROM chat_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    ensure_owner_access(request, row["owner_id"], detail="session owner mismatch")


def _resolve_extract_dir(session_id: str, file_id: str, safe_name: str) -> Path:
    session_dir = ensure_session_dir(session_id)
    extract_root = session_dir / EXTRACT_SUBDIR
    extract_root.mkdir(parents=True, exist_ok=True)
    stem = Path(safe_name).stem
    return extract_root / f"{file_id}_{stem}"


def _is_within_directory(base_dir: Path, target_path: Path) -> bool:
    base_dir = base_dir.resolve()
    target_path = target_path.resolve()
    return os.path.commonpath([str(base_dir), str(target_path)]) == str(base_dir)


def _safe_extract_zip(zip_file: zipfile.ZipFile, dest_dir: Path) -> int:
    count = 0
    for member in zip_file.infolist():
        member_path = dest_dir / member.filename
        if not _is_within_directory(dest_dir, member_path):
            raise HTTPException(status_code=400, detail="Archive entry escapes destination directory")
        if not member.is_dir():
            count += 1
    zip_file.extractall(dest_dir)
    return count


def _safe_extract_tar(tar_file: tarfile.TarFile, dest_dir: Path) -> int:
    count = 0
    for member in tar_file.getmembers():
        if member.islnk() or member.issym():
            raise HTTPException(status_code=400, detail="Archive links are not allowed for security reasons")
        member_path = dest_dir / member.name
        if not _is_within_directory(dest_dir, member_path):
            raise HTTPException(status_code=400, detail="Archive entry escapes destination directory")
        if member.isfile():
            count += 1
    tar_file.extractall(dest_dir)
    return count


def _extract_archive(file_path: Path, dest_dir: Path) -> int:
    if zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path) as zip_file:
            return _safe_extract_zip(zip_file, dest_dir)

    if tarfile.is_tarfile(file_path):
        with tarfile.open(file_path) as tar_file:
            return _safe_extract_tar(tar_file, dest_dir)

    raise HTTPException(status_code=400, detail="Unsupported archive format. Only .zip and .tar are allowed")


def _get_max_size(category: str) -> Optional[int]:
    return MAX_FILE_SIZE.get(category, DEFAULT_MAX_FILE_SIZE)


async def _save_upload_file(
    file: UploadFile,
    session_id: str,
    category: str,
) -> Dict[str, Any]:
    """
    saveuploadfile

    Returns:
        file
    """
    file_id = uuid.uuid4().hex[:12]
    original_name = file.filename or "unknown"
    safe_name = _sanitize_filename(original_name)

    session_dir = _get_session_upload_dir(session_id)
    file_path = session_dir / f"{file_id}_{safe_name}"

    file_size = 0
    max_size = _get_max_size(category)

    try:
        with open(file_path, "wb") as f:
            while chunk := await file.read(8192):  # 8KB chunks
                file_size += len(chunk)
                if max_size is not None and file_size > max_size:
                    f.close()
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"file,  {max_size / 1024 / 1024:.1f}MB",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"savefilefailed: {e}")
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"savefilefailed: {str(e)}")

    extracted_path = None
    extracted_files = None

    if category == "archive":
        extract_dir = _resolve_extract_dir(session_id, file_id, safe_name)
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            extracted_files = _extract_archive(file_path, extract_dir)
            extracted_path = str(extract_dir)
        except HTTPException:
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise
        except Exception as e:
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.error(f"failed: {e}")
            raise HTTPException(status_code=500, detail=f"failed: {str(e)}")

    if file_size < 1024:
        size_str = f"{file_size} B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.2f} KB"
    else:
        size_str = f"{file_size / 1024 / 1024:.2f} MB"

    file_type = file.content_type or "application/octet-stream"

    return {
        "file_id": file_id,
        "file_path": str(file_path),
        "file_name": f"{file_id}_{safe_name}",
        "original_name": original_name,
        "file_size": size_str,
        "file_size_bytes": file_size,
        "file_type": file_type,
        "category": category,
        "is_archive": category == "archive",
        "extracted_path": extracted_path,
        "extracted_files": extracted_files,
        "uploaded_at": datetime.now().isoformat(),
        "session_id": session_id,
    }


@router.post("/file", response_model=UploadResponse)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(...),
) -> UploadResponse:
    """
    uploadfile

    Args:
        file: uploadfile
        session_id: sessionID(, file)

    Returns:
        uploadresult
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id ")
    _ensure_session_access(session_id, request)

    is_valid, error_msg, category = _validate_file(file)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    logger.info(f"uploadfile: {file.filename}, session: {session_id}, type: {category}")

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
            category=file_info.get("category"),
            is_archive=file_info.get("is_archive"),
            extracted_path=file_info.get("extracted_path"),
            extracted_files=file_info.get("extracted_files"),
            session_id=session_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"uploadfilefailed: {e}")
        raise HTTPException(status_code=500, detail=f"upload failed: {str(e)}")


@router.post("/image", response_model=UploadResponse)
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(...),
) -> UploadResponse:
    """
    upload()

    Args:
        file: uploadfile
        session_id: sessionID(, file)

    Returns:
        uploadresult
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id ")
    _ensure_session_access(session_id, request)

    is_valid, error_msg, category = _validate_file(file, category="image")
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    logger.info(f"upload: {file.filename}, session: {session_id}, type: {category}")

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
            category=file_info.get("category"),
            is_archive=file_info.get("is_archive"),
            extracted_path=file_info.get("extracted_path"),
            extracted_files=file_info.get("extracted_files"),
            session_id=session_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"upload failed: {str(e)}")


@router.delete("/{file_id}")
async def delete_file(file_id: str, session_id: str, request: Request) -> Dict[str, Any]:
    """
    Delete an uploaded file by file ID.

    Args:
        file_id: File ID.
        session_id: Session ID.

    Returns:
        Deletion result payload.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")
    _ensure_session_access(session_id, request)

    session_dir = _get_session_upload_dir(session_id)

    found_file = None
    for file_path in session_dir.glob(f"{file_id}_*"):
        found_file = file_path
        break

    if not found_file:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        found_file.unlink()
        logger.info(f"Deleted file: {found_file}, session: {session_id}")
        return {"success": True, "message": "File deleted"}
    except Exception as e:
        logger.error(f"Delete file failed: {e}")
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")


@router.get("/list")
async def list_files(session_id: str, request: Request) -> Dict[str, Any]:
    """
    List uploaded files for a session.

    Args:
        session_id: Session ID.

    Returns:
        File list payload.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")
    _ensure_session_access(session_id, request)

    session_dir = _get_session_upload_dir(session_id)

    files = []
    for file_path in sorted(session_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if file_path.name == ".gitignore":
            continue

        file_name = file_path.name
        parts = file_name.split("_", 1)
        file_id = parts[0] if len(parts) > 0 else ""
        original_name = parts[1] if len(parts) > 1 else file_name

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

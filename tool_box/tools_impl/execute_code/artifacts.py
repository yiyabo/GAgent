"""Bounded file-change observations for the supplied kernel working directory.

Inputs and kernel internals are not reported as produced artifacts. A change
observation is not acceptance; format/content checking remains with the caller.
"""
from pathlib import Path
import os
from typing import Any
from app.services.plans.output_spec import ALLOWED_EXTENSIONS

MAX_FILES=500
_EXCLUDED={'scratch','.git','__pycache__','.venv','node_modules'}


def snapshot(work_dir:str)->dict[str,tuple[int,int]]:
    if not work_dir:return {}
    root=Path(work_dir).resolve()
    if not root.is_dir():return {}
    out={}
    visited=0
    for directory,dirs,files in os.walk(root):
        visited+=1
        if visited>1500:return out
        dirs[:]=sorted(d for d in dirs if d not in _EXCLUDED and not d.startswith('.'))
        for name in sorted(files):
            visited+=1
            if visited>1500:return out
            path=Path(directory)/name
            if path.is_symlink() or path.suffix.lower() not in ALLOWED_EXTENSIONS:continue
            try:
                stat=path.stat()
                out[str(path)]=(stat.st_size,stat.st_mtime_ns)
            except OSError:continue
            if len(out)>=MAX_FILES:return out
    return out


def _kernel_paths(listed:Any,work_dir:str)->list[str]:
    """The kernel's own list as absolute paths.

    The kernel reports session-relative paths for every file the cell wrote, whatever its extension.
    Downstream readers of ``produced_files`` take a relative entry to be rooted at ``/``, so a relative
    list never validates: it has to be absolute here.
    """
    if not isinstance(listed,list) or not work_dir:return []
    root=Path(work_dir).resolve()
    out=[]
    for item in listed:
        if not isinstance(item,str) or not item.strip():continue
        path=Path(item)
        out.append(str(path if path.is_absolute() else Path(os.path.normpath(root/path))))
    return out


def observe(result:dict[str,Any],before:dict,work_dir:str)->dict[str,Any]:
    """Report every file the cell changed, as absolute paths.

    The snapshot diff is clock-independent but only sees ``ALLOWED_EXTENSIONS``; the kernel's list sees every
    extension (``.faa``, ``.fna``, ``.bai``, ``.gff3`` ...). Both are kept: replacing the second by the first
    hid bioinformatics outputs from delivery acceptance whenever one allowed file changed beside them.
    """
    after=snapshot(work_dir)
    changed=[path for path,stamp in after.items() if stamp[0]>0 and before.get(path)!=stamp]
    merged=list(dict.fromkeys([*changed,*_kernel_paths(result.get('produced_files'),work_dir)]))
    if merged:
        result=dict(result)
        result['produced_files']=merged[:80]
        result['artifact_observation']='working_directory_file_changes'
    return result

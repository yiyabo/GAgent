"""Resolve recalled relative artifact links in their source session, without writes."""
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs

_LINK = re.compile(r'\[[^\]]*\]\(<?([^\s)>]+)>?\)')
_ROOTS = {'workspace','raw_files','results','deliverables','artifacts','code'}


def artifact_references(session_id, content):
    from app.services.path_router import get_path_router
    references=[];seen=set();root=None
    if len(content)>32768:content=content[:16384]+'\n'+content[-16384:]
    for raw in _LINK.findall(content):
        parsed=urlsplit(raw)
        if parsed.scheme or parsed.netloc:
            continue
        relative=unquote(parsed.path)
        endpoint=f'/artifacts/sessions/{session_id}/file'
        if relative==endpoint:
            relative=parse_qs(parsed.query).get('path',[''])[0]
        candidate=Path(relative)
        if candidate.is_absolute() or not candidate.parts or candidate.parts[0] not in _ROOTS or '..' in candidate.parts:
            continue
        if relative in seen or len(relative)>1000:
            continue
        if root is None:
            root=get_path_router().get_session_dir(session_id,create=False).resolve()
        try:
            path=(root/candidate).resolve()
            path.relative_to(root)
            present=path.exists()
        except (OSError,ValueError,RuntimeError):
            continue
        seen.add(relative)
        references.append({'path':relative,'source_path':str(path),'exists':present,'version_status':'untracked'})
        if len(references)==4:
            break
    return references

"""Content revisions for mutable session image references, without rewriting history."""

import hashlib
import re
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from app.services.upload_storage import get_session_root_dir

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
IMAGE_LINK = re.compile(r"(!\[[^\]\n]*\]\()([^\n)]+)(\))")


def revision(path: str | Path, session_id: str | None) -> str | None:
    if not session_id:
        return None
    try:
        root = get_session_root_dir(str(session_id)).resolve()
        candidate = Path(str(path))
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
        target.relative_to(root)
        if target.suffix.lower() not in IMAGE_EXTENSIONS or not target.is_file():
            return None
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def version_inline_images(text: str | None, session_id: str | None) -> str:
    """Only current reply's local, existing images get a stable content token."""
    cache = {}

    def replace(match):
        src = match[2].strip()
        try:
            url = urlsplit(src)
            if url.scheme or url.netloc:
                return match[0]
            pairs = parse_qsl(url.query, keep_blank_values=True)
            path = unquote(url.path)
            if "/artifacts/sessions/" in path:
                parts = path.split("/")
                index = parts.index("sessions")
                if unquote(parts[index + 1]) != str(session_id):
                    return match[0]
                path = dict(pairs).get("path", "")
            if path not in cache:
                cache[path] = revision(path, session_id)
            token = cache[path]
            if not token:
                return match[0]
            query = urlencode([*(item for item in pairs if item[0] != "v"), ("v", token)])
            return match[1] + urlunsplit((url.scheme, url.netloc, url.path, query, url.fragment)) + match[3]
        except (ValueError, IndexError):
            return match[0]

    output, fence = [], None
    for line in (text or "").splitlines(keepends=True):
        stripped = line.lstrip()
        marker = "```" if stripped.startswith("```") else "~~~" if stripped.startswith("~~~") else None
        if marker:
            fence = None if fence == marker else marker if fence is None else fence
            output.append(line)
        else:
            output.append(line if fence else IMAGE_LINK.sub(replace, line))
    return "".join(output)

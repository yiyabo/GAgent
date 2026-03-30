from __future__ import annotations

from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence


def _workspace_root() -> Path:
    try:
        return Path.cwd().resolve()
    except Exception:
        return Path.cwd()


def normalize_path_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("\\", "/")


def canonicalize_subject_ref(
    value: Any,
    *,
    workspace_root: Optional[Path] = None,
) -> str:
    text = normalize_path_text(value)
    if not text or "://" in text:
        return text
    root = workspace_root or _workspace_root()
    try:
        candidate = Path(text).expanduser()
        if candidate.is_absolute():
            return str(candidate.resolve(strict=False))
        return str((root / candidate).resolve(strict=False))
    except Exception:
        return text


def _relative_to_workspace(
    value: str,
    *,
    workspace_root: Optional[Path] = None,
) -> str:
    text = normalize_path_text(value)
    if not text:
        return ""
    root = workspace_root or _workspace_root()
    try:
        candidate = Path(text).expanduser().resolve(strict=False)
        return str(candidate.relative_to(root)).replace("\\", "/")
    except Exception:
        return ""


def build_subject_aliases(
    *values: Any,
    workspace_root: Optional[Path] = None,
) -> List[str]:
    aliases: List[str] = []
    seen = set()

    def _add(item: Any) -> None:
        text = normalize_path_text(item)
        if not text or text in seen:
            return
        seen.add(text)
        aliases.append(text)

    for value in values:
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                _add(nested)
                canonical = canonicalize_subject_ref(nested, workspace_root=workspace_root)
                _add(canonical)
                _add(_relative_to_workspace(canonical, workspace_root=workspace_root))
            continue

        _add(value)
        canonical = canonicalize_subject_ref(value, workspace_root=workspace_root)
        _add(canonical)
        _add(_relative_to_workspace(canonical, workspace_root=workspace_root))

    return aliases


def subject_aliases(subject: Optional[Mapping[str, Any]]) -> List[str]:
    if not isinstance(subject, Mapping):
        return []
    return build_subject_aliases(
        subject.get("aliases"),
        subject.get("canonical_ref"),
        subject.get("display_ref"),
    )


def subject_identity_matches(
    subject: Optional[Mapping[str, Any]],
    *,
    candidate_ref: Any = None,
    candidate_display_ref: Any = None,
    candidate_aliases: Optional[Sequence[Any]] = None,
) -> bool:
    left = set(subject_aliases(subject))
    if not left:
        return False
    right = set(
        build_subject_aliases(
            candidate_aliases,
            candidate_ref,
            candidate_display_ref,
        )
    )
    return bool(left & right)


def normalize_tool_path(
    value: Any,
    *,
    active_subject: Optional[Mapping[str, Any]] = None,
) -> str:
    text = normalize_path_text(value)
    if not text:
        return ""
    if subject_identity_matches(active_subject, candidate_ref=text):
        canonical = canonicalize_subject_ref(
            (active_subject or {}).get("canonical_ref") or (active_subject or {}).get("display_ref")
        )
        if canonical:
            return canonical
    if text.startswith("/"):
        stripped = text.lstrip("/")
        if stripped and stripped != text and subject_identity_matches(active_subject, candidate_ref=stripped):
            canonical = canonicalize_subject_ref(
                (active_subject or {}).get("canonical_ref") or (active_subject or {}).get("display_ref")
            )
            if canonical:
                return canonical
    return canonicalize_subject_ref(text)

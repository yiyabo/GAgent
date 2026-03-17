from __future__ import annotations

import inspect
from typing import Any, Callable, Dict


def prepare_handler_kwargs(handler: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Drop unsupported kwargs before calling a tool handler.

    Many execution paths attach contextual metadata such as ``session_id``.
    Handlers that do not explicitly accept those fields should still execute
    cleanly instead of failing with unexpected-keyword errors.
    """

    safe_kwargs = dict(kwargs or {})
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return safe_kwargs

    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return safe_kwargs

    allowed = {
        name
        for name, param in signature.parameters.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in safe_kwargs.items() if key in allowed}

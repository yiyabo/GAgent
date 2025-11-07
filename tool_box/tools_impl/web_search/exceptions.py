from typing import Any, Dict, Optional


class WebSearchError(Exception):
    """统一的 Web Search 错误类型"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.provider = provider
        self.meta = meta or {}
        super().__init__(message)

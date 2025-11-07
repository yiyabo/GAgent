from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class WebSearchResult:
    query: str
    provider: str
    response: str
    results: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    raw: Any = None
    meta: Dict[str, Any] = field(default_factory=dict)

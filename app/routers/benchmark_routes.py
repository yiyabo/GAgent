"""
基准测试相关API端点

包含系统性能基准测试和配置比较功能。
"""

from fastapi import APIRouter, HTTPException, Body
from typing import Any, Dict

from ..services.evaluation.benchmark import run_benchmark
from ..utils.route_helpers import parse_int

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


@router.post("")
def benchmark_api(payload: Dict[str, Any] = Body(...)):
    """Run benchmark: generate reports under different configs and evaluate.
    Body:
      - topic: str
      - configs: List[str] like ["base,use_context=False","ctx,use_context=True,max_chars=3000"]
      - sections: int (default 5)
    """
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid payload")
        topic = payload.get("topic")
        configs = payload.get("configs")
        sections = payload.get("sections", 5)
        if not isinstance(topic, str) or not topic.strip():
            raise HTTPException(status_code=400, detail="topic is required")
        if not isinstance(configs, list) or not configs:
            raise HTTPException(status_code=400, detail="configs must be a non-empty list")
        try:
            sections = int(sections)
        except (ValueError, TypeError):
            sections = 5

        out = run_benchmark(topic.strip(), configs, sections=sections)
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {str(e)}") from e

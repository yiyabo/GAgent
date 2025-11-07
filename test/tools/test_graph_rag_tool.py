from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List

import pytest

from app.config import get_graph_rag_settings, reset_graph_rag_settings_cache
from tool_box.tools_impl.graph_rag import graph_rag_handler
from tool_box.tools_impl.graph_rag.service import reset_graph_rag_service


@pytest.fixture()
def triples_file(tmp_path: Path, monkeypatch) -> Path:
    file_path = tmp_path / "triples.csv"
    with file_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "entity1",
                "entity1_type",
                "relation",
                "entity2",
                "entity2_type",
                "pdf_name",
                "source",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "entity1": "噬菌体",
                "entity1_type": "Phage",
                "relation": "感染",
                "entity2": "细菌",
                "entity2_type": "Bacteria",
                "pdf_name": "paper_1.pdf",
                "source": "文献A",
            }
        )
        writer.writerow(
            {
                "entity1": "噬菌体",
                "entity1_type": "Phage",
                "relation": "依赖",
                "entity2": "宿主",
                "entity2_type": "Host",
                "pdf_name": "paper_2.pdf",
                "source": "文献B",
            }
        )

    monkeypatch.setenv("GRAPH_RAG_TRIPLES_PATH", str(file_path))
    reset_graph_rag_settings_cache()
    reset_graph_rag_service()
    return file_path


@pytest.mark.asyncio
async def test_graph_rag_handler_success(triples_file: Path):
    result = await graph_rag_handler(query="噬菌体如何感染细菌？", top_k=5, hops=1)
    assert result["success"] is True
    payload = result["result"]
    assert payload["metadata"]["top_k"] <= get_graph_rag_settings().max_top_k
    assert payload["metadata"]["triple_count"] >= 1
    assert isinstance(payload["prompt"], str) and payload["prompt"]
    assert payload["triples"][0]["entity1"] == "噬菌体"


@pytest.mark.asyncio
async def test_graph_rag_handler_focus_entities(triples_file: Path):
    result = await graph_rag_handler(
        query="噬菌体与宿主交互",
        focus_entities=["宿主"],
        top_k=5,
        hops=0,
    )
    assert result["success"] is True
    triples: List[Dict[str, str]] = result["result"]["triples"]
    assert triples[0]["entity2"] == "宿主"
    assert result["result"]["metadata"]["focus_entities"] == ["宿主"]


@pytest.mark.asyncio
async def test_graph_rag_handler_missing_file(tmp_path: Path, monkeypatch):
    missing = tmp_path / "none.csv"
    monkeypatch.setenv("GRAPH_RAG_TRIPLES_PATH", str(missing))
    reset_graph_rag_settings_cache()
    reset_graph_rag_service()

    result = await graph_rag_handler(query="测试")
    assert result["success"] is False
    assert result["code"] == "missing_triples"

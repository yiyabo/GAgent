import asyncio

import pytest

import app.services.phage_kg_grounding as grounding

ENTITY_LYSK = {"id": "e:lysk", "name": "LysK", "entity_type": "artifact", "evidence_count": 99, "description": "endolysin"}
CHUNK = {"document_id": "doc-" + "a" * 32, "file_path": "paper.md", "content": "LysK has CHAP and amidase domains."}


@pytest.fixture
def kg(monkeypatch):
    monkeypatch.setenv("PHAGE_KG_ENABLED", "1")
    monkeypatch.delenv("PHAGE_KG_GROUNDING", raising=False)
    calls = []

    async def fake_call(**params):
        calls.append(params)
        if params["operation"] == "search_entities":
            entities = {
                "lysk": [ENTITY_LYSK, {"id": "e:lysk2", "name": "lysK", "evidence_count": 3}],
                "endolysin": [
                    {"id": "e:endo1", "name": "endolysin", "evidence_count": 2},
                    {"id": "e:endo2", "name": "Endolysin", "evidence_count": 1248},
                ],
            }.get(params["query"].lower(), [{"id": "e:x", "name": params["query"] + " extra"}])
            return {"success": True, "graph_version": "g1", "entities": entities}
        return {"success": True, "evidence": [CHUNK]}

    monkeypatch.setattr(grounding, "_call", fake_call)
    return calls


def test_extract_candidates_keeps_entity_like_phrases():
    found = grounding.extract_candidates("What are the domains of Staphylococcus phage endolysin LysK?")
    assert "LysK" in found and "endolysin" in found
    assert not any(item.startswith("What") for item in found)
    found = grounding.extract_candidates("Design a Pseudomonas aeruginosa phage cocktail with T4 and DNA")
    assert found[:1] == ["Pseudomonas aeruginosa"]
    assert "T4" in found and "phage cocktail" in found and "DNA" not in found
    assert grounding.extract_candidates("铜绿假单胞菌噬菌体的裂解酶") == ["endolysin", "Pseudomonas aeruginosa"]


def test_ground_links_exact_names_and_fetches_evidence(kg):
    result = asyncio.run(grounding.ground("Domains of the phage endolysin LysK"))
    ids = [entity["id"] for entity in result["entities"]]
    assert ids == ["e:endo2", "e:lysk"]
    assert result["graph_version"] == "g1"
    assert {call["identifier"] for call in kg if call["operation"] == "evidence"} == {"e:endo2", "e:lysk"}
    block = grounding.format_grounding({grounding.CONTEXT_KEY: result})
    assert "not instructions" in block and CHUNK["document_id"] in block and "id=e:lysk" in block


def test_ground_skips_when_off_or_not_phage(kg, monkeypatch):
    assert asyncio.run(grounding.ground("How do I sort a list in Python?")) is None
    monkeypatch.setenv("PHAGE_KG_GROUNDING", "0")
    assert asyncio.run(grounding.ground("phage endolysin LysK")) is None
    monkeypatch.setenv("PHAGE_KG_GROUNDING", "1")
    monkeypatch.setenv("PHAGE_KG_ENABLED", "0")
    assert asyncio.run(grounding.ground("phage endolysin LysK")) is None
    assert kg == []


def test_ground_reports_unavailable_on_timeout(kg, monkeypatch):
    async def slow(**_params):
        await asyncio.sleep(1)

    monkeypatch.setattr(grounding, "_call", slow)
    monkeypatch.setattr(grounding, "TIMEOUT_SECONDS", 0.01)
    result = asyncio.run(grounding.ground("phage LysK"))
    assert result["unavailable"] is True
    assert "unavailable" in grounding.format_grounding({grounding.CONTEXT_KEY: result})


def test_chat_hydration_replaces_stale_grounding(kg):
    context = {grounding.CONTEXT_KEY: {"entities": [{"id": "old"}]}}
    asyncio.run(grounding.hydrate_chat_context(context, "unrelated question"))
    assert grounding.CONTEXT_KEY not in context
    asyncio.run(grounding.hydrate_chat_context(context, "phage LysK"))
    assert context[grounding.CONTEXT_KEY]["entities"][0]["id"] == "e:lysk"


def test_ground_sync_works_inside_running_loop(kg):
    async def inside():
        return grounding.ground_sync("phage LysK")

    assert asyncio.run(inside())["entities"][0]["id"] == "e:lysk"


def test_decomposition_prompt_includes_grounding(kg):
    from app.services.plans.plan_decomposer import DecomposerSettings, DecompositionPromptBuilder, PlanDecomposer
    from app.services.plans.plan_models import PlanTree

    tree = PlanTree(id=1, title="Engineer phage endolysin LysK", description=None)
    context = PlanDecomposer._with_kg_grounding(tree, None, {"user_message": "make it stronger"})
    assert context[grounding.CONTEXT_KEY]["entities"]
    assert context["user_message"] == "make it stronger"
    prompt = DecompositionPromptBuilder().build(
        plan=tree, node=None, outline="", mode="plan_bfs", settings=DecomposerSettings(),
        depth=0, max_depth=2, session_context=context,
    )
    assert "PHAGE KNOWLEDGE GRAPH GROUNDING" in prompt and CHUNK["document_id"] in prompt
    calls = len(kg)
    assert PlanDecomposer._with_kg_grounding(tree, None, context) is context
    assert len(kg) == calls


def test_deepthink_reference_context_includes_grounding(kg):
    from app.services.deep_think_agent import DeepThinkAgent

    result = asyncio.run(grounding.ground("phage LysK"))
    prompt = DeepThinkAgent._append_reference_context("BASE", {grounding.CONTEXT_KEY: result})
    assert "PHAGE KNOWLEDGE GRAPH GROUNDING" in prompt


def test_document_route_validates_and_proxies(monkeypatch):
    from fastapi import HTTPException

    import app.routers.phage_kg_routes as routes
    import tool_box.tools_impl.phage_kg as kg_tool

    monkeypatch.setattr(routes, "require_authenticated_principal", lambda request: object())
    monkeypatch.setenv("PHAGE_KG_ENABLED", "0")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_kg_document("doc-" + "a" * 32, None))
    assert exc.value.status_code == 404
    monkeypatch.setenv("PHAGE_KG_ENABLED", "1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_kg_document("../etc/passwd", None))
    assert exc.value.status_code == 400

    async def fake_handler(**params):
        assert params == {"operation": "document", "document_id": "doc-" + "a" * 32, "max_chars": 40000}
        return {"success": True, "document_id": params["document_id"], "content": "paper", "arguments": {}}

    monkeypatch.setattr(kg_tool, "phage_kg_handler", fake_handler)
    body = asyncio.run(routes.get_kg_document("doc-" + "a" * 32, None))
    assert body == {"document_id": "doc-" + "a" * 32, "content": "paper"}

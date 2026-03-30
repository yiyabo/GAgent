from app.prompts.en_US import PROMPTS_EN_US
from app.services.tool_schemas import build_tool_schemas


def test_structured_agent_action_catalog_includes_review_pack_tools() -> None:
    base_actions = PROMPTS_EN_US["structured_agent"]["action_catalog"]["base_actions"]
    catalog_text = "\n".join(base_actions)

    assert "literature_pipeline" in catalog_text
    assert "review_pack_writer" in catalog_text
    assert "manuscript_writer" in catalog_text


def test_native_tool_schemas_include_paper_pipeline_tools() -> None:
    schemas = build_tool_schemas(
        [
            "literature_pipeline",
            "review_pack_writer",
            "manuscript_writer",
            "deliverable_submit",
        ]
    )
    function_names = {
        schema["function"]["name"]
        for schema in schemas
        if isinstance(schema, dict) and isinstance(schema.get("function"), dict)
    }

    assert "literature_pipeline" in function_names
    assert "review_pack_writer" in function_names
    assert "manuscript_writer" in function_names
    assert "deliverable_submit" in function_names

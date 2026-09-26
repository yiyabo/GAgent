"""A PDF page selection must survive both offer surfaces and the chat lane.

PDF parsing is billed per page, so a page selection is what bounds the charge.
It is worthless if it gets dropped on the way in (the native schema does not
offer it) or on the way through (the chat action lane strips it) — the model
would ask for pages 3-5, pay for all 250, and never know.
"""

from __future__ import annotations

from app.routers.chat.action_tool_params import _normalize_vision_reader_params
from app.services import tool_schemas


def test_native_schema_offers_the_page_selection() -> None:
    registry = tool_schemas._get_tool_registry()
    properties = registry["vision_reader"]["function"]["parameters"]["properties"]

    assert properties["page_numbers"]["type"] == "array"
    assert properties["page_numbers"]["items"] == {"type": "integer"}


def test_page_selection_survives_the_chat_lane() -> None:
    params = _normalize_vision_reader_params(
        None,
        None,
        "vision_reader",
        {
            "operation": "read_pdf",
            "file_path": "/data/paper.pdf",
            # 3 and 5 are usable; the rest is noise the lane must not forward.
            "page_numbers": [3, 5, "7", 0, -1, 3, True],
            "max_pages": 40,
        },
    )

    assert params["page_numbers"] == [3, 5]
    assert params["max_pages"] == 40
    assert params["operation"] == "read_pdf"


def test_absent_page_selection_is_not_invented() -> None:
    params = _normalize_vision_reader_params(
        None,
        None,
        "vision_reader",
        {"operation": "read_pdf", "image_path": "/data/paper.pdf"},
    )

    assert "page_numbers" not in params
    assert "max_pages" not in params


def test_non_positive_page_budget_is_dropped() -> None:
    params = _normalize_vision_reader_params(
        None,
        None,
        "vision_reader",
        {"operation": "read_pdf", "file_path": "/data/paper.pdf", "max_pages": 0},
    )

    assert "max_pages" not in params

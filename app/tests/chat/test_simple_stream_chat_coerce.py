"""Tests for simple-stream chat plain-text coercion (legacy JSON replies)."""

from app.routers.chat.prompt_builder import coerce_plain_text_chat_response


def test_coerce_plain_pass_through() -> None:
    assert coerce_plain_text_chat_response("Hello") == "Hello"
    assert coerce_plain_text_chat_response("  Hi  ") == "Hi"


def test_coerce_extracts_llm_reply_message() -> None:
    raw = (
        '{"llm_reply": {"message": "你好"}, "actions": []}'
    )
    assert coerce_plain_text_chat_response(raw) == "你好"


def test_coerce_strips_json_fence() -> None:
    raw = '```json\n{"llm_reply": {"message": "x"}, "actions": []}\n```'
    assert coerce_plain_text_chat_response(raw) == "x"


def test_coerce_invalid_json_unchanged() -> None:
    raw = "{not json"
    assert coerce_plain_text_chat_response(raw) == "{not json"

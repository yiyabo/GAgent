from app.routers.chat.agent import _build_simple_chat_thinking_process


def test_build_simple_chat_thinking_process_empty() -> None:
    assert _build_simple_chat_thinking_process("") is None
    assert _build_simple_chat_thinking_process("   ") is None


def test_build_simple_chat_thinking_process_shape() -> None:
    blob = _build_simple_chat_thinking_process("step one")
    assert blob is not None
    assert blob["status"] == "completed"
    assert blob["total_iterations"] == 1
    assert len(blob["steps"]) == 1
    assert blob["steps"][0]["thought"] == "step one"
    assert blob["steps"][0]["iteration"] == 0
    assert blob["steps"][0]["status"] == "done"

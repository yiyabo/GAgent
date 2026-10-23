"""Files sent with a chat message stay on the saved message and reach later turns."""
from app.routers.chat import run_routes
from app.routers.chat.models import ChatMessage, ChatRequest
from app.routers.chat.session_helpers import _convert_history_to_agent_format
from app.services.chat_attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    attach_message_attachments,
    history_attachment_note,
    sanitize_attachments,
)

LAMBDA = {"type": "file", "name": "lambda.fasta", "path": "/runtime/session_a/uploads/a1_lambda.fasta"}
T7 = {"type": "file", "name": "T7.fasta", "path": "/runtime/session_a/uploads/b2_T7.fasta"}


def test_sanitize_keeps_well_formed_entries_in_order_without_duplicates():
    raw = [LAMBDA, "junk", {"name": "no path"}, dict(LAMBDA), {**T7, "type": "weird", "extra": "x", "extracted_path": " "}]
    assert sanitize_attachments(raw) == [LAMBDA, T7]
    assert sanitize_attachments({"path": "/x"}) == []
    assert sanitize_attachments([{"path": "/a/b/reads.fq"}]) == [{"type": "file", "path": "/a/b/reads.fq", "name": "reads.fq"}]
    many = [{"path": f"/f{i}"} for i in range(MAX_ATTACHMENTS_PER_MESSAGE + 5)]
    assert len(sanitize_attachments(many)) == MAX_ATTACHMENTS_PER_MESSAGE


def test_attach_message_attachments_only_writes_when_there_are_files():
    metadata = {"client_message_id": "c1"}
    attach_message_attachments(metadata, {"attachments": []})
    assert metadata == {"client_message_id": "c1"}
    attach_message_attachments(metadata, {"attachments": [LAMBDA, T7]})
    assert metadata["attachments"] == [LAMBDA, T7]
    attach_message_attachments(metadata, None)
    assert metadata["attachments"] == [LAMBDA, T7]


def test_history_keeps_where_earlier_files_are():
    history = [
        ChatMessage(role="user", content="看看这些文件", metadata={"attachments": [LAMBDA, T7]}),
        ChatMessage(role="assistant", content="done", metadata={"attachments": [LAMBDA]}),
        ChatMessage(role="user", content="plain"),
    ]
    converted = _convert_history_to_agent_format(history)
    assert converted[0]["content"].startswith("看看这些文件\n\n📎 Files the user attached to this message:")
    assert f"- lambda.fasta: {LAMBDA['path']}" in converted[0]["content"]
    assert f"- T7.fasta: {T7['path']}" in converted[0]["content"]
    assert converted[1] == {"role": "assistant", "content": "done"}
    assert converted[2] == {"role": "user", "content": "plain"}
    assert history_attachment_note({"attachments": [{**LAMBDA, "extracted_path": "/x/extracted"}]}).endswith(
        "(extracted: /x/extracted)"
    )


def test_run_user_message_is_saved_with_its_attachments(monkeypatch):
    saved = []

    def _save(session_id, role, content, metadata=None, *, owner_id=None):
        saved.append((session_id, role, content, metadata, owner_id))
        return None

    monkeypatch.setattr(run_routes, "_save_chat_message", _save)
    request = ChatRequest(
        message="比较这两个噬菌体",
        session_id="sess",
        client_message_id="c1",
        context={"attachments": [LAMBDA, T7, {"bad": True}]},
    )
    run_routes._save_run_user_message(request, run_id="r1", session_id="sess", owner_id="alice")
    assert saved == [(
        "sess", "user", "比较这两个噬菌体",
        {"client_message_id": "c1", "attachments": [LAMBDA, T7]},
        "alice",
    )]

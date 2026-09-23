"""Stub generation tests: JSON schema -> Python signature mapping, allowlist filtering."""

from __future__ import annotations

from tool_box.tools_impl.execute_code import stub_gen


def test_type_mapping_and_required_optional_split():
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer"},
            "threshold": {"type": "number"},
            "flag": {"type": "boolean"},
            "items": {"type": "array"},
            "options": {"type": "object"},
            "untyped": {"description": "no type key"},
        },
        "required": ["query", "count"],
    }
    signature = stub_gen.build_signature("demo", parameters)
    assert signature == (
        "query: str, count: int, threshold: float = None, flag: bool = None, "
        "items: list = None, options: dict = None, untyped=None"
    )


def test_signature_line_uses_native_content_for_known_tool():
    line = stub_gen.signature_line("web_search")
    assert line is not None
    assert line.startswith("web_search(query: str")
    assert "max_results: int = None" in line


def test_signature_line_falls_back_to_impl_schema_for_lightrag():
    # lightrag_query has no NATIVE_TOOL_CONTENT entry; the impl schema is used.
    line = stub_gen.signature_line("lightrag_query")
    assert line is not None
    assert line.startswith("lightrag_query(query: str")


def test_signature_line_unknown_tool_returns_none():
    assert stub_gen.signature_line("no_such_tool_anywhere") is None


def test_generate_stub_module_filters_to_allowlist():
    source = stub_gen.generate_stub_module(["web_search", "url_fetch", "no_such_tool_anywhere"])
    assert "def web_search(" in source
    assert "def url_fetch(" in source
    assert "no_such_tool_anywhere" not in source
    assert "def sequence_fetch(" not in source
    # RPC transport + helpers are always present.
    assert "GAGENT_RPC_ENDPOINT" in source
    assert "def _call(" in source
    assert "def json_parse(" in source
    assert "def retry(" in source


def test_generated_stub_module_is_importable_and_calls_rpc_transport():
    source = stub_gen.generate_stub_module(["web_search"])
    namespace: dict = {}
    exec(compile(source, "gagent_tools.py", "exec"), namespace)
    web_search = namespace["web_search"]
    assert callable(web_search)
    assert "already-parsed dict" in (web_search.__doc__ or "")
    # The generated function forwards args to the RPC _call transport.
    captured = {}

    def fake_call(tool_name, args):
        captured["tool"] = tool_name
        captured["args"] = args
        return {"ok": True}

    namespace["_call"] = fake_call
    web_search = namespace["web_search"]
    result = web_search(query="phage", max_results=3)
    assert result == {"ok": True}
    assert captured["tool"] == "web_search"
    assert captured["args"]["query"] == "phage"
    assert captured["args"]["max_results"] == 3
    # Every schema property is forwarded (None for unset optional params).
    assert "queries" in captured["args"]

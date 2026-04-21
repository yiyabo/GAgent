from tool_box.tools_impl.literature_pipeline import _resolve_effective_proxy


def test_resolve_effective_proxy_prefers_explicit_proxy(monkeypatch):
    monkeypatch.setenv("LITERATURE_PIPELINE_PROXY", "http://127.0.0.1:7890")

    assert _resolve_effective_proxy("socks5://127.0.0.1:10808") == "socks5://127.0.0.1:10808"


def test_resolve_effective_proxy_uses_tool_scoped_env(monkeypatch):
    monkeypatch.delenv("LITERATURE_PROXY", raising=False)
    monkeypatch.setenv("LITERATURE_PIPELINE_PROXY", "http://127.0.0.1:7890")

    assert _resolve_effective_proxy(None) == "http://127.0.0.1:7890"
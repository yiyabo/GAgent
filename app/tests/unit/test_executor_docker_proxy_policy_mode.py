"""Report-vs-enforce behaviour of the proxy payload policy (app/ops/docker_proxy.py).

The proxy must never block anything while the policy is in report mode (the
default), and must refuse a bad payload with 403 *before* calling the daemon once
enforcement is on. The upstream client is stubbed, so these tests also pin that
a refused request produces no upstream traffic at all.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from app.ops import docker_proxy
from app.ops.docker_policy import ProxyPolicy

RUNTIME_ROOT = "/data/phage-agent/runtime"


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, raw: bytes = b"{}") -> None:
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.headers = {"content-type": "application/json"}
        self._raw = raw

    def json(self):
        return self._payload

    async def aiter_raw(self):
        yield self._raw


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: List[Dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, target, content=b"", headers=None):
        self.calls.append({"method": method, "target": target, "content": content})
        return _FakeStream(self.response)

    async def get(self, target):
        self.calls.append({"method": "GET", "target": target})
        return self.response


def _policy(**overrides) -> ProxyPolicy:
    base = dict(
        runtime_root=RUNTIME_ROOT,
        ro_roots=("/data",),
        allowed_images=frozenset({"gagent-sandbox-python:p1"}),
    )
    base.update(overrides)
    return ProxyPolicy(**base)


async def _call(method: str, path: str, body: bytes = b"", query: bytes = b"") -> tuple:
    scope = {"type": "http", "method": method, "path": path, "query_string": query, "headers": []}
    sent: List[Dict[str, Any]] = []
    queue = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await docker_proxy.app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, payload


@pytest.fixture()
def stub_client(monkeypatch):
    client = _FakeClient(_FakeResponse(200, {}, b'{"Id": "abc"}'))
    monkeypatch.setattr(docker_proxy, "_client", lambda: client)
    return client


def test_report_mode_forwards_and_never_blocks(stub_client, monkeypatch):
    monkeypatch.setattr(docker_proxy, "_POLICY", _policy(enforce=False))
    body = json.dumps({"Image": "alpine:latest", "HostConfig": {"Binds": ["/:/host"]}}).encode()
    status, _ = asyncio.run(_call("POST", "/containers/create", body))
    assert status == 200
    assert stub_client.calls and stub_client.calls[0]["method"] == "POST"


def test_enforce_mode_refuses_host_bind_without_calling_upstream(stub_client, monkeypatch):
    monkeypatch.setattr(docker_proxy, "_POLICY", _policy(enforce=True))
    body = json.dumps({"Image": "alpine:latest", "HostConfig": {"Binds": ["/:/host"]}}).encode()
    status, payload = asyncio.run(_call("POST", "/containers/create", body))
    assert status == 403
    assert b"policy" in payload
    assert stub_client.calls == []


def test_enforce_mode_allows_the_real_sandbox_payload(stub_client, monkeypatch):
    monkeypatch.setattr(docker_proxy, "_POLICY", _policy(enforce=True))
    body = json.dumps(
        {
            "Image": "gagent-sandbox-python:p1",
            "Labels": {"gagent.kind": "python"},
            "HostConfig": {
                "Binds": [f"{RUNTIME_ROOT}/session_1:/app/runtime/session_1:rw"],
                "NetworkMode": "gagent-sandbox",
            },
        }
    ).encode()
    status, _ = asyncio.run(_call("POST", "/containers/create", body))
    assert status == 200


def test_enforce_mode_scopes_delete_to_labelled_containers(monkeypatch):
    monkeypatch.setattr(docker_proxy, "_POLICY", _policy(enforce=True))
    inspected: List[str] = []

    async def _inspect(container_id: str):
        inspected.append(container_id)
        return {"Config": {"Labels": {"com.example": "ms-web"}}}

    monkeypatch.setattr(docker_proxy, "_inspect_container", _inspect)
    client = _FakeClient(_FakeResponse(204, {}))
    monkeypatch.setattr(docker_proxy, "_client", lambda: client)

    status, _ = asyncio.run(_call("DELETE", "/containers/msweb123"))
    assert status == 403
    assert inspected == ["msweb123"]
    assert client.calls == []


def test_enforce_mode_allows_deleting_a_gagent_container(monkeypatch):
    monkeypatch.setattr(docker_proxy, "_POLICY", _policy(enforce=True))

    async def _inspect(container_id: str):
        return {"Config": {"Labels": {"gagent.kind": "python"}}}

    monkeypatch.setattr(docker_proxy, "_inspect_container", _inspect)
    client = _FakeClient(_FakeResponse(204, {}))
    monkeypatch.setattr(docker_proxy, "_client", lambda: client)

    status, _ = asyncio.run(_call("DELETE", "/containers/gagent123"))
    assert status == 204
    assert client.calls and client.calls[0]["method"] == "DELETE"

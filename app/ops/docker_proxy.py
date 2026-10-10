"""Allowlisted Docker Engine API proxy — the stack's only path to the daemon.

WHY THIS EXISTS
The stack originally used the community image ``tecnativa/docker-socket-proxy``.
This deployment cannot obtain it: hosts here have no route to Docker Hub
(measured 2026-10-10 — registry-1.docker.io times out from .8, the daemon's
dockerproxy.net mirror is dead, the docker.1ms.run mirror answers on /v2/ but
times out for real pulls) and the byoryn registry carries python/alpine only.
So the same idea is implemented here, in a module that ships inside the app
image and runs as the ``proxy`` service. No extra artifact, no external image.

SECURITY PROPERTIES
- **Deny by default.** Only the (method, path) pairs in ``_ALLOW`` are
  forwarded; everything else is 403 and logged with the offending request line.
- **No hijacked streams.** ``exec``/``attach`` need a protocol upgrade this
  proxy deliberately does not implement; they are refused. Interactive exec is
  what the P3 sandbox-manager will own, with a narrower contract.
- **The daemon socket is mounted into THIS container only** (read-only), never
  into the app container.
- The endpoint allowlist limits *which* APIs are reachable, not the payloads of
  those APIs: ``containers/create`` can still be asked for arbitrary binds. The
  app's sandbox builder is the second line of defence, and the P3
  sandbox-manager (which owns mount policy) is the third.

Run: ``python -m app.ops.docker_proxy`` (PORT env, default 2375).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from typing import List, Pattern, Tuple

import httpx

logger = logging.getLogger("app.ops.docker_proxy")

_SOCKET_PATH = os.getenv("DOCKER_PROXY_SOCKET", "/var/run/docker.sock")
_PORT = int(os.getenv("DOCKER_PROXY_PORT", "2375"))
_HOST = os.getenv("DOCKER_PROXY_HOST", "0.0.0.0")

_ID = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
_NAME = r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,200}"

# (method, compiled path regex) — everything not listed is refused.
_ALLOW: List[Tuple[str, Pattern[str]]] = [
    ("GET", re.compile(r"^/_ping$")),
    ("GET", re.compile(r"^/version$")),
    ("GET", re.compile(r"^/info$")),
    ("GET", re.compile(r"^/containers/json$")),
    ("GET", re.compile(rf"^/containers/{_ID}/json$")),
    ("POST", re.compile(r"^/containers/create$")),
    ("POST", re.compile(rf"^/containers/{_ID}/start$")),
    ("POST", re.compile(rf"^/containers/{_ID}/stop$")),
    ("POST", re.compile(rf"^/containers/{_ID}/kill$")),
    ("POST", re.compile(rf"^/containers/{_ID}/wait$")),
    ("DELETE", re.compile(rf"^/containers/{_ID}$")),
    ("GET", re.compile(r"^/images/json$")),
    ("GET", re.compile(rf"^/images/{_NAME}/json$")),
]

# Hop-by-hop headers must not be forwarded.
_DROP_REQUEST_HEADERS = {"host", "connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer"}
_DROP_RESPONSE_HEADERS = {"connection", "keep-alive", "transfer-encoding", "upgrade", "trailer"}


def is_allowed(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in _ALLOW)


def _client() -> httpx.AsyncClient:
    # One client per request is wasteful but keeps the proxy stateless and free
    # of connection-pool state on a socket that the daemon may restart.
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=_SOCKET_PATH),
        base_url="http://docker",
        timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=10.0),
    )


async def app(scope, receive, send) -> None:
    """Pure-ASGI app (no framework): forward allowlisted requests to the daemon."""
    if scope["type"] != "http":
        return

    method = scope["method"].upper()
    path = scope.get("path") or "/"
    query = scope.get("query_string") or b""

    if not is_allowed(method, path):
        logger.warning("docker_proxy: refused %s %s", method, path)
        await _send_json(send, 403, {"message": "Forbidden by docker_proxy allowlist"})
        return

    # Read the (small) request body; Docker's create payloads are JSON, not streams.
    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break

    headers = {
        k.decode("latin-1"): v.decode("latin-1")
        for k, v in scope.get("headers", [])
        if k.decode("latin-1").lower() not in _DROP_REQUEST_HEADERS
    }
    target = path + (("?" + query.decode("latin-1")) if query else "")

    try:
        async with _client() as client:
            async with client.stream(method, target, content=body, headers=headers) as upstream:
                out_headers = [
                    (k.encode("latin-1"), v.encode("latin-1"))
                    for k, v in upstream.headers.items()
                    if k.lower() not in _DROP_RESPONSE_HEADERS
                ]
                await send(
                    {
                        "type": "http.response.start",
                        "status": upstream.status_code,
                        "headers": out_headers,
                    }
                )
                async for chunk in upstream.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    except FileNotFoundError:
        logger.error("docker_proxy: daemon socket %s not found", _SOCKET_PATH)
        await _send_json(send, 503, {"message": "Docker daemon socket unavailable"})
    except Exception as exc:  # pragma: no cover - transport failures
        logger.error("docker_proxy: upstream error: %s", exc)
        await _send_json(send, 502, {"message": f"Docker daemon error: {type(exc).__name__}"})


async def _send_json(send, status: int, payload: dict) -> None:
    import json

    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info(
        "docker_proxy listening on %s:%s -> %s (allowlisted: %d rules)",
        _HOST,
        _PORT,
        _SOCKET_PATH,
        len(_ALLOW),
    )
    uvicorn.run(app, host=_HOST, port=_PORT, log_level="warning", access_log=False)


if __name__ == "__main__":  # pragma: no cover - service entrypoint
    sys.exit(main())

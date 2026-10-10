"""Regressions for the loopback forwarder (app/ops/port_forward.py).

The forwarder exists because the containerized stack runs on a bridge network
where the host's loopback-only tunnels are unreachable, while the old
host-network container could reach them (LOCAL_INFRA §121). These tests pin the
spec parsing and prove a real byte round-trip through a forwarder, so a broken
forwarder fails here rather than as a mystery literature-search outage.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ops import port_forward


def test_parse_specs_maps_listen_port_to_target() -> None:
    specs = port_forward.parse_specs("11080:127.0.0.1:1080,17890:127.0.0.1:7890")
    assert specs == [
        ("127.0.0.1", 1080, "127.0.0.1", 11080),
        ("127.0.0.1", 7890, "127.0.0.1", 17890),
    ]


@pytest.mark.parametrize("bad", ["", "   ", "11080", "11080:127.0.0.1", "11080::1080", "a:b:c"])
def test_parse_specs_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        port_forward.parse_specs(bad)


def test_parse_binds_defaults_to_loopback() -> None:
    assert port_forward.parse_binds("") == ["127.0.0.1"]
    assert port_forward.parse_binds("  ") == ["127.0.0.1"]
    assert port_forward.parse_binds("172.17.0.1, 172.28.0.1") == ["172.17.0.1", "172.28.0.1"]


def test_forwarder_pumps_bytes_end_to_end() -> None:
    """A real round-trip: client -> forwarder -> echo server -> back."""

    async def scenario() -> bytes:
        # Echo upstream on an ephemeral port.
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = await reader.read(1024)
            writer.write(b"echo:" + data)
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]

        # Forwarder: an ephemeral listen port on loopback to that upstream.
        listen = await asyncio.start_server(
            lambda r, w: port_forward._handle(r, w, "127.0.0.1", upstream_port, "test"),
            "127.0.0.1", 0,
        )
        listen_port = listen.sockets[0].getsockname()[1]

        reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
        writer.write(b"hello")
        await writer.drain()
        reply = await asyncio.wait_for(reader.read(1024), timeout=5)
        writer.close()

        listen.close()
        upstream.close()
        return reply

    assert asyncio.run(scenario()) == b"echo:hello"


def test_forwarder_closes_client_when_upstream_is_down() -> None:
    """A dead upstream must not hang the client: the connection is closed."""

    async def scenario() -> bytes:
        # Nothing listens on this port; bind then close to get a free port.
        probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = probe.sockets[0].getsockname()[1]
        probe.close()
        await asyncio.sleep(0.05)

        listen = await asyncio.start_server(
            lambda r, w: port_forward._handle(r, w, "127.0.0.1", dead_port, "test"),
            "127.0.0.1", 0,
        )
        listen_port = listen.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
        reply = await asyncio.wait_for(reader.read(64), timeout=5)
        writer.close()
        listen.close()
        return reply

    assert asyncio.run(scenario()) == b""  # EOF, not a hang

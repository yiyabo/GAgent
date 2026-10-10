"""Loopback forwarder for host services that bind 127.0.0.1 only.

WHY THIS EXISTS
The old production container ran with ``network_mode: host``, so the app reached
the host's loopback tunnels directly: SOCKS 1080 (autossh -> cn00/.196, the
international egress used by literature search) and privoxy 7890 (nih.gov).
The containerized stack runs on a bridge network, where ``host.docker.internal``
is the bridge gateway — and a service bound to 127.0.0.1 is NOT reachable there
(measured on .8: ConnectionRefusedError for both ports, while the host itself
reaches them fine). This module runs in the HOST network namespace, where
127.0.0.1 *is* the host's loopback, and re-publishes those ports on the bridge
gateway so ordinary bridge containers can reach them — no host config change and
no extra image (it ships inside the app image, like app/ops/docker_proxy.py).

    bridge container -> 172.17.0.1:11080 -> [this] -> 127.0.0.1:1080
    bridge container -> 172.17.0.1:17890 -> [this] -> 127.0.0.1:7890

The listen ports are deliberately NOT 1080/7890 so this never collides with the
real services, and the bind list is explicit so the proxy is not exposed on the
host's LAN interfaces.

Config
    FORWARD_SPECS  "11080:127.0.0.1:1080,17890:127.0.0.1:7890"
    FORWARD_BIND   comma-separated listen addresses, e.g.
                   "172.17.0.1,172.28.0.1" (both bridge gateways we may be
                   resolved as). Empty = 127.0.0.1 only.

Run: ``python -m app.ops.port_forward``
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import List, Tuple

logger = logging.getLogger("app.ops.port_forward")

_DEFAULT_SPECS = "11080:127.0.0.1:1080,17890:127.0.0.1:7890"
_BUFFER = 64 * 1024


def parse_specs(raw: str) -> List[Tuple[str, int, str, int]]:
    """Parse "listen_port:target_host:target_port,..." into tuples.

    Raises ValueError on a malformed entry so a typo fails at startup rather
    than silently forwarding nothing.
    """
    specs: List[Tuple[str, int, str, int]] = []
    for chunk in str(raw or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 3:
            raise ValueError(f"bad forward spec {item!r}: want listen_port:host:port")
        listen_port_s, host, target_port_s = (p.strip() for p in parts)
        if not host:
            raise ValueError(f"bad forward spec {item!r}: empty target host")
        specs.append((host, int(target_port_s), host, int(listen_port_s)))
    if not specs:
        raise ValueError("no forward specs configured")
    return specs


def parse_binds(raw: str) -> List[str]:
    binds = [b.strip() for b in str(raw or "").split(",") if b.strip()]
    return binds or ["127.0.0.1"]


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(_BUFFER)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                  target_host: str, target_port: int, label: str) -> None:
    peer = client_writer.get_extra_info("peername")
    try:
        up_reader, up_writer = await asyncio.open_connection(target_host, target_port)
    except Exception as exc:
        logger.warning("%s: upstream %s:%s unavailable: %s", label, target_host, target_port, exc)
        client_writer.close()
        return
    logger.info("%s: %s -> %s:%s", label, peer, target_host, target_port)
    await asyncio.gather(
        _pump(client_reader, up_writer),
        _pump(up_reader, client_writer),
    )


async def main_async() -> None:
    specs = parse_specs(os.getenv("FORWARD_SPECS", _DEFAULT_SPECS))
    binds = parse_binds(os.getenv("FORWARD_BIND", ""))
    servers = []
    for target_host, target_port, _h, listen_port in specs:
        for bind in binds:
            label = f"{bind}:{listen_port}->{target_host}:{target_port}"
            try:
                server = await asyncio.start_server(
                    lambda r, w, th=target_host, tp=target_port, lb=label: _handle(r, w, th, tp, lb),
                    bind, listen_port,
                )
            except OSError as exc:
                # A bind can legitimately fail (e.g. the address does not exist on
                # this host); keep the other binds alive but say so loudly.
                logger.error("%s: cannot listen: %s", label, exc)
                continue
            servers.append(server)
            logger.info("forwarding %s", label)
    if not servers:
        raise RuntimeError("no forwarders could bind; check FORWARD_SPECS/FORWARD_BIND")
    await asyncio.gather(*(s.serve_forever() for s in servers))


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover - service entrypoint
    sys.exit(main())

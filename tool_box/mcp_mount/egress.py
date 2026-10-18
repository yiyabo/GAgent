"""Host-allowlisting HTTPS egress proxy for containerised MCP servers.

A mounted server runs on the internal sandbox network, which has no route to the
internet. The only way out is this proxy, reached with a per-container token.
It speaks just enough HTTP for ``CONNECT host:443`` and nothing else:

* the target must match the host allowlist of the container that owns the token,
  or the container must be marked ``public_web`` (then any public host passes);
* the proxy resolves the name itself and refuses the connection when ANY answer
  is not a global address (loopback, private, link-local, metadata, ...), then
  dials the validated address, so a name that resolves differently a second
  later cannot redirect the connection;
* only port 443, only IP-free host names, bounded concurrency and lifetime.

It controls *where* a server may connect, not what it sends there. TLS is
end-to-end, so an allowed host that relays data elsewhere is out of its reach.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import ipaddress
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

ALLOWED_PORT = 443
MAX_HEADER_BYTES = 8192
HEADER_TIMEOUT_SEC = 10.0
CONNECT_TIMEOUT_SEC = 10.0
IDLE_TIMEOUT_SEC = 120.0
MAX_CONNECTION_SEC = 300.0
MAX_CONCURRENT_PER_TOKEN = 32

# A DNS name with a purely alphabetic top-level label. That alone rules out IPv4
# literals in every spelling (dotted, hex, octal, decimal) and IPv6 literals.
_HOST_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

Resolver = Callable[[str, int], Awaitable[List[str]]]
Dialer = Callable[[str, int], Awaitable[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


async def _dial(address: str, port: int) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(address, port)


@dataclass(frozen=True)
class EgressGrant:
    """What one container may reach."""

    label: str
    hosts: Tuple[str, ...] = ()
    public_web: bool = False


@dataclass
class _Entry:
    grant: EgressGrant
    active: int = 0


def host_matches(host: str, patterns: Sequence[str]) -> bool:
    """Exact match, or ``*.example.org`` for any strict subdomain of example.org."""
    for pattern in patterns:
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.org"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    return False


async def system_resolver(host: str, port: int) -> List[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=0, proto=0)
    return list(dict.fromkeys(info[4][0] for info in infos))


def all_global(addresses: Sequence[str]) -> bool:
    if not addresses:
        return False
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            return False
    return True


class EgressProxy:
    def __init__(self, *, resolver: Resolver = system_resolver, dialer: Dialer = _dial) -> None:
        self._resolver = resolver
        self._dialer = dialer
        self._entries: Dict[str, _Entry] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._bind = "127.0.0.1"
        self._advertise = "127.0.0.1"
        self._port = 0
        self._tasks: Set[asyncio.Task] = set()
        self.denied: List[Tuple[str, str, str]] = []  # (label, target, reason); newest last, capped
        self.allowed: Dict[str, int] = {}  # label -> tunnels opened

    # ----------------------------------------------------------------- lifecycle
    async def start(self, *, bind: str, advertise: str, port: int = 0) -> None:
        if self._server is not None:
            return
        self._bind, self._advertise = bind, advertise
        self._server = await asyncio.start_server(self._handle, bind, port, limit=MAX_HEADER_BYTES * 2)
        self._port = int(self._server.sockets[0].getsockname()[1])
        logger.info("MCP egress proxy listening on %s:%s (advertised as %s)", bind, self._port, advertise)

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self._entries.clear()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._port

    # --------------------------------------------------------------------- grants
    def register(self, grant: EgressGrant) -> str:
        """Issue a token for *grant*; the caller puts it into the container's proxy URL."""
        token = secrets.token_urlsafe(24)
        self._entries[token] = _Entry(grant)
        return token

    def revoke(self, token: str) -> None:
        self._entries.pop(token, None)

    def proxy_url(self, token: str) -> str:
        return f"http://mcp:{token}@{self._advertise}:{self._port}"

    def _lookup(self, token: str) -> Optional[_Entry]:
        found: Optional[_Entry] = None
        for known, entry in self._entries.items():  # constant-time per comparison; few entries
            if hmac.compare_digest(known.encode(), token.encode()):
                found = entry
        return found

    # -------------------------------------------------------------------- serving
    def _deny(self, label: str, target: str, reason: str) -> None:
        self.denied.append((label, target, reason))
        del self.denied[:-200]
        logger.warning("MCP egress denied: mount=%s target=%s reason=%s", label, target, reason)

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: str) -> None:
        try:
            writer.write(f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
            await writer.drain()
        except OSError:
            pass

    async def _read_head(self, reader: asyncio.StreamReader) -> Optional[Tuple[str, Dict[str, str]]]:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_SEC)
        if len(raw) > MAX_HEADER_BYTES:
            return None
        lines = raw.decode("latin-1").split("\r\n")
        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return lines[0], headers

    @staticmethod
    def _token_from(headers: Dict[str, str]) -> str:
        auth = headers.get("proxy-authorization", "")
        scheme, _, blob = auth.partition(" ")
        if scheme.lower() != "basic":
            return ""
        try:
            user_pass = base64.b64decode(blob.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return ""
        return user_pass.partition(":")[2]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        entry: Optional[_Entry] = None
        try:
            try:
                head = await self._read_head(reader)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                return
            if head is None:
                await self._reply(writer, "431 Request Header Fields Too Large")
                return
            request_line, headers = head

            entry = self._lookup(self._token_from(headers))
            if entry is None:
                await self._reply(writer, "407 Proxy Authentication Required")
                return
            label = entry.grant.label

            parts = request_line.split(" ")
            if len(parts) != 3 or parts[0] != "CONNECT":
                self._deny(label, request_line[:80], "only CONNECT is supported")
                await self._reply(writer, "405 Method Not Allowed")
                return
            host, _, port_text = parts[1].rpartition(":")
            host = host.lower().rstrip(".")
            if port_text != str(ALLOWED_PORT):
                self._deny(label, parts[1], "port not allowed")
                await self._reply(writer, "403 Forbidden")
                return
            if not _HOST_RE.match(host):  # also rejects IP literals and bracketed IPv6
                self._deny(label, parts[1], "not a plain host name")
                await self._reply(writer, "403 Forbidden")
                return
            if not entry.grant.public_web and not host_matches(host, entry.grant.hosts):
                self._deny(label, parts[1], "host not in allowlist")
                await self._reply(writer, "403 Forbidden")
                return
            if entry.active >= MAX_CONCURRENT_PER_TOKEN:
                self._deny(label, parts[1], "too many concurrent connections")
                await self._reply(writer, "429 Too Many Requests")
                return

            try:
                addresses = await asyncio.wait_for(self._resolver(host, ALLOWED_PORT), CONNECT_TIMEOUT_SEC)
            except (OSError, asyncio.TimeoutError):
                await self._reply(writer, "502 Bad Gateway")
                return
            if not all_global(addresses):
                self._deny(label, parts[1], f"resolves to a non-public address ({','.join(addresses) or 'none'})")
                await self._reply(writer, "403 Forbidden")
                return

            upstream: Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]] = None
            for address in addresses:
                try:
                    upstream = await asyncio.wait_for(self._dialer(address, ALLOWED_PORT), CONNECT_TIMEOUT_SEC)
                    break
                except (OSError, asyncio.TimeoutError):
                    continue
            if upstream is None:
                await self._reply(writer, "502 Bad Gateway")
                return

            entry.active += 1
            self.allowed[label] = self.allowed.get(label, 0) + 1
            try:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await self._pipe(reader, writer, *upstream)
            finally:
                entry.active -= 1
        except asyncio.CancelledError:
            raise
        except Exception:  # a misbehaving peer must never take the proxy down
            logger.exception("MCP egress proxy: unexpected error")
        finally:
            if task is not None:
                self._tasks.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def _pipe(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
    ) -> None:
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    chunk = await asyncio.wait_for(src.read(65536), IDLE_TIMEOUT_SEC)
                    if not chunk:
                        break
                    dst.write(chunk)
                    await dst.drain()
            except (asyncio.TimeoutError, OSError, ConnectionError):
                pass

        pumps = [asyncio.ensure_future(copy(client_reader, up_writer)), asyncio.ensure_future(copy(up_reader, client_writer))]
        try:
            await asyncio.wait(pumps, timeout=MAX_CONNECTION_SEC, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            try:
                up_writer.close()
            except Exception:
                pass


_proxy: Optional[EgressProxy] = None


def get_egress_proxy() -> EgressProxy:
    global _proxy
    if _proxy is None:
        _proxy = EgressProxy()
    return _proxy

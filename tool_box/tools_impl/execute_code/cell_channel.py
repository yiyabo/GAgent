"""Cell + stderr channel for sandboxed execute_code kernels.

WHY THIS EXISTS
A local kernel is a child process: the host writes cells to its stdin and reads
sentinel-framed replies from its stdout (``kernel.py``). A *sandbox* kernel is a
sibling container, and the Docker API proxy deliberately implements no
``exec``/``attach`` hijack (``app/ops/docker_proxy.py``), so the two pipes cannot
be replaced one for one. The transport is therefore inverted: the sandbox dials
OUT to the host, and the host feeds those connections to the very same
reader/writer code the pipe path uses.

Two connections per kernel:
  * role ``cell``   — the runner ``dup2()``s it onto fds 0 and 1, so its
    ``for line in sys.stdin`` loop and its stdout reply framing are byte-for-byte
    the local protocol (including fd-level output from subprocesses, which rides
    fd 1 into the frame parser as raw bytes);
  * role ``stderr`` — ``dup2()``ed onto fd 2, keeping fd-level stderr a separate
    stream. It must NOT share the cell socket: two threads writing frames and
    stderr into one stream could split a protocol frame in half.

EOF on the cell socket is also the liveness signal. The local path hands the
child a pipe whose write end the host holds (``GAGENT_KERNEL_PARENT_FD``); here
the host closing the socket ends the runner's stdin loop, so the container exits
on its own. No watchdog fd crosses the container boundary.

SECURITY
The first line of every connection is a JSON handshake carrying the per-spawn
token. An empty server token or a mismatch closes the connection before any
stream byte is accepted (constant-time compare, fails closed). The server binds
``CODE_MODE_RPC_BIND`` and advertises ``CODE_MODE_RPC_ADVERTISE`` — the same
knobs as the tool RPC server, because "how a sandbox reaches the app" is one
question with one answer.
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

#: Env vars the sandbox runner reads to dial back (see KERNEL_RUNNER_SOURCE).
ENV_CELL_ENDPOINT = "GAGENT_CELL_ENDPOINT"
ENV_CELL_TOKEN = "GAGENT_CELL_TOKEN"

ROLE_CELL = "cell"
ROLE_STDERR = "stderr"

_MAX_HANDSHAKE_BYTES = 4096
_HANDSHAKE_TIMEOUT_SECONDS = 20.0


class _EmptyReader:
    """Stand-in stream for a connection that never arrived."""

    def read1(self, _size: int = 0) -> bytes:  # pragma: no cover - trivial
        return b""


def _close(sock: Optional[socket.socket]) -> None:
    try:
        if sock is not None:
            sock.close()
    except OSError:
        pass


class CellChannelServer:
    """Accepts the sandbox's dial-back connections for one kernel.

    Plain blocking sockets on a daemon thread: no event loop to hand a socket
    across, and the two streams are consumed by the kernel's existing
    ``_stdout_reader`` / ``_stderr_reader`` threads, which are blocking too.
    """

    def __init__(self, token: str) -> None:
        self.token = str(token or "")
        self._listen: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._cell_conn: Optional[socket.socket] = None
        self._stderr_conn: Optional[socket.socket] = None
        self._cell_ready = threading.Event()
        self._stderr_ready = threading.Event()
        self._stop = threading.Event()
        self._bind = "127.0.0.1"
        self._advertised = "127.0.0.1"
        self._port = 0

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> str:
        """Bind, spawn the accept thread, and return the ``tcp://host:port`` endpoint."""
        from .rpc import _advertise_host, _bind_host  # one source of truth

        self._bind = _bind_host()
        self._advertised = _advertise_host(self._bind)
        listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.bind((self._bind, 0))
        listen.listen(8)
        self._listen = listen
        self._port = int(listen.getsockname()[1])
        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="gagent-code-mode-channel"
        )
        self._thread.start()
        return f"tcp://{self._advertised}:{self._port}"

    @property
    def endpoint(self) -> str:
        return f"tcp://{self._advertised}:{self._port}"

    def stop(self) -> None:
        """Close everything. Closing the cell socket is what tells the runner the host is gone."""
        self._stop.set()
        _close(self._listen)
        self._listen = None
        _close(self._cell_conn)
        _close(self._stderr_conn)
        self._cell_conn = None
        self._stderr_conn = None
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2)
        # Never leave a waiter blocked on a channel that can no longer arrive.
        self._cell_ready.set()
        self._stderr_ready.set()

    # -- accept + handshake ---------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            listen = self._listen
            if listen is None:
                return
            try:
                conn, _peer = listen.accept()
            except OSError:
                return
            threading.Thread(
                target=self._handshake, args=(conn,), daemon=True
            ).start()

    def _read_handshake(self, conn: socket.socket) -> bytes:
        """Read exactly one line, one byte at a time.

        Byte-wise on purpose: a bulk ``recv`` could swallow the first stream
        bytes that follow the newline and lose them.
        """
        line = b""
        while not line.endswith(b"\n"):
            chunk = conn.recv(1)
            if not chunk:
                raise ConnectionError("connection closed during handshake")
            line += chunk
            if len(line) > _MAX_HANDSHAKE_BYTES:
                raise ValueError("handshake line too large")
        return line

    def _handshake(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(_HANDSHAKE_TIMEOUT_SECONDS)
            payload = json.loads(self._read_handshake(conn).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - a bad peer must not kill the server
            logger.warning("code-mode channel: rejected handshake: %s", exc)
            _close(conn)
            return

        token = str(payload.get("token") or "")
        role = str(payload.get("role") or "")
        if not self.token or not secrets.compare_digest(token, self.token):
            logger.warning("code-mode channel: rejected handshake (bad token)")
            _close(conn)
            return

        conn.settimeout(None)
        if role == ROLE_CELL and self._cell_conn is None:
            self._cell_conn = conn
            self._cell_ready.set()
            return
        if role == ROLE_STDERR and self._stderr_conn is None:
            self._stderr_conn = conn
            self._stderr_ready.set()
            return
        logger.warning("code-mode channel: unexpected role %r (or duplicate)", role)
        _close(conn)

    # -- handoff to the kernel ------------------------------------------------

    def wait_for_streams(self, timeout: float) -> Tuple[object, object, object]:
        """Block until the sandbox connects; returns ``(stdout_reader, stdin_writer, stderr_reader)``.

        The readers/writers are the file objects the kernel's pipe path already
        expects: ``read1()`` on the readers, ``write()``/``flush()`` on the
        writer. The stderr connection is best-effort — a kernel that never opens
        it still runs, with an empty stderr stream.
        """
        deadline = time.monotonic() + max(0.1, float(timeout))
        if not self._cell_ready.wait(max(0.0, deadline - time.monotonic())):
            raise TimeoutError("sandbox did not connect its cell channel in time")
        self._stderr_ready.wait(max(0.0, deadline - time.monotonic()))
        cell = self._cell_conn
        if cell is None:
            raise TimeoutError("sandbox cell channel closed before handoff")
        stderr_conn = self._stderr_conn
        return (
            cell.makefile("rb"),
            cell.makefile("wb"),
            stderr_conn.makefile("rb") if stderr_conn is not None else _EmptyReader(),
        )

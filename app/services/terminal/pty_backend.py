"""Local PTY backend for interactive terminal sessions."""

from __future__ import annotations

import asyncio
import os
import signal
import struct
import tempfile
import termios
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from uuid import uuid4

import fcntl
import pty

from .resource_limiter import DEFAULT_TERMINAL_LIMITS, ResourceLimits, apply_limits_in_child

CommandHandler = Callable[[str], Awaitable[str | bool]]


class PTYBackend:
    """Spawn and control a local PTY process."""

    def __init__(self) -> None:
        self.master_fd: Optional[int] = None
        self.child_pid: Optional[int] = None
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closed = True

        self._command_read_fd: Optional[int] = None
        self._verdict_write_fd: Optional[int] = None
        self._command_buffer = b""
        self._command_handler: Optional[CommandHandler] = None
        self._command_lock = asyncio.Lock()
        self._rcfile_path: Optional[str] = None  # cleaned up in terminate()

    async def spawn(
        self,
        *,
        shell: str = "/bin/bash",
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        cols: int = 120,
        rows: int = 36,
        command_handler: Optional[CommandHandler] = None,
        limits: Optional[ResourceLimits] = None,
    ) -> None:
        if self.child_pid is not None:
            raise RuntimeError("PTY process is already running")

        self._loop = asyncio.get_running_loop()
        self._command_handler = command_handler

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            raise RuntimeError(
                f"Failed to allocate PTY device: {exc}. "
                "The system may be out of available PTY devices."
            ) from exc
        self._set_winsize(slave_fd, cols, rows)

        cmd_read_fd, cmd_write_fd = os.pipe()
        verdict_read_fd, verdict_write_fd = os.pipe()

        # Write the security guard to a temp rcfile so bash loads it silently
        # (no PTY echo) instead of receiving it as interactive stdin input.
        rcfile_path: Optional[str] = None
        shell_name = os.path.basename(str(shell)).lower()
        if "bash" in shell_name:
            rcfile_path = os.path.join(
                tempfile.gettempdir(), f".agent_bashrc_{uuid4().hex}"
            )
            with open(rcfile_path, "w") as _f:
                _f.write(self._build_guard_script())
            self._rcfile_path = rcfile_path

        pid = os.fork()
        if pid == 0:  # pragma: no cover - exercised in integration tests
            try:
                os.setsid()
                os.close(master_fd)

                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)

                # fd=3: child writes command checks, fd=4: child reads verdicts.
                os.dup2(cmd_write_fd, 3)
                os.dup2(verdict_read_fd, 4)

                # Close parent-side descriptors in child process.
                for fd in (cmd_read_fd, verdict_write_fd, cmd_write_fd, verdict_read_fd):
                    if fd > 4:
                        try:
                            os.close(fd)
                        except OSError:
                            pass

                if cwd:
                    os.chdir(str(Path(cwd).resolve()))

                child_env = os.environ.copy()
                child_env.setdefault("TERM", "xterm-256color")
                if env:
                    child_env.update({str(k): str(v) for k, v in env.items()})

                apply_limits_in_child(limits or DEFAULT_TERMINAL_LIMITS)

                shell_path = str(shell)
                if "bash" in shell_name and rcfile_path:
                    # --rcfile loads the guard silently; suppress system-wide
                    # profile noise with --noprofile.
                    argv = [shell_path, "--noprofile", "--rcfile", rcfile_path, "-i"]
                else:
                    argv = [shell_path]
                os.execvpe(shell_path, argv, child_env)
            except Exception:
                os._exit(127)

        # Parent process
        os.close(slave_fd)
        os.close(cmd_write_fd)
        os.close(verdict_read_fd)

        # NOTE: Do NOT unlink rcfile here.  The child may not have finished
        # reading it yet (race condition).  Cleanup happens in terminate().

        self.master_fd = master_fd
        self.child_pid = pid
        self._command_read_fd = cmd_read_fd
        self._verdict_write_fd = verdict_write_fd
        self._closed = False

        os.set_blocking(self.master_fd, False)
        os.set_blocking(self._command_read_fd, False)

        self._loop.add_reader(self.master_fd, self._on_master_readable)
        self._loop.add_reader(self._command_read_fd, self._on_command_readable)

    def _set_winsize(self, fd: int, cols: int, rows: int) -> None:
        winsize = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    @staticmethod
    def _build_guard_script() -> str:
        """Return the bash DEBUG-trap security guard as a shell script string."""
        return (
            "_agent_check(){\n"
            "  [ -n \"${_AGENT_CHECK_ACTIVE:-}\" ] && return 0\n"
            "  local _cmd=\"${BASH_COMMAND}\"\n"
            "  case \"$_cmd\" in _agent_check*|trap*|\":\"|\"\") return 0 ;; esac\n"
            "  _AGENT_CHECK_ACTIVE=1\n"
            "  printf 'CMD_CHECK:%s\\n' \"$_cmd\" >&3\n"
            "  IFS= read -r _verdict <&4 || _verdict=BLOCK\n"
            "  unset _AGENT_CHECK_ACTIVE\n"
            "  [ \"$_verdict\" = \"ALLOW\" ] && return 0\n"
            "  echo 'Command blocked by security policy' >&2\n"
            "  return 1\n"
            "}\n"
            "trap '_agent_check' DEBUG\n"
        )

    def _on_master_readable(self) -> None:
        if self.master_fd is None:
            return
        try:
            chunk = os.read(self.master_fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""

        if chunk:
            if self._output_queue.full():
                try:
                    self._output_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._output_queue.put_nowait(chunk)
            return

        # EOF
        self._closed = True
        if self._loop and self.master_fd is not None:
            try:
                self._loop.remove_reader(self.master_fd)
            except Exception:
                pass

    def _on_command_readable(self) -> None:
        if self._command_read_fd is None:
            return

        try:
            chunk = os.read(self._command_read_fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""

        if not chunk:
            return

        self._command_buffer += chunk
        while b"\n" in self._command_buffer:
            line, self._command_buffer = self._command_buffer.split(b"\n", 1)
            text = line.decode("utf-8", errors="replace").strip()
            if not text.startswith("CMD_CHECK:"):
                continue
            command = text[len("CMD_CHECK:") :].strip()
            asyncio.create_task(self._process_command_check(command))

    async def _process_command_check(self, command: str) -> None:
        verdict = "ALLOW"
        async with self._command_lock:
            try:
                if self._command_handler is not None:
                    result = await self._command_handler(command)
                    if isinstance(result, bool):
                        verdict = "ALLOW" if result else "BLOCK"
                    else:
                        normalized = str(result or "").strip().upper()
                        verdict = "ALLOW" if normalized == "ALLOW" else "BLOCK"
            except Exception:
                verdict = "BLOCK"

            if self._verdict_write_fd is not None:
                try:
                    os.write(self._verdict_write_fd, f"{verdict}\n".encode("utf-8"))
                except OSError:
                    pass

    async def read(self) -> bytes:
        return await self._output_queue.get()

    async def write(self, data: bytes) -> None:
        if self.master_fd is None:
            raise RuntimeError("PTY is not running")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("PTY write() expects bytes")
        os.write(self.master_fd, bytes(data))

    async def resize(self, cols: int, rows: int) -> None:
        if self.master_fd is None:
            return
        self._set_winsize(self.master_fd, cols, rows)
        if self.child_pid:
            try:
                os.kill(self.child_pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    async def terminate(self) -> None:
        self._closed = True

        # Clean up the rcfile (deferred from spawn to avoid race condition)
        if self._rcfile_path:
            try:
                os.unlink(self._rcfile_path)
            except OSError:
                pass
            self._rcfile_path = None

        if self._loop and self.master_fd is not None:
            try:
                self._loop.remove_reader(self.master_fd)
            except Exception:
                pass

        if self._loop and self._command_read_fd is not None:
            try:
                self._loop.remove_reader(self._command_read_fd)
            except Exception:
                pass

        for fd_name in ("master_fd", "_command_read_fd", "_verdict_write_fd"):
            fd = getattr(self, fd_name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, fd_name, None)

        if self.child_pid is not None:
            pid = self.child_pid
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                self.child_pid = None

            await self._wait_for_exit(timeout_sec=3.0)
            if self.child_pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self._wait_for_exit(timeout_sec=1.0)

    async def _wait_for_exit(self, *, timeout_sec: float) -> None:
        if self.child_pid is None:
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_sec)
        while loop.time() < deadline and self.child_pid is not None:
            try:
                pid, _status = os.waitpid(self.child_pid, os.WNOHANG)
            except ChildProcessError:
                self.child_pid = None
                break
            if pid == self.child_pid:
                self.child_pid = None
                break
            await asyncio.sleep(0.05)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def pid(self) -> Optional[int]:
        return self.child_pid

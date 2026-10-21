"""Stdlib-only runner copied into the terminal workspace inside its container."""
import base64
import fcntl
import json
import os
import pty
import signal
import socket
import struct
import sys
import termios
import threading


def main():
    endpoint = os.environ["GAGENT_CELL_ENDPOINT"]
    host, _, port = endpoint.partition("://")[2].partition(":")

    def dial(role):
        sock = socket.create_connection((host, int(port)), timeout=8)
        sock.settimeout(None)
        sock.sendall((json.dumps({"role": role, "token": os.environ["GAGENT_CELL_TOKEN"]}) + "\n").encode())
        return sock

    channel, errors = dial("cell"), dial("stderr")
    master, slave = pty.openpty()
    size = json.loads(os.environ["GAGENT_TERMINAL_SIZE"])
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size[1], size[0], 0, 0))
    pid = os.fork()
    if pid == 0:
        try:
            channel.close(); errors.close(); os.close(master)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            for fd in (0, 1, 2):
                os.dup2(slave, fd)
            if slave > 2:
                os.close(slave)
            command = json.loads(os.environ["GAGENT_TERMINAL_COMMAND"])
            env = {k: v for k, v in os.environ.items() if not k.startswith("GAGENT_CELL_")}
            os.execvpe(command[0], command, env)
        finally:
            os._exit(127)
    os.close(slave)

    def output():
        try:
            while True:
                data = os.read(master, 65536)
                if not data:
                    break
                channel.sendall(data)
        except OSError:
            pass
        finally:
            try:
                channel.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    threading.Thread(target=output, daemon=True).start()
    try:
        for line in channel.makefile("rb"):
            message = json.loads(line)
            if message["op"] == "write":
                data = base64.b64decode(message["data"], validate=True)
                while data:
                    data = data[os.write(master, data):]
            elif message["op"] == "resize":
                size = struct.pack("HHHH", int(message["rows"]), int(message["cols"]), 0, 0)
                fcntl.ioctl(master, termios.TIOCSWINSZ, size)
                os.killpg(pid, signal.SIGWINCH)
            elif message["op"] == "close":
                break
    finally:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(master)
        channel.close(); errors.close()


if __name__ == "__main__":
    main()

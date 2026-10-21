"""Filesystem and process guard for local execute_code kernels. This file runs INSIDE the kernel.

kernel.py copies it into the kernel dir as ``gagent_fs_guard.py``. The runner calls ``install()`` before the
first cell and fails closed if it cannot; a ``sitecustomize.py`` next to it does the same in every Python
process the kernel starts (multiprocessing spawn workers, joblib's loky), because they inherit the policy
from the environment and the kernel dir from PYTHONPATH. Standard library only: it must not import app or
tool_box code.

Why it exists: a local kernel is a child process of the app with the same user. Without a guard a cell can
read /proc/<app pid>/environ (the provider keys the env scrub removed from the kernel's own env), the
SQLite files under data/, other sessions' workspaces, or /etc, even when ``file_operations`` has just refused
the same path. The guard applies the session boundary ``file_operations`` already enforces to what a cell can do.

Rules, from the policy the host builds in fs_policy.py:
  * reads are allowed everywhere except ``deny_roots`` (and other processes' /proc entries), with
    ``rw_roots``/``ro_roots`` carving exceptions out of a deny root;
  * writes are allowed only under ``rw_roots`` and ``write_roots`` (plus /dev/null, the temp dir, caches);
  * ``blind_roots`` can be read by exact path but not listed (outputs keyed by an unguessable run id);
  * programs cannot be started (subprocess, os.system, exec, posix_spawn): a child process would not carry
    this hook. fork stays allowed, because the child inherits the hook (multiprocessing keeps working).

This is a guardrail against a model working around a refusal, not a jail: an audit hook cannot be removed,
but a determined program can still go around it (dir_fd-relative opens, raw syscalls through ctypes objects
loaded before the guard, gc introspection). Real isolation is the sandbox container (CODE_MODE_SANDBOX=1),
where this guard is not used.

Errors are PermissionError (an OSError), so libraries that probe optional paths (matplotlib, fontconfig,
mimetypes) degrade the way they would on a read-only filesystem.
"""

import errno
import json
import os
import sys
import sysconfig
import tempfile

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

_PROCESS_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "pty.spawn",
    }
)
# (event, argument index) pairs that name a path being read / written.
_READ_ARGS = {
    "os.listdir": (0,),
    "os.scandir": (0,),
    "os.chdir": (0,),
    "os.link": (0,),
    "shutil.copyfile": (0,),
    "shutil.copymode": (0,),
    "shutil.copystat": (0,),
    "shutil.copytree": (0,),
    # sqlite3 opens read-write, but the event cannot tell a read-only reference database from a write,
    # so only the read rule applies: the app's own SQLite files sit under a deny root.
    "sqlite3.connect": (0,),
}
_WRITE_ARGS = {
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.remove": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "os.truncate": (0,),
    "os.mkfifo": (0,),
    "os.mknod": (0,),
    "os.rename": (0, 1),
    "os.link": (1,),
    "os.symlink": (1,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
}
_LIST_EVENTS = frozenset({"os.listdir", "os.scandir"})
_WATCHED = frozenset(
    set(_PROCESS_EVENTS) | set(_READ_ARGS) | set(_WRITE_ARGS) | {"open", "ctypes.dlopen"}
)
_LIBC_PREFIXES = ("libc.", "libc-", "libpthread", "libdl.", "libm.")
_ALWAYS_WRITABLE = ("/dev/null", "/dev/zero", "/dev/tty", "/dev/shm", "/dev/stdout", "/dev/stderr")


def _normalise(path):
    return os.path.normpath(str(path))


def _under(path, roots):
    """True when path is one of roots or inside one; roots is a set, so the cost is the path's depth."""
    while True:
        if path in roots:
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def _as_path(value):
    """A str path, or None for file descriptors, None and anything that is not path-like."""
    if value is None or isinstance(value, int):
        return None
    try:
        return os.fsdecode(os.fspath(value))
    except TypeError:
        return None


def _python_prefixes():
    """Where the interpreter and its packages live: always readable."""
    found = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    for value in sysconfig.get_paths().values():
        found.add(value)
    try:
        import site

        found.update(site.getsitepackages())
        found.add(site.getusersitepackages())
    except Exception:  # noqa: BLE001 - site may be unavailable in embedded interpreters
        pass
    found.update({"/usr/lib", "/usr/local/lib", "/usr/share", "/lib", "/lib64", "/usr/lib64"})
    return [_normalise(p) for p in found if p]


def _cache_roots():
    """Places libraries write caches to on first import (matplotlib, fontconfig)."""
    roots = []
    home = os.path.expanduser("~")
    for name in ("MPLCONFIGDIR", "XDG_CACHE_HOME"):
        value = os.environ.get(name)
        if value:
            roots.append(value)
    if home and home != "~":
        roots.append(os.path.join(home, ".cache"))
        roots.append(os.path.join(home, ".config", "matplotlib"))
    return roots


def _roots(paths):
    """Each root as given and as the filesystem resolves it (/tmp is /private/tmp on macOS)."""
    out = set()
    for path in paths:
        out.add(_normalise(path))
        out.add(_normalise(os.path.realpath(str(path))))
    return out


class _Policy:
    def __init__(self, data):
        pid = str(os.getpid())
        self.pid = pid
        self.allow_subprocess = bool(data.get("allow_subprocess"))
        self.rw_roots = _roots(data.get("rw_roots", []))
        self.ro_roots = _roots(data.get("ro_roots", []))
        self.blind_roots = _roots(data.get("blind_roots", []))
        self.deny_roots = _roots(data.get("deny_roots", []))
        extra = _roots(data.get("write_roots", []))
        extra |= _roots(_cache_roots())
        extra |= _roots([tempfile.gettempdir()])
        extra |= set(_ALWAYS_WRITABLE)
        extra.add("/proc/" + pid + "/fd")
        self.scratch_roots = extra
        self.python_roots = set(_python_prefixes())
        self.readable = self.rw_roots | self.ro_roots | self.blind_roots

    def _blocked(self, path, write):
        return PermissionError(
            errno.EACCES,
            "execute_code cannot %s this path: it is outside the session workspace. Keep files in "
            "the working directory (relative paths) and reach tools through gagent_tools"
            % ("write to" if write else "read"),
            path,
        )

    def _foreign_process_entry(self, real):
        # /proc/self resolves to /proc/<own pid>; any other pid is somebody else's process (the app's environ).
        if not real.startswith("/proc/"):
            return False
        head = real[len("/proc/"):].split("/", 1)[0]
        return head.isdigit() and head != self.pid

    def check(self, raw, write, listing=False):
        path = _as_path(raw)
        if path is None:
            return
        absolute = os.path.abspath(path)
        if not write and _under(absolute, self.python_roots):
            return
        real = os.path.realpath(absolute)
        if write:
            if _under(real, self.rw_roots):
                return
            # The temp dir and library caches are open for writing, but a deny root inside them (a runtime
            # dir under /tmp, as in tests) stays closed.
            if _under(real, self.deny_roots) or not _under(real, self.scratch_roots):
                raise self._blocked(path, True)
            return
        # Judged on both spellings: /proc/1/cwd/x resolves to a file under the app's own directory.
        if self._foreign_process_entry(real) or self._foreign_process_entry(absolute):
            raise self._blocked(path, False)
        if listing and real in self.blind_roots:
            raise self._blocked(path, False)
        if _under(real, self.readable):
            return
        if _under(real, self.deny_roots):
            raise self._blocked(path, False)

    def deny_process(self):
        raise PermissionError(
            errno.EACCES,
            "execute_code does not start other programs (subprocess, os.system, exec). Use bio_tools "
            "or the gagent_tools stubs for tools, and Python for everything else",
        )


def _open_writes(mode, flags):
    if isinstance(flags, int) and flags & _WRITE_FLAGS:
        return True
    return isinstance(mode, str) and any(ch in mode for ch in "wax+")


_installed = False


def install(policy_json):
    """Install the audit hook. The hook lives until the interpreter exits; a second call changes nothing."""
    global _installed
    if _installed:
        return
    policy = _Policy(json.loads(policy_json))
    try:
        # `import ctypes` itself dlopen()s the running interpreter (ctypes.pythonapi). Do it before the hook
        # exists, so that blocking ctypes.dlopen(None) later only stops a cell that asks for libc itself.
        import ctypes  # noqa: F401
    except ImportError:
        pass

    def hook(event, args):
        if event not in _WATCHED:
            return
        try:
            if event in _PROCESS_EVENTS:
                if not policy.allow_subprocess:
                    policy.deny_process()
                return
            if event == "open":
                mode = args[1] if len(args) > 1 else None
                flags = args[2] if len(args) > 2 else None
                policy.check(args[0], _open_writes(mode, flags))
                return
            if event == "ctypes.dlopen":
                name = args[0]
                if not policy.allow_subprocess and (
                    name is None or os.path.basename(str(name)).startswith(_LIBC_PREFIXES)
                ):
                    policy.deny_process()
                return
            for index in _READ_ARGS.get(event, ()):
                policy.check(args[index], False, listing=event in _LIST_EVENTS)
            for index in _WRITE_ARGS.get(event, ()):
                policy.check(args[index], True)
        except PermissionError:
            raise
        except Exception:  # noqa: BLE001 - a bug in the guard must not take the kernel down
            return

    sys.addaudithook(hook)
    _installed = True

"""Regression: ``tool_box.tools_impl`` must keep its submodule attributes.

``from .x import y`` binds ``y``; it never binds the *submodule* ``x``.  A
submodule attribute is set by the import system only at the moment that
submodule is first loaded, and that moment need not be this package's own
init: ``app.services.terminal.session_manager`` imports
``tool_box.bio_tools.remote_executor`` at module scope, which pulls in
``tool_box`` -> ``integration`` -> ``tool_registry`` -> ``.tools_impl`` while
``session_manager`` is still half-initialized.  The package init then raises on
its ``terminal_session`` line, CPython drops ``tool_box.tools_impl`` from
``sys.modules``, and the submodules it had already loaded stay cached.  The
next ``import tool_box.tools_impl`` builds a fresh package object that never
gets those cached submodules back as attributes -- so tests patching string
targets such as ``tool_box.tools_impl.plan_tools.resolve_plan_review_result``
fail with AttributeError (20 tests, measured 2026-10-11).

``_rebind_cached_submodules`` in ``tool_box/tools_impl/__init__.py`` closes
that hole.  The failing order cannot be reproduced in-process (pytest has
already imported the package by then), so the faithful probe runs in a
subprocess.  If the underlying cycle is ever fixed so the init stops raising,
this probe degrades into a plain import smoke check -- still the right thing
to assert, just no longer the landmine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Submodules the package init loads *before* the line that raises: exactly the
# attributes the bug used to erase.  terminal_session/execute_code are loaded
# after that line, so they stay present either way (controls).
_SUBMODULES_BEFORE_THE_RAISING_LINE = (
    "plan_tools",
    "code_executor",
    "web_search",
    "manuscript_writer",
    "result_interpreter",
    "phagescope",
    "graph_rag",
    "document_reader",
)
_SUBMODULES_AFTER_THE_RAISING_LINE = ("terminal_session", "execute_code")

_PROBE = """
from app.services.terminal.pty_backend import PTYBackend  # noqa: F401
import sys

import tool_box.tools_impl as package

names = {names!r}
missing = sorted(name for name in names if not hasattr(package, name))
print("MISSING=" + ",".join(missing))
wrong = sorted(
    name
    for name in names
    if name not in missing
    and getattr(package, name) is not sys.modules["tool_box.tools_impl." + name]
)
print("WRONG_IDENTITY=" + ",".join(wrong))
"""


def _run_probe() -> subprocess.CompletedProcess[str]:
    """Import the terminal backend first, then report on package attributes."""
    names = _SUBMODULES_BEFORE_THE_RAISING_LINE + _SUBMODULES_AFTER_THE_RAISING_LINE
    env = dict(os.environ)
    # Mirrors app/tests/conftest.py: a production APP_ENV activates the
    # fail-closed platform profile, which is not what this probe is about.
    env.setdefault("APP_ENV", "development")
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run(
        [sys.executable, "-c", _PROBE.format(names=names)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _probe_field(stdout: str, field: str) -> str:
    assert f"{field}=" in stdout, stdout
    return stdout.split(f"{field}=", 1)[1].splitlines()[0].strip()


def test_submodule_first_import_keeps_submodule_attributes() -> None:
    """A submodule import before the package must not erase its attributes."""
    result = _run_probe()

    assert result.returncode == 0, result.stderr
    missing = _probe_field(result.stdout, "MISSING")
    assert missing == "", f"tool_box.tools_impl lost submodule attributes: {missing}"
    wrong = _probe_field(result.stdout, "WRONG_IDENTITY")
    assert wrong == "", f"attributes bound to the wrong module object: {wrong}"


def test_every_cached_submodule_is_a_package_attribute() -> None:
    """The invariant the rebind establishes, checked on the live package."""
    import tool_box.tools_impl as package

    prefix = "tool_box.tools_impl."
    missing = sorted(
        name
        for name in sys.modules
        if name.startswith(prefix) and "." not in name[len(prefix) :] and not hasattr(package, name[len(prefix) :])
    )
    assert missing == [], f"cached submodules missing as attributes: {missing}"

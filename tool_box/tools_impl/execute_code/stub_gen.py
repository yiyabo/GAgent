"""Generate the ``gagent_tools.py`` stub module injected into code-mode kernels.

Unlike Hermes (hand-written ``_TOOL_STUBS`` table), signatures are generated
from each tool's JSON schema: native content (``tool_box.native_tool_schemas``)
first — that is what the LLM was offered — falling back to the tools_impl
``parameters_schema`` for tools without a native entry (e.g. lightrag_query).

Mapping: string→str, integer→int, number→float, boolean→bool, array→list,
object→dict; required params become positional, optional get ``= None``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}

_STUB_MODULE_NAME = "gagent_tools"

_TRANSPORT_HEADER = '''\
"""Auto-generated GAgent tool RPC stubs (code mode).

Every function here forwards to the host tool_box over a newline-delimited
JSON RPC socket. Return values are ALREADY-PARSED dicts — never json.loads()
them. Tool-side failures come back as {"error": ...} so the cell keeps
running; only transport failures raise RuntimeError.
"""
import json
import os
import socket
import threading
import time

_RPC_ENDPOINT = os.environ["GAGENT_RPC_ENDPOINT"]  # "tcp://127.0.0.1:<port>"
_RPC_TOKEN = os.environ.get("GAGENT_RPC_TOKEN", "")
_sock = None
_call_lock = threading.Lock()
_seq = [0]


def json_parse(text):
    """json.loads tolerant of a UTF-8 BOM and embedded control characters."""
    if isinstance(text, str) and text.startswith("\\ufeff"):
        text = text[1:]
    return json.loads(text, strict=False)


def retry(fn, max_attempts=3, delay=2):
    """Retry a callable with exponential backoff; re-raises the last error."""
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            last_err = exc
            if attempt < max_attempts - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_err


def _connect():
    global _sock
    if _sock is None:
        host, _, port = _RPC_ENDPOINT[len("tcp://"):].rpartition(":")
        _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _sock.connect((host or "127.0.0.1", int(port)))
        _sock.settimeout(300)
    return _sock


def _call(tool_name, args):
    """Send one tool call to the host and return the parsed result."""
    global _sock
    with _call_lock:
        _seq[0] += 1
        request = json.dumps({
            "id": _seq[0],
            "tool": tool_name,
            "args": args,
            "token": _RPC_TOKEN,
        }) + "\\n"
        last_exc = None
        for _attempt in range(2):
            try:
                conn = _connect()
                conn.sendall(request.encode())
                buf = b""
                while not buf.endswith(b"\\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        raise RuntimeError("host RPC connection closed")
                    buf += chunk
                break
            except (OSError, RuntimeError) as exc:
                last_exc = exc
                try:
                    if _sock is not None:
                        _sock.close()
                except OSError:
                    pass
                _sock = None
        else:
            raise last_exc
    response = json.loads(buf.decode().strip())
    if response.get("error") is not None:
        # Tool-side failure (allowlist/budget/handler error): cell continues.
        return {"error": response["error"]}
    result = response.get("result")
    for _ in range(2):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                break
    return result


'''


def _load_tool_content(name: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """(description, parameters) for *name*: native content first, impl fallback."""
    from tool_box.native_tool_schemas import NATIVE_TOOL_CONTENT

    content = NATIVE_TOOL_CONTENT.get(name)
    if content and isinstance(content.get("parameters"), dict):
        return str(content.get("description") or ""), content["parameters"]

    try:
        from tool_box import tools_impl

        tool_def = getattr(tools_impl, f"{name}_tool", None)
    except Exception:
        tool_def = None
    if isinstance(tool_def, dict):
        parameters = tool_def.get("parameters_schema") or tool_def.get("parameters")
        if isinstance(parameters, dict):
            return str(tool_def.get("description") or ""), parameters

    # Runtime-registry fallback (dynamically registered tools, e.g. in tests).
    try:
        from tool_box.tools import get_tool_registry

        registered = get_tool_registry().get_tool(name)
    except Exception:
        registered = None
    if registered is not None and isinstance(registered.parameters_schema, dict):
        return str(registered.description or ""), registered.parameters_schema
    return None


def _annotation(spec: Dict[str, Any]) -> str:
    return _TYPE_MAP.get(str(spec.get("type") or ""), "")


def build_signature(name: str, parameters: Dict[str, Any]) -> str:
    """Python parameter list for one tool's JSON schema."""
    properties = parameters.get("properties") or {}
    required_names = [key for key in parameters.get("required") or [] if key in properties]
    parts: List[str] = [f"{key}: {_annotation(properties[key])}" if _annotation(properties[key]) else key for key in required_names]
    for key, spec in properties.items():
        if key in required_names:
            continue
        annotation = _annotation(spec if isinstance(spec, dict) else {})
        parts.append(f"{key}: {annotation} = None" if annotation else f"{key}=None")
    return ", ".join(parts)


def signature_line(name: str) -> Optional[str]:
    """``name(sig)`` one-liner used in the tool description (None if unknown)."""
    content = _load_tool_content(name)
    if content is None:
        return None
    return f"{name}({build_signature(name, content[1])})"


def signature_lines(tool_names: List[str]) -> List[str]:
    return [line for line in (signature_line(name) for name in tool_names) if line]


def _docstring(description: str) -> str:
    first_line = " ".join(str(description or "").split())[:200]
    first_line = first_line.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""{first_line} Returns an already-parsed dict."""'


def generate_stub_module(tool_names: List[str]) -> str:
    """Full ``gagent_tools.py`` source for the given allowlist."""
    chunks = [_TRANSPORT_HEADER]
    for name in sorted(set(tool_names)):
        content = _load_tool_content(name)
        if content is None:
            continue
        description, parameters = content
        signature = build_signature(name, parameters)
        properties = parameters.get("properties") or {}
        args_expr = "{" + ", ".join(f"{key!r}: {key}" for key in properties) + "}"
        chunks.append(
            f"def {name}({signature}):\n"
            f"    {_docstring(description)}\n"
            f"    return _call({name!r}, {args_expr})\n"
        )
    return "\n".join(chunks) + "\n"

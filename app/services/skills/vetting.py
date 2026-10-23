"""Static vetting of agent skill directories (stdlib only, no code is executed).

``scan_skill_dir`` inspects a skill folder (``SKILL.md`` plus bundled files) and
reports findings a human reviewer should look at before the skill ships. It is a
heuristic filter, not a proof of safety: a clean result means "no known bad
pattern", never "reviewed by a human".

Result shape::

    {"ok": bool, "status": "passed" | "warnings" | "blocked",
     "findings": [{"severity": "block" | "warn" | "info", "rule": str,
                   "file": str, "line": int | None, "detail": str}],
     "stats": {"skill_md_lines": int, "chars": int, "files": int,
               "scripts": int, "external_urls": [str]}}

``ok`` is true when no finding has severity ``block``. Rule ids are stable; bump
``SCANNER_VERSION`` whenever a rule is added, removed or changes severity.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:  # PyYAML is a project dependency; degrade gracefully if it is missing.
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

SCANNER_VERSION = 1

MAX_SKILL_MD_LINES = 500
MAX_DESCRIPTION_CHARS = 1024
LOADER_DESCRIPTION_CHARS = 500  # SkillsLoader truncates descriptions to this length
MAX_NAME_CHARS = 64
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_FINDINGS_PER_RULE_FILE = 25

_SEVERITY_RANK = {"block": 0, "warn": 1, "info": 2}
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LICENSE_FILE_RE = re.compile(r"^(?:license|licence|copying|notice)(?:\.[a-z0-9]+)?$", re.I)
_SCRIPT_EXTS = {".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".cmd"}
_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
_SKIP_FILES = {".DS_Store"}
_BLOCK_SCALAR_INDICATORS = {"", ">", "|", ">-", "|-", ">+", "|+"}
_BINARY_EXEC_MAGICS = (
    b"\x7fELF",
    b"MZ",
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xfe\xed\xfa\xcf",
)


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------

def naive_frontmatter(content: str) -> Dict[str, str]:
    """Line parser identical to ``SkillsLoader._extract_frontmatter``.

    Kept as a copy so this module stays import-free; a test asserts equality
    with the real loader.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    payload: Dict[str, str] = {}
    index = 1
    while index < len(lines):
        line = lines[index]
        index += 1
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        text = value.strip()
        if re.fullmatch(r"[>|][+\-]?\d*", text):
            folded = text[0] == ">"
            block: List[str] = []
            while index < len(lines) and (not lines[index].strip() or lines[index][:1] in (" ", "\t")):
                block.append(lines[index].strip())
                index += 1
            text = (" " if folded else "\n").join(part for part in block if part)
        elif text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        elif text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        payload[key.strip()] = text
    return payload


def _split_frontmatter(content: str) -> Tuple[Optional[str], Optional[int]]:
    """Return (yaml_text, closing_line_index) or (None, None) when absent/unclosed."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), index
    return None, None


def _collapse(text: str) -> str:
    return " ".join(str(text).split())


# --------------------------------------------------------------------------
# Content rules
# --------------------------------------------------------------------------

def _rx(pattern: str, flags: int = re.I) -> "re.Pattern[str]":
    return re.compile(pattern, flags)


# (rule, severity, pattern, detail). Severity is lowered one step when the match
# sits in quoted context (code fence, blockquote, directly inside quotes).
_INJECTION_RULES: List[Tuple[str, str, "re.Pattern[str]", str]] = [
    (
        "inj.ignore_previous",
        "block",
        _rx(r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,30}\b(?:instructions?|prompts?|rules|directives)\b"),
        "tells the model to discard earlier instructions",
    ),
    (
        "inj.disregard_system",
        "block",
        _rx(r"\b(?:ignore|disregard|bypass|override|forget)\b[^.\n]{0,40}\b(?:system|developer)\s+(?:prompt|message|instructions?)\b"),
        "tells the model to bypass the system prompt",
    ),
    (
        "inj.conceal_from_user",
        "block",
        _rx(r"\b(?:do\s+not|don't|never|must\s+not)\s+(?:tell|inform|notify|alert)\s+the\s+user\b"),
        "instructs the model to hide something from the user",
    ),
    (
        "inj.silent_action",
        "block",
        _rx(r"\bwithout\s+(?:telling|informing|notifying|alerting)\s+the\s+user\b"),
        "instructs the model to act without the user knowing",
    ),
    (
        "inj.exfil_instruction",
        "block",
        _rx(r"\b(?:send|post|upload|transmit|forward|exfiltrate)\b[^.\n]{0,60}\b(?:conversation|chat\s+history|system\s+prompt|previous\s+messages)\b[^.\n]{0,60}https?://"),
        "instructs the model to send the conversation to an external URL",
    ),
    (
        "inj.silent_permission",
        "warn",
        _rx(r"\bwithout\s+(?:asking|requesting|waiting)\s+(?:for\s+)?(?:the\s+user(?:'s)?\s+)?(?:permission|consent|confirmation|approval)\b"),
        "tells the model to skip asking for permission",
    ),
    ("inj.role_override", "warn", _rx(r"\byou\s+are\s+now\b"), "role-override phrase"),
    ("inj.new_instructions", "warn", _rx(r"\bnew\s+(?:system\s+)?instructions?\s*:"), "instruction-replacement phrase"),
    (
        "inj.fake_system_tag",
        "warn",
        _rx(r"<\s*/?\s*(?:system|assistant|im_start|im_end)\s*>|\[\s*(?:SYSTEM|INST)\s*\]|<\|im_(?:start|end)\|>"),
        "chat-template or system-role marker inside skill text",
    ),
    ("inj.jailbreak", "warn", _rx(r"\b(?:jailbreak|DAN\s+mode|developer\s+mode\s+enabled)\b"), "jailbreak vocabulary"),
]

_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.S)
_INSTRUCTION_LIKE_RE = _rx(
    r"(?<![\w-])(?:ignore\s+(?:all|any|the|this|these|your|previous|prior|above|earlier)|disregard|"
    r"(?:new|these|the\s+following)\s+instructions?|you\s+(?:must|should|are|will)|do\s+not|don't|"
    r"system\s+prompt|execute|override|exfiltrat\w*|send\s+(?:it\s+|them\s+|this\s+)?to)(?![\w-])"
)

_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_HEX_BLOB_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}){64,}\b")

_NET_COMMAND = (
    r"(?:\bcurl\b|\bwget\b|\bnc\b|\bncat\b|\bscp\b|\brsync\b|\bsftp\b|"
    r"requests\.(?:post|put)|urllib\.request|urlopen|\bfetch\(|http\.client)"
)
_NET_COMMAND_RE = _rx(_NET_COMMAND)
_POST_RE = _rx(
    r"(?:\bcurl\b[^\n]*(?:\s-d\b|--data|\s-F\b|--form|-X\s*(?:POST|PUT)|--upload-file|\s-T\b)|"
    r"requests\.(?:post|put)|\.post\(|fetch\([^)]*method\s*:\s*['\"]POST|urlopen\([^)]*data=)"
)
_WEBHOOK_URL_RE = _rx(
    r"https?://[^\s'\")>]*?(?:hooks\.slack\.com|discord(?:app)?\.com/api/webhooks|webhook\.site|"
    r"requestbin\.[a-z]+|pipedream\.net|ngrok(?:-free)?\.(?:io|app|dev)|burpcollaborator\.net|"
    r"interact\.sh|oastify\.com|oast\.\w+|requestcatcher\.com|api\.telegram\.org/bot|pastebin\.com|"
    r"transfer\.sh|0x0\.st|ix\.io|hookb\.in|beeceptor\.com|canarytokens\.com)"
)
_SENSITIVE_STRONG_RE = _rx(
    r"(?:~|\$HOME|\$\{HOME\}|/home/[\w.-]+|/Users/[\w.-]+|/root)/\.(?:ssh|aws|gnupg|kube|netrc|npmrc|pypirc|git-credentials|docker/config\.json)\b"
    r"|\bid_(?:rsa|ed25519|ecdsa|dsa)\b|/etc/(?:passwd|shadow)\b"
)
_SENSITIVE_FILE_RE = _rx(r"(?<![\w-])\.env\b(?!\.(?:example|sample|template))|\bcredentials\.json\b|\bsecrets?\.(?:json|ya?ml)\b")
_READ_VERB_RE = _rx(r"(?:\bcat\b|\bless\b|\bmore\b|\bhead\b|\btail\b|\bcp\b|\bscp\b|\btar\b|\bzip\b|\bsource\b|\bopen\(|read_text\(|\bgrep\b)")

_SHELL_RULES: List[Tuple[str, str, "re.Pattern[str]", str]] = [
    (
        "shell.pipe_to_shell",
        "block",
        _rx(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b|\b(?:ba|z|da|k)?sh\s+<\(\s*(?:curl|wget)\b|\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?python3?\b"),
        "downloads content and pipes it into a shell/interpreter",
    ),
    (
        "shell.eval_download",
        "block",
        _rx(r"\b(?:eval|exec|source)\b[^\n]{0,40}(?:\$\(\s*(?:curl|wget)|<\(\s*(?:curl|wget)|`\s*(?:curl|wget))|"
            r"\b(?:ba|z)?sh\s+-c\s+[\"']?\$\(\s*(?:curl|wget)|"
            r"\b(?:exec|eval)\(\s*(?:requests\.get|urllib\.request\.urlopen|urlopen)\b"),
        "evaluates downloaded content",
    ),
    (
        "shell.base64_exec",
        "block",
        _rx(r"base64\s+(?:-d|-D|--decode)\b[^\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|\b(?:exec|eval)\(\s*base64\."),
        "decodes an obfuscated payload and executes it",
    ),
    (
        "shell.reverse_shell",
        "block",
        _rx(r"/dev/tcp/|\bnc(?:at)?\b[^\n]*\s-e\b|\bbash\s+-i\s*>&|\bmkfifo\s+\S+\s*;?\s*nc\b"),
        "reverse-shell pattern",
    ),
    (
        "shell.rm_rf_root",
        "block",
        _rx(r"\brm\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*[rR][A-Za-z]*\s+(?:-[A-Za-z]+\s+)*(?:--\s+)?(?:/\*?|~/?\*?|\$HOME/?\*?|\$\{HOME\}/?\*?)(?=[\s\"';)|&]|$)"),
        "recursive delete of the filesystem root or home directory",
    ),
    ("shell.sudo", "warn", _rx(r"\bsudo\b"), "uses sudo"),
    (
        "shell.chmod_777",
        "warn",
        _rx(r"\bchmod\s+(?:-[A-Za-z]+\s+)*(?:0?777|a\+rwx|ugo\+rwx)\b"),
        "world-writable permissions",
    ),
    (
        "code.dynamic_exec",
        "warn",
        _rx(r"(?<![\w.])(?:eval|exec)\s*\(", 0),
        "dynamic eval/exec call",
    ),
    (
        "net.git_clone",
        "warn",
        _rx(r"\bgit\s+clone\b[^\n]*(?:https?://|git@|ssh://)"),
        "clones a remote repository at run time (unvetted third-party content)",
    ),
    (
        "pkg.install",
        "info",
        _rx(r"\b(?:pip3?|uv\s+pip|conda|mamba|npm|npx|pnpm|yarn|apt(?:-get)?|brew|cargo|gem)\s+(?:install|add)\b"),
        "installs packages at run time",
    ),
]

# A shell match preceded on its line by a prohibition or bad-example marker is
# documentation, not an instruction: a warning drops to info (blocks never do).
_NEGATION_RE = _rx(r"\b(?:never|don't|do\s+not|must\s+not|avoid|bad|wrong|dangerous|unsafe|forbidden|anti-?pattern)\b|❌")

_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]}]+")
_URL_TRAILING = ".,;:!?"

# Unicode that is invisible or reorders text. ZWJ/ZWNJ are legitimate inside
# emoji and some scripts, so they are only reported between ASCII characters.
_HIDDEN_BLOCK = (
    {0x200B, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x180E, 0x00AD, 0x200E, 0x200F, 0x061C}
    | set(range(0x202A, 0x202F))
    | set(range(0x2066, 0x206A))
    | set(range(0xE0000, 0xE0080))
    | {0xFFF9, 0xFFFA, 0xFFFB}
)
_ZERO_WIDTH_JOINERS = {0x200C, 0x200D}


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _quoted_lines(lines: List[str]) -> List[bool]:
    """Per-line flag: inside a fenced code block or a blockquote."""
    flags: List[bool] = []
    fence: Optional[str] = None
    for line in lines:
        stripped = line.lstrip()
        marker = stripped[:3] if stripped[:3] in ("```", "~~~") else None
        if marker:
            if fence is None:
                fence = marker
                flags.append(True)
            elif marker == fence:
                fence = None
                flags.append(True)
            else:
                flags.append(True)
            continue
        flags.append(fence is not None or stripped.startswith(">"))
    return flags


def _lower(severity: str) -> str:
    return {"block": "warn", "warn": "info"}.get(severity, severity)


class _Collector:
    def __init__(self) -> None:
        self.findings: List[Dict[str, Any]] = []
        self._counts: Dict[Tuple[str, str], int] = {}
        self._seen: set = set()

    def add(self, severity: str, rule: str, file: str, line: Optional[int], detail: str) -> None:
        key = (rule, file, line, detail)
        if key in self._seen:
            return
        self._seen.add(key)
        bucket = (rule, file)
        count = self._counts.get(bucket, 0) + 1
        self._counts[bucket] = count
        if count > MAX_FINDINGS_PER_RULE_FILE:
            if count == MAX_FINDINGS_PER_RULE_FILE + 1:
                self.findings.append(
                    {
                        "severity": "info",
                        "rule": "scan.truncated",
                        "file": file,
                        "line": None,
                        "detail": f"further '{rule}' findings in this file are not listed",
                    }
                )
            return
        self.findings.append(
            {"severity": severity, "rule": rule, "file": file, "line": line, "detail": detail}
        )


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def _iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        relative_parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in relative_parts[:-1]):
            continue
        if path.name in _SKIP_FILES:
            continue
        if path.is_dir() and not path.is_symlink():
            continue
        yield path


def _check_frontmatter(skill_md: str, dir_name: str, out: _Collector) -> None:
    yaml_text, _closing = _split_frontmatter(skill_md)
    if yaml_text is None:
        if skill_md.splitlines()[:1] and skill_md.splitlines()[0].strip() == "---":
            out.add("block", "fm.unparseable", "SKILL.md", 1, "frontmatter is not closed by a '---' line")
        else:
            out.add("block", "fm.missing", "SKILL.md", 1, "SKILL.md has no YAML frontmatter")
        return

    naive = naive_frontmatter(skill_md)
    parsed: Any = None
    if yaml is None:  # pragma: no cover
        out.add("info", "fm.yaml_unavailable", "SKILL.md", 1, "PyYAML not installed; strict frontmatter check skipped")
    else:
        try:
            parsed = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            out.add("block", "fm.unparseable", "SKILL.md", 1, f"frontmatter is not valid YAML: {str(exc).splitlines()[0]}")
            return
        if not isinstance(parsed, dict):
            out.add("block", "fm.not_mapping", "SKILL.md", 1, "frontmatter must be a YAML mapping")
            return

    if parsed is None:
        name_value: Any = naive.get("name")
        desc_value: Any = naive.get("description")
    else:
        name_value = parsed.get("name")
        desc_value = parsed.get("description")

    # name
    if name_value is None or name_value == "":
        out.add("block", "name.missing", "SKILL.md", 1, "frontmatter has no 'name'")
    elif not isinstance(name_value, str):
        out.add("block", "name.invalid", "SKILL.md", 1, f"'name' must be a string, got {type(name_value).__name__}")
    else:
        if len(name_value) > MAX_NAME_CHARS or not _NAME_RE.match(name_value):
            out.add(
                "block",
                "name.invalid",
                "SKILL.md",
                1,
                f"name {name_value!r} must be 1-{MAX_NAME_CHARS} chars of lowercase letters, digits and single hyphens "
                "(no leading, trailing or consecutive hyphens)",
            )
        if name_value != dir_name:
            out.add("block", "name.dir_mismatch", "SKILL.md", 1, f"name {name_value!r} does not match directory {dir_name!r}")

    # description
    if desc_value is None or (isinstance(desc_value, str) and not desc_value.strip()):
        out.add("block", "desc.missing", "SKILL.md", 1, "frontmatter has no 'description'")
    elif not isinstance(desc_value, str):
        out.add("block", "desc.invalid", "SKILL.md", 1, f"'description' must be a string, got {type(desc_value).__name__}")
    else:
        length = len(desc_value)
        if length > MAX_DESCRIPTION_CHARS:
            out.add("block", "desc.too_long", "SKILL.md", 1, f"description is {length} chars (limit {MAX_DESCRIPTION_CHARS})")
        elif length > LOADER_DESCRIPTION_CHARS:
            out.add(
                "warn",
                "desc.loader_truncation",
                "SKILL.md",
                1,
                f"description is {length} chars; SkillsLoader keeps only the first {LOADER_DESCRIPTION_CHARS}",
            )

    # loader vs correct parse
    if parsed is not None:
        naive_name = naive.get("name", "").strip()
        if isinstance(name_value, str) and naive_name != name_value:
            out.add(
                "block",
                "fm.loader_mismatch",
                "SKILL.md",
                1,
                f"SkillsLoader reads name {naive_name!r} but YAML says {name_value!r} "
                "(nested or duplicate 'name' key?)",
            )
        if isinstance(desc_value, str) and desc_value.strip():
            naive_desc = naive.get("description", "")
            if _collapse(naive_desc) != _collapse(desc_value):
                severity = "block" if naive_desc.strip() in _BLOCK_SCALAR_INDICATORS else "warn"
                out.add(
                    severity,
                    "fm.loader_mismatch",
                    "SKILL.md",
                    1,
                    f"SkillsLoader reads description {naive_desc[:60]!r} which differs from the YAML value "
                    "(multi-line value, escapes, or nested 'description' key)",
                )


def _check_text(rel: str, text: str, out: _Collector, *, is_license: bool) -> List[str]:
    """Run content rules on one text file; return the URLs found."""
    lines = text.splitlines()

    # invisible / bidi unicode (applies to every text file, license included)
    for lineno, line in enumerate(lines, start=1):
        hidden: List[str] = []
        joiners: List[str] = []
        for pos, char in enumerate(line):
            code = ord(char)
            if code == 0xFEFF and not (lineno == 1 and pos == 0):
                hidden.append(f"U+{code:04X}")
            elif code in _HIDDEN_BLOCK:
                hidden.append(f"U+{code:04X}")
            elif code in _ZERO_WIDTH_JOINERS:
                before = line[pos - 1] if pos > 0 else ""
                after = line[pos + 1] if pos + 1 < len(line) else ""
                if (not before or before.isascii()) and (not after or after.isascii()):
                    joiners.append(f"U+{code:04X}")
            elif unicodedata.category(char) == "Cf" and code not in (0xFEFF, 0x200C, 0x200D):
                joiners.append(f"U+{code:04X}")
        if hidden:
            out.add("block", "unicode.invisible", rel, lineno, f"invisible or bidi control characters: {', '.join(sorted(set(hidden)))}")
        if joiners:
            out.add("warn", "unicode.format_char", rel, lineno, f"unusual format characters: {', '.join(sorted(set(joiners)))}")
    if text.startswith("﻿"):
        out.add("info", "unicode.bom", rel, 1, "file starts with a byte-order mark")

    if is_license:
        return []

    quoted = _quoted_lines(lines)

    def in_quote_marks(line: str, start: int) -> bool:
        return start > 0 and line[start - 1] in "\"'`“‘"

    # prompt injection
    for lineno, line in enumerate(lines, start=1):
        for rule, severity, pattern, detail in _INJECTION_RULES:
            for match in pattern.finditer(line):
                effective = severity
                if quoted[lineno - 1] or in_quote_marks(line, match.start()):
                    effective = _lower(severity)
                out.add(effective, rule, rel, lineno, f"{detail}: {match.group(0)[:80]!r}")

    # html comments with instruction-like text
    for match in _HTML_COMMENT_RE.finditer(text):
        body = match.group(1)
        hit = _INSTRUCTION_LIKE_RE.search(body)
        if hit:
            out.add(
                "warn",
                "html.comment_instruction",
                rel,
                _line_of(text, match.start()),
                f"HTML comment contains instruction-like text ({hit.group(0)!r}); comments are invisible when rendered",
            )

    # encoded blobs
    for lineno, line in enumerate(lines, start=1):
        for match in _BASE64_BLOB_RE.finditer(line):
            token = match.group(0)
            if sum(1 for check in (str.islower, str.isupper, str.isdigit) if any(check(c) for c in token)) >= 3:
                out.add("warn", "blob.base64", rel, lineno, f"{len(token)}-char base64-like run")
        for match in _HEX_BLOB_RE.finditer(line):
            out.add("warn", "blob.hex", rel, lineno, f"{len(match.group(0))}-char hex run")

    # shell / network / credential patterns
    for lineno, line in enumerate(lines, start=1):
        for rule, severity, pattern, detail in _SHELL_RULES:
            match = pattern.search(line)
            if match:
                effective = severity
                if severity == "warn" and _NEGATION_RE.search(line[: match.start()]):
                    effective = _lower(severity)
                out.add(effective, rule, rel, lineno, f"{detail}: {match.group(0)[:80]!r}")

        window = "\n".join(lines[max(0, lineno - 2) : lineno + 1])
        sensitive = _SENSITIVE_STRONG_RE.search(line)
        if not sensitive:
            file_hit = _SENSITIVE_FILE_RE.search(line)
            if file_hit and _READ_VERB_RE.search(line):
                sensitive = file_hit
        if sensitive:
            if _NET_COMMAND_RE.search(line) or (_NET_COMMAND_RE.search(window) and _POST_RE.search(window)):
                out.add("block", "cred.exfil", rel, lineno, f"credential path near a network command: {sensitive.group(0)!r}")
            else:
                out.add("warn", "cred.path", rel, lineno, f"touches a credential or secret location: {sensitive.group(0)!r}")

        webhook = _WEBHOOK_URL_RE.search(line)
        if webhook:
            if _POST_RE.search(window):
                out.add("block", "net.exfil_webhook", rel, lineno, f"posts to a webhook/collector URL: {webhook.group(0)[:100]!r}")
            else:
                out.add("warn", "net.webhook_url", rel, lineno, f"references a webhook/collector URL: {webhook.group(0)[:100]!r}")

    urls: List[str] = []
    for lineno, line in enumerate(lines, start=1):
        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(_URL_TRAILING)
            urls.append(url)
            out.add("info", "net.url", rel, lineno, url)
    return urls


def _classify_script(path: Path, data: bytes) -> bool:
    return path.suffix.lower() in _SCRIPT_EXTS or data.startswith(b"#!")


def scan_skill_dir(path: Any) -> Dict[str, Any]:
    """Scan one skill directory and return the vetting result (see module docstring)."""
    root = Path(path)
    out = _Collector()
    stats: Dict[str, Any] = {"skill_md_lines": 0, "chars": 0, "files": 0, "scripts": 0, "external_urls": []}

    if not root.is_dir():
        out.add("block", "skill.missing_dir", str(root), None, "not a directory")
        return _result(out, stats)
    root = root.resolve()
    dir_name = root.name

    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        out.add("block", "skill.missing_skill_md", "SKILL.md", None, "SKILL.md not found")
        skill_md = ""
    else:
        skill_md = skill_path.read_text(encoding="utf-8", errors="replace")
        stats["skill_md_lines"] = len(skill_md.splitlines())
        stats["chars"] = len(skill_md)
        if stats["skill_md_lines"] > MAX_SKILL_MD_LINES:
            out.add(
                "warn",
                "skill.too_long",
                "SKILL.md",
                None,
                f"SKILL.md has {stats['skill_md_lines']} lines (guideline {MAX_SKILL_MD_LINES}); move detail into references/",
            )
        _check_frontmatter(skill_md, dir_name, out)

    urls: List[str] = []
    script_count = 0
    file_count = 0
    for file_path in _iter_files(root):
        rel = file_path.relative_to(root).as_posix()
        file_count += 1

        if file_path.is_symlink():
            try:
                target = file_path.resolve()
                target.relative_to(root)
                out.add("warn", "file.symlink", rel, None, "symbolic link inside the skill directory")
            except (ValueError, OSError):
                out.add("block", "file.symlink_escape", rel, None, "symbolic link points outside the skill directory")
            continue

        try:
            size = file_path.stat().st_size
            data = file_path.read_bytes()[:MAX_SCAN_BYTES]
        except OSError as exc:
            out.add("warn", "file.unreadable", rel, None, f"cannot read file: {exc}")
            continue
        if size > MAX_SCAN_BYTES:
            out.add("warn", "file.oversize", rel, None, f"{size} bytes; only the first {MAX_SCAN_BYTES} were scanned")

        executable_bit = bool(file_path.stat().st_mode & 0o111)
        is_binary = b"\x00" in data[:8192]
        if is_binary:
            if data.startswith(_BINARY_EXEC_MAGICS):
                out.add("block", "file.binary_exec", rel, None, "bundled native executable")
            else:
                out.add("info", "file.binary", rel, None, f"binary file ({size} bytes) not scanned")
            continue

        text = data.decode("utf-8", errors="replace")
        is_script = _classify_script(file_path, data)
        if is_script:
            script_count += 1
            note = "executable bit set" if executable_bit else "not marked executable"
            out.add("warn", "script.bundled", rel, None, f"bundled script ({len(text.splitlines())} lines, {note}); review before enabling")
        elif executable_bit:
            out.add("warn", "file.exec_bit", rel, None, "executable bit set on a non-script file")

        if file_path.name == "config.json" and file_path.parent == root:
            try:
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    out.add("block", "config.invalid", rel, None, "config.json must contain a JSON object")
            except ValueError as exc:
                out.add("block", "config.invalid", rel, None, f"config.json is not valid JSON: {exc}")

        is_license = bool(_LICENSE_FILE_RE.match(file_path.name)) and file_path.parent == root
        urls.extend(_check_text(rel, text, out, is_license=is_license))

    stats["files"] = file_count
    stats["scripts"] = script_count
    stats["external_urls"] = sorted(set(urls))
    return _result(out, stats)


def _result(out: _Collector, stats: Dict[str, Any]) -> Dict[str, Any]:
    findings = sorted(
        out.findings,
        key=lambda f: (_SEVERITY_RANK[f["severity"]], f["file"], f["line"] or 0, f["rule"]),
    )
    has_block = any(f["severity"] == "block" for f in findings)
    has_warn = any(f["severity"] == "warn" for f in findings)
    status = "blocked" if has_block else ("warnings" if has_warn else "passed")
    return {"ok": not has_block, "status": status, "findings": findings, "stats": stats}

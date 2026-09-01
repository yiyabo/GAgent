"""Artifact produced events: schema, append-only log, and readers.

Every artifact a session produces (chat tool output, plan task output,
audit-repair promotion) is recorded exactly once in
``<session>/artifacts/events.jsonl``.  The log is the single fact source;
everything else (registry.json, deliverables/, manifest_latest.json) is a
derived view that can be rebuilt by replaying it.
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from uuid import uuid4

ARTIFACT_EVENT_SCHEMA_VERSION = 1
ARTIFACT_EVENT_TYPE = "artifact.produced"
EVENTS_LOG_NAME = "events.jsonl"

_PRODUCER_KINDS = {"chat_tool", "plan_task", "audit_repair"}
_PUBLISH_ROLES = {"normal", "final_report"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArtifactEvent:
    """One ``artifact.produced`` fact.

    ``alias`` is the semantic contract alias when known (``general.*`` /
    ``report.*`` ...); ``path_aliases`` keeps the legacy ``contract:`` path
    spellings, so both keys resolve to the same registry item and the two
    naming systems never have to be "matched" again.
    """

    session_id: str
    file_path: str
    alias: Optional[str] = None
    path_aliases: List[str] = field(default_factory=list)
    module: Optional[str] = None
    file_size: Optional[int] = None
    file_ext: str = ""
    file_sha256: Optional[str] = None
    producer_kind: str = "chat_tool"
    producer_tool: Optional[str] = None
    producer_plan_id: Optional[int] = None
    producer_task_id: Optional[int] = None
    producer_task_name: Optional[str] = None
    producer_job_id: Optional[str] = None
    contract_declared: bool = False
    contract_alias_source: Optional[str] = None
    publish_requested: bool = False
    publish_role: str = "normal"
    deliverable_path: Optional[str] = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    ts: str = field(default_factory=utc_now_iso)
    schema_version: int = ARTIFACT_EVENT_SCHEMA_VERSION
    type: str = ARTIFACT_EVENT_TYPE

    def identity(self) -> str:
        """Stable registry key: semantic alias first, then any recorded path
        alias, then the raw file path."""
        if self.alias:
            return f"alias::{self.alias}"
        for path_alias in self.path_aliases or []:
            if str(path_alias).strip():
                return f"alias::{path_alias}"
        return f"file::{self.file_path}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "event_id": self.event_id,
            "type": self.type,
            "ts": self.ts,
            "session_id": self.session_id,
            "alias": self.alias,
            "path_aliases": list(self.path_aliases or []),
            "file": {
                "path": self.file_path,
                "size": self.file_size,
                "ext": self.file_ext,
                "sha256": self.file_sha256,
            },
            "module": self.module,
            "producer": {
                "kind": self.producer_kind,
                "tool": self.producer_tool,
                "plan_id": self.producer_plan_id,
                "task_id": self.producer_task_id,
                "task_name": self.producer_task_name,
                "job_id": self.producer_job_id,
            },
            "contract": {
                "declared": bool(self.contract_declared),
                "alias_source": self.contract_alias_source,
            },
            "publish": {
                "requested": bool(self.publish_requested),
                "role": self.publish_role,
            },
            "deliverable_path": self.deliverable_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactEvent":
        if not isinstance(data, dict):
            raise ValueError("artifact event must be a JSON object")
        file_block = data.get("file") if isinstance(data.get("file"), dict) else {}
        producer = data.get("producer") if isinstance(data.get("producer"), dict) else {}
        contract = data.get("contract") if isinstance(data.get("contract"), dict) else {}
        publish = data.get("publish") if isinstance(data.get("publish"), dict) else {}
        role = str(publish.get("role") or "normal").strip().lower()
        if role not in _PUBLISH_ROLES:
            role = "normal"
        kind = str(producer.get("kind") or "chat_tool").strip().lower()
        if kind not in _PRODUCER_KINDS:
            kind = "chat_tool"
        path_aliases = data.get("path_aliases")
        if not isinstance(path_aliases, list):
            path_aliases = []
        return cls(
            session_id=str(data.get("session_id") or ""),
            file_path=str(file_block.get("path") or ""),
            alias=data.get("alias") if isinstance(data.get("alias"), str) else None,
            path_aliases=[str(item) for item in path_aliases if str(item).strip()],
            module=data.get("module") if isinstance(data.get("module"), str) else None,
            file_size=file_block.get("size") if isinstance(file_block.get("size"), int) else None,
            file_ext=str(file_block.get("ext") or ""),
            file_sha256=file_block.get("sha256") if isinstance(file_block.get("sha256"), str) else None,
            producer_kind=kind,
            producer_tool=producer.get("tool") if isinstance(producer.get("tool"), str) else None,
            producer_plan_id=producer.get("plan_id") if isinstance(producer.get("plan_id"), int) else None,
            producer_task_id=producer.get("task_id") if isinstance(producer.get("task_id"), int) else None,
            producer_task_name=producer.get("task_name") if isinstance(producer.get("task_name"), str) else None,
            producer_job_id=producer.get("job_id") if isinstance(producer.get("job_id"), str) else None,
            contract_declared=bool(contract.get("declared")),
            contract_alias_source=contract.get("alias_source") if isinstance(contract.get("alias_source"), str) else None,
            publish_requested=bool(publish.get("requested")),
            publish_role=role,
            deliverable_path=data.get("deliverable_path") if isinstance(data.get("deliverable_path"), str) else None,
            event_id=str(data.get("event_id") or uuid4().hex),
            ts=str(data.get("ts") or utc_now_iso()),
            schema_version=int(data.get("schema_version") or ARTIFACT_EVENT_SCHEMA_VERSION),
        )


def events_log_path(session_dir: Path) -> Path:
    return Path(session_dir) / "artifacts" / EVENTS_LOG_NAME


def append_events(session_dir: Path, events: List[ArtifactEvent]) -> Path:
    """Append events to the session log under an exclusive fcntl lock."""
    log_path = events_log_path(session_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = log_path.with_suffix(log_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                for event in events:
                    fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return log_path


def iter_events(session_dir: Path) -> Iterator[ArtifactEvent]:
    """Yield events in log order, tolerating truncated/corrupt lines."""
    log_path = events_log_path(session_dir)
    if not log_path.exists():
        return
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                yield ArtifactEvent.from_dict(data)
            except Exception:
                continue

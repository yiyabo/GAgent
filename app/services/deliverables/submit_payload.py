"""Deliverable-submit payload application (agent-explicit deliverables).

Extracted from ``publisher.py`` (cluster "deliverable_submit payload 应用 + 带原因
复制") per ``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.3. This
sibling owns ``raw_result["deliverable_submit"]``: the explicit artifact list an
agent submits, its per-artifact skip warnings, the conflict-strategy override and
the trusted-publish bypass used by plan task outputs.

Compatibility contract: the ``publisher`` facade re-exports every name defined
here and composes ``_SubmitPayloadMethods`` onto ``DeliverablePublisher``, so the
chat path (``action_handlers``) and the artifact stream
(``RegistryProjector.publish_artifact``) keep calling the publisher object
unchanged.

Every skip-reason and warning string is byte-identical to the pre-refactor
implementation (they surface in user-visible submit summaries).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config.deliverable_config import DeliverableConflictStrategy

from .file_ops import _KeepFirstConflict
from .policy import DELIVERABLE_SUBMIT_KEY


class _SubmitPayloadMethods:
    """``deliverable_submit`` payload methods mixed into ``DeliverablePublisher``."""

    @staticmethod
    def _extract_deliverable_submit(raw_result: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_result, dict):
            return None
        payload = raw_result.get(DELIVERABLE_SUBMIT_KEY)
        return payload if isinstance(payload, dict) else None

    def _copy_resolved_file_to_deliverables(
        self,
        *,
        file_path: Path,
        module: str,
        latest_root: Path,
        session_dir: Path,
        raw_result: Any,
        publish_status: str,
        now: str,
        previous_manifest: Dict[str, Any],
        from_explicit_submit: bool = False,
    ) -> Optional[Dict[str, Any]]:
        item, _ = self._copy_resolved_file_to_deliverables_with_reason(
            file_path=file_path,
            module=module,
            latest_root=latest_root,
            session_dir=session_dir,
            raw_result=raw_result,
            publish_status=publish_status,
            now=now,
            previous_manifest=previous_manifest,
            from_explicit_submit=from_explicit_submit,
        )
        return item

    def _copy_resolved_file_to_deliverables_with_reason(
        self,
        *,
        file_path: Path,
        module: str,
        latest_root: Path,
        session_dir: Path,
        raw_result: Any,
        publish_status: str,
        now: str,
        previous_manifest: Dict[str, Any],
        from_explicit_submit: bool = False,
        conflict_strategy: Optional[DeliverableConflictStrategy] = None,
        trusted: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        module_key = str(module or "").strip().lower()
        if module_key not in self._settings.modules:
            return None, f"unsupported module '{module_key}'"
        if trusted:
            # Trusted artifacts (plan task outputs arriving via the artifact
            # event stream) skip the filename-keyword whitelists; the noise /
            # intermediate-script safety filters still apply.
            if self._is_noise_artifact_file(file_path):
                return None, "artifact matches noise-file filter"
            if self._is_cc_intermediate_artifact(file_path):
                return None, "artifact is a Claude Code intermediate script"
        elif not self._should_publish_file(module_key, file_path):
            if self._is_noise_artifact_file(file_path):
                return None, "artifact matches noise-file filter"
            if self._is_cc_intermediate_artifact(file_path):
                return None, "artifact is a Claude Code intermediate script"
            if module_key == "code" and not self._is_allowed_code_file(file_path):
                return None, "artifact is not an allowed code file for the code module"
            if module_key == "docs" and not self._is_allowed_doc_file(file_path):
                return None, "artifact is not an allowed document for the docs module"
            return None, "artifact was filtered by deliverable publish policy"
        if module_key == "refs" and file_path.suffix.lower() == ".bib":
            refs_dir = latest_root / "refs"
            refs_dir.mkdir(parents=True, exist_ok=True)
            try:
                bib_text = file_path.read_text(encoding="utf-8")
            except Exception:
                bib_text = ""
            merged = self._paper_builder.merge_bib_entries(refs_dir=refs_dir, bib_text=bib_text)
            if merged is None:
                return None, "failed to merge bibliography entries"
            row = {
                "module": "refs",
                "path": str(merged.relative_to(latest_root)),
                "status": publish_status,
                "size": merged.stat().st_size,
                "updated_at": now,
                "source_path": self._to_project_relative(file_path),
            }
            if trusted:
                row["trusted_publish"] = True
            return row, None
        source_identity = self._source_identity(file_path)
        try:
            target = self._copy_to_module(
                source_path=file_path,
                module_dir=(latest_root / module_key),
                source_identity=source_identity,
                latest_root=latest_root,
                previous_manifest=previous_manifest,
                conflict_strategy=conflict_strategy,
            )
        except _KeepFirstConflict as exc:
            return None, exc.message
        except ValueError as exc:
            return None, str(exc)
        # Post-process: rewrite relative image paths in .md docs so they
        # resolve correctly under the deliverables/latest/ directory structure.
        # Front-end rejects paths containing ".." or "\", so we stage referenced
        # images into image_tabular/ and rewrite the markdown references.
        if module_key == "docs" and file_path.suffix.lower() == ".md":
            self._rewrite_md_image_paths_in_deliverable(
                target=target,
                section_source=file_path,
                latest_root=latest_root,
                session_dir=session_dir,
                previous_manifest=previous_manifest,
            )
        rel_path = str(target.relative_to(latest_root))
        checksum = self._sha256_file(target)
        row = {
            "module": module_key,
            "path": rel_path,
            "status": publish_status,
            "size": target.stat().st_size,
            "sha256": checksum,
            "updated_at": now,
            "source_path": self._to_project_relative(file_path),
        }
        if trusted:
            row["trusted_publish"] = True
        return row, None

    def _apply_deliverable_submit_payload(
        self,
        *,
        payload: Dict[str, Any],
        latest_root: Path,
        session_dir: Path,
        raw_result: Any,
        publish_status: str,
        now: str,
        previous_manifest: Dict[str, Any],
    ) -> Dict[str, Any]:
        artifacts = payload.get("artifacts")
        requested_count = len(artifacts) if isinstance(artifacts, list) else 0
        warnings: List[str] = []
        if not payload.get("publish", True):
            return {"items": [], "requested_count": requested_count, "warnings": warnings}
        if not isinstance(artifacts, list):
            return {"items": [], "requested_count": 0, "warnings": warnings}
        raw_strategy = str(payload.get("conflict_strategy") or "").strip().lower()
        conflict_strategy: Optional[DeliverableConflictStrategy] = None
        if raw_strategy:
            if raw_strategy in {"error", "rename", "keep_first"}:
                conflict_strategy = raw_strategy  # type: ignore[assignment]
            else:
                warnings.append(
                    f"invalid conflict_strategy '{raw_strategy}'; using global strategy"
                )
        out: List[Dict[str, Any]] = []
        for idx, row in enumerate(artifacts):
            if not isinstance(row, dict):
                warnings.append(f"artifact[{idx}] skipped: entry must be an object")
                continue
            raw_path = row.get("path")
            module_hint = row.get("module")
            trusted = bool(row.get("trusted"))
            if not isinstance(raw_path, str) or not raw_path.strip():
                warnings.append(f"artifact[{idx}] skipped: missing path")
                continue
            if not isinstance(module_hint, str) or not module_hint.strip():
                warnings.append(f"artifact[{idx}] skipped: missing module")
                continue
            raw_path_text = raw_path.strip()
            resolved = self._resolve_path(raw_path_text, session_dir=session_dir)
            if resolved is None:
                raw_candidate = Path(raw_path_text)
                if raw_candidate.is_absolute():
                    if raw_candidate.exists():
                        reason_text = "path is outside allowed deliverable sources"
                    else:
                        reason_text = "path does not exist"
                else:
                    candidate_paths = [session_dir / raw_candidate, self._project_root / raw_candidate]
                    if any(path.exists() for path in candidate_paths):
                        reason_text = "path is outside allowed deliverable sources"
                    else:
                        reason_text = "path does not exist"
                warnings.append(f"artifact[{idx}] skipped: {reason_text} ('{raw_path_text}')")
                continue
            if not resolved.exists():
                warnings.append(
                    f"artifact[{idx}] skipped: path '{raw_path_text}' does not exist"
                )
                continue
            if not resolved.is_file():
                warnings.append(
                    f"artifact[{idx}] skipped: path '{raw_path_text}' is not a file"
                )
                continue
            item, reason = self._copy_resolved_file_to_deliverables_with_reason(
                file_path=resolved,
                module=module_hint,
                latest_root=latest_root,
                session_dir=session_dir,
                raw_result=raw_result,
                publish_status=publish_status,
                now=now,
                previous_manifest=previous_manifest,
                from_explicit_submit=True,
                conflict_strategy=conflict_strategy,
                trusted=trusted,
            )
            if item is not None:
                out.append(item)
                continue
            warnings.append(
                f"artifact[{idx}] skipped: {reason or 'artifact was not publishable'} "
                f"({self._to_project_relative(resolved)})"
            )
        return {"items": out, "requested_count": requested_count, "warnings": warnings}


__all__ = ["_SubmitPayloadMethods"]

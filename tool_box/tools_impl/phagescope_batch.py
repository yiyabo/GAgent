"""PhageScope batch (submit/reconcile/retry) manifest orchestration helpers."""

import csv
import json
import re
import uuid
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

# batch_submit: models often omit modulelist; single-strain submit would fail with 400 otherwise.
_DEFAULT_BATCH_SUBMIT_MODULELIST: List[str] = ["quality"]


def _dedupe_phage_ids_preserve_order(ids: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in ids:
        t = str(item or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _normalize_phage_id_list(
    value: Any,
    *,
    file_path: Optional[str] = None,
) -> List[str]:
    """Coerce phage ID list from string (semicolon/comma/newline), list/tuple, or optional file path."""
    raw: List[str] = []
    if file_path:
        p = Path(str(file_path).strip()).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"phage_ids_file not found: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw.append(line)
    if value is not None:
        if isinstance(value, str):
            for part in re.split(r"[\s,;]+", value.replace("\n", ";")):
                part = part.strip()
                if part:
                    raw.append(part)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                s = str(item or "").strip()
                if s:
                    raw.append(s)
    return _dedupe_phage_ids_preserve_order(raw)


def _phage_accession_ids_from_result_payload(payload: Any) -> set:
    """Extract accession-like IDs from PhageScope ``result`` phage JSON."""
    if not isinstance(payload, dict):
        return set()
    rows = payload.get("results")
    if not isinstance(rows, list):
        return set()
    ids: set = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("Acession_ID", "Accession_ID", "phageid", "phage_id", "contig_id"):
            v = row.get(key)
            if isinstance(v, str) and v.strip():
                ids.add(v.strip())
    return ids


def _load_manifest_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def _save_manifest_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_taskid_from_submit_result(result: Dict[str, Any]) -> Optional[str]:
    """Parse remote task id from submit responses (only ``taskid`` / ``task_id`` fields, not status_code)."""

    def _coerce_taskid_value(value: Any) -> Optional[str]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            # Avoid confusing HTTP status codes (e.g. 200) with PhageScope task ids.
            if value < 1000:
                return None
            return str(value)
        if isinstance(value, str):
            s = value.strip()
            if s.isdigit() and int(s) >= 1000:
                return s
        return None

    data = result.get("data")
    if isinstance(data, dict):
        for key in ("taskid", "task_id", "remote_taskid"):
            if key in data:
                tid = _coerce_taskid_value(data.get(key))
                if tid:
                    return tid
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("taskid", "task_id", "remote_taskid"):
                if key in inner:
                    tid = _coerce_taskid_value(inner.get(key))
                    if tid:
                        return tid
        # Many PhageScope deployments return taskid under ``results``, not nested ``data``.
        results = data.get("results")
        if isinstance(results, dict):
            for key in ("taskid", "task_id", "remote_taskid", "id"):
                if key in results:
                    tid = _coerce_taskid_value(results.get(key))
                    if tid:
                        return tid
    return None


def _coerce_modulelist_for_manifest(modulelist: Any) -> Any:
    if modulelist is None:
        return None
    if isinstance(modulelist, (list, tuple)):
        return [str(x) for x in modulelist]
    if isinstance(modulelist, str):
        return modulelist
    if isinstance(modulelist, dict):
        return modulelist
    return str(modulelist)


async def _phagescope_batch_submit(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    userid: str,
    modulelist: Any,
    rundemo: str,
    analysistype: str,
    inputtype: str,
    sequence: Optional[str],
    file_path: Optional[str],
    comparedatabase: Optional[str],
    neednum: Optional[str],
    phage_ids: Any,
    phage_ids_file: Optional[str],
    batch_id: Optional[str],
    strategy: str,
    manifest_path_override: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    try:
        ids = _normalize_phage_id_list(phage_ids, file_path=phage_ids_file)
    except OSError as exc:
        return {"success": False, "status_code": 400, "action": "batch_submit", "error": str(exc)}
    if not ids:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_submit",
            "error": "phage_ids (or phage_ids_file) is required and must list at least one phage id",
        }
    if not modulelist:
        modulelist = list(facade._DEFAULT_BATCH_SUBMIT_MODULELIST)

    bid = str(batch_id or "").strip() or str(uuid.uuid4())
    manifests_dir, path_warning = facade._get_manifests_directory(session_id)
    if manifest_path_override:
        mpath = Path(str(manifest_path_override).strip()).expanduser().resolve()
    else:
        mpath = manifests_dir / f"{bid}.json"

    strat = str(strategy or "multi_one_task").strip().lower()
    if strat not in {"multi_one_task", "per_strain"}:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_submit",
            "error": "strategy must be multi_one_task or per_strain",
        }

    manifest: Dict[str, Any] = {
        "version": 1,
        "batch_id": bid,
        "strategy": strat,
        "requested_phage_ids": ids,
        "userid": userid,
        "modulelist": _coerce_modulelist_for_manifest(modulelist),
        "rundemo": str(rundemo).lower(),
        "analysistype": analysistype,
        "inputtype": inputtype,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(mpath),
        "primary_taskid": None,
        "per_strain_tasks": [],
        "retries": [],
        "last_reconcile": None,
    }

    if strat == "multi_one_task":
        joined = ";".join(ids)
        sub = await facade.phagescope_handler(
            action="submit",
            base_url=base_url,
            token=token,
            timeout=timeout,
            phageids=joined,
            userid=userid,
            modulelist=modulelist,
            rundemo=rundemo,
            analysistype=analysistype,
            inputtype=inputtype,
            sequence=sequence,
            file_path=file_path,
            session_id=session_id,
            comparedatabase=comparedatabase,
            neednum=neednum,
        )
        tid = _extract_taskid_from_submit_result(sub) if isinstance(sub, dict) else None
        manifest["primary_taskid"] = tid
        manifest["primary_submit"] = {"success": sub.get("success"), "status_code": sub.get("status_code")}
        _save_manifest_json(mpath, manifest)
        out: Dict[str, Any] = {
            "success": sub.get("success") is not False and bool(tid),
            "status_code": sub.get("status_code") or 200,
            "action": "batch_submit",
            "batch_id": bid,
            "strategy": strat,
            "requested_phage_ids": ids,
            "primary_taskid": tid,
            "manifest_path": str(mpath),
            "submit_result": sub,
        }
        if path_warning:
            out["warning"] = path_warning
        if not tid:
            out["success"] = False
            out["error"] = sub.get("error") or "could not extract taskid from submit response"
        return out

    per: List[Dict[str, Any]] = []
    all_ok = True
    for pid in ids:
        sub = await facade.phagescope_handler(
            action="submit",
            base_url=base_url,
            token=token,
            timeout=timeout,
            phageid=pid,
            userid=userid,
            modulelist=modulelist,
            rundemo=rundemo,
            analysistype=analysistype,
            inputtype=inputtype,
            sequence=sequence,
            file_path=file_path,
            session_id=session_id,
            comparedatabase=comparedatabase,
            neednum=neednum,
        )
        tid = _extract_taskid_from_submit_result(sub) if isinstance(sub, dict) else None
        if sub.get("success") is False or not tid:
            all_ok = False
        per.append({"phage_id": pid, "taskid": tid, "submit": sub})
    manifest["per_strain_tasks"] = per
    _save_manifest_json(mpath, manifest)
    out = {
        "success": all_ok,
        "status_code": 200 if all_ok else 207,
        "action": "batch_submit",
        "batch_id": bid,
        "strategy": strat,
        "requested_phage_ids": ids,
        "per_strain_tasks": per,
        "manifest_path": str(mpath),
    }
    if path_warning:
        out["warning"] = path_warning
    return out


async def _phagescope_batch_reconcile(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    batch_id: str,
    taskid: Optional[str],
    wait: bool,
    poll_interval: float,
    poll_timeout: float,
    manifest_path_override: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    bid = str(batch_id or "").strip()
    if not bid:
        return {"success": False, "status_code": 400, "action": "batch_reconcile", "error": "batch_id is required"}
    manifests_dir, path_warning = facade._get_manifests_directory(session_id)
    if manifest_path_override:
        mpath = Path(str(manifest_path_override).strip()).expanduser().resolve()
    else:
        mpath = manifests_dir / f"{bid}.json"
    try:
        manifest = _load_manifest_json(mpath)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"success": False, "status_code": 400, "action": "batch_reconcile", "error": str(exc)}

    remote_tid = str(taskid or "").strip() or str(manifest.get("primary_taskid") or "").strip()
    if not remote_tid:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_reconcile",
            "error": "taskid is required (or manifest must contain primary_taskid)",
        }

    td = await facade.phagescope_handler(
        action="task_detail",
        base_url=base_url,
        token=token,
        timeout=timeout,
        taskid=remote_tid,
        session_id=session_id,
    )
    if td.get("success") is False:
        return {
            "success": False,
            "status_code": td.get("status_code") or 400,
            "action": "batch_reconcile",
            "error": td.get("error") or "task_detail failed",
            "task_detail": td,
        }
    res_block = (td.get("data") or {}).get("results") if isinstance(td.get("data"), dict) else None
    remote_status = ""
    if isinstance(res_block, dict):
        remote_status = str(res_block.get("status") or "").strip()
    if remote_status.lower() != "success":
        return {
            "success": False,
            "status_code": 409,
            "action": "batch_reconcile",
            "error": f"remote task status is not Success (got {remote_status or 'unknown'})",
            "remote_status": remote_status,
            "task_detail": td,
        }

    pr = await facade.phagescope_handler(
        action="result",
        base_url=base_url,
        token=token,
        timeout=timeout,
        taskid=remote_tid,
        result_kind="phage",
        session_id=session_id,
        wait=wait,
        poll_interval=poll_interval,
        poll_timeout=poll_timeout,
    )
    observed: set = set()
    if pr.get("success"):
        pdata = pr.get("data")
        if isinstance(pdata, dict):
            observed = _phage_accession_ids_from_result_payload(pdata)
    requested_list = manifest.get("requested_phage_ids") or []
    if not isinstance(requested_list, list):
        requested_list = []
    requested_set = {str(x).strip() for x in requested_list if str(x).strip()}
    missing = sorted(requested_set - observed)
    reconcile_note: Optional[str] = None
    if not observed and pr.get("success"):
        reconcile_note = (
            "No rows in phage result; requested_vs_observed diff may be meaningless until result phage is populated."
        )
    elif missing and manifest.get("rundemo") in ("true", "1", "yes"):
        reconcile_note = (
            "Some requested IDs are missing from phage results; with rundemo=true the platform may substitute demo "
            "contigs so observed IDs may not match requested accessions."
        )

    manifest["last_reconcile"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "primary_taskid": remote_tid,
        "remote_status": remote_status,
        "observed_phage_ids": sorted(observed),
        "missing_phage_ids": missing,
        "reconcile_note": reconcile_note,
        "result_phage_success": pr.get("success"),
        "result_phage_status_code": pr.get("status_code"),
    }
    _save_manifest_json(mpath, manifest)

    out: Dict[str, Any] = {
        "success": True,
        "status_code": 200,
        "action": "batch_reconcile",
        "batch_id": bid,
        "primary_taskid": remote_tid,
        "manifest_path": str(mpath),
        "requested_phage_ids": sorted(requested_set),
        "observed_phage_ids": sorted(observed),
        "missing_phage_ids": missing,
        "reconcile_note": reconcile_note,
        "result_phage": pr,
    }
    if path_warning:
        out["warning"] = path_warning
    return out


async def _phagescope_batch_retry(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    userid: str,
    modulelist: Any,
    rundemo: str,
    analysistype: str,
    inputtype: str,
    sequence: Optional[str],
    file_path: Optional[str],
    comparedatabase: Optional[str],
    neednum: Optional[str],
    batch_id: str,
    retry_phage_ids: Any,
    manifest_path_override: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    bid = str(batch_id or "").strip()
    if not bid:
        return {"success": False, "status_code": 400, "action": "batch_retry", "error": "batch_id is required"}
    manifests_dir, path_warning = facade._get_manifests_directory(session_id)
    if manifest_path_override:
        mpath = Path(str(manifest_path_override).strip()).expanduser().resolve()
    else:
        mpath = manifests_dir / f"{bid}.json"
    try:
        manifest = _load_manifest_json(mpath)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"success": False, "status_code": 400, "action": "batch_retry", "error": str(exc)}

    to_retry: List[str]
    if retry_phage_ids is not None:
        to_retry = _normalize_phage_id_list(retry_phage_ids)
    else:
        lr = manifest.get("last_reconcile")
        if isinstance(lr, dict) and isinstance(lr.get("missing_phage_ids"), list):
            to_retry = [str(x).strip() for x in lr["missing_phage_ids"] if str(x).strip()]
        else:
            to_retry = []

    if not to_retry:
        return {
            "success": False,
            "status_code": 400,
            "action": "batch_retry",
            "error": "retry_phage_ids is required when last_reconcile.missing_phage_ids is empty or missing",
        }

    mod = modulelist if modulelist is not None else manifest.get("modulelist")
    uid = userid or str(manifest.get("userid") or "agent_default_user")
    rd = str(manifest.get("rundemo") or rundemo or "false").lower()
    atype = str(manifest.get("analysistype") or analysistype or "Annotation Pipline")
    itype = str(manifest.get("inputtype") or inputtype or "enter")

    retries = manifest.get("retries")
    if not isinstance(retries, list):
        retries = []

    results: List[Dict[str, Any]] = []
    for pid in to_retry:
        sub = await facade.phagescope_handler(
            action="submit",
            base_url=base_url,
            token=token,
            timeout=timeout,
            phageid=pid,
            userid=uid,
            modulelist=mod,
            rundemo=rd,
            analysistype=atype,
            inputtype=itype,
            sequence=sequence,
            file_path=file_path,
            session_id=session_id,
            comparedatabase=comparedatabase,
            neednum=neednum,
        )
        tid = _extract_taskid_from_submit_result(sub) if isinstance(sub, dict) else None
        entry = {
            "phage_id": pid,
            "taskid": tid,
            "success": sub.get("success") is not False and bool(tid),
            "at": datetime.now(timezone.utc).isoformat(),
            "submit_status_code": sub.get("status_code"),
        }
        if sub.get("success") is False or not tid:
            entry["error"] = sub.get("error") or "submit failed"
        retries.append(entry)
        results.append({"phage_id": pid, "taskid": tid, "submit": sub})

    manifest["retries"] = retries
    manifest["last_batch_retry_at"] = datetime.now(timezone.utc).isoformat()
    _save_manifest_json(mpath, manifest)

    slice_entries = retries[-len(to_retry) :] if retries else []
    all_ok = bool(slice_entries) and all(bool(e.get("success")) for e in slice_entries)
    out: Dict[str, Any] = {
        "success": all_ok,
        "status_code": 200 if all_ok else 207,
        "action": "batch_retry",
        "batch_id": bid,
        "manifest_path": str(mpath),
        "retry_phage_ids": to_retry,
        "retry_results": results,
    }
    if path_warning:
        out["warning"] = path_warning
    return out


def _results_payload_to_tsv_text(payload: Dict[str, Any]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("results")
    if rows is None:
        return None

    normalized_rows: List[Any]
    if isinstance(rows, list):
        normalized_rows = rows
    else:
        normalized_rows = [rows]

    buffer = StringIO()
    if normalized_rows and all(isinstance(item, dict) for item in normalized_rows):
        headers: List[str] = []
        for row in normalized_rows:
            for key in row.keys():
                key_str = str(key)
                if key_str not in headers:
                    headers.append(key_str)
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        for row in normalized_rows:
            writer.writerow({key: row.get(key, "") for key in headers})
        return buffer.getvalue()

    # Fallback for non-dict rows
    writer = csv.writer(buffer, delimiter="\t")
    writer.writerow(["value"])
    for row in normalized_rows:
        writer.writerow([row])
    return buffer.getvalue()


__all__ = [
    "_DEFAULT_BATCH_SUBMIT_MODULELIST", "_dedupe_phage_ids_preserve_order", "_normalize_phage_id_list",
    "_phage_accession_ids_from_result_payload", "_load_manifest_json", "_save_manifest_json",
    "_extract_taskid_from_submit_result", "_coerce_modulelist_for_manifest", "_phagescope_batch_submit",
    "_phagescope_batch_reconcile", "_phagescope_batch_retry", "_results_payload_to_tsv_text",
]

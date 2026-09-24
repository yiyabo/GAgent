"""PhageScope batch action branches (batch_submit / batch_reconcile / batch_retry)."""

from typing import Any, Dict, Optional


async def _action_batch_submit(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    userid: Optional[str],
    modulelist: Optional[Any],
    rundemo: str,
    analysistype: str,
    inputtype: str,
    sequence: Optional[str],
    file_path: Optional[str],
    comparedatabase: Optional[str],
    neednum: Optional[str],
    phage_ids: Optional[Any],
    phageids: Optional[str],
    phageid: Optional[str],
    phage_ids_file: Optional[str],
    batch_id: Optional[str],
    strategy: str,
    manifest_path: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    # Submit-style names (phageids / phage_id) are common; batch_submit previously only read
    # phage_ids / phage_ids_file, so calls with phageids only looked "empty" and failed validation.
    effective_phage_ids = phage_ids
    if effective_phage_ids is None and phageids is not None and str(phageids).strip():
        effective_phage_ids = phageids
    if effective_phage_ids is None and phageid is not None and str(phageid).strip():
        effective_phage_ids = phageid
    return await facade._phagescope_batch_submit(
        base_url=base_url,
        token=token,
        timeout=timeout,
        session_id=session_id,
        userid=userid,
        modulelist=modulelist,
        rundemo=rundemo,
        analysistype=analysistype,
        inputtype=inputtype,
        sequence=sequence,
        file_path=file_path,
        comparedatabase=comparedatabase,
        neednum=neednum,
        phage_ids=effective_phage_ids,
        phage_ids_file=phage_ids_file,
        batch_id=batch_id,
        strategy=strategy,
        manifest_path_override=manifest_path,
    )


async def _action_batch_reconcile(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    batch_id: Optional[str],
    taskid: Optional[str],
    wait: bool,
    poll_interval: float,
    poll_timeout: float,
    manifest_path: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    return await facade._phagescope_batch_reconcile(
        base_url=base_url,
        token=token,
        timeout=timeout,
        session_id=session_id,
        batch_id=str(batch_id or "").strip(),
        taskid=taskid,
        wait=wait,
        poll_interval=poll_interval,
        poll_timeout=poll_timeout,
        manifest_path_override=manifest_path,
    )


async def _action_batch_retry(
    *,
    base_url: str,
    token: Optional[str],
    timeout: float,
    session_id: Optional[str],
    userid: Optional[str],
    modulelist: Optional[Any],
    rundemo: str,
    analysistype: str,
    inputtype: str,
    sequence: Optional[str],
    file_path: Optional[str],
    comparedatabase: Optional[str],
    neednum: Optional[str],
    batch_id: Optional[str],
    retry_phage_ids: Optional[Any],
    manifest_path: Optional[str],
) -> Dict[str, Any]:
    from . import phagescope as facade

    return await facade._phagescope_batch_retry(
        base_url=base_url,
        token=token,
        timeout=timeout,
        session_id=session_id,
        userid=userid,
        modulelist=modulelist,
        rundemo=rundemo,
        analysistype=analysistype,
        inputtype=inputtype,
        sequence=sequence,
        file_path=file_path,
        comparedatabase=comparedatabase,
        neednum=neednum,
        batch_id=str(batch_id or "").strip(),
        retry_phage_ids=retry_phage_ids,
        manifest_path_override=manifest_path,
    )


__all__ = [
    "_action_batch_reconcile",
    "_action_batch_retry",
    "_action_batch_submit",
]

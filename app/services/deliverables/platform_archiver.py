from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def archive_session_to_platform(session_id: str) -> bool:
    logger.info(
        "[PlatformArchiver] Skipped local filesystem archive for session %s; "
        "platform artifact API is required",
        str(session_id or "").strip() or "<empty>",
    )
    return False

"""


, . 
"""

import logging
from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List

from ..repository.tasks import default_repo
from ..utils import split_prefix

router = APIRouter(tags=["smart-assembly"])
logger = logging.getLogger(__name__)


@router.get("/smart-assemble/{title}")
def smart_assemble_plan(title: str):
    """plan, support"""

    logger.info(f"please: title='{title}'")

    from ..database_pool import get_db
    with get_db() as conn:
        from ..utils import plan_prefix
        prefix = plan_prefix(title)

        rows = conn.execute(
            """
            SELECT t.id, t.name, o.content, t.priority
            FROM tasks t
            JOIN task_outputs o ON o.task_id = t.id
            WHERE t.name LIKE ? AND o.content IS NOT NULL AND o.content != ''
            ORDER BY t.priority ASC, t.id ASC
            """,
            (prefix + "%",),
        ).fetchall()

        logger.info(f"result: {len(rows)} task")

        if not rows:
            fuzzy_pattern = f"%{title}%"
            rows = conn.execute(
                """
                SELECT t.id, t.name, o.content, t.priority
                FROM tasks t
                JOIN task_outputs o ON o.task_id = t.id
                WHERE t.name LIKE ? AND o.content IS NOT NULL AND o.content != ''
                ORDER BY t.priority ASC, t.id ASC
                """,
                (fuzzy_pattern,),
            ).fetchall()
            logger.info(f"result: {len(rows)} task")

        if not rows and title:
            keywords = [word for word in title.split() if len(word) > 1]
            if keywords:
                main_keyword = keywords[0]
                keyword_pattern = f"%{main_keyword}%"
                rows = conn.execute(
                    """
                    SELECT t.id, t.name, o.content, t.priority
                    FROM tasks t
                    JOIN task_outputs o ON o.task_id = t.id
                    WHERE t.name LIKE ? AND o.content IS NOT NULL AND o.content != ''
                    ORDER BY t.priority ASC, t.id ASC
                    """,
                    (keyword_pattern,),
                ).fetchall()
                logger.info(f"result: {len(rows)} task (: '{main_keyword}')")

        if not rows:
            rows = conn.execute(
                """
                SELECT t.id, t.name, o.content, t.priority
                FROM tasks t
                JOIN task_outputs o ON o.task_id = t.id
                WHERE o.content IS NOT NULL AND o.content != ''
                ORDER BY t.id DESC
                LIMIT 10
                """,
            ).fetchall()
            logger.info(f"taskresult: {len(rows)} task")

    if not rows:
        logger.warning(f" '{title}' task")
        return {"title": title, "sections": [], "combined": "", "error": "task"}

    sections = []
    combined_parts = []

    for row in rows:
        try:
            task_id, name, content, priority = row[0], row[1], row[2], row[3]
        except:
            task_id, name, content, priority = row["id"], row["name"], row["content"], row["priority"]

        _, short = split_prefix(name)

        sections.append({
            "name": short,
            "content": content,
            "task_id": task_id,
            "priority": priority
        })
        combined_parts.append(f"## {short}\n\n{content}")

    result = {
        "title": title,
        "sections": sections,
        "combined": "\n\n".join(combined_parts),
        "total_sections": len(sections),
        "match_strategy": ""
    }

    logger.info(f"completed: {len(sections)} ")
    return result

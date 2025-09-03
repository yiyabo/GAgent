"""
智能组装路由

提供更智能的报告组装功能，解决标题匹配问题。
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
    """智能组装计划报告，支持多种匹配策略"""
    
    logger.info(f"智能组装请求: title='{title}'")
    
    from ..database_pool import get_db
    with get_db() as conn:
        # 策略1：精确前缀匹配
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
        
        logger.info(f"精确匹配结果: {len(rows)} 个任务")
        
        # 策略2：模糊匹配
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
            logger.info(f"模糊匹配结果: {len(rows)} 个任务")
        
        # 策略3：关键词匹配
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
                logger.info(f"关键词匹配结果: {len(rows)} 个任务 (关键词: '{main_keyword}')")
        
        # 策略4：最近的相关任务
        if not rows:
            # 查找最近创建的有内容的任务
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
            logger.info(f"最近任务匹配结果: {len(rows)} 个任务")
    
    if not rows:
        logger.warning(f"没有找到与标题 '{title}' 匹配的任务")
        return {"title": title, "sections": [], "combined": "", "error": "没有找到匹配的任务"}
    
    # 构建结果
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
        "match_strategy": "智能匹配"
    }
    
    logger.info(f"智能组装完成: {len(sections)} 个章节")
    return result

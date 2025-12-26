"""
Session Management Routes

This module provides endpoints for managing chat sessions and session-scoped tasks.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from ..database_pool import get_db
from ..services.upload_storage import delete_session_storage

logger = logging.getLogger(__name__)

router = APIRouter()


class SessionCreateRequest(BaseModel):
    name: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    is_active: bool
    task_count: int = 0
    current_plan_title: Optional[str] = None
    current_task_id: Optional[int] = None
    current_task_name: Optional[str] = None


class SessionContextUpdate(BaseModel):
    plan_title: Optional[str] = None
    task_id: Optional[int] = None
    task_name: Optional[str] = None


@router.post("/sessions", response_model=SessionResponse)
async def create_session(request: SessionCreateRequest):
    """Create a new chat session"""
    try:
        session_id = str(uuid.uuid4())
        session_name = request.name or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        with get_db() as conn:
            conn.execute(
                """INSERT INTO chat_sessions (id, name, created_at, updated_at, is_active) 
                   VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)""",
                (session_id, session_name)
            )
            
            # Get the created session
            cursor = conn.execute(
                "SELECT id, name, created_at, updated_at, is_active, current_plan_title, current_task_id, current_task_name FROM chat_sessions WHERE id = ?",
                (session_id,)
            )
            session = cursor.fetchone()
            
            if session:
                return SessionResponse(
                    id=session[0],
                    name=session[1],
                    created_at=session[2],
                    updated_at=session[3], 
                    is_active=bool(session[4]),
                    task_count=0,
                    current_plan_title=session[5],
                    current_task_id=session[6],
                    current_task_name=session[7],
                )
                
        raise HTTPException(status_code=500, detail="Failed to create session")
        
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions", response_model=List[SessionResponse])
async def get_sessions():
    """Get all chat sessions with task counts"""
    try:
        with get_db() as conn:
            cursor = conn.execute("""
                SELECT s.id, s.name, s.created_at, s.updated_at, s.is_active,
                       s.current_plan_title, s.current_task_id, s.current_task_name,
                       COALESCE(COUNT(t.id), 0) as task_count
                FROM chat_sessions s
                LEFT JOIN tasks t ON s.id = t.session_id
                GROUP BY s.id, s.name, s.created_at, s.updated_at, s.is_active
                ORDER BY s.updated_at DESC
            """)
            
            sessions = []
            for row in cursor.fetchall():
                sessions.append(SessionResponse(
                    id=row[0],
                    name=row[1], 
                    created_at=row[2],
                    updated_at=row[3],
                    is_active=bool(row[4]),
                    task_count=row[8],
                    current_plan_title=row[5],
                    current_task_id=row[6],
                    current_task_name=row[7],
                ))
                
            return sessions
            
    except Exception as e:
        logger.error(f"Error getting sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.head("/sessions/{session_id}")
async def head_session(session_id: str):
    """Check if a session exists (returns only headers, no body)"""
    try:
        with get_db() as conn:
            cursor = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
            if cursor.fetchone():
                return Response(status_code=200)
            raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """Get a specific session with task count"""
    try:
        with get_db() as conn:
            cursor = conn.execute("""
                SELECT s.id, s.name, s.created_at, s.updated_at, s.is_active,
                       s.current_plan_title, s.current_task_id, s.current_task_name,
                       COALESCE(COUNT(t.id), 0) as task_count
                FROM chat_sessions s
                LEFT JOIN tasks t ON s.id = t.session_id
                WHERE s.id = ?
                GROUP BY s.id, s.name, s.created_at, s.updated_at, s.is_active
            """, (session_id,))

            session = cursor.fetchone()
            if session:
                return SessionResponse(
                    id=session[0],
                    name=session[1],
                    created_at=session[2],
                    updated_at=session[3],
                    is_active=bool(session[4]),
                    task_count=session[8],
                    current_plan_title=session[5],
                    current_task_id=session[6],
                    current_task_name=session[7],
                )

        raise HTTPException(status_code=404, detail="Session not found")

    except Exception as e:
        logger.error(f"Error getting session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its tasks"""
    try:
        with get_db() as conn:
            # Check if session exists
            cursor = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Session not found")
            
            # Delete all tasks in the session (CASCADE will handle related data)
            task_cursor = conn.execute("DELETE FROM tasks WHERE session_id = ?", (session_id,))
            tasks_deleted = task_cursor.rowcount
            
            # Delete the session
            session_cursor = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            
            try:
                if delete_session_storage(session_id):
                    logger.info("Deleted session uploads for %s", session_id)
            except Exception as e:
                logger.warning("Failed to delete session uploads for %s: %s", session_id, e)

            return {
                "message": f"Session deleted successfully",
                "session_id": session_id,
                "tasks_deleted": tasks_deleted
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/sessions/{session_id}/context", response_model=SessionResponse)
async def update_session_context(session_id: str, context: SessionContextUpdate):
    """Update session context (current plan/task)."""
    try:
        updates = []
        params: List[Any] = []
        if context.plan_title is not None:
            updates.append("current_plan_title = ?")
            params.append(context.plan_title)
        if context.task_id is not None:
            updates.append("current_task_id = ?")
            params.append(context.task_id)
        if context.task_name is not None:
            updates.append("current_task_name = ?")
            params.append(context.task_name)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            with get_db() as conn:
                conn.execute(
                    f"UPDATE chat_sessions SET {', '.join(updates)} WHERE id = ?",
                    (*params, session_id)
                )
                conn.commit()

        return await get_session(session_id)

    except Exception as e:
        logger.error(f"Error updating session context for {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/tasks")
async def get_session_tasks(session_id: str):
    """Get all tasks for a specific session"""
    try:
        with get_db() as conn:
            # Check if session exists
            cursor = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (session_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Session not found")
            
            # Get tasks for the session
            cursor = conn.execute("""
                SELECT id, name, status, priority, parent_id, path, depth, task_type, session_id
                FROM tasks 
                WHERE session_id = ? 
                ORDER BY priority ASC, id DESC
            """, (session_id,))
            
            tasks = []
            for row in cursor.fetchall():
                tasks.append({
                    "id": row[0],
                    "name": row[1],
                    "status": row[2],
                    "priority": row[3],
                    "parent_id": row[4],
                    "path": row[5],
                    "depth": row[6], 
                    "task_type": row[7],
                    "session_id": row[8]
                })
                
            return {
                "session_id": session_id,
                "tasks": tasks,
                "task_count": len(tasks)
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting tasks for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

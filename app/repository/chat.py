from ..database import get_db
from typing import List, Dict, Any

def create_conversation(plan_id: int, title: str) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO conversations (plan_id, title) VALUES (?, ?)',
            (plan_id, title)
        )
        conn.commit()
        conversation_id = cursor.lastrowid
        return get_conversation(conversation_id)

def get_conversations_for_plan(plan_id: int) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, plan_id, title, created_at FROM conversations WHERE plan_id = ? ORDER BY created_at DESC',
            (plan_id,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_conversation(conversation_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, plan_id, title, created_at FROM conversations WHERE id = ?',
            (conversation_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def create_message(conversation_id: int, sender: str, text: str) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO messages (conversation_id, sender, text) VALUES (?, ?, ?)',
            (conversation_id, sender, text)
        )
        conn.commit()
        message_id = cursor.lastrowid
        cursor.execute(
            'SELECT id, conversation_id, sender, text, created_at FROM messages WHERE id = ?',
            (message_id,)
        )
        row = cursor.fetchone()
        return dict(row)

def get_messages_for_conversation(conversation_id: int) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, conversation_id, sender, text, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at ASC',
            (conversation_id,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

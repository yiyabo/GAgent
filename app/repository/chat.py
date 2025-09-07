from ..database import get_db
from typing import List, Dict, Any

def create_conversation(title: str) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO conversations (title) VALUES (?)',
            (title,)
        )
        conn.commit()
        conversation_id = cursor.lastrowid
        return get_conversation(conversation_id)

def get_all_conversations() -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, title, created_at FROM conversations ORDER BY created_at DESC'
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_conversation(conversation_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, title, created_at FROM conversations WHERE id = ?',
            (conversation_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def update_conversation(conversation_id: int, title: str) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE conversations SET title = ? WHERE id = ?',
            (title, conversation_id)
        )
        conn.commit()
        if cursor.rowcount > 0:
            return get_conversation(conversation_id)
        return None

def delete_conversation(conversation_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        # 由于外键约束，messages会自动删除
        cursor.execute(
            'DELETE FROM conversations WHERE id = ?',
            (conversation_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

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

def get_conversation_with_messages(conversation_id: int) -> Dict[str, Any]:
    conversation = get_conversation(conversation_id)
    if conversation:
        messages = get_messages_for_conversation(conversation_id)
        conversation['messages'] = messages
        return conversation
    return None

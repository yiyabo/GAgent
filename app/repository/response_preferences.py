"""Owner-scoped defaults; session settings are a separate, frozen choice."""
from app.services.response_style import ResponsePreferences, preferences_from_settings


def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS user_response_preferences (
        owner_id TEXT PRIMARY KEY,
        response_style TEXT NOT NULL,
        response_length TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")


def get_preferences(conn, owner_id: str) -> ResponsePreferences:
    # Older databases and minimal test stores may not have the additive table yet.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_response_preferences'").fetchone():
        return ResponsePreferences()
    row = conn.execute(
        "SELECT response_style, response_length FROM user_response_preferences WHERE owner_id=?", (owner_id,),
    ).fetchone()
    return preferences_from_settings(dict(row) if row else None)


def save_preferences(conn, owner_id: str, preferences: ResponsePreferences) -> None:
    conn.execute("""INSERT INTO user_response_preferences (owner_id, response_style, response_length)
        VALUES (?, ?, ?) ON CONFLICT(owner_id) DO UPDATE SET
        response_style=excluded.response_style, response_length=excluded.response_length,
        updated_at=CURRENT_TIMESTAMP""", (owner_id, preferences.response_style, preferences.response_length))

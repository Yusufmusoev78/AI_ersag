import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "assistant.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS file_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        if "title" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
        conn.commit()
    finally:
        conn.close()


def start_conversation() -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO conversations (started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def save_message(conversation_id: int, role: str, content) -> None:
    if not isinstance(content, str):
        content = json.dumps(content, default=lambda o: getattr(o, "__dict__", str(o)))
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def log_operation(tool_name: str, file_path: str, detail: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO file_operations (tool_name, file_path, detail, created_at) VALUES (?, ?, ?, ?)",
            (tool_name, file_path, detail, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def list_conversations(limit: int = 50):
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT c.id, c.started_at, c.title,
                   (SELECT content FROM messages m
                    WHERE m.conversation_id = c.id AND m.role = 'user'
                    ORDER BY m.id ASC LIMIT 1) AS first_message
            FROM conversations c
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def rename_conversation(conversation_id: int, title: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title.strip() or None, conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_conversation(conversation_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
    finally:
        conn.close()


def get_conversation_messages(conversation_id: int):
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def recent_operations(limit: int = 20):
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT tool_name, file_path, detail, created_at FROM file_operations "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()

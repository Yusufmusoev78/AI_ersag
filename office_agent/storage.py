import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "assistant.db"
PBKDF2_ITERATIONS = 200_000


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

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT,
                password_hash TEXT,
                auth_provider TEXT NOT NULL DEFAULT 'local',
                avatar_color TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        if "title" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
        if "user_id" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN user_id INTEGER")
        conn.commit()
    finally:
        conn.close()


def start_conversation(user_id: int | None = None) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO conversations (started_at, user_id) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users & sessions
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return secrets.compare_digest(digest.hex(), digest_hex)


def _user_row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "auth_provider": row[3],
        "avatar_color": row[4],
    }


def create_user(username: str, password: str | None, email: str = "", auth_provider: str = "local") -> dict:
    conn = _connect()
    try:
        password_hash = hash_password(password) if password else None
        cur = conn.execute(
            "INSERT INTO users (username, email, password_hash, auth_provider, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, email, password_hash, auth_provider, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"id": cur.lastrowid, "username": username, "email": email, "auth_provider": auth_provider, "avatar_color": None}
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, username, email, auth_provider, avatar_color, password_hash "
            "FROM users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        user = _user_row_to_dict(row)
        user["password_hash"] = row[5]
        return user
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, username, email, auth_provider, avatar_color, password_hash "
            "FROM users WHERE email = ?",
            (email,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        user = _user_row_to_dict(row)
        user["password_hash"] = row[5]
        return user
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, username, email, auth_provider, avatar_color FROM users WHERE id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        return _user_row_to_dict(row) if row else None
    finally:
        conn.close()


def set_user_avatar_color(user_id: int, color: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE users SET avatar_color = ? WHERE id = ?", (color, user_id))
        conn.commit()
    finally:
        conn.close()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_user_by_session(token: str) -> dict | None:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT u.id, u.username, u.email, u.auth_provider, u.avatar_color
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        )
        row = cur.fetchone()
        return _user_row_to_dict(row) if row else None
    finally:
        conn.close()


def delete_session(token: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
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


def list_conversations(user_id: int, limit: int = 50):
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT c.id, c.started_at, c.title,
                   (SELECT content FROM messages m
                    WHERE m.conversation_id = c.id AND m.role = 'user'
                    ORDER BY m.id ASC LIMIT 1) AS first_message
            FROM conversations c
            WHERE c.user_id = ?
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_conversation_owner(conversation_id: int) -> int | None:
    conn = _connect()
    try:
        cur = conn.execute("SELECT user_id FROM conversations WHERE id = ?", (conversation_id,))
        row = cur.fetchone()
        return row[0] if row else None
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


def latest_operation_id() -> int:
    conn = _connect()
    try:
        cur = conn.execute("SELECT COALESCE(MAX(id), 0) FROM file_operations")
        return cur.fetchone()[0]
    finally:
        conn.close()


def operations_since(operation_id: int):
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT tool_name, file_path FROM file_operations WHERE id > ? ORDER BY id ASC",
            (operation_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()

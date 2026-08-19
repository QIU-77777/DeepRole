"""全局用户表：匿名 token ↔ user_id 映射（data/users.db，不按用户隔离）。"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_USERS_DB = Path(__file__).resolve().parent.parent / "data" / "users.db"


def _connect() -> sqlite3.Connection:
    _USERS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_USERS_DB))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            token TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def resolve_user(token: str | None) -> str | None:
    """按 token 返回已存在的 user_id；无效/缺失返回 None。"""
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT user_id FROM users WHERE token = ?", (token,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def create_user() -> tuple[str, str]:
    """创建新用户，返回 (token, user_id)。"""
    token = uuid.uuid4().hex
    user_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO users (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()
    return token, user_id

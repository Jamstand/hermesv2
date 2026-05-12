"""SQLite-backed persistent memory with FTS5 full-text search."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT,
    channel_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp REAL NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_conv_user_ts ON conversations(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_conv_channel_ts ON conversations(channel_id, timestamp DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    content,
    user_id UNINDEXED,
    role UNINDEXED,
    content_rowid='id',
    content='conversations',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS conv_ai AFTER INSERT ON conversations BEGIN
    INSERT INTO conversations_fts(rowid, content, user_id, role)
    VALUES (new.id, new.content, new.user_id, new.role);
END;

CREATE TRIGGER IF NOT EXISTS conv_ad AFTER DELETE ON conversations BEGIN
    INSERT INTO conversations_fts(conversations_fts, rowid, content, user_id, role)
    VALUES('delete', old.id, old.content, old.user_id, old.role);
END;

CREATE TRIGGER IF NOT EXISTS conv_au AFTER UPDATE ON conversations BEGIN
    INSERT INTO conversations_fts(conversations_fts, rowid, content, user_id, role)
    VALUES('delete', old.id, old.content, old.user_id, old.role);
    INSERT INTO conversations_fts(rowid, content, user_id, role)
    VALUES (new.id, new.content, new.user_id, new.role);
END;

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT,
    timestamp REAL NOT NULL,
    UNIQUE(user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_facts_user_cat ON facts(user_id, category);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    timestamp REAL NOT NULL,
    UNIQUE(user_id, key)
);

CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL UNIQUE,
    traits TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class Memory:
    """SQLite store with FTS5 full-text search across past conversations."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_schema(self) -> None:
        with self._tx() as conn:
            conn.executescript(_SCHEMA)

    def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        channel_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        meta_json = json.dumps(metadata) if metadata else None
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO conversations "
                "(user_id, session_id, channel_id, role, content, timestamp, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, session_id, channel_id, role, content, time.time(), meta_json),
            )
            return cur.lastrowid

    def get_recent_messages(
        self, user_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, role, content, timestamp, channel_id, session_id, metadata "
            "FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [self._row_to_message(r) for r in reversed(rows)]

    def search_messages(
        self, user_id: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """FTS5 search. Returns matches ordered by relevance (bm25)."""
        if not query.strip():
            return []
        sanitized = _sanitize_fts(query)
        if not sanitized:
            return []
        conn = self._connect()
        rows = conn.execute(
            "SELECT c.id, c.role, c.content, c.timestamp, c.channel_id, "
            "       c.session_id, c.metadata "
            "FROM conversations_fts f "
            "JOIN conversations c ON c.id = f.rowid "
            "WHERE conversations_fts MATCH ? AND f.user_id = ? "
            "ORDER BY bm25(conversations_fts) LIMIT ?",
            (sanitized, user_id, limit),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def remember_fact(
        self, user_id: str, key: str, value: str, category: str | None = None
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO facts (user_id, key, value, category, timestamp) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET "
                "value=excluded.value, category=excluded.category, "
                "timestamp=excluded.timestamp",
                (user_id, key, value, category, time.time()),
            )

    def recall_fact(self, user_id: str, key: str) -> str | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT value FROM facts WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        return row["value"] if row else None

    def get_all_facts(self, user_id: str) -> dict[str, dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT key, value, category, timestamp FROM facts WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {
            r["key"]: {
                "value": r["value"],
                "category": r["category"],
                "timestamp": r["timestamp"],
            }
            for r in rows
        }

    def set_preference(self, user_id: str, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO preferences (user_id, key, value, timestamp) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET "
                "value=excluded.value, timestamp=excluded.timestamp",
                (user_id, key, value, time.time()),
            )

    def get_preference(
        self, user_id: str, key: str, default: str | None = None
    ) -> str | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT value FROM preferences WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        return row["value"] if row else default

    def update_user_profile(self, user_id: str, traits_dict: dict[str, Any]) -> None:
        existing = self.get_user_profile(user_id)
        merged = {**existing, **traits_dict}
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO user_profile (user_id, traits, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "traits=excluded.traits, updated_at=excluded.updated_at",
                (user_id, json.dumps(merged), time.time()),
            )

    def get_user_profile(self, user_id: str) -> dict[str, Any]:
        conn = self._connect()
        row = conn.execute(
            "SELECT traits FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["traits"])
        except json.JSONDecodeError:
            log.warning("Corrupt user_profile traits for %s", user_id)
            return {}

    def build_context(self, user_id: str, current_message: str) -> str:
        """Assemble a memory-context block to prepend to an LLM prompt."""
        parts: list[str] = []

        profile = self.get_user_profile(user_id)
        if profile:
            parts.append("# User profile\n" + json.dumps(profile, indent=2))

        facts = self.get_all_facts(user_id)
        if facts:
            lines = [f"- {k}: {v['value']}" for k, v in facts.items()]
            parts.append("# Known facts\n" + "\n".join(lines))

        related = self.search_messages(user_id, current_message, limit=5)
        if related:
            lines = [f"[{r['role']}] {r['content'][:200]}" for r in related]
            parts.append("# Related past messages\n" + "\n".join(lines))

        recent = self.get_recent_messages(user_id, limit=6)
        if recent:
            lines = [f"[{r['role']}] {r['content'][:200]}" for r in recent]
            parts.append("# Recent conversation\n" + "\n".join(lines))

        return "\n\n".join(parts)

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        meta = row["metadata"] if "metadata" in row.keys() else None
        try:
            meta_dict = json.loads(meta) if meta else {}
        except (json.JSONDecodeError, TypeError):
            meta_dict = {}
        return {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "timestamp": row["timestamp"],
            "channel_id": row["channel_id"] if "channel_id" in row.keys() else None,
            "session_id": row["session_id"] if "session_id" in row.keys() else None,
            "metadata": meta_dict,
        }


_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _sanitize_fts(query: str) -> str:
    """Extract bareword tokens for an FTS5 MATCH query.

    The FTS5 query parser errors on any character it doesn't recognize as a
    bareword char or a syntax token (e.g. `~`, `/`, `.`, `@`). Pulling only
    `\\w+` tokens out is the safest sanitization — it cannot produce a parser
    error regardless of what the user typed.
    """
    tokens = _FTS_TOKEN_RE.findall(query)
    return " ".join(tokens)

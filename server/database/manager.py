"""
Async database manager for JARVIS.
Wraps every table operation in a clean, typed interface so the rest of the
app never writes raw SQL outside of this file.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, List, Optional

import aiosqlite

from config import settings
from database.models import CREATE_TABLES_SQL


class DatabaseManager:
    """Single shared async SQLite connection pool."""

    def __init__(self, db_path: str = settings.DB_PATH):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the DB file (and parent dirs) then run all schema migrations."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent reads
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        async with self._lock:
            for sql in CREATE_TABLES_SQL:
                await self._db.execute(sql)
            await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _fetchall(self, sql: str, params: tuple = ()) -> List[aiosqlite.Row]:
        async with self._db.execute(sql, params) as cursor:
            return await cursor.fetchall()

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        async with self._db.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _execute(self, sql: str, params: tuple = ()) -> int:
        """Execute a write statement and return lastrowid."""
        async with self._lock:
            async with self._db.execute(sql, params) as cursor:
                await self._db.commit()
                return cursor.lastrowid  # type: ignore[return-value]

    # ─────────────────────────────────────────────────────────────────────────
    # Conversations
    # ─────────────────────────────────────────────────────────────────────────

    async def get_or_create_conversation(
        self, conversation_id: str, mode: str = "regular"
    ) -> dict:
        row = await self._fetchone(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
        if row:
            return dict(row)

        await self._execute(
            """
            INSERT INTO conversations (id, title, mode)
            VALUES (?, ?, ?)
            """,
            (conversation_id, "New Chat", mode),
        )
        row = await self._fetchone(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
        return dict(row)  # type: ignore[arg-type]

    async def list_conversations(self) -> List[dict]:
        rows = await self._fetchall(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        )
        return [dict(r) for r in rows]

    async def delete_conversation(self, conversation_id: str) -> None:
        await self._execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )

    async def _touch_conversation(self, conversation_id: str) -> None:
        await self._execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
            (conversation_id,),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Messages
    # ─────────────────────────────────────────────────────────────────────────

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tool_used: Optional[str] = None,
        tool_result: Optional[str] = None,
    ) -> int:
        row_id = await self._execute(
            """
            INSERT INTO messages (conversation_id, role, content, tool_used, tool_result)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, role, content, tool_used, tool_result),
        )
        await self._touch_conversation(conversation_id)
        return row_id

    async def get_messages(self, conversation_id: str, limit: int = 20) -> List[dict]:
        rows = await self._fetchall(
            """
            SELECT * FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        )
        # Return in chronological order
        return [dict(r) for r in reversed(rows)]

    # ─────────────────────────────────────────────────────────────────────────
    # Memories
    # ─────────────────────────────────────────────────────────────────────────

    async def upsert_memory(self, category: str, key: str, value: str) -> None:
        await self._execute(
            """
            INSERT INTO memories (category, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value      = excluded.value,
                category   = excluded.category,
                updated_at = datetime('now')
            """,
            (category, key, value),
        )

    async def get_all_memories(self) -> List[dict]:
        rows = await self._fetchall(
            "SELECT * FROM memories ORDER BY category, key"
        )
        return [dict(r) for r in rows]

    async def get_memories_by_category(self, category: str) -> List[dict]:
        rows = await self._fetchall(
            "SELECT * FROM memories WHERE category = ? ORDER BY key",
            (category,),
        )
        return [dict(r) for r in rows]

    async def delete_memory(self, memory_id: int) -> None:
        await self._execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    # ─────────────────────────────────────────────────────────────────────────
    # Reminders
    # ─────────────────────────────────────────────────────────────────────────

    async def add_reminder(
        self,
        title: str,
        description: str,
        remind_at: datetime,
        repeat: str = "none",
    ) -> int:
        return await self._execute(
            """
            INSERT INTO reminders (title, description, remind_at, repeat)
            VALUES (?, ?, ?, ?)
            """,
            (title, description, remind_at.isoformat(), repeat),
        )

    async def get_active_reminders(self) -> List[dict]:
        rows = await self._fetchall(
            """
            SELECT * FROM reminders
            WHERE is_active = 1 AND is_completed = 0
            ORDER BY remind_at
            """
        )
        return [dict(r) for r in rows]

    async def get_due_reminders(self) -> List[dict]:
        """Return reminders whose remind_at <= now and not yet completed."""
        now = datetime.now().isoformat()
        rows = await self._fetchall(
            """
            SELECT * FROM reminders
            WHERE is_active = 1 AND is_completed = 0 AND remind_at <= ?
            ORDER BY remind_at
            """,
            (now,),
        )
        return [dict(r) for r in rows]

    async def complete_reminder(self, reminder_id: int) -> None:
        await self._execute(
            "UPDATE reminders SET is_completed = 1 WHERE id = ?", (reminder_id,)
        )

    async def delete_reminder(self, reminder_id: int) -> None:
        await self._execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    # ─────────────────────────────────────────────────────────────────────────
    # Notes / Todos
    # ─────────────────────────────────────────────────────────────────────────

    async def add_note(
        self,
        title: str,
        content: str = "",
        category: str = "note",
        priority: int = 3,
    ) -> int:
        return await self._execute(
            """
            INSERT INTO notes (title, content, category, priority)
            VALUES (?, ?, ?, ?)
            """,
            (title, content, category, priority),
        )

    async def get_notes(self, category: Optional[str] = None) -> List[dict]:
        if category:
            rows = await self._fetchall(
                "SELECT * FROM notes WHERE category = ? ORDER BY priority, created_at",
                (category,),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM notes ORDER BY priority, created_at"
            )
        return [dict(r) for r in rows]

    async def complete_todo(self, note_id: int) -> None:
        await self._execute(
            """
            UPDATE notes SET is_completed = 1, updated_at = datetime('now')
            WHERE id = ?
            """,
            (note_id,),
        )

    async def delete_note(self, note_id: int) -> None:
        await self._execute("DELETE FROM notes WHERE id = ?", (note_id,))

    # ─────────────────────────────────────────────────────────────────────────
    # Calendar Events
    # ─────────────────────────────────────────────────────────────────────────

    async def add_calendar_event(
        self,
        title: str,
        description: str = "",
        start_time: datetime = None,  # type: ignore[assignment]
        end_time: Optional[datetime] = None,
        repeat: str = "none",
    ) -> int:
        return await self._execute(
            """
            INSERT INTO calendar_events (title, description, start_time, end_time, repeat)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                title,
                description,
                start_time.isoformat() if start_time else None,
                end_time.isoformat() if end_time else None,
                repeat,
            ),
        )

    async def get_upcoming_events(self, days: int = 7) -> List[dict]:
        from datetime import timedelta

        now = datetime.now()
        future = (now + timedelta(days=days)).isoformat()
        rows = await self._fetchall(
            """
            SELECT * FROM calendar_events
            WHERE start_time >= ? AND start_time <= ?
            ORDER BY start_time
            """,
            (now.isoformat(), future),
        )
        return [dict(r) for r in rows]

    async def delete_event(self, event_id: int) -> None:
        await self._execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))

    # ─────────────────────────────────────────────────────────────────────────
    # Settings
    # ─────────────────────────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: Any = None) -> Any:
        row = await self._fetchone(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self._execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
            """,
            (key, value),
        )

    async def get_all_settings(self) -> dict:
        rows = await self._fetchall("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}

"""
Database table definitions for JARVIS.
All CREATE TABLE statements live here so the manager can initialise the schema
in one place.
"""

# SQL to create every required table (idempotent — uses IF NOT EXISTS)
CREATE_TABLES_SQL = [
    # ── Conversations ─────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL DEFAULT 'New Chat',
        mode        TEXT NOT NULL DEFAULT 'regular',
        created_at  DATETIME NOT NULL DEFAULT (datetime('now')),
        updated_at  DATETIME NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Messages ──────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
        content         TEXT NOT NULL,
        tool_used       TEXT,
        tool_result     TEXT,
        created_at      DATETIME NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Memories ──────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS memories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        category    TEXT NOT NULL DEFAULT 'general',
        key         TEXT NOT NULL UNIQUE,
        value       TEXT NOT NULL,
        created_at  DATETIME NOT NULL DEFAULT (datetime('now')),
        updated_at  DATETIME NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Reminders ─────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS reminders (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        title           TEXT NOT NULL,
        description     TEXT DEFAULT '',
        remind_at       DATETIME NOT NULL,
        repeat          TEXT NOT NULL DEFAULT 'none',   -- none | daily | weekly
        is_active       BOOLEAN NOT NULL DEFAULT 1,
        is_completed    BOOLEAN NOT NULL DEFAULT 0,
        created_at      DATETIME NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Notes / Todos ─────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS notes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        title           TEXT NOT NULL,
        content         TEXT DEFAULT '',
        category        TEXT NOT NULL DEFAULT 'note',  -- 'note' | 'todo'
        is_completed    BOOLEAN NOT NULL DEFAULT 0,
        priority        INTEGER NOT NULL DEFAULT 3,    -- 1 high, 3 normal
        created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
        updated_at      DATETIME NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Calendar Events ───────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS calendar_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT NOT NULL,
        description TEXT DEFAULT '',
        start_time  DATETIME NOT NULL,
        end_time    DATETIME,
        repeat      TEXT NOT NULL DEFAULT 'none',   -- none | daily | weekly | monthly
        created_at  DATETIME NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Settings ──────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS settings (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  DATETIME NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # ── Indexes ───────────────────────────────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_reminders_active ON reminders(is_active, remind_at)",
    "CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category)",
    "CREATE INDEX IF NOT EXISTS idx_events_start ON calendar_events(start_time)",
]

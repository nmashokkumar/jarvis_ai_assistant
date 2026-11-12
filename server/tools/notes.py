"""
Notes and to-do tool for JARVIS.
Handles creation, listing, completion, and deletion of notes and todos.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.manager import DatabaseManager


# ─────────────────────────────────────────────────────────────────────────────
# Public CRUD functions
# ─────────────────────────────────────────────────────────────────────────────

async def add_note(title: str, content: str, db: "DatabaseManager") -> str:
    """Add a plain note and return a confirmation string."""
    note_id = await db.add_note(title=title, content=content, category="note")
    return f"Note saved (ID {note_id}): '{title}'."


async def add_todo(title: str, db: "DatabaseManager") -> str:
    """Add a to-do item and return a confirmation string."""
    todo_id = await db.add_note(title=title, content="", category="todo")
    return f"To-do added (ID {todo_id}): '{title}'."


async def list_notes(db: "DatabaseManager") -> str:
    """Return a formatted list of all notes (not todos)."""
    notes = await db.get_notes(category="note")
    if not notes:
        return "No notes saved, Boss."

    lines = ["Your notes:"]
    for n in notes:
        status = "✓" if n["is_completed"] else "○"
        lines.append(f"  [{n['id']}] {status} {n['title']}")
        if n["content"]:
            lines.append(f"      {n['content'][:100]}")
    return "\n".join(lines)


async def list_todos(db: "DatabaseManager") -> str:
    """Return a formatted list of all to-dos."""
    todos = await db.get_notes(category="todo")
    if not todos:
        return "Your to-do list is empty, Boss."

    lines = ["Your to-dos:"]
    for t in todos:
        status = "✓" if t["is_completed"] else "○"
        lines.append(f"  [{t['id']}] {status} {t['title']}")
    return "\n".join(lines)


async def complete_todo(todo_id: int, db: "DatabaseManager") -> str:
    """Mark a to-do as complete."""
    await db.complete_todo(todo_id)
    return f"To-do #{todo_id} marked as done. Nice work, Boss."


async def delete_note_item(note_id: int, db: "DatabaseManager") -> str:
    """Delete a note or to-do by ID."""
    await db.delete_note(note_id)
    return f"Item #{note_id} deleted."


# ─────────────────────────────────────────────────────────────────────────────
# Intent handler (called by router)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_title(message: str, keywords: list[str]) -> str:
    """Strip intent keywords and common filler words to get the item title."""
    text = message
    for kw in keywords:
        text = re.sub(re.escape(kw), "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(a|an|the|please|boss|note|todo|task|to-do|that|and|to|add|make|create|save)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip().rstrip(".,!?") or "Untitled"


async def handle_notes_intent(message: str, db: "DatabaseManager") -> str:
    """Detect sub-intent and dispatch to the right notes function."""
    lower = message.lower()

    # List notes
    if re.search(r"\b(list|show|see|view|my)\b.*\bnotes?\b", lower):
        return await list_notes(db)

    # List todos
    if re.search(r"\b(list|show|see|view|my)\b.*\b(todo|to-do|task|tasks)\b", lower):
        return await list_todos(db)

    # Complete todo
    done_match = re.search(r"(?:complete|done|finish|mark done)\s*(?:todo|task|item)?\s*#?(\d+)", lower)
    if done_match:
        return await complete_todo(int(done_match.group(1)), db)

    # Delete
    del_match = re.search(r"(?:delete|remove)\s*(?:note|todo|task|item)?\s*#?(\d+)", lower)
    if del_match:
        return await delete_note_item(int(del_match.group(1)), db)

    # Todo vs note
    is_todo = any(kw in lower for kw in ["todo", "to do", "to-do", "task", "add to list"])
    keywords = ["add a note", "make a note", "save a note", "note that",
                "add todo", "add to do", "add task", "add to list",
                "new todo", "create todo", "create task", "add a task"]
    title = _extract_title(message, keywords)

    if is_todo:
        return await add_todo(title, db)
    else:
        return await add_note(title, "", db)

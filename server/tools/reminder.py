"""
Reminder tool for JARVIS.
Handles natural-language time parsing ("in 30 minutes", "tomorrow at 9 AM", etc.)
and CRUD operations on the reminders table.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from database.manager import DatabaseManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Natural-language time parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_time(text: str) -> Optional[datetime]:
    """
    Parse a natural-language time expression into a datetime.
    Returns None if the expression could not be understood.

    Supports:
      - "in X minutes/hours/days"
      - "tomorrow at HH:MM [AM/PM]"
      - "today at HH:MM [AM/PM]"
      - "at HH:MM [AM/PM]"
      - "5 PM", "9:30 AM"
      - ISO-ish "YYYY-MM-DD HH:MM"
    """
    now = datetime.now()
    text = text.lower().strip()

    # "in X minutes / hours / days"
    m = re.search(r"in\s+(\d+)\s+(minute|hour|day)s?", text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        delta = {
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
        }[unit]
        return now + delta

    # Base date
    base_date = now.date()
    if "tomorrow" in text:
        base_date = (now + timedelta(days=1)).date()

    # Time part: "at HH:MM AM/PM" or "H PM" or "HH:MM"
    time_match = re.search(
        r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        text,
    )
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)

        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0

        try:
            result = datetime(base_date.year, base_date.month, base_date.day, hour, minute)
            # If the computed time is in the past (and no "tomorrow"), add one day
            if result < now and "tomorrow" not in text:
                result += timedelta(days=1)
            return result
        except ValueError:
            pass

    # ISO-ish "YYYY-MM-DD HH:MM"
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    return None


def _extract_reminder_title(message: str) -> str:
    """Pull the subject of the reminder from free text."""
    # Remove time phrases to get a cleaner title
    cleaned = re.sub(
        r"(remind me|set a reminder|set reminder|alarm|to|about|that|at|in|for|on)\b",
        " ",
        message,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(tomorrow|today|tonight|morning|evening)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".,!?")
    return cleaned or "Reminder"


# ─────────────────────────────────────────────────────────────────────────────
# Public CRUD functions
# ─────────────────────────────────────────────────────────────────────────────

async def add_reminder(
    title: str,
    remind_at_str: str,
    description: str = "",
    repeat: str = "none",
    db: "DatabaseManager" = None,  # type: ignore[assignment]
) -> str:
    """
    Add a reminder after parsing the time string.

    Returns:
        Confirmation or error message.
    """
    remind_at = _parse_time(remind_at_str)
    if remind_at is None:
        return (
            f"Couldn't parse '{remind_at_str}' as a time. "
            "Try 'in 30 minutes', 'tomorrow at 9 AM', or '5 PM', Boss."
        )

    await db.add_reminder(title, description, remind_at, repeat)
    formatted = remind_at.strftime("%A, %d %B %Y at %I:%M %p")
    return f"Reminder set: '{title}' on {formatted}."


async def list_reminders(db: "DatabaseManager") -> str:
    """Return a formatted list of active reminders."""
    reminders = await db.get_active_reminders()
    if not reminders:
        return "No active reminders, Boss."

    lines = ["Active reminders:"]
    for r in reminders:
        at = r["remind_at"]
        try:
            dt = datetime.fromisoformat(at)
            at = dt.strftime("%d %b %Y, %I:%M %p")
        except ValueError:
            pass
        repeat_info = f" (repeats: {r['repeat']})" if r["repeat"] != "none" else ""
        lines.append(f"  [{r['id']}] {r['title']} — {at}{repeat_info}")

    return "\n".join(lines)


async def cancel_reminder(reminder_id: int, db: "DatabaseManager") -> str:
    """Cancel (delete) a reminder by ID."""
    await db.delete_reminder(reminder_id)
    return f"Reminder #{reminder_id} cancelled, Boss."


# ─────────────────────────────────────────────────────────────────────────────
# Intent handler (called by router)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_reminder_intent(message: str, db: "DatabaseManager") -> str:
    """
    Detect sub-intent (add / list / cancel) from free text and dispatch.
    """
    lower = message.lower()

    # List intent
    if any(kw in lower for kw in ["list reminder", "show reminder", "my reminder", "what reminder"]):
        return await list_reminders(db)

    # Cancel intent
    cancel_match = re.search(r"(?:cancel|delete|remove) reminder\s*#?(\d+)", lower)
    if cancel_match:
        return await cancel_reminder(int(cancel_match.group(1)), db)

    # Default: add intent
    title = _extract_reminder_title(message)
    return await add_reminder(title, message, db=db)

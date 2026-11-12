"""
Calendar tool for JARVIS.
Adds events and returns upcoming schedules.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from database.manager import DatabaseManager


# ─────────────────────────────────────────────────────────────────────────────
# Time parser (reuses similar logic to reminder.py)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_event_time(text: str) -> Optional[datetime]:
    """Parse a date/time expression from free text."""
    now = datetime.now()
    lower = text.lower()

    # "in X minutes/hours/days"
    m = re.search(r"in\s+(\d+)\s+(minute|hour|day)s?", lower)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        return now + {"minute": timedelta(minutes=amount), "hour": timedelta(hours=amount), "day": timedelta(days=amount)}[unit]

    base_date = now.date()
    if "tomorrow" in lower:
        base_date = (now + timedelta(days=1)).date()
    elif "next week" in lower:
        base_date = (now + timedelta(weeks=1)).date()

    # Named month dates: "15 January", "January 15"
    month_names = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    m = re.search(rf"(\d{{1,2}})\s+({month_names})|({month_names})\s+(\d{{1,2}})", lower)
    if m:
        if m.group(1):
            day, month_str = int(m.group(1)), m.group(2)
        else:
            day, month_str = int(m.group(4)), m.group(3)
        month = datetime.strptime(month_str, "%B").month
        year = now.year
        try:
            base_date = datetime(year, month, day).date()
        except ValueError:
            pass

    # Time part
    t_match = re.search(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if t_match:
        hour = int(t_match.group(1))
        minute = int(t_match.group(2) or 0)
        meridiem = t_match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        try:
            result = datetime(base_date.year, base_date.month, base_date.day, hour, minute)
            if result < now and "tomorrow" not in lower and "next" not in lower:
                result += timedelta(days=1)
            return result
        except ValueError:
            pass

    # Default: same date, 9 AM
    try:
        return datetime(base_date.year, base_date.month, base_date.day, 9, 0)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def add_event(
    title: str,
    start_time_str: str,
    description: str = "",
    end_time_str: Optional[str] = None,
    repeat: str = "none",
    db: "DatabaseManager" = None,  # type: ignore[assignment]
) -> str:
    """Add a calendar event and return a confirmation string."""
    start_time = _parse_event_time(start_time_str)
    if not start_time:
        return f"Couldn't parse '{start_time_str}' as a date/time. Try 'tomorrow at 3 PM', Boss."

    end_time = _parse_event_time(end_time_str) if end_time_str else None

    await db.add_calendar_event(
        title=title,
        description=description,
        start_time=start_time,
        end_time=end_time,
        repeat=repeat,
    )

    formatted = start_time.strftime("%A, %d %B %Y at %I:%M %p")
    return f"Event added: '{title}' on {formatted}."


async def list_events(days: int = 7, db: "DatabaseManager" = None) -> str:  # type: ignore[assignment]
    """Return upcoming events for the next `days` days."""
    events = await db.get_upcoming_events(days)
    if not events:
        return f"No events in the next {days} days, Boss."

    lines = [f"Upcoming events (next {days} days):"]
    for ev in events:
        start = ev["start_time"]
        try:
            dt = datetime.fromisoformat(start)
            start = dt.strftime("%d %b, %I:%M %p")
        except ValueError:
            pass
        repeat_info = f" [{ev['repeat']}]" if ev["repeat"] != "none" else ""
        lines.append(f"  [{ev['id']}] {ev['title']} — {start}{repeat_info}")
        if ev["description"]:
            lines.append(f"      {ev['description'][:80]}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Intent handler (called by router)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_calendar_intent(message: str, db: "DatabaseManager") -> str:
    """Detect sub-intent and dispatch."""
    lower = message.lower()

    # List events
    if re.search(r"\b(list|show|see|view|my|upcoming)\b.*\b(event|calendar|schedule|meeting)\b", lower):
        return await list_events(db=db)

    # Add event — extract title by stripping known keywords
    keywords = [
        "add event", "schedule", "schedule a meeting", "add to calendar",
        "create event", "new event", "calendar event", "set a meeting",
        "book a meeting", "add a",
    ]
    title = message
    for kw in keywords:
        title = re.sub(re.escape(kw), "", title, flags=re.IGNORECASE)

    # Strip time phrases for the title
    title = re.sub(
        r"\b(at|on|for|tomorrow|today|next week|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip().rstrip(".,!?") or "Meeting"

    return await add_event(title, message, db=db)

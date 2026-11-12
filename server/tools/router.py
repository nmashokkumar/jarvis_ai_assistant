"""
Tool router for JARVIS.
Keyword-based intent detection maps user messages to the correct tool
and returns a formatted result string for the LLM to summarise.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from database.manager import DatabaseManager

# ─────────────────────────────────────────────────────────────────────────────
# Intent → keyword mapping
# ─────────────────────────────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "reminder": [
        "remind me", "set a reminder", "set reminder", "alarm",
        "remind at", "remind in", "set an alarm",
    ],
    "weather": [
        "weather", "temperature", "forecast", "humidity", "rain",
        "is it raining", "how hot", "how cold", "climate",
    ],
    "web_search": [
        "search", "look up", "look up", "google", "find out",
        "what is", "what are", "who is", "who are", "where is",
        "when did", "when is", "how much", "price of", "cost of",
        "latest news", "news about", "tell me about",
    ],
    "notes": [
        "add a note", "make a note", "save a note", "note that",
        "add todo", "add to do", "add task", "add to list",
        "new todo", "create todo", "create task",
    ],
    "calendar": [
        "add event", "schedule", "schedule a meeting", "add to calendar",
        "create event", "new event", "calendar event", "set a meeting",
        "book a meeting",
    ],
}


def detect_tool(message: str) -> Tuple[Optional[str], dict]:
    """
    Scan `message` for known intent keywords and return the matching tool name
    plus any extracted parameters.

    Returns:
        (tool_name, params_dict) — tool_name is None if no tool matched.
    """
    lower = message.lower()

    # Do not trigger web search for conversational self-referential questions
    conversational_patterns = [
        "who are you", "who am i", "what is your name", "what can you do",
        "how are you", "hello", "hey jarvis", "hi jarvis", "tell me a joke"
    ]
    for cp in conversational_patterns:
        if cp in lower:
            return None, {}

    for tool_name, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                params = _extract_params(tool_name, message, lower)
                return tool_name, params

    return None, {}


def _extract_params(tool_name: str, raw: str, lower: str) -> dict:
    """
    Minimal param extraction from raw text.  Tools do their own heavier
    parsing internally; we just surface obvious fields here so the router
    can pass them on.
    """
    params: dict = {"raw_message": raw}

    if tool_name == "weather":
        # Look for "in <location>" or "for <location>"
        m = re.search(r"\b(?:in|for|at)\s+([A-Za-z ,]+?)(?:\?|$)", raw)
        params["location"] = m.group(1).strip() if m else None

    elif tool_name == "web_search":
        # Strip common prefixes to get the actual query
        query = raw
        for prefix in [
            "search for", "search", "look up", "google", "find out about",
            "what is", "who is", "how much is", "price of", "tell me about",
        ]:
            pattern = re.compile(rf"^\s*{re.escape(prefix)}\s+", re.IGNORECASE)
            query = pattern.sub("", query)
        params["query"] = query.strip().rstrip("?.")

    return params


# ─────────────────────────────────────────────────────────────────────────────
# Tool executor
# ─────────────────────────────────────────────────────────────────────────────

async def execute_tool(
    tool_name: str,
    params: dict,
    user_message: str,
    db: "DatabaseManager",
) -> str:
    """
    Dispatch to the appropriate tool function and return its result string.

    Args:
        tool_name: one of 'reminder', 'weather', 'web_search', 'notes', 'calendar'
        params: extracted parameters from detect_tool()
        user_message: original raw message (passed to tools that need NLP)
        db: active DatabaseManager

    Returns:
        Human-readable result string.
    """
    if tool_name == "weather":
        from tools.weather import get_weather
        from config import settings

        location = params.get("location") or settings.LOCATION_DEFAULT
        return await get_weather(location)

    elif tool_name == "web_search":
        from tools.web_search import search_web

        query = params.get("query") or user_message
        return await search_web(query)

    elif tool_name == "reminder":
        from tools.reminder import handle_reminder_intent

        return await handle_reminder_intent(user_message, db)

    elif tool_name == "notes":
        from tools.notes import handle_notes_intent

        return await handle_notes_intent(user_message, db)

    elif tool_name == "calendar":
        from tools.calendar_tool import handle_calendar_intent

        return await handle_calendar_intent(user_message, db)

    return f"Unknown tool: {tool_name}"

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
# Ordered by priority (system monitor / reminders / weather / calendar / notes before generic search)
# ─────────────────────────────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "system_monitor": [
        "system status", "server status", "server health", "system health",
        "server systems", "are all server systems", "server diagnostic", "system diagnostic",
        "diagnostics", "check server", "check the server", "server check",
        "system check", "check system", "check systems", "how is the server",
        "how's the server", "cpu usage", "how much cpu", "cpu load",
        "ram usage", "memory usage", "how much ram", "memory load",
        "disk space", "disk usage", "storage space", "storage status",
        "free disk", "free storage", "server uptime", "system uptime",
        "hardware status", "server metrics",
    ],
    "reminder": [
        "remind me", "set a reminder", "set reminder", "alarm",
        "remind at", "remind in", "set an alarm",
    ],
    "weather": [
        "weather", "temperature", "forecast", "humidity", "rain",
        "is it raining", "how hot", "how cold", "climate",
    ],
    "calendar": [
        "add event", "schedule", "schedule a meeting", "add to calendar",
        "create event", "new event", "calendar event", "set a meeting",
        "book a meeting",
    ],
    "notes": [
        "add a note", "make a note", "save a note", "note that",
        "add todo", "add to do", "add task", "add to list",
        "new todo", "create todo", "create task",
    ],
    "web_search": [
        "search it", "search that", "look it up", "google it", "check it", "check that", "find it",
        "search for", "search", "look up", "google", "find out",
        "what is", "what are", "what does", "who is", "who are", "where is",
        "when did", "when is", "how much", "price of", "cost of",
        "latest news", "news about", "current news", "tell me about",
        "explain", "meaning of", "definition of", "define",
    ],
}

# Questions or conversational phrases that should NOT trigger action tools
_NEGATIVE_PATTERNS = [
    "who are you", "who am i", "what is your name", "what's your name",
    "what is my name", "what's my name", "what can you do", "what are you",
    "how are you", "hello", "hey jarvis", "hi jarvis", "tell me a joke",
    "are you real", "are you human", "who made you", "who created you",
    "what is a reminder", "how to set a reminder", "what is a calendar",
    "how do calendars work", "how do reminders work",
]


def _should_auto_search(raw: str, lower: str) -> bool:
    """
    Detect whether a question asks about concepts, acronyms, entities, or real-time topics
    even without explicit 'search' keywords.
    """
    # 1. Informational question triggers
    info_prefixes = [
        "do you know", "have you heard", "tell me about", "what is", "what are",
        "what does", "who is", "who was", "who are", "can you explain", "explain",
        "how does", "how do", "where is", "when did", "when was", "meaning of",
        "definition of", "how to"
    ]
    if any(prefix in lower for prefix in info_prefixes):
        return True

    # 2. Tech acronyms / uppercase terms (e.g. MCP, RAG, DSPY, LLM, VLLM, LORA, GGUF, CUDA, API)
    raw_tokens = re.findall(r"\b[A-Z]{2,6}\b", raw)
    acronyms = [t for t in raw_tokens if t not in {"OK", "AM", "PM", "AI", "I"}]
    if acronyms:
        return True

    # 3. Known tech companies / models / platforms
    entities = [
        "claude", "anthropic", "openai", "chatgpt", "gemini", "deepseek", "nvidia",
        "llama", "mistral", "qwen", "groq", "huggingface", "pytorch", "vllm", "unsloth",
        "apple", "google", "microsoft", "meta", "tesla", "github"
    ]
    if any(ent in lower for ent in entities):
        return True

    # 4. Temporal & live data triggers (with typo tolerance)
    live_markers = [
        "stock", "price", "prive", "share", "market", "score", "match", "winner",
        "election", "yesterday", "yesteday", "today", "tomorrow", "tonight", "latest",
        "recent", "current", "newest", "released", "launch"
    ]
    if any(marker in lower for marker in live_markers):
        return True

    return False


def detect_tool(message: str) -> Tuple[Optional[str], dict]:
    """
    Scan `message` for known intent keywords and return the matching tool name
    plus any extracted parameters.

    Returns:
        (tool_name, params_dict) — tool_name is None if no tool matched.
    """
    lower = message.lower().strip()

    # Do not trigger tools for purely conversational / meta questions
    for cp in _NEGATIVE_PATTERNS:
        if cp in lower:
            return None, {}

    # Check high-priority explicit tools first (system monitor, reminder, weather, calendar, notes)
    for tool_name, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                params = _extract_params(tool_name, message, lower)
                return tool_name, params

    # Auto-detect informational questions, tech acronyms, entities, or live data
    if _should_auto_search(message, lower):
        params = _extract_params("web_search", message, lower)
        return "web_search", params

    return None, {}


def _extract_params(tool_name: str, raw: str, lower: str) -> dict:
    """
    Minimal param extraction from raw text. Tools do their own heavier
    parsing internally; we just surface obvious fields here so the router
    can pass them on.
    """
    params: dict = {"raw_message": raw}

    if tool_name == "weather":
        # Look for "in <location>" or "for <location>"
        m = re.search(r"\b(?:in|for|at)\s+([A-Za-z ,]+?)(?:\?|$)", raw)
        params["location"] = m.group(1).strip() if m else None

    elif tool_name == "web_search":
        # Strip wake-word prefixes if user addressed Jarvis directly
        query = re.sub(r"^(hey\s+)?jarvis[,:\s]*", "", raw, flags=re.IGNORECASE)

        # Strip common search command prefixes to get the actual clean query
        for prefix in [
            "search for", "search", "look up", "google", "find out about", "find out",
            "what is", "what are", "what does", "who is", "who are", "where is",
            "how much is", "price of", "cost of", "tell me about", "explain",
            "meaning of", "definition of", "define",
        ]:
            pattern = re.compile(rf"^\s*{re.escape(prefix)}\s+", re.IGNORECASE)
            query = pattern.sub("", query)
        clean_query = query.strip().rstrip("?.")
        params["query"] = clean_query if clean_query else raw

    elif tool_name == "system_monitor":
        if any(w in lower for w in ["cpu", "processor"]):
            params["aspect"] = "cpu"
        elif any(w in lower for w in ["ram", "memory"]):
            params["aspect"] = "ram"
        elif any(w in lower for w in ["disk", "storage", "space", "hard drive"]):
            params["aspect"] = "disk"
        elif any(w in lower for w in ["uptime", "boot"]):
            params["aspect"] = "uptime"
        else:
            params["aspect"] = "all"

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
        tool_name: one of 'system_monitor', 'reminder', 'weather', 'web_search', 'notes', 'calendar'
        params: extracted parameters from detect_tool()
        user_message: original raw message (passed to tools that need NLP)
        db: active DatabaseManager

    Returns:
        Human-readable result string.
    """
    if tool_name == "system_monitor":
        from tools.system_monitor import get_system_metrics

        aspect = params.get("aspect", "all")
        return await get_system_metrics(aspect)

    elif tool_name == "weather":
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

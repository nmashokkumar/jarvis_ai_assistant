"""
Memory manager for JARVIS.
Uses simple rule-based pattern matching to extract and persist user facts
(name, preferences, age, etc.) from conversation turns.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from database.manager import DatabaseManager


# ─────────────────────────────────────────────────────────────────────────────
# Pattern registry
# Each entry: (regex_pattern, category, key_template)
# The first capture group becomes the stored value.
# ─────────────────────────────────────────────────────────────────────────────

_PATTERNS = [
    # Identity
    (r"my name is ([A-Za-z]+(?: [A-Za-z]+)?)", "identity", "name"),
    (r"i(?:'m| am) ([A-Za-z]+(?: [A-Za-z]+)?) years old", "identity", "age"),
    (r"i(?:'m| am) (\d{1,3}) years old", "identity", "age"),
    (r"my (?:birth ?day|dob) is (.+?)(?:\.|$)", "identity", "birthday"),
    (r"i(?:'m| am) from ([A-Za-z ,]+?)(?:\.|$)", "identity", "location"),
    (r"i (?:live|work|stay) in ([A-Za-z ,]+?)(?:\.|$)", "identity", "location"),
    # Preferences
    (r"i (?:love|really like|enjoy|prefer) (.+?)(?:\.|$)", "preferences", "likes"),
    (r"my (?:favourite|favorite) (?:\w+ )?is (.+?)(?:\.|$)", "preferences", "favourite"),
    (r"i (?:hate|dislike|can't stand|cannot stand) (.+?)(?:\.|$)", "preferences", "dislikes"),
    # Work / role
    (r"i(?:'m| am) a(?:n)? ([A-Za-z ]+?) (?:by profession|at |for )", "work", "job_title"),
    (r"i work (?:at|for) ([A-Za-z0-9 &,.]+?)(?:\.|$)", "work", "employer"),
    # Explicit "remember" instruction
    (r"remember (?:that )?(.+?)(?:\.|$)", "misc", "note"),
    # Wake word variant: "note that …"
    (r"note (?:that )?(.+?)(?:\.|$)", "misc", "note"),
]

_COMPILED = [
    (re.compile(pat, re.IGNORECASE), cat, key)
    for pat, cat, key in _PATTERNS
]


class MemoryManager:
    """Extract facts from conversation turns and persist them in the DB."""

    # ─────────────────────────────────────────────────────────────────────────
    # Extraction
    # ─────────────────────────────────────────────────────────────────────────

    async def extract_and_save_memories(
        self,
        user_message: str,
        assistant_response: str,
        db: "DatabaseManager",
    ) -> None:
        """
        Scan the user's message for personal facts and upsert them into the DB.

        Args:
            user_message: raw text from the user
            assistant_response: LLM reply (scanned for confirmation phrases)
            db: active DatabaseManager instance
        """
        combined = user_message  # We deliberately only extract from user turns

        for pattern, category, key in _COMPILED:
            match = pattern.search(combined)
            if match:
                value = match.group(1).strip().rstrip(".,;")
                if value and len(value) < 200:  # sanity-check length
                    # For keys that can repeat (likes, notes) we suffix with
                    # a short hash to avoid overwriting previous entries
                    if key in ("likes", "dislikes", "note"):
                        import hashlib

                        suffix = hashlib.md5(value.encode()).hexdigest()[:6]
                        unique_key = f"{key}_{suffix}"
                    else:
                        unique_key = key

                    await db.upsert_memory(category, unique_key, value)

    # ─────────────────────────────────────────────────────────────────────────
    # Retrieval
    # ─────────────────────────────────────────────────────────────────────────

    async def get_relevant_memories(
        self,
        query: str,  # noqa: ARG002  (reserved for future semantic search)
        db: "DatabaseManager",
    ) -> str:
        """
        Return a formatted string of all memories for injection into the system
        prompt.  In a future version this could do semantic retrieval.

        Args:
            query: current user message (unused — kept for API compatibility)
            db: active DatabaseManager instance

        Returns:
            Human-readable memory block, or empty string if no memories exist.
        """
        memories = await db.get_all_memories()
        if not memories:
            return ""

        lines = []
        current_category = None
        for mem in memories:
            if mem["category"] != current_category:
                current_category = mem["category"]
                lines.append(f"\n[{current_category.upper()}]")
            # Strip internal hash suffix from display key
            display_key = re.sub(r"_[a-f0-9]{6}$", "", mem["key"])
            lines.append(f"  {display_key}: {mem['value']}")

        return "\n".join(lines).strip()

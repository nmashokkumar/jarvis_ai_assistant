"""
Main chat engine for JARVIS.
Ties together: DB, LLM, memory, and tools into a single process_message call.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional

from config import settings
from core.llm import OllamaClient
from core.memory import MemoryManager
from core.personality import build_system_prompt, format_tool_result
from database.manager import DatabaseManager
from tools.router import detect_tool, execute_tool

logger = logging.getLogger(__name__)


class ChatEngine:
    """
    Orchestrates a full chat turn:
      1. Persist the user message.
      2. Optionally invoke a tool.
      3. Build the prompt (system + history + tool result + user message).
      4. Stream or batch-generate the LLM reply.
      5. Persist the assistant message.
      6. Fire-and-forget memory extraction.
    """

    def __init__(self, db: DatabaseManager, llm: OllamaClient) -> None:
        self.db = db
        self.llm = llm
        self.memory = MemoryManager()

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _build_messages(
        self,
        conversation_id: str,
        user_message: str,
        mode: str,
        tool_name: Optional[str] = None,
        tool_result: Optional[str] = None,
    ) -> list[dict]:
        """
        Assemble the message list to send to the LLM:
          [system, ...history, (tool injection), user]
        """
        # Fetch stored memories for context
        memories = await self.db.get_all_memories()
        system_prompt = build_system_prompt(mode, memories)

        # Fetch recent conversation history (excluding the current turn which we add next)
        history = await self.db.get_messages(conversation_id, limit=settings.MAX_CONTEXT_MESSAGES)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        # Add past history (skip if it matches the current user message at the very end)
        for i, msg in enumerate(history):
            if i == len(history) - 1 and msg["role"] == "user" and msg["content"] == user_message:
                continue
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Inject tool result as contextual info before user message
        if tool_name and tool_result:
            messages.append(
                {
                    "role": "user",
                    "content": format_tool_result(tool_name, tool_result),
                }
            )

        # Append the current user message
        messages.append({"role": "user", "content": user_message})

        return messages

    # ─────────────────────────────────────────────────────────────────────────
    # Non-streaming
    # ─────────────────────────────────────────────────────────────────────────

    async def process_message(
        self,
        conversation_id: str,
        user_message: str,
        mode: str = "regular",
        stream: bool = False,
    ) -> str:
        """
        Process a single chat turn without streaming.

        Returns:
            The assistant's response string.
        """
        # Ensure conversation exists
        await self.db.get_or_create_conversation(conversation_id, mode)

        # Persist user message
        await self.db.save_message(conversation_id, "user", user_message)

        # Tool detection
        tool_name, tool_params = detect_tool(user_message)
        tool_result: Optional[str] = None

        if tool_name:
            try:
                tool_result = await execute_tool(tool_name, tool_params, user_message, self.db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tool %s failed: %s", tool_name, exc)
                tool_result = f"Tool error: {exc}"

        # Build prompt
        messages = await self._build_messages(
            conversation_id, user_message, mode, tool_name, tool_result
        )

        # LLM call
        response = await self.llm.chat(messages, mode)

        # Persist assistant reply
        await self.db.save_message(
            conversation_id,
            "assistant",
            response,
            tool_used=tool_name,
            tool_result=tool_result,
        )

        # Fire-and-forget memory extraction
        asyncio.create_task(
            self.memory.extract_and_save_memories(user_message, response, self.db)
        )

        return response

    # ─────────────────────────────────────────────────────────────────────────
    # Streaming
    # ─────────────────────────────────────────────────────────────────────────

    async def process_message_stream(
        self,
        conversation_id: str,
        user_message: str,
        mode: str = "regular",
    ) -> AsyncGenerator[str, None]:
        """
        Process a chat turn and stream token chunks as they arrive.

        Yields:
            Individual token strings from the LLM.

        Note:
            The caller is responsible for collecting the full response text
            if it needs to persist or post-process it.  This generator handles
            DB writes internally before returning.
        """
        # Ensure conversation exists
        await self.db.get_or_create_conversation(conversation_id, mode)

        # Persist user message
        await self.db.save_message(conversation_id, "user", user_message)

        # Tool detection
        tool_name, tool_params = detect_tool(user_message)
        tool_result: Optional[str] = None

        if tool_name:
            try:
                tool_result = await execute_tool(tool_name, tool_params, user_message, self.db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tool %s failed: %s", tool_name, exc)
                tool_result = f"Tool error: {exc}"

        # Build prompt
        messages = await self._build_messages(
            conversation_id, user_message, mode, tool_name, tool_result
        )

        # Stream and collect
        full_response_parts: list[str] = []
        async for token in self.llm.chat_stream(messages, mode):
            full_response_parts.append(token)
            yield token

        full_response = "".join(full_response_parts).strip()

        # Persist after streaming completes
        await self.db.save_message(
            conversation_id,
            "assistant",
            full_response,
            tool_used=tool_name,
            tool_result=tool_result,
        )

        # Fire-and-forget memory extraction
        asyncio.create_task(
            self.memory.extract_and_save_memories(user_message, full_response, self.db)
        )

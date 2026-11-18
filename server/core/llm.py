"""
Async Ollama client for JARVIS.
Supports both blocking and streaming inference via httpx.
"""
from __future__ import annotations

import json
from typing import AsyncGenerator, List

import httpx

from config import settings


class OllamaClient:
    """
    Thin async wrapper around the Ollama /api/chat endpoint.

    Usage:
        client = OllamaClient()
        text = await client.chat(messages, mode="regular")
    """

    def __init__(self) -> None:
        self.base_url = settings.OLLAMA_BASE_URL
        self.model = settings.OLLAMA_MODEL
        self.timeout = httpx.Timeout(120.0, connect=10.0)

    def _build_payload(
        self, messages: List[dict], stream: bool, num_predict: Optional[int] = None
    ) -> dict:
        tokens = num_predict or settings.OLLAMA_NUM_PREDICT
        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": "24h",  # Pin model in RAM permanently — zero reload delay!
            "options": {
                "num_predict": tokens,
                "temperature": settings.OLLAMA_TEMPERATURE,
                "num_thread": 4,   # Utilize all 4 CPU cores for maximum inference speed
                "num_ctx": 8192,   # Expanded context window for deep brainstorming and chat
                "top_p": 0.90,
                "min_p": 0.06,
                "repeat_penalty": 1.12,
                "stop": [
                    "<|eot_id|>",
                    "<|start_header_id|>",
                    "<|end_header_id|>",
                    "<|im_end|>",
                    "</s>",
                ],
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Non-streaming
    # ─────────────────────────────────────────────────────────────────────────

    async def chat(
        self, messages: List[dict], mode: str = "regular", num_predict: Optional[int] = None
    ) -> str:
        """
        Send a full conversation and return the assistant reply as a string.

        Args:
            messages: list of {"role": ..., "content": ...} dicts
            mode: 'regular' or 'productivity' (unused at HTTP level but kept
                  for future per-mode parameter tuning)
            num_predict: optional dynamic token budget limit

        Returns:
            Assistant response text.
        """
        payload = self._build_payload(messages, stream=False, num_predict=num_predict)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["message"]["content"].strip()

        except httpx.ConnectError:
            return (
                "Boss, Ollama seems to be offline. Fire it up and I'll be right with you."
            )
        except httpx.TimeoutException:
            return "Boss, the model took too long to respond. Try a shorter question."
        except Exception as exc:  # noqa: BLE001
            return f"Boss, something went sideways with the AI backend: {exc}"

    # ─────────────────────────────────────────────────────────────────────────
    # Streaming
    # ─────────────────────────────────────────────────────────────────────────

    async def chat_stream(
        self, messages: List[dict], mode: str = "regular", num_predict: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream token chunks from Ollama as they arrive.

        Yields:
            Individual token strings (may be empty — callers should filter).
        """
        payload = self._build_payload(messages, stream=True, num_predict=num_predict)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token

                        # Ollama signals end-of-stream with done=true
                        if chunk.get("done", False):
                            break

        except httpx.ConnectError:
            yield "Boss, Ollama seems to be offline. Fire it up and I'll be right with you."
        except httpx.TimeoutException:
            yield "Boss, the model timed out. Try a shorter question."
        except Exception as exc:  # noqa: BLE001
            yield f"Boss, the AI backend hit an error: {exc}"

    # ─────────────────────────────────────────────────────────────────────────
    # Tool Calling / Query Rewriter (Approach 2)
    # ─────────────────────────────────────────────────────────────────────────

    async def call_with_tools(
        self, messages: List[dict], tools: List[dict]
    ) -> Optional[dict]:
        """
        Invoke Ollama with native tool specifications to resolve search queries / anaphora.
        Returns the raw message dict containing tool_calls if emitted.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {
                "num_predict": 35,
                "temperature": 0.1,
                "num_thread": 4,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                r = await client.post(f"{self.base_url}/api/chat", json=payload)
                r.raise_for_status()
                return r.json().get("message", {})
        except Exception as exc:  # noqa: BLE001
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Health check
    # ─────────────────────────────────────────────────────────────────────────

    async def is_healthy(self) -> bool:
        """Return True if Ollama is reachable and the model is loaded."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                models = [m["name"] for m in r.json().get("models", [])]
                return any(self.model in m for m in models)
        except Exception:  # noqa: BLE001
            return False

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

    def _build_payload(self, messages: List[dict], stream: bool) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": "24h",  # Pin model in RAM permanently — zero reload delay!
            "options": {
                "num_predict": settings.OLLAMA_NUM_PREDICT,
                "temperature": settings.OLLAMA_TEMPERATURE,
                "num_thread": 4,   # Utilize all 4 CPU cores for maximum inference speed
                "num_ctx": 2048,   # Optimized context window for 2x faster CPU processing
                "top_k": 30,
                "top_p": 0.9,
                "stop": ["<|im_end|>", "</s>"],
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Non-streaming
    # ─────────────────────────────────────────────────────────────────────────

    async def chat(self, messages: List[dict], mode: str = "regular") -> str:
        """
        Send a full conversation and return the assistant reply as a string.

        Args:
            messages: list of {"role": ..., "content": ...} dicts
            mode: 'regular' or 'productivity' (unused at HTTP level but kept
                  for future per-mode parameter tuning)

        Returns:
            Assistant response text.
        """
        payload = self._build_payload(messages, stream=False)
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
        self, messages: List[dict], mode: str = "regular"
    ) -> AsyncGenerator[str, None]:
        """
        Stream token chunks from Ollama as they arrive.

        Yields:
            Individual token strings (may be empty — callers should filter).
        """
        payload = self._build_payload(messages, stream=True)
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

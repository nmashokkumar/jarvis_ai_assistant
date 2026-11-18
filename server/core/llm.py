"""
Unified High-Performance LLM Client for JARVIS.
Routes inference directly to C++ llama-server (OpenAI-compatible) for sub-millisecond
dispatch, AVX2 SIMD acceleration, and prompt slot caching.
Includes automatic zero-downtime fallback to Ollama.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Optional

import httpx

from config import settings

logger = logging.getLogger("jarvis.llm")


class ILLMClient(ABC):
    """Abstract interface defining the contract for JARVIS LLM services."""

    @abstractmethod
    async def chat(
        self, messages: List[dict], mode: str = "regular", num_predict: Optional[int] = None
    ) -> str:
        """Non-streaming completion."""
        ...

    @abstractmethod
    async def chat_stream(
        self, messages: List[dict], mode: str = "regular", num_predict: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """Streaming token-by-token generation."""
        ...

    @abstractmethod
    async def call_with_tools(
        self, messages: List[dict], tools: List[dict]
    ) -> Optional[dict]:
        """Tool/function calling for query rewriting and autonomous actions."""
        ...

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Health check endpoint status."""
        ...


class LLMClient(ILLMClient):
    """
    Production-grade LLM client orchestrating C++ llama-server with graceful Ollama fallback.
    """

    def __init__(self) -> None:
        self.llama_url = settings.LLAMA_CPP_BASE_URL.rstrip("/")
        self.ollama_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self.ollama_model = settings.OLLAMA_MODEL
        self.timeout = httpx.Timeout(120.0, connect=5.0)

    # ─────────────────────────────────────────────────────────────────────────
    # Non-streaming Chat
    # ─────────────────────────────────────────────────────────────────────────

    async def chat(
        self, messages: List[dict], mode: str = "regular", num_predict: Optional[int] = None
    ) -> str:
        tokens = num_predict or settings.OLLAMA_NUM_PREDICT

        # 1. Primary: C++ llama-server (Direct native execution)
        try:
            payload = {
                "messages": messages,
                "max_tokens": tokens,
                "temperature": settings.OLLAMA_TEMPERATURE,
                "top_p": 0.90,
                "stop": [
                    "<|eot_id|>",
                    "<|start_header_id|>",
                    "<|end_header_id|>",
                    "<|im_end|>",
                    "</s>",
                ],
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(f"{self.llama_url}/chat/completions", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data["choices"][0]["message"]["content"].strip()
                logger.warning("llama-server returned HTTP %d, falling back to Ollama", res.status_code)
        except Exception as exc:
            logger.info("llama-server unavailable (%s), falling back to Ollama", exc)

        # 2. Secondary: Ollama Fallback
        return await self._chat_ollama(messages, tokens)

    async def _chat_ollama(self, messages: List[dict], tokens: int) -> str:
        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": tokens,
                "temperature": settings.OLLAMA_TEMPERATURE,
                "num_thread": 4,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.ollama_url}/api/chat", json=payload)
                response.raise_for_status()
                return response.json()["message"]["content"].strip()
        except httpx.ConnectError:
            return "Boss, both the C++ engine and Ollama seem to be offline."
        except httpx.TimeoutException:
            return "Boss, the model took too long to respond. Try a shorter question."
        except Exception as exc:
            return f"Boss, something went sideways with the AI backend: {exc}"

    # ─────────────────────────────────────────────────────────────────────────
    # Streaming Chat
    # ─────────────────────────────────────────────────────────────────────────

    async def chat_stream(
        self, messages: List[dict], mode: str = "regular", num_predict: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        tokens = num_predict or settings.OLLAMA_NUM_PREDICT

        # 1. Primary: C++ llama-server (SSE stream)
        use_fallback = False
        try:
            payload = {
                "messages": messages,
                "max_tokens": tokens,
                "temperature": settings.OLLAMA_TEMPERATURE,
                "top_p": 0.90,
                "stream": true if False else True,
                "stop": [
                    "<|eot_id|>",
                    "<|start_header_id|>",
                    "<|end_header_id|>",
                    "<|im_end|>",
                    "</s>",
                ],
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", f"{self.llama_url}/chat/completions", json=payload
                ) as response:
                    if response.status_code == 200:
                        async for line in response.aiter_lines():
                            line_str = line.strip()
                            if not line_str or not line_str.startswith("data: "):
                                continue
                            data_str = line_str[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                token = delta.get("content", "")
                                if token:
                                    yield token
                            except Exception:
                                continue
                        return
                    else:
                        use_fallback = True
        except Exception as exc:
            logger.info("llama-server stream error (%s), using Ollama fallback", exc)
            use_fallback = True

        # 2. Secondary: Ollama Streaming Fallback
        if use_fallback:
            async for token in self._chat_stream_ollama(messages, tokens):
                yield token

    async def _chat_stream_ollama(
        self, messages: List[dict], tokens: int
    ) -> AsyncGenerator[str, None]:
        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_predict": tokens,
                "temperature": settings.OLLAMA_TEMPERATURE,
                "num_thread": 4,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", f"{self.ollama_url}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if chunk.get("done", False):
                                break
                        except Exception:
                            continue
        except httpx.ConnectError:
            yield "Boss, both the C++ engine and Ollama seem to be offline."
        except httpx.TimeoutException:
            yield "Boss, the model timed out. Try a shorter question."
        except Exception as exc:
            yield f"Boss, the AI backend hit an error: {exc}"

    # ─────────────────────────────────────────────────────────────────────────
    # Tool Calling / Query Rewriter
    # ─────────────────────────────────────────────────────────────────────────

    async def call_with_tools(
        self, messages: List[dict], tools: List[dict]
    ) -> Optional[dict]:
        """
        Invoke LLM with native tool specifications to resolve search queries.
        Normalizes arguments into a Python dict regardless of backend.
        """
        # 1. Primary: C++ llama-server
        try:
            payload = {
                "messages": messages,
                "tools": tools,
                "max_tokens": 40,
                "temperature": 0.1,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                res = await client.post(f"{self.llama_url}/chat/completions", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    msg = data.get("choices", [{}])[0].get("message", {})
                    tool_calls = msg.get("tool_calls", [])
                    # Normalize arguments if JSON string
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        raw_args = func.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                func["arguments"] = json.loads(raw_args)
                            except Exception:
                                func["arguments"] = {}
                    return msg
        except Exception as exc:
            logger.info("llama-server tool call failed (%s), falling back to Ollama", exc)

        # 2. Secondary: Ollama Fallback
        try:
            payload = {
                "model": self.ollama_model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {
                    "num_predict": 35,
                    "temperature": 0.1,
                    "num_thread": 4,
                },
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                r = await client.post(f"{self.ollama_url}/api/chat", json=payload)
                r.raise_for_status()
                return r.json().get("message", {})
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Health Check
    # ─────────────────────────────────────────────────────────────────────────

    async def is_healthy(self) -> bool:
        """Return True if C++ llama-server or Ollama is online."""
        # Check llama-server
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                r = await client.get(f"{self.llama_url.rsplit('/', 1)[0]}/health")
                if r.status_code == 200 and r.json().get("status") == "ok":
                    return True
        except Exception:
            pass

        # Fallback check Ollama
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                r = await client.get(f"{self.ollama_url}/api/tags")
                if r.status_code == 200:
                    models = [m["name"] for m in r.json().get("models", [])]
                    return any(self.ollama_model in m for m in models)
        except Exception:
            pass

        return False


# Backward compatibility alias
OllamaClient = LLMClient

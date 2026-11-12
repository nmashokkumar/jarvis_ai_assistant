"""
Text-to-speech handler for JARVIS.
Supports high-speed local Kokoro ONNX TTS (primary) with fallback to Edge TTS.
Strips markdown, synthesises audio, and returns raw audio bytes ready to ship over WebSocket.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re

from config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Markdown stripping
# ─────────────────────────────────────────────────────────────────────────────

_MD_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),          # **bold**
    (re.compile(r"\*(.+?)\*"), r"\1"),               # *italic*
    (re.compile(r"__(.+?)__"), r"\1"),               # __bold__
    (re.compile(r"_(.+?)_"), r"\1"),                 # _italic_
    (re.compile(r"~~(.+?)~~"), r"\1"),               # ~~strikethrough~~
    (re.compile(r"`{1,3}(.+?)`{1,3}", re.DOTALL), r"\1"),  # `code`
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),  # ## headings
    (re.compile(r"!\[.*?\]\(.*?\)"), ""),            # ![img](url)
    (re.compile(r"\[(.+?)\]\(.+?\)"), r"\1"),        # [text](url)
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),# list bullets
    (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),# numbered lists
    (re.compile(r"\n{2,}"), " "),                    # collapse blank lines
    (re.compile(r"\s+"), " "),                       # normalise whitespace
]


def strip_markdown(text: str) -> str:
    """Remove markdown formatting so TTS reads clean prose."""
    for pattern, replacement in _MD_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Kokoro ONNX Singleton
# ─────────────────────────────────────────────────────────────────────────────

_kokoro_instance = None
_kokoro_init_attempted = False


def _get_kokoro():
    global _kokoro_instance, _kokoro_init_attempted
    if _kokoro_instance is not None:
        return _kokoro_instance
    if _kokoro_init_attempted:
        return None

    _kokoro_init_attempted = True
    model_path = settings.KOKORO_MODEL_PATH
    voices_path = settings.KOKORO_VOICES_PATH

    # Check common fallback locations
    if not (os.path.exists(model_path) and os.path.exists(voices_path)):
        alt_dirs = [
            "/data/home/kokoro_test",
            os.path.expanduser("~/kokoro_test"),
            os.path.join(os.path.dirname(__file__), "..", ".."),
        ]
        for ad in alt_dirs:
            m = os.path.join(ad, "kokoro-v1.0.onnx")
            v = os.path.join(ad, "voices-v1.0.bin")
            if os.path.exists(m) and os.path.exists(v):
                model_path, voices_path = m, v
                break

    if os.path.exists(model_path) and os.path.exists(voices_path):
        try:
            from kokoro_onnx import Kokoro
            logger.info("Initializing Kokoro ONNX from %s", model_path)
            _kokoro_instance = Kokoro(model_path, voices_path)
            logger.info("Kokoro ONNX initialized successfully.")
            return _kokoro_instance
        except Exception as exc:
            logger.error("Failed to initialize Kokoro ONNX: %s", exc)
            return None
    else:
        logger.warning("Kokoro model files not found at %s. Falling back to Edge TTS.", model_path)
        return None


def _synthesize_kokoro_sync(clean_text: str, voice_name: str) -> bytes:
    kokoro = _get_kokoro()
    if kokoro is None:
        raise RuntimeError("Kokoro engine not available")

    import soundfile as sf
    samples, sample_rate = kokoro.create(clean_text, voice=voice_name, speed=1.0, lang="en-us")
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def _synthesize_edge_tts(clean_text: str, voice_name: str) -> bytes:
    import edge_tts
    buffer = io.BytesIO()
    communicate = edge_tts.Communicate(clean_text, voice_name)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    return buffer.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def text_to_speech(text: str, voice: str | None = None) -> bytes:
    """
    Convert text to audio bytes (WAV from Kokoro, or MP3 from Edge TTS).

    Args:
        text: The text to synthesise (markdown is stripped automatically).
        voice: Optional voice override. Defaults to settings.KOKORO_VOICE for Kokoro
               or settings.TTS_VOICE for Edge.

    Returns:
        Raw audio bytes (WAV or MP3), ready to base64-encode for Web Audio playback.
    """
    clean_text = strip_markdown(text)
    if not clean_text:
        return b""

    # Primary path: Kokoro ONNX (running locally on server CPU)
    if getattr(settings, "TTS_ENGINE", "kokoro").lower() == "kokoro":
        try:
            kokoro_voice = voice or getattr(settings, "KOKORO_VOICE", "af_heart")
            loop = asyncio.get_running_loop()
            audio_bytes = await loop.run_in_executor(None, _synthesize_kokoro_sync, clean_text, kokoro_voice)
            if audio_bytes:
                return audio_bytes
        except Exception as exc:
            logger.warning("Kokoro local synthesis failed (%s), falling back to Edge TTS", exc)

    # Fallback or explicit Edge TTS path
    edge_voice = voice or settings.TTS_VOICE
    audio_bytes = await _synthesize_edge_tts(clean_text, edge_voice)
    if not audio_bytes:
        raise RuntimeError("TTS returned empty audio.")
    return audio_bytes

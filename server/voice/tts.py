"""
Edge TTS text-to-speech handler for JARVIS.
Strips markdown, synthesises speech with a British male voice, and returns
raw MP3 bytes ready to base64-encode and ship over WebSocket.
"""
from __future__ import annotations

import io
import re

import edge_tts

from config import settings

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
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def text_to_speech(text: str, voice: str | None = None) -> bytes:
    """
    Convert text to MP3 audio bytes using Edge TTS.

    Args:
        text: The text to synthesise (markdown is stripped automatically).
        voice: Optional voice override; falls back to settings.TTS_VOICE.

    Returns:
        Raw MP3 bytes.

    Raises:
        RuntimeError: If Edge TTS fails to produce audio.
    """
    clean_text = strip_markdown(text)
    if not clean_text:
        return b""

    selected_voice = voice or settings.TTS_VOICE

    # Collect audio chunks into a buffer
    buffer = io.BytesIO()
    communicate = edge_tts.Communicate(clean_text, selected_voice)

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])

    audio_bytes = buffer.getvalue()
    if not audio_bytes:
        raise RuntimeError("Edge TTS returned empty audio — check the voice name.")

    return audio_bytes

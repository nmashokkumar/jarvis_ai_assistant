"""
Local Speech-to-Text (STT) handler for JARVIS using faster-whisper.
Runs on CPU with int8 quantization. Ultra-low latency (~190-250ms).
Decodes any audio format (WebM, WAV, OGG, MP4) via PyAV.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_whisper_instance = None
_whisper_init_attempted = False


def get_whisper():
    """Lazy-load and return the faster-whisper model singleton."""
    global _whisper_instance, _whisper_init_attempted
    if _whisper_instance is not None:
        return _whisper_instance
    if _whisper_init_attempted:
        return None

    _whisper_init_attempted = True
    model_name = "tiny.en"
    download_root = "/data/jarvis_models/whisper"
    if not os.path.exists(download_root):
        download_root = os.path.expanduser("~/.cache/jarvis/whisper")
        os.makedirs(download_root, exist_ok=True)

    try:
        from faster_whisper import WhisperModel
        logger.info("Initializing faster-whisper (%s) on CPU...", model_name)
        t0 = time.perf_counter()
        _whisper_instance = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            download_root=download_root,
            cpu_threads=4,
        )
        logger.info("faster-whisper initialized successfully in %.1fms.", (time.perf_counter() - t0) * 1000)
        return _whisper_instance
    except Exception as exc:
        logger.error("Failed to initialize faster-whisper: %s", exc)
        return None


def transcribe_audio_sync(audio_bytes: bytes) -> str:
    """
    Decodes audio bytes (WebM, WAV, OGG, MP4, FLAC) and transcribes them to text using faster-whisper.
    """
    if not audio_bytes:
        return ""

    model = get_whisper()
    if model is None:
        raise RuntimeError("Whisper STT model not available")

    # 1. Decode and resample to 16kHz mono float32 using PyAV
    import av
    container = av.open(io.BytesIO(audio_bytes))
    stream = next((s for s in container.streams if s.type == "audio"), None)
    if stream is None:
        raise ValueError("No audio stream found in audio data")

    resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
    frames = []
    for frame in container.decode(stream):
        frame.pts = None
        r_frames = resampler.resample(frame)
        for rf in r_frames:
            frames.append(rf.to_ndarray()[0])

    if not frames:
        return ""

    audio16k = np.concatenate(frames).astype(np.float32)

    # 2. Transcribe using faster-whisper
    segments, _ = model.transcribe(audio16k, beam_size=1, language="en")
    text = " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
    return text


async def transcribe_audio(audio_bytes: bytes) -> str:
    """Async wrapper to run audio transcription in default thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, transcribe_audio_sync, audio_bytes)

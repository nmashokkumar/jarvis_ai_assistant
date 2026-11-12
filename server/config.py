"""
JARVIS Configuration
Centralised settings loaded from environment variables with sensible defaults.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Ollama ────────────────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    OLLAMA_NUM_PREDICT: int = int(os.getenv("OLLAMA_NUM_PREDICT", "300"))
    OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))

    # ── Database ──────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "/opt/jarvis/jarvis.db")

    # ── Auth ──────────────────────────────────────────────────────────────────
    SECRET_TOKEN: str = os.getenv("SECRET_TOKEN", "jarvis-secret-change-me")

    # ── Conversation ──────────────────────────────────────────────────────────
    MAX_CONTEXT_MESSAGES: int = int(os.getenv("MAX_CONTEXT_MESSAGES", "20"))

    # ── Defaults ──────────────────────────────────────────────────────────────
    LOCATION_DEFAULT: str = os.getenv("LOCATION_DEFAULT", "Chennai, India")

    # ── TTS ───────────────────────────────────────────────────────────────────
    TTS_VOICE: str = os.getenv("TTS_VOICE", "en-IN-PrabhatNeural")


# Singleton
settings = Config()

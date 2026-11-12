"""
JARVIS — Personal AI Assistant
FastAPI application entry point.

Endpoints:
  GET  /api/health
  POST /api/chat
  WS   /ws/chat
  GET  /api/conversations
  DELETE /api/conversations/{id}
  GET  /api/memories
  DELETE /api/memories/{id}
  GET/POST/DELETE /api/reminders
  GET/POST/PATCH/DELETE /api/notes
  GET/POST /api/events
  GET/POST /api/settings
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from core.auth import require_auth, require_ws_auth, verify_token
from core.chat import ChatEngine
from core.llm import OllamaClient
from database.manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jarvis")

# ─────────────────────────────────────────────────────────────────────────────
# Globals (initialised in lifespan)
# ─────────────────────────────────────────────────────────────────────────────

db: DatabaseManager = None  # type: ignore[assignment]
llm: OllamaClient = None    # type: ignore[assignment]
engine: ChatEngine = None   # type: ignore[assignment]
scheduler: AsyncIOScheduler = None  # type: ignore[assignment]

# Active WebSocket connections for push notifications
_ws_clients: set[WebSocket] = set()


# ─────────────────────────────────────────────────────────────────────────────
# Reminder checker (runs every minute)
# ─────────────────────────────────────────────────────────────────────────────

async def _check_reminders() -> None:
    """Fire due reminders to all connected WebSocket clients."""
    if not db:
        return
    try:
        due = await db.get_due_reminders()
        for reminder in due:
            await db.complete_reminder(reminder["id"])
            payload = {
                "type": "reminder",
                "id": reminder["id"],
                "title": reminder["title"],
                "description": reminder.get("description", ""),
            }
            dead = set()
            for ws in _ws_clients:
                try:
                    await ws.send_json(payload)
                except Exception:  # noqa: BLE001
                    dead.add(ws)
            _ws_clients.difference_update(dead)
    except Exception as exc:  # noqa: BLE001
        logger.error("Reminder check failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# App lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, llm, engine, scheduler

    # Initialise DB
    db = DatabaseManager(settings.DB_PATH)
    await db.initialize()
    logger.info("Database initialised at %s", settings.DB_PATH)

    # Initialise LLM client
    llm = OllamaClient()
    healthy = await llm.is_healthy()
    logger.info("Ollama healthy: %s (model: %s)", healthy, settings.OLLAMA_MODEL)

    # Initialise chat engine
    engine = ChatEngine(db, llm)

    # Start scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_check_reminders, "interval", minutes=1, id="reminder_check")
    scheduler.start()
    logger.info("Scheduler started")

    yield

    # Teardown
    scheduler.shutdown(wait=False)
    await db.close()
    logger.info("JARVIS shutdown complete")


# ─────────────────────────────────────────────────────────────────────────────
# App instance
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="JARVIS API",
    version="1.0.0",
    description="Personal AI Assistant backend",
    lifespan=lifespan,
)

# CORS — allow all origins for local-network access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str
    mode: str = "regular"  # 'regular' | 'productivity'


class ChatResponse(BaseModel):
    response: str
    conversation_id: str


class ReminderCreate(BaseModel):
    title: str
    remind_at: str           # natural language or ISO datetime
    description: str = ""
    repeat: str = "none"


class NoteCreate(BaseModel):
    title: str
    content: str = ""
    category: str = "note"  # 'note' | 'todo'


class EventCreate(BaseModel):
    title: str
    start_time: str
    description: str = ""
    end_time: Optional[str] = None
    repeat: str = "none"


class SettingWrite(BaseModel):
    key: str
    value: str


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> Dict[str, Any]:
    """Public health-check endpoint — no auth required."""
    return {"status": "ok", "model": settings.OLLAMA_MODEL}


# ─────────────────────────────────────────────────────────────────────────────
# Chat (REST)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(require_auth)])
async def chat(req: ChatRequest) -> ChatResponse:
    conv_id = req.conversation_id or str(uuid.uuid4())
    response = await engine.process_message(conv_id, req.message, req.mode)
    return ChatResponse(response=response, conversation_id=conv_id)


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket streaming chat
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    """
    Streaming chat over WebSocket.

    Client sends JSON:
      { "message": "...", "conversation_id": "...", "mode": "regular", "tts": true }

    Server responds with:
      { "type": "token",    "content": "..." }   (streamed tokens)
      { "type": "audio",    "data": "<base64>" } (optional TTS audio)
      { "type": "done" }                          (end of turn)
      { "type": "reminder", ... }                 (push notification)
      { "type": "error",   "message": "..." }     (on failure)
    """
    # Auth check
    token = websocket.query_params.get("token") or websocket.headers.get("Authorization", "").replace("Bearer ", "")
    if not verify_token(token):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("WebSocket client connected (total: %d)", len(_ws_clients))

    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message", "").strip()
            conv_id = data.get("conversation_id") or str(uuid.uuid4())
            mode = data.get("mode", "regular")
            tts_enabled: bool = data.get("tts", False)

            if not message:
                await websocket.send_json({"type": "error", "message": "Empty message."})
                continue

            # Stream tokens + real-time sentence-by-sentence TTS for ultra-low latency
            full_tokens: list[str] = []
            sentence_buffer = ""
            first_chunk_sent = False

            async def _synthesize_and_send(sentence_text: str):
                clean = sentence_text.strip()
                if not clean or not tts_enabled:
                    return
                try:
                    from voice.tts import text_to_speech
                    audio_bytes = await text_to_speech(clean)
                    if audio_bytes:
                        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                        await websocket.send_json({"type": "audio", "data": audio_b64})
                except Exception as tts_exc:  # noqa: BLE001
                    logger.warning("TTS chunk failed: %s", tts_exc)

            async for token in engine.process_message_stream(conv_id, message, mode):
                full_tokens.append(token)
                await websocket.send_json({"type": "token", "content": token})

                if tts_enabled:
                    sentence_buffer += token

                    # Ultra-fast first phrase dispatch (after comma or ~4 words) so audio starts under 200ms
                    is_first_phrase = (
                        not first_chunk_sent
                        and any(sentence_buffer.endswith(p) for p in [", ", " - ", ": "])
                        and len(sentence_buffer.split()) >= 3
                    )
                    is_sentence = any(
                        sentence_buffer.endswith(p) for p in [". ", "? ", "! ", ".\n", "?\n", "!\n", "\n\n"]
                    ) or (len(sentence_buffer) > 65 and sentence_buffer.endswith(" "))

                    if is_first_phrase or is_sentence:
                        first_chunk_sent = True
                        sentence_to_send = sentence_buffer
                        sentence_buffer = ""
                        await _synthesize_and_send(sentence_to_send)

            # Synthesize any trailing text
            if tts_enabled and sentence_buffer.strip():
                await _synthesize_and_send(sentence_buffer)

            await websocket.send_json({"type": "done", "conversation_id": conv_id})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:  # noqa: BLE001
        logger.error("WebSocket error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        _ws_clients.discard(websocket)


# ─────────────────────────────────────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/conversations", dependencies=[Depends(require_auth)])
async def list_conversations() -> List[dict]:
    return await db.list_conversations()


@app.delete("/api/conversations/{conv_id}", dependencies=[Depends(require_auth)])
async def delete_conversation(conv_id: str) -> Dict[str, str]:
    await db.delete_conversation(conv_id)
    return {"status": "deleted", "id": conv_id}


# ─────────────────────────────────────────────────────────────────────────────
# Memories
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/memories", dependencies=[Depends(require_auth)])
async def list_memories() -> List[dict]:
    return await db.get_all_memories()


@app.delete("/api/memories/{memory_id}", dependencies=[Depends(require_auth)])
async def delete_memory(memory_id: int) -> Dict[str, str]:
    await db.delete_memory(memory_id)
    return {"status": "deleted", "id": str(memory_id)}


# ─────────────────────────────────────────────────────────────────────────────
# Reminders
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/reminders", dependencies=[Depends(require_auth)])
async def list_reminders_api() -> List[dict]:
    return await db.get_active_reminders()


@app.post("/api/reminders", dependencies=[Depends(require_auth)])
async def create_reminder(body: ReminderCreate) -> Dict[str, Any]:
    from tools.reminder import _parse_time

    remind_at = _parse_time(body.remind_at)
    if not remind_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse remind_at: '{body.remind_at}'",
        )
    reminder_id = await db.add_reminder(
        title=body.title,
        description=body.description,
        remind_at=remind_at,
        repeat=body.repeat,
    )
    return {"status": "created", "id": reminder_id}


@app.delete("/api/reminders/{reminder_id}", dependencies=[Depends(require_auth)])
async def delete_reminder(reminder_id: int) -> Dict[str, str]:
    await db.delete_reminder(reminder_id)
    return {"status": "deleted", "id": str(reminder_id)}


# ─────────────────────────────────────────────────────────────────────────────
# Notes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/notes", dependencies=[Depends(require_auth)])
async def list_notes_api(category: Optional[str] = None) -> List[dict]:
    return await db.get_notes(category=category)


@app.post("/api/notes", dependencies=[Depends(require_auth)])
async def create_note(body: NoteCreate) -> Dict[str, Any]:
    note_id = await db.add_note(
        title=body.title,
        content=body.content,
        category=body.category,
    )
    return {"status": "created", "id": note_id}


@app.patch("/api/notes/{note_id}/complete", dependencies=[Depends(require_auth)])
async def complete_note(note_id: int) -> Dict[str, str]:
    await db.complete_todo(note_id)
    return {"status": "completed", "id": str(note_id)}


@app.delete("/api/notes/{note_id}", dependencies=[Depends(require_auth)])
async def delete_note(note_id: int) -> Dict[str, str]:
    await db.delete_note(note_id)
    return {"status": "deleted", "id": str(note_id)}


# ─────────────────────────────────────────────────────────────────────────────
# Calendar Events
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/events", dependencies=[Depends(require_auth)])
async def list_events_api(days: int = 7) -> List[dict]:
    return await db.get_upcoming_events(days=days)


@app.post("/api/events", dependencies=[Depends(require_auth)])
async def create_event(body: EventCreate) -> Dict[str, Any]:
    from tools.calendar_tool import _parse_event_time

    start_time = _parse_event_time(body.start_time)
    if not start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse start_time: '{body.start_time}'",
        )
    end_time = _parse_event_time(body.end_time) if body.end_time else None

    event_id = await db.add_calendar_event(
        title=body.title,
        description=body.description,
        start_time=start_time,
        end_time=end_time,
        repeat=body.repeat,
    )
    return {"status": "created", "id": event_id}


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def get_settings() -> Dict[str, str]:
    return await db.get_all_settings()


@app.post("/api/settings", dependencies=[Depends(require_auth)])
async def set_setting(body: SettingWrite) -> Dict[str, str]:
    await db.set_setting(body.key, body.value)
    return {"status": "saved", "key": body.key}


# ─────────────────────────────────────────────────────────────────────────────
# Static files (frontend)
# ─────────────────────────────────────────────────────────────────────────────

import os as _os

_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")


# ─────────────────────────────────────────────────────────────────────────────
# Dev entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )

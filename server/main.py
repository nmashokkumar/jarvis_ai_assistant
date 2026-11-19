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

    # Pre-warm Kokoro TTS engine so the first spoken turn is instant
    if getattr(settings, "TTS_ENGINE", "kokoro").lower() == "kokoro":
        try:
            from voice.tts import _get_kokoro
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _get_kokoro)
            logger.info("Kokoro ONNX engine pre-warmed (voice: %s)", getattr(settings, 'KOKORO_VOICE', 'af_heart'))
        except Exception as tts_init_err:
            logger.warning("Kokoro pre-warm warning: %s", tts_init_err)

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


@app.get("/api/system/status", dependencies=[Depends(require_auth)])
async def system_status_api() -> Dict[str, Any]:
    """Return JSON system metrics and hardware stats."""
    from tools.system_monitor import get_system_status
    import psutil, time
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime = time.time() - psutil.boot_time()
    return {
        "status": "ok",
        "cpu_percent": cpu,
        "ram": {
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "percent": mem.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": disk.percent,
        },
        "uptime_seconds": int(uptime),
        "summary": get_system_status(),
    }



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
            # Robust TTS toggle: Default to True for JARVIS voice assistant
            raw_tts = data.get("tts")
            if raw_tts is None:
                tts_enabled = True
            elif isinstance(raw_tts, str):
                tts_enabled = raw_tts.lower() not in ("false", "0", "off", "no")
            else:
                tts_enabled = bool(raw_tts)

            logger.info("WS request: message='%s', tts_enabled=%s", message, tts_enabled)

            if not message:
                await websocket.send_json({"type": "error", "message": "Empty message."})
                continue

            # Concurrent sentence-by-sentence TTS producer-consumer pipeline
            full_tokens: list[str] = []
            sentence_buffer = ""
            first_chunk_sent = False
            tts_task: Optional[asyncio.Task] = None
            tts_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

            async def _tts_worker():
                """Concurrent background worker: synthesizes sentences as queued without blocking LLM stream."""
                if not tts_enabled:
                    return
                from voice.tts import text_to_speech
                while True:
                    sentence_text = await tts_queue.get()
                    if sentence_text is None:  # Sentinel
                        tts_queue.task_done()
                        break
                    clean = sentence_text.strip()
                    if clean:
                        try:
                            logger.info("[TTS] Synthesizing chunk: '%s'", clean)
                            audio_bytes = await text_to_speech(clean)
                            if audio_bytes:
                                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                                await websocket.send_json({"type": "audio", "data": audio_b64})
                                logger.info("[TTS] Sent audio chunk (%d bytes)", len(audio_bytes))
                        except Exception as tts_exc:  # noqa: BLE001
                            logger.warning("TTS chunk synthesis failed: %s", tts_exc)
                    tts_queue.task_done()

            if tts_enabled:
                tts_task = asyncio.create_task(_tts_worker())

            def _is_valid_sentence_break(buf: str) -> bool:
                stripped = buf.strip()
                if not stripped:
                    return False
                words = stripped.split()
                if not words:
                    return False
                last_w = words[-1].lower()
                if last_w in ("e.g.", "i.e.", "mr.", "mrs.", "dr.", "vs.", "prof.", "inc.", "ltd."):
                    return False
                if len(stripped) >= 2 and stripped[-1] == "." and stripped[-2].isdigit():
                    return False
                return True

            try:
                async for token in engine.process_message_stream(conv_id, message, mode):
                    full_tokens.append(token)
                    await websocket.send_json({"type": "token", "content": token})

                    if tts_enabled:
                        sentence_buffer += token
                        words = sentence_buffer.strip().split()

                        # First phrase fast-dispatch (~3-5 words ending with comma, colon, or dash)
                        # Allows audio to start playing in ~400ms before sentence 1 completes
                        is_first_phrase = (
                            not first_chunk_sent
                            and len(words) >= 3
                            and any(sentence_buffer.endswith(p) for p in [",", ":", " -", ";", ", ", ": "])
                        )

                        # Full sentence boundary: ends with '.', '?', '!', or '\n'
                        is_sentence_end = (
                            any(sentence_buffer.endswith(p) for p in [".", "?", "!", ".\n", "?\n", "!\n", "\n\n", ". ", "? ", "! "])
                            and _is_valid_sentence_break(sentence_buffer)
                            and len(words) >= 2
                        )

                        # Long sentence split (>= 16 words) on any natural pause
                        is_long_split = len(words) >= 16 and any(sentence_buffer.endswith(p) for p in [",", ";", " ", "\n"])

                        if is_first_phrase or is_sentence_end or is_long_split:
                            first_chunk_sent = True
                            to_queue = sentence_buffer
                            sentence_buffer = ""
                            await tts_queue.put(to_queue)

                # Flush any trailing tokens to TTS queue
                if tts_enabled and sentence_buffer.strip():
                    await tts_queue.put(sentence_buffer)

                # Await all queued audio chunks to finish synthesis
                if tts_enabled and tts_task:
                    await tts_queue.put(None)
                    await tts_task

            finally:
                if tts_task and not tts_task.done():
                    tts_task.cancel()

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
# Full-Duplex Live Voice WebSocket (/ws/live)
# Low-Latency Chunked Streaming + Real-time Interruption (Barge-In)
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """
    Persistent full-duplex Live Voice session endpoint.
    Handles concurrent upstream (utterance, chunk_played, interrupt) and
    downstream (tokens, state events, 24kHz raw PCM s16le chunks).
    """
    token = websocket.query_params.get("token") or websocket.headers.get("Authorization", "").replace("Bearer ", "")
    if not verify_token(token):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("Live Voice WebSocket client connected (/ws/live)")

    send_lock = asyncio.Lock()

    async def safe_send(payload: dict) -> bool:
        try:
            async with send_lock:
                await websocket.send_json(payload)
            return True
        except Exception:
            return False

    session_id = str(uuid.uuid4())
    active_turn_id: int = 0
    active_gen_task: Optional[asyncio.Task] = None
    active_tts_task: Optional[asyncio.Task] = None
    tts_queue: Optional[asyncio.Queue] = None

    # History & chunk playback accounting
    spoken_sentences: dict[int, str] = {}  # chunk_id -> clean sentence
    confirmed_chunks: set[int] = set()     # chunk_ids played by client
    turn_user_text: str = ""
    turn_conv_id: str = session_id
    turn_tool_name: Optional[str] = None
    turn_tool_result: Optional[str] = None
    turn_full_response_text: str = ""
    turn_assistant_msg_saved: bool = False

    async def abort_active_turn(interrupted_turn_id: int, client_confirmed: Optional[list[int]] = None) -> None:
        """Cancel active tasks, clear queues, and commit only confirmed spoken sentences to SQLite."""
        nonlocal active_gen_task, active_tts_task, tts_queue, turn_assistant_msg_saved

        if client_confirmed:
            for cid in client_confirmed:
                confirmed_chunks.add(int(cid))

        # 1. Cancel running generation and TTS tasks
        if active_gen_task and not active_gen_task.done():
            active_gen_task.cancel()
        if active_tts_task and not active_tts_task.done():
            active_tts_task.cancel()

        # 2. Drain pending TTS queue
        if tts_queue:
            while not tts_queue.empty():
                try:
                    tts_queue.get_nowait()
                    tts_queue.task_done()
                except Exception:
                    break

        # 3. Commit ONLY the confirmed played sentences to DB
        if not turn_assistant_msg_saved and turn_conv_id:
            played_pieces = [spoken_sentences[cid] for cid in sorted(confirmed_chunks) if cid in spoken_sentences]
            confirmed_text = " ".join(played_pieces).strip()
            if confirmed_text:
                turn_assistant_msg_saved = True
                logger.info(
                    "[Live Interruption] Turn %d interrupted. Saving only confirmed spoken text (%d words): '%s'",
                    interrupted_turn_id, len(confirmed_text.split()), confirmed_text
                )
                await db.save_message(
                    turn_conv_id,
                    "assistant",
                    confirmed_text,
                    tool_used=turn_tool_name,
                    tool_result=turn_tool_result,
                )
                asyncio.create_task(
                    engine.memory.extract_and_save_memories(turn_user_text, confirmed_text, db)
                )
            else:
                logger.info(
                    "[Live Interruption] Turn %d interrupted before any audio chunk finished playing. No assistant message saved.",
                    interrupted_turn_id
                )

    async def run_turn_generator(turn_id: int, user_text: str, conv_id: str, mode: str):
        nonlocal active_tts_task, tts_queue, turn_tool_name, turn_tool_result
        nonlocal turn_full_response_text, turn_assistant_msg_saved
        try:
            # 1. Tool detection & execution
            from tools.router import detect_tool, execute_tool
            from core.chat import calculate_num_predict
            tool_name, tool_params = detect_tool(user_text)
            tool_result = None

            if tool_name:
                if tool_name == "web_search":
                    resolved_info = await engine._resolve_search_query(
                        conv_id, user_text, tool_params.get("query")
                    )
                    tool_params["query"] = resolved_info["query"]
                    tool_params["category"] = resolved_info.get("category", "text")
                    tool_params["timelimit"] = resolved_info.get("timelimit")

                try:
                    tool_result = await execute_tool(tool_name, tool_params, user_text, db)
                except Exception as exc:
                    logger.warning("Live tool %s failed: %s", tool_name, exc)
                    tool_result = f"Tool error: {exc}"

            turn_tool_name = tool_name
            turn_tool_result = tool_result

            # 3. Assemble prompt
            messages = await engine._build_messages(
                conv_id, user_text, mode, tool_name, tool_result
            )

            # 4. Setup concurrent Kokoro PCM TTS Worker
            tts_queue = asyncio.Queue()
            from voice.tts import text_to_pcm, strip_for_tts

            async def _live_tts_worker():
                first_chunk = True
                while True:
                    item = await tts_queue.get()
                    if item is None:
                        tts_queue.task_done()
                        break
                    chunk_id, clean_text = item
                    try:
                        if turn_id != active_turn_id:
                            tts_queue.task_done()
                            break
                        pcm_bytes = await text_to_pcm(clean_text)
                        if pcm_bytes and turn_id == active_turn_id:
                            spoken_sentences[chunk_id] = clean_text
                            pcm_b64 = base64.b64encode(pcm_bytes).decode("ascii")
                            if first_chunk:
                                first_chunk = False
                                await safe_send({"type": "state", "state": "speaking", "turn_id": turn_id})
                            await safe_send({
                                "type": "pcm_chunk",
                                "turn_id": turn_id,
                                "chunk_id": chunk_id,
                                "text": clean_text,
                                "audio": pcm_b64,
                                "sample_rate": 24000,
                            })
                    except asyncio.CancelledError:
                        break
                    except Exception as err:
                        logger.warning("TTS chunk %d error: %s", chunk_id, err)
                    finally:
                        tts_queue.task_done()

            active_tts_task = asyncio.create_task(_live_tts_worker())

            # 5. Stream LLM tokens and split by sentence
            num_predict = calculate_num_predict(user_text, tool_name)
            parts: list[str] = []
            sentence_buffer = ""
            chunk_counter = 0
            first_chunk_sent = False

            def _is_valid_sentence_break(buf: str) -> bool:
                stripped = buf.strip()
                if not stripped:
                    return False
                words = stripped.split()
                if not words:
                    return False
                last_w = words[-1].lower()
                if last_w in ("e.g.", "i.e.", "mr.", "mrs.", "dr.", "vs.", "prof.", "inc.", "ltd."):
                    return False
                if len(stripped) >= 2 and stripped[-1] == "." and stripped[-2].isdigit():
                    return False
                return True

            async for token in llm.chat_stream(messages, mode, num_predict=num_predict):
                if turn_id != active_turn_id:
                    break
                parts.append(token)
                await safe_send({"type": "token", "content": token, "turn_id": turn_id})

                sentence_buffer += token
                words = sentence_buffer.strip().split()

                # Fast first-phrase dispatch (3-5 words ending with comma, colon, dash)
                is_first_phrase = (
                    not first_chunk_sent
                    and len(words) >= 3
                    and any(sentence_buffer.endswith(p) for p in [",", ":", " -", ";", ", ", ": "])
                )
                # Sentence boundary: ends with '.', '?', '!', '\n'
                is_sentence_end = (
                    any(sentence_buffer.endswith(p) for p in [".", "?", "!", ".\n", "?\n", "!\n", "\n\n", ". ", "? ", "! "])
                    and _is_valid_sentence_break(sentence_buffer)
                    and len(words) >= 2
                )
                # Long clause break (>= 16 words)
                is_long_split = len(words) >= 16 and any(sentence_buffer.endswith(p) for p in [",", ";", " ", "\n"])

                if is_first_phrase or is_sentence_end or is_long_split:
                    clean_chunk = strip_for_tts(sentence_buffer)
                    sentence_buffer = ""
                    if clean_chunk:
                        first_chunk_sent = True
                        await tts_queue.put((chunk_counter, clean_chunk))
                        chunk_counter += 1

            # Flush remaining buffer
            if sentence_buffer.strip():
                clean_chunk = strip_for_tts(sentence_buffer)
                if clean_chunk:
                    await tts_queue.put((chunk_counter, clean_chunk))
                    chunk_counter += 1

            turn_full_response_text = "".join(parts).strip()

            # Finish TTS
            if active_tts_task and not active_tts_task.done():
                await tts_queue.put(None)
                await active_tts_task

            # Notify client turn generation finished
            await safe_send({
                "type": "turn_complete",
                "turn_id": turn_id,
                "total_chunks": chunk_counter,
                "text": turn_full_response_text,
            })

            # If no audio chunks were generated (empty reply), return to listening
            if chunk_counter == 0 and turn_id == active_turn_id:
                await safe_send({"type": "state", "state": "listening", "turn_id": turn_id})

        except asyncio.CancelledError:
            logger.info("Live turn %d task cancelled", turn_id)
            if active_tts_task and not active_tts_task.done():
                active_tts_task.cancel()
            raise
        except Exception as exc:
            logger.error("Live turn %d pipeline error: %s", turn_id, exc, exc_info=True)
            await safe_send({"type": "error", "message": str(exc), "turn_id": turn_id})
            await safe_send({"type": "state", "state": "listening", "turn_id": turn_id})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "utterance":
                raw_text = data.get("text", "").strip()
                if not raw_text:
                    continue
                turn_id = data.get("turn_id", active_turn_id + 1)
                conv_id = data.get("conversation_id") or session_id
                mode = data.get("mode", "regular")

                # Abort any in-flight turn
                await abort_active_turn(active_turn_id)

                # Initialize new turn state
                active_turn_id = turn_id
                spoken_sentences.clear()
                confirmed_chunks.clear()
                turn_user_text = raw_text
                turn_conv_id = conv_id
                turn_full_response_text = ""
                turn_assistant_msg_saved = False

                # Ensure conversation exists and commit user utterance to history
                await db.get_or_create_conversation(conv_id, mode)
                await db.save_message(conv_id, "user", raw_text)

                logger.info("Live Voice Turn %d: '%s'", turn_id, raw_text)
                await safe_send({"type": "state", "state": "thinking", "turn_id": turn_id})
                active_gen_task = asyncio.create_task(
                    run_turn_generator(turn_id, raw_text, conv_id, mode)
                )

            elif msg_type == "chunk_played":
                t_id = data.get("turn_id")
                c_id = data.get("chunk_id")
                if t_id == active_turn_id and c_id is not None:
                    confirmed_chunks.add(int(c_id))

            elif msg_type == "playback_finished":
                t_id = data.get("turn_id")
                if t_id == active_turn_id:
                    # Normal completion without interruption!
                    if not turn_assistant_msg_saved and turn_full_response_text and turn_conv_id:
                        turn_assistant_msg_saved = True
                        logger.info("Live Voice Turn %d: Normal playback finished. Committing full response.", t_id)
                        await db.save_message(
                            turn_conv_id,
                            "assistant",
                            turn_full_response_text,
                            tool_used=turn_tool_name,
                            tool_result=turn_tool_result,
                        )
                        asyncio.create_task(
                            engine.memory.extract_and_save_memories(
                                turn_user_text, turn_full_response_text, db
                            )
                        )
                    await safe_send({"type": "state", "state": "listening", "turn_id": t_id})

            elif msg_type == "interrupt":
                t_id = data.get("turn_id", active_turn_id)
                confirmed = data.get("confirmed_chunks", [])
                logger.info("Live Voice Turn %d: User barge-in interrupt received!", t_id)
                await abort_active_turn(t_id, confirmed)
                await safe_send({"type": "state", "state": "listening", "turn_id": t_id, "interrupted": True})

            elif msg_type == "ping":
                await safe_send({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("Live Voice WebSocket client disconnected")
    except Exception as exc:
        logger.error("Live Voice WebSocket error: %s", exc)
    finally:
        await abort_active_turn(active_turn_id)


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

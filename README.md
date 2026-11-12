# 🤖 J.A.R.V.I.S. — Self-Hosted Personal AI Assistant
### *Powered by Small Language Models (SLMs) on Constrained Hardware*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11%2B-brightgreen.svg)](https://www.python.org/)
[![Model: Llama 3.2 3B](https://img.shields.io/badge/Model-Llama_3.2_3B_Q4__K__M-orange.svg)](https://huggingface.co/meta-llama/Llama-3.2-3B)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![WebSocket](https://img.shields.io/badge/WebSocket-Full_Duplex-purple.svg)](https://websockets.readthedocs.io/)

**J.A.R.V.I.S.** is a production-engineered, voice-enabled personal AI assistant inspired by Tony Stark's iconic interface. It is architected specifically to run on **resource-constrained CPU server hardware** (4 vCPUs, 8 GB RAM) utilizing **quantized Small Language Models (SLMs)**, asynchronous full-duplex WebSockets, sentence-chunked streaming Text-to-Speech (TTS), and persistent memory.

Designed from the ground up for personal utility, low latency, and 24/7 reliability without requiring expensive GPU infrastructure.

---

## ⚡ Engineering Highlights (At a Glance)

* 🧠 **Small Language Model (SLM) Optimization**: Runs **Llama 3.2 3B (Q4_K_M)** on pure CPU with bounded KV-cache (`num_ctx: 2048`) and thread pinning (`num_thread: 4`), achieving sub-second Time-To-First-Token (TTFT) and resident RAM persistence (`keep_alive: 24h`).
* ⚡ **Sentence-Level Low-Latency Streaming TTS**: Overcomes cloud TTS lag by synthesizing speech chunk-by-chunk on early punctuation boundaries. Voice begins playback in **~300ms** while subsequent sentences stream concurrently.
* 🎙️ **Hands-Free Live Conversation Mode**: Implements smart silence endpointing (~450ms breath detection) and Web Audio queue synchronization for fluid, hands-free conversational loops.
* 🛡️ **Zero-Crash Server Architecture**: Managed by `systemd` daemon with automatic failure restarts (`Restart=always`), reverse-proxied through **Nginx** on Debian 13, and secured with token-gated authentication.
* 🗄️ **Persistent Memory & Action Tooling**: SQLite database in **WAL mode** with automatic memory extraction, calendar event management, natural language reminders (via APScheduler), notes/todos, weather, and web search.

---

## 🏗️ System Architecture

```
[ Client: Laptop / Mobile Browser ]
             │
             │  (Full-Duplex WSS & HTTPS)
             ▼
[ Reverse Proxy: Nginx (Port 80) ]
             │
             │  (Reverse Proxy / Unix Socket)
             ▼
[ Application Server: FastAPI + Uvicorn (Port 8000) ]
   ├── Token Authentication Guard
   ├── WebSocket Event Router (/ws/chat)
   ├── Personality & Prompt Synthesizer (Plain Conversational English)
   ├── Tool Dispatcher (Reminders, Calendar, Notes, Web Search, Weather)
   └── Background Task Worker (APScheduler)
             │
      ┌──────┴───────────────────────────┬───────────────────────────┐
      ▼                                  ▼                           ▼
[ SLM Inference ]            [ Acoustic Voice Engine ]     [ Storage Engine ]
Ollama Engine (Local)        Edge TTS / Kokoro ONNX        SQLite (WAL Mode)
Llama 3.2 3B (Q4_K_M)        Sentence-level Audio Stream   Memories, Chat History,
4 Thread CPU Pinning         RTF: 0.17x on 4-Core CPU      Reminders & Directives
```

---

## 📊 Performance & Resource Benchmarks

Benchmarked directly on a headless Debian Linux server (**4 AMD vCPUs, 8 GB RAM, 20 GB SSD**):

| Metric | Target / Measured Value | Impact |
|---|---|---|
| **SLM Model Footprint** | **~2.2 GB RAM** (Llama 3.2 3B Q4_K_M) | Leaves > 5 GB free memory for OS & application buffer |
| **API Server Memory** | **~41.5 MB RAM** (FastAPI / Uvicorn) | Negligible server footprint |
| **Acoustic Real-Time Factor (RTF)** | **0.174x** (Kokoro ONNX on 4 vCPUs) | Generates ~6.8s of speech in ~1.18s (~6x faster than real-time) |
| **Time to First Audio Byte** | **~300ms** (Early phrase streaming) | Eliminates awkward pauses before speech begins |
| **Speech Silence Endpointing** | **~450ms** (Custom client debounce) | Slashes ~1.2s of native browser waiting time |

---

## 🗂️ Project Structure

```
Jarvis/
├── server/
│   ├── main.py                 # FastAPI application & WebSocket endpoints
│   ├── config.py               # Centralised configuration & environment loader
│   ├── requirements.txt        # Production Python dependencies
│   ├── core/
│   │   ├── chat.py             # Chat engine & turn orchestrator
│   │   ├── llm.py              # Ollama async client with CPU thread & context tuning
│   │   ├── memory.py           # Long-term memory extraction & semantic recall
│   │   ├── personality.py      # System prompts (Regular vs. Productivity modes)
│   │   └── auth.py             # Bearer & query parameter token verification
│   ├── database/
│   │   ├── models.py           # SQLite schema definitions (7 tables)
│   │   └── manager.py          # Asynchronous database manager (aiosqlite)
│   ├── tools/
│   │   ├── router.py           # Keyword intent detection & tool execution
│   │   ├── reminder.py         # Natural language time parser & reminder CRUD
│   │   ├── notes.py            # Notes & mission directives (todos) CRUD
│   │   ├── calendar_tool.py    # Calendar schedule manager
│   │   ├── weather.py          # Open-Meteo API integration (zero API key)
│   │   └── web_search.py       # DuckDuckGo live web intelligence
│   ├── voice/
│   │   └── tts.py              # Neural voice synthesis with markdown stripping
│   └── static/                 # Futuristic Stark-themed HUD web application
│       ├── index.html          # Responsive single-page application & Arc Reactor UI
│       ├── css/style.css       # Obsidian & electric cyan glassmorphism design system
│       └── js/
│           ├── app.js          # App orchestrator & Hands-Free Live Mode controller
│           ├── chat.js         # Streaming message renderer & markdown parser
│           ├── voice.js        # Web Speech API & queued audio player
│           ├── panels.js       # Slide-over HUD modules (Notes, Todos, Calendar)
│           └── utils.js        # Dynamic API client & configuration
├── deploy/
│   ├── setup.sh                # Automated, idempotent Linux server setup script
│   ├── jarvis.service          # Systemd unit file with automatic failure recovery
│   ├── nginx.conf              # Production Nginx reverse proxy configuration
│   └── backup.sh               # Automated daily database backup with 7-day retention
├── .gitignore
└── README.md
```

---

## 🛠️ Quick Start & Local Setup

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.12 & 3.13)
- Ollama installed with model: `ollama pull llama3.2:3b`

### 2. Installation
```bash
git clone https://github.com/nmashokkumar/Jarvis.git
cd Jarvis/server

# Create and activate virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy the template configuration:
```bash
cp .env.example .env
```
Edit `.env` to configure your settings:
```env
SECRET_TOKEN=your_secure_password
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
DB_PATH=./jarvis.db
TTS_VOICE=en-IN-PrabhatNeural
```

### 4. Run Development Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Navigate to `http://localhost:8000` in Google Chrome or Edge.

---

## 🚢 Production Server Deployment

The deployment pipeline is fully automated and verified on Debian 13 (Trixie) Linux:

```bash
# Run the automated deployment script with sudo privileges
sudo bash deploy/setup.sh
```

The script automatically:
1. Provisions directory hierarchy (`/opt/jarvis/{data,logs,backups}`).
2. Creates isolated virtual environment and installs dependencies.
3. Configures `.env` with secure tokens and database paths.
4. Installs and enables the `jarvis.service` systemd daemon.
5. Configures and reloads the Nginx reverse proxy with WebSocket buffering disabled.
6. Executes automated health check on `http://localhost/api/health`.

### Service Management Commands
```bash
# Check service status
sudo systemctl status jarvis

# View real-time service logs
sudo journalctl -u jarvis -f

# Restart the service
sudo systemctl restart jarvis
```

---

## 🧑‍💻 Developer

**Ashok Kumar N**  
*AI & Machine Learning Engineer*  
*Specializing in Small Language Model (SLM) Optimization, Low-Latency Real-Time Voice Pipelines, and Constrained Systems.*

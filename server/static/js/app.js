/**
 * JARVIS — app.js
 * Central Application Orchestrator + Hands-Free Live Conversation Mode.
 * Coordinates WebSocket streaming, Audio, Speech Recognition, Chat history & HUD UI.
 */

// ---------------------------------------------------------------------------
// LiveModeController — Gemini Live-style continuous hands-free voice chat
// ---------------------------------------------------------------------------
class LiveModeController {
  constructor(app) {
    this.app = app;
    this.isActive = false;
    this.isMuted = false;
    this.state = 'idle'; // 'idle' | 'listening' | 'thinking' | 'speaking' | 'paused'
    this._pendingTranscript = '';
    this._currentReplyText = '';
    this._awaitingAudioForTurn = false;
    this._restartTimer = null;

    // DOM references
    this.overlay = document.getElementById('live-overlay');
    this.orb = document.getElementById('live-orb');
    this.stateLabel = document.getElementById('live-state-label');
    this.userCaption = document.getElementById('live-user-caption');
    this.jarvisCaption = document.getElementById('live-jarvis-caption');
    this.connectionNote = document.getElementById('live-connection-note');
    this.muteBtn = document.getElementById('live-mute-btn');
    this.endBtn = document.getElementById('live-end-btn');

    this._silenceTimer = null;
    this._lastSpokenText = '';

    // Neural Silero VAD Manager for Instant Barge-In (Interruption)
    this._vad = new SileroVADManager({
      positiveSpeechThreshold: 0.5,
      negativeSpeechThreshold: 0.35,
      onSpeechStart: () => {
        if (!this.isActive || this.isMuted) return;
        // If Jarvis is currently speaking, Boss spoke -> INTERRUPT IMMEDIATELY!
        if (this.state === 'speaking' || this.app.audioPlayer.isPlaying) {
          console.log('[LiveMode] Silero VAD detected user speech during playback -> Cutting off Jarvis audio!');
          this.app.audioPlayer.stop();
          this.setState('listening');
          this.startListening();
        }
      },
      onSpeechEnd: () => {
        if (!this.isActive || this.isMuted) return;
        // When Silero VAD confirms voice stopped, trigger fast STT flush if text exists
        if (this.state === 'listening' && this._lastSpokenText) {
          console.log('[LiveMode] Silero VAD confirmed silence -> Dispatching query to Boss assistant.');
          const toSend = this._lastSpokenText;
          this._lastSpokenText = '';
          clearTimeout(this._silenceTimer);
          clearTimeout(this._restartTimer);
          this._stt.stopListening();
          this._handleUserSpoke(toSend);
        }
      }
    });

    // Dedicated SpeechRecognition instance for Live Mode with smart fast-endpointing
    this._stt = new SpeechRecognitionManager({
      onStart: () => {
        if (this.isActive && !this.isMuted) {
          this.setState('listening');
        }
      },
      onResult: ({ interim, final, isFinal }) => {
        const text = (final || interim || '').trim();
        if (this.userCaption && text) {
          this.userCaption.textContent = `"${text}"`;
        }
        if (text) {
          this._lastSpokenText = text;
          // Fast-endpointing: don't wait 1.5s for Chrome — dispatch 450ms after speech pauses!
          clearTimeout(this._silenceTimer);
          const words = text.split(/\s+/).length;
          const delay = isFinal ? 50 : (words >= 2 ? 450 : 800);
          this._silenceTimer = setTimeout(() => {
            if (this.isActive && this.state === 'listening' && this._lastSpokenText) {
              const toSend = this._lastSpokenText;
              this._lastSpokenText = '';
              clearTimeout(this._restartTimer);
              this._stt.stopListening();
              this._handleUserSpoke(toSend);
            }
          }, delay);
        }
      },
      onEnd: () => {
        if (!this.isActive) return;
        clearTimeout(this._silenceTimer);
        const text = this._lastSpokenText;
        this._lastSpokenText = '';

        if (text && this.state === 'listening') {
          this._handleUserSpoke(text);
        } else if (!this.isMuted && this.state === 'listening') {
          // Keep listening smoothly without user having to re-trigger
          this._scheduleListenRestart(200);
        }
      },
      onError: (err) => {
        if (!this.isActive) return;
        if (err === 'not-allowed' || err === 'service-not-allowed') {
          this.isMuted = true;
          this.setState('paused');
          this._updateMuteBtn();
          showToast('Microphone permission denied. Please allow mic access.', 'error');
        } else if (err === 'no-speech' && !this.isMuted && this.state === 'listening') {
          this._scheduleListenRestart(350);
        } else if (err !== 'aborted' && err !== 'no-speech') {
          console.warn('[LiveMode STT] error:', err);
          this.setState('paused');
        }
      }
    });

    this._bindEvents();
  }

  _bindEvents() {
    // Tapping the glowing orb either interrupts Jarvis (barge-in) or toggles listening
    if (this.orb) {
      this.orb.addEventListener('click', () => {
        if (!this.isActive) return;
        if (this.state === 'speaking') {
          // Interrupt Jarvis mid-sentence and immediately listen to Boss
          this.app.audioPlayer.stop();
          this.isMuted = false;
          this._updateMuteBtn();
          this.startListening();
        } else if (this.state === 'paused') {
          this.isMuted = false;
          this._updateMuteBtn();
          this.startListening();
        } else if (this.state === 'listening') {
          this._stt.stopListening();
        }
      });
    }

    if (this.muteBtn) {
      this.muteBtn.addEventListener('click', () => this.toggleMute());
    }

    if (this.endBtn) {
      this.endBtn.addEventListener('click', () => this.exit());
    }
  }

  enter() {
    if (!this._stt.supported) {
      showToast('Live voice mode requires Chrome or Edge browser with microphone support.', 'error');
      return;
    }

    // Unlock browser AudioContext inside this click gesture
    this.app.audioPlayer.unlock();
    this.app.micController?.stop();

    this.isActive = true;
    this.isMuted = false;
    this._pendingTranscript = '';
    this._currentReplyText = '';
    this._updateMuteBtn();

    if (this.userCaption) this.userCaption.textContent = '';
    if (this.jarvisCaption) this.jarvisCaption.textContent = 'Listening, Boss...';

    // Show subtle banner if server is not yet connected
    const isConnected = this.app.ws && this.app.ws.readyState === WebSocket.OPEN;
    if (this.connectionNote) {
      this.connectionNote.classList.toggle('hidden', isConnected);
    }

    if (this.overlay) {
      this.overlay.classList.remove('hidden');
      requestAnimationFrame(() => this.overlay.classList.add('open'));
    }

    // Start background Neural Silero VAD for instant voice barge-in
    this._vad.start().catch((e) => console.warn('[LiveMode] Silero VAD start warning:', e));

    this.startListening();
  }

  exit() {
    this.isActive = false;
    clearTimeout(this._restartTimer);
    this._stt.abort();
    this._vad.stop();
    this.app.audioPlayer.stop();
    this.setState('idle');

    if (this.overlay) {
      this.overlay.classList.remove('open');
      setTimeout(() => {
        if (!this.isActive) this.overlay.classList.add('hidden');
      }, 300);
    }
  }

  toggleMute() {
    this.isMuted = !this.isMuted;
    this._updateMuteBtn();

    if (this.isMuted) {
      clearTimeout(this._restartTimer);
      this._stt.abort();
      this._vad.stop();
      if (this.state === 'listening') this.setState('paused');
    } else {
      this._vad.start().catch(() => {});
      if (this.state === 'paused' || this.state === 'idle') {
        this.startListening();
      }
    }
  }

  _updateMuteBtn() {
    if (!this.muteBtn) return;
    this.muteBtn.classList.toggle('muted', this.isMuted);
    this.muteBtn.innerHTML = this.isMuted
      ? `<span>🔇</span><span>Resume Mic</span>`
      : `<span>🎙️</span><span>Pause Mic</span>`;
  }

  setState(newState) {
    this.state = newState;
    if (this.overlay) {
      this.overlay.dataset.state = newState;
    }

    const labels = {
      idle: 'STANDBY',
      listening: 'LISTENING TO BOSS...',
      thinking: 'THINKING...',
      speaking: 'J.A.R.V.I.S. SPEAKING (TAP ORB TO INTERRUPT)',
      paused: 'MIC PAUSED (TAP TO RESUME)'
    };
    if (this.stateLabel) {
      this.stateLabel.textContent = labels[newState] || '';
    }
  }

  startListening() {
    if (!this.isActive || this.isMuted) return;
    clearTimeout(this._restartTimer);
    this._pendingTranscript = '';
    this.setState('listening');
    this._stt.startListening();
  }

  _scheduleListenRestart(delay = 350) {
    clearTimeout(this._restartTimer);
    this._restartTimer = setTimeout(() => {
      if (this.isActive && !this.isMuted && !this.app.audioPlayer.isPlaying && this.state !== 'thinking') {
        this.startListening();
      }
    }, delay);
  }

  _handleUserSpoke(text) {
    this._currentReplyText = '';
    this._awaitingAudioForTurn = true;
    this.setState('thinking');
    if (this.jarvisCaption) this.jarvisCaption.textContent = '...';

    // Check if live server is connected
    if (this.app.ws && this.app.ws.readyState === WebSocket.OPEN) {
      this.app.sendMessage(text, { liveMode: true, forceTts: true });
    } else {
      // Offline UI Preview simulation so Boss can test the Live Mode experience locally
      this._runOfflinePreviewResponse(text);
    }
  }

  onToken(token) {
    if (!this.isActive) return;
    this._currentReplyText += token;
    if (this.jarvisCaption) {
      this.jarvisCaption.textContent = this._currentReplyText;
    }
  }

  onAudioReceived() {
    if (!this.isActive) return;
    this._awaitingAudioForTurn = false;
    this.setState('speaking');
  }

  onResponseDone() {
    if (!this.isActive) return;
    // If TTS was disabled or no audio frame arrived, resume listening right away
    setTimeout(() => {
      if (this.isActive && !this.app.audioPlayer.isPlaying && this._awaitingAudioForTurn) {
        this._awaitingAudioForTurn = false;
        if (!this.isMuted) this.startListening();
        else this.setState('paused');
      }
    }, 500);
  }

  onAudioPlaybackEnd() {
    if (!this.isActive) return;
    // Automatically reopen microphone after Jarvis finishes speaking!
    if (!this.isMuted) {
      this._scheduleListenRestart(300);
    } else {
      this.setState('paused');
    }
  }

  _runOfflinePreviewResponse(userText) {
    const lower = userText.toLowerCase();
    let reply = "I heard you loud and clear, Boss. Connect me to your Linux server in Settings and we'll have full live conversations.";
    if (lower.includes('schedule') || lower.includes('calendar')) {
      reply = "In preview mode right now, Boss. Once connected to your server, I'll check your schedule, cancel your 9 AM slot, and ask what you'd like to put there instead.";
    } else if (lower.includes('hello') || lower.includes('jarvis') || lower.includes('hi')) {
      reply = "At your service, Boss. Live voice mode is active. What's on your mind today?";
    }

    setTimeout(() => {
      if (!this.isActive) return;
      if (this.jarvisCaption) this.jarvisCaption.textContent = reply;
      this.setState('speaking');
      this.app.audioPlayer.speakBrowserFallback(reply, () => {
        if (this.isActive && !this.isMuted) {
          this.startListening();
        }
      });
    }, 600);
  }
}

// ---------------------------------------------------------------------------
// Main Application Orchestrator
// ---------------------------------------------------------------------------
class JarvisApp {
  constructor() {
    this.token = localStorage.getItem('jarvis_token') || '';
    this.currentMode = localStorage.getItem('jarvis_mode') || 'regular';
    this.ttsEnabled = localStorage.getItem('jarvis_tts') !== 'false';
    this.activeConversationId = null;
    this.isThinking = false;
    this.ws = null;
    this._wsReconnectAttempts = 0;
    this._reconnectTimer = null;
    this._eventsBound = false; // Prevents duplicate listener registration on login!

    // DOM Elements
    this.loginScreen = document.getElementById('login-screen');
    this.appScreen = document.getElementById('app-screen');
    this.chatContainer = document.getElementById('chat-stream');
    this.messageInput = document.getElementById('message-input');
    this.sendBtn = document.getElementById('send-btn');
    this.micBtn = document.getElementById('mic-btn');
    this.liveBtn = document.getElementById('live-mode-btn');
    this.headerLiveBtn = document.getElementById('header-live-btn');
    this.modeToggleBtn = document.getElementById('mode-toggle-btn');
    this.newChatBtn = document.getElementById('new-chat-btn');
    this.sidebarToggleBtn = document.getElementById('sidebar-toggle');
    this.sidebar = document.getElementById('sidebar');
    this.sidebarOverlay = document.getElementById('sidebar-overlay');
    this.statusIndicator = document.getElementById('status-indicator');
    this.statusText = document.getElementById('status-text');
    this.emptyState = document.getElementById('empty-state');

    // Core Modules
    this.audioPlayer = new AudioPlayer({
      onPlayStart: () => {
        if (this.liveMode?.isActive) this.liveMode.setState('speaking');
      },
      onPlayEnd: () => {
        if (this.liveMode?.isActive) this.liveMode.onAudioPlaybackEnd();
      }
    });

    this.renderer = new MessageRenderer(this.chatContainer);
    this.convManager = new ConversationManager(
      document.getElementById('conv-list'),
      (id) => this.switchConversation(id),
      (id) => this.handleConversationDeleted(id)
    );
    this.panelManager = new PanelManager();
    window.panelManager = this.panelManager;

    this.liveMode = new LiveModeController(this);
    this.micController = null;
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------
  async init() {
    // Bind UI events ONLY ONCE so login doesn't register duplicate click handlers
    if (!this._eventsBound) {
      this._bindEvents();
      this._eventsBound = true;
    }

    if (!this.token) {
      this.showLogin();
      return;
    }

    this.showApp();
    this.updateModeUI();
    this._initMic();
    this.startNewChat();

    // Load history & open WebSocket (fails gracefully when previewing locally)
    this.connectWebSocket();
    try {
      await this.convManager.load();
    } catch (_) {
      // Offline preview mode handled quietly
    }
  }

  showLogin() {
    if (this.loginScreen) this.loginScreen.classList.remove('hidden');
    if (this.appScreen) this.appScreen.classList.add('hidden');
    const input = document.getElementById('login-token-input');
    const urlInput = document.getElementById('login-server-url');
    if (urlInput && localStorage.getItem('jarvis_server_url')) {
      urlInput.value = localStorage.getItem('jarvis_server_url');
    }
    if (input) input.focus();
  }

  showApp() {
    if (this.loginScreen) this.loginScreen.classList.add('hidden');
    if (this.appScreen) this.appScreen.classList.remove('hidden');
  }

  // ---------------------------------------------------------------------------
  // Event Bindings
  // ---------------------------------------------------------------------------
  _bindEvents() {
    // Login form
    const loginBtn = document.getElementById('login-submit-btn');
    const loginInput = document.getElementById('login-token-input');
    if (loginBtn && loginInput) {
      loginBtn.addEventListener('click', () => this._handleLogin());
      loginInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') this._handleLogin();
      });
    }

    // Send button & Textarea
    if (this.sendBtn) {
      this.sendBtn.addEventListener('click', () => this.sendMessage());
    }

    if (this.messageInput) {
      this.messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.sendMessage();
        }
      });

      // Auto-resize textarea
      this.messageInput.addEventListener('input', () => {
        this.messageInput.style.height = 'auto';
        this.messageInput.style.height = Math.min(this.messageInput.scrollHeight, 150) + 'px';
      });
    }

    // Live Mode triggers (dock button + header button)
    if (this.liveBtn) {
      this.liveBtn.addEventListener('click', () => this.liveMode.enter());
    }
    if (this.headerLiveBtn) {
      this.headerLiveBtn.addEventListener('click', () => this.liveMode.enter());
    }

    // New Chat button
    if (this.newChatBtn) {
      this.newChatBtn.addEventListener('click', () => this.startNewChat());
    }

    // Mode Toggle
    if (this.modeToggleBtn) {
      this.modeToggleBtn.addEventListener('click', () => this.toggleMode());
    }

    // Mobile Sidebar toggle
    if (this.sidebarToggleBtn && this.sidebar) {
      this.sidebarToggleBtn.addEventListener('click', () => {
        this.sidebar.classList.toggle('open');
        if (this.sidebarOverlay) this.sidebarOverlay.classList.toggle('active');
      });
    }

    if (this.sidebarOverlay) {
      this.sidebarOverlay.addEventListener('click', () => {
        this.sidebar.classList.remove('open');
        this.sidebarOverlay.classList.remove('active');
      });
    }

    // Panel Navigation buttons (Reminders, Todos, Notes, Calendar, Settings)
    document.querySelectorAll('.panel-nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const panel = btn.dataset.panel;
        if (this.panelManager.activePanel === panel) {
          this.panelManager.close();
        } else {
          this.panelManager.open(panel);
        }
      });
    });

    // Quick starter chips in empty state
    document.querySelectorAll('.starter-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const prompt = chip.dataset.prompt;
        if (prompt && this.messageInput) {
          this.messageInput.value = prompt;
          this.sendMessage();
        }
      });
    });
  }

  _handleLogin() {
    const input = document.getElementById('login-token-input');
    const urlInput = document.getElementById('login-server-url');
    const val = input ? input.value.trim() : '';
    if (!val) {
      showToast('Access token is required', 'error');
      return;
    }
    if (urlInput && urlInput.value.trim()) {
      CONFIG.setServerUrl(urlInput.value.trim());
    }
    this.token = val;
    localStorage.setItem('jarvis_token', val);
    this.init();
  }

  _initMic() {
    if (!this.micController && this.micBtn) {
      this.micController = new MicButton(this.micBtn, {
        onTranscript: (text) => {
          if (text) this.sendMessage(text);
        },
        audioPlayer: this.audioPlayer,
      });
    }
  }

  // ---------------------------------------------------------------------------
  // WebSocket Connection
  // ---------------------------------------------------------------------------
  reconnectWebSocket() {
    clearTimeout(this._reconnectTimer);
    this._wsReconnectAttempts = 0;
    if (this.ws) {
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }
    this.connectWebSocket();
  }

  connectWebSocket() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.setStatus('connecting', 'CONNECTING...');
    const wsUrl = `${CONFIG.getWsUrl('/ws/chat')}?token=${encodeURIComponent(this.token)}`;

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this._wsReconnectAttempts = 0;
        this.setStatus('online', 'ONLINE');
        console.log('[JARVIS] Connected to server:', CONFIG.getServerUrl());
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.handleWsMessage(data);
        } catch (err) {
          console.error('[JARVIS] Error parsing WebSocket message:', err);
        }
      };

      this.ws.onclose = (event) => {
        const label = CONFIG.isLocalFilePreview() ? 'PREVIEW MODE' : 'OFFLINE';
        this.setStatus('offline', label);

        if (event.code === 4001 || event.code === 4003) {
          showToast('Invalid access token. Please re-authenticate.', 'error');
          localStorage.removeItem('jarvis_token');
          this.showLogin();
          return;
        }

        // Only auto-reconnect continuously when hosted or explicitly connected
        if (!CONFIG.isLocalFilePreview()) {
          this._scheduleReconnect();
        }
      };

      this.ws.onerror = () => {
        const label = CONFIG.isLocalFilePreview() ? 'PREVIEW MODE' : 'OFFLINE';
        this.setStatus('offline', label);
      };
    } catch (err) {
      console.error('[JARVIS] WebSocket init error:', err);
      this.setStatus('offline', 'OFFLINE');
    }
  }

  _scheduleReconnect() {
    clearTimeout(this._reconnectTimer);
    const delay = Math.min(1000 * Math.pow(2, this._wsReconnectAttempts), 15000);
    this._wsReconnectAttempts++;
    this._reconnectTimer = setTimeout(() => {
      if (this.token) this.connectWebSocket();
    }, delay);
  }

  setStatus(state, label) {
    if (!this.statusIndicator || !this.statusText) return;
    this.statusIndicator.className = `status-dot ${state}`;
    this.statusText.textContent = label;
  }

  // ---------------------------------------------------------------------------
  // Chat Messaging
  // ---------------------------------------------------------------------------
  async sendMessage(overrideText = null, { liveMode = false, forceTts = false } = {}) {
    const text = (overrideText !== null ? overrideText : this.messageInput.value).trim();
    if (!text || this.isThinking) return;

    // Clear input field if sent via text box
    if (this.messageInput && overrideText === null) {
      this.messageInput.value = '';
      this.messageInput.style.height = 'auto';
    }

    if (this.emptyState) this.emptyState.classList.add('hidden');

    if (!this.activeConversationId) {
      this.activeConversationId = generateId();
    }

    // Add user bubble to chat history
    this.renderer.renderMessage('user', text, null, true);
    this.audioPlayer.stop();

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.setThinking(true);
      this.audioPlayer.startTurn();
      this.renderer.startStreaming();

      const payload = {
        message: text,
        conversation_id: this.activeConversationId,
        mode: this.currentMode,
        tts: forceTts || this.ttsEnabled,
        live_mode: liveMode
      };
      this.ws.send(JSON.stringify(payload));
    } else {
      showToast('Not connected to server yet. Check System Settings (⚙️) to configure server URL.', 'error');
    }
  }

  handleWsMessage(data) {
    switch (data.type) {
      case 'token':
        this.renderer.appendToStreaming(data.content || '');
        if (this.liveMode?.isActive) {
          this.liveMode.onToken(data.content || '');
        }
        break;

      case 'audio':
        if (data.data && (this.ttsEnabled || this.liveMode?.isActive)) {
          if (this.liveMode?.isActive) this.liveMode.onAudioReceived();
          this.audioPlayer.playBase64Audio(data.data);
        }
        break;

      case 'done':
        this.setThinking(false);
        this.renderer.finalizeStreaming(data.tool_used);
        this.audioPlayer.endTurn();
        if (this.liveMode?.isActive) this.liveMode.onResponseDone();
        this.convManager.load();
        break;

      case 'reminder':
        showToast(`⏰ REMINDER: ${data.title}`, 'reminder');
        break;

      case 'error':
        this.setThinking(false);
        this.renderer.finalizeStreaming();
        if (this.liveMode?.isActive) this.liveMode.onResponseDone();
        showToast(data.message || 'Error processing request', 'error');
        break;

      default:
        console.log('[JARVIS] Unknown WebSocket frame:', data);
    }
  }

  setThinking(thinking) {
    this.isThinking = thinking;
    const indicator = document.getElementById('thinking-indicator');
    if (indicator) {
      indicator.classList.toggle('active', thinking);
    }
    if (this.sendBtn) {
      this.sendBtn.disabled = thinking;
    }
  }

  // ---------------------------------------------------------------------------
  // Conversation & Mode Controls
  // ---------------------------------------------------------------------------
  startNewChat() {
    this.activeConversationId = generateId();
    this.renderer.clear();
    this.convManager.setActive(null);
    if (this.emptyState) this.emptyState.classList.remove('hidden');
    if (this.messageInput) this.messageInput.focus();

    if (this.sidebar) this.sidebar.classList.remove('open');
    if (this.sidebarOverlay) this.sidebarOverlay.classList.remove('active');
  }

  async switchConversation(id) {
    if (this.activeConversationId === id) return;
    this.activeConversationId = id;
    this.renderer.clear();
    if (this.emptyState) this.emptyState.classList.add('hidden');

    if (this.sidebar) this.sidebar.classList.remove('open');
    if (this.sidebarOverlay) this.sidebarOverlay.classList.remove('active');

    try {
      const data = await API.get(`/api/conversations/${id}/messages`);
      const messages = Array.isArray(data) ? data : (data.messages || []);
      messages.forEach(m => {
        this.renderer.renderMessage(m.role, m.content, m.tool_used, false);
      });
    } catch (err) {
      showToast('Failed to load conversation history', 'error');
    }
  }

  handleConversationDeleted(id) {
    if (this.activeConversationId === id) {
      this.startNewChat();
    }
  }

  toggleMode() {
    const nextMode = this.currentMode === 'regular' ? 'productivity' : 'regular';
    this.setMode(nextMode);
  }

  setMode(mode) {
    this.currentMode = mode;
    localStorage.setItem('jarvis_mode', mode);
    this.updateModeUI();
    showToast(`Switched to ${mode.toUpperCase()} mode`, 'info');
  }

  updateModeUI() {
    const isProd = this.currentMode === 'productivity';
    if (this.modeToggleBtn) {
      this.modeToggleBtn.innerHTML = isProd
        ? `<span class="mode-icon">⚡</span> Productivity Mode`
        : `<span class="mode-icon">🤖</span> Regular Mode`;
      this.modeToggleBtn.classList.toggle('prod-mode', isProd);
    }

    // Update top header mode pill if present
    const headerModeBadge = document.getElementById('header-mode-badge');
    if (headerModeBadge) {
      headerModeBadge.textContent = isProd ? '⚡ PRODUCTIVITY' : '🤖 REGULAR';
      headerModeBadge.classList.toggle('prod-mode', isProd);
    }
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  window.jarvisApp = new JarvisApp();
  window.jarvisApp.init();
});

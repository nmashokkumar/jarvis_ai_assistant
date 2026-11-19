/**
 * JARVIS — voice.js
 * SpeechRecognition, AudioPlayer (with playback callbacks for Live Mode), and MicButton controller.
 */

// ---------------------------------------------------------------------------
// LiveAudioQueue — Full-Duplex Web Audio API Scheduled Raw PCM (24kHz s16le)
// Gapless continuous streaming + instant <10ms barge-in interruption
// ---------------------------------------------------------------------------
class LiveAudioQueue {
  constructor({ onChunkPlayed, onPlayStart, onPlayEnd, onAudioLevel } = {}) {
    this.onChunkPlayed = onChunkPlayed || null;
    this.onPlayStart = onPlayStart || null;
    this.onPlayEnd = onPlayEnd || null;
    this.onAudioLevel = onAudioLevel || null;

    this.ctx = null;
    this.analyser = null;
    this.nextStartTime = 0;
    this.activeSources = new Set();
    this.isPlaying = false;
    this.currentTurnId = 0;
    this.confirmedChunks = new Set();
    this._animId = null;
  }

  initContext() {
    if (!this.ctx || this.ctx.state === 'closed') {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      try {
        this.ctx = new AudioCtx({ sampleRate: 24000 });
      } catch (_) {
        this.ctx = new AudioCtx();
      }
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 64;
      this.analyser.smoothingTimeConstant = 0.5;
      this.analyser.connect(this.ctx.destination);
    }
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }
    return this.ctx;
  }

  unlock() {
    this.initContext();
  }

  startTurn(turnId) {
    this.currentTurnId = turnId;
    this.confirmedChunks.clear();
    const ctx = this.initContext();
    this.nextStartTime = ctx.currentTime;
  }

  enqueuePcmChunk(turnId, chunkId, base64Pcm, sampleRate = 24000) {
    // Drop late-arriving packets from stale / interrupted turns
    if (turnId !== this.currentTurnId) {
      console.log(`[LiveAudioQueue] Dropping stale chunk ${chunkId} for turn ${turnId} (active: ${this.currentTurnId})`);
      return;
    }

    const ctx = this.initContext();
    const binary = atob(base64Pcm);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binary.charCodeAt(i);
    }

    // Convert 16-bit signed little-endian PCM to 32-bit float [-1.0, 1.0]
    const int16Count = Math.floor(len / 2);
    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, int16Count);
    const buffer = ctx.createBuffer(1, int16Count, sampleRate);
    const channelData = buffer.getChannelData(0);
    for (let i = 0; i < int16Count; i++) {
      channelData[i] = int16[i] / 32768.0;
    }

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    if (this.analyser) {
      source.connect(this.analyser);
    } else {
      source.connect(ctx.destination);
    }

    const now = ctx.currentTime;
    const startTime = Math.max(now, this.nextStartTime);
    source.start(startTime);
    this.nextStartTime = startTime + buffer.duration;
    this.activeSources.add(source);

    if (!this.isPlaying) {
      this.isPlaying = true;
      this.onPlayStart?.();
      this._startVisualizer();
    }

    source.onended = () => {
      this.activeSources.delete(source);
      if (turnId === this.currentTurnId) {
        this.confirmedChunks.add(chunkId);
        this.onChunkPlayed?.(turnId, chunkId);
      }
      if (this.activeSources.size === 0) {
        this.isPlaying = false;
        this._stopVisualizer();
        this.onPlayEnd?.(turnId);
      }
    };
  }

  stop() {
    this.activeSources.forEach(src => {
      try {
        src.onended = null;
        src.stop();
      } catch (_) {}
    });
    this.activeSources.clear();
    this.isPlaying = false;
    this._stopVisualizer();
    if (this.ctx) {
      this.nextStartTime = this.ctx.currentTime;
    }
  }

  interrupt() {
    const played = Array.from(this.confirmedChunks);
    this.currentTurnId++; // Invalidate stale packets
    this.stop();
    return played;
  }

  _startVisualizer() {
    if (this._animId) return;
    const data = new Uint8Array(this.analyser ? this.analyser.frequencyBinCount : 0);
    const loop = () => {
      if (!this.isPlaying) {
        this._animId = null;
        return;
      }
      if (this.analyser) {
        this.analyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        const avg = sum / (data.length * 255.0);
        this.onAudioLevel?.(avg);
      }
      this._animId = requestAnimationFrame(loop);
    };
    this._animId = requestAnimationFrame(loop);
  }

  _stopVisualizer() {
    if (this._animId) {
      cancelAnimationFrame(this._animId);
      this._animId = null;
    }
    this.onAudioLevel?.(0);
  }
}

// ---------------------------------------------------------------------------
// AudioPlayer — plays base64-encoded MP3 from server TTS
// ---------------------------------------------------------------------------
class AudioPlayer {
  constructor({ onPlayStart, onPlayEnd } = {}) {
    this._ctx = null;
    this._source = null;
    this.isPlaying = false;
    this._queue = [];
    this._processing = false;
    this._turnActive = false;
    this.onPlayStart = onPlayStart || null;
    this.onPlayEnd = onPlayEnd || null;
  }

  startTurn() {
    this._turnActive = true;
  }

  endTurn() {
    this._turnActive = false;
    if (!this._processing && !this.isPlaying && this._queue.length === 0) {
      this.onPlayEnd?.();
    }
  }

  /**
   * Unlocks or creates the Web AudioContext.
   * Should be called inside a user gesture (e.g. click, keydown, "Enter Live Mode").
   */
  unlock() {
    try {
      const ctx = this._getCtx();
      if (ctx && ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
    } catch (_) {}
  }

  _getCtx() {
    if (!this._ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        this._ctx = new AudioCtx();
      }
    }
    if (this._ctx && this._ctx.state === 'suspended') {
      this._ctx.resume().catch(() => {});
    }
    return this._ctx;
  }

  async playBase64Audio(base64data) {
    if (!base64data) return;
    this._queue.push(base64data);
    if (!this._processing) this._processQueue();
  }

  async _processQueue() {
    this._processing = true;
    this.isPlaying = true;
    this.onPlayStart?.();

    while (this._queue.length) {
      const b64 = this._queue.shift();
      await this._play(b64);
    }

    this._processing = false;
    this.isPlaying = false;

    // Only fire onPlayEnd when turn is completely finished
    if (!this._turnActive && this._queue.length === 0) {
      this.onPlayEnd?.();
    }
  }

  async _play(base64data) {
    if (!base64data) return;

    // 1. Primary: Web Audio API (Low latency, sample-accurate queuing)
    try {
      const ctx = this._getCtx();
      if (ctx) {
        if (ctx.state === 'suspended') {
          await ctx.resume().catch(() => {});
        }
        if (ctx.state === 'running') {
          const played = await new Promise((resolve) => {
            try {
              const binary = atob(base64data);
              const bytes = new Uint8Array(binary.length);
              for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

              ctx.decodeAudioData(
                bytes.buffer.slice(0),
                (buffer) => {
                  const source = ctx.createBufferSource();
                  source.buffer = buffer;
                  source.connect(ctx.destination);
                  this._source = source;
                  source.onended = () => {
                    this._source = null;
                    resolve(true);
                  };
                  source.start(0);
                },
                (err) => {
                  console.warn('[AudioPlayer] Web Audio decode error, falling back to HTML5 Audio:', err);
                  resolve(false);
                }
              );
            } catch (err) {
              console.warn('[AudioPlayer] Web Audio exception, falling back to HTML5 Audio:', err);
              resolve(false);
            }
          });

          if (played) return;
        }
      }
    } catch (_) {}

    // 2. Universal Fallback: HTML5 Audio (Plays reliably across all mobile & desktop browsers)
    await this._playHtml5Audio(base64data);
  }

  async _playHtml5Audio(base64data) {
    return new Promise((resolve) => {
      try {
        const audio = new Audio("data:audio/wav;base64," + base64data);
        audio.onended = () => resolve();
        audio.onerror = (e) => {
          console.warn('[AudioPlayer] HTML5 audio error:', e);
          resolve();
        };
        const playPromise = audio.play();
        if (playPromise !== undefined) {
          playPromise.catch((err) => {
            console.warn('[AudioPlayer] HTML5 audio play blocked:', err);
            resolve();
          });
        }
      } catch (err) {
        console.warn('[AudioPlayer] HTML5 audio fallback failed:', err);
        resolve();
      }
    });
  }

  /**
   * Browser-native SpeechSynthesis fallback (used only in offline preview/demo mode).
   */
  speakBrowserFallback(text, onEnd) {
    if (!('speechSynthesis' in window)) {
      onEnd?.();
      return;
    }
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(stripMarkdown(text));
    utter.rate = 1.02;
    utter.pitch = 0.95;
    this.isPlaying = true;
    this.onPlayStart?.();

    utter.onend = () => {
      this.isPlaying = false;
      this.onPlayEnd?.();
      onEnd?.();
    };
    utter.onerror = () => {
      this.isPlaying = false;
      this.onPlayEnd?.();
      onEnd?.();
    };
    window.speechSynthesis.speak(utter);
  }

  stop() {
    this._queue = [];
    if (this._source) {
      try {
        this._source.onended = null;
        this._source.stop();
      } catch (_) {}
      this._source = null;
    }
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch (_) {}
    }
    const wasPlaying = this.isPlaying;
    this.isPlaying = false;
    this._processing = false;
    if (wasPlaying) this.onPlayEnd?.();
  }
}

// ---------------------------------------------------------------------------
// SpeechRecognitionManager
// ---------------------------------------------------------------------------
class SpeechRecognitionManager {
  constructor({ onResult, onStart, onEnd, onError } = {}) {
    this.onResult = onResult;
    this.onStart = onStart;
    this.onEnd = onEnd;
    this.onError = onError;
    this.isListening = false;
    this._recognition = null;
    this._supported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  get supported() { return this._supported; }

  _create() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const r = new SR();
    r.continuous = true; // Continuous listening so browser doesn't abruptly cut off mid-thought!
    r.interimResults = true;
    r.lang = 'en-IN'; // Indian English recognition for accurate accent comprehension
    r.maxAlternatives = 1;

    r.onstart = () => {
      this.isListening = true;
      this.onStart?.();
    };

    r.onresult = (e) => {
      let interim = '';
      let final = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) final += t;
        else interim += t;
      }
      this.onResult?.({ interim, final, isFinal: !!final });
    };

    r.onend = () => {
      this.isListening = false;
      this.onEnd?.();
    };

    r.onerror = (e) => {
      this.isListening = false;
      this.onError?.(e.error);
    };

    return r;
  }

  startListening() {
    if (!this._supported) {
      showToast('Speech recognition is not supported in this browser. Please use Chrome or Edge.', 'error');
      return false;
    }
    if (this.isListening) return true;
    try {
      this._recognition = this._create();
      this._recognition.start();
      return true;
    } catch (err) {
      console.error('[STT] start error:', err);
      this.onError?.(err.message);
      return false;
    }
  }

  stopListening() {
    if (this._recognition) {
      try { this._recognition.stop(); } catch (_) {}
      this._recognition = null;
    }
    this.isListening = false;
  }

  abort() {
    if (this._recognition) {
      try { this._recognition.abort(); } catch (_) {}
      this._recognition = null;
    }
    this.isListening = false;
  }
}

// ---------------------------------------------------------------------------
// MicButton — standard push-to-talk / click-to-toggle for text chat dock
// ---------------------------------------------------------------------------
class MicButton {
  constructor(btnEl, { onTranscript, audioPlayer }) {
    this.btn = btnEl;
    this.onTranscript = onTranscript;
    this.audioPlayer = audioPlayer;
    this._interimEl = null;
    this._pendingFinal = '';

    this._stt = new SpeechRecognitionManager({
      onResult: ({ interim, final, isFinal }) => {
        this._updateInterim(interim || final);
        if (isFinal) {
          this._pendingFinal = final.trim();
        }
      },
      onStart: () => this._setRecording(true),
      onEnd: () => {
        this._setRecording(false);
        this._clearInterim();
        if (this._pendingFinal) {
          this.onTranscript(this._pendingFinal);
          this._pendingFinal = '';
        }
      },
      onError: (err) => {
        this._setRecording(false);
        this._clearInterim();
        if (err !== 'no-speech' && err !== 'aborted') {
          showToast(`Microphone error: ${err}`, 'error');
        }
      },
    });

    this._bind();
  }

  _bind() {
    this.btn.addEventListener('click', () => {
      if (this._stt.isListening) {
        this._stt.stopListening();
      } else {
        this.audioPlayer?.stop();
        this._stt.startListening();
      }
    });

    if (!this._stt.supported) {
      this.btn.disabled = true;
      this.btn.title = 'Speech recognition not supported in this browser';
    }
  }

  _setRecording(active) {
    this.btn.classList.toggle('mic-active', active);
    this.btn.setAttribute('aria-pressed', String(active));
    this.btn.title = active ? 'Stop listening' : 'Voice input';
  }

  _updateInterim(text) {
    if (!this._interimEl) {
      this._interimEl = document.createElement('div');
      this._interimEl.className = 'interim-transcript';
      const inputBar = document.getElementById('input-bar');
      if (inputBar) inputBar.prepend(this._interimEl);
    }
    this._interimEl.textContent = text ? `🎤 ${text}` : '';
  }

  _clearInterim() {
    if (this._interimEl) {
      this._interimEl.remove();
      this._interimEl = null;
    }
  }

  stop() { this._stt.stopListening(); }
}

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
// UniversalVoiceRecognizer — Full-Duplex Robust Speech Recognition
// Dual-Engine: Uses Web Speech API for low-latency live streaming when available,
// with automatic transparent fallback to HTML5 MediaRecorder + Server faster-whisper STT.
// Guaranteed to work across all browsers (Chrome, Edge, Brave, Firefox, Safari, Mobile).
// ---------------------------------------------------------------------------
class UniversalVoiceRecognizer {
  constructor({ onResult, onStart, onEnd, onError, onAudioLevel } = {}) {
    this.onResult = onResult || null;
    this.onStart = onStart || null;
    this.onEnd = onEnd || null;
    this.onError = onError || null;
    this.onAudioLevel = onAudioLevel || null;

    this.isListening = false;
    this.activeStream = null;
    this._mediaRecorder = null;
    this._recordedChunks = [];
    this._recognition = null;
    this._useServerFallback = false;
    this._analyser = null;
    this._audioCtx = null;
    this._animId = null;

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.hasWebSpeech = !!SR;
  }

  get supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  async acquireMicrophone() {
    if (this.activeStream && this.activeStream.active) {
      return this.activeStream;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error('Microphone access is not supported in this browser.');
    }
    this.activeStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }
    });
    return this.activeStream;
  }

  async startListening() {
    if (this.isListening) return true;
    try {
      await this.acquireMicrophone();
    } catch (micErr) {
      console.warn('[UniversalSTT] Microphone access error:', micErr);
      this.onError?.('Microphone access denied. Please allow microphone in browser.');
      return false;
    }

    this.isListening = true;
    this.onStart?.();
    this._startLevelMonitor();

    // Start background MediaRecorder capture
    this._startMediaRecorder();

    // If Web Speech API is supported and not flagged as broken, try it in parallel
    if (this.hasWebSpeech && !this._useServerFallback) {
      this._startWebSpeech();
    }

    return true;
  }

  startUtterance() {
    // Reset buffer at start of actual detected speech
    this._recordedChunks = [];
    if (!this._mediaRecorder || this._mediaRecorder.state === 'inactive') {
      this._startMediaRecorder();
    }
  }

  _startWebSpeech() {
    try {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      const r = new SR();
      r.continuous = true;
      r.interimResults = true;
      r.lang = navigator.language || 'en-US';
      r.maxAlternatives = 1;

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

      r.onerror = (e) => {
        console.warn('[UniversalSTT] Web Speech API notice:', e.error);
        if (e.error === 'not-allowed' || e.error === 'service-not-allowed' || e.error === 'audio-capture' || e.error === 'network') {
          // Seamlessly switch to Server Whisper fallback without pausing
          this._useServerFallback = true;
          try { r.abort(); } catch (_) {}
          this._recognition = null;
        }
      };

      r.onend = () => {
        if (this.isListening && !this._useServerFallback) {
          try { r.start(); } catch (_) {}
        }
      };

      r.start();
      this._recognition = r;
      return true;
    } catch (err) {
      console.warn('[UniversalSTT] Web Speech start failed, falling back to MediaRecorder:', err);
      this._useServerFallback = true;
      return false;
    }
  }

  _startMediaRecorder() {
    if (!this.activeStream || !this.activeStream.active) return false;
    try {
      if (this._mediaRecorder && this._mediaRecorder.state === 'recording') {
        return true;
      }
      this._recordedChunks = [];
      let options = {};
      if (typeof MediaRecorder !== 'undefined') {
        if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) options = { mimeType: 'audio/webm;codecs=opus' };
        else if (MediaRecorder.isTypeSupported('audio/webm')) options = { mimeType: 'audio/webm' };
        else if (MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) options = { mimeType: 'audio/ogg;codecs=opus' };
        else if (MediaRecorder.isTypeSupported('audio/mp4')) options = { mimeType: 'audio/mp4' };
      }

      const rec = new MediaRecorder(this.activeStream, options);
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          this._recordedChunks.push(e.data);
        }
      };

      rec.start(250);
      this._mediaRecorder = rec;
      return true;
    } catch (recErr) {
      console.error('[UniversalSTT] MediaRecorder start error:', recErr);
      return false;
    }
  }

  async stopAndGetAudioBlob() {
    if (!this._mediaRecorder || this._mediaRecorder.state === 'inactive') {
      if (this._recordedChunks.length > 0) {
        const mime = this._mediaRecorder?.mimeType || 'audio/webm';
        const blob = new Blob(this._recordedChunks, { type: mime });
        this._recordedChunks = [];
        return blob;
      }
      return null;
    }

    return new Promise((resolve) => {
      const rec = this._mediaRecorder;
      rec.onstop = () => {
        const mime = rec.mimeType || 'audio/webm';
        const blob = new Blob(this._recordedChunks, { type: mime });
        this._recordedChunks = [];
        // Immediately restart for next utterance
        if (this.isListening) {
          this._startMediaRecorder();
        }
        resolve(blob);
      };
      try {
        rec.stop();
      } catch (_) {
        resolve(null);
      }
    });
  }

  async _transcribeOnServer(blob) {
    try {
      const fd = new FormData();
      fd.append('file', blob, 'recording.webm');
      const token = localStorage.getItem('jarvis_token') || 'jarvis123';
      const url = `${CONFIG.getServerUrl()}/api/voice/transcribe?token=${encodeURIComponent(token)}`;

      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: fd
      });

      if (res.ok) {
        const data = await res.json();
        const text = (data.text || '').trim();
        if (text) {
          this.onResult?.({ interim: '', final: text, isFinal: true });
        }
      }
    } catch (transcribeErr) {
      console.warn('[UniversalSTT] Server transcription error:', transcribeErr);
    }
  }

  async stopListening() {
    if (!this.isListening) return;
    this.isListening = false;
    this._stopLevelMonitor();

    if (this._recognition) {
      try {
        this._recognition.onend = null;
        this._recognition.stop();
      } catch (_) {}
      this._recognition = null;
    }

    if (this._mediaRecorder && this._mediaRecorder.state !== 'inactive') {
      await new Promise((resolve) => {
        const rec = this._mediaRecorder;
        rec.onstop = async () => {
          try {
            const mime = rec.mimeType || 'audio/webm';
            const blob = new Blob(this._recordedChunks, { type: mime });
            this._recordedChunks = [];
            if (blob.size > 2000) {
              await this._transcribeOnServer(blob);
            }
          } catch (e) {
            console.warn('[UniversalSTT] onstop transcription error:', e);
          }
          resolve();
        };
        try {
          rec.stop();
        } catch (_) {
          resolve();
        }
      });
    }

    this.onEnd?.();
  }

  abort() {
    this.isListening = false;
    this._recordedChunks = [];
    this._stopLevelMonitor();
    if (this._recognition) {
      try {
        this._recognition.onend = null;
        this._recognition.abort();
      } catch (_) {}
      this._recognition = null;
    }
    if (this._mediaRecorder && this._mediaRecorder.state !== 'inactive') {
      try {
        this._mediaRecorder.onstop = null;
        this._mediaRecorder.stop();
      } catch (_) {}
    }
  }

  _startLevelMonitor() {
    if (!this.activeStream || this._animId) return;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this._audioCtx = new AudioCtx();
      const src = this._audioCtx.createMediaStreamSource(this.activeStream);
      this._analyser = this._audioCtx.createAnalyser();
      this._analyser.fftSize = 64;
      src.connect(this._analyser);

      const data = new Uint8Array(this._analyser.frequencyBinCount);
      const loop = () => {
        if (!this.isListening) {
          this._animId = null;
          return;
        }
        this._analyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        const avg = sum / (data.length * 255.0);
        this.onAudioLevel?.(avg);
        this._animId = requestAnimationFrame(loop);
      };
      this._animId = requestAnimationFrame(loop);
    } catch (_) {}
  }

  _stopLevelMonitor() {
    if (this._animId) {
      cancelAnimationFrame(this._animId);
      this._animId = null;
    }
    if (this._audioCtx && this._audioCtx.state !== 'closed') {
      try { this._audioCtx.close(); } catch (_) {}
      this._audioCtx = null;
    }
    this.onAudioLevel?.(0);
  }
}

// Backwards-compatibility alias
const SpeechRecognitionManager = UniversalVoiceRecognizer;

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

    this._stt = new UniversalVoiceRecognizer({
      onResult: ({ interim, final, isFinal }) => {
        const text = (final || interim || '').trim();
        this._updateInterim(text);
        if (isFinal) {
          this._pendingFinal = text;
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
          showToast(`Microphone: ${err}`, 'error');
        }
      },
    });

    this._bind();
  }

  _bind() {
    this.btn.addEventListener('click', async () => {
      if (this._stt.isListening) {
        await this._stt.stopListening();
      } else {
        this.audioPlayer?.stop();
        this._pendingFinal = '';
        await this._stt.startListening();
        this._stt.startUtterance();
      }
    });
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

  stop() { this._stt.abort(); }
}

/**
 * JARVIS — voice.js
 * SpeechRecognition, AudioPlayer (with playback callbacks for Live Mode), and MicButton controller.
 */

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
   * Should be called inside a user click gesture (e.g. "Enter Live Mode").
   */
  unlock() {
    this._getCtx();
  }

  _getCtx() {
    if (!this._ctx) {
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (this._ctx.state === 'suspended') {
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
    return new Promise((resolve) => {
      try {
        const ctx = this._getCtx();
        const binary = atob(base64data);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

        ctx.decodeAudioData(
          bytes.buffer,
          (buffer) => {
            const source = ctx.createBufferSource();
            source.buffer = buffer;
            source.connect(ctx.destination);
            this._source = source;
            source.onended = () => {
              this._source = null;
              resolve();
            };
            source.start(0);
          },
          (err) => {
            console.warn('[AudioPlayer] decodeAudioData error:', err);
            resolve();
          }
        );
      } catch (err) {
        console.warn('[AudioPlayer] play error:', err);
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

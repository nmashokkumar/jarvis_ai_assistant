/**
 * JARVIS — silero_vad.js
 * Hybrid Voice Activity Detection (Dual Energy + Silero Neural VAD) via Web Audio.
 * Direct 16kHz Float32 PCM sample buffering + zero-dependency instant WAV encoding.
 * Ensures pristine audio quality with 400ms pre-speech buffer and 100% valid headers every turn.
 */

function float32ToWav(samples, sampleRate = 16000) {
  const dataLength = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  function writeString(offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true); // SubChunk1Size (16 for PCM)
  view.setUint16(20, 1, true);  // AudioFormat (1 = PCM)
  view.setUint16(22, 1, true);  // NumChannels (1 = Mono)
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // ByteRate
  view.setUint16(32, 2, true);  // BlockAlign
  view.setUint16(34, 16, true); // BitsPerSample
  writeString(36, 'data');
  view.setUint32(40, dataLength, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

class SileroVADManager {
  constructor({
    onSpeechStart,
    onSpeechEnd,
    onVADMisfire,
    onAudioLevel,
    positiveSpeechThreshold = 0.45,
    negativeSpeechThreshold = 0.25,
    minSpeechFrames = 4,   // ~130ms minimum speech to trigger turn start
    silenceFrames = 22     // ~700-800ms of silence to trigger turn completion
  } = {}) {
    this.onSpeechStart = onSpeechStart;
    this.onSpeechEnd = onSpeechEnd;
    this.onVADMisfire = onVADMisfire;
    this.onAudioLevel = onAudioLevel || null;

    this.positiveSpeechThreshold = positiveSpeechThreshold;
    this.negativeSpeechThreshold = negativeSpeechThreshold;
    this.minSpeechFrames = minSpeechFrames;
    this.silenceFrames = silenceFrames;

    this.isLoaded = false;
    this.isListening = false;
    this._session = null;
    this._audioCtx = null;
    this._mediaStream = null;
    this._processor = null;
    this._source = null;
    this._muteNode = null;
    this._ownsStream = false;

    // Ambient noise tracking for dynamic calibration
    this._ambientRms = 0.008;

    // Frame tracking
    this._isSpeaking = false;
    this._speechFrameCount = 0;
    this._silenceFrameCount = 0;

    // Pre-speech rolling buffer (~400ms = 6400 samples at 16kHz)
    this._preSpeechChunks = [];
    this._preSpeechSamples = 0;
    this._maxPreSpeechSamples = 6400;

    // Active speech buffer
    this._speechChunks = [];

    // Silero VAD state tensors
    this._h = null;
    this._c = null;
    this._srTensor = null;

    // Eagerly initiate model load in background
    this.loadModel().catch(() => {});
  }

  unlock() {
    try {
      if (!this._audioCtx || this._audioCtx.state === 'closed') {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        this._audioCtx = new AudioCtx();
      }
      if (this._audioCtx && this._audioCtx.state === 'suspended') {
        this._audioCtx.resume().catch(() => {});
      }
    } catch (_) {}
  }

  /**
   * Initializes ONNX Runtime Web and loads the local Silero VAD model in background.
   */
  async loadModel() {
    if (this.isLoaded) return true;
    try {
      if (typeof ort === 'undefined') {
        console.warn('[SileroVAD] onnxruntime-web not found on page. Running in high-precision energy VAD mode.');
        return false;
      }

      ort.env.wasm.wasmPaths = 'vendor/';
      ort.env.wasm.numThreads = 1;

      const modelUrl = 'models/silero_vad.onnx';
      this._session = await ort.InferenceSession.create(modelUrl, {
        executionProviders: ['wasm'],
        graphOptimizationLevel: 'all'
      });

      this._resetModelStates();
      this.isLoaded = true;
      console.log('[SileroVAD] Neural VAD model loaded successfully.');
      return true;
    } catch (err) {
      console.warn('[SileroVAD] Neural VAD load warning, using energy VAD:', err);
      return false;
    }
  }

  _resetModelStates() {
    if (typeof ort !== 'undefined') {
      try {
        const stateData = new Float32Array(2 * 1 * 64);
        this._h = new ort.Tensor('float32', stateData, [2, 1, 64]);
        this._c = new ort.Tensor('float32', new Float32Array(2 * 1 * 64), [2, 1, 64]);
        this._srTensor = new ort.Tensor('int64', BigInt64Array.from([16000n]), [1]);
      } catch (_) {}
    }
    this.resetTurn();
  }

  resetTurn() {
    this._isSpeaking = false;
    this._speechFrameCount = 0;
    this._silenceFrameCount = 0;
    this._speechChunks = [];
    this._preSpeechChunks = [];
    this._preSpeechSamples = 0;
  }

  /**
   * Starts real-time microphone capture via Web Audio resampled to 16kHz.
   * Can reuse an existing MediaStream to avoid duplicate hardware device locks.
   */
  async start(existingStream = null) {
    if (this.isListening) return true;

    try {
      this.unlock();

      if (existingStream && existingStream.active) {
        this._mediaStream = existingStream;
        this._ownsStream = false;
      } else {
        this._mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
          }
        });
        this._ownsStream = true;
      }

      if (!this._audioCtx || this._audioCtx.state === 'closed') {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        this._audioCtx = new AudioCtx();
      }
      if (this._audioCtx.state === 'suspended') {
        await this._audioCtx.resume().catch(() => {});
      }

      const source = this._audioCtx.createMediaStreamSource(this._mediaStream);
      const sampleRate = this._audioCtx.sampleRate;
      const bufferSize = 2048;
      const processor = this._audioCtx.createScriptProcessor(bufferSize, 1, 1);

      let accumulated = [];

      processor.onaudioprocess = (e) => {
        if (!this.isListening) return;
        const inputData = e.inputBuffer.getChannelData(0);

        // 1. Measure RMS energy
        let sumSquares = 0;
        for (let i = 0; i < inputData.length; i++) {
          sumSquares += inputData[i] * inputData[i];
        }
        const rms = Math.sqrt(sumSquares / inputData.length);
        this.onAudioLevel?.(rms);

        // 2. Dynamic noise floor tracking
        if (!this._isSpeaking) {
          this._ambientRms = this._ambientRms * 0.95 + rms * 0.05;
        }
        const energyThreshold = Math.max(0.015, this._ambientRms * 2.6);

        // 3. Resample to 16kHz
        const resampled = this._resampleTo16k(inputData, sampleRate);

        // 4. Buffer audio samples for crystal-clear non-clipped WAV generation
        if (!this._isSpeaking) {
          this._preSpeechChunks.push(resampled);
          this._preSpeechSamples += resampled.length;
          while (this._preSpeechSamples > this._maxPreSpeechSamples && this._preSpeechChunks.length > 1) {
            const removed = this._preSpeechChunks.shift();
            this._preSpeechSamples -= removed.length;
          }
        } else {
          this._speechChunks.push(resampled);
        }

        // 5. Silero 512-frame chunking
        for (let i = 0; i < resampled.length; i++) {
          accumulated.push(resampled[i]);
          if (accumulated.length === 512) {
            const frame = new Float32Array(accumulated);
            accumulated = [];
            this.processFrame(frame, rms, energyThreshold);
          }
        }
      };

      source.connect(processor);
      const muteNode = this._audioCtx.createGain();
      muteNode.gain.setValueAtTime(0, this._audioCtx.currentTime);
      processor.connect(muteNode);
      muteNode.connect(this._audioCtx.destination);

      this._processor = processor;
      this._source = source;
      this._muteNode = muteNode;
      this.isListening = true;
      this._resetModelStates();
      console.log('[SileroVAD] Audio processor listening (state:', this._audioCtx.state, ')');
      return true;
    } catch (err) {
      console.error('[SileroVAD] Could not start VAD audio processor:', err);
      return false;
    }
  }

  async processFrame(audioFrame512, rms, energyThreshold) {
    let prob = 0;
    if (this._session && this.isLoaded) {
      try {
        const inputTensor = new ort.Tensor('float32', audioFrame512, [1, audioFrame512.length]);
        let feeds = {};
        const inputNames = this._session.inputNames;
        if (inputNames.includes('input')) feeds['input'] = inputTensor;
        else if (inputNames.includes('x')) feeds['x'] = inputTensor;

        if (inputNames.includes('sr')) feeds['sr'] = this._srTensor;
        if (inputNames.includes('h')) feeds['h'] = this._h;
        if (inputNames.includes('c')) feeds['c'] = this._c;
        if (inputNames.includes('state')) feeds['state'] = this._h;

        const results = await this._session.run(feeds);
        if (results.output) prob = results.output.data[0];
        else if (results.prob) prob = results.prob.data[0];
        else if (results[this._session.outputNames[0]]) {
          prob = results[this._session.outputNames[0]].data[0];
        }

        if (results.hn) this._h = results.hn;
        if (results.cn) this._c = results.cn;
        if (results.stateN) this._h = results.stateN;
      } catch (e) {
        prob = 0;
      }
    }

    // Hybrid decision: Neural confidence OR Energy trigger
    const isSpeech = (this._session && this.isLoaded)
      ? (prob >= this.positiveSpeechThreshold || (prob >= 0.30 && rms >= energyThreshold))
      : (rms >= energyThreshold);

    this._handleSpeechDecision(isSpeech, prob, rms);
  }

  _handleSpeechDecision(isSpeech, prob, rms) {
    if (isSpeech) {
      this._silenceFrameCount = 0;
      this._speechFrameCount++;

      if (!this._isSpeaking && this._speechFrameCount >= this.minSpeechFrames) {
        this._isSpeaking = true;
        console.log(`[SileroVAD] Speech Detected (rms: ${rms.toFixed(4)}, prob: ${prob.toFixed(2)})`);
        // Transfer pre-speech buffer into active speech chunks
        this._speechChunks = [...this._preSpeechChunks];
        this._preSpeechChunks = [];
        this._preSpeechSamples = 0;
        this.onSpeechStart?.();
      }
    } else {
      this._speechFrameCount = 0;
      if (this._isSpeaking) {
        this._silenceFrameCount++;
        if (this._silenceFrameCount >= this.silenceFrames) {
          this._isSpeaking = false;
          this._silenceFrameCount = 0;
          console.log(`[SileroVAD] Silence Confirmed (~${Math.round(this.silenceFrames * 32)}ms) -> Finalizing turn`);

          // Encode all accumulated Float32 PCM samples into a 16kHz mono WAV Blob
          let wavBlob = null;
          let totalLen = 0;
          for (const c of this._speechChunks) totalLen += c.length;
          if (totalLen >= 3200) { // >= 200ms
            const merged = new Float32Array(totalLen);
            let offset = 0;
            for (const c of this._speechChunks) {
              merged.set(c, offset);
              offset += c.length;
            }
            wavBlob = float32ToWav(merged, 16000);
          }

          this._speechChunks = [];
          this._preSpeechChunks = [];
          this._preSpeechSamples = 0;
          this.onSpeechEnd?.(wavBlob);
        }
      }
    }
  }

  _resampleTo16k(audioBuffer, fromSampleRate) {
    if (fromSampleRate === 16000) return audioBuffer;
    const ratio = fromSampleRate / 16000;
    const newLength = Math.round(audioBuffer.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
      const origIndex = i * ratio;
      const lower = Math.floor(origIndex);
      const upper = Math.min(lower + 1, audioBuffer.length - 1);
      const frac = origIndex - lower;
      result[i] = audioBuffer[lower] * (1 - frac) + audioBuffer[upper] * frac;
    }
    return result;
  }

  stop() {
    this.isListening = false;
    this.resetTurn();

    if (this._processor) {
      try { this._processor.disconnect(); } catch (_) {}
      this._processor = null;
    }
    if (this._source) {
      try { this._source.disconnect(); } catch (_) {}
      this._source = null;
    }
    if (this._muteNode) {
      try { this._muteNode.disconnect(); } catch (_) {}
      this._muteNode = null;
    }
    if (this._ownsStream && this._mediaStream) {
      try {
        this._mediaStream.getTracks().forEach(t => t.stop());
      } catch (_) {}
    }
    this._mediaStream = null;
    if (this._audioCtx && this._audioCtx.state !== 'closed') {
      try { this._audioCtx.close(); } catch (_) {}
      this._audioCtx = null;
    }
  }
}

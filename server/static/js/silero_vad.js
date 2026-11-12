/**
 * JARVIS — silero_vad.js
 * Client-Side Neural Voice Activity Detection (Silero VAD) via Web Audio + ONNX Runtime Web.
 * Enables instant barge-in / interruption (cuts off TTS playback the instant Boss speaks)
 * and robust silence endpointing without relying on flaky browser-native timeouts.
 */

class SileroVADManager {
  constructor({
    onSpeechStart,
    onSpeechEnd,
    onVADMisfire,
    positiveSpeechThreshold = 0.5,
    negativeSpeechThreshold = 0.35,
    redemptionFrames = 8, // ~256ms of silence before declaring speech end
    preSpeechPadFrames = 1,
    minSpeechFrames = 3   // ~96ms minimum voice duration to trigger
  } = {}) {
    this.onSpeechStart = onSpeechStart;
    this.onSpeechEnd = onSpeechEnd;
    this.onVADMisfire = onVADMisfire;

    this.positiveSpeechThreshold = positiveSpeechThreshold;
    this.negativeSpeechThreshold = negativeSpeechThreshold;
    this.redemptionFrames = redemptionFrames;
    this.preSpeechPadFrames = preSpeechPadFrames;
    this.minSpeechFrames = minSpeechFrames;

    this.isLoaded = false;
    this.isListening = false;
    this._session = null;
    this._audioCtx = null;
    this._mediaStream = null;
    this._workletNode = null;

    // Silero VAD state tensors (h and c states for GRU/RNN)
    this._h = null;
    this._c = null;
    this._srTensor = null;

    // Frame tracking
    this._isSpeaking = false;
    this._speechFrameCount = 0;
    this._silenceFrameCount = 0;
  }

  /**
   * Initializes ONNX Runtime Web and loads the local Silero VAD model.
   */
  async loadModel() {
    if (this.isLoaded) return true;
    try {
      if (typeof ort === 'undefined') {
        console.warn('[SileroVAD] onnxruntime-web not found on page.');
        return false;
      }

      // Configure WASM paths
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
      console.warn('[SileroVAD] Failed to load local VAD model:', err);
      return false;
    }
  }

  _resetModelStates() {
    // Silero VAD v5 / standard RNN state shapes: [2, 1, 64]
    const stateData = new Float32Array(2 * 1 * 64);
    this._h = new ort.Tensor('float32', stateData, [2, 1, 64]);
    this._c = new ort.Tensor('float32', new Float32Array(2 * 1 * 64), [2, 1, 64]);
    this._srTensor = new ort.Tensor('int64', BigInt64Array.from([16000n]), [1]);
    this._isSpeaking = false;
    this._speechFrameCount = 0;
    this._silenceFrameCount = 0;
  }

  /**
   * Evaluates a single 512-sample float32 audio chunk (sampled at 16kHz).
   * Returns speech probability between 0.0 and 1.0.
   */
  async processFrame(audioFrame512) {
    if (!this._session) return 0;
    try {
      const inputTensor = new ort.Tensor('float32', audioFrame512, [1, audioFrame512.length]);
      
      let feeds = {};
      const inputNames = this._session.inputNames;
      if (inputNames.includes('input')) {
        feeds['input'] = inputTensor;
      } else if (inputNames.includes('x')) {
        feeds['x'] = inputTensor;
      }

      if (inputNames.includes('sr')) feeds['sr'] = this._srTensor;
      if (inputNames.includes('h')) feeds['h'] = this._h;
      if (inputNames.includes('c')) feeds['c'] = this._c;
      if (inputNames.includes('state')) feeds['state'] = this._h;

      const results = await this._session.run(feeds);

      // Extract output probability
      let prob = 0;
      if (results.output) prob = results.output.data[0];
      else if (results.prob) prob = results.prob.data[0];
      else if (results[this._session.outputNames[0]]) {
        prob = results[this._session.outputNames[0]].data[0];
      }

      // Update RNN recurrent state
      if (results.hn) this._h = results.hn;
      if (results.cn) this._c = results.cn;
      if (results.stateN) this._h = results.stateN;

      this._handleProbability(prob);
      return prob;
    } catch (e) {
      console.warn('[SileroVAD] Inference step warning:', e);
      return 0;
    }
  }

  _handleProbability(prob) {
    if (prob >= this.positiveSpeechThreshold) {
      this._silenceFrameCount = 0;
      this._speechFrameCount++;

      if (!this._isSpeaking && this._speechFrameCount >= this.minSpeechFrames) {
        this._isSpeaking = true;
        console.log(`[SileroVAD] Speech Detected (prob: ${prob.toFixed(2)}) -> Triggering Barge-in!`);
        this.onSpeechStart?.();
      }
    } else if (prob < this.negativeSpeechThreshold) {
      this._speechFrameCount = 0;
      if (this._isSpeaking) {
        this._silenceFrameCount++;
        if (this._silenceFrameCount >= this.redemptionFrames) {
          this._isSpeaking = false;
          this._silenceFrameCount = 0;
          console.log(`[SileroVAD] Silence Confirmed -> Finalizing turn.`);
          this.onSpeechEnd?.();
        }
      }
    }
  }

  /**
   * Starts real-time microphone capture via Web Audio ScriptProcessor/AudioWorklet resampled to 16kHz.
   */
  async start() {
    if (this.isListening) return true;
    const ready = await this.loadModel();
    if (!ready) return false;

    try {
      this._mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });

      this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = this._audioCtx.createMediaStreamSource(this._mediaStream);

      // Simple 16kHz resampler + 512 frame chunker
      const sampleRate = this._audioCtx.sampleRate;
      const bufferSize = 2048;
      const processor = this._audioCtx.createScriptProcessor(bufferSize, 1, 1);

      let accumulated = [];

      processor.onaudioprocess = (e) => {
        if (!this.isListening) return;
        const inputData = e.inputBuffer.getChannelData(0);

        // Linear resample from audioContext sampleRate down to 16000 Hz
        const resampled = this._resampleTo16k(inputData, sampleRate);
        for (let i = 0; i < resampled.length; i++) {
          accumulated.push(resampled[i]);
          if (accumulated.length === 512) {
            const frame = new Float32Array(accumulated);
            accumulated = [];
            this.processFrame(frame);
          }
        }
      };

      source.connect(processor);
      processor.connect(this._audioCtx.destination);
      this._processor = processor;
      this._source = source;
      this.isListening = true;
      this._resetModelStates();
      return true;
    } catch (err) {
      console.error('[SileroVAD] Could not open mic for VAD:', err);
      return false;
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
    if (this._processor) {
      try { this._processor.disconnect(); } catch (_) {}
      this._processor = null;
    }
    if (this._source) {
      try { this._source.disconnect(); } catch (_) {}
      this._source = null;
    }
    if (this._mediaStream) {
      try {
        this._mediaStream.getTracks().forEach(t => t.stop());
      } catch (_) {}
      this._mediaStream = null;
    }
    if (this._audioCtx && this._audioCtx.state !== 'closed') {
      try { this._audioCtx.close(); } catch (_) {}
      this._audioCtx = null;
    }
  }
}

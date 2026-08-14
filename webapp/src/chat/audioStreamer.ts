/**
 * audioStreamer.ts — Web Audio PCM capture (16kHz) and playback queue (24kHz).
 *
 * Implements low-latency raw linear PCM audio streaming for Gemini Live API:
 * - Input: Captures mic audio, resamples to 16,000 Hz, 16-bit linear PCM mono, base64-encoded.
 * - Output: Decodes 24,000 Hz 16-bit linear PCM mono base64 chunks and schedules jitter-free playback.
 * - Interruption: Instantly stops all playing audio and resets scheduling buffers.
 */

// Resample float32 audio buffer from source sampleRate to target sampleRate (16000)
function downsampleAudio(
  buffer: Float32Array,
  sourceRate: number,
  targetRate: number = 16000
): Float32Array {
  if (sourceRate === targetRate) return buffer;
  const ratio = sourceRate / targetRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;

  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

// Convert Float32Array [-1.0, 1.0] to Int16 ArrayBuffer (PCM 16-bit little-endian)
function floatTo16BitPCM(input: Float32Array): ArrayBuffer {
  const output = new DataView(new ArrayBuffer(input.length * 2));
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    output.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return output.buffer;
}

// Convert ArrayBuffer to Base64 string
function arrayBufferToBase64(buffer: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary);
}

// Decode Base64 to ArrayBuffer
function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binaryString = window.atob(base64);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * AudioRecorder — Captures mic input and emits 16kHz 16-bit PCM chunks.
 */
export class AudioRecorder {
  private mediaStream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private processorNode: ScriptProcessorNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private onChunkCallback: ((base64Pcm: string) => void) | null = null;
  private isRecording = false;

  async start(onChunk: (base64Pcm: string) => void): Promise<void> {
    if (this.isRecording) return;
    this.onChunkCallback = onChunk;

    try {
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      const AudioCtxClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioContext = new AudioCtxClass();
      if (this.audioContext.state === "suspended") {
        await this.audioContext.resume();
      }

      this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
      // script processor with bufferSize = 4096 (approx 85-90ms of audio at 48kHz)
      this.processorNode = this.audioContext.createScriptProcessor(4096, 1, 1);

      this.processorNode.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!this.isRecording || !this.onChunkCallback) return;
        const inputData = e.inputBuffer.getChannelData(0);
        const currentSampleRate = this.audioContext?.sampleRate || 44100;
        const downsampled = downsampleAudio(inputData, currentSampleRate, 16000);
        const pcmBuffer = floatTo16BitPCM(downsampled);
        const base64 = arrayBufferToBase64(pcmBuffer);
        this.onChunkCallback(base64);
      };

      this.sourceNode.connect(this.processorNode);
      this.processorNode.connect(this.audioContext.destination);
      this.isRecording = true;
    } catch (err) {
      this.stop();
      throw err;
    }
  }

  stop(): void {
    this.isRecording = false;
    this.onChunkCallback = null;

    if (this.processorNode) {
      try {
        this.processorNode.disconnect();
      } catch (_) {}
      this.processorNode = null;
    }
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch (_) {}
      this.sourceNode = null;
    }
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop());
      this.mediaStream = null;
    }
    if (this.audioContext) {
      try {
        this.audioContext.close();
      } catch (_) {}
      this.audioContext = null;
    }
  }

  get active(): boolean {
    return this.isRecording;
  }
}

/**
 * AudioPlayer — Receives 24kHz 16-bit PCM chunks and plays them in sequence.
 */
export class AudioPlayer {
  private audioContext: AudioContext | null = null;
  private nextPlayTime = 0;
  private activeSources: AudioBufferSourceNode[] = [];
  private isPlaying = false;
  private onPlaybackStateChange?: (playing: boolean) => void;

  constructor(onPlaybackStateChange?: (playing: boolean) => void) {
    this.onPlaybackStateChange = onPlaybackStateChange;
  }

  private getAudioContext(): AudioContext {
    if (!this.audioContext || this.audioContext.state === "closed") {
      const AudioCtxClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioContext = new AudioCtxClass({ sampleRate: 24000 });
    }
    if (this.audioContext.state === "suspended") {
      this.audioContext.resume();
    }
    return this.audioContext;
  }

  playChunk(base64Pcm: string): void {
    try {
      const ctx = this.getAudioContext();
      const arrayBuffer = base64ToArrayBuffer(base64Pcm);
      const dataView = new DataView(arrayBuffer);
      const numSamples = arrayBuffer.byteLength / 2;
      const float32 = new Float32Array(numSamples);

      for (let i = 0; i < numSamples; i++) {
        const int16 = dataView.getInt16(i * 2, true);
        float32[i] = int16 / 32768.0;
      }

      const audioBuffer = ctx.createBuffer(1, numSamples, 24000);
      audioBuffer.copyToChannel(float32, 0);

      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);

      const now = ctx.currentTime;
      const startTime = Math.max(now, this.nextPlayTime);
      source.start(startTime);
      this.nextPlayTime = startTime + audioBuffer.duration;

      this.activeSources.push(source);
      if (!this.isPlaying) {
        this.isPlaying = true;
        this.onPlaybackStateChange?.(true);
      }

      source.onended = () => {
        const idx = this.activeSources.indexOf(source);
        if (idx >= 0) {
          this.activeSources.splice(idx, 1);
        }
        if (this.activeSources.length === 0) {
          this.isPlaying = false;
          this.onPlaybackStateChange?.(false);
        }
      };
    } catch (err) {
      console.warn("AudioPlayer playChunk error:", err);
    }
  }

  /**
   * Interrupts playback immediately (e.g. user interruption or stop signal).
   */
  interrupt(): void {
    for (const source of this.activeSources) {
      try {
        source.stop();
        source.disconnect();
      } catch (_) {}
    }
    this.activeSources = [];
    this.nextPlayTime = 0;
    if (this.isPlaying) {
      this.isPlaying = false;
      this.onPlaybackStateChange?.(false);
    }
  }

  close(): void {
    this.interrupt();
    if (this.audioContext) {
      try {
        this.audioContext.close();
      } catch (_) {}
      this.audioContext = null;
    }
  }
}

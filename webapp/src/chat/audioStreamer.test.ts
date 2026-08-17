import { describe, it, expect } from "vitest";
import {
  downsampleAudio,
  floatTo16BitPCM,
  arrayBufferToBase64,
  base64ToArrayBuffer,
} from "./audioStreamer";

describe("audioStreamer utilities", () => {
  it("downsamples 48kHz audio to 16kHz (3:1 ratio)", () => {
    const input = new Float32Array([0.3, 0.6, 0.9, -0.3, -0.6, -0.9]);
    const downsampled = downsampleAudio(input, 48000, 16000);
    expect(downsampled.length).toBe(2);
    expect(downsampled[0]).toBeCloseTo(0.6, 2);
    expect(downsampled[1]).toBeCloseTo(-0.6, 2);
  });

  it("returns original buffer when source sample rate matches target", () => {
    const input = new Float32Array([0.1, 0.2, 0.3]);
    const downsampled = downsampleAudio(input, 16000, 16000);
    expect(downsampled).toBe(input);
  });

  it("converts Float32Array to 16-bit PCM ArrayBuffer and clamps [-1, 1]", () => {
    const input = new Float32Array([0.0, 1.0, -1.0, 2.0, -2.0]);
    const buffer = floatTo16BitPCM(input);
    expect(buffer.byteLength).toBe(10); // 5 samples * 2 bytes

    const view = new DataView(buffer);
    expect(view.getInt16(0, true)).toBe(0);
    expect(view.getInt16(2, true)).toBe(32767);
    expect(view.getInt16(4, true)).toBe(-32768);
    expect(view.getInt16(6, true)).toBe(32767); // clamped
    expect(view.getInt16(8, true)).toBe(-32768); // clamped
  });

  it("encodes and decodes base64 ArrayBuffers roundtrip", () => {
    const original = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]).buffer;
    const b64 = arrayBufferToBase64(original);
    expect(typeof b64).toBe("string");
    expect(b64.length).toBeGreaterThan(0);

    const decoded = base64ToArrayBuffer(b64);
    const decodedBytes = new Uint8Array(decoded);
    const originalBytes = new Uint8Array(original);

    expect(decodedBytes.length).toBe(originalBytes.length);
    for (let i = 0; i < decodedBytes.length; i++) {
      expect(decodedBytes[i]).toBe(originalBytes[i]);
    }
  });
});

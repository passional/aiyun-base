import { describe, expect, it } from "vitest";
import { downmixToMono, encodePcm16Wav, resampleLinear } from "./audio";

describe("downmixToMono", () => {
  it("averages every channel sample by sample", () => {
    const mono = downmixToMono([
      new Float32Array([1, 0, -1]),
      new Float32Array([-1, 0.5, 1]),
    ]);
    expect(Array.from(mono)).toEqual([0, 0.25, 0]);
  });

  it("returns a copy when there is a single channel", () => {
    const source = new Float32Array([0.25, -0.5]);
    const mono = downmixToMono([source]);
    expect(Array.from(mono)).toEqual([0.25, -0.5]);
    expect(mono).not.toBe(source);
  });

  it("returns an empty buffer when no channel is supplied", () => {
    expect(downmixToMono([]).length).toBe(0);
  });
});

describe("resampleLinear", () => {
  it("upsamples 8kHz to 16kHz and interpolates between samples", () => {
    const output = resampleLinear(new Float32Array([0, 1]), 8000, 16000);
    expect(output.length).toBe(4);
    expect(Array.from(output)).toEqual([0, 0.5, 1, 1]);
  });

  it("downsamples 48kHz to 16kHz to one third of the input length", () => {
    const output = resampleLinear(new Float32Array(4800), 48000, 16000);
    expect(output.length).toBe(1600);
  });

  it("returns an unchanged copy when the rates match", () => {
    const source = new Float32Array([0.5, -0.25]);
    const output = resampleLinear(source, 16000, 16000);
    expect(Array.from(output)).toEqual([0.5, -0.25]);
    expect(output).not.toBe(source);
  });

  it("keeps an empty input empty", () => {
    expect(resampleLinear(new Float32Array(0), 44100, 16000).length).toBe(0);
  });
});

describe("encodePcm16Wav", () => {
  function ascii(buffer: ArrayBuffer, offset: number, length: number) {
    return String.fromCharCode(...new Uint8Array(buffer, offset, length));
  }

  it("writes a 44-byte mono PCM16 header", () => {
    const buffer = encodePcm16Wav(new Float32Array([0, 0, 0]));
    const view = new DataView(buffer);

    expect(buffer.byteLength).toBe(44 + 6);
    expect(ascii(buffer, 0, 4)).toBe("RIFF");
    expect(ascii(buffer, 8, 4)).toBe("WAVE");
    expect(ascii(buffer, 12, 4)).toBe("fmt ");
    expect(ascii(buffer, 36, 4)).toBe("data");
    expect(view.getUint32(4, true)).toBe(36 + 6);
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint32(28, true)).toBe(32000);
    expect(view.getUint16(32, true)).toBe(2);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(6);
  });

  it("encodes little-endian samples and clamps outside [-1, 1]", () => {
    const view = new DataView(encodePcm16Wav(new Float32Array([0, 1, -1, 0.5, 2])));
    expect(view.getInt16(44, true)).toBe(0);
    expect(view.getInt16(46, true)).toBe(32767);
    expect(view.getInt16(48, true)).toBe(-32768);
    expect(view.getInt16(50, true)).toBe(16384);
    expect(view.getInt16(52, true)).toBe(32767);
  });
});

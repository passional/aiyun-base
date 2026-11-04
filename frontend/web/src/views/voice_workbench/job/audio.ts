/**
 * 浏览器侧音频转码：WebAudio 解码 + 纯函数下混、重采样与 PCM16 WAV 编码。
 *
 * 上游只接受 16kHz 单声道 PCM16 WAV（ADR-0011），转码因此发生在上传之前。
 * 解码以外的步骤都是纯函数，便于用合成采样直接验证。
 */

/** 上游要求的采样率 */
export const TARGET_SAMPLE_RATE = 16000;

/** 浏览器无法解码音频时抛出的错误；界面据此给出「浏览器无法解码该音频」 */
export class AudioDecodeError extends Error {
  constructor(message = "浏览器无法解码该音频") {
    super(message);
    this.name = "AudioDecodeError";
  }
}

/** 多声道下混为单声道：逐采样取各声道均值 */
export function downmixToMono(channels: readonly Float32Array[]): Float32Array {
  if (channels.length === 0) return new Float32Array(0);
  if (channels.length === 1) return channels[0]!.slice();

  const length = Math.min(...channels.map((channel) => channel.length));
  const mono = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    let sum = 0;
    for (const channel of channels) sum += channel[i] ?? 0;
    mono[i] = sum / channels.length;
  }
  return mono;
}

/**
 * 线性插值重采样。
 *
 * 输出长度为 `round(输入长度 × 目标采样率 / 源采样率)`；越界位置钳到最后一个采样，
 * 避免短音频在末尾产生额外的静音。
 */
export function resampleLinear(
  samples: Float32Array,
  sourceRate: number,
  targetRate: number
): Float32Array {
  if (sourceRate <= 0 || targetRate <= 0) {
    throw new RangeError("采样率必须大于 0");
  }
  if (samples.length === 0) return new Float32Array(0);
  if (sourceRate === targetRate) return samples.slice();

  const length = Math.max(1, Math.round((samples.length * targetRate) / sourceRate));
  const step = sourceRate / targetRate;
  const result = new Float32Array(length);
  const lastIndex = samples.length - 1;

  for (let i = 0; i < length; i += 1) {
    const position = i * step;
    const index = Math.floor(position);
    const fraction = position - index;
    const current = samples[Math.min(index, lastIndex)] ?? 0;
    const next = samples[Math.min(index + 1, lastIndex)] ?? current;
    result[i] = current + (next - current) * fraction;
  }
  return result;
}

/** 采样值钳到 [-1, 1] 后转 16 位有符号整数（正负满量程不同，避免 +1 溢出成负数） */
function toPcm16(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample));
  return Math.round(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
}

/** 编码 44 字节头 + PCM16 数据的单声道 WAV */
export function encodePcm16Wav(samples: Float32Array, sampleRate = TARGET_SAMPLE_RATE): ArrayBuffer {
  const bytesPerSample = 2;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // 单声道
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (const sample of samples) {
    view.setInt16(offset, toPcm16(sample), true);
    offset += bytesPerSample;
  }
  return buffer;
}

function writeAscii(view: DataView, offset: number, text: string) {
  for (let i = 0; i < text.length; i += 1) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}

type AudioContextConstructor = typeof AudioContext;

function resolveAudioContextConstructor(): AudioContextConstructor | null {
  if (typeof window === "undefined") return null;
  const candidate = window as unknown as {
    AudioContext?: AudioContextConstructor;
    webkitAudioContext?: AudioContextConstructor;
  };
  return candidate.AudioContext ?? candidate.webkitAudioContext ?? null;
}

/**
 * 解码任意浏览器支持的音频并转成 16kHz 单声道 PCM16 WAV。
 *
 * 解码失败一律转成 `AudioDecodeError`：调用方据此标记该文件失败，且不能发起识别请求。
 */
export async function transcodeAudioFile(
  file: File,
  targetRate = TARGET_SAMPLE_RATE
): Promise<ArrayBuffer> {
  const AudioContextClass = resolveAudioContextConstructor();
  if (!AudioContextClass) throw new AudioDecodeError();

  const context = new AudioContextClass();
  try {
    const decoded = await context.decodeAudioData(await file.arrayBuffer());
    const channels = Array.from({ length: decoded.numberOfChannels }, (_, index) =>
      decoded.getChannelData(index)
    );
    const mono = downmixToMono(channels);
    const resampled = resampleLinear(mono, decoded.sampleRate, targetRate);
    return encodePcm16Wav(resampled, targetRate);
  } catch {
    throw new AudioDecodeError();
  } finally {
    await context.close().catch(() => undefined);
  }
}

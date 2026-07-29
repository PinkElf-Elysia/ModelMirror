export interface StreamingAudioResult {
  blobUrl: string;
  playbackUrl: string;
  byteLength: number;
  streamed: boolean;
}

interface StreamingMp3SessionOptions {
  onPlaybackUrl?: (url: string, streamed: boolean) => void;
  onPlaybackFallback?: (message: string) => void;
}

function decodeBase64(value: string): Uint8Array {
  const decoded = window.atob(value);
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes;
}

/**
 * Decodes base64 without assuming that upstream SSE chunks align to groups of
 * four. Padded chunks are also treated as independent boundaries so a provider
 * may send either one continuous base64 stream or individually encoded blocks.
 */
export class IncrementalBase64Decoder {
  private remainder = "";

  push(value: string): Uint8Array[] {
    const cleaned = value
      .replace(/^data:audio\/[A-Za-z0-9.+-]+;base64,/i, "")
      .replace(/\s+/g, "");
    if (!cleaned) return [];
    if (!/^[A-Za-z0-9+/=]+$/.test(cleaned)) {
      throw new Error("语音流包含无效的 Base64 数据。");
    }

    this.remainder += cleaned;
    const decoded: Uint8Array[] = [];
    while (this.remainder) {
      const paddingIndex = this.remainder.indexOf("=");
      if (paddingIndex >= 0) {
        let boundary = paddingIndex;
        while (
          boundary < this.remainder.length &&
          this.remainder[boundary] === "="
        ) {
          boundary += 1;
        }
        const segment = this.remainder.slice(0, boundary);
        if (segment.length % 4 !== 0) break;
        decoded.push(decodeBase64(segment));
        this.remainder = this.remainder.slice(boundary);
        continue;
      }

      const completeLength =
        this.remainder.length - (this.remainder.length % 4);
      if (completeLength === 0) break;
      const segment = this.remainder.slice(0, completeLength);
      decoded.push(decodeBase64(segment));
      this.remainder = this.remainder.slice(completeLength);
    }
    return decoded;
  }

  finish(): Uint8Array[] {
    if (!this.remainder) return [];
    if (this.remainder.length % 4 === 1) {
      this.remainder = "";
      throw new Error("语音流结尾不完整。");
    }
    const padded = this.remainder.padEnd(
      Math.ceil(this.remainder.length / 4) * 4,
      "=",
    );
    this.remainder = "";
    return [decodeBase64(padded)];
  }
}

export class StreamingMp3Session {
  private readonly decoder = new IncrementalBase64Decoder();
  private readonly chunks: Uint8Array[] = [];
  private readonly appendQueue: Uint8Array[] = [];
  private readonly options: StreamingMp3SessionOptions;
  private mediaSource: MediaSource | null = null;
  private sourceBuffer: SourceBuffer | null = null;
  private mediaUrl = "";
  private blobUrl = "";
  private finished = false;
  private mediaFailed = false;
  private disposed = false;

  constructor(options: StreamingMp3SessionOptions = {}) {
    this.options = options;
  }

  pushBase64(value: string) {
    if (this.disposed || this.finished) return;
    const decoded = this.decoder.push(value);
    for (const bytes of decoded) {
      this.append(bytes);
    }
  }

  finish(): StreamingAudioResult {
    if (this.disposed) {
      throw new Error("语音流已经释放。");
    }
    if (!this.finished) {
      for (const bytes of this.decoder.finish()) {
        this.append(bytes);
      }
      this.finished = true;
    }
    const byteLength = this.chunks.reduce(
      (total, chunk) => total + chunk.byteLength,
      0,
    );
    if (byteLength === 0) {
      throw new Error("模型没有返回可播放的语音。");
    }

    if (!this.blobUrl) {
      this.blobUrl = URL.createObjectURL(
        new Blob(this.chunks, { type: "audio/mpeg" }),
      );
    }
    if (!this.mediaSource || this.mediaFailed) {
      this.options.onPlaybackUrl?.(this.blobUrl, false);
    } else {
      this.drainQueue();
      this.endMediaStreamWhenReady();
    }

    return {
      blobUrl: this.blobUrl,
      playbackUrl:
        this.mediaSource && !this.mediaFailed
          ? this.mediaUrl
          : this.blobUrl,
      byteLength,
      streamed: Boolean(this.mediaSource && !this.mediaFailed),
    };
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.appendQueue.length = 0;
    if (this.mediaUrl) URL.revokeObjectURL(this.mediaUrl);
    if (this.blobUrl) URL.revokeObjectURL(this.blobUrl);
    this.mediaUrl = "";
    this.blobUrl = "";
    this.mediaSource = null;
    this.sourceBuffer = null;
  }

  private append(bytes: Uint8Array) {
    if (bytes.byteLength === 0) return;
    const owned = new Uint8Array(bytes.byteLength);
    owned.set(bytes);
    this.chunks.push(owned);
    this.ensureMediaSource();
    if (this.mediaSource && !this.mediaFailed) {
      this.appendQueue.push(owned);
      this.drainQueue();
    }
  }

  private ensureMediaSource() {
    if (
      this.mediaSource ||
      this.mediaFailed ||
      typeof window.MediaSource === "undefined" ||
      !window.MediaSource.isTypeSupported("audio/mpeg")
    ) {
      return;
    }
    try {
      this.mediaSource = new MediaSource();
      this.mediaUrl = URL.createObjectURL(this.mediaSource);
      this.mediaSource.addEventListener(
        "sourceopen",
        () => {
          if (!this.mediaSource || this.disposed) return;
          try {
            this.sourceBuffer =
              this.mediaSource.addSourceBuffer("audio/mpeg");
            this.sourceBuffer.addEventListener("updateend", () => {
              this.drainQueue();
              this.endMediaStreamWhenReady();
            });
            this.sourceBuffer.addEventListener("error", () => {
              this.fallbackToBlob("浏览器无法继续追加语音流，已改为完整播放。");
            });
            this.options.onPlaybackUrl?.(this.mediaUrl, true);
            this.drainQueue();
            this.endMediaStreamWhenReady();
          } catch {
            this.fallbackToBlob("浏览器不支持增量播放，已改为完整播放。");
          }
        },
        { once: true },
      );
    } catch {
      this.mediaSource = null;
      this.mediaFailed = true;
    }
  }

  private drainQueue() {
    if (
      this.disposed ||
      this.mediaFailed ||
      !this.sourceBuffer ||
      this.sourceBuffer.updating ||
      this.appendQueue.length === 0
    ) {
      return;
    }
    const bytes = this.appendQueue.shift();
    if (!bytes) return;
    try {
      const copy = new Uint8Array(bytes.byteLength);
      copy.set(bytes);
      this.sourceBuffer.appendBuffer(copy.buffer);
    } catch {
      this.fallbackToBlob("语音流追加失败，已改为完整播放。");
    }
  }

  private endMediaStreamWhenReady() {
    if (
      !this.finished ||
      this.mediaFailed ||
      !this.mediaSource ||
      this.mediaSource.readyState !== "open" ||
      this.sourceBuffer?.updating ||
      this.appendQueue.length > 0
    ) {
      return;
    }
    try {
      this.mediaSource.endOfStream();
    } catch {
      this.fallbackToBlob("语音流收尾失败，已改为完整播放。");
    }
  }

  private fallbackToBlob(message: string) {
    if (this.mediaFailed) return;
    this.mediaFailed = true;
    this.appendQueue.length = 0;
    this.options.onPlaybackFallback?.(message);
    if (this.finished && this.blobUrl) {
      this.options.onPlaybackUrl?.(this.blobUrl, false);
    }
  }
}

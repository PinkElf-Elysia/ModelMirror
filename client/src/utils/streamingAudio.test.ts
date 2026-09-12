import { afterEach, describe, expect, it, vi } from "vitest";
import { StreamingAudioSession } from "./streamingAudio";

function encode(bytes: Uint8Array) {
  return window.btoa(String.fromCharCode(...bytes));
}

describe("StreamingAudioSession WAV buffering", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("buffers continuous base64 and exposes one WAV blob only at finish", async () => {
    const expected = new Uint8Array([82, 73, 70, 70, 4, 0, 0, 0, 87, 65, 86, 69]);
    const encoded = encode(expected);
    const capturedBlobs: Blob[] = [];
    const createObjectURL = vi.fn((value: Blob) => {
      capturedBlobs.push(value);
      return "blob:modelmirror-wav";
    });
    const onPlaybackUrl = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL,
      revokeObjectURL: vi.fn(),
    });

    const session = new StreamingAudioSession({
      format: "wav",
      onPlaybackUrl,
    });
    session.pushBase64(encoded.slice(0, 5));
    session.pushBase64(encoded.slice(5));

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(onPlaybackUrl).not.toHaveBeenCalled();

    const result = session.finish();
    expect(result).toEqual({
      blobUrl: "blob:modelmirror-wav",
      playbackUrl: "blob:modelmirror-wav",
      byteLength: expected.byteLength,
      streamed: false,
    });
    expect(onPlaybackUrl).toHaveBeenCalledWith("blob:modelmirror-wav", false);
    const capturedBlob = capturedBlobs[0];
    expect(capturedBlob).toBeDefined();
    if (!capturedBlob) throw new Error("WAV blob was not created");
    expect(capturedBlob.type).toBe("audio/wav");
    expect(new Uint8Array(await capturedBlob.arrayBuffer())).toEqual(expected);
  });

  it("joins independently padded WAV chunks without creating a media stream", async () => {
    const first = new Uint8Array([82, 73, 70, 70, 4]);
    const second = new Uint8Array([0, 0, 0, 87, 65, 86, 69]);
    const capturedBlobs: Blob[] = [];
    const mediaSourceConstructor = vi.fn();
    vi.stubGlobal(
      "MediaSource",
      class {
        static isTypeSupported() {
          return true;
        }

        constructor() {
          mediaSourceConstructor();
        }
      },
    );
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn((value: Blob) => {
        capturedBlobs.push(value);
        return "blob:modelmirror-wav-padded";
      }),
      revokeObjectURL: vi.fn(),
    });

    const session = new StreamingAudioSession({ format: "wav" });
    session.pushBase64(encode(first));
    session.pushBase64(encode(second));
    const result = session.finish();

    expect(result.streamed).toBe(false);
    expect(mediaSourceConstructor).not.toHaveBeenCalled();
    const capturedBlob = capturedBlobs[0];
    expect(capturedBlob).toBeDefined();
    if (!capturedBlob) throw new Error("WAV blob was not created");
    expect(new Uint8Array(await capturedBlob.arrayBuffer())).toEqual(
      new Uint8Array([...first, ...second]),
    );
  });
});

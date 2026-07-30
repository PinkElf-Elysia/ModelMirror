import assert from "node:assert/strict";
import { fetchChatStream } from "../src/utils/fetchChatStream.ts";
import { IncrementalBase64Decoder } from "../src/utils/streamingAudio.ts";

globalThis.window = {
  atob: globalThis.atob,
};

function merge(chunks) {
  return Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)));
}

const source = Buffer.from("ID3-stream-tail");
const encoded = source.toString("base64");
const arbitraryBoundaries = [
  encoded.slice(0, 1),
  encoded.slice(1, 3),
  encoded.slice(3, 7),
  encoded.slice(7, 13),
  encoded.slice(13),
];
const decoder = new IncrementalBase64Decoder();
const decoded = arbitraryBoundaries
  .flatMap((chunk) => decoder.push(chunk))
  .concat(decoder.finish());
assert.deepEqual(merge(decoded), source);

const independentlyPadded = new IncrementalBase64Decoder();
const paddedDecoded = [
  Buffer.from("ID3-").toString("base64"),
  Buffer.from("tail").toString("base64"),
]
  .flatMap((chunk) => independentlyPadded.push(chunk))
  .concat(independentlyPadded.finish());
assert.equal(merge(paddedDecoded).toString(), "ID3-tail");

const incomplete = new IncrementalBase64Decoder();
incomplete.push("S");
assert.throws(() => incomplete.finish(), /结尾不完整/);

const encoder = new TextEncoder();
const streamEvents = [];
const audioPieces = [
  Buffer.from("ID3-first").toString("base64"),
  Buffer.from("-last").toString("base64"),
];
globalThis.fetch = async () =>
  new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({
              choices: [
                {
                  delta: {
                    audio: {
                      data: audioPieces[0],
                      transcript: "第一段",
                    },
                  },
                  finish_reason: "stop",
                },
              ],
            })}\n\n`,
          ),
        );
        controller.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({
              choices: [
                {
                  delta: {
                    audio: {
                      data: audioPieces[1],
                      transcript: "第二段",
                    },
                  },
                  finish_reason: null,
                },
              ],
            })}\n\nevent: message_end\ndata: {}\n\ndata: [DONE]\n\n`,
          ),
        );
        controller.close();
      },
    }),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    },
  );

await fetchChatStream({
  modelId: "openai/gpt-audio",
  messages: [{ role: "user", content: "请用语音回答" }],
  responseAudio: { enabled: true, voice: "alloy", format: "mp3" },
  onDelta: (delta) => streamEvents.push(`text:${delta}`),
  onAudioDelta: (audio) =>
    streamEvents.push(`audio:${audio.transcript ?? ""}`),
  onMessageEnd: () => streamEvents.push("end"),
});
assert.deepEqual(streamEvents, [
  "audio:第一段",
  "audio:第二段",
  "end",
]);

console.log("streaming audio base64 checks passed");

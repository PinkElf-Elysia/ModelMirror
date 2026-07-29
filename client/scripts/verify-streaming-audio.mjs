import assert from "node:assert/strict";
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

console.log("streaming audio base64 checks passed");

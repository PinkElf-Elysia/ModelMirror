import assert from "node:assert/strict";
import test from "node:test";
import { IncomingFrames, MAX_FRAME_BYTES, PROTOCOL, encodeFrame } from "../src/protocol.mjs";

test("round-trips strict sequential frames", () => {
  const frames = new IncomingFrames();
  const first = frames.push(encodeFrame(1, "run.cancel", { run_id: "r1" }));
  assert.equal(first.length, 1);
  assert.equal(first[0].protocol, PROTOCOL);
  const secondLine = encodeFrame(2, "run.shutdown", {});
  assert.equal(frames.push(secondLine.slice(0, 7)).length, 0);
  assert.equal(frames.push(secondLine.slice(7)).length, 1);
  frames.end();
});

test("rejects gaps, duplicates and unknown fields", () => {
  assert.throws(() => new IncomingFrames().push(encodeFrame(2, "run.shutdown", {})), /sequence mismatch/);
  const duplicate = new IncomingFrames();
  duplicate.push(encodeFrame(1, "run.shutdown", {}));
  assert.throws(() => duplicate.push(encodeFrame(1, "run.shutdown", {})), /sequence mismatch/);
  const bad = JSON.stringify({ protocol: PROTOCOL, seq: 1, type: "run.shutdown", payload: {}, pollution: true });
  assert.throws(() => new IncomingFrames().push(`${bad}\n`), /unknown fields/);
});

test("rejects unsupported, blank and oversized frames", () => {
  const unsupported = JSON.stringify({ protocol: PROTOCOL, seq: 1, type: "mystery", payload: {} });
  assert.throws(() => new IncomingFrames().push(`${unsupported}\n`), /unsupported/);
  assert.throws(() => new IncomingFrames().push("\n"), /blank/);
  assert.throws(() => encodeFrame(1, "run.shutdown", { data: "x".repeat(MAX_FRAME_BYTES) }), /4 MiB/);
});

test("rejects truncated final input", () => {
  const frames = new IncomingFrames();
  frames.push('{"protocol":');
  assert.throws(() => frames.end(), /truncated/);
});

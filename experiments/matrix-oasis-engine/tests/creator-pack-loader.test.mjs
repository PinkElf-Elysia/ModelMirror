import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  LocalPackLoader,
  MAX_LOCAL_PACK_BYTES,
  prepareCreatorSession,
} from "../apps/creator-web/src/pack-loader.ts";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const mechanicsText = readFileSync(
  path.join(
    moduleRoot,
    "examples",
    "mechanics-conformance.authoring-game-pack.json",
  ),
  "utf8",
);

function encode(text) {
  return new TextEncoder().encode(text);
}

class FakeFile {
  #bytes;
  #sizes;
  #sizeReadCount = 0;
  #waitForRead;
  #readError;

  constructor(name, bytes, options = {}) {
    this.name = name;
    this.#bytes = bytes;
    this.#sizes = options.sizes ?? [bytes.byteLength];
    this.#waitForRead = options.waitForRead;
    this.#readError = options.readError;
    this.readCount = 0;
  }

  get size() {
    const index = Math.min(this.#sizeReadCount, this.#sizes.length - 1);
    this.#sizeReadCount += 1;
    return this.#sizes[index];
  }

  async arrayBuffer() {
    this.readCount += 1;
    if (this.#waitForRead) {
      await this.#waitForRead;
    }
    if (this.#readError) {
      throw this.#readError;
    }
    return this.#bytes.slice().buffer;
  }
}

async function makeActiveSession() {
  const result = await prepareCreatorSession(mechanicsText, {
    kind: "builtin",
    id: "neutral",
  });
  assert.equal(result.ok, true);
  return result.candidate;
}

function diagnosticCode(result) {
  assert.equal(result.status, "rejected");
  return result.diagnostics[0].code;
}

function assertPreserved(result, activeSession) {
  assert.strictEqual(result.activeSession, activeSession);
  assert.equal(Object.hasOwn(result, "candidate"), false);
}

test("loads JSON into a separate frozen parity candidate and canonical artifact", async () => {
  const activeSession = await makeActiveSession();
  const loader = new LocalPackLoader();
  const result = await loader.loadCandidate(
    new FakeFile("PRIVATE-NAME.JSON", encode(mechanicsText)),
    activeSession,
  );

  assert.equal(result.status, "ready");
  assert.strictEqual(result.activeSession, activeSession);
  assert.notStrictEqual(result.candidate, activeSession);
  assert.deepEqual(result.candidate.source, { kind: "local" });
  assert.equal(result.candidate.snapshot.snapshotVersion, 1);
  assert.equal(result.candidate.snapshot.authoring.stepCount, 0);
  assert.equal(result.candidate.snapshot.runtime.stepCount, 0);
  assert.equal(result.candidate.inspection.pack.id, "mechanics-conformance");
  assert.equal(
    JSON.stringify(JSON.parse(result.candidate.artifact.runtimePackJson)),
    result.candidate.artifact.runtimePackJson,
  );
  assert.equal(Object.isFrozen(result.candidate), true);
  assert.equal(Object.isFrozen(result.candidate.artifact), true);
  assert.equal(JSON.stringify(result).includes("PRIVATE-NAME"), false);
});

test("rejects invalid Pack content while preserving the active session", async () => {
  const activeSession = await makeActiveSession();
  const result = await new LocalPackLoader().loadCandidate(
    new FakeFile("candidate.json", encode("{")),
    activeSession,
  );

  assert.equal(result.status, "rejected");
  assert.equal(result.diagnostics[0].phase, "parse");
  assertPreserved(result, activeSession);
  assert.equal(Object.isFrozen(result.diagnostics), true);
});

test("accepts an exactly 1 MiB UTF-8 JSON document", async () => {
  const sourceBytes = encode(mechanicsText);
  const exactText = `${mechanicsText}${" ".repeat(
    MAX_LOCAL_PACK_BYTES - sourceBytes.byteLength,
  )}`;
  const result = await new LocalPackLoader().loadCandidate(
    new FakeFile("exact.json", encode(exactText)),
    null,
  );

  assert.equal(result.status, "ready");
  assert.equal(result.candidate.inspection.pack.id, "mechanics-conformance");
});

test("rejects files above 1 MiB before reading", async () => {
  const file = new FakeFile("large.json", encode("{}"), {
    sizes: [MAX_LOCAL_PACK_BYTES + 1],
  });
  const activeSession = await makeActiveSession();
  const result = await new LocalPackLoader().loadCandidate(file, activeSession);

  assert.equal(diagnosticCode(result), "PACK_LOADER_FILE_TOO_LARGE");
  assert.equal(file.readCount, 0);
  assertPreserved(result, activeSession);
});

test("rechecks size and identity after the asynchronous read", async () => {
  const bytes = encode(mechanicsText);
  const activeSession = await makeActiveSession();
  const oversized = await new LocalPackLoader().loadCandidate(
    new FakeFile("oversized.json", new Uint8Array(MAX_LOCAL_PACK_BYTES + 1), {
      sizes: [MAX_LOCAL_PACK_BYTES, MAX_LOCAL_PACK_BYTES + 1],
    }),
    activeSession,
  );
  const changed = await new LocalPackLoader().loadCandidate(
    new FakeFile("changed.json", bytes, {
      sizes: [bytes.byteLength, bytes.byteLength + 1],
    }),
    activeSession,
  );

  assert.equal(diagnosticCode(oversized), "PACK_LOADER_FILE_TOO_LARGE");
  assert.equal(diagnosticCode(changed), "PACK_LOADER_FILE_CHANGED");
  assertPreserved(oversized, activeSession);
  assertPreserved(changed, activeSession);
});

test("rejects invalid UTF-8 and wrong extensions without replacement decoding", async () => {
  const activeSession = await makeActiveSession();
  const invalidUtf8 = await new LocalPackLoader().loadCandidate(
    new FakeFile("invalid.json", new Uint8Array([0xc3, 0x28])),
    activeSession,
  );
  const wrongFile = new FakeFile("candidate.json.txt", encode(mechanicsText));
  const wrongExtension = await new LocalPackLoader().loadCandidate(
    wrongFile,
    activeSession,
  );

  assert.equal(diagnosticCode(invalidUtf8), "PACK_LOADER_UTF8_INVALID");
  assert.equal(diagnosticCode(wrongExtension), "PACK_LOADER_EXTENSION_INVALID");
  assert.equal(wrongFile.readCount, 0);
});

test("maps read exceptions to static diagnostics without leakage", async () => {
  const activeSession = await makeActiveSession();
  const sentinel = ["PRIVATE", "READ", "FAILURE"].join("-");
  const result = await new LocalPackLoader().loadCandidate(
    new FakeFile("private.json", encode(mechanicsText), {
      readError: new Error(sentinel),
    }),
    activeSession,
  );

  assert.equal(diagnosticCode(result), "PACK_LOADER_READ_FAILED");
  assert.equal(JSON.stringify(result).includes(sentinel), false);
  assertPreserved(result, activeSession);
});

test("a newer request makes an older completion stale", async () => {
  let releaseFirst;
  const waitForRead = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  const activeSession = await makeActiveSession();
  const loader = new LocalPackLoader();
  const firstPromise = loader.loadCandidate(
    new FakeFile("first.json", encode(mechanicsText), { waitForRead }),
    activeSession,
  );
  const second = await loader.loadCandidate(
    new FakeFile("second.json", encode(mechanicsText)),
    activeSession,
  );
  releaseFirst();
  const first = await firstPromise;

  assert.equal(second.status, "ready");
  assert.equal(first.status, "stale");
  assert.strictEqual(first.activeSession, activeSession);
  assert.equal(Object.hasOwn(first, "candidate"), false);
  assert.equal(Object.hasOwn(first, "diagnostics"), false);
});

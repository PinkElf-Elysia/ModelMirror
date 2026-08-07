import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  createGameSession,
  prepareAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-simulator";
import {
  LocalPackLoader,
  MAX_LOCAL_PACK_BYTES,
} from "../apps/creator-web/src/pack-loader.ts";
import {
  applySessionActionCandidate,
  resetSessionCandidate,
  selectSessionCandidate,
} from "../apps/creator-web/src/session-transaction.ts";

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

function makeActiveSession() {
  const prepared = prepareAuthoringGamePackJson(mechanicsText);
  assert.equal(prepared.ok, true);
  const created = createGameSession(prepared.prepared);
  assert.equal(created.ok, true);
  return Object.freeze({
    source: Object.freeze({ kind: "builtin", id: "neutral" }),
    prepared: prepared.prepared,
    snapshot: created.snapshot,
    inspection: created.inspection,
    emittedCues: created.emittedCues,
    transition: null,
  });
}

function diagnosticCode(result) {
  assert.equal(result.status, "rejected");
  return result.diagnostics[0].code;
}

function assertPreserved(result, activeSession) {
  assert.strictEqual(result.activeSession, activeSession);
  assert.equal(Object.hasOwn(result, "candidate"), false);
}

test("loads case-insensitive JSON into a separate frozen local candidate", async () => {
  const activeSession = makeActiveSession();
  const file = new FakeFile("PRIVATE-NAME.JSON", encode(mechanicsText));
  const loader = new LocalPackLoader();

  const result = await loader.loadCandidate(file, activeSession);

  assert.equal(result.status, "ready");
  assert.equal(result.requestToken, 1);
  assert.equal(loader.latestRequestToken, 1);
  assert.strictEqual(result.activeSession, activeSession);
  assert.notStrictEqual(result.candidate, activeSession);
  assert.deepEqual(result.candidate.source, { kind: "local" });
  assert.equal(result.candidate.transition, null);
  assert.equal(result.candidate.snapshot.stepCount, 0);
  assert.equal(result.candidate.inspection.pack.id, "mechanics-conformance");
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.candidate), true);
  assert.equal(Object.isFrozen(result.candidate.source), true);
  assert.equal(Object.isFrozen(result.candidate.prepared), true);
  assert.equal(Object.isFrozen(result.candidate.snapshot), true);
  assert.equal(Object.isFrozen(result.candidate.inspection), true);
  assert.equal(Object.isFrozen(result.candidate.emittedCues), true);
  assert.equal(JSON.stringify(result).includes("PRIVATE-NAME"), false);
  assert.equal(activeSession.source.kind, "builtin");
});

test("rejects invalid Pack content while preserving the active session", async () => {
  const activeSession = makeActiveSession();
  const loader = new LocalPackLoader();
  const result = await loader.loadCandidate(
    new FakeFile("candidate.json", encode("{")),
    activeSession,
  );

  assert.equal(result.status, "rejected");
  assert.equal(result.diagnostics[0].phase, "parse");
  assertPreserved(result, activeSession);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.diagnostics), true);
});

test("accepts an exactly 1 MiB UTF-8 JSON document", async () => {
  const sourceBytes = encode(mechanicsText);
  assert.ok(sourceBytes.byteLength < MAX_LOCAL_PACK_BYTES);
  const exactText = `${mechanicsText}${" ".repeat(
    MAX_LOCAL_PACK_BYTES - sourceBytes.byteLength,
  )}`;
  const exactBytes = encode(exactText);
  assert.equal(exactBytes.byteLength, MAX_LOCAL_PACK_BYTES);

  const result = await new LocalPackLoader().loadCandidate(
    new FakeFile("exact.json", exactBytes),
    null,
  );

  assert.equal(result.status, "ready");
  assert.equal(result.candidate.inspection.pack.id, "mechanics-conformance");
});

test("rejects files above 1 MiB before reading", async () => {
  const file = new FakeFile("large.json", encode("{}"), {
    sizes: [MAX_LOCAL_PACK_BYTES + 1],
  });
  const activeSession = makeActiveSession();
  const result = await new LocalPackLoader().loadCandidate(file, activeSession);

  assert.equal(diagnosticCode(result), "PACK_LOADER_FILE_TOO_LARGE");
  assert.equal(file.readCount, 0);
  assertPreserved(result, activeSession);
});

test("rechecks the 1 MiB limit after an asynchronous read", async () => {
  const bytes = new Uint8Array(MAX_LOCAL_PACK_BYTES + 1);
  const file = new FakeFile("post-limit.json", bytes, {
    sizes: [MAX_LOCAL_PACK_BYTES, MAX_LOCAL_PACK_BYTES + 1],
  });
  const activeSession = makeActiveSession();
  const result = await new LocalPackLoader().loadCandidate(file, activeSession);

  assert.equal(diagnosticCode(result), "PACK_LOADER_FILE_TOO_LARGE");
  assert.equal(file.readCount, 1);
  assertPreserved(result, activeSession);
});

test("rejects a file that grows after the pre-read size check", async () => {
  const bytes = encode(mechanicsText);
  const file = new FakeFile("growing.json", bytes, {
    sizes: [bytes.byteLength, bytes.byteLength + 1],
  });
  const activeSession = makeActiveSession();
  const result = await new LocalPackLoader().loadCandidate(file, activeSession);

  assert.equal(diagnosticCode(result), "PACK_LOADER_FILE_CHANGED");
  assert.equal(file.readCount, 1);
  assertPreserved(result, activeSession);
});

test("rejects invalid UTF-8 without passing replacement text to the validator", async () => {
  const bytes = new Uint8Array([0xc3, 0x28]);
  const activeSession = makeActiveSession();
  const result = await new LocalPackLoader().loadCandidate(
    new FakeFile("invalid-utf8.json", bytes),
    activeSession,
  );

  assert.equal(diagnosticCode(result), "PACK_LOADER_UTF8_INVALID");
  assertPreserved(result, activeSession);
});

test("rejects a wrong extension case-insensitively before reading", async () => {
  const activeSession = makeActiveSession();
  for (const name of ["candidate.JsOn.txt", "candidate.json\n"]) {
    const file = new FakeFile(name, encode(mechanicsText));
    const result = await new LocalPackLoader().loadCandidate(file, activeSession);

    assert.equal(diagnosticCode(result), "PACK_LOADER_EXTENSION_INVALID");
    assert.equal(file.readCount, 0);
    assertPreserved(result, activeSession);
  }
});

test("maps read exceptions to static diagnostics without leaking details", async () => {
  const activeSession = makeActiveSession();
  const file = new FakeFile("PRIVATE-FILE.json", encode(mechanicsText), {
    readError: new Error("PRIVATE-THROW-MESSAGE"),
  });
  const result = await new LocalPackLoader().loadCandidate(file, activeSession);
  const serialized = JSON.stringify(result);

  assert.equal(diagnosticCode(result), "PACK_LOADER_READ_FAILED");
  assert.equal(serialized.includes("PRIVATE-FILE"), false);
  assert.equal(serialized.includes("PRIVATE-THROW-MESSAGE"), false);
  assertPreserved(result, activeSession);
});

test("a newer request makes an older completion stale", async () => {
  let releaseFirst;
  const firstRead = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  const activeSession = makeActiveSession();
  const loader = new LocalPackLoader();
  const firstPromise = loader.loadCandidate(
    new FakeFile("first.json", encode(mechanicsText), {
      waitForRead: firstRead,
    }),
    activeSession,
  );
  const secondPromise = loader.loadCandidate(
    new FakeFile("second.json", encode(mechanicsText)),
    activeSession,
  );
  const second = await secondPromise;
  releaseFirst();
  const first = await firstPromise;

  assert.equal(second.status, "ready");
  assert.equal(second.requestToken, 2);
  assert.equal(first.status, "stale");
  assert.equal(first.requestToken, 1);
  assert.strictEqual(first.activeSession, activeSession);
  assert.equal(Object.hasOwn(first, "candidate"), false);
  assert.equal(Object.hasOwn(first, "diagnostics"), false);
  assert.equal(loader.latestRequestToken, 2);
});

test("a stale operation candidate cannot overwrite or mix into a newer session", () => {
  const original = makeActiveSession();
  const replacement = makeActiveSession();
  const lateReset = resetSessionCandidate(original);
  assert.equal(lateReset.ok, true);

  const decision = selectSessionCandidate(
    replacement,
    original,
    lateReset.candidate,
  );

  assert.equal(decision.committed, false);
  assert.strictEqual(decision.session, replacement);
  assert.notStrictEqual(decision.session, lateReset.candidate);
  assert.strictEqual(decision.session.prepared, replacement.prepared);
  assert.strictEqual(decision.session.snapshot, replacement.snapshot);
});

test("simulator operational failures become static diagnostics without leakage", () => {
  const activeSession = makeActiveSession();
  const sentinel = "PRIVATE-SIMULATOR-THROW";
  const throwingCreate = () => {
    throw new Error(sentinel);
  };
  const throwingApply = () => {
    throw new Error(sentinel);
  };

  const reset = resetSessionCandidate(activeSession, throwingCreate);
  const applied = applySessionActionCandidate(
    activeSession,
    "action-initialize",
    throwingApply,
  );

  for (const result of [reset, applied]) {
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PACK_RUNTIME_INTERNAL_ERROR");
    assert.equal(JSON.stringify(result).includes(sentinel), false);
    assert.equal(Object.isFrozen(result), true);
    assert.equal(Object.isFrozen(result.diagnostics), true);
  }
});

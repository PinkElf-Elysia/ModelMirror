import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  validateAuthoringGamePack,
  validateAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-validator";
import {
  CANONICAL_JSON_PROFILE,
  GAME_PACK_COMPILER_ID,
  GAME_PACK_COMPILER_VERSION,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";

import {
  GamePackCompilerOperationalError,
  compileAuthoringGamePack,
  compileAuthoringGamePackJson,
} from "../src/index.mjs";
import { createGamePackCompiler } from "../src/compiler.mjs";

function makeAuthoringPack() {
  return {
    format: "matrix-oasis.authoring-game-pack",
    formatVersion: "0.1.0",
    id: "compiler-conformance",
    contentVersion: "1",
    language: "en",
    title: "Compiler conformance",
    entryNodeId: "node-start",
    entities: [
      { id: "entity-first", label: "First" },
      {
        id: "entity-second",
        label: "Second",
        description: "Second entity.",
      },
    ],
    variables: [
      { id: "flag-value", type: "boolean", initial: false },
      { id: "count-value", type: "integer", initial: 0 },
      {
        id: "mode-value",
        type: "enum",
        allowedValues: ["mode-alpha", "mode-beta"],
        initial: "mode-alpha",
      },
    ],
    cues: [
      { id: "cue-first", channel: "visual", intent: "First cue." },
      { id: "cue-second", channel: "audio", intent: "Second cue." },
      { id: "cue-third", channel: "ui", intent: "Third cue." },
    ],
    nodes: [
      {
        id: "node-start",
        title: "Start",
        entityIds: ["entity-second", "entity-first"],
        entryCueIds: ["cue-third", "cue-first"],
        actions: [
          {
            id: "action-all-ops",
            label: "Exercise all operations",
            when: {
              op: "all",
              conditions: [
                { op: "eq", variableId: "flag-value", value: false },
                { op: "ne", variableId: "mode-value", value: "mode-beta" },
                { op: "lt", variableId: "count-value", value: 1 },
                { op: "lte", variableId: "count-value", value: 0 },
                { op: "gt", variableId: "count-value", value: -1 },
                { op: "gte", variableId: "count-value", value: 0 },
                {
                  op: "any",
                  conditions: [
                    { op: "eq", variableId: "mode-value", value: "mode-alpha" },
                    {
                      op: "not",
                      condition: {
                        op: "eq",
                        variableId: "flag-value",
                        value: true,
                      },
                    },
                  ],
                },
              ],
            },
            effects: [
              { op: "set", variableId: "flag-value", value: true },
              { op: "add", variableId: "count-value", value: 2 },
              { op: "emitCue", cueId: "cue-second" },
              { op: "set", variableId: "mode-value", value: "mode-beta" },
            ],
            target: { kind: "node", id: "node-finish" },
          },
        ],
      },
      {
        id: "node-finish",
        title: "Finish",
        text: "Finish text.",
        entityIds: [],
        entryCueIds: ["cue-second"],
        actions: [
          {
            id: "action-end",
            label: "End",
            entityIds: ["entity-first"],
            effects: [],
            target: { kind: "ending", id: "ending-done" },
          },
        ],
      },
    ],
    endings: [
      {
        id: "ending-done",
        title: "Done",
        cueIds: ["cue-second", "cue-first"],
      },
    ],
  };
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function assertDeepFrozen(value, seen = new Set()) {
  if (!value || typeof value !== "object" || seen.has(value)) {
    return;
  }
  seen.add(value);
  assert.equal(Object.isFrozen(value), true);
  for (const child of Object.values(value)) {
    assertDeepFrozen(child, seen);
  }
}

async function sha256Hex(text) {
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

test("public surface is exactly the two async APIs and operational error", async () => {
  const module = await import("../src/index.mjs");
  assert.deepEqual(Object.keys(module).sort(), [
    "GamePackCompilerOperationalError",
    "compileAuthoringGamePack",
    "compileAuthoringGamePackJson",
  ]);
  const valuePromise = compileAuthoringGamePack(makeAuthoringPack());
  const jsonPromise = compileAuthoringGamePackJson(
    JSON.stringify(makeAuthoringPack()),
  );
  assert.equal(valuePromise instanceof Promise, true);
  assert.equal(jsonPromise instanceof Promise, true);
  const [valueResult, jsonResult] = await Promise.all([
    valuePromise,
    jsonPromise,
  ]);
  assert.equal(valueResult.ok, true);
  assert.equal(jsonResult.ok, true);
});

test("invalid content returns the original R1 validation report and frozen output", async () => {
  const invalid = makeAuthoringPack();
  invalid.entryNodeId = "missing-node";
  const directReport = validateAuthoringGamePack(invalid);
  const result = await compileAuthoringGamePack(invalid);

  assert.equal(result.ok, false);
  assert.deepEqual(result.validationReport, directReport);
  assert.equal(result.validationReport.valid, false);
  assert.equal(
    result.validationReport.diagnostics.some(
      ({ code }) => code === "PACK_ENTRY_NODE_UNKNOWN",
    ),
    true,
  );
  assertDeepFrozen(result);

  const invalidJson = await compileAuthoringGamePackJson("{");
  assert.equal(invalidJson.ok, false);
  assert.deepEqual(invalidJson.validationReport, validateAuthoringGamePackJson("{"));
  assert.equal(invalidJson.validationReport.diagnostics[0].phase, "parse");
  assertDeepFrozen(invalidJson);
  assert.deepEqual(Reflect.ownKeys(result), ["ok", "validationReport"]);
});

test("invalid input never reaches Web Crypto or the Runtime Validator", async () => {
  const invalid = makeAuthoringPack();
  invalid.entryNodeId = "missing-node";
  let runtimeValidationCalls = 0;
  const isolatedCompiler = createGamePackCompiler({
    validateRuntimeJson: async () => {
      runtimeValidationCalls += 1;
      throw new Error("must not run");
    },
  });
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      enumerable: true,
      value: undefined,
    });
    const result = await isolatedCompiler.compileAuthoringGamePack(invalid);
    assert.equal(result.ok, false);
    assert.equal(runtimeValidationCalls, 0);
  } finally {
    Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
  }
});

test("captures and revalidates a canonical descriptor-safe object snapshot", async () => {
  let valueValidationCalls = 0;
  let jsonValidationCalls = 0;
  const isolatedCompiler = createGamePackCompiler({
    validateAuthoringValue(value) {
      valueValidationCalls += 1;
      return validateAuthoringGamePack(value);
    },
    validateAuthoringJson(text) {
      jsonValidationCalls += 1;
      return validateAuthoringGamePackJson(text);
    },
  });

  const result = await isolatedCompiler.compileAuthoringGamePack(
    makeAuthoringPack(),
  );
  assert.equal(result.ok, true);
  assert.equal(valueValidationCalls, 0);
  assert.equal(jsonValidationCalls, 1);
});

test("is immune to source mutation after descriptor capture", async () => {
  const source = makeAuthoringPack();
  const expectedTitle = source.title;
  const expectedEntityLabel = source.entities[0].label;
  let mutationScheduled = false;
  const mutableProxy = new Proxy(source, {
    ownKeys(target) {
      if (!mutationScheduled) {
        mutationScheduled = true;
        queueMicrotask(() => {
          target.title = "Mutated after capture";
          target.entities[0].label = "Mutated entity after capture";
        });
      }
      return Reflect.ownKeys(target);
    },
  });

  const result = await compileAuthoringGamePack(mutableProxy);
  assert.equal(result.ok, true);
  assert.equal(source.title, "Mutated after capture");
  assert.equal(result.runtimePack.title, expectedTitle);
  assert.equal(result.runtimePack.entities[0].label, expectedEntityLabel);
});

test("compiles the full Authoring shape into ordered typed indexes and explicit optionals", async () => {
  const source = makeAuthoringPack();
  const result = await compileAuthoringGamePack(source);
  assert.equal(result.ok, true);
  assert.deepEqual(Reflect.ownKeys(result), [
    "ok",
    "runtimePack",
    "canonicalJson",
    "receipt",
  ]);
  assert.deepEqual(Object.getOwnPropertySymbols(result), []);

  assert.deepEqual(result.runtimePack.entities, [
    { id: "entity-first", label: "First", description: null },
    {
      id: "entity-second",
      label: "Second",
      description: "Second entity.",
    },
  ]);
  assert.equal(result.runtimePack.summary, null);
  assert.equal(result.runtimePack.entryNodeIndex, 0);
  assert.deepEqual(result.runtimePack.nodes[0].entityIndexes, [1, 0]);
  assert.deepEqual(result.runtimePack.nodes[0].entryCueIndexes, [2, 0]);
  assert.equal(result.runtimePack.nodes[0].text, null);
  assert.equal(result.runtimePack.nodes[1].actions[0].when, null);
  assert.deepEqual(result.runtimePack.nodes[1].actions[0].entityIndexes, [0]);
  assert.deepEqual(result.runtimePack.endings[0].cueIndexes, [1, 0]);
  assert.deepEqual(result.runtimePack.cues.map(({ id }) => id), [
    "cue-first",
    "cue-second",
    "cue-third",
  ]);

  const condition = result.runtimePack.nodes[0].actions[0].when;
  assert.deepEqual(
    condition.conditions.slice(0, 6).map(({ op, variableIndex }) => [op, variableIndex]),
    [
      ["eq", 0],
      ["ne", 2],
      ["lt", 1],
      ["lte", 1],
      ["gt", 1],
      ["gte", 1],
    ],
  );
  assert.equal(condition.conditions[6].conditions[0].variableIndex, 2);
  assert.equal(condition.conditions[6].conditions[1].condition.variableIndex, 0);
  assert.deepEqual(result.runtimePack.nodes[0].actions[0].effects, [
    { op: "set", variableIndex: 0, value: true },
    { op: "add", variableIndex: 1, value: 2 },
    { op: "emitCue", cueIndex: 1 },
    { op: "set", variableIndex: 2, value: "mode-beta" },
  ]);
  assert.deepEqual(result.runtimePack.nodes[0].actions[0].target, {
    kind: "node",
    index: 1,
  });
  assert.deepEqual(result.runtimePack.nodes[1].actions[0].target, {
    kind: "ending",
    index: 0,
  });
});

test("canonical source and artifact hashes plus UTF-8 byte length are exact", async () => {
  const source = makeAuthoringPack();
  source.title = "编译器一致性";
  source.summary = "Preserved summary.";
  source.endings[0].text = "Preserved ending text.";
  const result = await compileAuthoringGamePack(source);
  assert.equal(result.ok, true);
  assert.equal(result.runtimePack.summary, source.summary);
  assert.equal(result.runtimePack.endings[0].text, source.endings[0].text);

  const canonicalSource = canonicalizeJsonValue(source);
  assert.equal(
    result.runtimePack.source.canonicalSha256,
    await sha256Hex(canonicalSource),
  );
  assert.equal(result.canonicalJson, canonicalizeJsonValue(result.runtimePack));
  assert.deepEqual(result.receipt, {
    format: "matrix-oasis.runtime-game-pack-receipt",
    formatVersion: "0.1.0",
    canonicalization: CANONICAL_JSON_PROFILE,
    compiler: {
      id: GAME_PACK_COMPILER_ID,
      version: GAME_PACK_COMPILER_VERSION,
    },
    artifact: {
      format: "matrix-oasis.runtime-game-pack",
      formatVersion: "0.1.0",
      sha256: await sha256Hex(result.canonicalJson),
      byteLength: new TextEncoder().encode(result.canonicalJson).byteLength,
    },
  });

  const validation = await validateRuntimeGamePackJson(
    result.canonicalJson,
    canonicalizeJsonValue(result.receipt),
  );
  assert.deepEqual(validation, {
    reportVersion: 1,
    valid: true,
    diagnostics: [],
  });
});

test("object and JSON APIs produce byte-identical deterministic artifacts", async () => {
  const source = makeAuthoringPack();
  const reorderedText = JSON.stringify({
    endings: source.endings,
    nodes: source.nodes,
    cues: source.cues,
    variables: source.variables,
    entities: source.entities,
    entryNodeId: source.entryNodeId,
    title: source.title,
    language: source.language,
    contentVersion: source.contentVersion,
    id: source.id,
    formatVersion: source.formatVersion,
    format: source.format,
  }, null, 2);
  const objectResult = await compileAuthoringGamePack(source);
  const jsonResult = await compileAuthoringGamePackJson(`\n${reorderedText}\n`);

  assert.deepEqual(jsonResult, objectResult);
  const serialized = [];
  for (let iteration = 0; iteration < 20; iteration += 1) {
    const result = await compileAuthoringGamePackJson(reorderedText);
    serialized.push(JSON.stringify(result));
  }
  assert.equal(new Set(serialized).size, 1);

  const concurrent = await Promise.all(
    Array.from({ length: 20 }, () =>
      compileAuthoringGamePackJson(reorderedText),
    ),
  );
  assert.equal(new Set(concurrent.map((result) => JSON.stringify(result))).size, 1);
});

test("normalizes negative zero in every scalar position before returning objects", async () => {
  const source = makeAuthoringPack();
  source.variables[1].initial = -0;
  source.nodes[0].actions[0].when.conditions[2].value = -0;
  source.nodes[0].actions[0].effects.unshift({
    op: "set",
    variableId: "count-value",
    value: -0,
  });

  const result = await compileAuthoringGamePack(source);
  assert.equal(result.ok, true);
  assert.equal(Object.is(result.runtimePack.variables[1].initial, -0), false);
  assert.equal(
    Object.is(result.runtimePack.nodes[0].actions[0].when.conditions[2].value, -0),
    false,
  );
  assert.equal(
    Object.is(result.runtimePack.nodes[0].actions[0].effects[0].value, -0),
    false,
  );
  assert.deepEqual(JSON.parse(result.canonicalJson), result.runtimePack);
});

test("equivalent Unicode escapes, exponents, whitespace, and key order compile identically", async () => {
  const source = makeAuthoringPack();
  const canonicalResult = await compileAuthoringGamePack(source);
  const equivalentText = JSON.stringify(source)
    .replace("Compiler conformance", "\\u0043ompiler conformance")
    .replace('"initial":0', '"initial":0e0');
  const equivalentResult = await compileAuthoringGamePackJson(
    ` \r\n ${equivalentText}\t`,
  );

  assert.deepEqual(equivalentResult, canonicalResult);
});

test("compiles lone UTF-16 code units through canonical escaped source text", async () => {
  const source = makeAuthoringPack();
  source.title = String.fromCharCode(0xd800);
  const objectResult = await compileAuthoringGamePack(source);
  const lowercaseText = JSON.stringify(source);
  const jsonResult = await compileAuthoringGamePackJson(lowercaseText);
  const uppercaseResult = await compileAuthoringGamePackJson(
    lowercaseText.replace("\\ud800", "\\uD800"),
  );

  assert.equal(objectResult.ok, true);
  assert.deepEqual(objectResult, jsonResult);
  assert.deepEqual(objectResult, uppercaseResult);
  assert.equal(objectResult.canonicalJson.includes("\\ud800"), true);
});

test("does not mutate input and deep-freezes every successful output object", async () => {
  const source = makeAuthoringPack();
  const before = deepClone(source);
  const result = await compileAuthoringGamePack(source);

  assert.deepEqual(source, before);
  assertDeepFrozen(result);
  assert.notEqual(result.runtimePack.entities, source.entities);
  assert.notEqual(result.runtimePack.variables, source.variables);
  source.title = "Changed after compilation";
  source.variables[2].allowedValues.push("mode-after-compile");
  assert.equal(result.runtimePack.title, before.title);
  assert.deepEqual(result.runtimePack.variables[2].allowedValues, [
    "mode-alpha",
    "mode-beta",
  ]);
  assert.throws(() => {
    result.runtimePack.nodes[0].title = "mutated";
  }, TypeError);
});

test("redacts missing and rejected Web Crypto operations", async () => {
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const cases = [
    undefined,
    {
      subtle: {
        async digest() {
          throw new Error("sensitive crypto rejection");
        },
      },
    },
  ];
  try {
    for (const cryptoValue of cases) {
      Object.defineProperty(globalThis, "crypto", {
        configurable: true,
        enumerable: true,
        value: cryptoValue,
      });
      await assert.rejects(
        compileAuthoringGamePack(makeAuthoringPack()),
        (error) => {
          assert.equal(error instanceof GamePackCompilerOperationalError, true);
          assert.equal(error.code, "PACK_COMPILER_INTERNAL_ERROR");
          assert.equal(error.message, "PACK_COMPILER_INTERNAL_ERROR");
          assert.equal(error.cause, undefined);
          assert.equal(String(error).includes("sensitive"), false);
          return true;
        },
      );
    }
  } finally {
    Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
  }
});

test("turns Runtime Validator rejection and faults into one operational error", async () => {
  const cases = [
    async () => ({
      reportVersion: 1,
      valid: false,
      diagnostics: [{ code: "dynamic invalid output" }],
    }),
    async () => {
      throw new Error("sensitive validator rejection");
    },
  ];
  for (const validateRuntimeJson of cases) {
    const isolatedCompiler = createGamePackCompiler({ validateRuntimeJson });
    await assert.rejects(
      isolatedCompiler.compileAuthoringGamePack(makeAuthoringPack()),
      (error) => {
        assert.equal(error instanceof GamePackCompilerOperationalError, true);
        assert.equal(error.code, "PACK_COMPILER_INTERNAL_ERROR");
        assert.equal(error.message, "PACK_COMPILER_INTERNAL_ERROR");
        assert.equal(error.cause, undefined);
        assert.equal(String(error).includes("sensitive"), false);
        assert.equal(String(error).includes("dynamic"), false);
        return true;
      },
    );
  }
});

test("descriptor-checks the exact Runtime Validator success report", async () => {
  let getterCalls = 0;
  const withGetter = {
    reportVersion: 1,
    diagnostics: [],
  };
  Object.defineProperty(withGetter, "valid", {
    enumerable: true,
    get() {
      getterCalls += 1;
      throw new Error("sensitive report getter");
    },
  });
  const malformedReports = [
    withGetter,
    { reportVersion: 1, valid: true, diagnostics: [], extra: true },
    { reportVersion: 1, valid: true, diagnostics: {} },
    { reportVersion: 1, valid: "true", diagnostics: [] },
    Object.assign(Object.create(null), {
      reportVersion: 1,
      valid: true,
      diagnostics: [],
    }),
  ];

  for (const report of malformedReports) {
    const isolatedCompiler = createGamePackCompiler({
      validateRuntimeJson: async () => report,
    });
    await assert.rejects(
      isolatedCompiler.compileAuthoringGamePack(makeAuthoringPack()),
      (error) =>
        error instanceof GamePackCompilerOperationalError &&
        error.code === "PACK_COMPILER_INTERNAL_ERROR" &&
        !String(error).includes("sensitive"),
    );
  }
  assert.equal(getterCalls, 0);
});

test("fails closed on unknown unions and missing references behind service seams", async () => {
  const bypassingCompiler = createGamePackCompiler({
    validateAuthoringJson() {
      return { reportVersion: 1, valid: true, diagnostics: [] };
    },
  });
  const mutators = [
    (pack) => {
      pack.variables[0].type = "future-variable";
    },
    (pack) => {
      pack.nodes[0].actions[0].when.op = "future-condition";
    },
    (pack) => {
      pack.nodes[0].actions[0].effects[0].op = "future-effect";
    },
    (pack) => {
      pack.nodes[0].actions[0].target.kind = "future-target";
    },
    (pack) => {
      pack.nodes[0].actions[0].target.id = "missing-node";
    },
  ];

  for (const mutate of mutators) {
    const source = makeAuthoringPack();
    mutate(source);
    await assert.rejects(
      bypassingCompiler.compileAuthoringGamePack(source),
      (error) =>
        error instanceof GamePackCompilerOperationalError &&
        error.code === "PACK_COMPILER_INTERNAL_ERROR",
    );
  }
});

test("rejects accessors without invoking them and redacts operational failures", async () => {
  let getterInvocations = 0;
  const source = makeAuthoringPack();
  Object.defineProperty(source, "summary", {
    enumerable: true,
    get() {
      getterInvocations += 1;
      throw new Error("sensitive getter value");
    },
  });
  const accessorResult = await compileAuthoringGamePack(source);
  assert.equal(accessorResult.ok, false);
  assert.equal(getterInvocations, 0);

  const trapped = new Proxy(makeAuthoringPack(), {
    ownKeys() {
      throw new Error("sensitive proxy value");
    },
  });
  await assert.rejects(
    compileAuthoringGamePack(trapped),
    (error) => {
      assert.equal(error instanceof GamePackCompilerOperationalError, true);
      assert.equal(error.name, "GamePackCompilerOperationalError");
      assert.equal(error.code, "PACK_COMPILER_INTERNAL_ERROR");
      assert.equal(error.message, "PACK_COMPILER_INTERNAL_ERROR");
      assert.equal(String(error).includes("sensitive"), false);
      return true;
    },
  );
});

test("runtime source stays browser-compatible and imports only public package roots", async () => {
  const source = (
    await Promise.all(
      ["compiler.mjs", "index.mjs"].map((fileName) =>
        readFile(new URL(`../src/${fileName}`, import.meta.url), "utf8"),
      ),
    )
  ).join("\n");
  assert.equal(/(?:node:|from\s+["'](?:\.\.\/){2}|process\.|Buffer\b)/u.test(source), false);
  const forbiddenCapabilities = [
    ["fet", "ch"],
    ["XML", "HttpRequest"],
    ["Web", "Socket"],
    ["Event", "Source"],
    ["local", "Storage"],
    ["session", "Storage"],
    ["indexed", "DB"],
  ].map((parts) => parts.join(""));
  for (const capability of forbiddenCapabilities) {
    assert.equal(source.includes(capability), false);
  }
  assert.equal(source.includes("game-pack-validator/src"), false);
  assert.equal(source.includes("runtime-pack-validator/src"), false);
  assert.equal(/(?:examples|last-train|metro|rail|mechanics-conformance)/iu.test(source), false);
});

test("package metadata contains only the three approved runtime dependencies", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  );
  assert.equal(packageJson.private, true);
  assert.equal(packageJson.license, "UNLICENSED");
  assert.deepEqual(packageJson.dependencies, {
    "@matrix-oasis/game-pack-validator": "0.1.0-r1",
    "@matrix-oasis/runtime-pack-contracts": "0.1.0-r3",
    "@matrix-oasis/runtime-pack-validator": "0.1.0-r3",
  });
});

test("declaration consumes authoritative Runtime types without mirroring them", async () => {
  const declaration = await readFile(
    new URL("../src/index.d.ts", import.meta.url),
    "utf8",
  );
  const contractsImport = declaration.match(
    /import\s+type\s*\{(?<names>[^}]*)\}\s*from\s*"@matrix-oasis\/runtime-pack-contracts";/u,
  );
  assert.ok(contractsImport?.groups?.names);
  assert.deepEqual(
    contractsImport.groups.names
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean)
      .sort(),
    ["RuntimeGamePack", "RuntimeGamePackReceipt"],
  );
  assert.equal(
    /export\s+(?:interface|type)\s+Runtime[A-Za-z0-9_]*/u.test(
      declaration,
    ),
    false,
  );
});

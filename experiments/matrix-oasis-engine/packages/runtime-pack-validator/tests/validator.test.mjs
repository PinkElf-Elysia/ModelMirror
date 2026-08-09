import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  CANONICAL_JSON_PROFILE,
  GAME_PACK_COMPILER_ID,
  GAME_PACK_COMPILER_VERSION,
  RUNTIME_GAME_PACK_FORMAT,
  RUNTIME_GAME_PACK_FORMAT_VERSION,
  RUNTIME_GAME_PACK_RECEIPT_FORMAT,
  RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import {
  RuntimeGamePackValidatorOperationalError,
  validateRuntimeGamePackJson,
} from "../src/index.mjs";
import { validateStructures } from "../src/structural-validator.mjs";

const HASH_A = "a".repeat(64);
const DEEP_INPUT_CHILD_PATH = fileURLToPath(
  new URL("./deep-input-child.mjs", import.meta.url),
);

function makePack() {
  return {
    format: RUNTIME_GAME_PACK_FORMAT,
    formatVersion: RUNTIME_GAME_PACK_FORMAT_VERSION,
    canonicalization: CANONICAL_JSON_PROFILE,
    source: {
      format: "matrix-oasis.authoring-game-pack",
      formatVersion: "0.1.0",
      id: "validator-fixture",
      contentVersion: "1",
      canonicalSha256: HASH_A,
    },
    language: "en",
    title: "Validator fixture",
    summary: null,
    entryNodeIndex: 0,
    entities: [
      { id: "operator", label: "Operator", description: null },
    ],
    variables: [
      { id: "enabled", type: "boolean", initial: false },
      { id: "count", type: "integer", initial: 0 },
      {
        id: "mode",
        type: "enum",
        allowedValues: ["open", "closed"],
        initial: "open",
      },
    ],
    cues: [{ id: "confirmed", channel: "ui", intent: "Confirm action" }],
    nodes: [
      {
        id: "start",
        title: "Start",
        text: null,
        entityIndexes: [0],
        entryCueIndexes: [0],
        actions: [
          {
            id: "finish",
            label: "Finish",
            entityIndexes: [0],
            when: { op: "eq", variableIndex: 0, value: false },
            effects: [
              { op: "set", variableIndex: 0, value: true },
              { op: "add", variableIndex: 1, value: 1 },
              { op: "emitCue", cueIndex: 0 },
            ],
            target: { kind: "ending", index: 0 },
          },
        ],
      },
    ],
    endings: [
      { id: "complete", title: "Complete", text: null, cueIndexes: [0] },
    ],
  };
}

function bytesToHex(bytes) {
  return [...bytes]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256(text) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return bytesToHex(new Uint8Array(digest));
}

async function makeReceipt(runtimeText) {
  return {
    format: RUNTIME_GAME_PACK_RECEIPT_FORMAT,
    formatVersion: RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION,
    canonicalization: CANONICAL_JSON_PROFILE,
    compiler: {
      id: GAME_PACK_COMPILER_ID,
      version: GAME_PACK_COMPILER_VERSION,
    },
    artifact: {
      format: RUNTIME_GAME_PACK_FORMAT,
      formatVersion: RUNTIME_GAME_PACK_FORMAT_VERSION,
      sha256: await sha256(runtimeText),
      byteLength: new TextEncoder().encode(runtimeText).byteLength,
    },
  };
}

async function makeDocuments(pack = makePack()) {
  const runtimeText = canonicalizeJsonValue(pack);
  const receipt = await makeReceipt(runtimeText);
  return {
    runtimeText,
    receipt,
    receiptText: canonicalizeJsonValue(receipt),
  };
}

function codes(report) {
  return report.diagnostics.map((diagnostic) => diagnostic.code);
}

function nestedArrayText(depth) {
  return `${"[".repeat(depth)}0${"]".repeat(depth)}`;
}

function nestedNotConditionText(depth) {
  return `${'{"op":"not","condition":'.repeat(depth)}` +
    '{"op":"eq","variableIndex":0,"value":false}' +
    "}".repeat(depth);
}

function appendRootPropertyText(documentText, propertyName, valueText) {
  assert.equal(documentText.at(-1), "}");
  return `${documentText.slice(0, -1)},${JSON.stringify(propertyName)}:${valueText}}`;
}

function deeplyNestedConditionRuntimeText(depth) {
  const pack = makePack();
  const shallowConditionText = canonicalizeJsonValue(
    pack.nodes[0].actions[0].when,
  );
  return canonicalizeJsonValue(pack).replace(
    shallowConditionText,
    nestedNotConditionText(depth),
  );
}

function assertStaticFrozenReport(report) {
  assert.equal(Object.isFrozen(report), true);
  assert.equal(Object.isFrozen(report.diagnostics), true);
  for (const diagnostic of report.diagnostics) {
    assert.equal(Object.isFrozen(diagnostic), true);
    assert.equal(diagnostic.message, diagnostic.code);
    if (diagnostic.location) {
      assert.equal(Object.isFrozen(diagnostic.location), true);
    }
  }
}

function assertOnlyEnumerableStringKeys(value, expectedKeys) {
  assert.deepEqual(Object.keys(value), expectedKeys);
  assert.deepEqual(Object.getOwnPropertyNames(value), expectedKeys);
  assert.deepEqual(Object.getOwnPropertySymbols(value), []);
  for (const key of expectedKeys) {
    assert.equal(
      Object.getOwnPropertyDescriptor(value, key).enumerable,
      true,
      key,
    );
  }
}

test("accepts a canonical Runtime Pack and matching canonical receipt", async () => {
  const { runtimeText, receiptText } = await makeDocuments();
  const first = await validateRuntimeGamePackJson(runtimeText, receiptText);

  assert.deepEqual(first, {
    reportVersion: 1,
    valid: true,
    diagnostics: [],
  });
  for (let index = 0; index < 20; index += 1) {
    assert.deepEqual(
      await validateRuntimeGamePackJson(runtimeText, receiptText),
      first,
    );
  }
  assertOnlyEnumerableStringKeys(first, [
    "reportVersion",
    "valid",
    "diagnostics",
  ]);
  assertStaticFrozenReport(first);
});

test("aggregates both documents at the parse gate and redacts unknown keys", async () => {
  const runtimeSecret = "runtime-secret-that-must-not-leak";
  const receiptSecret = "receipt-secret-that-must-not-leak";
  const runtimeText = `{${JSON.stringify(runtimeSecret)}:1,${JSON.stringify(runtimeSecret)}:2}`;
  const receiptText = `{${JSON.stringify(receiptSecret)}:1,${JSON.stringify(receiptSecret)}:2}`;

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);

  assert.equal(report.valid, false);
  assert.deepEqual(codes(report), [
    "RUNTIME_PACK_JSON_DUPLICATE_KEY",
    "RUNTIME_RECEIPT_JSON_DUPLICATE_KEY",
  ]);
  assert.equal(JSON.stringify(report).includes(runtimeSecret), false);
  assert.equal(JSON.stringify(report).includes(receiptSecret), false);
  assert.equal(report.diagnostics[0].path, "/runtimePack");
  assert.equal(report.diagnostics[0].relatedPath, "/runtimePack");
  assert.equal(report.diagnostics[1].path, "/receipt");
  assert.equal(report.diagnostics[1].relatedPath, "/receipt");
  assertOnlyEnumerableStringKeys(report.diagnostics[0], [
    "phase",
    "severity",
    "code",
    "path",
    "message",
    "relatedPath",
    "location",
  ]);
  assertOnlyEnumerableStringKeys(report.diagnostics[0].location, [
    "line",
    "column",
  ]);
  assertStaticFrozenReport(report);
});

test("uses exact safe pointers and locations for known duplicate keys", async () => {
  const runtimeText = '{\n  "format":"first",\n  "format":"second"\n}';
  const receiptText = '{\n  "artifact":{},\n  "artifact":{}\n}';

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);

  assert.deepEqual(report.diagnostics, [
    {
      phase: "parse",
      severity: "error",
      code: "RUNTIME_PACK_JSON_DUPLICATE_KEY",
      path: "/runtimePack/format",
      message: "RUNTIME_PACK_JSON_DUPLICATE_KEY",
      relatedPath: "/runtimePack/format",
      location: { line: 3, column: 3 },
    },
    {
      phase: "parse",
      severity: "error",
      code: "RUNTIME_RECEIPT_JSON_DUPLICATE_KEY",
      path: "/receipt/artifact",
      message: "RUNTIME_RECEIPT_JSON_DUPLICATE_KEY",
      relatedPath: "/receipt/artifact",
      location: { line: 3, column: 3 },
    },
  ]);
});

test("uses only container-local Runtime Pack schema keys in duplicate paths", async () => {
  const sensitiveKey = [
    "dynamic",
    "runtime",
    "position",
    String(Date.now()),
  ].join("-");
  const runtimeText = `{
    "op":"root-first",
    "op":"root-second",
    "source":{"target":"first","target":"second"},
    "nodes":[{"actions":[{"target":{"kind":"node","kind":"ending"}}]}],
    ${JSON.stringify(sensitiveKey)}:1,
    ${JSON.stringify(sensitiveKey)}:2
  }`;

  const report = await validateRuntimeGamePackJson(runtimeText, "{}");

  assert.deepEqual(
    report.diagnostics.map(({ path, relatedPath }) => ({ path, relatedPath })),
    [
      { path: "/runtimePack", relatedPath: "/runtimePack" },
      {
        path: "/runtimePack/source",
        relatedPath: "/runtimePack/source",
      },
      {
        path: "/runtimePack/nodes/0/actions/0/target/kind",
        relatedPath: "/runtimePack/nodes/0/actions/0/target/kind",
      },
      { path: "/runtimePack", relatedPath: "/runtimePack" },
    ],
  );
  const published = JSON.stringify(report);
  assert.equal(published.includes("/runtimePack/op"), false);
  assert.equal(published.includes("/runtimePack/source/target"), false);
  assert.equal(published.includes(sensitiveKey), false);
});

test("uses only container-local receipt schema keys in duplicate paths", async () => {
  const sensitiveKey = [
    "dynamic",
    "receipt",
    "position",
    String(Date.now()),
  ].join("-");
  const receiptText = `{
    "sha256":"root-first",
    "sha256":"root-second",
    "compiler":{"byteLength":1,"byteLength":2},
    "artifact":{"sha256":"${"a".repeat(64)}","sha256":"${"b".repeat(64)}"},
    ${JSON.stringify(sensitiveKey)}:1,
    ${JSON.stringify(sensitiveKey)}:2
  }`;

  const report = await validateRuntimeGamePackJson("{}", receiptText);

  assert.deepEqual(
    report.diagnostics.map(({ path, relatedPath }) => ({ path, relatedPath })),
    [
      { path: "/receipt", relatedPath: "/receipt" },
      { path: "/receipt/compiler", relatedPath: "/receipt/compiler" },
      {
        path: "/receipt/artifact/sha256",
        relatedPath: "/receipt/artifact/sha256",
      },
      { path: "/receipt", relatedPath: "/receipt" },
    ],
  );
  const published = JSON.stringify(report);
  assert.equal(published.includes("/receipt/sha256"), false);
  assert.equal(published.includes("/receipt/compiler/byteLength"), false);
  assert.equal(published.includes(sensitiveKey), false);
});

test("rejects non-string and malformed inputs without evaluating later gates", async () => {
  const nonString = await validateRuntimeGamePackJson(null, 42);
  assert.deepEqual(new Set(codes(nonString)), new Set([
    "RUNTIME_PACK_JSON_INPUT_TYPE",
    "RUNTIME_RECEIPT_JSON_INPUT_TYPE",
  ]));

  const malformed = await validateRuntimeGamePackJson("{", "[");
  assert.deepEqual(new Set(codes(malformed)), new Set([
    "RUNTIME_PACK_JSON_SYNTAX",
    "RUNTIME_RECEIPT_JSON_SYNTAX",
  ]));
  assert.ok(malformed.diagnostics.every((item) => item.phase === "parse"));
});

test("aggregates closed-schema failures and never coerces values", async () => {
  const runtime = makePack();
  runtime.entryNodeIndex = "0";
  runtime["runtime-private-value"] = "must-not-leak";
  const runtimeText = canonicalizeJsonValue(runtime);
  const receipt = await makeReceipt(runtimeText);
  receipt["receipt-private-value"] = "must-not-leak";

  const report = await validateRuntimeGamePackJson(
    runtimeText,
    canonicalizeJsonValue(receipt),
  );

  assert.ok(codes(report).includes("RUNTIME_PACK_SCHEMA_TYPE"));
  assert.ok(codes(report).includes("RUNTIME_PACK_SCHEMA_UNKNOWN_PROPERTY"));
  assert.ok(codes(report).includes("RUNTIME_RECEIPT_SCHEMA_UNKNOWN_PROPERTY"));
  assert.ok(report.diagnostics.every((item) => item.phase === "schema"));
  assert.equal(JSON.stringify(report).includes("private-value"), false);
  assert.equal(runtime.entryNodeIndex, "0");
});

test("orders all Runtime Pack schema diagnostics before receipt diagnostics", async () => {
  const report = await validateRuntimeGamePackJson("{}", "{}");
  assert.ok(report.diagnostics.length > 2);
  assert.ok(report.diagnostics.every((item) => item.phase === "schema"));

  const firstReceiptIndex = report.diagnostics.findIndex((item) =>
    item.path === "/receipt" || item.path.startsWith("/receipt/"),
  );
  assert.ok(firstReceiptIndex > 0);
  assert.ok(
    report.diagnostics
      .slice(0, firstReceiptIndex)
      .every((item) =>
        item.path === "/runtimePack" || item.path.startsWith("/runtimePack/"),
      ),
  );
  assert.ok(
    report.diagnostics
      .slice(firstReceiptIndex)
      .every((item) =>
        item.path === "/receipt" || item.path.startsWith("/receipt/"),
      ),
  );
});

test("returns a static parse report for a condition nested over 2000 levels", async () => {
  const runtimeText = deeplyNestedConditionRuntimeText(2_001);
  const receiptText = canonicalizeJsonValue(await makeReceipt(runtimeText));

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);

  assert.deepEqual(report, {
    reportVersion: 1,
    valid: false,
    diagnostics: [{
      phase: "parse",
      severity: "error",
      code: "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
      path: "/runtimePack",
      message: "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
    }],
  });
});

test("returns static ordered parse reports for generic 5000-level documents", async () => {
  const deepPropertyName = "private-runtime-depth-must-not-leak";
  const baseRuntimeText = canonicalizeJsonValue(makePack());
  const runtimeText = appendRootPropertyText(
    baseRuntimeText,
    deepPropertyName,
    nestedArrayText(5_001),
  );
  const receipt = await makeReceipt(runtimeText);
  const receiptText = appendRootPropertyText(
    canonicalizeJsonValue(receipt),
    "private-receipt-depth-must-not-leak",
    nestedArrayText(5_001),
  );

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);

  assert.deepEqual(codes(report), [
    "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
    "RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED",
  ]);
  assert.deepEqual(
    report.diagnostics.map((diagnostic) => diagnostic.path),
    ["/runtimePack", "/receipt"],
  );
  assert.equal(JSON.stringify(report).includes("private-runtime"), false);
  assert.equal(JSON.stringify(report).includes("private-receipt"), false);
});

test("returns a static receipt parse report for a 5000-level receipt value", async () => {
  const { runtimeText, receipt } = await makeDocuments();
  const receiptText = appendRootPropertyText(
    canonicalizeJsonValue(receipt),
    "receipt-only-private-depth",
    nestedArrayText(5_001),
  );

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);

  assert.deepEqual(codes(report), ["RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED"]);
  assert.equal(report.diagnostics[0].path, "/receipt");
  assert.equal(JSON.stringify(report).includes("receipt-only-private"), false);
});

test("stops at the global parse gate when only the Runtime Pack is too deep", async () => {
  const report = await validateRuntimeGamePackJson(
    deeplyNestedConditionRuntimeText(2_001),
    "{}",
  );

  assert.deepEqual(report.diagnostics, [{
    phase: "parse",
    severity: "error",
    code: "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
    path: "/runtimePack",
    message: "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
  }]);
});

test("stops at the global parse gate when only the receipt is too deep", async () => {
  const validRuntimeText = canonicalizeJsonValue(makePack());
  const validReceipt = await makeReceipt(validRuntimeText);
  const deepReceiptText = appendRootPropertyText(
    canonicalizeJsonValue(validReceipt),
    "private-deep-receipt",
    nestedArrayText(5_001),
  );

  const report = await validateRuntimeGamePackJson("{}", deepReceiptText);

  assert.deepEqual(report.diagnostics, [{
    phase: "parse",
    severity: "error",
    code: "RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED",
    path: "/receipt",
    message: "RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED",
  }]);
  assert.equal(JSON.stringify(report).includes("private-deep-receipt"), false);
});

test("does not count brackets or escaped quotes inside JSON strings", async () => {
  const payload = `${"{[".repeat(300)}escaped quote: \"; slash: \\;${"]}".repeat(300)}`;
  const report = await validateRuntimeGamePackJson(
    JSON.stringify({ payload }),
    JSON.stringify({ payload }),
  );

  assert.equal(codes(report).includes("RUNTIME_PACK_JSON_DEPTH_EXCEEDED"), false);
  assert.equal(
    codes(report).includes("RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED"),
    false,
  );
  assert.ok(report.diagnostics.every((diagnostic) => diagnostic.phase === "schema"));
});

test("allows exactly 256 raw container levels and rejects level 257", async () => {
  const boundaryReport = await validateRuntimeGamePackJson(
    nestedArrayText(256),
    "{}",
  );
  assert.equal(
    codes(boundaryReport).includes("RUNTIME_PACK_JSON_DEPTH_EXCEEDED"),
    false,
  );
  assert.ok(
    boundaryReport.diagnostics.every(
      (diagnostic) => diagnostic.phase === "schema",
    ),
  );

  const exceededReport = await validateRuntimeGamePackJson(
    nestedArrayText(257),
    "{}",
  );
  assert.deepEqual(exceededReport.diagnostics, [{
    phase: "parse",
    severity: "error",
    code: "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
    path: "/runtimePack",
    message: "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
  }]);
});

test("retains iterative parsed-value depth checks as defense in depth", async () => {
  let deepValue = 0;
  for (let index = 0; index < 300; index += 1) {
    deepValue = [deepValue];
  }
  const runtimePack = makePack();
  runtimePack.privateDepth = deepValue;
  const { receipt } = await makeDocuments();

  const runtimeDiagnostics = validateStructures(runtimePack, receipt);
  assert.deepEqual(
    runtimeDiagnostics.map(({ code, path }) => ({ code, path })),
    [{ code: "RUNTIME_PACK_SCHEMA_INVALID", path: "/runtimePack" }],
  );

  const receiptWithDepth = structuredClone(receipt);
  receiptWithDepth.privateDepth = deepValue;
  const receiptDiagnostics = validateStructures(makePack(), receiptWithDepth);
  assert.deepEqual(
    receiptDiagnostics.map(({ code, path }) => ({ code, path })),
    [{ code: "RUNTIME_RECEIPT_SCHEMA_INVALID", path: "/receipt" }],
  );
});

test("deep-input cases remain static in independent fresh processes", () => {
  const expected = new Map([
    [
      "runtime-generic",
      ["RUNTIME_PACK_JSON_DEPTH_EXCEEDED", "/runtimePack"],
    ],
    [
      "receipt-generic",
      ["RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED", "/receipt"],
    ],
    [
      "runtime-condition",
      ["RUNTIME_PACK_JSON_DEPTH_EXCEEDED", "/runtimePack"],
    ],
  ]);

  for (const [mode, [expectedCode, expectedPath]] of expected) {
    const child = spawnSync(process.execPath, [DEEP_INPUT_CHILD_PATH, mode], {
      encoding: "utf8",
      timeout: 30_000,
      windowsHide: true,
    });
    assert.equal(child.status, 0, `${mode}: ${child.stderr}`);
    assert.equal(child.stderr, "");
    const output = JSON.parse(child.stdout);
    assert.deepEqual(output, {
      reportVersion: 1,
      valid: false,
      diagnostics: [{
        phase: "parse",
        severity: "error",
        code: expectedCode,
        path: expectedPath,
        message: expectedCode,
      }],
    });
    assert.equal(child.stdout.includes("dynamic-private-sentinel"), false);
  }
});

test("reports duplicate identifiers and typed index failures", async () => {
  const pack = makePack();
  pack.variables[0].id = pack.entities[0].id;
  pack.nodes[0].actions.push(structuredClone(pack.nodes[0].actions[0]));
  pack.entryNodeIndex = 3;
  pack.nodes[0].entityIndexes = [4];
  pack.nodes[0].entryCueIndexes = [4];
  pack.nodes[0].actions[0].entityIndexes = [4];
  pack.nodes[0].actions[0].when.variableIndex = 9;
  pack.nodes[0].actions[0].effects[0].variableIndex = 9;
  pack.nodes[0].actions[0].effects[2].cueIndex = 9;
  pack.nodes[0].actions[0].target.index = 9;
  pack.endings[0].cueIndexes = [9];
  const { runtimeText, receiptText } = await makeDocuments(pack);

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);
  const actual = new Set(codes(report));
  for (const code of [
    "RUNTIME_PACK_TOP_LEVEL_ID_DUPLICATE",
    "RUNTIME_PACK_ACTION_ID_DUPLICATE",
    "RUNTIME_PACK_ENTRY_NODE_INDEX_INVALID",
    "RUNTIME_PACK_ENTITY_INDEX_INVALID",
    "RUNTIME_PACK_CUE_INDEX_INVALID",
    "RUNTIME_PACK_VARIABLE_INDEX_INVALID",
    "RUNTIME_PACK_TARGET_INDEX_INVALID",
  ]) {
    assert.ok(actual.has(code), code);
  }
  assert.ok(report.diagnostics.every((item) => item.phase === "semantic"));
});

test("validates enum membership and condition/effect variable types", async () => {
  const pack = makePack();
  pack.variables[2].initial = "undeclared";
  pack.nodes[0].actions = [
    {
      id: "invalid-values",
      label: "Invalid values",
      entityIndexes: [],
      when: {
        op: "all",
        conditions: [
          { op: "eq", variableIndex: 2, value: "undeclared" },
          { op: "eq", variableIndex: 0, value: "open" },
          { op: "lt", variableIndex: 2, value: 1 },
        ],
      },
      effects: [
        { op: "set", variableIndex: 2, value: "undeclared" },
        { op: "set", variableIndex: 0, value: "open" },
        { op: "add", variableIndex: 0, value: 1 },
      ],
      target: { kind: "ending", index: 0 },
    },
  ];
  const { runtimeText, receiptText } = await makeDocuments(pack);

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);
  const actual = new Set(codes(report));
  for (const code of [
    "RUNTIME_PACK_ENUM_INITIAL_NOT_ALLOWED",
    "RUNTIME_PACK_ENUM_VALUE_NOT_ALLOWED",
    "RUNTIME_PACK_CONDITION_VALUE_TYPE_MISMATCH",
    "RUNTIME_PACK_CONDITION_VARIABLE_TYPE_MISMATCH",
    "RUNTIME_PACK_EFFECT_VALUE_TYPE_MISMATCH",
    "RUNTIME_PACK_EFFECT_VARIABLE_TYPE_MISMATCH",
  ]) {
    assert.ok(actual.has(code), code);
  }
});

test("enforces condition depth 16", async () => {
  const pack = makePack();
  let condition = { op: "eq", variableIndex: 0, value: false };
  for (let index = 0; index < 16; index += 1) {
    condition = { op: "not", condition };
  }
  pack.nodes[0].actions[0].when = condition;
  const { runtimeText, receiptText } = await makeDocuments(pack);

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);
  assert.ok(codes(report).includes("RUNTIME_PACK_CONDITION_DEPTH_EXCEEDED"));

  const boundaryPack = makePack();
  let boundaryCondition = { op: "eq", variableIndex: 0, value: false };
  for (let index = 0; index < 15; index += 1) {
    boundaryCondition = { op: "not", condition: boundaryCondition };
  }
  boundaryPack.nodes[0].actions[0].when = boundaryCondition;
  const boundaryDocuments = await makeDocuments(boundaryPack);
  assert.equal(
    (
      await validateRuntimeGamePackJson(
        boundaryDocuments.runtimeText,
        boundaryDocuments.receiptText,
      )
    ).valid,
    true,
  );

  const semanticPack = makePack();
  let semanticCondition = { op: "eq", variableIndex: 0, value: false };
  for (let index = 0; index < 128; index += 1) {
    semanticCondition = { op: "not", condition: semanticCondition };
  }
  semanticPack.nodes[0].actions[0].when = semanticCondition;
  const semanticDocuments = await makeDocuments(semanticPack);
  const semanticReport = await validateRuntimeGamePackJson(
    semanticDocuments.runtimeText,
    semanticDocuments.receiptText,
  );
  assert.deepEqual(codes(semanticReport), [
    "RUNTIME_PACK_CONDITION_DEPTH_EXCEEDED",
  ]);
  assert.equal(semanticReport.diagnostics[0].phase, "semantic");
});

test("reports unreachable nodes and reachable nodes without an ending path", async () => {
  const pack = makePack();
  pack.nodes = [
    {
      id: "loop",
      title: "Loop",
      text: null,
      entityIndexes: [],
      entryCueIndexes: [],
      actions: [{
        id: "again",
        label: "Again",
        entityIndexes: [],
        when: null,
        effects: [],
        target: { kind: "node", index: 0 },
      }],
    },
    {
      id: "isolated",
      title: "Isolated",
      text: null,
      entityIndexes: [],
      entryCueIndexes: [],
      actions: [{
        id: "finish",
        label: "Finish",
        entityIndexes: [],
        when: null,
        effects: [],
        target: { kind: "ending", index: 0 },
      }],
    },
  ];
  const { runtimeText, receiptText } = await makeDocuments(pack);

  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);
  assert.ok(codes(report).includes("RUNTIME_PACK_NODE_NO_ENDING_PATH"));
  assert.ok(codes(report).includes("RUNTIME_PACK_NODE_UNREACHABLE"));
});

test("rejects non-canonical Runtime Pack and receipt text independently", async () => {
  const canonicalRuntimeText = canonicalizeJsonValue(makePack());
  const nonCanonicalRuntimeText = ` ${canonicalRuntimeText}`;
  const receiptForNonCanonical = await makeReceipt(nonCanonicalRuntimeText);
  const runtimeReport = await validateRuntimeGamePackJson(
    nonCanonicalRuntimeText,
    canonicalizeJsonValue(receiptForNonCanonical),
  );
  assert.deepEqual(codes(runtimeReport), ["RUNTIME_PACK_JSON_NON_CANONICAL"]);

  const receipt = await makeReceipt(canonicalRuntimeText);
  const nonCanonicalReceiptText = ` ${canonicalizeJsonValue(receipt)}`;
  const receiptReport = await validateRuntimeGamePackJson(
    canonicalRuntimeText,
    nonCanonicalReceiptText,
  );
  assert.deepEqual(codes(receiptReport), ["RUNTIME_RECEIPT_JSON_NON_CANONICAL"]);
});

test("rejects equivalent Unicode escapes and non-canonical zero spellings", async () => {
  const canonicalDocuments = await makeDocuments();
  assert.equal(
    (
      await validateRuntimeGamePackJson(
        canonicalDocuments.runtimeText,
        canonicalDocuments.receiptText,
      )
    ).valid,
    true,
  );

  const integerVariableText =
    '"id":"count","initial":0,"type":"integer"';
  const cases = [
    {
      label: "Unicode escape",
      runtimeText: canonicalDocuments.runtimeText.replace(
        '"Validator fixture"',
        '"\\u0056alidator fixture"',
      ),
    },
    {
      label: "negative zero",
      runtimeText: canonicalDocuments.runtimeText.replace(
        integerVariableText,
        '"id":"count","initial":-0,"type":"integer"',
      ),
    },
    {
      label: "exponent zero",
      runtimeText: canonicalDocuments.runtimeText.replace(
        integerVariableText,
        '"id":"count","initial":0e0,"type":"integer"',
      ),
    },
  ];

  for (const entry of cases) {
    assert.notEqual(
      entry.runtimeText,
      canonicalDocuments.runtimeText,
      entry.label,
    );
    const receiptText = canonicalizeJsonValue(
      await makeReceipt(entry.runtimeText),
    );
    const report = await validateRuntimeGamePackJson(
      entry.runtimeText,
      receiptText,
    );
    assert.deepEqual(
      report.diagnostics,
      [{
        phase: "integrity",
        severity: "error",
        code: "RUNTIME_PACK_JSON_NON_CANONICAL",
        path: "/runtimePack",
        message: "RUNTIME_PACK_JSON_NON_CANONICAL",
      }],
      entry.label,
    );
  }
});

test("isolates raw and uppercase lone surrogates from canonical escaped text", async () => {
  const { runtimeText } = await makeDocuments();
  const replacementCharacter = String.fromCodePoint(0xfffd);
  const replacementCanonicalText = runtimeText.replace(
    '"Validator fixture"',
    JSON.stringify(replacementCharacter),
  );
  const replacementReceipt = await makeReceipt(replacementCanonicalText);
  const replacementReport = await validateRuntimeGamePackJson(
    replacementCanonicalText,
    canonicalizeJsonValue(replacementReceipt),
  );
  assert.deepEqual(replacementReport, {
    reportVersion: 1,
    valid: true,
    diagnostics: [],
  });

  const cases = [
    { label: "high", codeUnit: 0xd800 },
    { label: "low", codeUnit: 0xdfff },
  ];
  for (const { label, codeUnit } of cases) {
    const loneCodeUnit = String.fromCharCode(codeUnit);
    const lowercaseEscape = `\\u${codeUnit.toString(16)}`;
    const uppercaseEscape = `\\u${codeUnit.toString(16).toUpperCase()}`;
    const rawText = runtimeText.replace(
      '"Validator fixture"',
      `"${loneCodeUnit}"`,
    );
    const escapedCanonicalText = runtimeText.replace(
      '"Validator fixture"',
      `"${lowercaseEscape}"`,
    );
    const uppercaseEscapeText = runtimeText.replace(
      '"Validator fixture"',
      `"${uppercaseEscape}"`,
    );

    const rawBytes = new TextEncoder().encode(rawText);
    const replacementBytes = new TextEncoder().encode(
      replacementCanonicalText,
    );
    assert.deepEqual(rawBytes, replacementBytes, label);
    assert.equal(await sha256(rawText), await sha256(replacementCanonicalText));

    const rawReceipt = await makeReceipt(rawText);
    assert.equal(
      rawReceipt.artifact.sha256,
      replacementReceipt.artifact.sha256,
      label,
    );
    assert.deepEqual(
      codes(
        await validateRuntimeGamePackJson(
          rawText,
          canonicalizeJsonValue(rawReceipt),
        ),
      ),
      ["RUNTIME_PACK_JSON_NON_CANONICAL"],
      `${label} raw`,
    );

    const escapedReceipt = await makeReceipt(escapedCanonicalText);
    assert.notEqual(
      escapedReceipt.artifact.sha256,
      replacementReceipt.artifact.sha256,
      label,
    );
    assert.deepEqual(
      await validateRuntimeGamePackJson(
        escapedCanonicalText,
        canonicalizeJsonValue(escapedReceipt),
      ),
      { reportVersion: 1, valid: true, diagnostics: [] },
      `${label} lowercase escape`,
    );

    assert.deepEqual(
      codes(
        await validateRuntimeGamePackJson(
          uppercaseEscapeText,
          canonicalizeJsonValue(await makeReceipt(uppercaseEscapeText)),
        ),
      ),
      ["RUNTIME_PACK_JSON_NON_CANONICAL"],
      `${label} uppercase escape`,
    );
  }
});

test("checks UTF-8 artifact byte length and SHA-256", async () => {
  const { runtimeText, receipt } = await makeDocuments();
  receipt.artifact.byteLength += 1;
  receipt.artifact.sha256 = "0".repeat(64);

  const report = await validateRuntimeGamePackJson(
    runtimeText,
    canonicalizeJsonValue(receipt),
  );
  assert.deepEqual(new Set(codes(report)), new Set([
    "RUNTIME_RECEIPT_ARTIFACT_BYTE_LENGTH_MISMATCH",
    "RUNTIME_RECEIPT_ARTIFACT_SHA256_MISMATCH",
  ]));
  assert.ok(report.diagnostics.every((item) => item.phase === "integrity"));

  const unicodePack = makePack();
  unicodePack.title = "多字节验证";
  const unicodeDocuments = await makeDocuments(unicodePack);
  assert.ok(
    new TextEncoder().encode(unicodeDocuments.runtimeText).byteLength >
      unicodeDocuments.runtimeText.length,
  );
  assert.equal(
    (
      await validateRuntimeGamePackJson(
        unicodeDocuments.runtimeText,
        unicodeDocuments.receiptText,
      )
    ).valid,
    true,
  );
});

test("publishes only the documented API and fixed operational error", async () => {
  const namespace = await import("../src/index.mjs");
  assert.deepEqual(Object.keys(namespace).sort(), [
    "RuntimeGamePackValidatorOperationalError",
    "validateRuntimeGamePackJson",
  ]);

  const error = new RuntimeGamePackValidatorOperationalError();
  assert.equal(error.name, "RuntimeGamePackValidatorOperationalError");
  assert.equal(error.code, "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR");
  assert.equal(error.message, "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR");
});

test("redacts an unavailable Web Crypto implementation as an operational error", async () => {
  const { runtimeText, receiptText } = await makeDocuments();
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      enumerable: true,
      value: undefined,
    });
    await assert.rejects(
      validateRuntimeGamePackJson(runtimeText, receiptText),
      (error) =>
        error instanceof RuntimeGamePackValidatorOperationalError &&
        error.code === "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR" &&
        error.message === "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR",
    );
  } finally {
    Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
  }
});

test("redacts a rejected Web Crypto digest as a fixed operational error", async () => {
  const { runtimeText, receiptText } = await makeDocuments();
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const sensitiveSentinel = [
    "dynamic",
    "crypto",
    "digest",
    "private",
    String(Date.now()),
  ].join("-");

  try {
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      enumerable: true,
      value: {
        subtle: {
          async digest() {
            throw new Error(sensitiveSentinel);
          },
        },
      },
    });

    await assert.rejects(
      validateRuntimeGamePackJson(runtimeText, receiptText),
      (error) => {
        const visibleErrorSurface = Reflect.ownKeys(error)
          .map((key) => String(error[key]))
          .join("\n");
        assert.equal(
          error instanceof RuntimeGamePackValidatorOperationalError,
          true,
        );
        assert.equal(error.name, "RuntimeGamePackValidatorOperationalError");
        assert.equal(error.code, "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR");
        assert.equal(error.message, "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR");
        assert.equal(error.cause, undefined);
        assert.equal(visibleErrorSurface.includes(sensitiveSentinel), false);
        assert.equal(String(error).includes(sensitiveSentinel), false);
        return true;
      },
    );
  } finally {
    Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
  }
});

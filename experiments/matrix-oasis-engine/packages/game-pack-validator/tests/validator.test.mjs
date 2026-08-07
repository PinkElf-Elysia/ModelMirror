import assert from "node:assert/strict";
import test from "node:test";
import {
  AuthoringGamePackOperationalError,
  validateAuthoringGamePack,
  validateAuthoringGamePackJson,
} from "../src/index.mjs";

function makeValidPack() {
  return {
    format: "matrix-oasis.authoring-game-pack",
    formatVersion: "0.1.0",
    id: "validation-pack",
    contentVersion: "1",
    language: "en",
    title: "Validation Pack",
    entryNodeId: "node-start",
    entities: [{ id: "entity-alpha", label: "Entity Alpha" }],
    variables: [
      { id: "flag-ready", type: "boolean", initial: false },
      { id: "count-step", type: "integer", initial: 0 },
      {
        id: "mode-state",
        type: "enum",
        allowedValues: ["idle", "active"],
        initial: "idle",
      },
    ],
    cues: [{ id: "cue-enter", channel: "ui", intent: "Show state" }],
    nodes: [
      {
        id: "node-start",
        title: "Start",
        entityIds: ["entity-alpha"],
        entryCueIds: ["cue-enter"],
        actions: [
          {
            id: "continue",
            label: "Continue",
            entityIds: ["entity-alpha"],
            when: {
              op: "all",
              conditions: [
                { op: "eq", variableId: "flag-ready", value: false },
                { op: "lt", variableId: "count-step", value: 2 },
                { op: "eq", variableId: "mode-state", value: "idle" },
              ],
            },
            effects: [
              { op: "set", variableId: "flag-ready", value: true },
              { op: "add", variableId: "count-step", value: 1 },
              { op: "set", variableId: "mode-state", value: "active" },
              { op: "emitCue", cueId: "cue-enter" },
            ],
            target: { kind: "node", id: "node-finish" },
          },
        ],
      },
      {
        id: "node-finish",
        title: "Finish",
        entityIds: [],
        entryCueIds: [],
        actions: [
          {
            id: "complete",
            label: "Complete",
            effects: [],
            target: { kind: "ending", id: "ending-complete" },
          },
        ],
      },
    ],
    endings: [
      {
        id: "ending-complete",
        title: "Complete",
        cueIds: ["cue-enter"],
      },
    ],
  };
}

function findDiagnostic(report, code, path) {
  return report.diagnostics.find(
    (diagnostic) =>
      diagnostic.code === code && (path === undefined || diagnostic.path === path),
  );
}

function nestedCondition(depth) {
  let condition = { op: "eq", variableId: "flag-ready", value: false };
  for (let level = 1; level < depth; level += 1) {
    condition = { op: "not", condition };
  }
  return condition;
}

test("accepts the neutral contract fixture through both synchronous APIs", () => {
  const pack = makeValidPack();
  assert.deepEqual(validateAuthoringGamePack(pack), {
    reportVersion: 1,
    valid: true,
    diagnostics: [],
  });
  assert.deepEqual(validateAuthoringGamePackJson(JSON.stringify(pack)), {
    reportVersion: 1,
    valid: true,
    diagnostics: [],
  });
});

test("does not return a pack or mutate the caller value", () => {
  const pack = makeValidPack();
  const before = JSON.stringify(pack);
  const report = validateAuthoringGamePack(pack);
  assert.equal(Object.hasOwn(report, "pack"), false);
  assert.equal(JSON.stringify(pack), before);
});

test("rejects comments, trailing commas, empty content, and non-string text", () => {
  const validText = JSON.stringify(makeValidPack(), null, 2);
  for (const text of [
    `// comment\n${validText}`,
    validText.replace(/\n}$/, ",\n}"),
    "",
  ]) {
    const report = validateAuthoringGamePackJson(text);
    assert.equal(report.valid, false);
    assert.ok(findDiagnostic(report, "PACK_JSON_SYNTAX"));
  }
  assert.ok(
    findDiagnostic(validateAuthoringGamePackJson(null), "PACK_JSON_INPUT_TYPE"),
  );
});

test("rejects duplicate JSON keys at the second key location", () => {
  const report = validateAuthoringGamePackJson(
    '{\n  "format": "first",\n  "format": "second"\n}',
  );
  const diagnostic = findDiagnostic(report, "PACK_JSON_DUPLICATE_KEY", "/format");
  assert.ok(diagnostic);
  assert.equal(diagnostic.relatedPath, "/format");
  assert.deepEqual(diagnostic.location, { line: 3, column: 3 });
});

test("keeps the frozen diagnostic serialization field order", () => {
  const pack = makeValidPack();
  pack.entryNodeId = "missing-node";
  const [diagnostic] = validateAuthoringGamePack(pack).diagnostics;
  assert.deepEqual(Object.keys(diagnostic), [
    "phase",
    "severity",
    "code",
    "path",
    "message",
  ]);

  const duplicate = validateAuthoringGamePackJson(
    '{\n  "format": "first",\n  "format": "second"\n}',
  ).diagnostics[0];
  assert.deepEqual(Object.keys(duplicate), [
    "phase",
    "severity",
    "code",
    "path",
    "message",
    "relatedPath",
    "location",
  ]);
});

test("publishes plain diagnostics without hidden symbol or non-enumerable keys", () => {
  const pack = makeValidPack();
  pack.entryNodeId = "missing-node";
  const report = validateAuthoringGamePack(pack);
  const [diagnostic] = report.diagnostics;
  assert.deepEqual(Reflect.ownKeys(report), [
    "reportVersion",
    "valid",
    "diagnostics",
  ]);
  assert.deepEqual(Reflect.ownKeys(diagnostic), [
    "phase",
    "severity",
    "code",
    "path",
    "message",
  ]);
  for (const descriptor of Object.values(
    Object.getOwnPropertyDescriptors(diagnostic),
  )) {
    assert.equal(descriptor.enumerable, true);
  }

  const duplicate = validateAuthoringGamePackJson(
    '{\n  "format": "first",\n  "format": "second"\n}',
  ).diagnostics[0];
  assert.deepEqual(Reflect.ownKeys(duplicate), [
    "phase",
    "severity",
    "code",
    "path",
    "message",
    "relatedPath",
    "location",
  ]);
  assert.deepEqual(Reflect.ownKeys(duplicate.location), ["line", "column"]);
  for (const descriptor of Object.values(
    Object.getOwnPropertyDescriptors(duplicate),
  )) {
    assert.equal(descriptor.enumerable, true);
  }
});

test("uses Ajv without coercion, defaults, or property removal", () => {
  const pack = makeValidPack();
  pack.formatVersion = 1;
  pack.unapproved = true;
  const report = validateAuthoringGamePack(pack);
  assert.equal(report.valid, false);
  assert.ok(findDiagnostic(report, "PACK_SCHEMA_TYPE", "/formatVersion"));
  assert.ok(findDiagnostic(report, "PACK_SCHEMA_UNKNOWN_PROPERTY", ""));
  assert.equal(pack.formatVersion, 1);
  assert.equal(pack.unapproved, true);
});

test("requires every top-level identifier to be unique across collections", () => {
  const pack = makeValidPack();
  pack.variables[0].id = pack.entities[0].id;
  const diagnostic = findDiagnostic(
    validateAuthoringGamePack(pack),
    "PACK_TOP_LEVEL_ID_DUPLICATE",
    "/variables/0/id",
  );
  assert.ok(diagnostic);
  assert.equal(diagnostic.relatedPath, "/entities/0/id");
});

test("requires action identifiers to be unique only inside their node", () => {
  const pack = makeValidPack();
  pack.nodes[0].actions.push(structuredClone(pack.nodes[0].actions[0]));
  const diagnostic = findDiagnostic(
    validateAuthoringGamePack(pack),
    "PACK_ACTION_ID_DUPLICATE",
    "/nodes/0/actions/1/id",
  );
  assert.ok(diagnostic);
  assert.equal(diagnostic.relatedPath, "/nodes/0/actions/0/id");

  const valid = makeValidPack();
  valid.nodes[1].actions[0].id = valid.nodes[0].actions[0].id;
  assert.equal(validateAuthoringGamePack(valid).valid, true);
});

test("validates entity references on nodes and actions", () => {
  const pack = makeValidPack();
  pack.nodes[0].entityIds[0] = "missing-entity";
  pack.nodes[0].actions[0].entityIds[0] = "another-missing-entity";
  const report = validateAuthoringGamePack(pack);
  assert.ok(findDiagnostic(report, "PACK_ENTITY_REFERENCE_UNKNOWN", "/nodes/0/entityIds/0"));
  assert.ok(
    findDiagnostic(
      report,
      "PACK_ENTITY_REFERENCE_UNKNOWN",
      "/nodes/0/actions/0/entityIds/0",
    ),
  );
});

test("validates cue references on nodes, effects, and endings", () => {
  const pack = makeValidPack();
  pack.nodes[0].entryCueIds[0] = "missing-entry-cue";
  pack.nodes[0].actions[0].effects[3].cueId = "missing-effect-cue";
  pack.endings[0].cueIds[0] = "missing-ending-cue";
  const report = validateAuthoringGamePack(pack);
  assert.ok(findDiagnostic(report, "PACK_CUE_REFERENCE_UNKNOWN", "/nodes/0/entryCueIds/0"));
  assert.ok(
    findDiagnostic(
      report,
      "PACK_CUE_REFERENCE_UNKNOWN",
      "/nodes/0/actions/0/effects/3/cueId",
    ),
  );
  assert.ok(findDiagnostic(report, "PACK_CUE_REFERENCE_UNKNOWN", "/endings/0/cueIds/0"));
});

test("validates condition and effect variable references", () => {
  const pack = makeValidPack();
  pack.nodes[0].actions[0].when.conditions[0].variableId = "missing-condition-variable";
  pack.nodes[0].actions[0].effects[0].variableId = "missing-effect-variable";
  const report = validateAuthoringGamePack(pack);
  assert.ok(
    findDiagnostic(
      report,
      "PACK_VARIABLE_REFERENCE_UNKNOWN",
      "/nodes/0/actions/0/when/conditions/0/variableId",
    ),
  );
  assert.ok(
    findDiagnostic(
      report,
      "PACK_VARIABLE_REFERENCE_UNKNOWN",
      "/nodes/0/actions/0/effects/0/variableId",
    ),
  );
});

test("validates enum initial, condition, and set-effect values", () => {
  const pack = makeValidPack();
  pack.variables[2].initial = "undeclared";
  pack.nodes[0].actions[0].when.conditions[2].value = "other";
  pack.nodes[0].actions[0].effects[2].value = "unknown";
  const report = validateAuthoringGamePack(pack);
  assert.ok(findDiagnostic(report, "PACK_ENUM_INITIAL_NOT_ALLOWED", "/variables/2/initial"));
  assert.ok(
    findDiagnostic(
      report,
      "PACK_ENUM_VALUE_NOT_ALLOWED",
      "/nodes/0/actions/0/when/conditions/2/value",
    ),
  );
  assert.ok(
    findDiagnostic(
      report,
      "PACK_ENUM_VALUE_NOT_ALLOWED",
      "/nodes/0/actions/0/effects/2/value",
    ),
  );
});

test("validates variable types used by comparisons and effects", () => {
  const pack = makeValidPack();
  pack.nodes[0].actions[0].when.conditions[1].variableId = "flag-ready";
  pack.nodes[0].actions[0].effects[1].variableId = "flag-ready";
  pack.nodes[0].actions[0].effects[0].value = "idle";
  const report = validateAuthoringGamePack(pack);
  assert.ok(findDiagnostic(report, "PACK_CONDITION_VARIABLE_TYPE_MISMATCH"));
  assert.ok(findDiagnostic(report, "PACK_EFFECT_VARIABLE_TYPE_MISMATCH"));
  assert.ok(findDiagnostic(report, "PACK_EFFECT_VALUE_TYPE_MISMATCH"));
});

test("accepts condition depth 16 and rejects depth 17 with root depth 1", () => {
  const accepted = makeValidPack();
  accepted.nodes[0].actions[0].when = nestedCondition(16);
  assert.equal(validateAuthoringGamePack(accepted).valid, true);

  const rejected = makeValidPack();
  rejected.nodes[0].actions[0].when = nestedCondition(17);
  assert.ok(findDiagnostic(validateAuthoringGamePack(rejected), "PACK_CONDITION_DEPTH_EXCEEDED"));
});

test("validates entry and typed action targets before graph analysis", () => {
  const pack = makeValidPack();
  pack.entryNodeId = "missing-entry";
  pack.nodes[0].actions[0].target.id = "missing-target";
  const report = validateAuthoringGamePack(pack);
  assert.ok(findDiagnostic(report, "PACK_ENTRY_NODE_UNKNOWN", "/entryNodeId"));
  assert.ok(
    findDiagnostic(
      report,
      "PACK_TARGET_REFERENCE_UNKNOWN",
      "/nodes/0/actions/0/target/id",
    ),
  );
  assert.equal(findDiagnostic(report, "PACK_NODE_UNREACHABLE"), undefined);
  assert.equal(findDiagnostic(report, "PACK_NODE_NO_ENDING_PATH"), undefined);
});

test("reports unreachable nodes without adding a no-ending-path cascade", () => {
  const pack = makeValidPack();
  pack.nodes.push({
    id: "node-isolated",
    title: "Isolated",
    entityIds: [],
    entryCueIds: [],
    actions: [
      {
        id: "leave",
        label: "Leave",
        effects: [],
        target: { kind: "ending", id: "ending-complete" },
      },
    ],
  });
  const report = validateAuthoringGamePack(pack);
  assert.ok(findDiagnostic(report, "PACK_NODE_UNREACHABLE", "/nodes/2/id"));
  assert.equal(findDiagnostic(report, "PACK_NODE_NO_ENDING_PATH", "/nodes/2/id"), undefined);
});

test("allows cycles with an exit and rejects reachable closed cycles", () => {
  const withExit = makeValidPack();
  withExit.nodes[1].actions.unshift({
    id: "loop",
    label: "Loop",
    effects: [],
    target: { kind: "node", id: "node-start" },
  });
  assert.equal(validateAuthoringGamePack(withExit).valid, true);

  const closed = makeValidPack();
  closed.nodes[1].actions = [
    {
      id: "loop",
      label: "Loop",
      effects: [],
      target: { kind: "node", id: "node-start" },
    },
  ];
  const report = validateAuthoringGamePack(closed);
  assert.ok(findDiagnostic(report, "PACK_NODE_NO_ENDING_PATH", "/nodes/0/id"));
  assert.ok(findDiagnostic(report, "PACK_NODE_NO_ENDING_PATH", "/nodes/1/id"));
});

test("produces byte-stable diagnostics and never includes input values", () => {
  const pack = makeValidPack();
  const sensitive = ["C", ":", "\\", "private", "\\", "sentinel-value"].join("");
  pack.unapproved = sensitive;
  pack.entryNodeId = "missing-entry";
  const reports = Array.from({ length: 20 }, () =>
    JSON.stringify(validateAuthoringGamePack(pack)),
  );
  assert.equal(new Set(reports).size, 1);
  assert.equal(reports[0].includes(sensitive), false);
  assert.equal(reports[0].includes("schemaPath"), false);
  assert.equal(reports[0].includes("must "), false);
});

test("redacts arbitrary additional-property and duplicate-key names", () => {
  const sensitiveKey = ["sk", "-", "property", "-", "x".repeat(24)].join("");

  const rootPack = makeValidPack();
  rootPack[sensitiveKey] = true;
  const rootReport = validateAuthoringGamePack(rootPack);
  const rootDiagnostic = findDiagnostic(
    rootReport,
    "PACK_SCHEMA_UNKNOWN_PROPERTY",
    "",
  );
  assert.ok(rootDiagnostic);
  assert.equal(JSON.stringify(rootReport).includes(sensitiveKey), false);

  const nestedPack = makeValidPack();
  nestedPack.nodes[0][sensitiveKey] = true;
  const nestedReport = validateAuthoringGamePack(nestedPack);
  const nestedDiagnostic = findDiagnostic(
    nestedReport,
    "PACK_SCHEMA_UNKNOWN_PROPERTY",
    "/nodes/0",
  );
  assert.ok(nestedDiagnostic);
  assert.equal(JSON.stringify(nestedReport).includes(sensitiveKey), false);

  const duplicateText = `{\n  ${JSON.stringify(sensitiveKey)}: true,\n  ${JSON.stringify(sensitiveKey)}: false\n}`;
  const duplicateReport = validateAuthoringGamePackJson(duplicateText);
  const duplicateDiagnostic = findDiagnostic(
    duplicateReport,
    "PACK_JSON_DUPLICATE_KEY",
    "",
  );
  assert.ok(duplicateDiagnostic);
  assert.equal(duplicateDiagnostic.relatedPath, "");
  assert.equal(JSON.stringify(duplicateReport).includes(sensitiveKey), false);
});

test("fails closed for cyclic and otherwise non-JSON JavaScript values", () => {
  const cyclic = makeValidPack();
  cyclic.self = cyclic;
  assert.ok(findDiagnostic(validateAuthoringGamePack(cyclic), "PACK_SCHEMA_NON_JSON_VALUE"));
  assert.ok(findDiagnostic(validateAuthoringGamePack(undefined), "PACK_SCHEMA_NON_JSON_VALUE"));
});

test("separates internal operational failures from invalid content reports", () => {
  const hostile = new Proxy(
    {},
    {
      ownKeys() {
        throw new Error("untrusted detail");
      },
    },
  );
  assert.throws(
    () => validateAuthoringGamePack(hostile),
    (error) => {
      assert.equal(error instanceof AuthoringGamePackOperationalError, true);
      assert.equal(error.code, "PACK_VALIDATOR_INTERNAL_ERROR");
      assert.equal(error.message.includes("untrusted detail"), false);
      return true;
    },
  );
});

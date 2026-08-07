import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  validateAuthoringGamePack,
  validateAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-validator";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");
const neutralExampleName = "mechanics-conformance.authoring-game-pack.json";
const expectedExamples = Object.freeze([
  Object.freeze({
    name: "last-train-r1.authoring-game-pack.json",
    sha256: "c98b277d8e960404658f530eeb11ccee5faec2829032711ca02be3fdd827bf98",
  }),
  Object.freeze({
    name: neutralExampleName,
    sha256: "55896eaa631f2b563df163f77002924e4e6ea1d3a9d421dc383e777c172aa119",
  }),
]);

const exampleBytes = new Map();
for (const example of expectedExamples) {
  exampleBytes.set(example.name, await readFile(path.join(examplesRoot, example.name)));
}
const neutralPack = JSON.parse(exampleBytes.get(neutralExampleName).toString("utf8"));

function cloneNeutralPack() {
  return structuredClone(neutralPack);
}

function assertDiagnostic(report, code, pointer) {
  assert.equal(report.valid, false);
  assert.ok(
    report.diagnostics.some(
      (diagnostic) => diagnostic.code === code && diagnostic.path === pointer,
    ),
    `Expected ${code} at ${pointer}; received ${JSON.stringify(report.diagnostics)}`,
  );
}

function assertOnlyDiagnostic(pack, code, pointer) {
  const report = validateAuthoringGamePack(pack);
  assertDiagnostic(report, code, pointer);
  assert.equal(report.diagnostics.length, 1, JSON.stringify(report.diagnostics));
  assert.deepEqual(
    Object.keys(report.diagnostics[0]).sort(),
    ["code", "message", "path", "phase", "severity"].sort(),
  );
}

function nestedNotCondition(depth) {
  let condition = { op: "eq", variableId: "flag-active", value: true };
  for (let currentDepth = 1; currentDepth < depth; currentDepth += 1) {
    condition = { op: "not", condition };
  }
  return condition;
}

async function collectSourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await collectSourceFiles(entryPath)));
    } else if (/\.(?:d\.ts|json|mjs)$/.test(entry.name)) {
      files.push(entryPath);
    }
  }
  return files;
}

test("committed examples are the exact byte-locked fixtures and both validate", async () => {
  const names = (await readdir(examplesRoot))
    .filter((name) => name.endsWith(".authoring-game-pack.json"))
    .sort();
  assert.deepEqual(names, expectedExamples.map(({ name }) => name).sort());

  for (const example of expectedExamples) {
    const firstRead = exampleBytes.get(example.name);
    const secondRead = await readFile(path.join(examplesRoot, example.name));
    assert.deepEqual(secondRead, firstRead);
    assert.equal(createHash("sha256").update(firstRead).digest("hex"), example.sha256);

    const text = new TextDecoder("utf-8", { fatal: true }).decode(firstRead);
    const firstReport = validateAuthoringGamePackJson(text);
    assert.deepEqual(firstReport, {
      reportVersion: 1,
      valid: true,
      diagnostics: [],
    });
    for (let repetition = 0; repetition < 5; repetition += 1) {
      assert.equal(
        JSON.stringify(validateAuthoringGamePackJson(text)),
        JSON.stringify(firstReport),
      );
    }
  }
});

test("missing required field is rejected at its exact pointer", () => {
  const pack = cloneNeutralPack();
  delete pack.title;
  assertOnlyDiagnostic(pack, "PACK_SCHEMA_REQUIRED", "/title");
});

test("unknown field is rejected without reporting its value", () => {
  const pack = cloneNeutralPack();
  pack.unexpected = "must-not-appear-in-diagnostics";
  const report = validateAuthoringGamePack(pack);
  assertOnlyDiagnostic(pack, "PACK_SCHEMA_UNKNOWN_PROPERTY", "");
  assert.doesNotMatch(JSON.stringify(report), /must-not-appear-in-diagnostics/);
});

test("top-level identifiers are globally unique across collections", () => {
  const pack = cloneNeutralPack();
  pack.entities.push(structuredClone(pack.entities[0]));
  const report = validateAuthoringGamePack(pack);
  assertDiagnostic(report, "PACK_TOP_LEVEL_ID_DUPLICATE", "/entities/2/id");
  assert.equal(report.diagnostics.length, 1);
  assert.equal(report.diagnostics[0].relatedPath, "/entities/0/id");
});

test("action identifiers are unique inside their node", () => {
  const pack = cloneNeutralPack();
  pack.nodes[0].actions.push(structuredClone(pack.nodes[0].actions[0]));
  const report = validateAuthoringGamePack(pack);
  assertDiagnostic(report, "PACK_ACTION_ID_DUPLICATE", "/nodes/0/actions/1/id");
  assert.equal(report.diagnostics.length, 1);
  assert.equal(report.diagnostics[0].relatedPath, "/nodes/0/actions/0/id");
});

test("dangling entity reference is rejected at the reference", () => {
  const pack = cloneNeutralPack();
  pack.nodes[0].entityIds[0] = "missing-entity";
  assertOnlyDiagnostic(pack, "PACK_ENTITY_REFERENCE_UNKNOWN", "/nodes/0/entityIds/0");
});

test("dangling cue reference is rejected at the reference", () => {
  const pack = cloneNeutralPack();
  pack.nodes[0].entryCueIds[0] = "missing-cue";
  assertOnlyDiagnostic(pack, "PACK_CUE_REFERENCE_UNKNOWN", "/nodes/0/entryCueIds/0");
});

test("dangling variable reference is rejected at the reference", () => {
  const pack = cloneNeutralPack();
  pack.nodes[1].actions[0].when.conditions[0].variableId = "missing-variable";
  assertOnlyDiagnostic(
    pack,
    "PACK_VARIABLE_REFERENCE_UNKNOWN",
    "/nodes/1/actions/0/when/conditions/0/variableId",
  );
});

test("typed target is resolved only in its declared target collection", () => {
  const pack = cloneNeutralPack();
  pack.nodes[0].actions[0].target.kind = "ending";
  assertOnlyDiagnostic(
    pack,
    "PACK_TARGET_REFERENCE_UNKNOWN",
    "/nodes/0/actions/0/target/id",
  );
});

test("enum initial value must belong to allowedValues", () => {
  const pack = cloneNeutralPack();
  pack.variables[2].initial = "mode-undeclared";
  assertOnlyDiagnostic(pack, "PACK_ENUM_INITIAL_NOT_ALLOWED", "/variables/2/initial");
});

test("condition value type must match its variable", () => {
  const pack = cloneNeutralPack();
  pack.nodes[1].actions[0].when.conditions[0].value = "not-a-boolean";
  assertOnlyDiagnostic(
    pack,
    "PACK_CONDITION_VALUE_TYPE_MISMATCH",
    "/nodes/1/actions/0/when/conditions/0/value",
  );
});

test("effect value type must match its variable", () => {
  const pack = cloneNeutralPack();
  pack.nodes[0].actions[0].effects[0].value = "not-a-boolean";
  assertOnlyDiagnostic(
    pack,
    "PACK_EFFECT_VALUE_TYPE_MISMATCH",
    "/nodes/0/actions/0/effects/0/value",
  );
});

test("condition nesting depth 17 is rejected at the first excessive condition", () => {
  const pack = cloneNeutralPack();
  pack.nodes[0].actions[0].when = nestedNotCondition(17);
  assertOnlyDiagnostic(
    pack,
    "PACK_CONDITION_DEPTH_EXCEEDED",
    `/nodes/0/actions/0/when${"/condition".repeat(16)}`,
  );
});

test("unreachable node is rejected at its identifier", () => {
  const pack = cloneNeutralPack();
  pack.nodes.push({
    ...structuredClone(pack.nodes[4]),
    id: "node-unreachable",
  });
  assertOnlyDiagnostic(pack, "PACK_NODE_UNREACHABLE", "/nodes/5/id");
});

test("reachable closed branch with no ending path reports only its closed node", () => {
  const pack = cloneNeutralPack();
  pack.nodes = [
    {
      ...pack.nodes[0],
      actions: [
        ...pack.nodes[0].actions,
        {
          id: "action-enter-closed",
          label: "Enter closed branch",
          effects: [],
          target: { kind: "node", id: "node-closed" },
        },
      ],
    },
    ...pack.nodes.slice(1),
    {
      id: "node-closed",
      title: "Closed branch",
      entityIds: [],
      entryCueIds: [],
      actions: [
        {
          id: "action-loop-closed",
          label: "Remain in closed branch",
          effects: [],
          target: { kind: "node", id: "node-closed" },
        },
      ],
    },
  ];
  assertOnlyDiagnostic(pack, "PACK_NODE_NO_ENDING_PATH", "/nodes/5/id");
});

for (const forbiddenField of [
  "facts",
  "evidence",
  "npcBeliefs",
  "assetPath",
  "godotScene",
  "transform",
]) {
  test(`sample-specific field ${forbiddenField} is forbidden by the generic contract`, () => {
    const pack = cloneNeutralPack();
    pack[forbiddenField] = true;
    assertOnlyDiagnostic(pack, "PACK_SCHEMA_UNKNOWN_PROPERTY", "");
  });
}

test("contracts and validator sources are fixture- and subject-independent", async () => {
  const sourceRoots = [
    path.join(moduleRoot, "packages", "game-pack-contracts", "src"),
    path.join(moduleRoot, "packages", "game-pack-contracts", "schemas"),
    path.join(moduleRoot, "packages", "game-pack-validator", "src"),
  ];
  const sourceFiles = (
    await Promise.all(sourceRoots.map((sourceRoot) => collectSourceFiles(sourceRoot)))
  ).flat();
  const combinedSource = (
    await Promise.all(sourceFiles.map((sourceFile) => readFile(sourceFile, "utf8")))
  ).join("\n");

  assert.doesNotMatch(combinedSource, /(?:\.\.[/\\])+examples[/\\]/i);
  assert.doesNotMatch(
    combinedSource,
    /last[- ]?train|末班地铁|回声十三站|银环线|背包学生|夜班护士|沉默通勤者/i,
  );
});

test("example validator uses only local module inputs and has stable output", async () => {
  const scriptPath = path.join(moduleRoot, "scripts", "validate-examples.mjs");
  const scriptSource = await readFile(scriptPath, "utf8");
  const importSpecifiers = [...scriptSource.matchAll(/from\s+["']([^"']+)["']/g)]
    .map((match) => match[1])
    .sort();
  assert.deepEqual(importSpecifiers, [
    "@matrix-oasis/game-pack-validator",
    "node:crypto",
    "node:fs/promises",
    "node:path",
    "node:url",
  ]);
  assert.doesNotMatch(
    scriptSource,
    /\b(?:fetch|WebSocket|EventSource)\b|node:(?:child_process|dgram|dns|http|https|net|tls)|https?:\/\//,
  );
  assert.doesNotMatch(scriptSource, /(?:^|[/\\])(?:client|server)(?:[/\\]|$)/m);
  assert.doesNotMatch(scriptSource, /process\.env/);

  const run = () =>
    spawnSync(process.execPath, [scriptPath], {
      cwd: moduleRoot,
      encoding: "utf8",
      windowsHide: true,
    });
  const first = run();
  const second = run();
  assert.equal(first.status, 0, first.stderr);
  assert.equal(first.signal, null);
  assert.equal(first.stderr, "");
  assert.equal(second.status, first.status);
  assert.equal(second.signal, first.signal);
  assert.equal(second.stdout, first.stdout);
  assert.equal(second.stderr, first.stderr);
  assert.equal(
    first.stdout,
    [
      "EXAMPLE_VALID\tlast-train-r1.authoring-game-pack.json\tsha256=c98b277d8e960404658f530eeb11ccee5faec2829032711ca02be3fdd827bf98",
      "EXAMPLE_VALID\tmechanics-conformance.authoring-game-pack.json\tsha256=55896eaa631f2b563df163f77002924e4e6ea1d3a9d421dc383e777c172aa119",
      "EXAMPLES_VALID\tcount=2",
      "",
    ].join("\n"),
  );
});

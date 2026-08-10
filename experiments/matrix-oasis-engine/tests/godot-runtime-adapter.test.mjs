import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  buildGodotAdapterCases,
  GODOT_ADAPTER_MARKER,
  GodotRuntimeHarnessError,
  parseGodotAdapterOutput,
} from "../scripts/lib/godot-runtime-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function example(name) {
  return {
    name,
    text: fs.readFileSync(path.join(moduleRoot, "examples", `${name}.authoring-game-pack.json`), "utf8"),
  };
}

test("adapter cases compile both frozen examples and lock every rejection class", async () => {
  const cases = await buildGodotAdapterCases({
    examples: [example("mechanics-conformance"), example("last-train-r1")],
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
  });
  assert.deepEqual(cases.map((item) => [item.name, item.expected]), [
    ["valid-mechanics-conformance", { ok: true }],
    ["valid-last-train-r1", { ok: true }],
    ["noncanonical-runtime", { ok: false, code: "GODOT_RUNTIME_JSON_INVALID" }],
    ["invalid-utf8", { ok: false, code: "GODOT_RUNTIME_JSON_INVALID" }],
    ["wrong-hash", { ok: false, code: "GODOT_RUNTIME_INTEGRITY_MISMATCH" }],
    ["invalid-index", { ok: false, code: "GODOT_RUNTIME_SEMANTIC_INVALID" }],
    ["unknown-field", { ok: false, code: "GODOT_RUNTIME_SCHEMA_INVALID" }],
    ["unsupported-text", { ok: false, code: "GODOT_RUNTIME_UNSUPPORTED_TEXT" }],
    ["unsafe-integer", { ok: false, code: "GODOT_RUNTIME_JSON_INVALID" }],
  ]);
  assert.equal(Object.isFrozen(cases), true);
  assert.equal(cases.every((item) => Object.isFrozen(item) && Object.isFrozen(item.expected)), true);
  assert.equal(cases[0].runtimeBytes.byteLength > 0, true);
  assert.equal(cases[1].receiptBytes.byteLength > 0, true);
});

test("adapter marker parser accepts only the fixed public result surface", () => {
  assert.deepEqual(parseGodotAdapterOutput(`${GODOT_ADAPTER_MARKER}{"ok":true}\n`, 0), { ok: true });
  const failure = {
    ok: false,
    diagnostics: [{
      phase: "integrity",
      severity: "error",
      code: "GODOT_RUNTIME_INTEGRITY_MISMATCH",
      path: "/receipt/artifact/sha256",
      message: "GODOT_RUNTIME_INTEGRITY_MISMATCH",
    }],
  };
  assert.deepEqual(
    parseGodotAdapterOutput(`${GODOT_ADAPTER_MARKER}${JSON.stringify(failure)}\n`, 1),
    failure,
  );
});

test("adapter marker parser rejects extra fields, dynamic paths, and duplicate markers", () => {
  const secret = ["do-not", "echo-this"].join("-");
  const invalidReports = [
    [`${GODOT_ADAPTER_MARKER}{"ok":true,"extra":true}\n`, 0],
    [`${GODOT_ADAPTER_MARKER}{"ok":false,"diagnostics":[]}\n`, 1],
    [`${GODOT_ADAPTER_MARKER}${JSON.stringify({
      ok: false,
      diagnostics: [{
        phase: "input",
        severity: "error",
        code: "GODOT_RUNTIME_INPUT_INVALID",
        path: `/runtimePack/${secret}`,
        message: "GODOT_RUNTIME_INPUT_INVALID",
      }],
    })}\n`, 1],
    [`${GODOT_ADAPTER_MARKER}{"ok":true}\n${GODOT_ADAPTER_MARKER}{"ok":true}\n`, 0],
  ];
  for (const [output, status] of invalidReports) {
    assert.throws(
      () => parseGodotAdapterOutput(output, status),
      (error) => error instanceof GodotRuntimeHarnessError &&
        ["GODOT_ADAPTER_MARKER_INVALID", "GODOT_ADAPTER_REPORT_INVALID"].includes(error.code) &&
        !String(error).includes(secret),
    );
  }
});

test("Godot adapter sources remain independent from JavaScript evaluators and topic IDs", () => {
  const runtimeRoot = path.join(moduleRoot, "apps", "runtime-godot", "runtime");
  const source = fs.readdirSync(runtimeRoot)
    .filter((name) => name.endsWith(".gd"))
    .map((name) => fs.readFileSync(path.join(runtimeRoot, name), "utf8"))
    .join("\n");
  for (const forbidden of [
    "game-pack-simulator",
    "runtime-pack-simulator",
    "game-pack-parity-harness",
    "last-train",
    "ending-return",
    "ending-stay",
    "ending-loop",
  ]) {
    assert.equal(source.includes(forbidden), false);
  }
});

test("Godot runtime exposes only synchronous create, inspect, and one-step apply entrypoints", () => {
  const source = fs.readFileSync(
    path.join(moduleRoot, "apps", "runtime-godot", "runtime", "runtime_session.gd"),
    "utf8",
  );
  for (const signature of [
    "static func create_game_session(",
    "static func inspect_game_session(",
    "static func apply_game_session_action(",
  ]) {
    assert.equal(source.includes(signature), true);
  }
  assert.equal(source.includes("await "), false);
  assert.equal(source.includes("PACK_GODOT_RUNTIME_INTERNAL_ERROR"), true);
  assert.equal(source.includes("_deep_read_only(result)"), true);
});

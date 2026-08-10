import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { isDeepStrictEqual } from "node:util";
import { assertGodotOutputClean, runGodotCommand } from "./godot-core.mjs";

export const GODOT_ADAPTER_MARKER = "MATRIX_OASIS_R5_ADAPTER_JSON:";
export const GODOT_TRACE_MARKER = "MATRIX_OASIS_R5_TRACE_JSON:";

const PUBLIC_DIAGNOSTIC_CODES = new Set([
  "GODOT_RUNTIME_INPUT_INVALID",
  "GODOT_RUNTIME_JSON_INVALID",
  "GODOT_RUNTIME_SCHEMA_INVALID",
  "GODOT_RUNTIME_SEMANTIC_INVALID",
  "GODOT_RUNTIME_INTEGRITY_MISMATCH",
  "GODOT_RUNTIME_UNSUPPORTED_TEXT",
]);

export class GodotRuntimeHarnessError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotRuntimeHarnessError";
    this.code = code;
  }
}

function fail(code) {
  throw new GodotRuntimeHarnessError(code);
}

function exactKeys(value, expected) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) {
    return false;
  }
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function freezeJson(value) {
  if (Array.isArray(value)) {
    for (const item of value) {
      freezeJson(item);
    }
    return Object.freeze(value);
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) {
      freezeJson(item);
    }
    return Object.freeze(value);
  }
  return value;
}

export function parseGodotAdapterOutput(output, status) {
  const text = typeof output === "string" ? output : "";
  const markerCount = text.split(GODOT_ADAPTER_MARKER).length - 1;
  if (markerCount !== 1) {
    fail("GODOT_ADAPTER_MARKER_INVALID");
  }
  const markerLine = text.split(/\r?\n/u).find((line) => line.includes(GODOT_ADAPTER_MARKER));
  if (!markerLine) {
    fail("GODOT_ADAPTER_MARKER_INVALID");
  }
  let report;
  try {
    report = JSON.parse(markerLine.slice(markerLine.indexOf(GODOT_ADAPTER_MARKER) + GODOT_ADAPTER_MARKER.length));
  } catch {
    fail("GODOT_ADAPTER_REPORT_INVALID");
  }
  if (status === 0 && exactKeys(report, ["ok"]) && report.ok === true) {
    return Object.freeze({ ok: true });
  }
  if (status !== 1 || !exactKeys(report, ["diagnostics", "ok"]) || report.ok !== false ||
      !Array.isArray(report.diagnostics) || report.diagnostics.length !== 1) {
    fail("GODOT_ADAPTER_REPORT_INVALID");
  }
  const diagnostic = report.diagnostics[0];
  if (!exactKeys(diagnostic, ["code", "message", "path", "phase", "severity"]) ||
      diagnostic.severity !== "error" || diagnostic.message !== diagnostic.code ||
      !PUBLIC_DIAGNOSTIC_CODES.has(diagnostic.code) ||
      !["input", "parse", "schema", "semantic", "integrity"].includes(diagnostic.phase) ||
      !/^\/(?:runtimePack|receipt)(?:\/[A-Za-z0-9]+)*$/u.test(diagnostic.path)) {
    fail("GODOT_ADAPTER_REPORT_INVALID");
  }
  return Object.freeze({
    ok: false,
    diagnostics: Object.freeze([Object.freeze({ ...diagnostic })]),
  });
}

export function parseGodotTraceOutput(output, status) {
  const text = typeof output === "string" ? output : "";
  if (status !== 0 || text.split(GODOT_TRACE_MARKER).length - 1 !== 1) {
    fail("GODOT_TRACE_MARKER_INVALID");
  }
  const markerLine = text.split(/\r?\n/u).find((line) => line.includes(GODOT_TRACE_MARKER));
  if (!markerLine) {
    fail("GODOT_TRACE_MARKER_INVALID");
  }
  let trace;
  try {
    trace = JSON.parse(markerLine.slice(markerLine.indexOf(GODOT_TRACE_MARKER) + GODOT_TRACE_MARKER.length));
  } catch {
    fail("GODOT_TRACE_REPORT_INVALID");
  }
  if (!exactKeys(trace, ["created", "steps", "traceVersion"]) || trace.traceVersion !== 1 ||
      !trace.created || typeof trace.created !== "object" || trace.created.ok !== true ||
      !Array.isArray(trace.steps)) {
    fail("GODOT_TRACE_REPORT_INVALID");
  }
  return freezeJson(trace);
}

async function sha256(text) {
  if (!globalThis.crypto?.subtle || typeof globalThis.TextEncoder !== "function") {
    fail("GODOT_ADAPTER_CRYPTO_UNAVAILABLE");
  }
  let digest;
  try {
    digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  } catch {
    fail("GODOT_ADAPTER_CRYPTO_FAILED");
  }
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function receiptForRuntime(runtimeText, receipt, canonicalizeJsonValue) {
  const next = JSON.parse(canonicalizeJsonValue(receipt));
  next.artifact.byteLength = new TextEncoder().encode(runtimeText).byteLength;
  next.artifact.sha256 = await sha256(runtimeText);
  return canonicalizeJsonValue(next);
}

function referenceTrace({ prepared, actions, stepLimit, createSession, applyAction }) {
  const created = createSession(prepared, { stepLimit });
  if (!created?.ok) {
    fail("GODOT_PARITY_REFERENCE_CREATE_FAILED");
  }
  const steps = [];
  let snapshot = created.snapshot;
  for (const actionId of actions) {
    const result = applyAction(prepared, snapshot, actionId);
    if (!result || typeof result.ok !== "boolean") {
      fail("GODOT_PARITY_REFERENCE_STEP_FAILED");
    }
    steps.push(result);
    if (result.ok) {
      snapshot = result.snapshot;
    }
  }
  return freezeJson(JSON.parse(JSON.stringify({ traceVersion: 1, created, steps })));
}

function overflowAuthoringText(mechanicsText, initial, delta) {
  let pack;
  try {
    pack = JSON.parse(mechanicsText);
  } catch {
    fail("GODOT_PARITY_CASE_INPUT_INVALID");
  }
  const variable = pack.variables?.find((item) => item.id === "counter-value");
  const entryNode = pack.nodes?.find((item) => item.id === pack.entryNodeId);
  const action = entryNode?.actions?.find((item) => item.id === "action-initialize");
  if (!variable || variable.type !== "integer" || !action) {
    fail("GODOT_PARITY_CASE_INPUT_INVALID");
  }
  variable.initial = initial;
  delete action.when;
  action.effects = [
    { op: "emitCue", cueId: "cue-change" },
    { op: "add", variableId: variable.id, value: delta },
  ];
  return JSON.stringify(pack);
}

export async function buildGodotParityCases({
  examples,
  compileAuthoringGamePackJson,
  canonicalizeJsonValue,
  prepareRuntimeGamePackJson,
  createRuntimeGameSession,
  applyRuntimeGameSessionAction,
}) {
  if (!Array.isArray(examples) || examples.length !== 2 ||
      typeof compileAuthoringGamePackJson !== "function" ||
      typeof canonicalizeJsonValue !== "function" ||
      typeof prepareRuntimeGamePackJson !== "function" ||
      typeof createRuntimeGameSession !== "function" ||
      typeof applyRuntimeGameSessionAction !== "function") {
    fail("GODOT_PARITY_CASE_INPUT_INVALID");
  }
  const byName = new Map(examples.map((item) => [item.name, item.text]));
  const mechanicsText = byName.get("mechanics-conformance");
  const lastTrainText = byName.get("last-train-r1");
  if (typeof mechanicsText !== "string" || typeof lastTrainText !== "string") {
    fail("GODOT_PARITY_CASE_INPUT_INVALID");
  }
  const specifications = [
    {
      name: "mechanics-complete-with-failures",
      text: mechanicsText,
      actions: [
        "unknown-action", "action-initialize", "action-check-hold", "action-check",
        "action-adjust", "action-review", "action-complete", "action-complete",
      ],
      stepLimit: 256,
      repetitions: 20,
    },
    {
      name: "last-train-return",
      text: lastTrainText,
      actions: [
        "inspect-map", "compare-ticket", "return-to-carriage", "ask-student",
        "trust-student", "compare-versions", "choose-return",
      ],
      stepLimit: 256,
      repetitions: 1,
    },
    {
      name: "last-train-stay",
      text: lastTrainText,
      actions: ["step-out", "follow-platform-light", "choose-stay"],
      stepLimit: 256,
      repetitions: 1,
    },
    {
      name: "last-train-loop-ending",
      text: lastTrainText,
      actions: ["ask-student", "trust-student", "trust-announcement"],
      stepLimit: 256,
      repetitions: 1,
    },
    {
      name: "last-train-explicit-loop-limit",
      text: lastTrainText,
      actions: ["inspect-map", "return-carriage", "inspect-map", "return-carriage", "inspect-map"],
      stepLimit: 4,
      repetitions: 1,
    },
    {
      name: "positive-overflow",
      text: overflowAuthoringText(mechanicsText, Number.MAX_SAFE_INTEGER, 1),
      actions: ["action-initialize"],
      stepLimit: 256,
      repetitions: 1,
    },
    {
      name: "negative-overflow",
      text: overflowAuthoringText(mechanicsText, Number.MIN_SAFE_INTEGER, -1),
      actions: ["action-initialize"],
      stepLimit: 256,
      repetitions: 1,
    },
  ];
  const cases = [];
  for (const specification of specifications) {
    const compiled = await compileAuthoringGamePackJson(specification.text);
    if (!compiled?.ok || typeof compiled.canonicalJson !== "string") {
      fail("GODOT_PARITY_EXAMPLE_COMPILE_FAILED");
    }
    const receiptText = canonicalizeJsonValue(compiled.receipt);
    const prepared = await prepareRuntimeGamePackJson(compiled.canonicalJson, receiptText);
    if (!prepared?.ok) {
      fail("GODOT_PARITY_REFERENCE_PREPARE_FAILED");
    }
    const trace = referenceTrace({
      prepared: prepared.prepared,
      actions: specification.actions,
      stepLimit: specification.stepLimit,
      createSession: createRuntimeGameSession,
      applyAction: applyRuntimeGameSessionAction,
    });
    cases.push(Object.freeze({
      name: specification.name,
      runtimeText: compiled.canonicalJson,
      receiptText,
      actions: Object.freeze([...specification.actions]),
      stepLimit: specification.stepLimit,
      repetitions: specification.repetitions,
      referenceTrace: trace,
    }));
  }
  return Object.freeze(cases);
}

export async function buildGodotAdapterCases({
  examples,
  compileAuthoringGamePackJson,
  canonicalizeJsonValue,
}) {
  if (!Array.isArray(examples) || examples.length !== 2 ||
      typeof compileAuthoringGamePackJson !== "function" || typeof canonicalizeJsonValue !== "function") {
    fail("GODOT_ADAPTER_CASE_INPUT_INVALID");
  }
  const compiledExamples = [];
  for (const example of examples) {
    const compiled = await compileAuthoringGamePackJson(example.text);
    if (!compiled?.ok || typeof compiled.canonicalJson !== "string") {
      fail("GODOT_ADAPTER_EXAMPLE_COMPILE_FAILED");
    }
    compiledExamples.push({
      name: example.name,
      runtimePack: compiled.runtimePack,
      runtimeText: compiled.canonicalJson,
      receipt: compiled.receipt,
      receiptText: canonicalizeJsonValue(compiled.receipt),
    });
  }

  const cases = compiledExamples.map((compiled) => ({
    name: `valid-${compiled.name}`,
    runtimeBytes: new TextEncoder().encode(compiled.runtimeText),
    receiptBytes: new TextEncoder().encode(compiled.receiptText),
    expected: { ok: true },
  }));
  const reference = compiledExamples[0];

  cases.push({
    name: "noncanonical-runtime",
    runtimeBytes: new TextEncoder().encode(`${reference.runtimeText}\n`),
    receiptBytes: new TextEncoder().encode(reference.receiptText),
    expected: { ok: false, code: "GODOT_RUNTIME_JSON_INVALID" },
  });
  cases.push({
    name: "invalid-utf8",
    runtimeBytes: Uint8Array.from([0xff]),
    receiptBytes: new TextEncoder().encode(reference.receiptText),
    expected: { ok: false, code: "GODOT_RUNTIME_JSON_INVALID" },
  });

  const wrongHash = JSON.parse(reference.receiptText);
  wrongHash.artifact.sha256 = "0".repeat(64);
  cases.push({
    name: "wrong-hash",
    runtimeBytes: new TextEncoder().encode(reference.runtimeText),
    receiptBytes: new TextEncoder().encode(canonicalizeJsonValue(wrongHash)),
    expected: { ok: false, code: "GODOT_RUNTIME_INTEGRITY_MISMATCH" },
  });

  const invalidIndex = JSON.parse(reference.runtimeText);
  invalidIndex.nodes[0].actions[0].target = { kind: "node", index: invalidIndex.nodes.length };
  const invalidIndexText = canonicalizeJsonValue(invalidIndex);
  cases.push({
    name: "invalid-index",
    runtimeBytes: new TextEncoder().encode(invalidIndexText),
    receiptBytes: new TextEncoder().encode(
      await receiptForRuntime(invalidIndexText, reference.receipt, canonicalizeJsonValue),
    ),
    expected: { ok: false, code: "GODOT_RUNTIME_SEMANTIC_INVALID" },
  });

  const unknownField = JSON.parse(reference.runtimeText);
  unknownField.unexpected = true;
  const unknownFieldText = canonicalizeJsonValue(unknownField);
  cases.push({
    name: "unknown-field",
    runtimeBytes: new TextEncoder().encode(unknownFieldText),
    receiptBytes: new TextEncoder().encode(
      await receiptForRuntime(unknownFieldText, reference.receipt, canonicalizeJsonValue),
    ),
    expected: { ok: false, code: "GODOT_RUNTIME_SCHEMA_INVALID" },
  });

  const unsupportedText = JSON.parse(reference.runtimeText);
  unsupportedText.title = String.fromCharCode(0xd800);
  const unsupportedTextJson = canonicalizeJsonValue(unsupportedText);
  cases.push({
    name: "unsupported-text",
    runtimeBytes: new TextEncoder().encode(unsupportedTextJson),
    receiptBytes: new TextEncoder().encode(
      await receiptForRuntime(unsupportedTextJson, reference.receipt, canonicalizeJsonValue),
    ),
    expected: { ok: false, code: "GODOT_RUNTIME_UNSUPPORTED_TEXT" },
  });

  const unsafeIntegerText = reference.runtimeText.replace('"entryNodeIndex":0', '"entryNodeIndex":9007199254740992');
  cases.push({
    name: "unsafe-integer",
    runtimeBytes: new TextEncoder().encode(unsafeIntegerText),
    receiptBytes: new TextEncoder().encode(reference.receiptText),
    expected: { ok: false, code: "GODOT_RUNTIME_JSON_INVALID" },
  });

  return Object.freeze(cases.map((item) => Object.freeze({
    ...item,
    expected: Object.freeze({ ...item.expected }),
  })));
}

function temporaryBase(moduleRoot) {
  if (process.platform === "win32") {
    return path.join(path.parse(moduleRoot).root, "tmp");
  }
  return os.tmpdir();
}

function removeTemporaryRoot(temporaryRoot, base, expectedPrefix) {
  const resolvedBase = fs.realpathSync(base);
  const resolvedRoot = fs.realpathSync(temporaryRoot);
  const relative = path.relative(resolvedBase, resolvedRoot);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative) ||
      !path.basename(resolvedRoot).startsWith(expectedPrefix)) {
    fail("GODOT_ADAPTER_TEMPORARY_PATH_INVALID");
  }
  fs.rmSync(resolvedRoot, { recursive: true });
}

export function runGodotAdapterCases({
  moduleRoot,
  sourceProjectRoot,
  godotCommand,
  cases,
  spawn = spawnSync,
}) {
  const base = temporaryBase(moduleRoot);
  fs.mkdirSync(base, { recursive: true });
  const temporaryRoot = fs.mkdtempSync(path.join(base, "matrix-oasis-r5-adapter-"));
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  const inputsRoot = path.join(temporaryRoot, "inputs");
  try {
    fs.cpSync(sourceProjectRoot, projectRoot, {
      recursive: true,
      filter: (source) => path.basename(source) !== ".godot",
    });
    fs.mkdirSync(inputsRoot);
    const importOutput = runGodotCommand({
      command: godotCommand,
      args: ["--headless", "--editor", "--path", projectRoot, "--quit"],
      cwd: moduleRoot,
      timeout: 120_000,
      spawn,
    });
    assertGodotOutputClean(importOutput);

    const results = [];
    for (let index = 0; index < cases.length; index += 1) {
      const item = cases[index];
      const caseRoot = path.join(inputsRoot, String(index));
      fs.mkdirSync(caseRoot);
      const runtimePath = path.join(caseRoot, "runtime.json");
      const receiptPath = path.join(caseRoot, "receipt.json");
      fs.writeFileSync(runtimePath, item.runtimeBytes, { flag: "wx" });
      fs.writeFileSync(receiptPath, item.receiptBytes, { flag: "wx" });
      const processResult = spawn(godotCommand, [
        "--headless",
        "--path",
        projectRoot,
        "--script",
        "res://runtime/adapter_probe.gd",
        "--",
        `--matrix-oasis-runtime-pack=${runtimePath}`,
        `--matrix-oasis-runtime-receipt=${receiptPath}`,
      ], {
        cwd: moduleRoot,
        encoding: "utf8",
        maxBuffer: 8 * 1024 * 1024,
        shell: false,
        timeout: 30_000,
        windowsHide: true,
      });
      if (processResult.error || ![0, 1].includes(processResult.status)) {
        fail("GODOT_ADAPTER_COMMAND_FAILED");
      }
      const output = `${processResult.stdout ?? ""}${processResult.stderr ?? ""}`;
      assertGodotOutputClean(output);
      const report = parseGodotAdapterOutput(output, processResult.status);
      if (report.ok !== item.expected.ok || (!report.ok && report.diagnostics[0].code !== item.expected.code)) {
        fail("GODOT_ADAPTER_EXPECTATION_FAILED");
      }
      results.push(Object.freeze({ name: item.name, ok: report.ok, code: report.ok ? null : report.diagnostics[0].code }));
    }
    removeTemporaryRoot(temporaryRoot, base, "matrix-oasis-r5-adapter-");
    return Object.freeze(results);
  } catch (error) {
    if (error instanceof GodotRuntimeHarnessError) {
      throw error;
    }
    fail("GODOT_ADAPTER_HARNESS_INTERNAL_ERROR");
  }
}

export function runGodotParityCases({
  moduleRoot,
  sourceProjectRoot,
  godotCommand,
  cases,
  spawn = spawnSync,
}) {
  if (!Array.isArray(cases) || cases.length < 1) {
    fail("GODOT_PARITY_CASE_INPUT_INVALID");
  }
  const base = temporaryBase(moduleRoot);
  fs.mkdirSync(base, { recursive: true });
  const temporaryRoot = fs.mkdtempSync(path.join(base, "matrix-oasis-r5-parity-"));
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  const inputsRoot = path.join(temporaryRoot, "inputs");
  try {
    fs.cpSync(sourceProjectRoot, projectRoot, {
      recursive: true,
      filter: (source) => path.basename(source) !== ".godot",
    });
    fs.mkdirSync(inputsRoot);
    const importOutput = runGodotCommand({
      command: godotCommand,
      args: ["--headless", "--editor", "--path", projectRoot, "--quit"],
      cwd: moduleRoot,
      timeout: 120_000,
      spawn,
    });
    assertGodotOutputClean(importOutput);
    const results = [];
    for (let index = 0; index < cases.length; index += 1) {
      const item = cases[index];
      const caseRoot = path.join(inputsRoot, String(index));
      fs.mkdirSync(caseRoot);
      const runtimePath = path.join(caseRoot, "runtime.json");
      const receiptPath = path.join(caseRoot, "receipt.json");
      fs.writeFileSync(runtimePath, item.runtimeText, { encoding: "utf8", flag: "wx" });
      fs.writeFileSync(receiptPath, item.receiptText, { encoding: "utf8", flag: "wx" });
      const serializations = [];
      for (let repetition = 0; repetition < item.repetitions; repetition += 1) {
        const args = [
          "--headless",
          "--path",
          projectRoot,
          "--script",
          "res://runtime/runtime_trace_runner.gd",
          "--",
          `--matrix-oasis-runtime-pack=${runtimePath}`,
          `--matrix-oasis-runtime-receipt=${receiptPath}`,
          `--matrix-oasis-trace-step-limit=${item.stepLimit}`,
          ...item.actions.map((actionId) => `--matrix-oasis-trace-action=${actionId}`),
        ];
        const processResult = spawn(godotCommand, args, {
          cwd: moduleRoot,
          encoding: "utf8",
          maxBuffer: 8 * 1024 * 1024,
          shell: false,
          timeout: 30_000,
          windowsHide: true,
        });
        if (processResult.error || processResult.status !== 0) {
          fail("GODOT_PARITY_COMMAND_FAILED");
        }
        const output = `${processResult.stdout ?? ""}${processResult.stderr ?? ""}`;
        assertGodotOutputClean(output);
        const trace = parseGodotTraceOutput(output, processResult.status);
        if (!isDeepStrictEqual(trace, item.referenceTrace)) {
          fail("GODOT_PARITY_MISMATCH");
        }
        serializations.push(JSON.stringify(trace));
      }
      if (new Set(serializations).size !== 1) {
        fail("GODOT_PARITY_NONDETERMINISTIC");
      }
      results.push(Object.freeze({ name: item.name, repetitions: item.repetitions }));
    }
    removeTemporaryRoot(temporaryRoot, base, "matrix-oasis-r5-parity-");
    return Object.freeze(results);
  } catch (error) {
    if (error instanceof GodotRuntimeHarnessError) {
      throw error;
    }
    fail("GODOT_PARITY_HARNESS_INTERNAL_ERROR");
  }
}

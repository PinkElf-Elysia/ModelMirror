import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { assertGodotOutputClean, runGodotCommand } from "./godot-core.mjs";

export const GODOT_ADAPTER_MARKER = "MATRIX_OASIS_R5_ADAPTER_JSON:";

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

function removeTemporaryRoot(temporaryRoot, base) {
  const resolvedBase = fs.realpathSync(base);
  const resolvedRoot = fs.realpathSync(temporaryRoot);
  const relative = path.relative(resolvedBase, resolvedRoot);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative) ||
      !path.basename(resolvedRoot).startsWith("matrix-oasis-r5-adapter-")) {
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
    removeTemporaryRoot(temporaryRoot, base);
    return Object.freeze(results);
  } catch (error) {
    if (error instanceof GodotRuntimeHarnessError) {
      throw error;
    }
    fail("GODOT_ADAPTER_HARNESS_INTERNAL_ERROR");
  }
}

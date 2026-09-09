import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  loadRpg04RuntimeProtocol,
  RPG04_RUNTIME_PROTOCOL_SHA256,
  validateRpg04RuntimeProtocolText,
} from "../tooling/context-protocol.mjs";

const runtimeText = fs.readFileSync(new URL("../docs/RPG04_PROTOCOL_RUNTIME.txt", import.meta.url), "utf8");
const auditText = fs.readFileSync(new URL("../docs/RPG04_PROTOCOL_AUDIT.md", import.meta.url), "utf8");
const sha = (text) => createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex");

test("loads only the fixed runtime protocol and binds its exact bytes", () => {
  const report = loadRpg04RuntimeProtocol();
  assert.equal(report.valid, true, JSON.stringify(report.diagnostics));
  assert.equal(report.value.content, runtimeText);
  assert.equal(report.value.sha256, RPG04_RUNTIME_PROTOCOL_SHA256);
  assert.equal(sha(report.value.content), RPG04_RUNTIME_PROTOCOL_SHA256);
  assert.equal(report.value.sourceReference, "docs/RPG04_PROTOCOL_RUNTIME.txt");
  assert.equal(pathIsAbsolute(report.value.sourceReference), false);
});

test("rejects audit text without reflecting source content in diagnostics", () => {
  const report = validateRpg04RuntimeProtocolText(auditText);
  assert.equal(report.valid, false);
  assert.equal(report.diagnostics[0].code, "RPG04_PROTOCOL_HASH_MISMATCH");
  assert.equal(report.diagnostics[0].path, "/protocol");
  assert.equal(JSON.stringify(report).includes(auditText.slice(0, 32)), false);
});

test("rejects appended audit text, whitespace and line-ending drift", () => {
  for (const text of [runtimeText + auditText, runtimeText + " ", runtimeText.replace(/\r?\n/g, "\r\n")]) {
    const report = validateRpg04RuntimeProtocolText(text);
    assert.equal(report.valid, false);
    assert.equal(report.diagnostics[0].code, "RPG04_PROTOCOL_HASH_MISMATCH");
  }
});

test("rejects invalid types and oversized UTF-8 input with stable diagnostics", () => {
  const invalid = validateRpg04RuntimeProtocolText(null);
  const oversizedCharacters = validateRpg04RuntimeProtocolText("x".repeat(65537));
  const oversizedBytes = validateRpg04RuntimeProtocolText("界".repeat(21846));
  assert.deepEqual(invalid.diagnostics[0], { phase: "protocol", severity: "error", code: "RPG04_PROTOCOL_TEXT_INVALID", path: "/protocol" });
  assert.equal(oversizedCharacters.diagnostics[0].code, "RPG04_PROTOCOL_TOO_LARGE");
  assert.equal(oversizedBytes.diagnostics[0].code, "RPG04_PROTOCOL_TOO_LARGE");
});

test("rejects every caller-supplied path or option", () => {
  for (const args of [["../docs/RPG04_PROTOCOL_AUDIT.md"], [{}], [undefined]]) {
    const report = loadRpg04RuntimeProtocol(...args);
    assert.equal(report.valid, false);
    assert.equal(report.diagnostics[0].code, "RPG04_PROTOCOL_ARGUMENTS_REJECTED");
    assert.equal(report.diagnostics[0].path, "");
  }
});

test("returns deterministic detached results that cannot pollute later calls", () => {
  const first = loadRpg04RuntimeProtocol();
  assert.throws(() => { first.value.content = "changed"; }, TypeError);
  const second = loadRpg04RuntimeProtocol();
  assert.deepEqual(first, second);
  assert.equal(second.value.content, runtimeText);
});

test("keeps the audit artifact byte identity outside the runtime input", () => {
  assert.equal(sha(auditText), "b239035ef2094b83e2df67e18218ff2149569ff23aa7d84fc54309261089ee55");
});

test("rejects a symbolic link on the fixed path without reading any protocol", (t) => {
  const original = fs.lstatSync;
  const reads = [];
  t.mock.method(fs, "lstatSync", (target) => path.basename(target) === "RPG04_PROTOCOL_RUNTIME.txt"
    ? { isSymbolicLink: () => true }
    : original(target));
  t.mock.method(fs, "readFileSync", (target) => { reads.push(String(target)); throw new Error("must not read"); });
  const report = loadRpg04RuntimeProtocol();
  assert.equal(report.diagnostics[0].code, "RPG04_PROTOCOL_SYMLINK_REJECTED");
  assert.deepEqual(reads, []);
});

test("rejects a realpath escape before reading any protocol", (t) => {
  const original = fs.realpathSync;
  let calls = 0;
  const reads = [];
  t.mock.method(fs, "realpathSync", (target) => ++calls === 1 ? original(target) : path.resolve(original(target), "..", "escaped.txt"));
  t.mock.method(fs, "readFileSync", (target) => { reads.push(String(target)); throw new Error("must not read"); });
  const report = loadRpg04RuntimeProtocol();
  assert.equal(report.diagnostics[0].code, "RPG04_PROTOCOL_PATH_ESCAPE");
  assert.deepEqual(reads, []);
});

test("contains read errors and never falls back to the audit artifact", (t) => {
  const reads = [];
  t.mock.method(fs, "readFileSync", (target) => { reads.push(String(target)); throw new Error("private read detail"); });
  const report = loadRpg04RuntimeProtocol();
  assert.equal(report.diagnostics[0].code, "RPG04_PROTOCOL_READ_FAILED");
  assert.equal(JSON.stringify(report).includes("private read detail"), false);
  assert.equal(reads.length, 1);
  assert.equal(reads.some((target) => target.includes("RPG04_PROTOCOL_AUDIT")), false);
});

test("rejects invalid UTF-8 bytes before accepting their decoded text", (t) => {
  t.mock.method(fs, "readFileSync", () => Buffer.from([0xff]));
  const report = loadRpg04RuntimeProtocol();
  assert.equal(report.valid, false);
  assert.equal(report.diagnostics[0].code, "RPG04_PROTOCOL_UTF8_INVALID");
});

function pathIsAbsolute(value) {
  return /^[A-Za-z]:[\\/]/.test(value) || value.startsWith("/") || value.startsWith("\\\\");
}

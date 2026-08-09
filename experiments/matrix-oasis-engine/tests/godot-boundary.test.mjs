import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { auditGodotBoundary } from "../scripts/check-godot-boundary.mjs";

function fixture(source, { vendorSource } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-godot-boundary-"));
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "scripts", "case.gd"), source, "utf8");
  if (vendorSource) {
    const vendor = path.join(root, "addons", "gdUnit4");
    fs.mkdirSync(vendor, { recursive: true });
    fs.writeFileSync(path.join(vendor, "upstream.gd"), vendorSource, "utf8");
  }
  return root;
}

function codes(report) {
  return report.violations.map((item) => item.code);
}

test("the committed first-party Godot source satisfies the R4 boundary", () => {
  const report = auditGodotBoundary();
  assert.equal(report.ok, true);
  assert.equal(report.checked >= 2, true);
});

test("approved static res reads pass and vendored source is excluded", (context) => {
  const root = fixture(
    "extends Node\nvar file = FileAccess.open(\"res://fixture.txt\", FileAccess.READ)\nvar scene = preload(\"res://fixture.tscn\")\n",
    { vendorSource: ["HTTP", "Client.new()"].join("") },
  );
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.deepEqual(auditGodotBoundary({ root }), { ok: true, checked: 1, violations: [] });
});

const cases = [
  ["network", ["HTTP", "Request.new()"].join(""), "GODOT_FIRST_PARTY_NETWORK"],
  ["socket", ["StreamPeer", "TCP.new()"].join(""), "GODOT_FIRST_PARTY_NETWORK"],
  ["process", ["OS.ex", "ecute(\"tool\")"].join(""), "GODOT_FIRST_PARTY_PROCESS"],
  ["environment", ["OS.get_", "environment(\"TOKEN\")"].join(""), "GODOT_FIRST_PARTY_ENVIRONMENT"],
  ["absolute-windows", "var path = \"" + ["C:", "outside"].join("\\") + "\"", "GODOT_FIRST_PARTY_ABSOLUTE_PATH"],
  ["absolute-posix", "var path = \"" + ["", "opt", "outside"].join("/") + "\"", "GODOT_FIRST_PARTY_ABSOLUTE_PATH"],
  ["dynamic-load", "var path = \"res://fixture.gd\"\nvar script = load(path)", "GODOT_FIRST_PARTY_DYNAMIC_LOAD"],
  ["script-new", ["GDScript", ".new()"].join(""), "GODOT_FIRST_PARTY_DYNAMIC_SCRIPT"],
  ["file-write", ["FileAccess.open(\"res://result.txt\", FileAccess.", "WRITE)"].join(""), "GODOT_FIRST_PARTY_FILESYSTEM_WRITE"],
  ["resource-save", ["ResourceSaver.", "save(Resource.new(), \"res://saved.tres\")"].join(""), "GODOT_FIRST_PARTY_FILESYSTEM_WRITE"],
];

for (const [name, source, expected] of cases) {
  test(`rejects ${name}`, (context) => {
    const root = fixture(source);
    context.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const report = auditGodotBoundary({ root });
    assert.equal(report.ok, false);
    assert.equal(codes(report).includes(expected), true);
    assert.deepEqual(Object.keys(report.violations[0]), ["code", "path"]);
  });
}

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { auditGodotBoundary } from "../scripts/check-godot-boundary.mjs";

function fixture(source, { vendorSource, splatVendorSource, unknownAddonSource } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-godot-boundary-"));
  fs.mkdirSync(path.join(root, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(root, "scripts", "case.gd"), source, "utf8");
  if (vendorSource) {
    const vendor = path.join(root, "addons", "gdUnit4");
    fs.mkdirSync(vendor, { recursive: true });
    fs.writeFileSync(path.join(vendor, "upstream.gd"), vendorSource, "utf8");
  }
  if (splatVendorSource) {
    const vendor = path.join(root, "addons", "gdgs");
    fs.mkdirSync(vendor, { recursive: true });
    fs.writeFileSync(path.join(vendor, "upstream.gd"), splatVendorSource, "utf8");
  }
  if (unknownAddonSource) {
    const vendor = path.join(root, "addons", "unknown");
    fs.mkdirSync(vendor, { recursive: true });
    fs.writeFileSync(path.join(vendor, "upstream.gd"), unknownAddonSource, "utf8");
  }
  return root;
}

function codes(report) {
  return report.violations.map((item) => item.code);
}

test("the committed first-party Godot source satisfies the active round boundary", () => {
  const report = auditGodotBoundary();
  assert.equal(report.ok, true);
  assert.equal(report.checked >= 2, true);
});

test("approved static res reads pass and only the two hash-locked vendor roots are excluded", (context) => {
  const root = fixture(
    "extends Node\nvar file = FileAccess.open(\"res://fixture.txt\", FileAccess.READ)\nvar scene = preload(\"res://fixture.tscn\")\n",
    {
      vendorSource: ["HTTP", "Client.new()"].join(""),
      splatVendorSource: ["ResourceSaver", ".save(Resource.new(), \"res://fixture.tres\")"].join(""),
    },
  );
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.deepEqual(auditGodotBoundary({ root }), { ok: true, checked: 1, violations: [] });
});

test("an unknown addon remains first-party and cannot bypass the source rules", (context) => {
  const root = fixture("extends Node\n", {
    unknownAddonSource: ["HTTP", "Client.new()"].join(""),
  });
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const report = auditGodotBoundary({ root });
  assert.equal(report.ok, false);
  assert.equal(codes(report).includes("GODOT_FIRST_PARTY_NETWORK"), true);
  assert.equal(report.violations[0].path, "addons/unknown/upstream.gd");
});

test("fixed runtime diagnostic pointers pass without allowing arbitrary absolute paths", (context) => {
  const root = fixture([
    "extends Node",
    "const DIAGNOSTIC_PATHS = [\"/prepared\", \"/snapshot/pack\", \"/actionId\", \"/spatialAssembly\"]",
  ].join("\n"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.equal(auditGodotBoundary({ root }).ok, true);
  fs.writeFileSync(
    path.join(root, "scripts", "case.gd"),
    "extends Node\nconst OUTSIDE = \"/opt/outside\"\n",
    "utf8",
  );
  assert.equal(codes(auditGodotBoundary({ root })).includes("GODOT_FIRST_PARTY_ABSOLUTE_PATH"), true);
});

test("only the approved runtime and Scene artifact loaders may open validated dynamic paths read-only", (context) => {
  const root = fixture("extends Node\n");
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const runtime = path.join(root, "runtime");
  fs.mkdirSync(runtime, { recursive: true });
  fs.writeFileSync(
    path.join(runtime, "runtime_artifact_loader.gd"),
    "extends RefCounted\nfunc read(approved_path):\n\treturn FileAccess.open(approved_path, FileAccess.READ)\n",
    "utf8",
  );
  assert.equal(auditGodotBoundary({ root }).ok, true);
  const sceneBinding = path.join(root, "scene_binding");
  fs.mkdirSync(sceneBinding, { recursive: true });
  fs.writeFileSync(
    path.join(sceneBinding, "scene_artifact_loader.gd"),
    "extends RefCounted\nfunc read(path):\n\treturn FileAccess.open(path, FileAccess.READ)\n",
    "utf8",
  );
  assert.equal(auditGodotBoundary({ root }).ok, true);
  fs.writeFileSync(
    path.join(root, "scripts", "case.gd"),
    "extends Node\nfunc read(approved_path):\n\treturn FileAccess.open(approved_path, FileAccess.READ)\n",
    "utf8",
  );
  assert.equal(codes(auditGodotBoundary({ root })).includes("GODOT_FIRST_PARTY_FILESYSTEM_WRITE"), true);
});

test("only the isolated analyzer may read and write its Node-owned temporary paths", (context) => {
  const root = fixture("extends Node\n");
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const analyzerRoot = path.join(root, "spatial_analysis");
  fs.mkdirSync(analyzerRoot, { recursive: true });
  fs.writeFileSync(
    path.join(analyzerRoot, "environment_analyzer.gd"),
    [
      "extends Node",
      "func transfer(path, paths):",
      "\tvar input = FileAccess.open(path, FileAccess.READ)",
      "\treturn FileAccess.open(paths[\"output\"], FileAccess.WRITE)",
    ].join("\n"),
    "utf8",
  );
  assert.equal(auditGodotBoundary({ root }).ok, true);

  fs.writeFileSync(
    path.join(analyzerRoot, "environment_analyzer.gd"),
    "extends Node\nfunc write(path):\n\treturn FileAccess.open(path, FileAccess.WRITE)\n",
    "utf8",
  );
  assert.equal(codes(auditGodotBoundary({ root })).includes("GODOT_FIRST_PARTY_FILESYSTEM_WRITE"), true);
});

test("only the isolated R14 verifier may read and write its Node-owned temporary paths", (context) => {
  const root = fixture("extends Node\n");
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const verifierRoot = path.join(root, "spatial_solution_verification");
  fs.mkdirSync(verifierRoot, { recursive: true });
  fs.writeFileSync(
    path.join(verifierRoot, "solution_verifier.gd"),
    [
      "extends Node",
      "const POINTERS = [\"/verificationRequest\", \"/placements/0\", \"/nodeContexts/0/actionTerminal\"]",
      "func transfer(path):",
      "\tvar input = FileAccess.open(path, FileAccess.READ)",
      "\treturn FileAccess.open(path, FileAccess.WRITE)",
    ].join("\n"),
    "utf8",
  );
  assert.equal(auditGodotBoundary({ root }).ok, true);
  fs.writeFileSync(
    path.join(root, "scripts", "case.gd"),
    "extends Node\nfunc write(path):\n\treturn FileAccess.open(path, FileAccess.WRITE)\n",
    "utf8",
  );
  assert.equal(codes(auditGodotBoundary({ root })).includes("GODOT_FIRST_PARTY_FILESYSTEM_WRITE"), true);
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

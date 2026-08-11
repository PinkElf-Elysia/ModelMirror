import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  parseSplatQualificationArguments,
  qualifyGodotSplat,
  SPLAT_FIXED_COMMIT,
  SPLAT_EXPECTED_VERSION,
} from "../scripts/lib/godot-splat-qualification-core.mjs";

function png(file) {
  const bytes = Buffer.alloc(24);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bytes);
  bytes.write("IHDR", 12, "ascii");
  bytes.writeUInt32BE(640, 16);
  bytes.writeUInt32BE(360, 20);
  fs.writeFileSync(file, bytes);
}

test("splat qualification parser requires one absolute external source and output", () => {
  const source = path.join(os.tmpdir(), "source");
  const output = path.join(os.tmpdir(), "output");
  assert.deepEqual(parseSplatQualificationArguments(["--source", source, "--output", output]), {source, output});
  assert.throws(() => parseSplatQualificationArguments(["--source", "relative", "--output", output]),
    /GODOT_SPLAT_QUALIFICATION_ARGUMENT_ERROR/u);
  assert.throws(() => parseSplatQualificationArguments(["--source", source]),
    /GODOT_SPLAT_QUALIFICATION_ARGUMENT_ERROR/u);
});

test("qualification reports the locked commit version mismatch without integrating it", () => {
  const trustedTemp = process.platform === "win32" ? path.join(path.parse(process.cwd()).root, "tmp") : os.tmpdir();
  const root = fs.mkdtempSync(path.join(trustedTemp, "matrix-oasis-splat-qualification-test-"));
  const source = path.join(root, "source");
  const output = path.join(root, "output");
  try {
    fs.mkdirSync(path.join(source, "addons", "gdgs"), {recursive: true});
    fs.mkdirSync(path.join(source, "samples", "assets"), {recursive: true});
    fs.writeFileSync(path.join(source, "addons", "gdgs", "plugin.cfg"), "[plugin]\nversion=\"3.3.0\"\n");
    fs.writeFileSync(path.join(source, "LICENSE"), "MIT\n");
    fs.writeFileSync(path.join(source, "samples", "assets", "demo.sog"), Buffer.from([1, 2, 3]));
    const fakeSpawn = (command, args) => {
      if (command === "git") {
        if (args.includes("status")) return {status: 0, stdout: "", stderr: ""};
        if (args.at(-1) === "HEAD") return {status: 0, stdout: `${SPLAT_FIXED_COMMIT}\n`, stderr: ""};
        return {status: 0, stdout: `${"a".repeat(40)}\n`, stderr: ""};
      }
      if (args.includes("res://tests/capture_demo.gd")) {
        png(args.at(-1));
      }
      return {status: 0, stdout: "ok\n", stderr: ""};
    };
    const report = qualifyGodotSplat({
      source,
      output,
      godotCommand: "godot-test-double",
      godotVersion: "4.6.3",
      spawn: fakeSpawn,
    });
    assert.equal(SPLAT_EXPECTED_VERSION, "3.2.0-beta");
    assert.equal(report.candidate.detectedVersion, "3.3.0");
    assert.equal(report.recommendation, "defer");
    assert.equal(report.reason, "LOCKED_VERSION_METADATA_MISMATCH");
    assert.equal(report.spzSupported, false);
    assert.equal(report.sourceUnchanged, true);
    assert.equal(report.checks.every((check) => check.status === "pass"), true);
    assert.equal(report.frame.width, 640);
    assert.equal(fs.existsSync(path.join(output, "qualification-report.json")), true);
  } finally {
    fs.rmSync(root, {recursive: true});
  }
});

test("qualification source never calls vendors or performs SPZ conversion", () => {
  const source = fs.readFileSync(new URL("../scripts/lib/godot-splat-qualification-core.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(source, /marble|meshy|api[_-]?key|credits/iu);
  assert.doesNotMatch(source, /convert.*spz|spz.*convert/iu);
  assert.match(source, /spzSupported: false/u);
});

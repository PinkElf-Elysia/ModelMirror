import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";
import {
  createRuntimePreviewArtifacts,
  GodotRuntimePreviewError,
  parseRuntimePreviewArguments,
  removeRuntimePreviewArtifacts,
  runtimePreviewGodotArguments,
} from "../scripts/prepare-godot-runtime.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const projectRoot = path.join(moduleRoot, "apps", "runtime-godot");

test("preview arguments accept only one frozen example selector", () => {
  assert.equal(
    parseRuntimePreviewArguments(["--example", "mechanics-conformance"]),
    "mechanics-conformance",
  );
  assert.equal(
    parseRuntimePreviewArguments(["--example", "last-train-r1"]),
    "last-train-r1",
  );
  for (const args of [
    [],
    ["--example"],
    ["--example", "unknown"],
    ["--example", "mechanics-conformance", "extra"],
    ["--unknown", "mechanics-conformance"],
  ]) {
    assert.throws(
      () => parseRuntimePreviewArguments(args),
      (error) => error instanceof GodotRuntimePreviewError &&
        error.code === "GODOT_RUNTIME_PREVIEW_ARGUMENT_ERROR",
    );
  }
});

test("Godot arguments select only the independent Runtime Lab scene and local pair", () => {
  const runtimePath = path.join(path.parse(moduleRoot).root, "tmp", "runtime.json");
  const receiptPath = path.join(path.parse(moduleRoot).root, "tmp", "receipt.json");
  assert.deepEqual(runtimePreviewGodotArguments({ projectRoot, runtimePath, receiptPath }), [
    "--path",
    projectRoot,
    "res://runtime/runtime_lab.tscn",
    "--",
    `--matrix-oasis-runtime-pack=${runtimePath}`,
    `--matrix-oasis-runtime-receipt=${receiptPath}`,
  ]);
  assert.deepEqual(runtimePreviewGodotArguments({
    projectRoot,
    runtimePath,
    receiptPath,
    smoke: true,
  }), [
    "--headless",
    "--path",
    projectRoot,
    "res://runtime/runtime_lab.tscn",
    "--",
    `--matrix-oasis-runtime-pack=${runtimePath}`,
    `--matrix-oasis-runtime-receipt=${receiptPath}`,
    "--matrix-oasis-runtime-smoke",
  ]);
});

test("both frozen examples compile to temporary canonical pairs and clean narrowly", async () => {
  for (const example of ["mechanics-conformance", "last-train-r1"]) {
    const artifacts = await createRuntimePreviewArtifacts({
      moduleRoot,
      example,
      compileAuthoringGamePackJson,
      canonicalizeJsonValue,
    });
    assert.equal(path.basename(artifacts.temporaryRoot).startsWith("matrix-oasis-r5-preview-"), true);
    assert.equal(fs.readFileSync(artifacts.runtimePath, "utf8"), artifacts.runtimeText);
    assert.equal(fs.readFileSync(artifacts.receiptPath, "utf8"), artifacts.receiptText);
    const report = await validateRuntimeGamePackJson(artifacts.runtimeText, artifacts.receiptText);
    assert.equal(report.valid, true);
    removeRuntimePreviewArtifacts(artifacts.temporaryRoot, {
      moduleRoot,
      identity: artifacts.identity,
    });
    assert.equal(fs.existsSync(artifacts.temporaryRoot), false);
  }
});

test("compile faults stay static and never expose dynamic error text", async () => {
  const sentinel = ["preview", "private", "value"].join("-");
  await assert.rejects(
    createRuntimePreviewArtifacts({
      moduleRoot,
      example: "mechanics-conformance",
      compileAuthoringGamePackJson: async () => {
        throw new Error(sentinel);
      },
      canonicalizeJsonValue,
    }),
    (error) => error instanceof GodotRuntimePreviewError &&
      error.code === "GODOT_RUNTIME_PREVIEW_COMPILE_FAILED" &&
      !String(error).includes(sentinel),
  );
});

test("cleanup rejects an unowned directory without removing it", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-unowned-preview-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const sentinelPath = path.join(root, "sentinel.txt");
  fs.writeFileSync(sentinelPath, "keep", "utf8");
  assert.throws(
    () => removeRuntimePreviewArtifacts(root, { moduleRoot }),
    (error) => error instanceof GodotRuntimePreviewError &&
      error.code === "GODOT_RUNTIME_PREVIEW_CLEANUP_INVALID",
  );
  assert.equal(fs.readFileSync(sentinelPath, "utf8"), "keep");
});

test("cleanup preserves a same-name replacement with a different identity", async (context) => {
  const artifacts = await createRuntimePreviewArtifacts({
    moduleRoot,
    example: "mechanics-conformance",
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
  });
  const movedRoot = `${artifacts.temporaryRoot}-moved`;
  fs.renameSync(artifacts.temporaryRoot, movedRoot);
  fs.mkdirSync(artifacts.temporaryRoot);
  const sentinelPath = path.join(artifacts.temporaryRoot, "sentinel.txt");
  fs.writeFileSync(sentinelPath, "keep", "utf8");
  context.after(() => {
    fs.rmSync(artifacts.temporaryRoot, { recursive: true, force: true });
    fs.rmSync(movedRoot, { recursive: true, force: true });
  });
  assert.throws(
    () => removeRuntimePreviewArtifacts(artifacts.temporaryRoot, {
      moduleRoot,
      identity: artifacts.identity,
    }),
    (error) => error instanceof GodotRuntimePreviewError &&
      error.code === "GODOT_RUNTIME_PREVIEW_CLEANUP_INVALID",
  );
  assert.equal(fs.readFileSync(sentinelPath, "utf8"), "keep");
});

test("Runtime Lab keeps native controls, responsive structure, and topic-independent source", () => {
  const script = fs.readFileSync(
    path.join(projectRoot, "runtime", "runtime_lab.gd"),
    "utf8",
  );
  const scene = fs.readFileSync(
    path.join(projectRoot, "runtime", "runtime_lab.tscn"),
    "utf8",
  );
  assert.equal(script.includes("MATRIX_OASIS_R5_GODOT_RUNTIME_READY"), true);
  assert.equal(script.includes("Button.new()"), true);
  assert.equal(script.includes("Control.FOCUS_ALL"), true);
  assert.equal(script.includes("NARROW_WIDTH := 720.0"), true);
  assert.equal(scene.includes('path="res://scenes/bootstrap.tscn"'), true);
  assert.equal(scene.includes("custom_minimum_size = Vector2(0, 44)"), true);
  assert.equal(scene.includes('type="ScrollContainer"'), true);
  for (const forbidden of [
    "last-train",
    "ending-return",
    "ending-stay",
    "ending-loop",
    "fetch(",
    "get_environment",
  ]) {
    assert.equal(`${script}\n${scene}`.includes(forbidden), false);
  }
});

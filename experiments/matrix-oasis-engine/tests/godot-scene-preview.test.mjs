import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import {
  GodotSceneHarnessError,
  parseSceneExampleArguments,
  SCENE_READY_MARKER,
  sceneGodotArguments,
} from "../scripts/lib/godot-scene-core.mjs";
import { createScenePreviewArtifacts, removeScenePreviewArtifacts } from "../scripts/prepare-godot-scene.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("scene preview accepts only the two frozen example selectors", () => {
  assert.equal(parseSceneExampleArguments(["--example", "mechanics-conformance"]), "mechanics-conformance");
  assert.equal(parseSceneExampleArguments(["--example", "last-train-r1"]), "last-train-r1");
  for (const args of [[], ["--example", "unknown"], ["--example", "mechanics-conformance", "extra"]]) {
    assert.throws(() => parseSceneExampleArguments(args), (error) =>
      error instanceof GodotSceneHarnessError && error.code === "GODOT_SCENE_ARGUMENT_ERROR");
  }
});

test("Godot arguments bind the independent scene lab to exactly three local files", () => {
  const temporaryRoot = process.platform === "win32" ? path.join(path.parse(moduleRoot).root, "tmp") : os.tmpdir();
  const root = path.join(temporaryRoot, "scene-preview-fixture");
  const args = sceneGodotArguments({
    projectRoot: path.join(root, "project"),
    runtimePath: path.join(root, "runtime.json"),
    receiptPath: path.join(root, "receipt.json"),
    scenePath: path.join(root, "scene.json"),
    smoke: true,
  });
  assert.deepEqual(args.slice(0, 4), ["--headless", "--path", path.join(root, "project"), "res://scene_binding/scene_lab.tscn"]);
  assert.equal(args.filter((item) => item.startsWith("--matrix-oasis-")).length, 4);
  assert.equal(SCENE_READY_MARKER, "MATRIX_OASIS_R7_SCENE_BINDING_READY");
});

test("both preview candidates are canonical, self-validated, and confined to temporary roots", async () => {
  for (const example of ["mechanics-conformance", "last-train-r1"]) {
    const artifacts = await createScenePreviewArtifacts({
      moduleRoot,
      example,
      compileAuthoringGamePackJson,
      canonicalizeRuntimeJsonValue: canonicalizeJsonValue,
    });
    try {
      const sceneText = fs.readFileSync(artifacts.scenePath, "utf8");
      assert.equal(sceneText, artifacts.sceneText);
      assert.equal(sceneText.endsWith("\n"), false);
      const report = await validateScenePackJson(sceneText, artifacts.runtimeText, artifacts.receiptText);
      assert.equal(report.valid, true);
      const names = fs.readdirSync(path.join(path.dirname(artifacts.scenePath), "assets")).sort();
      assert.deepEqual(names, ["Textures", "crate.glb", "figurine.glb", "floor-square.glb", "wall.glb"]);
      assert.equal(path.relative(artifacts.temporaryRoot, artifacts.scenePath).startsWith(".."), false);
    } finally {
      removeScenePreviewArtifacts(artifacts.temporaryRoot, {moduleRoot, identity: artifacts.identity});
    }
    assert.equal(fs.existsSync(artifacts.temporaryRoot), false);
  }
});

test("preview entrypoint imports only public package roots and the isolated scene harness", async () => {
  const source = await fs.promises.readFile(path.join(moduleRoot, "scripts", "preview-godot-scene.mjs"), "utf8");
  assert.match(source, /@matrix-oasis\/game-pack-compiler/u);
  assert.match(source, /\.\/prepare-godot-scene\.mjs/u);
  assert.equal(source.includes("/src/"), false);
  assert.equal(source.includes("fetch("), false);
});

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/scene-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import {
  createRuntimePreviewArtifacts,
  removeRuntimePreviewArtifacts,
} from "./prepare-godot-runtime.mjs";
import { buildScenePack, GodotSceneHarnessError, SCENE_ASSETS } from "./lib/godot-scene-core.mjs";

const KENNEY_TEXTURE = Object.freeze({
  path: "Textures/colormap.png",
  byteLength: 8706,
  sha256: "0d4947d34ff32acf4a359c7f22ca784e057e7e72f622170a9a77b6fc88fdb70e",
});

function fail(code) {
  throw new GodotSceneHarnessError(code);
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function copyLockedFile(sourceRoot, targetRoot, record) {
  const source = path.join(sourceRoot, ...record.path.split("/"));
  const target = path.join(targetRoot, ...record.path.split("/"));
  let bytes;
  try {
    bytes = fs.readFileSync(source);
  } catch {
    fail("GODOT_SCENE_ASSET_SOURCE_INVALID");
  }
  if (bytes.length !== record.byteLength || sha256(bytes) !== record.sha256) {
    fail("GODOT_SCENE_ASSET_SOURCE_INVALID");
  }
  fs.mkdirSync(path.dirname(target), {recursive: true});
  fs.writeFileSync(target, bytes, {flag: "wx"});
}

export async function createScenePreviewArtifacts({
  moduleRoot,
  example,
  compileAuthoringGamePackJson,
  canonicalizeRuntimeJsonValue,
}) {
  let runtimeArtifacts = null;
  try {
    runtimeArtifacts = await createRuntimePreviewArtifacts({
      moduleRoot,
      example,
      compileAuthoringGamePackJson,
      canonicalizeJsonValue: canonicalizeRuntimeJsonValue,
    });
    const runtimePack = JSON.parse(runtimeArtifacts.runtimeText);
    const receipt = JSON.parse(runtimeArtifacts.receiptText);
    const scenePack = buildScenePack({example, runtimePack, receipt});
    const sceneText = canonicalizeJsonValue(scenePack);
    const report = await validateScenePackJson(sceneText, runtimeArtifacts.runtimeText, runtimeArtifacts.receiptText);
    if (!report?.valid || report.diagnostics?.length !== 0) {
      fail("GODOT_SCENE_MANIFEST_INVALID");
    }
    const bundleRoot = path.join(runtimeArtifacts.temporaryRoot, "scene-bundle");
    const assetRoot = path.join(bundleRoot, "assets");
    fs.mkdirSync(assetRoot, {recursive: true});
    const sourceRoot = path.join(moduleRoot, "examples", "scene-bundles", "kenney-prototype", "assets");
    for (const asset of SCENE_ASSETS) {
      copyLockedFile(sourceRoot, assetRoot, {...asset, path: path.basename(asset.path)});
    }
    copyLockedFile(sourceRoot, assetRoot, KENNEY_TEXTURE);
    const scenePath = path.join(bundleRoot, "scene.json");
    fs.writeFileSync(scenePath, sceneText, {encoding: "utf8", flag: "wx"});
    return Object.freeze({...runtimeArtifacts, scenePath, sceneText});
  } catch (error) {
    if (runtimeArtifacts !== null) {
      try {
        removeRuntimePreviewArtifacts(runtimeArtifacts.temporaryRoot, {
          moduleRoot,
          identity: runtimeArtifacts.identity,
        });
      } catch {
        fail("GODOT_SCENE_CLEANUP_INVALID");
      }
    }
    if (error instanceof GodotSceneHarnessError) {
      throw error;
    }
    fail("GODOT_SCENE_PREPARE_INTERNAL_ERROR");
  }
}

export function removeScenePreviewArtifacts(temporaryRoot, options) {
  removeRuntimePreviewArtifacts(temporaryRoot, options);
}

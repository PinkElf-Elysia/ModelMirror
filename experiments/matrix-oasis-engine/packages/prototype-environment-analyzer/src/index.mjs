import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validatePrototypeSpatialEnvironmentBundleJson } from "@matrix-oasis/prototype-spatial-environment";
import {
  validatePrototypeEnvironmentFactsJson,
  validatePrototypeSpatialIntentJson,
} from "@matrix-oasis/prototype-spatial-planning-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const INTERNAL_CODE = "PROTOTYPE_SPATIAL_ANALYZER_INTERNAL_ERROR";
const ANALYZER_KIND = "matrix-oasis.godot-environment-analyzer/1";
const READY_MARKER = "MATRIX_OASIS_R13_SPATIAL_ANALYSIS_READY";
const COLLIDER_PATH = "assets/environment-collider.glb";
const SPLAT_PATH = "assets/environment.compressed.ply";
const analyzerState = new WeakMap();
const moduleRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const projectRoot = path.join(moduleRoot, "apps", "runtime-godot");

export class PrototypeEnvironmentAnalyzerOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "PrototypeEnvironmentAnalyzerOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function operational(error) {
  return error instanceof PrototypeEnvironmentAnalyzerOperationalError
    ? error
    : new PrototypeEnvironmentAnalyzerOperationalError();
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function exactRecord(value, keys) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) return null;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (Reflect.ownKeys(descriptors).some((key) => typeof key !== "string") || Object.keys(descriptors).length !== keys.length) return null;
  const output = {};
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) return null;
    output[key] = descriptor.value;
  }
  return output;
}

function copyFiles(value) {
  if (!(value instanceof Map) || value.size !== 2) return null;
  const output = new Map();
  for (const [key, bytes] of value) {
    if (![COLLIDER_PATH, SPLAT_PATH].includes(key) || !(bytes instanceof Uint8Array)) return null;
    output.set(key, Uint8Array.prototype.slice.call(bytes));
  }
  return output.size === 2 ? output : null;
}

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function staticFailure(code, pathValue) {
  return deepFreeze({
    ok: false,
    diagnostics: [{ phase: "input", severity: "error", code, path: pathValue, message: code }],
  });
}

function parseCanonical(text) {
  const value = JSON.parse(text);
  if (canonicalizeJsonValue(value) !== text) throw new PrototypeEnvironmentAnalyzerOperationalError();
  return value;
}

function hasSingleMarker(output) {
  return output.split(READY_MARKER).length - 1 === 1 &&
    !/\b(?:SCRIPT ERROR|ERROR:)\b/u.test(output);
}

export function createGodotEnvironmentAnalyzer(config) {
  try {
    const captured = exactRecord(config, ["godotBin"]);
    if (!captured || typeof captured.godotBin !== "string" || !path.isAbsolute(captured.godotBin)) throw new PrototypeEnvironmentAnalyzerOperationalError();
    const probe = spawnSync(captured.godotBin, ["--version"], {
      encoding: "utf8",
      shell: false,
      timeout: 5_000,
      windowsHide: true,
    });
    if (probe.error || probe.status !== 0 || !/(?:^|\D)4\.6\.3(?:\D|$)/u.test(`${probe.stdout ?? ""}${probe.stderr ?? ""}`)) {
      throw new PrototypeEnvironmentAnalyzerOperationalError();
    }
    const handle = Object.freeze({ kind: ANALYZER_KIND });
    analyzerState.set(handle, Object.freeze({ godotBin: captured.godotBin }));
    return handle;
  } catch (error) {
    throw operational(error);
  }
}

export async function analyzePrototypeEnvironment(request, analyzer) {
  let temporaryRoot = null;
  try {
    const state = analyzerState.get(analyzer);
    const captured = exactRecord(request, ["spatialIntentJson", "spatialEnvironmentBundleJson", "spatialEnvironmentFiles"]);
    const files = captured ? copyFiles(captured.spatialEnvironmentFiles) : null;
    if (!state || !captured || typeof captured.spatialIntentJson !== "string" || typeof captured.spatialEnvironmentBundleJson !== "string" || !files) {
      return staticFailure("PROTOTYPE_SPATIAL_ANALYZER_INPUT_INVALID", "");
    }
    const intentReport = validatePrototypeSpatialIntentJson(captured.spatialIntentJson);
    if (!intentReport.valid) return deepFreeze({ ok: false, diagnostics: intentReport.diagnostics });
    const environmentReport = await validatePrototypeSpatialEnvironmentBundleJson(captured.spatialEnvironmentBundleJson, files);
    if (!environmentReport.valid) return deepFreeze({ ok: false, diagnostics: environmentReport.diagnostics });
    const intent = parseCanonical(captured.spatialIntentJson);
    const environment = parseCanonical(captured.spatialEnvironmentBundleJson);
    if (intent.scene.id !== environment.scene.id || intent.scene.contentVersion !== environment.scene.contentVersion || intent.blueprint.canonicalSha256.replace(/^sha256:/u, "") !== environment.blueprint.canonicalSha256.replace(/^sha256:/u, "")) {
      return staticFailure("PROTOTYPE_SPATIAL_ANALYZER_IDENTITY_MISMATCH", "/spatialEnvironmentBundle");
    }
    const colliderBytes = files.get(COLLIDER_PATH);
    if (sha256(colliderBytes) !== `sha256:${environment.assets.collider.sha256}`) throw new PrototypeEnvironmentAnalyzerOperationalError();
    temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r13-analyzer-"));
    const colliderPath = path.join(temporaryRoot, "environment-collider.glb");
    const requestPath = path.join(temporaryRoot, "request.json");
    const outputPath = path.join(temporaryRoot, "raw-facts.json");
    await writeFile(colliderPath, colliderBytes, { flag: "wx" });
    const source = {
      scene: { ...intent.scene },
      blueprint: { ...intent.blueprint },
      runtime: { ...intent.runtime },
      spatialEnvironmentBundle: {
        format: environment.format,
        formatVersion: environment.formatVersion,
        canonicalSha256: sha256(captured.spatialEnvironmentBundleJson),
      },
      environmentBundleSha256: `sha256:${environment.source.environmentBundleSha256}`,
      collider: {
        format: "glb",
        byteLength: environment.assets.collider.byteLength,
        sha256: `sha256:${environment.assets.collider.sha256}`,
      },
      calibration: { ...environment.calibration },
    };
    const godotRequest = canonicalizeJsonValue({
      format: "matrix-oasis.godot-environment-analysis-request",
      formatVersion: "0.1.0",
      source,
      colliderPath,
    });
    await writeFile(requestPath, godotRequest, { encoding: "utf8", flag: "wx" });
    const result = spawnSync(state.godotBin, [
      "--headless",
      "--path",
      projectRoot,
      "res://spatial_analysis/environment_analyzer.tscn",
      "--",
      `--matrix-oasis-analysis-request=${requestPath}`,
      `--matrix-oasis-analysis-output=${outputPath}`,
    ], {
      cwd: moduleRoot,
      encoding: "utf8",
      maxBuffer: 8 * 1024 * 1024,
      shell: false,
      timeout: 180_000,
      windowsHide: true,
    });
    const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
    if (result.error || result.status !== 0 || !hasSingleMarker(output)) throw new PrototypeEnvironmentAnalyzerOperationalError();
    const raw = await readFile(outputPath, "utf8");
    if (Buffer.byteLength(raw, "utf8") > 16 * 1024 * 1024) throw new PrototypeEnvironmentAnalyzerOperationalError();
    const facts = JSON.parse(raw);
    const canonicalFactsJson = canonicalizeJsonValue(facts);
    const factsReport = validatePrototypeEnvironmentFactsJson(canonicalFactsJson);
    if (!factsReport.valid) throw new PrototypeEnvironmentAnalyzerOperationalError();
    const canonicalReportJson = canonicalizeJsonValue({
      format: "matrix-oasis.prototype-environment-analysis-report",
      formatVersion: "0.1.0",
      factsSha256: sha256(canonicalFactsJson),
      navigation: {
        vertexCount: facts.navigationMesh.verticesMm.length,
        polygonCount: facts.navigationMesh.polygons.length,
        componentCount: facts.navigationMesh.components.length,
      },
      anchors: { floorCount: facts.floorAnchors.length, wallCount: facts.wallAnchors.length },
      analyzer: { id: "godot-environment-analyzer", version: "0.1.0-r13", godotVersion: "4.6.3" },
    });
    return deepFreeze({ ok: true, facts, canonicalFactsJson, canonicalReportJson });
  } catch (error) {
    throw operational(error);
  } finally {
    if (temporaryRoot !== null) await rm(temporaryRoot, { recursive: true, force: true }).catch(() => {});
  }
}

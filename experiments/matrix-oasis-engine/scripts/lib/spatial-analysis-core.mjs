import { createHash } from "node:crypto";
import {
  lstat,
  mkdtemp,
  mkdir,
  open,
  realpath,
  readFile,
  rename,
  rm,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  analyzePrototypeEnvironment,
  createGodotEnvironmentAnalyzer,
} from "@matrix-oasis/prototype-environment-analyzer";
import {
  validatePrototypeEnvironmentFactsJson,
  validatePrototypeSpatialIntentJson,
} from "@matrix-oasis/prototype-spatial-planning-contracts";
import { validatePrototypeSpatialEnvironmentBundleJson } from "@matrix-oasis/prototype-spatial-environment";
import { validatePrototypeSpatialAssemblyJson } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const INTERNAL_CODE = "SPATIAL_ANALYSIS_CLI_INTERNAL_ERROR";
const SOURCE_FILES = Object.freeze({
  intent: "prototype-spatial-intent.json",
  bundle: "prototype-spatial-environment-bundle.json",
  collider: "assets/environment-collider.glb",
  splat: "assets/environment.compressed.ply",
  assembly: "spatial-assembly.json",
});
const OUTPUT_FILES = Object.freeze({
  facts: "prototype-environment-facts.json",
  report: "prototype-environment-analysis-report.json",
});
export const SPATIAL_ANALYSIS_OUTPUT_ROOT = path.resolve(process.platform === "win32" ? "C:\\tmp" : os.tmpdir());
const LIMITS = Object.freeze({
  intent: 2 * 1024 * 1024,
  bundle: 256 * 1024,
  collider: 32 * 1024 * 1024,
  splat: 96 * 1024 * 1024,
  assembly: 2 * 1024 * 1024,
  facts: 16 * 1024 * 1024,
  report: 256 * 1024,
});

const defaultServices = Object.freeze({
  lstat,
  mkdtemp,
  mkdir,
  openFile: open,
  realpath,
  readFile,
  rename,
  rm,
  validateIntent: validatePrototypeSpatialIntentJson,
  validateBundle: validatePrototypeSpatialEnvironmentBundleJson,
  validateAssembly: validatePrototypeSpatialAssemblyJson,
  validateFacts: validatePrototypeEnvironmentFactsJson,
  createAnalyzer: createGodotEnvironmentAnalyzer,
  runAnalysis: analyzePrototypeEnvironment,
});

export class SpatialAnalysisCliOperationalError extends Error {
  constructor(code = INTERNAL_CODE) {
    super(code);
    this.name = "SpatialAnalysisCliOperationalError";
    this.code = code;
  }
}

function fail(code = INTERNAL_CODE) {
  throw new SpatialAnalysisCliOperationalError(code);
}

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function encode(text) {
  return new TextEncoder().encode(text);
}

function decode(bytes, code) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    fail(code);
  }
}

function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function identity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint"
    ? Object.freeze({ dev: stat.dev, ino: stat.ino })
    : null;
}

function sameIdentity(stat, expected) {
  return expected !== null && stat.dev === expected.dev && stat.ino === expected.ino;
}

function fileState(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? Object.freeze({ size: stat.size, mtimeNs: stat.mtimeNs, ctimeNs: stat.ctimeNs })
    : null;
}

function sameFileState(stat, expected) {
  return expected !== null && stat.size === expected.size && stat.mtimeNs === expected.mtimeNs && stat.ctimeNs === expected.ctimeNs;
}

function canonical(text, code) {
  try {
    const value = JSON.parse(text);
    if (canonicalizeJsonValue(value) !== text) fail(code);
    return value;
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    fail(code);
  }
}

function externalTempRoot() {
  return SPATIAL_ANALYSIS_OUTPUT_ROOT;
}

function parsePairs(args, names, code) {
  if (!Array.isArray(args) || args.length !== names.size * 2) fail(code);
  const output = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = names.get(args[index]);
    const value = args[index + 1];
    if (!name || Object.hasOwn(output, name) || typeof value !== "string" || value.length === 0 || value.includes("\0")) fail(code);
    output[name] = path.resolve(value);
  }
  if (Object.keys(output).length !== names.size) fail(code);
  return Object.freeze(output);
}

export function parseSpatialAnalysisArguments(args) {
  return parsePairs(args, new Map([
    ["--spatial-environment-dir", "sourceDirectory"],
    ["--output", "outputDirectory"],
  ]), "SPATIAL_ANALYSIS_ARGUMENT_INVALID");
}

export function parseSpatialFactsCaptureArguments(args) {
  return parsePairs(args, new Map([
    ["--facts-dir", "factsDirectory"],
    ["--output", "outputDirectory"],
  ]), "SPATIAL_FACTS_CAPTURE_ARGUMENT_INVALID");
}

async function trustedDirectory(candidate, parent, services, code) {
  try {
    const absolute = path.resolve(candidate);
    const resolvedParent = path.resolve(parent);
    const resolved = path.resolve(await services.realpath(absolute));
    const stat = await services.lstat(absolute, { bigint: true });
    const observed = identity(stat);
    if (!contained(resolvedParent, absolute) || resolved !== absolute || stat.isSymbolicLink() || !stat.isDirectory() || observed === null) fail(code);
    return Object.freeze({ path: absolute, identity: observed });
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    fail(code);
  }
}

async function assertDirectory(record, parent, services, code) {
  const current = await trustedDirectory(record.path, parent, services, code);
  if (current.identity.dev !== record.identity.dev || current.identity.ino !== record.identity.ino) fail(code);
}

async function readStableFile(directory, relative, maximum, services, code) {
  const candidate = path.resolve(directory.path, ...relative.split("/"));
  if (!contained(directory.path, candidate) || path.relative(directory.path, candidate).split(path.sep).some((part) => part === "..")) fail(code);
  let handle = null;
  try {
    await assertDirectory(directory, path.dirname(directory.path), services, code);
    handle = await services.openFile(candidate, "r");
    const before = await handle.stat({ bigint: true });
    const observed = identity(before);
    const state = fileState(before);
    if (!before.isFile() || before.isSymbolicLink() || observed === null || state === null || before.size < 1n || before.size > BigInt(maximum)) fail(code);
    const resolved = path.resolve(await services.realpath(candidate));
    const linked = await services.lstat(candidate, { bigint: true });
    if (resolved !== candidate || !contained(directory.path, resolved) || linked.isSymbolicLink() || !linked.isFile() || !sameIdentity(linked, observed) || !sameFileState(linked, state)) fail(code);
    const output = new Uint8Array(Number(before.size));
    let offset = 0;
    while (offset < output.length) {
      const result = await handle.read(output, offset, output.length - offset, offset);
      if (!result || result.bytesRead < 1) fail(code);
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, output.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameIdentity(after, observed) || !sameFileState(after, state)) fail(code);
    return output;
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    fail(code);
  } finally {
    if (handle !== null) await handle.close().catch(() => {});
  }
}

async function readOptionalStableFile(directory, relative, maximum, services, code) {
  const candidate = path.resolve(directory.path, ...relative.split("/"));
  try {
    await services.lstat(candidate, { bigint: true });
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    fail(code);
  }
  return readStableFile(directory, relative, maximum, services, code);
}

async function loadSource(sourceDirectory, services) {
  const source = await trustedDirectory(sourceDirectory, path.dirname(sourceDirectory), services, "SPATIAL_ANALYSIS_SOURCE_INVALID");
  const intentJson = decode(await readStableFile(source, SOURCE_FILES.intent, LIMITS.intent, services, "SPATIAL_ANALYSIS_SOURCE_INVALID"), "SPATIAL_ANALYSIS_SOURCE_INVALID");
  const bundleJson = decode(await readStableFile(source, SOURCE_FILES.bundle, LIMITS.bundle, services, "SPATIAL_ANALYSIS_SOURCE_INVALID"), "SPATIAL_ANALYSIS_SOURCE_INVALID");
  canonical(intentJson, "SPATIAL_ANALYSIS_SOURCE_INVALID");
  canonical(bundleJson, "SPATIAL_ANALYSIS_SOURCE_INVALID");
  const files = new Map([
    ["assets/environment-collider.glb", await readStableFile(source, SOURCE_FILES.collider, LIMITS.collider, services, "SPATIAL_ANALYSIS_SOURCE_INVALID")],
    ["assets/environment.compressed.ply", await readStableFile(source, SOURCE_FILES.splat, LIMITS.splat, services, "SPATIAL_ANALYSIS_SOURCE_INVALID")],
  ]);
  const assemblyBytes = await readOptionalStableFile(source, SOURCE_FILES.assembly, LIMITS.assembly, services, "SPATIAL_ANALYSIS_SOURCE_INVALID");
  const assemblyJson = assemblyBytes === null ? null : decode(assemblyBytes, "SPATIAL_ANALYSIS_SOURCE_INVALID");
  if (assemblyJson !== null) canonical(assemblyJson, "SPATIAL_ANALYSIS_SOURCE_INVALID");
  const intentReport = services.validateIntent(intentJson);
  const bundleReport = await services.validateBundle(bundleJson, files);
  const assemblyReport = assemblyJson === null ? null : services.validateAssembly(assemblyJson);
  if (!intentReport?.valid || !bundleReport?.valid || (assemblyReport !== null && !assemblyReport?.valid)) fail("SPATIAL_ANALYSIS_SOURCE_INVALID");
  return Object.freeze({ source, intentJson, bundleJson, files, assemblyJson });
}

async function targetParent(outputDirectory, services, code) {
  const rootPath = externalTempRoot();
  const root = await trustedDirectory(rootPath, path.dirname(rootPath), services, code);
  const candidate = path.resolve(outputDirectory);
  if (path.dirname(candidate) !== root.path || candidate === root.path) fail(code);
  try {
    await services.lstat(candidate, { bigint: true });
    fail(code);
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    if (error?.code !== "ENOENT") fail(code);
  }
  return Object.freeze({ root, candidate });
}

async function writeStableFile(directory, name, bytes, services, code) {
  const destination = path.join(directory.path, name);
  let handle = null;
  try {
    await assertDirectory(directory, path.dirname(directory.path), services, code);
    handle = await services.openFile(destination, "wx+");
    const before = await handle.stat({ bigint: true });
    const observed = identity(before);
    if (!before.isFile() || before.isSymbolicLink() || observed === null) fail(code);
    let offset = 0;
    while (offset < bytes.length) {
      const result = await handle.write(bytes, offset, bytes.length - offset, offset);
      if (!result || result.bytesWritten < 1) fail(code);
      offset += result.bytesWritten;
    }
    await handle.sync();
    const readback = new Uint8Array(bytes.length);
    offset = 0;
    while (offset < readback.length) {
      const result = await handle.read(readback, offset, readback.length - offset, offset);
      if (!result || result.bytesRead < 1) fail(code);
      offset += result.bytesRead;
    }
    if (sha256(readback) !== sha256(bytes)) fail(code);
    const linked = await services.lstat(destination, { bigint: true });
    if (!linked.isFile() || linked.isSymbolicLink() || !sameIdentity(linked, observed)) fail(code);
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    fail(code);
  } finally {
    if (handle !== null) await handle.close().catch(() => {});
  }
}

async function publishPair(outputDirectory, files, services, code) {
  const target = await targetParent(outputDirectory, services, code);
  const stagingPath = await services.mkdtemp(path.join(target.root.path, ".matrix-oasis-r13-analysis-"));
  const staging = await trustedDirectory(stagingPath, target.root.path, services, code);
  let published = false;
  let renamed = false;
  try {
    for (const [name, bytes] of files) await writeStableFile(staging, name, bytes, services, code);
    await assertDirectory(staging, target.root.path, services, code);
    await services.rename(staging.path, target.candidate);
    renamed = true;
    const final = await trustedDirectory(target.candidate, target.root.path, services, code);
    if (final.identity.dev !== staging.identity.dev || final.identity.ino !== staging.identity.ino) fail(code);
    for (const [name, bytes] of files) {
      const observed = await readStableFile(final, name, Math.max(bytes.length, 1), services, code);
      if (sha256(observed) !== sha256(bytes)) fail(code);
    }
    published = true;
    return final.path;
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    fail(code);
  } finally {
    if (!published) {
      try {
        const cleanupPath = renamed ? target.candidate : staging.path;
        const current = await trustedDirectory(cleanupPath, target.root.path, services, code);
        if (current.identity.dev === staging.identity.dev && current.identity.ino === staging.identity.ino) {
          await services.rm(cleanupPath, { recursive: true, force: true });
        }
      } catch {
        // Ambiguous staging ownership is deliberately left in place.
      }
    }
  }
}

export async function publishSpatialEnvironmentAnalysis({ sourceDirectory, outputDirectory, godotBin }, overrides = {}) {
  const services = Object.freeze({ ...defaultServices, ...overrides });
  try {
    if (typeof godotBin !== "string" || !path.isAbsolute(godotBin)) fail("SPATIAL_ANALYSIS_GODOT_INVALID");
    const source = await loadSource(path.resolve(sourceDirectory), services);
    const analyzer = services.createAnalyzer({ godotBin });
    const result = await services.runAnalysis({
      spatialIntentJson: source.intentJson,
      spatialEnvironmentBundleJson: source.bundleJson,
      spatialEnvironmentFiles: source.files,
      ...(source.assemblyJson === null ? {} : { spatialAssemblyJson: source.assemblyJson }),
    }, analyzer);
    if (!result?.ok || typeof result.canonicalFactsJson !== "string" || typeof result.canonicalReportJson !== "string") fail("SPATIAL_ANALYSIS_FAILED");
    if (encode(result.canonicalFactsJson).byteLength > LIMITS.facts || encode(result.canonicalReportJson).byteLength > LIMITS.report) fail("SPATIAL_ANALYSIS_OUTPUT_INVALID");
    canonical(result.canonicalFactsJson, "SPATIAL_ANALYSIS_OUTPUT_INVALID");
    const report = canonical(result.canonicalReportJson, "SPATIAL_ANALYSIS_OUTPUT_INVALID");
    if (!services.validateFacts(result.canonicalFactsJson)?.valid || report.factsSha256 !== sha256(encode(result.canonicalFactsJson))) fail("SPATIAL_ANALYSIS_OUTPUT_INVALID");
    const publishedDirectory = await publishPair(path.resolve(outputDirectory), new Map([
      [OUTPUT_FILES.facts, encode(result.canonicalFactsJson)],
      [OUTPUT_FILES.report, encode(result.canonicalReportJson)],
    ]), services, "SPATIAL_ANALYSIS_PUBLISH_FAILED");
    return Object.freeze({ publishedDirectory, factsSha256: report.factsSha256 });
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    fail();
  }
}

function svgForFacts(facts) {
  const points = facts.navigationMesh.verticesMm;
  const minimum = facts.environmentBounds.minimumMm;
  const maximum = facts.environmentBounds.maximumMm;
  const spanX = Math.max(1, maximum[0] - minimum[0]);
  const spanZ = Math.max(1, maximum[2] - minimum[2]);
  const project = (point) => [40 + Math.round((point[0] - minimum[0]) * 920 / spanX), 40 + Math.round((maximum[2] - point[2]) * 520 / spanZ)];
  const polygons = facts.navigationMesh.polygons.map((polygon) => {
    const value = polygon.vertexIndices.map((index) => project(points[index]).join(",")).join(" ");
    return `<polygon points="${value}" fill="none" stroke="#55d6be" stroke-width="2"/>`;
  }).join("");
  const floors = facts.floorAnchors.map((anchor) => {
    const [x, y] = project(anchor.positionMm);
    return `<circle cx="${x}" cy="${y}" r="3" fill="#f5c451"/>`;
  }).join("");
  const walls = facts.wallAnchors.map((anchor) => {
    const [x, y] = project(anchor.positionMm);
    return `<rect x="${x - 2}" y="${y - 2}" width="4" height="4" fill="#ee6c4d"/>`;
  }).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600"><rect width="1000" height="600" fill="#101722"/>${polygons}${floors}${walls}</svg>`;
}

export async function captureSpatialFacts({ factsDirectory, outputDirectory }, overrides = {}) {
  const services = Object.freeze({ ...defaultServices, ...overrides });
  try {
    const source = await trustedDirectory(path.resolve(factsDirectory), path.dirname(path.resolve(factsDirectory)), services, "SPATIAL_FACTS_CAPTURE_SOURCE_INVALID");
    const factsJson = decode(await readStableFile(source, OUTPUT_FILES.facts, LIMITS.facts, services, "SPATIAL_FACTS_CAPTURE_SOURCE_INVALID"), "SPATIAL_FACTS_CAPTURE_SOURCE_INVALID");
    const facts = canonical(factsJson, "SPATIAL_FACTS_CAPTURE_SOURCE_INVALID");
    if (!services.validateFacts(factsJson)?.valid) fail("SPATIAL_FACTS_CAPTURE_SOURCE_INVALID");
    const svg = svgForFacts(facts);
    const reportJson = canonicalizeJsonValue({
      format: "matrix-oasis.spatial-facts-capture-report",
      formatVersion: "0.1.0",
      factsSha256: sha256(encode(factsJson)),
      svgSha256: sha256(encode(svg)),
      navigation: {
        vertexCount: facts.navigationMesh.verticesMm.length,
        polygonCount: facts.navigationMesh.polygons.length,
        componentCount: facts.navigationMesh.components.length,
      },
      anchors: { floorCount: facts.floorAnchors.length, wallCount: facts.wallAnchors.length },
    });
    const publishedDirectory = await publishPair(path.resolve(outputDirectory), new Map([
      ["spatial-facts-plan.svg", encode(svg)],
      ["capture-report.json", encode(reportJson)],
    ]), services, "SPATIAL_FACTS_CAPTURE_PUBLISH_FAILED");
    return Object.freeze({ publishedDirectory, factsSha256: sha256(encode(factsJson)) });
  } catch (error) {
    if (error instanceof SpatialAnalysisCliOperationalError) throw error;
    fail();
  }
}

import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { lstat, mkdir, mkdtemp, open, realpath, rename, rmdir, unlink } from "node:fs/promises";
import path from "node:path";
import {
  materializePrototypeSpatialEnvironment,
  validatePrototypeSpatialEnvironmentBundleJson,
} from "@matrix-oasis/prototype-spatial-environment";

const ARGUMENTS = Object.freeze({
  "--environment-dir": "environmentDir",
  "--spz-file": "spzFile",
  "--output": "output",
  "--metric-scale-micros": "metricScaleMicros",
  "--ground-plane-offset-mm": "groundPlaneOffsetMm",
  "--translation-mm": "godotTranslationMm",
  "--rotation-mdeg": "godotRotationMilliDegrees",
});
const OUTPUT_NAME = /^[a-z0-9][a-z0-9._-]{0,127}$/u;
const MAXIMUMS = Object.freeze({
  manifest: 256 * 1024,
  spz: 64 * 1024 * 1024,
  panorama: 64 * 1024 * 1024,
  collider: 32 * 1024 * 1024,
  splat: 96 * 1024 * 1024,
});
const OUTPUTS = Object.freeze([
  "prototype-spatial-environment-bundle.json",
  "prototype-spatial-environment-report.json",
  "assets/environment.compressed.ply",
  "assets/environment-collider.glb",
]);
const encoder = new TextEncoder();

export class SpatialEnvironmentQualificationOperationalError extends Error {
  constructor(code = "SPATIAL_ENVIRONMENT_QUALIFICATION_INTERNAL_ERROR") {
    super(code);
    this.name = "SpatialEnvironmentQualificationOperationalError";
    this.code = code;
  }
}

function fail(code = "SPATIAL_ENVIRONMENT_QUALIFICATION_INTERNAL_ERROR") {
  throw new SpatialEnvironmentQualificationOperationalError(code);
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
  return expected && stat.dev === expected.dev && stat.ino === expected.ino;
}

function fileState(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? Object.freeze({ size: stat.size, mtimeNs: stat.mtimeNs, ctimeNs: stat.ctimeNs })
    : null;
}

function sameFileState(stat, expected) {
  return expected && stat.size === expected.size && stat.mtimeNs === expected.mtimeNs && stat.ctimeNs === expected.ctimeNs;
}

function parseInteger(value, minimum, maximum) {
  if (typeof value !== "string" || !/^-?(?:0|[1-9][0-9]*)$/u.test(value)) fail("SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) fail("SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
  return Object.is(parsed, -0) ? 0 : parsed;
}

function parseVector(value, minimum, maximum) {
  if (typeof value !== "string") fail("SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
  const parts = value.split(",");
  if (parts.length !== 3) fail("SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
  return Object.freeze(parts.map((part) => parseInteger(part, minimum, maximum)));
}

export function parseSpatialEnvironmentQualificationArgs(args, temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 14 || !path.isAbsolute(temporaryRoot)) {
    fail("SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
  }
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const key = ARGUMENTS[args[index]];
    const value = args[index + 1];
    if (!key || key in values || typeof value !== "string" || value.length === 0 || value.includes("\0")) {
      fail("SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
    }
    values[key] = value;
  }
  const environmentDir = path.resolve(values.environmentDir);
  const spzFile = path.resolve(values.spzFile);
  const output = path.resolve(values.output);
  const temp = path.resolve(temporaryRoot);
  if (!path.isAbsolute(environmentDir) || !path.isAbsolute(spzFile) || path.dirname(output) !== temp ||
      !OUTPUT_NAME.test(path.basename(output))) fail("SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
  return Object.freeze({
    environmentDir,
    spzFile,
    output,
    calibration: Object.freeze({
      coordinateTransform: "spz-raw-ply-to-godot-v1",
      metricScaleMicros: parseInteger(values.metricScaleMicros, 1, 100_000_000),
      groundPlaneOffsetMm: parseInteger(values.groundPlaneOffsetMm, -1_000_000, 1_000_000),
      godotTranslationMm: parseVector(values.godotTranslationMm, -1_000_000, 1_000_000),
      godotRotationMilliDegrees: parseVector(values.godotRotationMilliDegrees, -360_000, 360_000),
    }),
  });
}

async function exists(candidate, services) {
  try { await services.lstat(candidate, { bigint: true }); return true; }
  catch (error) { if (error?.code === "ENOENT") return false; throw error; }
}

async function trustedDirectory(candidate, parent, services, code) {
  try {
    const absolute = path.resolve(candidate);
    const resolved = path.resolve(await services.realpath(absolute));
    const stat = await services.lstat(absolute, { bigint: true });
    const observed = identity(stat);
    if (resolved !== absolute || !contained(parent, resolved) || !stat.isDirectory() || stat.isSymbolicLink() || !observed) fail(code);
    return Object.freeze({ path: absolute, identity: observed });
  } catch (error) {
    if (error instanceof SpatialEnvironmentQualificationOperationalError) throw error;
    fail(code);
  }
}

async function assertDirectory(directory, parent, services, code) {
  const current = await trustedDirectory(directory.path, parent, services, code);
  if (current.identity.dev !== directory.identity.dev || current.identity.ino !== directory.identity.ino) fail(code);
}

async function readStableFile(candidate, maximum, services, code) {
  let handle;
  try {
    const absolute = path.resolve(candidate);
    handle = await services.openFile(absolute, "r");
    const before = await handle.stat({ bigint: true });
    const observed = identity(before); const state = fileState(before);
    if (!before.isFile() || before.isSymbolicLink() || !observed || !state || before.size < 1n || before.size > BigInt(maximum)) fail(code);
    const linked = await services.lstat(absolute, { bigint: true });
    const resolved = path.resolve(await services.realpath(absolute));
    if (resolved !== absolute || linked.isSymbolicLink() || !linked.isFile() || !sameIdentity(linked, observed) || !sameFileState(linked, state)) fail(code);
    const output = new Uint8Array(Number(before.size)); let offset = 0;
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
    if (error instanceof SpatialEnvironmentQualificationOperationalError) throw error;
    fail(code);
  } finally {
    if (handle) await handle.close().catch(() => {});
  }
}

function decode(bytes, code) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { fail(code); }
}

function exactSuccess(result) {
  return result && typeof result === "object" && Object.getPrototypeOf(result) === Object.prototype &&
    Reflect.ownKeys(result).length === 5 && ["ok", "bundle", "canonicalBundleJson", "canonicalReportJson", "files"].every((key) => Object.hasOwn(result, key)) &&
    result.ok === true && typeof result.canonicalBundleJson === "string" && typeof result.canonicalReportJson === "string" &&
    Array.isArray(result.files) && result.files.length === 2;
}

async function writeArtifact(parent, name, bytes, services, code) {
  await assertDirectory(parent.directory, parent.parent, services, code);
  const candidate = path.join(parent.directory.path, name);
  const handle = await services.openFile(candidate, "wx+");
  try {
    const opened = await handle.stat({ bigint: true }); const observed = identity(opened);
    const linked = await services.lstat(candidate, { bigint: true }); const resolved = path.resolve(await services.realpath(candidate));
    if (!observed || !opened.isFile() || linked.isSymbolicLink() || resolved !== candidate || !sameIdentity(linked, observed)) fail(code);
    await handle.writeFile(bytes); await handle.sync();
    const output = new Uint8Array(bytes.length); let offset = 0;
    while (offset < output.length) {
      const result = await handle.read(output, offset, output.length - offset, offset);
      if (!result || result.bytesRead < 1) fail(code);
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, output.length);
    const final = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !observed || !sameIdentity(final, observed) ||
        !output.every((byte, index) => byte === bytes[index])) fail(code);
    return Object.freeze({ path: candidate, identity: observed });
  } finally { await handle.close().catch(() => {}); }
}

async function assertArtifact(record, candidate, bytes, services, code) {
  let handle;
  try {
    handle = await services.openFile(candidate, "r");
    const opened = await handle.stat({ bigint: true });
    const linked = await services.lstat(candidate, { bigint: true });
    const resolved = path.resolve(await services.realpath(candidate));
    if (!opened.isFile() || linked.isSymbolicLink() || resolved !== candidate ||
        !sameIdentity(opened, record.identity) || !sameIdentity(linked, record.identity) ||
        opened.size !== BigInt(bytes.length)) fail(code);
    const output = new Uint8Array(bytes.length); let offset = 0;
    while (offset < output.length) {
      const result = await handle.read(output, offset, output.length - offset, offset);
      if (!result || result.bytesRead < 1) fail(code);
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, output.length);
    const final = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameIdentity(final, record.identity) ||
        !output.every((byte, index) => byte === bytes[index])) fail(code);
  } catch (error) {
    if (error instanceof SpatialEnvironmentQualificationOperationalError) throw error;
    fail(code);
  } finally { if (handle) await handle.close().catch(() => {}); }
}

async function publish(output, artifacts, temporaryRoot, services) {
  if (await exists(output, services)) fail("SPATIAL_ENVIRONMENT_QUALIFICATION_OUTPUT_EXISTS");
  let stage; let stageRecord; let assetsRecord; const fileRecords = [];
  try {
    stage = await services.mkdtemp(path.join(temporaryRoot, ".matrix-oasis-r11-spatial-"));
    stageRecord = await trustedDirectory(stage, temporaryRoot, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    const assetsPath = path.join(stageRecord.path, "assets");
    await services.mkdir(assetsPath, { recursive: false });
    assetsRecord = await trustedDirectory(assetsPath, stageRecord.path, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    for (const artifact of artifacts) {
      const nested = artifact.path.startsWith("assets/");
      const directory = nested ? assetsRecord : stageRecord;
      fileRecords.push(await writeArtifact({ directory, parent: nested ? stageRecord.path : temporaryRoot },
        nested ? artifact.path.slice(7) : artifact.path, artifact.bytes, services,
        "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED"));
    }
    await assertDirectory(stageRecord, temporaryRoot, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    await assertDirectory(assetsRecord, stageRecord.path, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    for (let index = 0; index < artifacts.length; index += 1) {
      await assertArtifact(fileRecords[index], fileRecords[index].path, artifacts[index].bytes, services,
        "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    }
    await services.rename(stageRecord.path, output); stage = undefined;
    const published = await trustedDirectory(output, temporaryRoot, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    if (published.identity.dev !== stageRecord.identity.dev || published.identity.ino !== stageRecord.identity.ino) fail("SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    const publishedAssets = await trustedDirectory(path.join(output, "assets"), output, services,
      "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    if (publishedAssets.identity.dev !== assetsRecord.identity.dev || publishedAssets.identity.ino !== assetsRecord.identity.ino) {
      fail("SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    }
    for (let index = 0; index < artifacts.length; index += 1) {
      await assertArtifact(fileRecords[index], path.join(output, artifacts[index].path), artifacts[index].bytes, services,
        "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
    }
  } finally {
    if (stage && stageRecord && assetsRecord && fileRecords.length === artifacts.length) {
      try {
        await assertDirectory(stageRecord, temporaryRoot, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
        await assertDirectory(assetsRecord, stageRecord.path, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
        for (let index = 0; index < artifacts.length; index += 1) {
          await assertArtifact(fileRecords[index], fileRecords[index].path, artifacts[index].bytes, services,
            "SPATIAL_ENVIRONMENT_QUALIFICATION_PUBLISH_FAILED");
        }
        for (const record of fileRecords) await services.unlink(record.path);
        await services.rmdir(assetsRecord.path);
        await services.rmdir(stageRecord.path);
      } catch { /* preserve ambiguous staging for inspection */ }
    }
  }
}

export async function executeSpatialEnvironmentQualification({ args, temporaryRoot, services, materialize, validate }) {
  try {
    const parsed = parseSpatialEnvironmentQualificationArgs(args, temporaryRoot);
    const tempReal = path.resolve(await services.realpath(temporaryRoot));
    const environmentParent = path.dirname(parsed.environmentDir);
    const environmentParentReal = path.resolve(await services.realpath(environmentParent));
    const environment = await trustedDirectory(parsed.environmentDir, environmentParentReal, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_INPUT_INVALID");
    const environmentJson = decode(await readStableFile(path.join(environment.path, "prototype-environment-bundle.json"), MAXIMUMS.manifest, services,
      "SPATIAL_ENVIRONMENT_QUALIFICATION_INPUT_INVALID"), "SPATIAL_ENVIRONMENT_QUALIFICATION_INPUT_INVALID");
    const panorama = await readStableFile(path.join(environment.path, "assets", "environment-panorama.png"), MAXIMUMS.panorama, services,
      "SPATIAL_ENVIRONMENT_QUALIFICATION_INPUT_INVALID");
    const collider = await readStableFile(path.join(environment.path, "assets", "environment-collider.glb"), MAXIMUMS.collider, services,
      "SPATIAL_ENVIRONMENT_QUALIFICATION_INPUT_INVALID");
    const spz = await readStableFile(parsed.spzFile, MAXIMUMS.spz, services, "SPATIAL_ENVIRONMENT_QUALIFICATION_INPUT_INVALID");
    const result = await materialize({ environmentBundleJson: environmentJson,
      environmentFiles: new Map([["assets/environment-panorama.png", panorama], ["assets/environment-collider.glb", collider]]),
      spzBytes: spz, calibration: parsed.calibration });
    if (!result?.ok) {
      const code = result?.diagnostics?.[0]?.code;
      return Object.freeze({ exitCode: 1, stdout: "", stderr: `${typeof code === "string" && /^[A-Z][A-Z0-9_]+$/u.test(code) ? code : "SPATIAL_ENVIRONMENT_QUALIFICATION_CONTENT_INVALID"}\n` });
    }
    if (!exactSuccess(result)) fail();
    const files = new Map(result.files.map((file) => [file.path, file.bytes]));
    const validation = await validate(result.canonicalBundleJson, files);
    if (!validation?.valid) fail();
    const splat = files.get("assets/environment.compressed.ply");
    const publishedCollider = files.get("assets/environment-collider.glb");
    if (!(splat instanceof Uint8Array) || splat.length > MAXIMUMS.splat || !(publishedCollider instanceof Uint8Array)) fail();
    const artifacts = Object.freeze([
      Object.freeze({ path: OUTPUTS[0], bytes: encoder.encode(result.canonicalBundleJson) }),
      Object.freeze({ path: OUTPUTS[1], bytes: encoder.encode(result.canonicalReportJson) }),
      Object.freeze({ path: OUTPUTS[2], bytes: Uint8Array.from(splat) }),
      Object.freeze({ path: OUTPUTS[3], bytes: Uint8Array.from(publishedCollider) }),
    ]);
    await publish(parsed.output, artifacts, tempReal, services);
    const digest = createHash("sha256").update(artifacts[2].bytes).digest("hex");
    return Object.freeze({ exitCode: 0, stdout: `SPATIAL_ENVIRONMENT_QUALIFIED files=2 splatSha256=${digest}\n`, stderr: "" });
  } catch (error) {
    const code = error instanceof SpatialEnvironmentQualificationOperationalError ? error.code : "SPATIAL_ENVIRONMENT_QUALIFICATION_INTERNAL_ERROR";
    return Object.freeze({ exitCode: 2, stdout: "", stderr: `${code}\n` });
  }
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const temporaryRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
  const result = await executeSpatialEnvironmentQualification({ args: process.argv.slice(2), temporaryRoot,
    services: { lstat, mkdir, mkdtemp, openFile: open, realpath, rename, rmdir, unlink },
    materialize: materializePrototypeSpatialEnvironment, validate: validatePrototypeSpatialEnvironmentBundleJson });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}

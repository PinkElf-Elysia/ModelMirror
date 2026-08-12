import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  realpath,
  rename,
  rm,
} from "node:fs/promises";
import path from "node:path";
import {
  MARBLE_PROVIDER_ENDPOINT,
  createMarbleWorldProvider,
  materializePrototypeEnvironment,
  planPrototypeEnvironment,
  validatePrototypeEnvironmentBundleJson,
} from "@matrix-oasis/prototype-environment-pipeline";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const BLUEPRINT_NAME = "scene-blueprint.json";
const BLUEPRINT_LIMIT = 1024 * 1024;
const OUTPUT_NAME = /^[a-z0-9][a-z0-9._-]{0,127}$/u;
const SAFE_CODE = /^[A-Z][A-Z0-9_]{2,127}$/u;
const SAFE_PATH = /^\/(?:[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*)?$/u;
const OFFICIAL_ASSET_HOSTS = Object.freeze([
  "assets.worldlabs.ai",
  "cdn.marble.worldlabs.ai",
  "cdn.worldlabs.ai",
  "storage.cloud.google.com",
  "storage.googleapis.com",
]);
const OUTPUT_FILES = Object.freeze([
  "prototype-environment-bundle.json",
  "prototype-environment-report.json",
  "assets/environment-panorama.png",
  "assets/environment-collider.glb",
]);

export class MarbleQualificationOperationalError extends Error {
  constructor(code = "MARBLE_QUALIFICATION_INTERNAL_ERROR") {
    super(code);
    this.name = "MarbleQualificationOperationalError";
    this.code = code;
  }
}

function fail(code = "MARBLE_QUALIFICATION_INTERNAL_ERROR") {
  throw new MarbleQualificationOperationalError(code);
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype &&
    Reflect.ownKeys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
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

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

async function exists(candidate, services) {
  try {
    await services.lstat(candidate, { bigint: true });
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function trustedDirectory(candidate, tempRoot, services) {
  const absolute = path.resolve(candidate);
  const real = await services.realpath(absolute);
  const stat = await services.lstat(absolute, { bigint: true });
  const observed = identity(stat);
  if (!contained(tempRoot, absolute) || path.resolve(real) !== absolute || !stat.isDirectory() || stat.isSymbolicLink() || !observed) fail();
  return Object.freeze({ path: absolute, identity: observed });
}

async function assertDirectory(record, parent, services) {
  const current = await trustedDirectory(record.path, parent, services);
  if (current.identity.dev !== record.identity.dev || current.identity.ino !== record.identity.ino) fail();
}

async function readBlueprint(directory, services) {
  const candidate = path.join(directory.path, BLUEPRINT_NAME);
  let handle;
  try {
    await assertDirectory(directory, path.dirname(directory.path), services);
    handle = await services.openFile(candidate, "r");
    const before = await handle.stat({ bigint: true });
    const observed = identity(before);
    if (!before.isFile() || !observed || before.size < 1n || before.size > BigInt(BLUEPRINT_LIMIT)) fail();
    const real = await services.realpath(candidate);
    const pathStat = await services.lstat(candidate, { bigint: true });
    if (path.resolve(real) !== candidate || !contained(directory.path, real) || !sameIdentity(pathStat, observed) || pathStat.isSymbolicLink()) fail();
    const bytes = new Uint8Array(Number(before.size));
    let offset = 0;
    while (offset < bytes.length) {
      const read = await handle.read(bytes, offset, bytes.length - offset, offset);
      if (!read || read.bytesRead < 1) fail();
      offset += read.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, bytes.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameIdentity(after, observed) || after.size !== before.size || after.mtimeNs !== before.mtimeNs) fail();
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      fail();
    }
  } finally {
    if (handle) await handle.close().catch(() => {});
  }
}

function outputBytes(materialization) {
  if (!exactKeys(materialization, ["ok", "bundle", "canonicalBundleJson", "canonicalReportJson", "files"]) ||
      materialization.ok !== true || typeof materialization.canonicalBundleJson !== "string" ||
      typeof materialization.canonicalReportJson !== "string" || !Array.isArray(materialization.files) ||
      materialization.files.length !== 2) fail();
  const files = new Map();
  for (const file of materialization.files) {
    if (!exactKeys(file, ["path", "bytes"]) || typeof file.path !== "string" || !(file.bytes instanceof Uint8Array) || files.has(file.path)) fail();
    files.set(file.path, Uint8Array.from(file.bytes));
  }
  const validation = validatePrototypeEnvironmentBundleJson(materialization.canonicalBundleJson, files);
  if (!validation?.valid) fail();
  const panorama = files.get("assets/environment-panorama.png");
  const collider = files.get("assets/environment-collider.glb");
  if (!panorama || !collider) fail();
  const encoder = new TextEncoder();
  const artifacts = Object.freeze([
    Object.freeze({ path: OUTPUT_FILES[0], bytes: encoder.encode(materialization.canonicalBundleJson) }),
    Object.freeze({ path: OUTPUT_FILES[1], bytes: encoder.encode(materialization.canonicalReportJson) }),
    Object.freeze({ path: OUTPUT_FILES[2], bytes: panorama }),
    Object.freeze({ path: OUTPUT_FILES[3], bytes: collider }),
  ]);
  let report;
  try {
    report = JSON.parse(materialization.canonicalReportJson);
  } catch {
    fail();
  }
  if (canonicalizeJsonValue(report) !== materialization.canonicalReportJson ||
      !exactKeys(report, ["bundleSha256", "counts", "files", "format", "formatVersion", "provider"]) ||
      report.format !== "matrix-oasis.prototype-environment-materialization-report" || report.formatVersion !== "0.1.0" ||
      !exactKeys(report.provider, ["id", "model"]) || report.provider.id !== "world-labs-marble" || report.provider.model !== "marble-1.1" ||
      !exactKeys(report.counts, ["creates", "downloads", "polls", "worldGets"]) || report.counts.creates !== 1 ||
      report.counts.downloads !== 2 || report.counts.worldGets !== 1 || !Number.isSafeInteger(report.counts.polls) ||
      report.counts.polls < 1 || report.counts.polls > 180 || report.bundleSha256 !== sha256(artifacts[0].bytes) ||
      !Array.isArray(report.files) || report.files.length !== 2) fail();
  for (let index = 0; index < 2; index += 1) {
    const item = report.files[index];
    const artifact = artifacts[index + 2];
    if (!exactKeys(item, ["byteLength", "path", "sha256"]) || item.path !== artifact.path ||
        item.byteLength !== artifact.bytes.byteLength || item.sha256 !== sha256(artifact.bytes)) fail();
  }
  return Object.freeze({ artifacts, polls: report.counts.polls });
}

async function writeExact(stage, assets, artifacts, services) {
  for (const artifact of artifacts) {
    const parent = artifact.path.startsWith("assets/") ? assets : stage;
    const name = artifact.path.startsWith("assets/") ? artifact.path.slice(7) : artifact.path;
    const candidate = path.join(parent.path, name);
    await assertDirectory(stage, path.dirname(stage.path), services);
    await assertDirectory(parent, parent === stage ? path.dirname(stage.path) : stage.path, services);
    const handle = await services.openFile(candidate, "wx+");
    try {
      const before = await handle.stat({ bigint: true });
      const observed = identity(before);
      if (!before.isFile() || !observed) fail();
      const pathStat = await services.lstat(candidate, { bigint: true });
      const real = await services.realpath(candidate);
      if (!sameIdentity(pathStat, observed) || pathStat.isSymbolicLink() || path.resolve(real) !== candidate || !contained(parent.path, real)) fail();
      await handle.writeFile(artifact.bytes);
      await handle.sync();
      const output = new Uint8Array(artifact.bytes.length);
      let offset = 0;
      while (offset < output.length) {
        const read = await handle.read(output, offset, output.length - offset, offset);
        if (!read || read.bytesRead < 1) fail();
        offset += read.bytesRead;
      }
      if (!output.every((byte, index) => byte === artifact.bytes[index])) fail();
    } finally {
      await handle.close().catch(() => {});
    }
  }
}

async function safeCleanup(stage, expected, tempRoot, services) {
  if (!stage || !expected) return;
  try {
    const stat = await services.lstat(stage, { bigint: true });
    const real = await services.realpath(stage);
    if (sameIdentity(stat, expected) && stat.isDirectory() && !stat.isSymbolicLink() && path.resolve(real) === stage && contained(tempRoot, real)) {
      await services.rm(stage, { recursive: true, force: false });
    }
  } catch { /* preserve ambiguous staging for inspection */ }
}

async function publish(output, artifacts, tempRoot, services) {
  if (await exists(output, services)) fail("MARBLE_QUALIFICATION_OUTPUT_EXISTS");
  let stage;
  let stageIdentity;
  try {
    stage = await services.mkdtemp(path.join(tempRoot, ".matrix-oasis-r10-marble-"));
    const stageRecord = await trustedDirectory(stage, tempRoot, services);
    stageIdentity = stageRecord.identity;
    const assetsPath = path.join(stage, "assets");
    await services.mkdir(assetsPath, { recursive: false });
    const assets = await trustedDirectory(assetsPath, stage, services);
    await writeExact(stageRecord, assets, artifacts, services);
    await assertDirectory(stageRecord, tempRoot, services);
    await services.rename(stage, output);
    stage = undefined;
    const published = await trustedDirectory(output, tempRoot, services);
    if (published.identity.dev !== stageIdentity.dev || published.identity.ino !== stageIdentity.ino) fail();
  } finally {
    await safeCleanup(stage, stageIdentity, tempRoot, services);
  }
}

function publicDiagnostic(result) {
  const diagnostic = result?.diagnostics?.[0];
  const code = typeof diagnostic?.code === "string" && SAFE_CODE.test(diagnostic.code) ? diagnostic.code : "MARBLE_QUALIFICATION_CONTENT_INVALID";
  const pointer = typeof diagnostic?.path === "string" && SAFE_PATH.test(diagnostic.path) ? diagnostic.path : "";
  return `${code}${pointer ? ` ${pointer}` : ""}\n`;
}

export function parseMarbleQualificationArgs(args, tempRoot) {
  if (!Array.isArray(args) || args.length !== 5 || args[4] !== "--acknowledge-external-upload") fail("MARBLE_QUALIFICATION_ARGUMENT_INVALID");
  const values = Object.create(null);
  for (let index = 0; index < 4; index += 2) {
    const key = args[index];
    const value = args[index + 1];
    if (!["--prototype-dir", "--output"].includes(key) || Object.hasOwn(values, key) || typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) {
      fail("MARBLE_QUALIFICATION_ARGUMENT_INVALID");
    }
    values[key] = path.resolve(value);
  }
  const output = values["--output"];
  if (!values["--prototype-dir"] || !output || path.dirname(output) !== tempRoot || !OUTPUT_NAME.test(path.basename(output))) fail("MARBLE_QUALIFICATION_ARGUMENT_INVALID");
  return Object.freeze({ prototypeDir: values["--prototype-dir"], output });
}

export async function executeMarbleQualification({ args, tempRoot, environment, services, pipeline }) {
  try {
    const parsed = parseMarbleQualificationArgs(args, tempRoot);
    const key = environment?.MATRIX_OASIS_MARBLE_API_KEY;
    if (typeof key !== "string" || key.length < 1 || key.length > 8192 || /[\r\n]/u.test(key)) fail("MARBLE_QUALIFICATION_CONFIG_INVALID");
    const input = await trustedDirectory(parsed.prototypeDir, tempRoot, services);
    const blueprint = await readBlueprint(input, services);
    const plan = pipeline.planPrototypeEnvironment(blueprint);
    if (!plan?.ok) return Object.freeze({ exitCode: 1, stdout: "", stderr: publicDiagnostic(plan) });
    const provider = pipeline.createMarbleWorldProvider({
      endpoint: MARBLE_PROVIDER_ENDPOINT,
      apiKey: key,
      allowedAssetHosts: OFFICIAL_ASSET_HOSTS,
    });
    const materialization = await pipeline.materializePrototypeEnvironment({ plan,
      approval: { blueprintSha256: plan.plan.blueprint.canonicalSha256, model: "marble-1.1", maxCreateRequests: 1,
        maxPollAttempts: 180, maxWorldGets: 1, maxDownloads: 2, creditLimit: 1600, usdLimitCents: 150 } }, provider);
    if (!materialization?.ok) return Object.freeze({ exitCode: 1, stdout: "", stderr: publicDiagnostic(materialization) });
    const publication = outputBytes(materialization);
    await publish(parsed.output, publication.artifacts, tempRoot, services);
    return Object.freeze({ exitCode: 0, stdout: `MARBLE_ENVIRONMENT_QUALIFIED files=2 polls=${publication.polls}\n`, stderr: "" });
  } catch (error) {
    const code = error instanceof MarbleQualificationOperationalError ? error.code : "MARBLE_QUALIFICATION_INTERNAL_ERROR";
    return Object.freeze({ exitCode: 2, stdout: "", stderr: `${SAFE_CODE.test(code) ? code : "MARBLE_QUALIFICATION_INTERNAL_ERROR"}\n` });
  }
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
  const result = await executeMarbleQualification({
    args: process.argv.slice(2),
    tempRoot,
    environment: process.env,
    services: { lstat, mkdir, mkdtemp, openFile: open, realpath, rename, rm },
    pipeline: { createMarbleWorldProvider, materializePrototypeEnvironment, planPrototypeEnvironment },
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.exitCode;
}

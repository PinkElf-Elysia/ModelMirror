import path from "node:path";
import { createHash } from "node:crypto";

const PROTOTYPE_FILES = Object.freeze({
  authoringGamePackJson: ["authoring-game-pack.json", 1024 * 1024],
  sceneBlueprintJson: ["scene-blueprint.json", 256 * 1024],
  runtimeGamePackJson: ["runtime-game-pack.json", 16 * 1024 * 1024],
  runtimeReceiptJson: ["runtime-receipt.json", 16 * 1024],
});
const MAX_ACQUIRED_BYTES = 128 * 1024 * 1024;

export class PrototypeAssetCliOperationalError extends Error {
  constructor(code) {
    super(code);
    this.name = "PrototypeAssetCliOperationalError";
    this.code = code;
  }
}

function fail(code) {
  throw new PrototypeAssetCliOperationalError(code);
}

function samePath(left, right) {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function directChild(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== "" && !path.isAbsolute(relative) && !relative.includes(path.sep) && relative !== "..";
}

function containedDescendant(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== "" && !path.isAbsolute(relative) && relative !== ".." &&
    !relative.startsWith(`..${path.sep}`);
}

function normalDirectory(stat) {
  return stat?.isDirectory?.() === true && stat.isSymbolicLink?.() !== true;
}

function regularFile(stat) {
  return stat?.isFile?.() === true && stat.isSymbolicLink?.() !== true;
}

function identity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint"
    ? `${stat.dev}:${stat.ino}`
    : null;
}

function result(exitCode, stdout = "", stderr = "") {
  return Object.freeze({ exitCode, stdout, stderr });
}

function normalizeArgument(value, code) {
  if (typeof value !== "string" || value.length === 0 || value.includes("\0")) fail(code);
  return value;
}

function parsePairs(args, allowed, required, code) {
  if (!Array.isArray(args)) fail(code);
  const output = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const option = normalizeArgument(args[index], code);
    const key = allowed[option];
    if (!key || index + 1 >= args.length || Object.hasOwn(output, key)) fail(code);
    output[key] = normalizeArgument(args[index + 1], code);
  }
  if (required.some((key) => !Object.hasOwn(output, key))) fail(code);
  return Object.freeze(output);
}

export function parsePlanPrototypeAssetsArgs(args) {
  return parsePairs(
    args,
    { "--prototype-dir": "prototypeDir" },
    ["prototypeDir"],
    "PROTOTYPE_ASSET_PLAN_ARGUMENT_INVALID",
  );
}

export function parseMaterializePrototypeAssetsArgs(args) {
  return parsePairs(
    args,
    {
      "--prototype-dir": "prototypeDir",
      "--acquired-dir": "acquiredDir",
      "--output": "output",
    },
    ["prototypeDir", "acquiredDir", "output"],
    "PROTOTYPE_ASSET_MATERIALIZE_ARGUMENT_INVALID",
  );
}

async function exists(candidate, lstat) {
  try {
    await lstat(candidate, { bigint: true });
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function trustedTempRoot(tempRoot, { realpath, lstat }) {
  const expected = process.platform === "win32"
    ? path.resolve(`${["C", ":"].join("")}${path.sep}`, "tmp")
    : path.resolve(path.parse(process.cwd()).root, "tmp");
  if (typeof tempRoot !== "string" || !path.isAbsolute(tempRoot) || !samePath(tempRoot, expected)) {
    fail("PROTOTYPE_ASSET_TEMP_ROOT_INVALID");
  }
  try {
    const stat = await lstat(tempRoot, { bigint: true });
    if (!samePath(await realpath(tempRoot), tempRoot) || !normalDirectory(stat) || identity(stat) === null) {
      fail("PROTOTYPE_ASSET_TEMP_ROOT_INVALID");
    }
  } catch (error) {
    if (error instanceof PrototypeAssetCliOperationalError) throw error;
    fail("PROTOTYPE_ASSET_TEMP_ROOT_INVALID");
  }
  return path.resolve(tempRoot);
}

async function trustedInputDirectory(candidate, tempRoot, services, code) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate)) fail(code);
  const resolved = path.resolve(candidate);
  if (!directChild(tempRoot, resolved)) fail(code);
  try {
    const stat = await services.lstat(resolved, { bigint: true });
    if (!normalDirectory(stat) || identity(stat) === null || !samePath(await services.realpath(resolved), resolved)) {
      fail(code);
    }
  } catch (error) {
    if (error instanceof PrototypeAssetCliOperationalError) throw error;
    fail(code);
  }
  return resolved;
}

async function trustedDescendantInputDirectory(candidate, tempRoot, services, code) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate)) fail(code);
  const resolved = path.resolve(candidate);
  if (!containedDescendant(tempRoot, resolved)) fail(code);
  const relative = path.relative(tempRoot, resolved);
  let current = tempRoot;
  try {
    for (const segment of relative.split(path.sep)) {
      current = path.join(current, segment);
      const stat = await services.lstat(current, { bigint: true });
      if (!normalDirectory(stat) || identity(stat) === null || !samePath(await services.realpath(current), current)) {
        fail(code);
      }
    }
  } catch (error) {
    if (error instanceof PrototypeAssetCliOperationalError) throw error;
    fail(code);
  }
  return resolved;
}

async function readTrustedFile(candidate, parent, maximum, services, code) {
  let handle;
  try {
    if (!directChild(parent, candidate)) fail(code);
    const before = await services.lstat(candidate, { bigint: true });
    if (
      !regularFile(before) || identity(before) === null || before.size < 1n ||
      before.size > BigInt(maximum) || !samePath(await services.realpath(candidate), candidate)
    ) fail(code);
    handle = await services.openFile(candidate, "r");
    const opened = await handle.stat({ bigint: true });
    if (!regularFile(opened) || identity(opened) !== identity(before) || opened.size !== before.size) fail(code);
    const bytes = new Uint8Array(Number(opened.size));
    let offset = 0;
    while (offset < bytes.byteLength) {
      const read = await handle.read(bytes, offset, bytes.byteLength - offset, offset);
      if (!read || read.bytesRead < 1) fail(code);
      offset += read.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, bytes.byteLength);
    if (!tail || tail.bytesRead !== 0) fail(code);
    const after = await services.lstat(candidate, { bigint: true });
    if (identity(after) !== identity(before) || after.size !== before.size) fail(code);
    return bytes;
  } catch (error) {
    if (error instanceof PrototypeAssetCliOperationalError) throw error;
    fail(code);
  } finally {
    try { await handle?.close(); } catch { /* fail closed at the caller boundary */ }
  }
}

function decodeFatal(bytes, code) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    fail(code);
  }
}

async function readPrototypeInputs(prototypeDir, services) {
  const output = Object.create(null);
  for (const [key, [name, maximum]] of Object.entries(PROTOTYPE_FILES)) {
    output[key] = decodeFatal(
      await readTrustedFile(path.join(prototypeDir, name), prototypeDir, maximum, services, "PROTOTYPE_ASSET_INPUT_INVALID"),
      "PROTOTYPE_ASSET_INPUT_INVALID",
    );
  }
  return output;
}

function safeBriefId(value) {
  return typeof value === "string" && /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/u.test(value);
}

async function assertDirectory(candidate, parent, expectedIdentity, services) {
  const stat = await services.lstat(candidate, { bigint: true });
  if (
    !directChild(parent, candidate) || !normalDirectory(stat) ||
    identity(stat) !== expectedIdentity || !samePath(await services.realpath(candidate), candidate)
  ) throw new Error("UNTRUSTED_DIRECTORY");
}

async function assertFile(candidate, parent, expectedIdentity, services) {
  const stat = await services.lstat(candidate, { bigint: true });
  if (
    !directChild(parent, candidate) || !regularFile(stat) ||
    identity(stat) !== expectedIdentity || !samePath(await services.realpath(candidate), candidate)
  ) throw new Error("UNTRUSTED_FILE");
}

async function assertHandle(handle, candidate, parent, expectedIdentity, services) {
  const stat = await handle.stat({ bigint: true });
  const currentIdentity = identity(stat);
  if (!regularFile(stat) || currentIdentity === null || (expectedIdentity && currentIdentity !== expectedIdentity)) {
    throw new Error("UNTRUSTED_HANDLE");
  }
  await assertFile(candidate, parent, currentIdentity, services);
  return currentIdentity;
}

async function readHandle(handle, size) {
  const output = new Uint8Array(size);
  let offset = 0;
  while (offset < size) {
    const read = await handle.read(output, offset, size - offset, offset);
    if (!read || read.bytesRead < 1) throw new Error("READBACK_INVALID");
    offset += read.bytesRead;
  }
  const tail = await handle.read(new Uint8Array(1), 0, 1, size);
  if (!tail || tail.bytesRead !== 0) throw new Error("READBACK_INVALID");
  return output;
}

function equalBytes(left, right) {
  return left.byteLength === right.byteLength && left.every((byte, index) => byte === right[index]);
}

async function publishBundle({ tempRoot, output, artifacts, services }) {
  if (typeof output !== "string" || !path.isAbsolute(output)) fail("PROTOTYPE_ASSET_OUTPUT_INVALID");
  const target = path.resolve(output);
  if (!directChild(tempRoot, target) || !safeBriefId(path.basename(target))) fail("PROTOTYPE_ASSET_OUTPUT_INVALID");
  if (await exists(target, services.lstat)) fail("PROTOTYPE_ASSET_OUTPUT_EXISTS");
  let staging;
  const handles = [];
  try {
    staging = await services.mkdtemp(path.join(tempRoot, `.matrix-oasis-r9-${path.basename(target)}-`));
    const stagingIdentity = identity(await services.lstat(staging, { bigint: true }));
    if (stagingIdentity === null) throw new Error("STAGING_INVALID");
    await assertDirectory(staging, tempRoot, stagingIdentity, services);
    const assetsDir = path.join(staging, "assets");
    await services.mkdir(assetsDir, { recursive: false });
    const assetsIdentity = identity(await services.lstat(assetsDir, { bigint: true }));
    if (assetsIdentity === null) throw new Error("ASSETS_INVALID");
    await assertDirectory(assetsDir, staging, assetsIdentity, services);
    const records = [];
    for (const artifact of artifacts) {
      const parent = artifact.path.startsWith("assets/") ? assetsDir : staging;
      const name = artifact.path.startsWith("assets/") ? artifact.path.slice(7) : artifact.path;
      if (!safeBriefId(name.replace(/\.glb$/u, "")) && !["prototype-asset-bundle.json", "generation-report.json"].includes(name)) {
        throw new Error("OUTPUT_PATH_INVALID");
      }
      const filePath = path.join(parent, name);
      await assertDirectory(staging, tempRoot, stagingIdentity, services);
      await assertDirectory(assetsDir, staging, assetsIdentity, services);
      const handle = await services.openFile(filePath, "wx+");
      handles.push(handle);
      const fileIdentity = await assertHandle(handle, filePath, parent, null, services);
      records.push({ ...artifact, parent, filePath, handle, fileIdentity });
    }
    for (const record of records) {
      await assertDirectory(staging, tempRoot, stagingIdentity, services);
      await assertDirectory(assetsDir, staging, assetsIdentity, services);
      for (const check of records) await assertHandle(check.handle, check.filePath, check.parent, check.fileIdentity, services);
      await record.handle.writeFile(record.bytes);
      await record.handle.sync();
      if (!equalBytes(await readHandle(record.handle, record.bytes.byteLength), record.bytes)) throw new Error("WRITE_MISMATCH");
    }
    for (const record of records) {
      await assertHandle(record.handle, record.filePath, record.parent, record.fileIdentity, services);
      await record.handle.close();
      handles.splice(handles.indexOf(record.handle), 1);
      await assertFile(record.filePath, record.parent, record.fileIdentity, services);
    }
    await assertDirectory(staging, tempRoot, stagingIdentity, services);
    await assertDirectory(assetsDir, staging, assetsIdentity, services);
    if (await exists(target, services.lstat)) fail("PROTOTYPE_ASSET_OUTPUT_EXISTS");
    await services.rename(staging, target);
    staging = undefined;
    await assertDirectory(target, tempRoot, stagingIdentity, services);
    const finalAssets = path.join(target, "assets");
    await assertDirectory(finalAssets, target, assetsIdentity, services);
    for (const record of records) {
      const finalParent = record.parent === assetsDir ? finalAssets : target;
      const finalPath = path.join(finalParent, path.basename(record.filePath));
      await assertFile(finalPath, finalParent, record.fileIdentity, services);
      const verifyHandle = await services.openFile(finalPath, "r");
      handles.push(verifyHandle);
      await assertHandle(verifyHandle, finalPath, finalParent, record.fileIdentity, services);
      if (!equalBytes(await readHandle(verifyHandle, record.bytes.byteLength), record.bytes)) {
        throw new Error("FINAL_READBACK_MISMATCH");
      }
      await verifyHandle.close();
      handles.splice(handles.indexOf(verifyHandle), 1);
    }
  } catch (error) {
    if (error instanceof PrototypeAssetCliOperationalError) throw error;
    fail("PROTOTYPE_ASSET_PUBLISH_ERROR");
  } finally {
    for (const handle of handles) {
      try { await handle.close(); } catch { /* preserve primary failure */ }
    }
    // A replaced or partially observed staging tree is deliberately preserved in C:\tmp.
  }
}

function publicPlan(planResult) {
  return {
    ok: true,
    scene: planResult.plan.scene,
    blueprintCanonicalSha256: planResult.plan.blueprint.canonicalSha256,
    runtimeArtifactSha256: planResult.plan.runtimeIdentity.artifactSha256,
    assetBriefs: planResult.plan.blueprint.assetBriefs.map(({ id, kind, roles }) => ({ id, kind, roles })),
  };
}

function safeFailure(error, fallback) {
  const code = error instanceof PrototypeAssetCliOperationalError ? error.code : fallback;
  return result(2, "", `${code}\n`);
}

export async function executePlanPrototypeAssetsCli({ args, tempRoot, services, planPrototypeAssets }) {
  try {
    const parsed = parsePlanPrototypeAssetsArgs(args);
    const trustedRoot = await trustedTempRoot(tempRoot, services);
    const prototypeDir = await trustedInputDirectory(parsed.prototypeDir, trustedRoot, services, "PROTOTYPE_ASSET_INPUT_INVALID");
    const planned = await planPrototypeAssets(await readPrototypeInputs(prototypeDir, services));
    if (!planned?.ok) return result(1, "", "PROTOTYPE_ASSET_PLAN_REJECTED\n");
    return result(0, `${JSON.stringify(publicPlan(planned))}\n`, "");
  } catch (error) {
    return safeFailure(error, "PROTOTYPE_ASSET_CLI_INTERNAL_ERROR");
  }
}

export async function executeMaterializePrototypeAssetsCli({
  args,
  tempRoot,
  services,
  planPrototypeAssets,
  materializePrototypeAssetBundle,
  environmentRoot,
}) {
  try {
    const parsed = parseMaterializePrototypeAssetsArgs(args);
    const trustedRoot = await trustedTempRoot(tempRoot, services);
    const prototypeDir = await trustedInputDirectory(parsed.prototypeDir, trustedRoot, services, "PROTOTYPE_ASSET_INPUT_INVALID");
    const acquiredDir = await trustedDescendantInputDirectory(parsed.acquiredDir, trustedRoot, services, "PROTOTYPE_ASSET_ACQUIRED_INPUT_INVALID");
    const planned = await planPrototypeAssets(await readPrototypeInputs(prototypeDir, services));
    if (!planned?.ok) return result(1, "", "PROTOTYPE_ASSET_PLAN_REJECTED\n");
    const acquiredAssets = new Map();
    for (const brief of planned.plan.blueprint.assetBriefs) {
      if (brief.kind === "environment") continue;
      if (!safeBriefId(brief.id)) fail("PROTOTYPE_ASSET_ACQUIRED_INPUT_INVALID");
      acquiredAssets.set(
        brief.id,
        await readTrustedFile(path.join(acquiredDir, `${brief.id}.glb`), acquiredDir, MAX_ACQUIRED_BYTES, services, "PROTOTYPE_ASSET_ACQUIRED_INPUT_INVALID"),
      );
    }
    const environmentAssets = new Map();
    for (const name of ["floor-square", "wall"]) {
      environmentAssets.set(name, await readTrustedFile(path.join(environmentRoot, `${name}.glb`), environmentRoot, 32 * 1024 * 1024, services, "PROTOTYPE_ASSET_TEMPLATE_INVALID"));
    }
    const environmentTexture = await readTrustedFile(
      path.join(environmentRoot, "Textures", "colormap.png"),
      path.join(environmentRoot, "Textures"),
      16 * 1024 * 1024,
      services,
      "PROTOTYPE_ASSET_TEMPLATE_INVALID",
    );
    const materialized = await materializePrototypeAssetBundle({
      plan: planned,
      acquiredAssets,
      environmentAssets,
      environmentTexture,
    });
    if (!materialized?.ok) return result(1, "", "PROTOTYPE_ASSET_MATERIALIZATION_REJECTED\n");
    const artifacts = [
      { path: "prototype-asset-bundle.json", bytes: new TextEncoder().encode(materialized.canonicalBundleJson) },
      { path: "generation-report.json", bytes: new TextEncoder().encode(materialized.canonicalReportJson) },
      ...materialized.files.map(({ path: filePath, bytes }) => ({ path: filePath, bytes })),
    ];
    await publishBundle({ trustedRoot, tempRoot: trustedRoot, output: parsed.output, artifacts, services });
    return result(0, `PROTOTYPE_ASSET_MATERIALIZED files=${materialized.files.length}\n`, "");
  } catch (error) {
    return safeFailure(error, "PROTOTYPE_ASSET_CLI_INTERNAL_ERROR");
  }
}

export const MESHY_QUALIFICATION_OPERATIONS = Object.freeze([
  "preview-create",
  "preview-poll",
  "preview-download",
  "refine-create",
  "refine-poll",
  "refine-download",
]);

export function parseQualifyMeshyAssetArgs(args) {
  const parsed = parsePairs(
    args,
    {
      "--prototype-dir": "prototypeDir",
      "--brief": "briefId",
      "--operation": "operation",
    },
    ["prototypeDir", "briefId", "operation"],
    "MESHY_QUALIFICATION_ARGUMENT_INVALID",
  );
  if (!safeBriefId(parsed.briefId) || !MESHY_QUALIFICATION_OPERATIONS.includes(parsed.operation)) {
    fail("MESHY_QUALIFICATION_ARGUMENT_INVALID");
  }
  return parsed;
}

function stageFile(briefRoot, stage) {
  return path.join(briefRoot, `${stage}.json`);
}

function exactState(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const actual = Object.keys(value);
  if (actual.length !== keys.length || actual.some((key) => !keys.includes(key))) return null;
  return value;
}

async function readState(briefRoot, stage, keys, services) {
  const text = decodeFatal(
    await readTrustedFile(stageFile(briefRoot, stage), briefRoot, 16 * 1024, services, "MESHY_QUALIFICATION_STATE_INVALID"),
    "MESHY_QUALIFICATION_STATE_INVALID",
  );
  try {
    const value = exactState(JSON.parse(text), keys);
    if (!value || value.briefId !== path.basename(briefRoot)) fail("MESHY_QUALIFICATION_STATE_INVALID");
    return value;
  } catch (error) {
    if (error instanceof PrototypeAssetCliOperationalError) throw error;
    fail("MESHY_QUALIFICATION_STATE_INVALID");
  }
}

async function writeNewFile(candidate, parent, bytes, services, code) {
  let handle;
  try {
    handle = await services.openFile(candidate, "wx+");
    const fileIdentity = await assertHandle(handle, candidate, parent, null, services);
    await handle.writeFile(bytes);
    await handle.sync();
    if (!equalBytes(await readHandle(handle, bytes.byteLength), bytes)) throw new Error("WRITE_MISMATCH");
    await assertHandle(handle, candidate, parent, fileIdentity, services);
    await handle.close();
    handle = undefined;
    await assertFile(candidate, parent, fileIdentity, services);
  } catch (error) {
    try { await handle?.close(); } catch { /* preserve primary failure */ }
    if (error?.code === "EEXIST") fail("MESHY_QUALIFICATION_STAGE_EXISTS");
    if (error instanceof PrototypeAssetCliOperationalError) throw error;
    fail(code);
  }
}

async function writeState(briefRoot, stage, value, services) {
  await writeNewFile(
    stageFile(briefRoot, stage),
    briefRoot,
    new TextEncoder().encode(JSON.stringify(value)),
    services,
    "MESHY_QUALIFICATION_STATE_WRITE_ERROR",
  );
}

async function ensureQualificationRoots({ tempRoot, qualificationRoot, briefId, operation, services }) {
  if (!path.isAbsolute(qualificationRoot) || !directChild(tempRoot, qualificationRoot)) {
    fail("MESHY_QUALIFICATION_OUTPUT_INVALID");
  }
  if (!(await exists(qualificationRoot, services.lstat))) {
    if (operation !== "preview-create") fail("MESHY_QUALIFICATION_STATE_INVALID");
    await services.mkdir(qualificationRoot, { recursive: false });
  }
  const rootStat = await services.lstat(qualificationRoot, { bigint: true });
  const rootIdentity = identity(rootStat);
  if (rootIdentity === null) fail("MESHY_QUALIFICATION_OUTPUT_INVALID");
  await assertDirectory(qualificationRoot, tempRoot, rootIdentity, services);
  const briefRoot = path.join(qualificationRoot, briefId);
  if (!(await exists(briefRoot, services.lstat))) {
    if (operation !== "preview-create") fail("MESHY_QUALIFICATION_STATE_INVALID");
    await services.mkdir(briefRoot, { recursive: false });
  }
  const briefIdentity = identity(await services.lstat(briefRoot, { bigint: true }));
  if (briefIdentity === null) fail("MESHY_QUALIFICATION_OUTPUT_INVALID");
  await assertDirectory(briefRoot, qualificationRoot, briefIdentity, services);
  return { briefRoot, rootIdentity };
}

function safeTaskId(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= 128 && !/[\u0000-\u001f\u007f]/u.test(value);
}

function safeGlbUrl(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= 4096 && !/[\u0000-\u001f\u007f]/u.test(value);
}

function providerFailure() {
  return result(1, "", "MESHY_QUALIFICATION_PROVIDER_REJECTED\n");
}

async function pollTask({ provider, taskId, attempts, intervalMs, delay }) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const current = await provider.getTask({ taskId });
    if (!current?.ok) return current;
    if (current.task.status === "succeeded") return current;
    if (current.task.status === "failed") return { ok: false };
    if (attempt + 1 < attempts) await delay(intervalMs);
  }
  return { ok: false };
}

function sha256Bytes(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export async function executeQualifyMeshyAssetCli({
  args,
  tempRoot,
  qualificationRoot,
  services,
  provider,
  planPrototypeAssets,
  pollAttempts = 120,
  pollIntervalMs = 5_000,
  delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  try {
    const parsed = parseQualifyMeshyAssetArgs(args);
    const trustedRoot = await trustedTempRoot(tempRoot, services);
    const prototypeDir = await trustedInputDirectory(parsed.prototypeDir, trustedRoot, services, "PROTOTYPE_ASSET_INPUT_INVALID");
    const planned = await planPrototypeAssets(await readPrototypeInputs(prototypeDir, services));
    if (!planned?.ok) return result(1, "", "PROTOTYPE_ASSET_PLAN_REJECTED\n");
    const brief = planned.plan.blueprint.assetBriefs.find(({ id }) => id === parsed.briefId);
    if (!brief || !["prop", "character-placeholder"].includes(brief.kind)) {
      return result(1, "", "MESHY_QUALIFICATION_BRIEF_REJECTED\n");
    }
    const { briefRoot } = await ensureQualificationRoots({
      tempRoot: trustedRoot,
      qualificationRoot: path.resolve(qualificationRoot),
      briefId: brief.id,
      operation: parsed.operation,
      services,
    });
    const outputStage = {
      "preview-create": "preview-created",
      "preview-poll": "preview-polled",
      "preview-download": "preview-downloaded",
      "refine-create": "refine-created",
      "refine-poll": "refine-polled",
      "refine-download": "refine-downloaded",
    }[parsed.operation];
    if (await exists(stageFile(briefRoot, outputStage), services.lstat)) {
      fail("MESHY_QUALIFICATION_STAGE_EXISTS");
    }
    if (parsed.operation === "preview-create") {
      const created = await provider.createPreview({ prompt: brief.prompt });
      if (!created?.ok || !safeTaskId(created.taskId)) return providerFailure();
      await writeState(briefRoot, "preview-created", { briefId: brief.id, taskId: created.taskId }, services);
    } else if (parsed.operation === "preview-poll") {
      const state = await readState(briefRoot, "preview-created", ["briefId", "taskId"], services);
      if (!safeTaskId(state.taskId)) fail("MESHY_QUALIFICATION_STATE_INVALID");
      const polled = await pollTask({ provider, taskId: state.taskId, attempts: pollAttempts, intervalMs: pollIntervalMs, delay });
      if (!polled?.ok || !safeGlbUrl(polled.task.glbUrl)) return providerFailure();
      await writeState(briefRoot, "preview-polled", {
        briefId: brief.id,
        taskId: state.taskId,
        glbUrl: polled.task.glbUrl,
        consumedCredits: polled.task.consumedCredits,
      }, services);
    } else if (parsed.operation === "preview-download") {
      const state = await readState(briefRoot, "preview-polled", ["briefId", "taskId", "glbUrl", "consumedCredits"], services);
      if (!safeGlbUrl(state.glbUrl)) fail("MESHY_QUALIFICATION_STATE_INVALID");
      const downloaded = await provider.downloadGlb({ url: state.glbUrl });
      if (!downloaded?.ok || !(downloaded.bytes instanceof Uint8Array)) return providerFailure();
      await writeNewFile(path.join(briefRoot, "preview.glb"), briefRoot, downloaded.bytes, services, "MESHY_QUALIFICATION_ASSET_WRITE_ERROR");
      await writeState(briefRoot, "preview-downloaded", {
        briefId: brief.id,
        byteLength: downloaded.bytes.byteLength,
        sha256: sha256Bytes(downloaded.bytes),
      }, services);
    } else if (parsed.operation === "refine-create") {
      await readState(briefRoot, "preview-downloaded", ["briefId", "byteLength", "sha256"], services);
      const preview = await readState(briefRoot, "preview-created", ["briefId", "taskId"], services);
      if (!safeTaskId(preview.taskId)) fail("MESHY_QUALIFICATION_STATE_INVALID");
      const created = await provider.createRefine({ previewTaskId: preview.taskId });
      if (!created?.ok || !safeTaskId(created.taskId)) return providerFailure();
      await writeState(briefRoot, "refine-created", { briefId: brief.id, taskId: created.taskId }, services);
    } else if (parsed.operation === "refine-poll") {
      const state = await readState(briefRoot, "refine-created", ["briefId", "taskId"], services);
      if (!safeTaskId(state.taskId)) fail("MESHY_QUALIFICATION_STATE_INVALID");
      const polled = await pollTask({ provider, taskId: state.taskId, attempts: pollAttempts, intervalMs: pollIntervalMs, delay });
      if (!polled?.ok || !safeGlbUrl(polled.task.glbUrl)) return providerFailure();
      await writeState(briefRoot, "refine-polled", {
        briefId: brief.id,
        taskId: state.taskId,
        glbUrl: polled.task.glbUrl,
        consumedCredits: polled.task.consumedCredits,
      }, services);
    } else {
      const state = await readState(briefRoot, "refine-polled", ["briefId", "taskId", "glbUrl", "consumedCredits"], services);
      if (!safeGlbUrl(state.glbUrl)) fail("MESHY_QUALIFICATION_STATE_INVALID");
      const downloaded = await provider.downloadGlb({ url: state.glbUrl });
      if (!downloaded?.ok || !(downloaded.bytes instanceof Uint8Array)) return providerFailure();
      const acquiredRoot = path.join(path.resolve(qualificationRoot), "acquired");
      if (!(await exists(acquiredRoot, services.lstat))) await services.mkdir(acquiredRoot, { recursive: false });
      const acquiredIdentity = identity(await services.lstat(acquiredRoot, { bigint: true }));
      if (acquiredIdentity === null) fail("MESHY_QUALIFICATION_OUTPUT_INVALID");
      await assertDirectory(acquiredRoot, path.resolve(qualificationRoot), acquiredIdentity, services);
      await writeNewFile(path.join(acquiredRoot, `${brief.id}.glb`), acquiredRoot, downloaded.bytes, services, "MESHY_QUALIFICATION_ASSET_WRITE_ERROR");
      await writeState(briefRoot, "refine-downloaded", {
        briefId: brief.id,
        byteLength: downloaded.bytes.byteLength,
        sha256: sha256Bytes(downloaded.bytes),
      }, services);
    }
    return result(0, `MESHY_QUALIFICATION_STAGE_OK operation=${parsed.operation}\n`, "");
  } catch (error) {
    return safeFailure(error, "MESHY_QUALIFICATION_INTERNAL_ERROR");
  }
}

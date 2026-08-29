import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const INTERNAL_CODE = "NPC_AUTHORITY_INTERNAL_ERROR";
const MAX_INPUT_BYTES = 16 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 32 * 1024 * 1024;
export const R19_TEMP_ROOT = path.win32.join("C:" + path.win32.sep, "tmp");

export class R19CliError extends Error {
  constructor(code) {
    super(code);
    this.name = "R19CliError";
    this.code = code;
  }
}

function fail(code) {
  throw new R19CliError(code);
}

function samePath(left, right) {
  return path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase();
}

function contained(parent, candidate) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate));
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function identity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint" ? `${stat.dev}:${stat.ino}` : null;
}

function stableState(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? `${stat.size}:${stat.mtimeNs}:${stat.ctimeNs}` : null;
}

function normalFile(stat) {
  return stat?.isFile?.() === true && stat.isSymbolicLink?.() !== true;
}

function normalDirectory(stat) {
  return stat?.isDirectory?.() === true && stat.isSymbolicLink?.() !== true;
}

async function readBounded(handle, maximum, expectedLength = maximum) {
  const bytes = new Uint8Array(Math.min(maximum, expectedLength) + 1);
  let offset = 0;
  while (offset < bytes.byteLength) {
    const read = await handle.read(bytes, offset, bytes.byteLength - offset, offset);
    if (read.bytesRead === 0) break;
    offset += read.bytesRead;
  }
  if (offset > maximum) fail("NPC_AUTHORITY_CLI_INPUT_INVALID");
  return bytes.subarray(0, offset);
}

function parseArgs(args, required) {
  if (!Array.isArray(args) || args.length !== required.length * 2) fail("NPC_AUTHORITY_CLI_ARGUMENTS_INVALID");
  const output = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index];
    const value = args[index + 1];
    if (!required.includes(key) || typeof value !== "string" || value === "" || Object.hasOwn(output, key)) fail("NPC_AUTHORITY_CLI_ARGUMENTS_INVALID");
    output[key] = value;
  }
  if (required.some((key) => !Object.hasOwn(output, key))) fail("NPC_AUTHORITY_CLI_ARGUMENTS_INVALID");
  return output;
}

async function trustedTempRoot(tempRoot, services) {
  const absolute = path.resolve(tempRoot);
  const linked = await services.lstat(absolute, { bigint: true });
  const real = await services.realpath(absolute);
  if (!normalDirectory(linked) || identity(linked) === null || !samePath(real, absolute)) fail("NPC_AUTHORITY_CLI_TEMP_ROOT_INVALID");
  return absolute;
}

async function readStableFile(candidate, services, maximum = MAX_INPUT_BYTES) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate)) fail("NPC_AUTHORITY_CLI_INPUT_INVALID");
  const absolute = path.resolve(candidate);
  let handle;
  try {
    const beforePath = await services.lstat(absolute, { bigint: true });
    const real = await services.realpath(absolute);
    if (!normalFile(beforePath) || identity(beforePath) === null || !samePath(real, absolute) || beforePath.size > BigInt(maximum)) fail("NPC_AUTHORITY_CLI_INPUT_INVALID");
    handle = await services.openFile(absolute, "r");
    const before = await handle.stat({ bigint: true });
    if (!normalFile(before) || identity(before) !== identity(beforePath) || stableState(before) === null) fail("NPC_AUTHORITY_CLI_INPUT_CHANGED");
    const bytes = await readBounded(handle, maximum, Number(before.size));
    const after = await handle.stat({ bigint: true });
    const afterPath = await services.lstat(absolute, { bigint: true });
    if (identity(after) !== identity(before) || stableState(after) !== stableState(before) || identity(afterPath) !== identity(before) || !samePath(await services.realpath(absolute), absolute)) {
      fail("NPC_AUTHORITY_CLI_INPUT_CHANGED");
    }
    if (bytes.byteLength > maximum) fail("NPC_AUTHORITY_CLI_INPUT_INVALID");
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    if (error instanceof R19CliError) throw error;
    fail("NPC_AUTHORITY_CLI_INPUT_INVALID");
  } finally {
    await handle?.close().catch(() => {});
  }
}

async function trustedAuthorityDirectory(candidate, tempRoot, services) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate)) fail("NPC_AUTHORITY_CLI_AUTHORITY_DIR_INVALID");
  const absolute = path.resolve(candidate);
  if (!contained(tempRoot, absolute)) fail("NPC_AUTHORITY_CLI_AUTHORITY_DIR_INVALID");
  try {
    const linked = await services.lstat(absolute, { bigint: true });
    const directoryIdentity = identity(linked);
    if (!normalDirectory(linked) || directoryIdentity === null || !samePath(await services.realpath(absolute), absolute)) fail("NPC_AUTHORITY_CLI_AUTHORITY_DIR_INVALID");
    return { absolute, identity: directoryIdentity };
  } catch (error) {
    if (error instanceof R19CliError) throw error;
    fail("NPC_AUTHORITY_CLI_AUTHORITY_DIR_INVALID");
  }
}

async function readAuthority(candidate, tempRoot, services) {
  const directory = await trustedAuthorityDirectory(candidate, tempRoot, services);
  const read = (name) => readStableFile(path.join(directory.absolute, name), services);
  const [runtimeGamePackJson, runtimeReceiptJson, policyJson, runtimeSnapshotJson, worldEventLedgerJson] = await Promise.all([
    read("runtime-game-pack.json"),
    read("runtime-receipt.json"),
    read("npc-authority-policy.json"),
    read("runtime-snapshot.json"),
    read("world-event-ledger.json"),
  ]);
  let runtimeSnapshot;
  try { runtimeSnapshot = JSON.parse(runtimeSnapshotJson); } catch { fail("NPC_AUTHORITY_CLI_INPUT_INVALID"); }
  await assertDirectory(directory.absolute, directory.identity, services, "NPC_AUTHORITY_CLI_INPUT_CHANGED");
  return { runtimeGamePackJson, runtimeReceiptJson, policyJson, runtimeSnapshot, worldEventLedgerJson };
}

async function outputTarget(output, tempRoot, services) {
  if (typeof output !== "string" || !path.isAbsolute(output)) fail("NPC_AUTHORITY_CLI_OUTPUT_INVALID");
  const absolute = path.resolve(output);
  const parent = path.dirname(absolute);
  if (!contained(tempRoot, absolute) || !contained(tempRoot, parent) && !samePath(parent, tempRoot)) fail("NPC_AUTHORITY_CLI_OUTPUT_INVALID");
  let parentIdentity;
  try {
    const parentStat = await services.lstat(parent, { bigint: true });
    parentIdentity = identity(parentStat);
    if (!normalDirectory(parentStat) || parentIdentity === null || !samePath(await services.realpath(parent), parent)) fail("NPC_AUTHORITY_CLI_OUTPUT_INVALID");
    await services.lstat(absolute, { bigint: true });
    fail("NPC_AUTHORITY_CLI_OUTPUT_EXISTS");
  } catch (error) {
    if (error instanceof R19CliError) throw error;
    if (error?.code !== "ENOENT") fail("NPC_AUTHORITY_CLI_OUTPUT_INVALID");
  }
  await assertDirectory(parent, parentIdentity, services);
  return { absolute, parent, parentIdentity };
}

async function assertDirectory(candidate, expectedIdentity, services, code = INTERNAL_CODE) {
  try {
    const linked = await services.lstat(candidate, { bigint: true });
    if (!normalDirectory(linked) || identity(linked) !== expectedIdentity || !samePath(await services.realpath(candidate), candidate)) fail(code);
  } catch (error) {
    if (error instanceof R19CliError) throw error;
    fail(code);
  }
}

async function assertFile(candidate, expectedIdentity, services) {
  const linked = await services.lstat(candidate, { bigint: true });
  if (!normalFile(linked) || identity(linked) !== expectedIdentity || !samePath(await services.realpath(candidate), candidate)) fail(INTERNAL_CODE);
}

async function assertPublishedFile(candidate, record, services) {
  await assertFile(candidate, record.identity, services);
  let handle;
  try {
    handle = await services.openFile(candidate, "r");
    const before = await handle.stat({ bigint: true });
    if (!normalFile(before) || identity(before) !== record.identity || stableState(before) === null) fail(INTERNAL_CODE);
    const observed = await readBounded(handle, record.bytes.byteLength, record.bytes.byteLength);
    const after = await handle.stat({ bigint: true });
    if (stableState(after) !== stableState(before) || observed.byteLength !== record.bytes.byteLength || !observed.every((value, index) => value === record.bytes[index])) fail(INTERNAL_CODE);
  } catch (error) {
    if (error instanceof R19CliError && error.code === INTERNAL_CODE) throw error;
    fail(INTERNAL_CODE);
  } finally {
    await handle?.close().catch(() => {});
  }
}

async function cleanupStaging(staging, stagingIdentity, services) {
  if (!staging || !stagingIdentity) return;
  try {
    const linked = await services.lstat(staging, { bigint: true });
    if (normalDirectory(linked) && identity(linked) === stagingIdentity && samePath(await services.realpath(staging), staging)) await services.rm(staging, { recursive: true, force: false });
  } catch {
    // Ambiguous staging identity is deliberately retained for manual inspection.
  }
}

export async function publishR19Artifacts({ output, tempRoot, artifacts, services }) {
  const target = await outputTarget(output, tempRoot, services);
  const entries = Object.entries(artifacts).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0);
  if (!entries.length || entries.some(([name, text]) => !/^[a-z][a-z0-9-]*\.json$/u.test(name) || typeof text !== "string") || entries.reduce((sum, [, text]) => sum + new TextEncoder().encode(text).byteLength, 0) > MAX_OUTPUT_BYTES) {
    fail(INTERNAL_CODE);
  }
  let staging;
  let stagingIdentity;
  const handles = [];
  const records = [];
  try {
    await assertDirectory(target.parent, target.parentIdentity, services);
    staging = await services.mkdtemp(path.join(target.parent, `.matrix-oasis-r19-${path.basename(target.absolute)}-`));
    stagingIdentity = identity(await services.lstat(staging, { bigint: true }));
    if (!stagingIdentity) fail(INTERNAL_CODE);
    await assertDirectory(staging, stagingIdentity, services);
    for (const [name, text] of entries) {
      await assertDirectory(staging, stagingIdentity, services);
      const candidate = path.join(staging, name);
      const handle = await services.openFile(candidate, "wx+");
      handles.push(handle);
      const opened = await handle.stat({ bigint: true });
      const fileIdentity = identity(opened);
      if (!fileIdentity || !normalFile(opened)) fail(INTERNAL_CODE);
      await handle.writeFile(new TextEncoder().encode(text));
      await handle.sync();
      const after = await handle.stat({ bigint: true });
      if (identity(after) !== fileIdentity || after.size !== BigInt(new TextEncoder().encode(text).byteLength)) fail(INTERNAL_CODE);
      const expected = new TextEncoder().encode(text);
      const observed = new Uint8Array(expected.byteLength);
      const readback = await handle.read(observed, 0, observed.byteLength, 0);
      if (readback.bytesRead !== expected.byteLength || !observed.every((value, index) => value === expected[index])) fail(INTERNAL_CODE);
      await handle.close();
      handles.splice(handles.indexOf(handle), 1);
      await assertFile(candidate, fileIdentity, services);
      records.push({ name, identity: fileIdentity, bytes: expected });
    }
    await assertDirectory(staging, stagingIdentity, services);
    try { await services.lstat(target.absolute, { bigint: true }); fail("NPC_AUTHORITY_CLI_OUTPUT_EXISTS"); } catch (error) {
      if (error instanceof R19CliError) throw error;
      if (error?.code !== "ENOENT") fail(INTERNAL_CODE);
    }
    await assertDirectory(target.parent, target.parentIdentity, services);
    await services.rename(staging, target.absolute);
    staging = target.absolute;
    await assertDirectory(target.parent, target.parentIdentity, services);
    await assertDirectory(target.absolute, stagingIdentity, services);
    for (const record of records) await assertPublishedFile(path.join(target.absolute, record.name), record, services);
    staging = undefined;
    stagingIdentity = undefined;
    return Object.freeze({ ok: true });
  } catch (error) {
    if (error instanceof R19CliError) throw error;
    fail(INTERNAL_CODE);
  } finally {
    for (const handle of handles) await handle.close().catch(() => {});
    await cleanupStaging(staging, stagingIdentity, services);
  }
}

function operationFailure(result) {
  if (result?.ok === true) return;
  const code = result?.diagnostics?.[0]?.code;
  fail(typeof code === "string" && /^[A-Z0-9_]+$/u.test(code) ? code : INTERNAL_CODE);
}

function canonicalArtifact(value) {
  return canonicalizeJsonValue(value);
}

export async function executeCreateNpcAuthorityTimelineCli({ args, tempRoot, services, operations }) {
  try {
    const parsed = parseArgs(args, ["--runtime-pack", "--runtime-receipt", "--policy", "--timeline", "--output"]);
    const trustedRoot = await trustedTempRoot(tempRoot, services);
    const [runtimeGamePackJson, runtimeReceiptJson, policyJson] = await Promise.all([
      readStableFile(path.resolve(parsed["--runtime-pack"]), services),
      readStableFile(path.resolve(parsed["--runtime-receipt"]), services),
      readStableFile(path.resolve(parsed["--policy"]), services),
    ]);
    const prepared = await operations.prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson, policyJson });
    operationFailure(prepared);
    const timeline = operations.createNpcAuthorityTimeline(prepared.prepared, { timelineId: parsed["--timeline"] });
    operationFailure(timeline);
    await publishR19Artifacts({ output: parsed["--output"], tempRoot: trustedRoot, services, artifacts: {
      "npc-authority-policy.json": policyJson,
      "runtime-game-pack.json": runtimeGamePackJson,
      "runtime-inspection.json": canonicalArtifact(timeline.inspection),
      "runtime-receipt.json": runtimeReceiptJson,
      "runtime-snapshot.json": canonicalArtifact(timeline.runtimeSnapshot),
      "world-event-ledger.json": timeline.canonicalWorldEventLedgerJson,
    } });
    return { exitCode: 0, stdout: "R19_AUTHORITY_TIMELINE_CREATED\n", stderr: "" };
  } catch (error) {
    return { exitCode: 2, stdout: "", stderr: `${error instanceof R19CliError ? error.code : INTERNAL_CODE}\n` };
  }
}

export async function executeAdjudicateNpcIntentCli({ args, tempRoot, services, operations }) {
  try {
    const parsed = parseArgs(args, ["--authority-dir", "--intent", "--output"]);
    const trustedRoot = await trustedTempRoot(tempRoot, services);
    const authority = await readAuthority(parsed["--authority-dir"], trustedRoot, services);
    const npcIntentJson = await readStableFile(path.resolve(parsed["--intent"]), services);
    const prepared = await operations.prepareNpcAuthority(authority);
    operationFailure(prepared);
    const adjudicated = operations.adjudicateNpcIntent({ prepared: prepared.prepared, runtimeSnapshot: authority.runtimeSnapshot, worldEventLedgerJson: authority.worldEventLedgerJson, npcIntentJson });
    operationFailure(adjudicated);
    const replayed = operations.replayWorldEventLedger({ prepared: prepared.prepared, worldEventLedgerJson: adjudicated.canonicalWorldEventLedgerJson });
    operationFailure(replayed);
    await publishR19Artifacts({ output: parsed["--output"], tempRoot: trustedRoot, services, artifacts: {
      "adjudication-result.json": adjudicated.canonicalAdjudicationResultJson,
      "npc-authority-policy.json": authority.policyJson,
      "npc-intent.json": npcIntentJson,
      "runtime-game-pack.json": authority.runtimeGamePackJson,
      "runtime-inspection.json": canonicalArtifact(replayed.inspection),
      "runtime-receipt.json": authority.runtimeReceiptJson,
      "runtime-snapshot.json": canonicalArtifact(adjudicated.runtimeSnapshot),
      "world-event-ledger.json": adjudicated.canonicalWorldEventLedgerJson,
    } });
    return { exitCode: 0, stdout: "R19_NPC_INTENT_ADJUDICATED\n", stderr: "" };
  } catch (error) {
    return { exitCode: 2, stdout: "", stderr: `${error instanceof R19CliError ? error.code : INTERNAL_CODE}\n` };
  }
}

export async function executeReplayWorldEventLedgerCli({ args, tempRoot, services, operations }) {
  try {
    const parsed = parseArgs(args, ["--authority-dir", "--output"]);
    const trustedRoot = await trustedTempRoot(tempRoot, services);
    const authority = await readAuthority(parsed["--authority-dir"], trustedRoot, services);
    const prepared = await operations.prepareNpcAuthority(authority);
    operationFailure(prepared);
    const replayed = operations.replayWorldEventLedger({ prepared: prepared.prepared, worldEventLedgerJson: authority.worldEventLedgerJson });
    operationFailure(replayed);
    await publishR19Artifacts({ output: parsed["--output"], tempRoot: trustedRoot, services, artifacts: {
      "runtime-inspection.json": canonicalArtifact(replayed.inspection),
      "runtime-snapshot.json": canonicalArtifact(replayed.runtimeSnapshot),
      "world-event-ledger-replay-report.json": replayed.canonicalWorldEventLedgerReplayReportJson,
      "world-event-ledger.json": replayed.canonicalWorldEventLedgerJson,
    } });
    return { exitCode: 0, stdout: "R19_LEDGER_REPLAYED\n", stderr: "" };
  } catch (error) {
    return { exitCode: 2, stdout: "", stderr: `${error instanceof R19CliError ? error.code : INTERNAL_CODE}\n` };
  }
}

export async function executeValidateNpcAuthorityCli({ args, services, validators }) {
  try {
    const parsed = parseArgs(args, ["--kind", "--file"]);
    const validator = validators[parsed["--kind"]];
    if (typeof validator !== "function") fail("NPC_AUTHORITY_CLI_KIND_INVALID");
    const text = await readStableFile(path.resolve(parsed["--file"]), services);
    const report = validator(text);
    return { exitCode: report.valid ? 0 : 1, stdout: `${canonicalizeJsonValue(report)}\n`, stderr: "" };
  } catch (error) {
    return { exitCode: 2, stdout: "", stderr: `${error instanceof R19CliError ? error.code : INTERNAL_CODE}\n` };
  }
}

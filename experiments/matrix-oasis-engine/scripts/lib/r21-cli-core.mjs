import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readdir,
  realpath,
  rename,
} from "node:fs/promises";
import path from "node:path";
import {
  NPC_DERIVED_STATE_LIMITS,
  validateNpcDerivedStateBundleJson,
  validateNpcMemoryProjectionJson,
  validateNpcPersonaSeedJson,
  validateNpcProjectionQualificationReportJson,
  validateNpcRelationshipProjectionJson,
  validateNpcRelationshipProjectionPolicyJson,
} from "@matrix-oasis/npc-derived-state-contracts";
import {
  validateDerivedProjectionManifestJson,
  validateWorldEventLedgerReplayReportJson,
} from "@matrix-oasis/npc-authority-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  acquireR20WriterLease,
  auditR20TimelineStore,
  readStableR20File,
  releaseR20WriterLease,
  validateR20QualificationEvidenceJson,
} from "./r20-cli-core.mjs";

const SHA256 = /^sha256:[0-9a-f]{64}$/u;
const MANIFEST_ID = /^[0-9a-f]{64}$/u;
const OUTPUT_NAME = /^(?!\.)(?!.*[. ]$)[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/u;
const WINDOWS_DEVICE = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/iu;
const UTF8 = new TextDecoder("utf-8", { fatal: true });
const ENCODER = new TextEncoder();

export const R21_PROJECT_FILES = Object.freeze([
  "memory-derived-projection-manifest.json",
  "npc-derived-state-bundle.json",
  "npc-memory-projection.json",
  "npc-persona-seed.json",
  "npc-relationship-projection-policy.json",
  "npc-relationship-projection.json",
  "relationship-derived-projection-manifest.json",
  "world-event-ledger-replay-report.json",
].sort());

export const R21_QUALIFICATION_REPORT_FILE = "npc-projection-qualification-report.json";
export const R21_QUALIFICATION_FILES = Object.freeze([...R21_PROJECT_FILES, R21_QUALIFICATION_REPORT_FILE].sort());
export const R21_QUALIFICATION_MARKERS = Object.freeze([
  "R21_LEDGER_REBUILD_EQUIVALENT",
  "R21_MEMORY_DELETION_VERIFIED",
  "R21_RELATIONSHIP_PROJECTION_DETERMINISTIC",
]);

const PROJECT_MAXIMUMS = Object.freeze({
  "npc-persona-seed.json": NPC_DERIVED_STATE_LIMITS.personaBytes,
  "npc-relationship-projection-policy.json": NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes,
  "world-event-ledger-replay-report.json": 1024 * 1024,
  "npc-memory-projection.json": NPC_DERIVED_STATE_LIMITS.memoryProjectionBytes,
  "npc-relationship-projection.json": NPC_DERIVED_STATE_LIMITS.relationshipProjectionBytes,
  "memory-derived-projection-manifest.json": NPC_DERIVED_STATE_LIMITS.bundleBytes,
  "relationship-derived-projection-manifest.json": NPC_DERIVED_STATE_LIMITS.bundleBytes,
  "npc-derived-state-bundle.json": NPC_DERIVED_STATE_LIMITS.bundleBytes,
  [R21_QUALIFICATION_REPORT_FILE]: NPC_DERIVED_STATE_LIMITS.qualificationReportBytes,
});

const defaultOperations = Object.freeze({
  acquireWriterLease: acquireR20WriterLease,
  auditTimelineStore: auditR20TimelineStore,
  lstat,
  mkdir,
  mkdtemp,
  openFile: open,
  readStableFile: readStableR20File,
  readdir,
  realpath,
  releaseWriterLease: releaseR20WriterLease,
  rename,
  validateQualificationEvidence: validateR20QualificationEvidenceJson,
});

export class R21CliOperationalError extends Error {
  constructor(code = "R21_DERIVED_STATE_CLI_INTERNAL_ERROR", cause = undefined) {
    super(code);
    this.name = "R21CliOperationalError";
    this.code = code;
    if (cause !== undefined) this.cause = cause;
  }
}

function fail(code) {
  throw new R21CliOperationalError(code);
}

function operations(overrides = {}) {
  if (!overrides || typeof overrides !== "object" || Array.isArray(overrides)) fail("R21_CLI_OPERATIONS_INVALID");
  return Object.freeze({ ...defaultOperations, ...overrides });
}

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function exact(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Reflect.ownKeys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function sameJson(left, right) {
  return canonicalizeJsonValue(left) === canonicalizeJsonValue(right);
}

function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function sameIdentity(left, right) {
  return left && right && left.dev === right.dev && left.ino === right.ino;
}

function sameRecord(left, right) {
  return sameIdentity(left, right) && left.size === right.size && left.mtimeNs === right.mtimeNs && left.ctimeNs === right.ctimeNs && left.sha256 === right.sha256;
}

function identity(stat) {
  if (!stat || typeof stat.dev !== "bigint" || typeof stat.ino !== "bigint") fail("R21_PATH_IDENTITY_INVALID");
  return Object.freeze({
    dev: stat.dev,
    ino: stat.ino,
    size: stat.size,
    mtimeNs: stat.mtimeNs,
    ctimeNs: stat.ctimeNs,
  });
}

async function exists(candidate, ops) {
  try {
    await ops.lstat(candidate, { bigint: true });
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function trustedTemporaryRoot(temporaryRoot, ops) {
  if (typeof temporaryRoot !== "string" || !path.isAbsolute(temporaryRoot) || temporaryRoot.includes("\0")) fail("R21_PATH_INVALID");
  const candidate = path.resolve(temporaryRoot);
  let stat;
  try {
    stat = await ops.lstat(candidate, { bigint: true });
  } catch {
    fail("R21_PATH_INVALID");
  }
  if (!stat.isDirectory() || stat.isSymbolicLink() || path.resolve(await ops.realpath(candidate)) !== candidate) fail("R21_PATH_INVALID");
  return Object.freeze({ path: candidate, ...identity(stat) });
}

async function observeDirectory(candidate, trustedRoot, ops, expected = null) {
  const absolute = path.resolve(candidate);
  let stat;
  try {
    stat = await ops.lstat(absolute, { bigint: true });
  } catch {
    fail("R21_PATH_INVALID");
  }
  const observed = identity(stat);
  if (!stat.isDirectory() || stat.isSymbolicLink() || !contained(trustedRoot.path, absolute) || path.resolve(await ops.realpath(absolute)) !== absolute || (expected && !sameIdentity(observed, expected))) fail("R21_PATH_IDENTITY_INVALID");
  return Object.freeze({ path: absolute, ...observed });
}

async function observeFile(candidate, trustedRoot, ops) {
  const absolute = path.resolve(candidate);
  let stat;
  try {
    stat = await ops.lstat(absolute, { bigint: true });
  } catch {
    fail("R21_PATH_INVALID");
  }
  const observed = identity(stat);
  if (!stat.isFile() || stat.isSymbolicLink() || !contained(trustedRoot.path, absolute) || path.resolve(await ops.realpath(absolute)) !== absolute) fail("R21_PATH_IDENTITY_INVALID");
  return Object.freeze({ path: absolute, ...observed });
}

export async function readStableR21FileRecord(candidate, maximumBytes, temporaryRoot, overrides = {}) {
  const ops = operations(overrides);
  const trustedRoot = typeof temporaryRoot === "string" ? await trustedTemporaryRoot(temporaryRoot, ops) : temporaryRoot;
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1) fail("R21_FILE_LIMIT_INVALID");
  const before = await observeFile(candidate, trustedRoot, ops);
  let bytes;
  try {
    bytes = await ops.readStableFile(before.path, maximumBytes);
  } catch (error) {
    if (error instanceof R21CliOperationalError) throw error;
    fail("R21_FILE_IDENTITY_INVALID");
  }
  if (!(bytes instanceof Uint8Array) || bytes.byteLength !== Number(before.size) || bytes.byteLength < 1 || bytes.byteLength > maximumBytes) fail("R21_FILE_IDENTITY_INVALID");
  const after = await observeFile(before.path, trustedRoot, ops);
  const record = Object.freeze({ ...after, sha256: sha256(bytes), bytes: Uint8Array.from(bytes) });
  if (!sameIdentity(before, after) || before.size !== after.size || before.mtimeNs !== after.mtimeNs || before.ctimeNs !== after.ctimeNs) fail("R21_FILE_IDENTITY_INVALID");
  return record;
}

function decode(record, code) {
  try {
    return UTF8.decode(record.bytes);
  } catch {
    fail(code);
  }
}

function canonicalDocument(record, code) {
  const text = decode(record, code);
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    fail(code);
  }
  if (canonicalizeJsonValue(value) !== text) fail(code);
  return Object.freeze({ text, value });
}

async function revalidateInputRecord(record, maximumBytes, trustedRoot, ops) {
  const current = await readStableR21FileRecord(record.path, maximumBytes, trustedRoot, ops);
  if (!sameRecord(record, current) || !Buffer.from(record.bytes).equals(Buffer.from(current.bytes))) fail("R21_INPUT_CHANGED");
}

function parsePairs(args, specification, required) {
  if (!Array.isArray(args) || args.length !== required.length * 2) fail("R21_CLI_ARGUMENT_INVALID");
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = specification[args[index]];
    const value = args[index + 1];
    if (!name || Object.hasOwn(values, name) || typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) fail("R21_CLI_ARGUMENT_INVALID");
    values[name] = path.resolve(value);
  }
  if (required.some((name) => !Object.hasOwn(values, name))) fail("R21_CLI_ARGUMENT_INVALID");
  return values;
}

function safeOutputPath(candidate, temporaryRoot) {
  const output = path.resolve(candidate);
  const name = path.basename(output);
  if (path.dirname(output) !== path.resolve(temporaryRoot) || !OUTPUT_NAME.test(name) || WINDOWS_DEVICE.test(name)) fail("R21_CLI_ARGUMENT_INVALID");
  return output;
}

export function parseR21ProjectArguments(args, temporaryRoot) {
  const specification = Object.freeze({
    "--npc-run-root": "npcRunRoot",
    "--runtime-pack": "runtimePack",
    "--runtime-receipt": "runtimeReceipt",
    "--authority-policy": "authorityPolicy",
    "--persona-seed": "personaSeed",
    "--relationship-policy": "relationshipPolicy",
    "--output": "output",
  });
  const required = ["npcRunRoot", "runtimePack", "runtimeReceipt", "authorityPolicy", "personaSeed", "relationshipPolicy", "output"];
  const values = parsePairs(args, specification, required);
  values.output = safeOutputPath(values.output, temporaryRoot);
  if (path.dirname(values.npcRunRoot) !== path.resolve(temporaryRoot) || !values.npcRunRoot.endsWith("-npc") || new Set(required.map((name) => values[name])).size !== required.length) fail("R21_CLI_ARGUMENT_INVALID");
  return Object.freeze(values);
}

export function parseR21VerifyArguments(args, temporaryRoot) {
  const specification = Object.freeze({
    "--npc-run-root": "npcRunRoot",
    "--runtime-pack": "runtimePack",
    "--runtime-receipt": "runtimeReceipt",
    "--authority-policy": "authorityPolicy",
    "--projection-dir": "projectionDir",
  });
  const required = ["npcRunRoot", "runtimePack", "runtimeReceipt", "authorityPolicy", "projectionDir"];
  const values = parsePairs(args, specification, required);
  if (path.dirname(values.npcRunRoot) !== path.resolve(temporaryRoot) || !values.npcRunRoot.endsWith("-npc") || !contained(path.resolve(temporaryRoot), values.projectionDir) || new Set(required.map((name) => values[name])).size !== required.length) fail("R21_CLI_ARGUMENT_INVALID");
  return Object.freeze(values);
}

export function parseR21ValidateArguments(args) {
  if (!Array.isArray(args) || args.length !== 4 || args[0] !== "--kind" || args[2] !== "--file" || typeof args[1] !== "string" || typeof args[3] !== "string" || !path.isAbsolute(args[3]) || args[3].includes("\0")) fail("R21_CLI_ARGUMENT_INVALID");
  if (!["persona", "relationship-policy", "memory", "relationship", "bundle", "qualification"].includes(args[1])) fail("R21_CLI_ARGUMENT_INVALID");
  return Object.freeze({ kind: args[1], file: path.resolve(args[3]) });
}

function requireValid(report, code) {
  if (!report || report.valid !== true || !Array.isArray(report.diagnostics) || report.diagnostics.length !== 0) fail(code);
}

function currentDocument(document) {
  const keys = ["format", "formatVersion", "manifestSha256", "timelineId", "revision", "headSha256", "qualificationReceiptSha256"];
  if (!exact(document, keys) || document.format !== "matrix-oasis.npc-current" || document.formatVersion !== "0.1.0" || !SHA256.test(document.manifestSha256 ?? "") || !SHA256.test(document.qualificationReceiptSha256 ?? "") || typeof document.timelineId !== "string" || document.timelineId.length < 1 || !Number.isSafeInteger(document.revision) || document.revision < 0 || (document.headSha256 !== null && !SHA256.test(document.headSha256 ?? ""))) fail("R21_R20_CURRENT_INVALID");
  return document;
}

function exactCurrentSummary(audit, current) {
  if (!audit || audit.ok !== true || audit.pendingCurrent !== null || !sameJson(audit.current, current) || !Array.isArray(audit.timelines)) fail("R21_R20_SOURCE_NOT_QUIESCENT");
  const manifestId = current.manifestSha256.slice(7);
  if (!MANIFEST_ID.test(manifestId)) fail("R21_R20_CURRENT_INVALID");
  const found = audit.timelines.filter((entry) => entry?.manifestId === manifestId && entry?.timelineId === current.timelineId && entry?.revision === current.revision && entry?.headSha256 === current.headSha256 && entry?.qualificationReceiptSha256 === current.qualificationReceiptSha256 && entry?.qualified === true && entry?.status === "qualified");
  if (found.length !== 1) fail("R21_R20_SOURCE_NOT_QUALIFIED");
  return Object.freeze({ manifestId, summary: found[0] });
}

const sourceStates = new WeakMap();

function publicSource(state) {
  return Object.freeze({
    current: state.current.value,
    currentJson: state.current.text,
    authorityManifestJson: state.documents.authorityManifest.text,
    npcEntityBindingJson: state.documents.entityBinding.text,
    worldEventLedgerJson: state.documents.ledger.text,
    qualificationEvidence: state.evidence,
    runtimeGamePackJson: state.documents.runtimePack.text,
    runtimeReceiptJson: state.documents.runtimeReceipt.text,
    authorityPolicyJson: state.documents.authorityPolicy.text,
    sourceIdentity: state.sourceIdentity,
  });
}

async function acquireSource(request, overrides = {}) {
  const ops = operations(overrides);
  const trustedRoot = await trustedTemporaryRoot(request.temporaryRoot, ops);
  const npcRoot = await observeDirectory(request.npcRunRoot, trustedRoot, ops);
  const lease = await ops.acquireWriterLease({ npcRunRoot: request.npcRunRoot, temporaryRoot: trustedRoot.path });
  let keepLease = false;
  let primaryError = null;
  try {
    const audit = await ops.auditTimelineStore({ npcRunRoot: request.npcRunRoot, temporaryRoot: trustedRoot.path, writerLease: lease });
    const currentRecord = await readStableR21FileRecord(path.join(request.npcRunRoot, "npc-current.json"), 1024 * 1024, trustedRoot, ops);
    const current = canonicalDocument(currentRecord, "R21_R20_CURRENT_INVALID");
    currentDocument(current.value);
    const selected = exactCurrentSummary(audit, current.value);
    const timelineRoot = path.join(request.npcRunRoot, "timelines", selected.manifestId);
    const timelineDirectory = await observeDirectory(timelineRoot, trustedRoot, ops);
    const records = Object.freeze({
      current: currentRecord,
      authorityManifest: await readStableR21FileRecord(path.join(timelineRoot, "authority-manifest.json"), 16 * 1024 * 1024, trustedRoot, ops),
      entityBinding: await readStableR21FileRecord(path.join(timelineRoot, "entity-bindings.json"), 16 * 1024 * 1024, trustedRoot, ops),
      ledger: await readStableR21FileRecord(path.join(timelineRoot, "world-event-ledger.json"), 16 * 1024 * 1024, trustedRoot, ops),
      qualificationEvidence: await readStableR21FileRecord(path.join(timelineRoot, "qualification-evidence.json"), 32 * 1024 * 1024, trustedRoot, ops),
      runtimePack: await readStableR21FileRecord(request.runtimePack, 16 * 1024 * 1024, trustedRoot, ops),
      runtimeReceipt: await readStableR21FileRecord(request.runtimeReceipt, 16 * 1024 * 1024, trustedRoot, ops),
      authorityPolicy: await readStableR21FileRecord(request.authorityPolicy, 16 * 1024 * 1024, trustedRoot, ops),
    });
    const documents = Object.freeze({
      authorityManifest: canonicalDocument(records.authorityManifest, "R21_R20_MANIFEST_INVALID"),
      entityBinding: canonicalDocument(records.entityBinding, "R21_R20_BINDING_INVALID"),
      ledger: canonicalDocument(records.ledger, "R21_R20_LEDGER_INVALID"),
      qualificationEvidence: canonicalDocument(records.qualificationEvidence, "R21_R20_QUALIFICATION_INVALID"),
      runtimePack: canonicalDocument(records.runtimePack, "R21_RUNTIME_PACK_INVALID"),
      runtimeReceipt: canonicalDocument(records.runtimeReceipt, "R21_RUNTIME_RECEIPT_INVALID"),
      authorityPolicy: canonicalDocument(records.authorityPolicy, "R21_AUTHORITY_POLICY_INVALID"),
    });
    let evidence;
    try {
      evidence = ops.validateQualificationEvidence(documents.qualificationEvidence.text);
    } catch {
      fail("R21_R20_QUALIFICATION_INVALID");
    }
    if (!evidence || evidence.legacy === true || evidence.formatVersion !== "0.2.0" || evidence.runtimeGamePackJson !== documents.runtimePack.text || evidence.runtimeReceiptJson !== documents.runtimeReceipt.text || evidence.authorityPolicyJson !== documents.authorityPolicy.text || evidence.runtimeGamePackSha256 !== records.runtimePack.sha256 || evidence.runtimeReceiptSha256 !== records.runtimeReceipt.sha256 || evidence.authorityPolicySha256 !== records.authorityPolicy.sha256 || evidence.qualificationReceiptSha256 !== current.value.qualificationReceiptSha256) fail("R21_R20_QUALIFICATION_IDENTITY_MISMATCH");
    if (documents.authorityManifest.value.timelineId !== current.value.timelineId || records.authorityManifest.sha256 !== current.value.manifestSha256 || documents.ledger.value?.timeline?.id !== current.value.timelineId || documents.ledger.value?.revision !== current.value.revision || documents.ledger.value?.headSha256 !== current.value.headSha256) fail("R21_R20_SOURCE_IDENTITY_MISMATCH");
    const sourceIdentity = Object.freeze({
      r20CurrentSha256: records.current.sha256,
      r20AuthorityManifestSha256: records.authorityManifest.sha256,
      r20QualificationReceiptSha256: current.value.qualificationReceiptSha256,
      npcEntityBindingSha256: records.entityBinding.sha256,
    });
    const handle = Object.freeze(Object.create(null));
    sourceStates.set(handle, { ops, trustedRoot, lease, audit, current, selected, npcRoot, npcRunRoot: request.npcRunRoot, timelineDirectory, timelineRoot, records, documents, evidence, sourceIdentity, released: false });
    keepLease = true;
    return handle;
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    if (!keepLease) {
      try {
        await ops.releaseWriterLease(lease);
      } catch (error) {
        throw new R21CliOperationalError("R21_SOURCE_LEASE_RELEASE_FAILED", primaryError ?? error);
      }
    }
  }
}

export async function acquireQualifiedR20Source(request, overrides = {}) {
  if (!request || typeof request !== "object" || Array.isArray(request)) fail("R21_SOURCE_REQUEST_INVALID");
  return acquireSource(request, overrides);
}

export function inspectQualifiedR20Source(handle) {
  const state = sourceStates.get(handle);
  if (!state || state.released) fail("R21_SOURCE_HANDLE_INVALID");
  return publicSource(state);
}

async function rereadRecord(record, state) {
  const maximum = record.path.endsWith("qualification-evidence.json") ? 32 * 1024 * 1024 : 16 * 1024 * 1024;
  const current = await readStableR21FileRecord(record.path, maximum, state.trustedRoot, state.ops);
  if (!sameRecord(record, current) || !Buffer.from(record.bytes).equals(Buffer.from(current.bytes))) fail("R21_R20_SOURCE_CHANGED");
}

export async function revalidateQualifiedR20Source(handle) {
  const state = sourceStates.get(handle);
  if (!state || state.released) fail("R21_SOURCE_HANDLE_INVALID");
  await observeDirectory(state.npcRunRoot, state.trustedRoot, state.ops, state.npcRoot);
  await observeDirectory(state.timelineRoot, state.trustedRoot, state.ops, state.timelineDirectory);
  const audit = await state.ops.auditTimelineStore({ npcRunRoot: state.npcRunRoot, temporaryRoot: state.trustedRoot.path, writerLease: state.lease });
  exactCurrentSummary(audit, state.current.value);
  if (!sameJson(audit.current, state.audit.current) || !sameJson(audit.timelines, state.audit.timelines)) fail("R21_R20_SOURCE_CHANGED");
  for (const record of Object.values(state.records)) await rereadRecord(record, state);
  return publicSource(state);
}

export async function releaseQualifiedR20Source(handle) {
  const state = sourceStates.get(handle);
  if (!state || state.released) fail("R21_SOURCE_HANDLE_INVALID");
  await state.ops.releaseWriterLease(state.lease);
  state.released = true;
}

function artifactMap(projected, bound, personaSeedJson, relationshipPolicyJson) {
  if (!exact(projected, ["ok", "canonicalWorldEventLedgerReplayReportJson", "canonicalNpcMemoryProjectionJson", "canonicalNpcRelationshipProjectionJson", "canonicalMemoryDerivedProjectionManifestJson", "canonicalRelationshipDerivedProjectionManifestJson"]) || projected.ok !== true || !exact(bound, ["ok", "canonicalNpcDerivedStateBundleJson"]) || bound.ok !== true) fail("R21_RUNTIME_RESULT_INVALID");
  const entries = [
    ["npc-persona-seed.json", personaSeedJson],
    ["npc-relationship-projection-policy.json", relationshipPolicyJson],
    ["world-event-ledger-replay-report.json", projected.canonicalWorldEventLedgerReplayReportJson],
    ["npc-memory-projection.json", projected.canonicalNpcMemoryProjectionJson],
    ["npc-relationship-projection.json", projected.canonicalNpcRelationshipProjectionJson],
    ["memory-derived-projection-manifest.json", projected.canonicalMemoryDerivedProjectionManifestJson],
    ["relationship-derived-projection-manifest.json", projected.canonicalRelationshipDerivedProjectionManifestJson],
    ["npc-derived-state-bundle.json", bound.canonicalNpcDerivedStateBundleJson],
  ];
  if (entries.some(([, value]) => typeof value !== "string")) fail("R21_RUNTIME_RESULT_INVALID");
  return new Map(entries.map(([name, text]) => [name, ENCODER.encode(text)]));
}

function validateProjectArtifacts(artifacts) {
  const text = (name) => decode({ bytes: artifacts.get(name) }, "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateNpcPersonaSeedJson(text("npc-persona-seed.json")), "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateNpcRelationshipProjectionPolicyJson(text("npc-relationship-projection-policy.json")), "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateWorldEventLedgerReplayReportJson(text("world-event-ledger-replay-report.json")), "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateNpcMemoryProjectionJson(text("npc-memory-projection.json")), "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateNpcRelationshipProjectionJson(text("npc-relationship-projection.json")), "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateDerivedProjectionManifestJson(text("memory-derived-projection-manifest.json")), "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateDerivedProjectionManifestJson(text("relationship-derived-projection-manifest.json")), "R21_PROJECT_ARTIFACT_INVALID");
  requireValid(validateNpcDerivedStateBundleJson(text("npc-derived-state-bundle.json")), "R21_PROJECT_ARTIFACT_INVALID");
  for (const [name, bytes] of artifacts) if (!PROJECT_MAXIMUMS[name] || bytes.byteLength < 1 || bytes.byteLength > PROJECT_MAXIMUMS[name]) fail("R21_PROJECT_ARTIFACT_INVALID");
}

function equalArtifactMaps(left, right, allowed = R21_PROJECT_FILES) {
  for (const name of allowed) {
    const a = left.get(name); const b = right.get(name);
    if (!(a instanceof Uint8Array) || !(b instanceof Uint8Array) || !Buffer.from(a).equals(Buffer.from(b))) return false;
  }
  return true;
}

async function writeOwnedDirectory(artifacts, temporaryRoot, baseName, ops) {
  const names = [...artifacts.keys()].sort();
  const allowed = names.includes(R21_QUALIFICATION_REPORT_FILE) ? R21_QUALIFICATION_FILES : R21_PROJECT_FILES;
  if (names.join("\0") !== allowed.join("\0")) fail("R21_PROJECT_ARTIFACT_SET_INVALID");
  const stagePath = await ops.mkdtemp(path.join(temporaryRoot.path, `.r21-${baseName}-`));
  const stage = await observeDirectory(stagePath, temporaryRoot, ops);
  const files = new Map();
  try {
    for (const name of allowed) {
      const bytes = artifacts.get(name);
      if (!(bytes instanceof Uint8Array) || bytes.byteLength < 1 || bytes.byteLength > PROJECT_MAXIMUMS[name]) fail("R21_PROJECT_ARTIFACT_INVALID");
      const candidate = path.join(stage.path, name);
      const handle = await ops.openFile(candidate, "wx+");
      try {
        const opened = identity(await handle.stat({ bigint: true }));
        files.set(name, Object.freeze({ path: candidate, ...opened, bytes: null, sha256: null, partial: true }));
        await handle.writeFile(bytes);
        await handle.sync();
        const readback = new Uint8Array(bytes.byteLength);
        let offset = 0;
        while (offset < readback.byteLength) {
          const result = await handle.read(readback, offset, readback.byteLength - offset, offset);
          if (!result || result.bytesRead < 1) fail("R21_OUTPUT_WRITE_INVALID");
          offset += result.bytesRead;
        }
        const tail = await handle.read(new Uint8Array(1), 0, 1, readback.byteLength);
        if (tail.bytesRead !== 0 || !Buffer.from(readback).equals(Buffer.from(bytes))) fail("R21_OUTPUT_WRITE_INVALID");
        const after = identity(await handle.stat({ bigint: true }));
        if (!sameIdentity(opened, after) || after.size !== BigInt(bytes.byteLength)) fail("R21_OUTPUT_WRITE_INVALID");
      } finally {
        await handle.close();
      }
      const record = await readStableR21FileRecord(candidate, PROJECT_MAXIMUMS[name], temporaryRoot, ops);
      if (!Buffer.from(record.bytes).equals(Buffer.from(bytes))) fail("R21_OUTPUT_WRITE_INVALID");
      files.set(name, record);
    }
    await observeDirectory(stage.path, temporaryRoot, ops, stage);
    if ((await ops.readdir(stage.path)).sort().join("\0") !== allowed.join("\0")) fail("R21_OUTPUT_CONTENT_INVALID");
    return { path: stage.path, ...stage, files, allowed, published: false, created: true };
  } catch (error) {
    const record = { path: stage.path, ...stage, files, allowed, published: false, created: true };
    try {
      await quarantineAndRemoveOwnedR21Directory(record, temporaryRoot, ops, false);
    } catch (cleanupError) {
      throw new R21CliOperationalError("R21_STAGING_CLEANUP_FAILED", new AggregateError([error, cleanupError]));
    }
    throw error;
  }
}

async function readOwnedArtifacts(record, temporaryRoot, ops) {
  await observeDirectory(record.path, temporaryRoot, ops, record);
  const names = (await ops.readdir(record.path)).sort();
  if (names.join("\0") !== record.allowed.join("\0")) fail("R21_OUTPUT_CONTENT_INVALID");
  const artifacts = new Map();
  for (const name of record.allowed) {
    const expected = record.files.get(name);
    const found = await readStableR21FileRecord(path.join(record.path, name), PROJECT_MAXIMUMS[name], temporaryRoot, ops);
    if (!expected || !sameRecord(expected, found) || !Buffer.from(expected.bytes).equals(Buffer.from(found.bytes))) fail("R21_OUTPUT_IDENTITY_INVALID");
    artifacts.set(name, found.bytes);
  }
  return artifacts;
}

async function safeRemoveOwnedR21Directory(record, temporaryRoot, overrides = {}) {
  const ops = operations(overrides);
  const trustedRoot = typeof temporaryRoot === "string" ? await trustedTemporaryRoot(temporaryRoot, ops) : temporaryRoot;
  if (!record?.created || typeof record.path !== "string" || path.dirname(path.resolve(record.path)) !== trustedRoot.path || !Array.isArray(record.allowed) || !(record.files instanceof Map)) fail("R21_OWNED_DIRECTORY_INVALID");
  return quarantineAndRemoveOwnedR21Directory(record, trustedRoot, ops, true);
}

async function quarantineAndRemoveOwnedR21Directory(record, trustedRoot, ops, requireComplete) {
  await observeDirectory(record.path, trustedRoot, ops, record);
  const originalPath = record.path;
  const quarantinePath = await ops.mkdtemp(path.join(trustedRoot.path, ".r21-quarantine-"));
  const quarantine = await observeDirectory(quarantinePath, trustedRoot, ops);
  const isolated = path.join(quarantine.path, "owned");
  try {
    await ops.rename(record.path, isolated);
  } catch (error) {
    // Preserve the empty, hidden quarantine. Path-based directory deletion
    // would reintroduce the same final-component race this routine prevents.
    throw error;
  }
  record.path = isolated;
  try {
    const isolatedDirectory = await observeDirectory(isolated, trustedRoot, ops);
    if (!sameIdentity(isolatedDirectory, record)) {
      if (await exists(originalPath, ops)) {
        record.created = false;
        fail("R21_OWNED_DIRECTORY_RECOVERY_FAILED");
      }
      try {
        await ops.rename(isolated, originalPath);
      } catch (error) {
        record.created = false;
        throw new R21CliOperationalError("R21_OWNED_DIRECTORY_RECOVERY_FAILED", error);
      }
      const restored = await observeDirectory(originalPath, trustedRoot, ops);
      if (!sameIdentity(restored, isolatedDirectory)) {
        record.created = false;
        fail("R21_OWNED_DIRECTORY_RECOVERY_FAILED");
      }
      record.path = originalPath;
      record.created = false;
      fail("R21_OWNED_DIRECTORY_CHANGED");
    }
    const expectedNames = (requireComplete ? record.allowed : [...record.files.keys()]).slice().sort();
    const foundNames = (await ops.readdir(isolated)).sort();
    if (foundNames.join("\0") !== expectedNames.join("\0")) fail("R21_OWNED_DIRECTORY_CHANGED");
    for (const name of expectedNames) {
      const expected = record.files.get(name);
      if (!expected) fail("R21_OWNED_DIRECTORY_CHANGED");
      const candidate = path.join(isolated, name);
      const linked = await observeFile(candidate, trustedRoot, ops);
      if (!sameIdentity(expected, linked)) fail("R21_OWNED_DIRECTORY_CHANGED");
      if (expected.partial !== true) {
        const found = await readStableR21FileRecord(candidate, PROJECT_MAXIMUMS[name], trustedRoot, ops);
        if (!sameRecord(expected, found) || !Buffer.from(expected.bytes).equals(Buffer.from(found.bytes))) fail("R21_OWNED_DIRECTORY_CHANGED");
      }
    }
    await observeDirectory(isolated, trustedRoot, ops, record);
    if ((await ops.readdir(isolated)).sort().join("\0") !== expectedNames.join("\0")) fail("R21_OWNED_DIRECTORY_CHANGED");
    await observeDirectory(quarantine.path, trustedRoot, ops, quarantine);
    if ((await ops.readdir(quarantine.path)).join("\0") !== "owned") fail("R21_OWNED_DIRECTORY_CHANGED");
    // Standard Node fs on Windows has no unlink-at-directory-handle or
    // delete-on-close primitive. Keeping the exact, validated directory under
    // an unpredictable hidden quarantine is the only fail-closed operation
    // against a same-user parent swap: the product/probe namespace is removed,
    // while no path-based mutation can touch a competitor. This is namespace
    // deletion, not secure byte erasure, and is recorded as an R21 limitation.
    record.created = false;
    return true;
  } catch (error) {
    // The original success/staging path is already gone. Preserve any ambiguous
    // bytes under the hidden quarantine rather than recursively deleting them.
    record.created = false;
    throw error;
  }
}

async function publishOwnedDirectory(record, output, temporaryRoot, verify, beforeRename, afterRename, ops) {
  if (await exists(output, ops)) fail("R21_OUTPUT_EXISTS");
  await readOwnedArtifacts(record, temporaryRoot, ops);
  await verify(record.path);
  await beforeRename();
  if (await exists(output, ops)) fail("R21_OUTPUT_EXISTS");
  try {
    await ops.rename(record.path, output);
    record.path = output;
    record.published = true;
  } catch (error) {
    const stageExists = await exists(record.path, ops);
    if (stageExists) throw error;
    record.path = output;
    try {
      await readOwnedArtifacts(record, temporaryRoot, ops);
      await verify(output);
      record.published = true;
    } catch {
      throw error;
    }
  }
  await readOwnedArtifacts(record, temporaryRoot, ops);
  await verify(output);
  await afterRename();
  record.created = false;
  return output;
}

export async function publishR21Artifacts({ artifacts, output, temporaryRoot, verifyDirectory, beforeRename = async () => {}, afterRename = async () => {} }, overrides = {}) {
  const ops = operations(overrides);
  const trustedRoot = await trustedTemporaryRoot(temporaryRoot, ops);
  if (!(artifacts instanceof Map) || typeof verifyDirectory !== "function" || typeof beforeRename !== "function" || typeof afterRename !== "function") fail("R21_PUBLISH_REQUEST_INVALID");
  const target = safeOutputPath(output, trustedRoot.path);
  const record = await writeOwnedDirectory(artifacts, trustedRoot, path.basename(target), ops);
  try {
    return await publishOwnedDirectory(record, target, trustedRoot, verifyDirectory, beforeRename, afterRename, ops);
  } catch (error) {
    if (record.created) {
      try {
        await safeRemoveOwnedR21Directory(record, trustedRoot, ops);
      } catch {
        if (record.published && await exists(target, ops)) fail("R21_OUTPUT_ROLLBACK_FAILED");
      }
    }
    if (record.published && await exists(target, ops)) fail("R21_OUTPUT_ROLLBACK_FAILED");
    throw error;
  }
}

async function readProjectionDirectory(directory, temporaryRoot, ops, allowQualification = true) {
  const trustedRoot = await trustedTemporaryRoot(temporaryRoot, ops);
  const root = await observeDirectory(directory, trustedRoot, ops);
  const names = (await ops.readdir(root.path)).sort();
  const allowed = names.includes(R21_QUALIFICATION_REPORT_FILE) && allowQualification ? R21_QUALIFICATION_FILES : R21_PROJECT_FILES;
  if (names.join("\0") !== allowed.join("\0")) fail("R21_OUTPUT_CONTENT_INVALID");
  const artifacts = new Map();
  const records = new Map();
  for (const name of allowed) {
    const record = await readStableR21FileRecord(path.join(root.path, name), PROJECT_MAXIMUMS[name], trustedRoot, ops);
    records.set(name, record);
    artifacts.set(name, record.bytes);
  }
  return Object.freeze({ root, artifacts, records, allowed });
}

async function revalidateProjectionDirectory(original, temporaryRoot, ops) {
  const current = await readProjectionDirectory(original.root.path, temporaryRoot, ops, original.allowed.includes(R21_QUALIFICATION_REPORT_FILE));
  if (current.allowed.join("\0") !== original.allowed.join("\0") || !sameIdentity(current.root, original.root)) fail("R21_OUTPUT_IDENTITY_INVALID");
  for (const name of original.allowed) {
    const before = original.records.get(name);
    const after = current.records.get(name);
    if (!before || !after || !sameRecord(before, after) || !Buffer.from(before.bytes).equals(Buffer.from(after.bytes))) fail("R21_OUTPUT_IDENTITY_INVALID");
  }
}

async function rollbackPublishedR21Directory(output, expectedArtifacts, temporaryRoot, ops) {
  if (!await exists(output, ops)) return;
  const found = await readProjectionDirectory(output, temporaryRoot.path, ops, expectedArtifacts.has(R21_QUALIFICATION_REPORT_FILE));
  if (!equalArtifactMaps(found.artifacts, expectedArtifacts, found.allowed)) fail("R21_OUTPUT_ROLLBACK_FAILED");
  const record = {
    path: found.root.path,
    ...found.root,
    files: found.records,
    allowed: found.allowed,
    published: true,
    created: true,
  };
  try {
    await safeRemoveOwnedR21Directory(record, temporaryRoot, ops);
  } catch {
    if (await exists(output, ops)) fail("R21_OUTPUT_ROLLBACK_FAILED");
  }
  if (await exists(output, ops)) fail("R21_OUTPUT_ROLLBACK_FAILED");
}

async function finishQualifiedSource(handle, primaryError, published, trustedRoot, ops) {
  try {
    await releaseQualifiedR20Source(handle);
  } catch (releaseError) {
    if (published !== null) {
      try {
        await rollbackPublishedR21Directory(published.output, published.artifacts, trustedRoot, ops);
      } catch (rollbackError) {
        throw new R21CliOperationalError("R21_OUTPUT_ROLLBACK_FAILED", rollbackError);
      }
    }
    throw new R21CliOperationalError("R21_SOURCE_LEASE_RELEASE_FAILED", primaryError ?? releaseError);
  }
  if (primaryError !== null) throw primaryError;
}

async function prepareAndProject(source, personaSeedJson, relationshipPolicyJson, runtime) {
  if (!runtime || typeof runtime.prepareNpcDerivedState !== "function" || typeof runtime.projectNpcDerivedState !== "function" || typeof runtime.bindNpcDerivedStateSource !== "function") fail("R21_RUNTIME_INTERFACE_INVALID");
  requireValid(validateNpcPersonaSeedJson(personaSeedJson), "R21_PERSONA_SEED_INVALID");
  requireValid(validateNpcRelationshipProjectionPolicyJson(relationshipPolicyJson), "R21_RELATIONSHIP_POLICY_INVALID");
  const prepared = await runtime.prepareNpcDerivedState({
    runtimeGamePackJson: source.runtimeGamePackJson,
    runtimeReceiptJson: source.runtimeReceiptJson,
    authorityPolicyJson: source.authorityPolicyJson,
    npcEntityBindingJson: source.npcEntityBindingJson,
    personaSeedJson,
    relationshipPolicyJson,
  });
  if (!prepared || prepared.ok !== true || prepared.prepared === undefined) fail("R21_RUNTIME_PREPARE_FAILED");
  const projected = await runtime.projectNpcDerivedState({ prepared: prepared.prepared, worldEventLedgerJson: source.worldEventLedgerJson });
  const bound = await runtime.bindNpcDerivedStateSource({ projected, sourceIdentity: source.sourceIdentity, personaSeedJson, relationshipPolicyJson });
  const artifacts = artifactMap(projected, bound, personaSeedJson, relationshipPolicyJson);
  validateProjectArtifacts(artifacts);
  return Object.freeze({ prepared: prepared.prepared, projected, bound, artifacts });
}

async function verifyArtifactMap(artifacts, source, runtime) {
  validateProjectArtifacts(artifacts);
  const text = (name) => decode({ bytes: artifacts.get(name) }, "R21_PROJECT_ARTIFACT_INVALID");
  const built = await prepareAndProject(source, text("npc-persona-seed.json"), text("npc-relationship-projection-policy.json"), runtime);
  if (!equalArtifactMaps(artifacts, built.artifacts)) fail("R21_PROJECTION_REBUILD_MISMATCH");
  if (typeof runtime.verifyNpcDerivedState !== "function") fail("R21_RUNTIME_INTERFACE_INVALID");
  const verified = await runtime.verifyNpcDerivedState({
    prepared: built.prepared,
    worldEventLedgerJson: source.worldEventLedgerJson,
    memoryProjectionJson: text("npc-memory-projection.json"),
    relationshipProjectionJson: text("npc-relationship-projection.json"),
    memoryManifestJson: text("memory-derived-projection-manifest.json"),
    relationshipManifestJson: text("relationship-derived-projection-manifest.json"),
    derivedStateBundleJson: text("npc-derived-state-bundle.json"),
  });
  if (!verified || verified.ok !== true) fail("R21_PROJECTION_VERIFY_FAILED");
  const bundle = JSON.parse(text("npc-derived-state-bundle.json"));
  if (!sameJson(bundle.source, source.sourceIdentity)) fail("R21_R20_SOURCE_IDENTITY_MISMATCH");
  if (bundle.authority?.npcEntityBindingSha256 !== source.sourceIdentity.npcEntityBindingSha256) fail("R21_R20_SOURCE_IDENTITY_MISMATCH");
  return Object.freeze({ built, verified, bundle });
}

function projectRequest(parsed, temporaryRoot) {
  return Object.freeze({ npcRunRoot: parsed.npcRunRoot, runtimePack: parsed.runtimePack, runtimeReceipt: parsed.runtimeReceipt, authorityPolicy: parsed.authorityPolicy, temporaryRoot });
}

export async function runR21Project(args, runtime, { temporaryRoot, ...overrides } = {}) {
  const parsed = parseR21ProjectArguments(args, temporaryRoot);
  const ops = operations(overrides);
  if (await exists(parsed.output, ops)) fail("R21_OUTPUT_EXISTS");
  const trustedRoot = await trustedTemporaryRoot(temporaryRoot, ops);
  const sourceHandle = await acquireSource(projectRequest(parsed, temporaryRoot), ops);
  let result = null;
  let primaryError = null;
  let published = null;
  try {
    const source = inspectQualifiedR20Source(sourceHandle);
    const personaRecord = await readStableR21FileRecord(parsed.personaSeed, NPC_DERIVED_STATE_LIMITS.personaBytes, trustedRoot, ops);
    const policyRecord = await readStableR21FileRecord(parsed.relationshipPolicy, NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes, trustedRoot, ops);
    const persona = canonicalDocument(personaRecord, "R21_PERSONA_SEED_INVALID");
    const policy = canonicalDocument(policyRecord, "R21_RELATIONSHIP_POLICY_INVALID");
    const revalidateInputs = async () => {
      await revalidateQualifiedR20Source(sourceHandle);
      await revalidateInputRecord(personaRecord, NPC_DERIVED_STATE_LIMITS.personaBytes, trustedRoot, ops);
      await revalidateInputRecord(policyRecord, NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes, trustedRoot, ops);
    };
    const projected = await prepareAndProject(source, persona.text, policy.text, runtime);
    await publishR21Artifacts({
      artifacts: projected.artifacts,
      output: parsed.output,
      temporaryRoot: trustedRoot.path,
      verifyDirectory: async (directory) => { const read = await readProjectionDirectory(directory, trustedRoot.path, ops, false); await verifyArtifactMap(read.artifacts, source, runtime); await revalidateProjectionDirectory(read, trustedRoot.path, ops); },
      beforeRename: revalidateInputs,
      afterRename: revalidateInputs,
    }, ops);
    published = Object.freeze({ output: parsed.output, artifacts: projected.artifacts });
    result = Object.freeze({ ok: true, output: parsed.output, bundleSha256: sha256(projected.artifacts.get("npc-derived-state-bundle.json")) });
  } catch (error) {
    primaryError = error;
  }
  await finishQualifiedSource(sourceHandle, primaryError, published, trustedRoot, ops);
  return result;
}

export async function runR21Verify(args, runtime, { temporaryRoot, ...overrides } = {}) {
  const parsed = parseR21VerifyArguments(args, temporaryRoot);
  const ops = operations(overrides);
  const trustedRoot = await trustedTemporaryRoot(temporaryRoot, ops);
  const sourceHandle = await acquireSource(projectRequest(parsed, temporaryRoot), ops);
  let result = null;
  let primaryError = null;
  try {
    const source = inspectQualifiedR20Source(sourceHandle);
    const directory = await readProjectionDirectory(parsed.projectionDir, temporaryRoot, ops, true);
    const projectArtifacts = new Map(R21_PROJECT_FILES.map((name) => [name, directory.artifacts.get(name)]));
    const verifiedArtifacts = await verifyArtifactMap(projectArtifacts, source, runtime);
    if (directory.allowed.includes(R21_QUALIFICATION_REPORT_FILE)) verifyQualificationReportAgainstArtifacts(decode({ bytes: directory.artifacts.get(R21_QUALIFICATION_REPORT_FILE) }, "R21_QUALIFICATION_REPORT_INVALID"), projectArtifacts, source);
    await revalidateQualifiedR20Source(sourceHandle);
    await revalidateProjectionDirectory(directory, temporaryRoot, ops);
    result = Object.freeze({ ok: true, projectionDir: parsed.projectionDir, bundleSha256: sha256(projectArtifacts.get("npc-derived-state-bundle.json")), bundle: verifiedArtifacts.bundle });
  } catch (error) {
    primaryError = error;
  }
  await finishQualifiedSource(sourceHandle, primaryError, null, trustedRoot, ops);
  return result;
}

function rebuildEvidence(artifacts) {
  return Object.freeze({
    personaSeedSha256: sha256(artifacts.get("npc-persona-seed.json")),
    relationshipPolicySha256: sha256(artifacts.get("npc-relationship-projection-policy.json")),
    replayReportSha256: sha256(artifacts.get("world-event-ledger-replay-report.json")),
    bundleSha256: sha256(artifacts.get("npc-derived-state-bundle.json")),
    memoryProjectionSha256: sha256(artifacts.get("npc-memory-projection.json")),
    relationshipProjectionSha256: sha256(artifacts.get("npc-relationship-projection.json")),
    memoryManifestSha256: sha256(artifacts.get("memory-derived-projection-manifest.json")),
    relationshipManifestSha256: sha256(artifacts.get("relationship-derived-projection-manifest.json")),
  });
}

function verifyQualificationReportAgainstArtifacts(reportJson, artifacts, source) {
  requireValid(validateNpcProjectionQualificationReportJson(reportJson), "R21_QUALIFICATION_REPORT_INVALID");
  let report;
  try {
    report = JSON.parse(reportJson);
  } catch {
    fail("R21_QUALIFICATION_REPORT_INVALID");
  }
  const document = (name) => JSON.parse(decode({ bytes: artifacts.get(name) }, "R21_QUALIFICATION_REPORT_INVALID"));
  const bundle = document("npc-derived-state-bundle.json");
  const replay = document("world-event-ledger-replay-report.json");
  const memory = document("npc-memory-projection.json");
  const relationship = document("npc-relationship-projection.json");
  let ledger;
  try {
    ledger = JSON.parse(source.worldEventLedgerJson);
  } catch {
    fail("R21_QUALIFICATION_REPORT_INVALID");
  }
  const evidence = rebuildEvidence(artifacts);
  const acceptedEntries = ledger.entries.filter((entry) => entry.decision.status === "accepted").length;
  const expectedCounts = {
    ledgerEntries: ledger.entries.length,
    acceptedEntries,
    rejectedEntries: ledger.entries.length - acceptedEntries,
    memoryEpisodes: memory.episodes.length,
    relationshipEdges: relationship.relationships.length,
    relationshipContributions: relationship.relationships.reduce((sum, edge) => sum + edge.contributions.length, 0),
  };
  const expectedDeletion = {
    mode: "whole-derived-state",
    derivedArtifactsRemoved: true,
    runtimeSnapshotSha256Before: replay.finalSnapshotSha256,
    runtimeSnapshotSha256After: replay.finalSnapshotSha256,
    ledgerSha256Before: sha256(source.worldEventLedgerJson),
    ledgerSha256After: sha256(source.worldEventLedgerJson),
  };
  if (report.qualifiedBundleSha256 !== evidence.bundleSha256 ||
      !sameJson(report.ledger, bundle.ledger) || !sameJson(report.ledger, memory.ledger) || !sameJson(report.ledger, relationship.ledger) ||
      report.ledger.timelineId !== ledger.timeline.id || report.ledger.canonicalSha256 !== sha256(source.worldEventLedgerJson) || report.ledger.throughRevision !== ledger.revision || report.ledger.throughHeadSha256 !== ledger.headSha256 ||
      !sameJson(report.profile, bundle.profile) || !sameJson(report.rebuilds.initial, evidence) || !sameJson(report.rebuilds.repeated, evidence) || !sameJson(report.rebuilds.afterDeletion, evidence) ||
      !sameJson(report.deletion, expectedDeletion) || !sameJson(report.counts, expectedCounts) || !sameJson(report.markers, R21_QUALIFICATION_MARKERS) ||
      bundle.replay.reportSha256 !== evidence.replayReportSha256 || bundle.replay.finalSnapshotSha256 !== replay.finalSnapshotSha256 || bundle.replay.finalInspectionSha256 !== replay.finalInspectionSha256) fail("R21_QUALIFICATION_REPORT_IDENTITY_MISMATCH");
  return Object.freeze(report);
}

function qualificationReport(artifacts, source, initial, repeated, afterDeletion) {
  const bundle = JSON.parse(decode({ bytes: artifacts.get("npc-derived-state-bundle.json"), }, "R21_QUALIFICATION_REPORT_INVALID"));
  const ledger = JSON.parse(source.worldEventLedgerJson);
  const memory = JSON.parse(decode({ bytes: artifacts.get("npc-memory-projection.json") }, "R21_QUALIFICATION_REPORT_INVALID"));
  const relationship = JSON.parse(decode({ bytes: artifacts.get("npc-relationship-projection.json") }, "R21_QUALIFICATION_REPORT_INVALID"));
  const acceptedEntries = ledger.entries.filter((entry) => entry.decision.status === "accepted").length;
  const report = canonicalizeJsonValue({
    format: "matrix-oasis.npc-projection-qualification-report",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    qualifiedBundleSha256: initial.bundleSha256,
    ledger: bundle.ledger,
    profile: bundle.profile,
    rebuilds: { initial, repeated, afterDeletion, repeatedBuildCount: 20 },
    deletion: { mode: "whole-derived-state", derivedArtifactsRemoved: true, runtimeSnapshotSha256Before: bundle.replay.finalSnapshotSha256, runtimeSnapshotSha256After: bundle.replay.finalSnapshotSha256, ledgerSha256Before: bundle.ledger.canonicalSha256, ledgerSha256After: bundle.ledger.canonicalSha256 },
    counts: { ledgerEntries: ledger.entries.length, acceptedEntries, rejectedEntries: ledger.entries.length - acceptedEntries, memoryEpisodes: memory.episodes.length, relationshipEdges: relationship.relationships.length, relationshipContributions: relationship.relationships.reduce((sum, edge) => sum + edge.contributions.length, 0) },
    isolation: { externalModelCalls: 0, networkRequests: 0, credentialReads: 0 },
    markers: [...R21_QUALIFICATION_MARKERS],
  });
  requireValid(validateNpcProjectionQualificationReportJson(report), "R21_QUALIFICATION_REPORT_INVALID");
  return report;
}

export async function runR21Qualification(args, runtime, { temporaryRoot, ...overrides } = {}) {
  const parsed = parseR21ProjectArguments(args, temporaryRoot);
  const ops = operations(overrides);
  if (await exists(parsed.output, ops)) fail("R21_OUTPUT_EXISTS");
  const trustedRoot = await trustedTemporaryRoot(temporaryRoot, ops);
  const sourceHandle = await acquireSource(projectRequest(parsed, temporaryRoot), ops);
  let probe = null;
  let result = null;
  let primaryError = null;
  let published = null;
  try {
    const source = inspectQualifiedR20Source(sourceHandle);
    const personaRecord = await readStableR21FileRecord(parsed.personaSeed, NPC_DERIVED_STATE_LIMITS.personaBytes, trustedRoot, ops);
    const policyRecord = await readStableR21FileRecord(parsed.relationshipPolicy, NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes, trustedRoot, ops);
    const persona = canonicalDocument(personaRecord, "R21_PERSONA_SEED_INVALID");
    const policy = canonicalDocument(policyRecord, "R21_RELATIONSHIP_POLICY_INVALID");
    const revalidateInputs = async () => {
      await revalidateQualifiedR20Source(sourceHandle);
      await revalidateInputRecord(personaRecord, NPC_DERIVED_STATE_LIMITS.personaBytes, trustedRoot, ops);
      await revalidateInputRecord(policyRecord, NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes, trustedRoot, ops);
    };
    const first = await prepareAndProject(source, persona.text, policy.text, runtime);
    const initialEvidence = rebuildEvidence(first.artifacts);
    let repeatedEvidence = initialEvidence;
    for (let index = 1; index < 20; index += 1) {
      const repeated = await prepareAndProject(source, persona.text, policy.text, runtime);
      if (!equalArtifactMaps(first.artifacts, repeated.artifacts)) fail("R21_PROJECTION_REBUILD_MISMATCH");
      repeatedEvidence = rebuildEvidence(repeated.artifacts);
    }
    await revalidateInputs();
    probe = await writeOwnedDirectory(first.artifacts, trustedRoot, `${path.basename(parsed.output)}-deletion-probe`, ops);
    await verifyArtifactMap(await readOwnedArtifacts(probe, trustedRoot, ops), source, runtime);
    await revalidateInputs();
    await safeRemoveOwnedR21Directory(probe, trustedRoot, ops);
    probe = null;
    await revalidateInputs();
    const rebuilt = await prepareAndProject(source, persona.text, policy.text, runtime);
    if (!equalArtifactMaps(first.artifacts, rebuilt.artifacts)) fail("R21_PROJECTION_REBUILD_MISMATCH");
    const afterDeletion = rebuildEvidence(rebuilt.artifacts);
    const reportJson = qualificationReport(rebuilt.artifacts, source, initialEvidence, repeatedEvidence, afterDeletion);
    const qualified = new Map(rebuilt.artifacts);
    qualified.set(R21_QUALIFICATION_REPORT_FILE, ENCODER.encode(reportJson));
    await publishR21Artifacts({
      artifacts: qualified,
      output: parsed.output,
      temporaryRoot: trustedRoot.path,
      verifyDirectory: async (directory) => {
        const read = await readProjectionDirectory(directory, trustedRoot.path, ops, true);
        const projectArtifacts = new Map(R21_PROJECT_FILES.map((name) => [name, read.artifacts.get(name)]));
        await verifyArtifactMap(projectArtifacts, source, runtime);
        const foundReport = decode({ bytes: read.artifacts.get(R21_QUALIFICATION_REPORT_FILE) }, "R21_QUALIFICATION_REPORT_INVALID");
        if (foundReport !== reportJson) fail("R21_QUALIFICATION_REPORT_INVALID");
        verifyQualificationReportAgainstArtifacts(foundReport, projectArtifacts, source);
        await revalidateProjectionDirectory(read, trustedRoot.path, ops);
      },
      beforeRename: revalidateInputs,
      afterRename: revalidateInputs,
    }, ops);
    published = Object.freeze({ output: parsed.output, artifacts: qualified });
    result = Object.freeze({ ok: true, output: parsed.output, bundleSha256: initialEvidence.bundleSha256, markers: R21_QUALIFICATION_MARKERS });
  } catch (error) {
    primaryError = error;
  }
  if (probe?.created) {
    try {
      await safeRemoveOwnedR21Directory(probe, trustedRoot, ops);
    } catch (error) {
      primaryError = new R21CliOperationalError("R21_PROBE_ROLLBACK_FAILED", primaryError ?? error);
    }
  }
  await finishQualifiedSource(sourceHandle, primaryError, published, trustedRoot, ops);
  return result;
}

export async function validateR21Document(kind, file, temporaryRoot, overrides = {}) {
  const validators = Object.freeze({
    persona: [validateNpcPersonaSeedJson, NPC_DERIVED_STATE_LIMITS.personaBytes],
    "relationship-policy": [validateNpcRelationshipProjectionPolicyJson, NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes],
    memory: [validateNpcMemoryProjectionJson, NPC_DERIVED_STATE_LIMITS.memoryProjectionBytes],
    relationship: [validateNpcRelationshipProjectionJson, NPC_DERIVED_STATE_LIMITS.relationshipProjectionBytes],
    bundle: [validateNpcDerivedStateBundleJson, NPC_DERIVED_STATE_LIMITS.bundleBytes],
    qualification: [validateNpcProjectionQualificationReportJson, NPC_DERIVED_STATE_LIMITS.qualificationReportBytes],
  });
  const selected = validators[kind];
  if (!selected) fail("R21_CLI_ARGUMENT_INVALID");
  const record = await readStableR21FileRecord(file, selected[1], temporaryRoot, overrides);
  return selected[0](decode(record, "R21_DOCUMENT_INVALID"));
}

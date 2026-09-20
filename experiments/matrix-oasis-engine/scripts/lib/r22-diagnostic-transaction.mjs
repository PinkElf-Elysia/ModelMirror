import path from "node:path";
import { lstat, mkdir, open, readdir, realpath, rename } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { types } from "node:util";
import { createNpcCognitionToolUsageDiagnosticPlan, evaluateNpcCognitionToolUsageDiagnosticFixture,
  createNpcCognitionBillingDiagnosticPlan, evaluateNpcCognitionBillingDiagnosticFixture } from "@matrix-oasis/npc-cognition-provider-openai";
import { R22_TOOL_USAGE_CAPTURE_POLICY } from "../../packages/npc-cognition-provider-openai/src/tool-usage-observer.mjs";
import { R22_BILLING_CAPTURE_POLICY } from "../../packages/npc-cognition-provider-openai/src/billing-observer.mjs";
import { openR22DiagnosticBudgetStore, validateR22CognitionSessionManifest } from "./r22-call-store.mjs";
import { canonicalText, sha256, readStableR22File, trustR22TemporaryRoot, directR22TemporaryChild } from "./r22-cli-core.mjs";
import { prepareR22FileCredentialReader } from "./r22-live-provider.mjs";

const CANON = "matrix-oasis.canonical-json/1";
const HASH = /^sha256:[0-9a-f]{64}$/u;
const HEX = /^[0-9a-f]{64}$/u;
const MAX_TRANSACTIONS = 128;
const FORMAT = "matrix-oasis.r22-tool-usage-transaction";
const nativeRequest = globalThis.fetch;
const BASE = Object.freeze({ format: FORMAT, formatVersion: "0.1.0", canonicalization: CANON,
  executionKind: "offline-fixture", realRequestCount: 0, qualificationEligible: false });
const MODES = ["offline-fixture", "injected-transport", "official-once"];
const TRANSPORT_STATUSES = ["response", "credential_unavailable", "timeout", "network_ambiguous", "http_error", "response_invalid", "response_limit"];
const RECORDS = ["transaction-plan.json", "approval-record.json", "dispatch-record.json", "transport-record.json", "observation-record.json", "terminal-record.json"];
const defaults = Object.freeze({ lstat, mkdir, openFile: open, readdir, realpath, rename, randomBytes, clock: () => performance.now(),
  phase: async () => {} });
const fixtureBuffer = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Uint8Array.prototype), "buffer").get;
const fixtureLength = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Uint8Array.prototype), "byteLength").get;
function fail(code = "R22_DIAGNOSTIC_TRANSACTION_INVALID") { throw new Error(code); }
function sanitize(error) { fail(typeof error?.message === "string" && /^R22_[A-Z_]+$/u.test(error.message) ? error.message : "R22_DIAGNOSTIC_TRANSACTION_INVALID"); }
function exact(value, keys) {
  if (!value || typeof value !== "object" || types.isProxy(value) || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Reflect.ownKeys(descriptors).length === keys.length && keys.every((key) => descriptors[key]?.enumerable && Object.hasOwn(descriptors[key], "value"));
}
function frozen(value) { if (value && typeof value === "object" && !Object.isFrozen(value)) { for (const child of Object.values(value)) frozen(child); Object.freeze(value); } return value; }
function integer(value, min, max) { return Number.isSafeInteger(value) && !Object.is(value, -0) && value >= min && value <= max; }
function parse(text) { let value; try { value = JSON.parse(text); if (canonicalText(value) !== text) fail(); } catch { fail(); } return value; }
function sameFile(a, b) { return a.dev === b.dev && a.ino === b.ino && a.size === b.size && a.mtimeNs === b.mtimeNs && a.ctimeNs === b.ctimeNs; }
function budgetKey(plan) {
  const transactionSha256 = sha256(canonicalText(plan));
  const mode = plan.executionKind === "offline-fixture" ? {} : { executionKind: plan.executionKind, sourceProviderMode: plan.sourceProviderMode };
  const purpose = plan.executionKind === "offline-fixture" ? "matrix-oasis.r22-diagnostic-budget/1" : "matrix-oasis.r22-diagnostic-budget/2";
  return { authoritySessionSha256: sha256(canonicalText({ purpose, ...mode, transactionSha256 })),
    callPlanSha256: plan.callPlanSha256, transactionSha256, ...mode };
}
function recordBase(mode = "offline-fixture", version = mode === "offline-fixture" ? "0.1.0" : "0.2.0") {
  if (!MODES.includes(mode) || (mode === "offline-fixture" ? version !== "0.1.0"
    : version !== "0.2.0" && !(mode === "official-once" && ["0.3.0", "0.4.0", "0.5.0"].includes(version)))) fail();
  return mode === "offline-fixture" ? BASE : { format: FORMAT, formatVersion: version, canonicalization: CANON, executionKind: mode,
    sourceProviderMode: mode === "official-once" ? "official-once" : "offline-fake", qualificationEligible: false };
}
function baseRecord(kind, transactionSha256, body, mode, version) { return { ...recordBase(mode, version), kind, transactionSha256, ...body }; }
function fixedPlan(captureProfile = "tool-usage") {
  if (captureProfile === "tool-usage") return createNpcCognitionToolUsageDiagnosticPlan();
  if (captureProfile === "billing") return createNpcCognitionBillingDiagnosticPlan();
  fail();
}
// Preserve the historical transaction envelope and legacy bytes. Its already
// approval-bound capture hash selects exactly one fixed vocabulary, never both.
function profileForPlan(plan) {
  for (const profile of ["tool-usage", "billing"]) if (plan?.capturePolicySha256 === fixedPlan(profile).capturePolicySha256) return profile;
  fail();
}
function captureDisclosure(state) {
  return state.captureProfile === "billing" ? { capturePolicy: R22_BILLING_CAPTURE_POLICY } : {};
}
function environmentConfigured() { return Object.hasOwn(process.env, "MATRIX_OASIS_R22_OPENAI_API_KEY"); }
function credentialConfiguration(state) {
  return state.credentialReader ? { kind: "pinned-file", configured: true, identitySha256: state.credentialReader.sourceBinding.identitySha256 }
    : { kind: "environment", configured: environmentConfigured() };
}

function validatePlan(plan) {
  const expected = fixedPlan(profileForPlan(plan));
  const base = recordBase(plan?.executionKind, plan?.formatVersion);
  const keys = [...Object.keys(base), "kind", "hostRunId", "sourceRootSha256", "sourceSessionManifestSha256", "sourceHostBudgetSha256",
    "outputPathSha256", "callPlanSha256", "diagnosticApprovalSha256", "capturePolicySha256", "callPlan", ...(plan?.executionKind === "offline-fixture" ? [] : ["accountSummary"]),
    ...(["0.3.0", "0.4.0", "0.5.0"].includes(plan?.formatVersion) ? ["credentialSource"] : []),
    ...(plan?.formatVersion === "0.4.0" ? ["additionalAuthorization"] : []),
    ...(plan?.formatVersion === "0.5.0" ? ["billingAuthorization"] : [])];
  if (!exact(plan, keys) || Object.keys(base).some((key) => plan[key] !== base[key]) || plan.kind !== "plan" ||
      typeof plan.hostRunId !== "string" || !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/u.test(plan.hostRunId) || plan.hostRunId.length > 96 ||
      ![plan.sourceRootSha256, plan.sourceSessionManifestSha256, plan.sourceHostBudgetSha256, plan.outputPathSha256].every((value) => HASH.test(value ?? "")) ||
      plan.callPlanSha256 !== sha256(expected.callPlanJson) || plan.diagnosticApprovalSha256 !== expected.diagnosticApprovalSha256 ||
      plan.capturePolicySha256 !== expected.capturePolicySha256 || canonicalText(plan.callPlan) !== expected.callPlanJson) fail();
  if (["0.3.0", "0.4.0", "0.5.0"].includes(plan.formatVersion) && (!exact(plan.credentialSource, ["kind", "identitySha256"]) ||
      plan.credentialSource.kind !== "pinned-file" || !HASH.test(plan.credentialSource.identitySha256 ?? ""))) fail();
  if (plan.formatVersion === "0.4.0") {
    const grant = plan.additionalAuthorization;
    if (!exact(grant, ["kind", "previousTransactionSha256", "previousFormatVersion", "previousTerminalSha256", "previousTransportSha256", "previousBudgetEntrySha256", "officialDiagnosticLimit"]) ||
        grant.kind !== "single-zero-request-credential-repair" || grant.officialDiagnosticLimit !== 2 ||
        !["0.2.0", "0.3.0"].includes(grant.previousFormatVersion) ||
        ![grant.previousTransactionSha256, grant.previousTerminalSha256, grant.previousTransportSha256, grant.previousBudgetEntrySha256].every((value) => typeof value === "string" && HASH.test(value))) fail();
  }
  if (plan.formatVersion === "0.5.0") {
    const grant = plan.billingAuthorization;
    if (profileForPlan(plan) !== "billing" ||
        !exact(grant, ["kind", "previousTransactionSha256", "previousFormatVersion", "previousTerminalSha256", "previousTransportSha256", "previousObservationSha256", "previousBudgetEntrySha256", "officialDiagnosticLimit"]) ||
        grant.kind !== "single-billing-observation" || grant.officialDiagnosticLimit !== 3 || grant.previousFormatVersion !== "0.4.0" ||
        ![grant.previousTransactionSha256, grant.previousTerminalSha256, grant.previousTransportSha256, grant.previousObservationSha256, grant.previousBudgetEntrySha256].every((value) => typeof value === "string" && HASH.test(value))) fail();
  }
  if (plan.executionKind !== "offline-fixture") {
    const account = plan.accountSummary;
    if (!exact(account, ["entryCount", "chargedMicrousd", "reservedMicrousd", "limitMicrousd", "additionalRequestLimit", "additionalCeilingMicrousd"]) ||
        !integer(account.entryCount, 0, 100) || !integer(account.chargedMicrousd, 0, 1000000) || !integer(account.reservedMicrousd, 0, 1000000) ||
        account.chargedMicrousd + account.reservedMicrousd > 1000000 || account.limitMicrousd !== 1000000 ||
        account.additionalRequestLimit !== 1 || account.additionalCeilingMicrousd !== 10000) fail();
  }
  return plan;
}
function validateObservation(value, captureProfile = "tool-usage") {
  const expected = fixedPlan(captureProfile), base = ["profile", "capturePolicySha256", "binding", "semanticCoverage", "qualificationEligible", "status"];
  if (!value || value.profile !== expected.diagnosticProfile || value.capturePolicySha256 !== expected.capturePolicySha256 ||
      value.semanticCoverage !== "observation_only" || value.qualificationEligible !== false ||
      !exact(value.binding, ["callPlanSha256", "diagnosticApprovalSha256"]) || value.binding.callPlanSha256 !== sha256(expected.callPlanJson) ||
      value.binding.diagnosticApprovalSha256 !== expected.diagnosticApprovalSha256) fail();
  if (["not_captured", "failed_limit", "failed_internal"].includes(value.status)) { if (!exact(value, base)) fail(); return; }
  if (captureProfile === "billing") { validateBillingObservation(value, base); return; }
  const jsonTypes = ["null", "object", "array", "string", "number", "boolean"];
  if (value.status !== "observed" || !exact(value, [...base, "rootType", "coverage", "paths", "summary"]) ||
      ![...jsonTypes, "absent"].includes(value.rootType) || !["redacted", "vocabulary_only"].includes(value.coverage) ||
      !Array.isArray(value.paths) || value.paths.length !== 8) fail();
  for (let i = 0; i < value.paths.length; i += 1) {
    const item = value.paths[i], captured = item?.counterStatus === "captured";
    if (!exact(item, ["path", "jsonType", "counterStatus", ...(captured ? ["value"] : [])]) ||
        item.path !== R22_TOOL_USAGE_CAPTURE_POLICY.counterPaths[i]) fail();
    if (captured ? item.jsonType !== "number" || !integer(item.value, 0, 65536)
      : item.counterStatus === "invalid_number" ? item.jsonType !== "number"
      : item.counterStatus === "absent" ? item.jsonType !== "absent"
      : item.counterStatus === "not_captured" ? item.jsonType !== "not-captured"
      : item.counterStatus === "not_counter" ? !jsonTypes.filter((type) => type !== "number").includes(item.jsonType) : true) fail();
  }
  const s = value.summary;
  if (!exact(s, ["nodeCount", "maxDepth", "unknownFieldCount", "typeCounts"]) || !exact(s.typeCounts, jsonTypes) ||
      !integer(s.nodeCount, 0, 128) || !integer(s.maxDepth, 0, 8) || !integer(s.unknownFieldCount, 0, 127) ||
      !Object.values(s.typeCounts).every((number) => integer(number, 0, 128)) || Object.values(s.typeCounts).reduce((a, b) => a + b, 0) !== s.nodeCount ||
      value.coverage !== (s.unknownFieldCount > 0 || s.typeCounts.array > 0 ? "redacted" : "vocabulary_only") ||
      (value.rootType === "absent" && (s.nodeCount !== 0 || s.maxDepth !== 0 || s.unknownFieldCount !== 0 || value.paths.some((item) => item.counterStatus !== "absent"))) ||
      Buffer.byteLength(canonicalText(value)) > 8192) fail();
}
function validateBillingObservation(value, base) {
  const policy = R22_BILLING_CAPTURE_POLICY, jsonTypes = ["null", "object", "array", "string", "number", "boolean"];
  if (value.status !== "observed" || !exact(value, [...base, "rootType", "paths", "summary"]) ||
      ![...jsonTypes, "absent"].includes(value.rootType) || !Array.isArray(value.paths) || value.paths.length !== policy.paths.length) fail();
  for (const [index, item] of value.paths.entries()) {
    if (!exact(item, ["path", "jsonType", "category"]) || item.path !== policy.paths[index]) fail();
    const categories = item.jsonType === "absent" ? ["absent"] : item.jsonType === "not-captured" ? ["not_captured"]
      : item.jsonType === "string" ? ["other_string", ...(Object.hasOwn(policy.stringLiterals, item.path)
        ? policy.stringLiterals[item.path].map((literal) => `literal_${literal}`) : [])]
        : jsonTypes.includes(item.jsonType) ? ["type_only"] : [];
    if (!categories.includes(item.category)) fail();
  }
  const s = value.summary;
  if (!exact(s, ["nodeCount", "maxDepth", "unknownFieldCount", "typeCounts"]) || !exact(s.typeCounts, jsonTypes) ||
      !integer(s.nodeCount, 0, 128) || !integer(s.maxDepth, 0, 8) || !integer(s.unknownFieldCount, 0, 127) ||
      !Object.values(s.typeCounts).every((count) => integer(count, 0, 128)) ||
      Object.values(s.typeCounts).reduce((a, b) => a + b, 0) !== s.nodeCount ||
      s.unknownFieldCount > Math.max(0, s.nodeCount - 1) || s.maxDepth > Math.max(0, s.nodeCount - 1) ||
      (s.nodeCount > 1 && s.maxDepth === 0)) fail();
  if (value.rootType === "absent") {
    if (s.nodeCount !== 0 || s.maxDepth !== 0 || value.paths.some((item) => item.jsonType !== "absent")) fail();
  } else if (s.nodeCount < 1 || s.typeCounts[value.rootType] < 1) fail();
  if (!["absent", "object"].includes(value.rootType) && value.paths.some((item) => item.jsonType !== "not-captured")) fail();
  if (value.rootType === "object" && value.paths.slice(0, -1).some((item) => item.jsonType === "not-captured")) fail();
  if (value.paths.some((item) => jsonTypes.includes(item.jsonType) && s.maxDepth < item.path.split(".").length)) fail();
  if (jsonTypes.filter((type) => !["object", "array"].includes(type)).includes(value.rootType) &&
      (s.nodeCount !== 1 || s.maxDepth !== 0 || s.unknownFieldCount !== 0)) fail();
  const parent = value.paths.find((item) => item.path === "tool_costs"), child = value.paths.at(-1);
  if (parent.jsonType === "absent" ? child.jsonType !== "absent"
    : parent.jsonType !== "object" ? child.jsonType !== "not-captured" : child.jsonType === "not-captured") fail();
  for (const type of jsonTypes) {
    if (value.paths.filter((item) => item.jsonType === type).length + (value.rootType === type ? 1 : 0) > s.typeCounts[type]) fail();
  }
  if (Buffer.byteLength(canonicalText(value)) > policy.limits.maximumObservationBytes) fail();
}
function validateRecords(records, id) {
  const plan = validatePlan(records["transaction-plan.json"]);
  const base = recordBase(plan.executionKind, plan.formatVersion);
  if (sha256(canonicalText(plan)) !== id) fail();
  const approval = records["approval-record.json"], dispatch = records["dispatch-record.json"], transport = records["transport-record.json"], observation = records["observation-record.json"], terminal = records["terminal-record.json"];
  const prefix = (record, kind, keys) => {
    if (!exact(record, [...Object.keys(base), "kind", "transactionSha256", ...keys]) ||
        Object.keys(base).some((key) => record[key] !== base[key]) || record.kind !== kind || record.transactionSha256 !== id) fail();
  };
  if (approval) {
    prefix(approval, "approval", ["disclosureSha256", "approvalTokenSha256", "processEpochSha256"]);
    if (approval.disclosureSha256 !== id || !HASH.test(approval.approvalTokenSha256 ?? "") || !HASH.test(approval.processEpochSha256 ?? "")) fail();
  }
  if (dispatch) {
    prefix(dispatch, "dispatch", ["approvalRecordSha256", "budgetAuthoritySha256", "callPlanSha256", "reservedMicrousd", "requestLimit", "retryLimit"]);
    if (!approval || dispatch.approvalRecordSha256 !== sha256(canonicalText(approval)) || dispatch.budgetAuthoritySha256 !== budgetKey(plan).authoritySessionSha256 ||
        dispatch.callPlanSha256 !== plan.callPlanSha256 || dispatch.reservedMicrousd !== 10000 || dispatch.requestLimit !== 1 || dispatch.retryLimit !== 0) fail();
  }
  if (transport) {
    prefix(transport, "transport", ["dispatchRecordSha256", "status", "requestCount"]);
    if (plan.executionKind === "offline-fixture" || !dispatch || transport.dispatchRecordSha256 !== sha256(canonicalText(dispatch)) ||
        !TRANSPORT_STATUSES.includes(transport.status) || transport.requestCount !== (transport.status === "credential_unavailable" ? 0 : 1)) fail();
  }
  if (observation) {
    prefix(observation, "observation", ["dispatchRecordSha256", "observation", ...(plan.executionKind === "offline-fixture" ? [] : ["transportRecordSha256"])]);
    if (!dispatch || observation.dispatchRecordSha256 !== sha256(canonicalText(dispatch)) ||
        (plan.executionKind !== "offline-fixture" && (transport?.status !== "response" || observation.transportRecordSha256 !== sha256(canonicalText(transport))))) fail();
    validateObservation(observation.observation, profileForPlan(plan));
  }
  if (terminal) {
    if (canonicalText(terminal) !== canonicalText(terminalFor(id, records))) fail();
  }
  return { plan, approval, dispatch, transport, observation, terminal };
}

function fileSystem(temporaryRoot, overrides) {
  const ops = Object.freeze({ ...defaults, ...(overrides ?? {}) });
  if (Object.keys(defaults).some((key) => typeof ops[key] !== "function")) fail();
  let trusted;
  const directories = new Map(), files = new Map();
  const directory = async (target, expected = null) => {
    trusted ??= await trustR22TemporaryRoot(temporaryRoot, ops);
    const relative = path.relative(trusted.path, target);
    if (relative.startsWith("..") || path.isAbsolute(relative)) fail();
    const before = await ops.lstat(target, { bigint: true });
    const previous = directories.get(path.resolve(target));
    if (!before.isDirectory() || before.isSymbolicLink() || path.resolve(await ops.realpath(target)) !== path.resolve(target) ||
        (expected && (expected.dev !== before.dev || expected.ino !== before.ino)) ||
        (previous && (previous.dev !== before.dev || previous.ino !== before.ino))) fail("R22_DIAGNOSTIC_PATH_CHANGED");
    directories.set(path.resolve(target), before);
    return before;
  };
  const file = async (target, limit = 32 * 1024) => {
    await directory(path.dirname(target));
    const before = await ops.lstat(target, { bigint: true });
    if (before.nlink !== 1n) fail("R22_DIAGNOSTIC_PATH_CHANGED");
    const record = await readStableR22File(target, limit, trusted, ops);
    const after = await ops.lstat(target, { bigint: true });
    const previous = files.get(path.resolve(target));
    if (after.nlink !== 1n || !sameFile(before, after) || (previous && !sameFile(previous, after))) fail("R22_DIAGNOSTIC_PATH_CHANGED");
    files.set(path.resolve(target), after);
    return new TextDecoder("utf-8", { fatal: true }).decode(record.bytes);
  };
  const optional = async (target) => { try { await ops.lstat(target, { bigint: true }); } catch (error) { if (error?.code === "ENOENT") return null; throw error; } return file(target); };
  const absent = async (target) => { if (await ops.lstat(target, { bigint: true }).catch((error) => { if (error?.code === "ENOENT") return null; throw error; }) !== null) fail("R22_DIAGNOSTIC_OUTPUT_EXISTS"); };
  const write = async (target, text) => {
    let handle;
    const parent = path.dirname(target), parentIdentity = await directory(parent);
    try {
      handle = await ops.openFile(target, "wx+");
      const before = await handle.stat({ bigint: true });
      if (!before.isFile() || before.nlink !== 1n) fail();
      await handle.writeFile(new TextEncoder().encode(text)); await handle.sync();
      const after = await handle.stat({ bigint: true });
      if (before.dev !== after.dev || before.ino !== after.ino || after.nlink !== 1n || after.size !== BigInt(Buffer.byteLength(text))) fail();
    } finally { await handle?.close().catch(() => {}); }
    await directory(parent, parentIdentity); if (await file(target) !== text) fail();
  };
  const immutable = async (root, name, value, phase) => {
    const text = canonicalText(value), target = path.join(root, name), pending = path.join(root, `.${name}.pending`);
    await absent(target); await absent(pending); const parentIdentity = await directory(root);
    await write(pending, text); await phase(`${name}:staged`);
    const pinned = await ops.lstat(pending, { bigint: true });
    await directory(root, parentIdentity); await absent(target); if (await file(pending) !== text) fail();
    await ops.rename(pending, target);
    const moved = await ops.lstat(target, { bigint: true });
    if (!sameFile(pinned, { ...moved, ctimeNs: pinned.ctimeNs }) || await file(target) !== text) fail();
    await directory(root, parentIdentity); await phase(`${name}:published`);
  };
  const records = async (root) => {
    const pinned = await directory(root), names = (await ops.readdir(root)).sort(), result = {};
    if (names.some((name) => !RECORDS.includes(name))) fail("R22_DIAGNOSTIC_INCOMPLETE_RECORD");
    for (const name of names) result[name] = parse(await file(path.join(root, name)));
    await directory(root, pinned); return result;
  };
  const promotePendingPlan = async (root, text) => {
    const pinnedParent = await directory(root), pending = path.join(root, ".transaction-plan.json.pending"), target = path.join(root, "transaction-plan.json");
    await absent(target); if (await file(pending) !== text) fail();
    const pinned = await ops.lstat(pending, { bigint: true });
    await directory(root, pinnedParent); await absent(target);
    await ops.rename(pending, target);
    const moved = await ops.lstat(target, { bigint: true });
    if (!sameFile(pinned, { ...moved, ctimeNs: pinned.ctimeNs }) || await file(target) !== text) fail();
    await directory(root, pinnedParent);
  };
  return { ops, directory, file, optional, absent, write, immutable, records, promotePendingPlan };
}

async function openAccount(config, overrides, mode = "offline-fixture", prepareCredential = false) {
  if (!config || typeof config !== "object" || types.isProxy(config)) fail();
  const optional = [...(Object.hasOwn(config, "captureProfile") ? ["captureProfile"] : []),
    ...(mode === "official-once" && prepareCredential ? ["credentialFile", "expectedDisclosureSha256", "previousTransactionSha256", "billingAfterTransactionSha256"].filter((key) => Object.hasOwn(config ?? {}, key)) : [])];
  if (!exact(config, ["temporaryRoot", "cognitionRunRoot", "hostRunId", "expectedSessionManifestSha256", "expectedHostBudgetSha256", "output", ...optional]) ||
      (optional.includes("captureProfile") && !["tool-usage", "billing"].includes(config.captureProfile)) ||
      (optional.includes("credentialFile") && typeof config.credentialFile !== "string") ||
      (optional.includes("expectedDisclosureSha256") && !HASH.test(config.expectedDisclosureSha256 ?? "")) ||
      (optional.includes("previousTransactionSha256") && (!optional.includes("credentialFile") ||
        typeof config.previousTransactionSha256 !== "string" || !HASH.test(config.previousTransactionSha256))) ||
      (optional.includes("billingAfterTransactionSha256") && (!optional.includes("credentialFile") || optional.includes("previousTransactionSha256") ||
        config.captureProfile !== "billing" || typeof config.billingAfterTransactionSha256 !== "string" || !HASH.test(config.billingAfterTransactionSha256)))) fail();
  const { output, credentialFile, expectedDisclosureSha256, previousTransactionSha256, billingAfterTransactionSha256, captureProfile = "tool-usage", ...accountConfig } = config;
  const fs = fileSystem(config.temporaryRoot, overrides);
  const trusted = await trustR22TemporaryRoot(config.temporaryRoot, fs.ops);
  const target = directR22TemporaryChild(output, trusted);
  if (target === path.resolve(config.cognitionRunRoot)) fail();
  const account = await openR22DiagnosticBudgetStore(accountConfig, overrides);
  try {
    recordBase(mode);
    if (account.identity.providerMode !== (mode === "official-once" ? "official-once" : "offline-fake")) fail("R22_DIAGNOSTIC_LIVE_DISABLED");
    if (mode === "official-once") {
      const parent = path.join(path.resolve(config.cognitionRunRoot), "diagnostics");
      const exists = await fs.ops.lstat(parent, { bigint: true }).catch((error) => { if (error?.code === "ENOENT") return null; throw error; });
      if (exists) {
        await fs.directory(parent); const names = await fs.ops.readdir(parent);
        if (names.length > 3 || (prepareCredential && names.length >= (billingAfterTransactionSha256 ? 3 : 2)) || names.some((name) => !HEX.test(name))) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
      }
    }
    const credentialReader = optional.includes("credentialFile") ? await prepareR22FileCredentialReader({ credentialFile, temporaryRoot: config.temporaryRoot }) : null;
    return { fs, account, target, config: { ...accountConfig, output }, expectedDisclosureSha256, previousTransactionSha256, billingAfterTransactionSha256,
      credentialReader, root: path.resolve(config.cognitionRunRoot), mode, captureProfile };
  } catch (error) { await account.close(); throw error; }
}
function makePlan(state, initialBudgetJson) {
  const fixed = fixedPlan(state.captureProfile);
  const budget = parse(initialBudgetJson);
  const accountSummary = { entryCount: budget.entries.length,
    chargedMicrousd: budget.entries.reduce((sum, entry) => sum + entry.chargedMicrousd, 0),
    reservedMicrousd: budget.entries.reduce((sum, entry) => sum + (entry.state === "reserved" ? entry.reservedMicrousd : 0), 0),
    limitMicrousd: budget.limitMicrousd, additionalRequestLimit: 1, additionalCeilingMicrousd: 10000 };
  return { ...recordBase(state.mode, state.billingAuthorization ? "0.5.0" : state.additionalAuthorization ? "0.4.0" : state.credentialReader ? "0.3.0" : undefined), kind: "plan", hostRunId: state.config.hostRunId, sourceRootSha256: sha256(state.root.toLowerCase()),
    sourceSessionManifestSha256: state.config.expectedSessionManifestSha256, sourceHostBudgetSha256: sha256(initialBudgetJson),
    outputPathSha256: sha256(state.target.toLowerCase()), callPlanSha256: sha256(fixed.callPlanJson),
    diagnosticApprovalSha256: fixed.diagnosticApprovalSha256, capturePolicySha256: fixed.capturePolicySha256, callPlan: parse(fixed.callPlanJson),
    ...(state.mode === "offline-fixture" ? {} : { accountSummary }),
    ...(state.credentialReader ? { credentialSource: state.credentialReader.sourceBinding } : {}),
    ...(state.additionalAuthorization ? { additionalAuthorization: state.additionalAuthorization } : {}),
    ...(state.billingAuthorization ? { billingAuthorization: state.billingAuthorization } : {}) };
}

// This is a new, one-time authorization, not a retry of a consumed claim.
// Both the immutable zero-request failure and its conservative charge remain.
function additionalAuthorizationFor(previous) {
  const { plan, terminal, transport, budgetEntry } = previous;
  if (plan.executionKind !== "official-once" || !["0.2.0", "0.3.0"].includes(plan.formatVersion) ||
      !terminal || terminal.state !== "transport_failed" || terminal.realRequestCount !== 0 || terminal.chargedMicrousd !== 10000 ||
      !transport || transport.status !== "credential_unavailable" || transport.requestCount !== 0 ||
      !budgetEntry || budgetEntry.state !== "charged" || budgetEntry.chargedMicrousd !== 10000) fail("R22_DIAGNOSTIC_ADDITIONAL_AUTHORIZATION_INVALID");
  return { kind: "single-zero-request-credential-repair", previousTransactionSha256: sha256(canonicalText(plan)),
    previousFormatVersion: plan.formatVersion, previousTerminalSha256: sha256(canonicalText(terminal)),
    previousTransportSha256: sha256(canonicalText(transport)), previousBudgetEntrySha256: sha256(canonicalText(budgetEntry)), officialDiagnosticLimit: 2 };
}
function validateClosedBudgetRecord(value, budget) {
  const { plan, approval, dispatch, terminal } = value, key = budgetKey(plan);
  const entry = budget.entries.find((item) => item.authoritySessionSha256 === key.authoritySessionSha256 && item.callPlanSha256 === key.callPlanSha256);
  if (!terminal || (entry && !approval) || (dispatch && !entry) ||
      (entry && (entry.state !== (dispatch ? "charged" : "released") || entry.chargedMicrousd !== (dispatch ? 10000 : 0)))) fail("R22_DIAGNOSTIC_RECOVERY_REQUIRED");
  return entry;
}
function validateAdditionalLink(plan, previous) {
  if (canonicalText(plan.additionalAuthorization) !== canonicalText(additionalAuthorizationFor(previous)) ||
      plan.hostRunId !== previous.plan.hostRunId || plan.sourceRootSha256 !== previous.plan.sourceRootSha256 ||
      plan.sourceSessionManifestSha256 !== previous.plan.sourceSessionManifestSha256 || plan.outputPathSha256 === previous.plan.outputPathSha256) fail("R22_DIAGNOSTIC_ADDITIONAL_AUTHORIZATION_INVALID");
}
// An explicit third claim is limited to the new observation vocabulary. The
// completed tool observation and its failed credential predecessor stay intact.
function billingAuthorizationFor(previous) {
  const { plan, terminal, transport, observation, budgetEntry } = previous;
  if (plan.executionKind !== "official-once" || plan.formatVersion !== "0.4.0" || profileForPlan(plan) !== "tool-usage" ||
      !terminal || terminal.state !== "observed" || terminal.realRequestCount !== 1 || terminal.chargedMicrousd !== 10000 ||
      !transport || transport.status !== "response" || transport.requestCount !== 1 || observation?.observation?.status !== "observed" ||
      !budgetEntry || budgetEntry.state !== "charged" || budgetEntry.chargedMicrousd !== 10000) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
  return { kind: "single-billing-observation", previousTransactionSha256: sha256(canonicalText(plan)), previousFormatVersion: plan.formatVersion,
    previousTerminalSha256: sha256(canonicalText(terminal)), previousTransportSha256: sha256(canonicalText(transport)),
    previousObservationSha256: sha256(canonicalText(observation)), previousBudgetEntrySha256: sha256(canonicalText(budgetEntry)), officialDiagnosticLimit: 3 };
}
function validateBillingLink(plan, previous) {
  if (canonicalText(plan.billingAuthorization) !== canonicalText(billingAuthorizationFor(previous)) ||
      canonicalText(plan.credentialSource) !== canonicalText(previous.plan.credentialSource) ||
      plan.hostRunId !== previous.plan.hostRunId || plan.sourceRootSha256 !== previous.plan.sourceRootSha256 ||
      plan.sourceSessionManifestSha256 !== previous.plan.sourceSessionManifestSha256 || plan.outputPathSha256 === previous.plan.outputPathSha256) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
}
function validateOfficialHistory(official) {
  if (official.length === 3) {
    const billing = official.filter((item) => item.plan.formatVersion === "0.5.0");
    const previous = official.find((item) => item.plan.formatVersion === "0.4.0");
    if (billing.length !== 1 || !previous) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    validateBillingLink(billing[0].plan, previous);
    const prefix = official.filter((item) => item !== billing[0]);
    if (prefix.some((item) => item.plan.outputPathSha256 === billing[0].plan.outputPathSha256)) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    validateOfficialHistory(prefix);
  } else if (official.some((item) => item.plan.formatVersion === "0.5.0")) {
    fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
  } else if (official.length === 2) {
    const additional = official.filter((item) => item.plan.formatVersion === "0.4.0");
    if (additional.length !== 1) fail();
    validateAdditionalLink(additional[0].plan, official.find((item) => item !== additional[0]));
  } else if (official.some((item) => item.plan.formatVersion === "0.4.0")) {
    fail("R22_DIAGNOSTIC_ADDITIONAL_AUTHORIZATION_INVALID");
  }
}
async function officialClaimIds(state) {
  const parent = path.join(state.root, "diagnostics");
  const exists = await state.fs.ops.lstat(parent, { bigint: true }).catch((error) => { if (error?.code === "ENOENT") return null; throw error; });
  if (!exists) return [];
  await state.fs.directory(parent);
  const ids = await state.fs.ops.readdir(parent);
  if (ids.length > 3 || ids.some((id) => !HEX.test(id))) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  return ids;
}
async function readClosedOfficialRecord(state, previousId) {
  const previous = validateRecords(await state.fs.records(path.join(state.root, "diagnostics", previousId.slice(7))), previousId);
  if (previous.plan.executionKind !== "official-once" || previous.plan.hostRunId !== state.config.hostRunId || previous.plan.sourceRootSha256 !== sha256(state.root.toLowerCase()) ||
      previous.plan.sourceSessionManifestSha256 !== state.config.expectedSessionManifestSha256) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
  previous.budgetEntry = validateClosedBudgetRecord(previous, parse((await state.account.inspect()).canonicalBudgetJson));
  return previous;
}
async function readAdditionalPredecessor(state, previousId) {
  const previous = await readClosedOfficialRecord(state, previousId);
  additionalAuthorizationFor(previous);
  return previous;
}
async function readBillingPredecessor(state, previousId) {
  const previous = await readClosedOfficialRecord(state, previousId);
  billingAuthorizationFor(previous);
  validateAdditionalLink(previous.plan, await readAdditionalPredecessor(state, previous.plan.additionalAuthorization.previousTransactionSha256));
  return previous;
}
async function prepareOfficialClaim(state) {
  if (state.mode !== "official-once") return;
  const ids = await officialClaimIds(state);
  if (state.billingAfterTransactionSha256) {
    if (ids.length !== 2 || !ids.includes(state.billingAfterTransactionSha256.slice(7))) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    const initial = await state.account.inspect();
    if (parse(initial.canonicalBudgetJson).entries.length >= 100 || initial.charged + 10000 > 1000000) fail("R22_BUDGET_EXHAUSTED");
    const previous = await readBillingPredecessor(state, state.billingAfterTransactionSha256);
    if (credentialConfiguration(state).identitySha256 !== previous.plan.credentialSource.identitySha256) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    if (!ids.includes(previous.plan.additionalAuthorization.previousTransactionSha256.slice(7))) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    state.billingAuthorization = frozen(billingAuthorizationFor(previous));
    return;
  }
  if (!state.previousTransactionSha256) {
    if (ids.length !== 0) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    return;
  }
  if (ids.length !== 1 || ids[0] !== state.previousTransactionSha256.slice(7)) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  const initial = await state.account.inspect();
  if (parse(initial.canonicalBudgetJson).entries.length >= 100 || initial.charged + 10000 > 1000000) fail("R22_BUDGET_EXHAUSTED");
  state.additionalAuthorization = frozen(additionalAuthorizationFor(await readAdditionalPredecessor(state, state.previousTransactionSha256)));
}
async function revalidateOfficialClaim(state, id, plan) {
  if (state.mode !== "official-once") return;
  const ids = await officialClaimIds(state);
  if (plan.formatVersion === "0.5.0") {
    const previousId = plan.billingAuthorization.previousTransactionSha256;
    if (ids.length !== 3 || previousId === id || !ids.includes(id.slice(7)) || !ids.includes(previousId.slice(7))) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    const previous = await readBillingPredecessor(state, previousId);
    if (!ids.includes(previous.plan.additionalAuthorization.previousTransactionSha256.slice(7))) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    validateBillingLink(plan, previous);
    const first = await readAdditionalPredecessor(state, previous.plan.additionalAuthorization.previousTransactionSha256);
    if (first.plan.outputPathSha256 === plan.outputPathSha256) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    return;
  }
  if (ids.length === 3 && ids.includes(id.slice(7))) {
    // Old terminal recovery is read-only with respect to its immutable record.
    // It cannot dispatch again: every member must already be fully terminal.
    const official = [];
    for (const hex of ids) official.push(await readClosedOfficialRecord(state, `sha256:${hex}`));
    validateOfficialHistory(official);
    return;
  }
  if (plan.formatVersion !== "0.4.0") {
    if (ids.length !== 1 || ids[0] !== id.slice(7)) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    return;
  }
  const previousId = plan.additionalAuthorization.previousTransactionSha256;
  if (ids.length !== 2 || previousId === id || !ids.includes(id.slice(7)) || !ids.includes(previousId.slice(7))) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  validateAdditionalLink(plan, await readAdditionalPredecessor(state, previousId));
}
function validateSource(state, plan) {
  if (plan.capturePolicySha256 !== fixedPlan(state.captureProfile).capturePolicySha256 ||
      plan.executionKind !== state.mode || plan.hostRunId !== state.config.hostRunId || plan.sourceRootSha256 !== sha256(state.root.toLowerCase()) ||
      plan.sourceSessionManifestSha256 !== state.config.expectedSessionManifestSha256 || plan.outputPathSha256 !== sha256(state.target.toLowerCase())) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
}
function terminalFor(id, records) {
  const mode = records["transaction-plan.json"].executionKind;
  const dispatch = records["dispatch-record.json"], transport = records["transport-record.json"], observation = records["observation-record.json"];
  const requestCount = transport ? transport.requestCount : dispatch ? null : 0;
  return baseRecord("terminal", id, { dispatchRecordSha256: dispatch ? sha256(canonicalText(dispatch)) : null,
    observationRecordSha256: observation ? sha256(canonicalText(observation)) : null,
    state: observation ? "observed" : transport && transport.status !== "response" ? "transport_failed" : dispatch ? "dispatch_uncertain" : "cancelled",
    dispatchCount: dispatch ? 1 : 0, chargedMicrousd: dispatch ? 10000 : 0, providerReplayRequests: 0,
    ...(mode === "offline-fixture" ? {} : { transportRecordSha256: transport ? sha256(canonicalText(transport)) : null,
      transportRequestCount: requestCount, realRequestCount: mode === "official-once" ? requestCount : 0, requestCountUpperBound: dispatch ? 1 : 0 }) }, mode, records["transaction-plan.json"].formatVersion);
}
async function publish(state, id, records, terminal) {
  const { fs, target } = state;
  const artifacts = { "transaction-plan.json": records["transaction-plan.json"], "diagnostic-report.json": terminal,
    ...(records["approval-record.json"] ? { "approval-record.json": records["approval-record.json"] } : {}),
    ...(records["dispatch-record.json"] ? { "dispatch-record.json": records["dispatch-record.json"] } : {}),
    ...(records["transport-record.json"] ? { "transport-record.json": records["transport-record.json"] } : {}),
    ...(records["observation-record.json"] ? { "observation-record.json": records["observation-record.json"] } : {}) };
  const names = Object.keys(artifacts).sort();
  const verify = async (root) => {
    const directory = await fs.directory(root);
    if ((await fs.ops.readdir(root)).sort().join("\0") !== names.join("\0")) fail("R22_DIAGNOSTIC_OUTPUT_INVALID");
    for (const name of names) if (await fs.file(path.join(root, name)) !== canonicalText(artifacts[name])) fail("R22_DIAGNOSTIC_OUTPUT_INVALID");
    await fs.directory(root, directory); return directory;
  };
  const stage = path.join(path.dirname(target), `.r22d-${id.slice(7)}.pending`);
  const targetExists = await fs.ops.lstat(target, { bigint: true }).catch((error) => { if (error?.code === "ENOENT") return null; throw error; });
  if (targetExists) {
    await fs.absent(stage); await verify(target); return;
  }
  const stageExists = await fs.ops.lstat(stage, { bigint: true }).catch((error) => { if (error?.code === "ENOENT") return null; throw error; });
  if (!stageExists) {
    await fs.ops.mkdir(stage); await fs.directory(stage);
    for (const name of names) { await fs.write(path.join(stage, name), canonicalText(artifacts[name])); await fs.ops.phase(`output:${name}:written`); }
  }
  const identity = await verify(stage);
  await fs.ops.phase("output:staged"); await state.account.revalidate();
  await revalidateOfficialClaim(state, id, records["transaction-plan.json"]);
  await fs.absent(target); await fs.directory(stage, identity);
  const parentIdentity = await fs.directory(path.dirname(target));
  await fs.ops.rename(stage, target); await fs.directory(path.dirname(target), parentIdentity);
  await fs.directory(target, identity); await verify(target); await fs.ops.phase("output:published");
}
async function finish(state, id, root) {
  const records = await state.fs.records(root), validated = validateRecords(records, id);
  validateSource(state, validated.plan);
  await revalidateOfficialClaim(state, id, validated.plan);
  const key = budgetKey(validated.plan), budget = parse((await state.account.inspect()).canonicalBudgetJson);
  const entry = budget.entries.find((value) => value.authoritySessionSha256 === key.authoritySessionSha256 && value.callPlanSha256 === key.callPlanSha256);
  if ((validated.dispatch && !entry) || (entry && !validated.approval)) fail();
  if (entry) await state.account.settle(key, Boolean(validated.dispatch));
  await state.fs.ops.phase("budget:settled");
  const terminal = terminalFor(id, records);
  await publish(state, id, records, terminal);
  await revalidateOfficialClaim(state, id, validated.plan);
  if (!validated.terminal) await state.fs.immutable(root, "terminal-record.json", terminal, state.fs.ops.phase);
  return frozen({ transactionSha256: id, terminal, output: state.target });
}

// Private transport: no exported key/URL/payload executor. The only caller is
// createTransaction after its durable approval, reservation and dispatch gates.
async function sendDiagnosticRequest(operations, revalidateDispatch, phase, captureProfile) {
  const timeoutMs = operations.timeoutMs;
  const controller = new AbortController();
  const expired = Symbol("deadline");
  let timer, reader = null, response = null, invoked = false;
  const timeout = new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(expired); }, timeoutMs); });
  timeout.catch(() => {});
  const wait = (value) => Promise.race([Promise.resolve(value), timeout]);
  const result = (status, bytes) => ({ status, requestCount: invoked ? 1 : 0, ...(bytes ? { bytes } : {}) });
  const cancel = () => { try { Promise.resolve(reader ? reader.cancel() : response?.body?.cancel()).catch(() => {}); } catch { /* Never persist a transport error. */ } };
  try {
    await phase("credential:before_read"); await revalidateDispatch();
    let credential;
    try { credential = await wait(operations.readCredential()); } catch { return result("credential_unavailable"); }
    await phase("credential:read");
    if (typeof credential !== "string" || credential.length > 8192 ||
        (operations.fixture ? credential !== "offline-placeholder-fixture" : !/^sk-[A-Za-z0-9_-]{16,8189}$/u.test(credential) || /^sk-or-/iu.test(credential))) return result("credential_unavailable");
    await revalidateDispatch();
    if (controller.signal.aborted) return result("credential_unavailable");
    const fixed = fixedPlan(captureProfile), callPlan = parse(fixed.callPlanJson);
    let pending;
    invoked = true;
    try {
      pending = Promise.resolve(operations.requestImplementation(callPlan.endpoint, {
        method: "POST", redirect: "error", credentials: "omit", cache: "no-store", signal: controller.signal,
        headers: { accept: "application/json", "content-type": "application/json", authorization: `Bearer ${credential}` },
        body: fixed.providerRequestJson,
      }));
      pending.catch(() => {});
      pending.then((late) => { if (controller.signal.aborted) { try { Promise.resolve(late?.body?.cancel()).catch(() => {}); } catch { /* Discard late bytes. */ } } }, () => {});
    } catch { return result(controller.signal.aborted ? "timeout" : "network_ambiguous"); }
    credential = null;
    await phase("transport:invoked");
    try { response = await wait(pending); }
    catch (error) { return result(error === expired || controller.signal.aborted ? "timeout" : "network_ambiguous"); }
    try {
      if (response?.redirected !== false || (response.url !== "" && response.url !== callPlan.endpoint)) return result("network_ambiguous");
      if (!integer(response.status, 100, 599)) return result("response_invalid");
      if (response.status >= 300 && response.status < 400) return result("network_ambiguous");
      if (response.status >= 400) return result("http_error");
      if (response.status !== 200 || !/^application\/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*$/iu.test(response.headers?.get("content-type") ?? "")) return result("response_invalid");
      const length = response.headers.get("content-length");
      if (length !== null && (!/^(?:0|[1-9][0-9]{0,6})$/u.test(length) || Number(length) > 65536)) return result("response_limit");
      reader = response.body?.getReader();
      if (!reader) return result("response_invalid");
      const bytes = new Uint8Array(65536); let total = 0, chunks = 0;
      while (true) {
        const chunk = await wait(reader.read());
        if (chunk.done === true) break;
        if (++chunks > 4096 || types.isProxy(chunk.value) || !types.isUint8Array(chunk.value) ||
            types.isSharedArrayBuffer(fixtureBuffer.call(chunk.value)) || fixtureLength.call(chunk.value) > bytes.byteLength - total) return result("response_limit");
        Uint8Array.prototype.set.call(bytes, chunk.value, total); total += fixtureLength.call(chunk.value);
      }
      if (length !== null && Number(length) !== total) return result("response_invalid");
      if (controller.signal.aborted) return result("timeout");
      return result("response", bytes.slice(0, total));
    } catch (error) { return result(error === expired || controller.signal.aborted ? "timeout" : "response_invalid"); }
  } finally { clearTimeout(timer); controller.abort(); cancel(); }
}

// The real factory deliberately accepts no operations, credential, sender or
// mode override. Optional credentialFile selects the existing pinned, deferred
// reader; it never supplies executable code or a credential value.
export async function createR22OfficialToolUsageDiagnosticTransaction(config) {
  if (arguments.length !== 1) fail("R22_APPROVAL_MISMATCH");
  if (typeof nativeRequest !== "function") fail("R22_DIAGNOSTIC_LIVE_DISABLED");
  return createTransaction(config, undefined, "official-once", Object.freeze({
    fixture: false, timeoutMs: 30000,
    readCredential: () => process.env.MATRIX_OASIS_R22_OPENAI_API_KEY,
    requestImplementation: (endpoint, options) => nativeRequest(endpoint, options),
  }));
}

// Data-only transport matrix. The caller cannot supply a sender or key reader.
// The existing filesystem/clock/phase fault seams remain a trusted test harness,
// not a security sandbox for arbitrary same-process code.
export async function createR22InjectedToolUsageDiagnosticTransaction(config, fixture, overrides) {
  const scenarios = ["response", "network-throw", "network-reject", "request-timeout", "body-timeout", "late-response", "late-reject", "credential-missing", "credential-error", "credential-timeout"];
  if (!exact(fixture, ["scenario", "responseBytes", "status", "contentType", "contentLength", "redirected", "responseUrl", "chunkSize", "timeoutMs"]) ||
      !scenarios.includes(fixture.scenario) || types.isProxy(fixture.responseBytes) || !types.isUint8Array(fixture.responseBytes) ||
      types.isSharedArrayBuffer(fixtureBuffer.call(fixture.responseBytes)) || fixtureLength.call(fixture.responseBytes) > 65537 ||
      !integer(fixture.status, 100, 599) || typeof fixture.contentType !== "string" || fixture.contentType.length > 128 ||
      !(fixture.contentLength === null || typeof fixture.contentLength === "string" && fixture.contentLength.length <= 32) ||
      typeof fixture.redirected !== "boolean" || !["locked", "empty", "other"].includes(fixture.responseUrl) ||
      !integer(fixture.chunkSize, 1, 65537) || !integer(fixture.timeoutMs, 1, 30000)) fail();
  const f = { ...fixture, responseBytes: new Uint8Array(fixtureLength.call(fixture.responseBytes)) };
  Uint8Array.prototype.set.call(f.responseBytes, fixture.responseBytes);
  const memoryResponse = () => {
    const headers = new Headers({ "content-type": f.contentType });
    if (f.contentLength !== null) headers.set("content-length", f.contentLength);
    let offset = 0;
    return { status: f.status, redirected: f.redirected, headers,
      url: f.responseUrl === "locked" ? parse(fixedPlan().callPlanJson).endpoint : f.responseUrl === "empty" ? "" : "unapproved",
      body: new ReadableStream({ pull(controller) {
        if (f.scenario === "body-timeout") return new Promise(() => {});
        if (offset >= f.responseBytes.length) { controller.close(); return; }
        const end = Math.min(offset + f.chunkSize, f.responseBytes.length); controller.enqueue(f.responseBytes.slice(offset, end)); offset = end;
      } }) };
  };
  const operations = Object.freeze({ fixture: true, timeoutMs: f.timeoutMs,
    readCredential() {
      if (f.scenario === "credential-error") throw new Error("fixture");
      if (f.scenario === "credential-timeout") return new Promise(() => {});
      return f.scenario === "credential-missing" ? undefined : "offline-placeholder-fixture";
    },
    requestImplementation() {
      if (f.scenario === "network-throw") throw new Error("fixture");
      if (f.scenario === "network-reject") return Promise.reject(new Error("fixture"));
      if (f.scenario === "request-timeout") return new Promise(() => {});
      if (["late-response", "late-reject"].includes(f.scenario)) return new Promise((resolve, reject) => setTimeout(() => {
        if (f.scenario === "late-reject") reject(new Error("fixture")); else resolve(memoryResponse());
      }, f.timeoutMs * 2));
      return memoryResponse();
    },
  });
  return createTransaction(config, overrides, "injected-transport", operations);
}

export async function recoverR22OfficialToolUsageDiagnosticTransaction(config, id) {
  if (arguments.length > 2) fail("R22_APPROVAL_MISMATCH");
  return recoverTransaction(config, id, undefined, "official-once");
}
export async function recoverR22InjectedToolUsageDiagnosticTransaction(config, id, overrides) {
  return recoverTransaction(config, id, overrides, "injected-transport");
}

// Disclosure only. No diagnostic directory/approval/reservation is created and
// no credential is read. The short-lived existing writer protects the read.
export async function describeR22OfficialToolUsageDiagnosticTransaction(config) {
  if (arguments.length !== 1) fail("R22_APPROVAL_MISMATCH");
  const state = await openAccount(config, undefined, "official-once", true).catch(sanitize);
  try {
    const initial = await state.account.inspect();
    if (initial.reserved !== 0) fail("R22_CALL_IN_FLIGHT");
    const budget = parse(initial.canonicalBudgetJson);
    if (budget.entries.length >= 100 || initial.charged + 10000 > budget.limitMicrousd) fail("R22_BUDGET_EXHAUSTED");
    await state.fs.absent(state.target);
    await prepareOfficialClaim(state);
    await state.account.revalidate();
    const plan = makePlan(state, initial.canonicalBudgetJson);
    return frozen({ transactionSha256: sha256(canonicalText(plan)), diagnosticPlan: fixedPlan(state.captureProfile), transactionPlan: plan, ...captureDisclosure(state),
      credentialConfiguration: credentialConfiguration(state), output: state.target });
  } catch (error) { sanitize(error); }
  finally { await state.account.close(); }
}

// Offline-only durable harness. No credential parameter, remote executor,
// environment reader, Runtime, Ledger, qualification or Godot write capability.
export async function createR22ToolUsageDiagnosticTransaction(config, overrides) {
  return createTransaction(config, overrides, "offline-fixture");
}

async function createTransaction(config, overrides, mode, transportOperations = null) {
  const state = await openAccount(config, overrides, mode, mode === "official-once").catch(sanitize);
  try {
    const initial = await state.account.inspect();
    if (initial.reserved !== 0) fail("R22_CALL_IN_FLIGHT");
    await state.fs.absent(state.target);
    await prepareOfficialClaim(state);
    const plan = makePlan(state, initial.canonicalBudgetJson), id = sha256(canonicalText(plan)), parent = path.join(state.root, "diagnostics"), root = path.join(parent, id.slice(7));
    // Compare the newly pinned source inside the same writer, before even the
    // diagnostics parent exists. A changed source must not consume the claim.
    if ((state.credentialReader || state.expectedDisclosureSha256) && state.expectedDisclosureSha256 !== id) fail("R22_APPROVAL_MISMATCH");
    if (state.expectedDisclosureSha256 && !credentialConfiguration(state).configured) fail("R22_DIAGNOSTIC_CREDENTIAL_NOT_CONFIGURED");
    const parentExists = await state.fs.ops.lstat(parent, { bigint: true }).catch((error) => { if (error?.code === "ENOENT") return null; throw error; });
    if (!parentExists) await state.fs.ops.mkdir(parent);
    await state.fs.directory(parent);
    const ids = await state.fs.ops.readdir(parent);
    if (ids.some((hex) => !HEX.test(hex))) fail();
    // A new diagnostic is an extra, separately approved single request against
    // the existing account, not a reset of its ordinary one-shot history.
    // mkdir consumes this lane's claim even if create/cancel later fails.
    if (mode === "official-once" && state.billingAuthorization) {
      if (ids.length !== 2 || !ids.includes(state.billingAfterTransactionSha256.slice(7))) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
      const previous = await readBillingPredecessor(state, state.billingAfterTransactionSha256);
      if (!ids.includes(previous.plan.additionalAuthorization.previousTransactionSha256.slice(7))) fail("R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
      validateBillingLink(plan, previous);
    } else if (mode === "official-once" && (state.additionalAuthorization ? ids.length !== 1 || ids[0] !== state.previousTransactionSha256.slice(7) : ids.length !== 0)) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    if (ids.length >= MAX_TRANSACTIONS) fail("R22_DIAGNOSTIC_TRANSACTION_LIMIT");
    // The same writer protects enumeration, history validation and mkdir.
    // A cancelled zero-budget transaction still consumes a history slot.
    await auditR22DiagnosticBudgetHistory({ cognitionRunRoot: state.root, hostRunId: config.hostRunId, budget: parse(initial.canonicalBudgetJson),
      remember: (file, limit) => state.fs.file(file, limit),
      rememberDirectory: async (directory) => { await state.fs.directory(directory); return state.fs.ops.readdir(directory); } });
    await state.account.revalidate(); await state.fs.absent(root); await state.fs.ops.mkdir(root);
    await state.fs.ops.phase("transaction:created");
    await revalidateOfficialClaim(state, id, plan);
    await state.fs.immutable(root, "transaction-plan.json", plan, state.fs.ops.phase);
    let approval = null, expiry = null, claimed = null, result = null, closed = false, closing = false, closeAttempt = null;
    let queue = Promise.resolve();
    const exclusive = (operation) => {
      if (closing || closed) return Promise.reject(new Error("R22_DIAGNOSTIC_STORE_UNAVAILABLE"));
      const next = queue.then(async () => { if (closed) fail(); await state.account.revalidate(); return operation(); }).catch(sanitize); queue = next.catch(() => {}); return next;
    };
    let previousClock = 0;
    const now = () => { const value = state.fs.ops.clock(); if (!Number.isFinite(value) || value < previousClock || value > Number.MAX_SAFE_INTEGER - 300000) fail(); previousClock = value; return value; };
    return Object.freeze({
      disclosure: frozen({ transactionSha256: id, diagnosticPlan: fixedPlan(state.captureProfile), transactionPlan: plan, ...captureDisclosure(state), output: state.target }),
      approve(input) { return exclusive(async () => {
        if (claimed !== null) fail("R22_CALL_IN_FLIGHT");
        await revalidateOfficialClaim(state, id, plan);
        if (!exact(input, ["disclosureSha256"]) || input.disclosureSha256 !== id) fail("R22_APPROVAL_MISMATCH");
        if (approval !== null) return frozen({ approvalTokenSha256: approval.approvalTokenSha256 });
        const random = state.fs.ops.randomBytes(32); if (!types.isUint8Array(random) || random.byteLength !== 32) fail();
        const clock = now(); expiry = clock + 300000;
        approval = baseRecord("approval", id, { disclosureSha256: id, approvalTokenSha256: sha256(random), processEpochSha256: state.account.identity.processEpochSha256 }, mode, plan.formatVersion);
        await state.fs.immutable(root, "approval-record.json", approval, state.fs.ops.phase);
        return frozen({ approvalTokenSha256: approval.approvalTokenSha256 });
      }); },
      execute(input) {
        const offline = mode === "offline-fixture";
        if (!exact(input, offline ? ["approvalTokenSha256", "responseBytes"] : ["approvalTokenSha256"]) || !HASH.test(input.approvalTokenSha256 ?? "") ||
            (offline && (types.isProxy(input.responseBytes) || !types.isUint8Array(input.responseBytes) ||
            types.isSharedArrayBuffer(fixtureBuffer.call(input.responseBytes)) || fixtureLength.call(input.responseBytes) > 65537))) return Promise.reject(new Error("R22_APPROVAL_MISMATCH"));
        let bytes = null;
        if (offline) { bytes = new Uint8Array(fixtureLength.call(input.responseBytes)); Uint8Array.prototype.set.call(bytes, input.responseBytes); }
        const identity = `${input.approvalTokenSha256}:${offline ? sha256(bytes) : mode}`;
        if (claimed !== null) return identity === claimed ? result : Promise.reject(new Error("R22_APPROVAL_MISMATCH"));
        claimed = identity;
        result = exclusive(async () => {
          if (!approval || input.approvalTokenSha256 !== approval.approvalTokenSha256) fail("R22_APPROVAL_MISMATCH");
          if (now() >= expiry) fail("R22_DIAGNOSTIC_APPROVAL_EXPIRED");
          const records = await state.fs.records(root); validateRecords(records, id);
          if (canonicalText(records["approval-record.json"]) !== canonicalText(approval)) fail("R22_APPROVAL_MISMATCH");
          await state.fs.ops.phase("budget:before_reserve");
          await revalidateOfficialClaim(state, id, plan);
          const reservation = await state.account.reserve(budgetKey(plan));
          if (!reservation.ok) fail(reservation.diagnosticCode);
          await state.fs.ops.phase("budget:reserved");
          if (now() >= expiry) fail("R22_DIAGNOSTIC_APPROVAL_EXPIRED");
          const dispatch = baseRecord("dispatch", id, { approvalRecordSha256: sha256(canonicalText(approval)),
            budgetAuthoritySha256: budgetKey(plan).authoritySessionSha256, callPlanSha256: plan.callPlanSha256,
            reservedMicrousd: 10000, requestLimit: 1, retryLimit: 0 }, mode, plan.formatVersion);
          await state.account.revalidate(); await state.fs.immutable(root, "dispatch-record.json", dispatch, state.fs.ops.phase);
          // Charge the conservative ceiling before executing even this local
          // fixture. A later missing observation cannot release that charge.
          await state.account.settle(budgetKey(plan), true);
          const revalidateDurableDispatch = async (expectedTransport = null) => {
            const current = await state.fs.records(root); validateRecords(current, id);
            if (canonicalText(current["dispatch-record.json"]) !== canonicalText(dispatch) ||
                canonicalText(current["approval-record.json"]) !== canonicalText(approval) ||
                canonicalText(current["transaction-plan.json"]) !== canonicalText(plan) || current["observation-record.json"] || current["terminal-record.json"] ||
                (expectedTransport ? canonicalText(current["transport-record.json"]) !== canonicalText(expectedTransport) : current["transport-record.json"])) fail();
            await state.account.revalidate();
            await revalidateOfficialClaim(state, id, plan);
            const entry = parse((await state.account.inspect()).canonicalBudgetJson).entries.find((value) => value.authoritySessionSha256 === budgetKey(plan).authoritySessionSha256);
            if (!entry || entry.state !== "charged" || entry.chargedMicrousd !== 10000 || entry.callPlanSha256 !== plan.callPlanSha256) fail();
          };
          const revalidateDispatch = async () => {
            await revalidateDurableDispatch();
            await state.fs.absent(state.target);
            await state.fs.absent(path.join(path.dirname(state.target), `.r22d-${id.slice(7)}.pending`));
            if (now() >= expiry) fail("R22_DIAGNOSTIC_APPROVAL_EXPIRED");
          };
          await state.fs.ops.phase(offline ? "fixture:before_execute" : "transport:before_execute");
          await revalidateDispatch();
          let transportRecord = null;
          if (!offline) {
            const sender = state.credentialReader ? Object.freeze({ ...transportOperations, readCredential: state.credentialReader }) : transportOperations;
            const received = await sendDiagnosticRequest(sender, revalidateDispatch, state.fs.ops.phase, state.captureProfile);
            await state.fs.ops.phase("transport:received");
            // Remote bytes and errors are never part of this durable record.
            // Expiry controls dispatch, not acceptance of already dispatched bytes.
            await revalidateDurableDispatch();
            transportRecord = baseRecord("transport", id, { dispatchRecordSha256: sha256(canonicalText(dispatch)),
              status: received.status, requestCount: received.requestCount }, mode, plan.formatVersion);
            await state.fs.immutable(root, "transport-record.json", transportRecord, state.fs.ops.phase);
            if (received.status !== "response") return finish(state, id, root);
            bytes = received.bytes;
          }
          const evaluate = state.captureProfile === "billing" ? evaluateNpcCognitionBillingDiagnosticFixture : evaluateNpcCognitionToolUsageDiagnosticFixture;
          const evaluated = await evaluate(fixedPlan(state.captureProfile), bytes);
          await state.fs.ops.phase(offline ? "fixture:received" : "transport:parsed");
          if (evaluated.fixtureOnly !== true || evaluated.realRequestCount !== 0 || !evaluated.observation) fail();
          validateObservation(evaluated.observation, state.captureProfile);
          if (!offline) await revalidateDurableDispatch(transportRecord);
          const observation = baseRecord("observation", id, { dispatchRecordSha256: sha256(canonicalText(dispatch)), observation: evaluated.observation,
            ...(offline ? {} : { transportRecordSha256: sha256(canonicalText(transportRecord)) }) }, mode, plan.formatVersion);
          await state.fs.immutable(root, "observation-record.json", observation, state.fs.ops.phase);
          return finish(state, id, root);
        });
        return result;
      },
      cancel() { return exclusive(async () => { if (claimed !== null) fail("R22_CALL_IN_FLIGHT"); claimed = "cancelled"; return finish(state, id, root); }); },
      close() {
        if (closeAttempt) return closeAttempt;
        if (closed) return Promise.resolve(Object.freeze({ ok: true }));
        closing = true;
        closeAttempt = (async () => { await queue; const result = await state.account.close(); closed = true; return result; })()
          .catch(sanitize).finally(() => { closeAttempt = null; });
        return closeAttempt;
      },
    });
  } catch (error) { await state.account.close().catch(() => {}); sanitize(error); }
}

export async function recoverR22ToolUsageDiagnosticTransaction(config, transactionSha256, overrides) {
  return recoverTransaction(config, transactionSha256, overrides, "offline-fixture");
}

async function recoverTransaction(config, transactionSha256, overrides, mode) {
  if (transactionSha256 !== undefined && !HASH.test(transactionSha256 ?? "")) fail();
  const state = await openAccount(config, overrides, mode).catch(sanitize);
  try {
    const budget = parse((await state.account.inspect()).canonicalBudgetJson);
    // Before disclosure returns there cannot be a reservation or approval.
    // Recover that first durable plan from the exact caller configuration,
    // without requiring an id that the interrupted create never returned.
    const id = transactionSha256 ?? sha256(canonicalText(makePlan(state, canonicalText(budget))));
    const root = path.join(state.root, "diagnostics", id.slice(7));
    if (mode === "official-once") {
      const parent = path.dirname(root); await state.fs.directory(parent);
      const ids = await state.fs.ops.readdir(parent);
      if (ids.length < 1 || ids.length > 3 || !ids.includes(id.slice(7))) fail("R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    }
    await state.fs.directory(root);
    const names = (await state.fs.ops.readdir(root)).sort();
    if (names.length === 0) {
      const plan = makePlan(state, canonicalText(budget)), key = budgetKey(plan);
      if (sha256(canonicalText(plan)) !== id || budget.entries.some((entry) => entry.authoritySessionSha256 === key.authoritySessionSha256)) fail();
      await state.account.revalidate(); await state.fs.immutable(root, "transaction-plan.json", plan, state.fs.ops.phase);
      return await finish(state, id, root);
    }
    const pending = names.length === 1 && names[0] === ".transaction-plan.json.pending";
    if (pending || transactionSha256 === undefined) {
      if (!pending && (names.length !== 1 || names[0] !== "transaction-plan.json")) fail("R22_DIAGNOSTIC_INCOMPLETE_RECORD");
      const text = await state.fs.file(path.join(root, names[0])), plan = validatePlan(parse(text)), key = budgetKey(plan);
      if (sha256(text) !== id || budget.entries.some((entry) => entry.authoritySessionSha256 === key.authoritySessionSha256)) fail();
      validateSource(state, plan);
      await revalidateOfficialClaim(state, id, plan);
      if (pending) { await state.account.revalidate(); await state.fs.promotePendingPlan(root, text); }
    }
    return await finish(state, id, root);
  }
  catch (error) { if (typeof error?.message === "string" && /^R22_[A-Z_]+$/u.test(error.message)) throw error; fail(); }
  finally { await state.account.close(); }
}

// Read-only reconciliation seam for ordinary history auditing. A directory or
// hash prefix is never a budget exemption: every edge is checked in both directions.
export async function auditR22DiagnosticBudgetHistory({ cognitionRunRoot, hostRunId, budget, remember, rememberDirectory }) {
  const parent = path.join(cognitionRunRoot, "diagnostics");
  try { await lstat(parent, { bigint: true }); } catch (error) { if (error?.code === "ENOENT") return new Map(); throw error; }
  const manifest = parse(await remember(path.join(cognitionRunRoot, "cognition-session-manifest.json"), 16 * 1024));
  validateR22CognitionSessionManifest(manifest, hostRunId);
  const ids = await rememberDirectory(parent); if (ids.length > MAX_TRANSACTIONS || ids.some((id) => !HEX.test(id)) ||
      (manifest.providerMode === "official-once" && ids.length > 3)) fail();
  const result = new Map(), official = [];
  for (const hex of ids) {
    const root = path.join(parent, hex), names = await rememberDirectory(root), records = {};
    if (names.some((name) => !RECORDS.includes(name))) fail();
    for (const name of names) {
      const file = path.join(root, name), stat = await lstat(file, { bigint: true });
      if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1n) fail();
      records[name] = parse(await remember(file, 32 * 1024));
    }
    const validated = validateRecords(records, `sha256:${hex}`), { plan } = validated;
    if (manifest.providerMode !== (plan.executionKind === "official-once" ? "official-once" : "offline-fake") ||
        plan.hostRunId !== hostRunId || plan.sourceRootSha256 !== sha256(path.resolve(cognitionRunRoot).toLowerCase()) ||
        plan.sourceSessionManifestSha256 !== sha256(canonicalText(manifest))) fail();
    const key = budgetKey(plan), keyString = `${key.authoritySessionSha256}:${key.callPlanSha256}`;
    const entry = validateClosedBudgetRecord(validated, budget);
    // Recovery must settle before the ordinary preview restarts. Active or
    // partial diagnostics never bypass that gate or silently unlock a budget.
    if (entry) result.set(keyString, { chargedMicrousd: entry.chargedMicrousd, state: entry.state });
    if (plan.executionKind === "official-once") official.push({ ...validated, budgetEntry: entry });
  }
  validateOfficialHistory(official);
  return result;
}

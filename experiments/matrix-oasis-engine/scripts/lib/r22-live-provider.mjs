import path from "node:path";
import { lstat, open, realpath } from "node:fs/promises";
import { createOpenAiNpcCognitionProvider, executeApprovedNpcCognitionTurn } from "@matrix-oasis/npc-cognition-provider-openai";
import { validateNpcCognitionCallPlanJson } from "@matrix-oasis/npc-cognition-contracts";
import { canonicalText, sha256 } from "./r22-cli-core.mjs";

function fail() { throw new Error("R22_OFFICIAL_ONE_SHOT_UNAVAILABLE"); }
function credentialFail() { throw new Error("R22_OFFICIAL_CREDENTIAL_FILE_UNAVAILABLE"); }
function defaultCredentialReader() { return process.env.MATRIX_OASIS_R22_OPENAI_API_KEY; }
function emitProviderDiagnostic(marker, record) {
  let stream;
  const ignoreError = () => {};
  const detach = () => { try { stream?.removeListener("error", ignoreError); } catch { /* Observation only. */ } };
  try {
    stream = process.stdout;
    stream.once("error", ignoreError);
    stream.write(`${marker}:${canonicalText(record)}\n`, () => {
      // Writable emits an asynchronous error after its write callback. Keep the
      // handler through that turn, without awaiting I/O or altering the result.
      setImmediate(detach);
    });
  } catch { detach(); }
}
async function defaultExecutor(input) {
  const provider = createOpenAiNpcCognitionProvider({ apiKey: input.apiKey });
  const result = await executeApprovedNpcCognitionTurn({ callPlanJson: input.callPlanJson,
    providerRequestJson: input.providerRequestJson, approvalHash: input.approvalHash }, provider);
  if (!result.ok && result.httpDiagnostic) {
    // Only the official adapter's closed, redacted enum record is emitted.
    // It is an observation, not a Receipt or evidence of an accepted Action.
    emitProviderDiagnostic("R22_PROVIDER_HTTP_DIAGNOSTIC_JSON", {
      callPlanSha256: sha256(input.callPlanJson), ...result.httpDiagnostic,
    });
  } else if (!result.ok && result.responseDiagnostic) {
    emitProviderDiagnostic("R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON", {
      callPlanSha256: sha256(input.callPlanJson), ...result.responseDiagnostic,
    });
  }
  return result;
}

function sameIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size &&
    left.mtimeNs === right.mtimeNs && left.ctimeNs === right.ctimeNs && left.nlink === right.nlink;
}
function sameDirectoryIdentity(left, right) { return left.dev === right.dev && left.ino === right.ino; }

async function pinRealPath(target, kind) {
  const linked = await lstat(target, { bigint: true });
  if (linked.isSymbolicLink() || (kind === "file" ? !linked.isFile() : !linked.isDirectory()) ||
      (kind === "file" && linked.nlink !== 1n) ||
      path.resolve(await realpath(target)) !== target) credentialFail();
  return linked;
}

export async function prepareR22FileCredentialReader({ credentialFile, temporaryRoot } = {}) {
  try {
    if (typeof credentialFile !== "string" || typeof temporaryRoot !== "string") credentialFail();
    if (!path.isAbsolute(credentialFile) || !path.isAbsolute(temporaryRoot)) credentialFail();
    const root = path.resolve(temporaryRoot), file = path.resolve(credentialFile);
    const requiredRoot = path.resolve(path.parse(root).root, "tmp");
    if (root !== requiredRoot || path.dirname(file) !== root || file === root) credentialFail();
    const rootIdentity = await pinRealPath(root, "directory");
    const fileIdentity = await pinRealPath(file, "file");
    if (fileIdentity.size < 1n || fileIdentity.size > 8192n) credentialFail();
    async function readCredential() {
      let handle, bytes;
      try {
        const rootBefore = await pinRealPath(root, "directory");
        if (!sameDirectoryIdentity(rootIdentity, rootBefore)) credentialFail();
        const linkedBefore = await pinRealPath(file, "file");
        if (!sameIdentity(fileIdentity, linkedBefore)) credentialFail();
        handle = await open(file, "r");
        const openedBefore = await handle.stat({ bigint: true });
        if (!openedBefore.isFile() || openedBefore.nlink !== 1n || !sameIdentity(fileIdentity, openedBefore)) credentialFail();
        bytes = Buffer.allocUnsafe(8193);
        let length = 0;
        while (length < bytes.byteLength) {
          const { bytesRead } = await handle.read(bytes, length, bytes.byteLength - length, null);
          if (bytesRead === 0) break;
          length += bytesRead;
        }
        if (length < 1 || length > 8192 || BigInt(length) !== fileIdentity.size) credentialFail();
        const openedAfter = await handle.stat({ bigint: true });
        const linkedAfter = await pinRealPath(file, "file");
        const rootAfter = await pinRealPath(root, "directory");
        if (!sameIdentity(fileIdentity, openedAfter) || !sameIdentity(fileIdentity, linkedAfter) ||
            !sameDirectoryIdentity(rootIdentity, rootAfter)) credentialFail();
        let value = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, length));
        if (value.charCodeAt(0) === 0xfeff) value = value.slice(1);
        if (value.endsWith("\r\n")) value = value.slice(0, -2);
        else if (value.endsWith("\n")) value = value.slice(0, -1);
        if (/^sk-or-/iu.test(value) || /openrouter/iu.test(value) ||
            !/^sk-[A-Za-z0-9_-]{16,8189}$/u.test(value) || /[\u0000-\u0020\u007f-\u009f]/u.test(value)) credentialFail();
        return value;
      } catch {
        credentialFail();
      } finally {
        bytes?.fill(0);
        await handle?.close().catch(() => {});
      }
    }
    // This binds the selected file, not its secret bytes. Preparation still
    // performs metadata checks only; content is read by the deferred function.
    const sourceBinding = Object.freeze({ kind: "pinned-file",
      identitySha256: sha256(canonicalText({ profile: "matrix-oasis.r22-credential-source/1",
        pathSha256: sha256(file.toLowerCase()), rootDev: String(rootIdentity.dev), rootIno: String(rootIdentity.ino),
        dev: String(fileIdentity.dev), ino: String(fileIdentity.ino), size: String(fileIdentity.size),
        mtimeNs: String(fileIdentity.mtimeNs), ctimeNs: String(fileIdentity.ctimeNs), nlink: String(fileIdentity.nlink) })) });
    Object.defineProperty(readCredential, "sourceBinding", { value: sourceBinding });
    return Object.freeze(readCredential);
  } catch {
    credentialFail();
  }
}

// An additional qualification-only cap, not a replacement for the transactional
// host budget. This handle is shared across every reset of this fresh live run.
// Explicit crash recovery may reopen the same root, but the composition denies
// all new paid turns on that invocation. There is no reset/rearm operation here.
export function createR22OfficialOneShotOperations({ readDispatch, readCredential = defaultCredentialReader,
  execute = defaultExecutor }) {
  if ([readDispatch, readCredential, execute].some((value) => typeof value !== "function")) fail();
  let credentialClaimed = false, executionClaimed = false, credentialReads = 0, providerRequests = 0;
  let bound = null;
  const readBoundDispatch = async () => {
    const current = await readDispatch();
    if (!current || current.checkpoint?.active?.stage !== "dispatching" ||
        current.checkpoint.providerRequests !== 0 || typeof current.callPlanJson !== "string" ||
        typeof current.dispatchRecordJson !== "string") fail();
    const validation = validateNpcCognitionCallPlanJson(current.callPlanJson);
    if (validation.valid !== true || validation.diagnostics.length !== 0) fail();
    const plan = JSON.parse(current.callPlanJson), dispatch = JSON.parse(current.dispatchRecordJson);
    if (canonicalText(plan) !== current.callPlanJson || canonicalText(dispatch) !== current.dispatchRecordJson ||
        dispatch.format !== "matrix-oasis.r22-dispatch-record" || dispatch.formatVersion !== "0.1.0" ||
        dispatch.canonicalization !== "matrix-oasis.canonical-json/1" ||
        dispatch.callPlanSha256 !== sha256(current.callPlanJson) ||
        dispatch.callPlanSha256 !== current.checkpoint.active.callPlanSha256 ||
        dispatch.turnSha256 !== plan.turnSha256 || dispatch.approvalContentSha256 !== plan.approval.hash ||
        !/^sha256:[0-9a-f]{64}$/u.test(dispatch.approvalTokenSha256 ?? "") ||
        dispatch.reservationMicrousd !== 10000 || dispatch.providerRequestLimit !== 1 || dispatch.providerRetryLimit !== 0 ||
        Object.keys(dispatch).length !== 10) fail();
    return Object.freeze({ callPlanJson: current.callPlanJson, dispatchRecordJson: current.dispatchRecordJson, plan });
  };
  return Object.freeze({
    async keyReader() {
      if (credentialClaimed) fail();
      credentialClaimed = true;
      bound = await readBoundDispatch();
      credentialReads += 1;
      return readCredential();
    },
    async providerExecutor(input) {
      if (!credentialClaimed || executionClaimed || bound === null) fail();
      executionClaimed = true;
      const current = await readBoundDispatch();
      if (current.callPlanJson !== bound.callPlanJson || current.dispatchRecordJson !== bound.dispatchRecordJson ||
          input.callPlanJson !== bound.callPlanJson || input.approvalHash !== bound.plan.approval.hash ||
          typeof input.providerRequestJson !== "string" || sha256(input.providerRequestJson) !== bound.plan.providerPayloadSha256) fail();
      // Count conservatively before handing control to the already bounded
      // official adapter; a lost response may still have incurred a request.
      providerRequests = 1;
      return execute(input);
    },
    inspect() { return Object.freeze({ credentialClaimed, executionClaimed, sourceCredentialReads: credentialReads,
      providerRequests, requestLimit: 1, retryLimit: 0 }); },
  });
}

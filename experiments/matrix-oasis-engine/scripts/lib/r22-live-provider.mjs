import { createOpenAiNpcCognitionProvider, executeApprovedNpcCognitionTurn } from "@matrix-oasis/npc-cognition-provider-openai";
import { validateNpcCognitionCallPlanJson } from "@matrix-oasis/npc-cognition-contracts";
import { canonicalText, sha256 } from "./r22-cli-core.mjs";

function fail() { throw new Error("R22_OFFICIAL_ONE_SHOT_UNAVAILABLE"); }
function defaultCredentialReader() { return process.env.MATRIX_OASIS_R22_OPENAI_API_KEY; }
async function defaultExecutor(input) {
  const provider = createOpenAiNpcCognitionProvider({ apiKey: input.apiKey });
  return executeApprovedNpcCognitionTurn({ callPlanJson: input.callPlanJson,
    providerRequestJson: input.providerRequestJson, approvalHash: input.approvalHash }, provider);
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

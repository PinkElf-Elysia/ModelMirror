import { createHash } from "node:crypto";
import { types } from "node:util";
import {
  NPC_COGNITION_ENDPOINT,
  NPC_COGNITION_LIMITS,
  NPC_COGNITION_MODEL,
  NPC_COGNITION_TRUSTED_INSTRUCTIONS,
  computeNpcCognitionApprovalHash,
  validateNpcCognitionCallPlanJson,
} from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { observeToolUsage } from "./tool-usage-observer.mjs";
import { createNpcCognitionToolUsageDiagnosticPlan } from "./tool-usage-diagnostic-profile.mjs";
import { observeBilling } from "./billing-observer.mjs";
import { createNpcCognitionBillingDiagnosticPlan } from "./billing-diagnostic-profile.mjs";

export { createNpcCognitionToolUsageDiagnosticPlan, createNpcCognitionBillingDiagnosticPlan };

const PROVIDER_STATE = new WeakMap();
const INTERNAL_CODE = "NPC_COGNITION_INTERNAL_ERROR";
const JSON_CONTENT_TYPE = /^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/iu;
const SHA256 = /^sha256:[0-9a-f]{64}$/u;
const CHOICE_ID = /^choice-[0-9a-f]{64}$/u;
const RESPONSE_DEPTH_LIMIT = 256;
const RESPONSE_CHUNK_LIMIT = 4096;
const BYTE_LENGTH_GETTER = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Uint8Array.prototype), "byteLength").get;
const BUFFER_GETTER = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Uint8Array.prototype), "buffer").get;
const STRUCTURED_OUTPUT_NAME = "matrix_oasis_npc_dialogue_proposal";
const TIMEOUT_RESULT = Object.freeze({ kind: "timeout" });
const MODEL_ID = /^[A-Za-z0-9._:-]{1,128}$/u;
const HTTP_ERROR_TYPES = new Set([
  "invalid_request_error", "authentication_error", "permission_denied_error",
  "insufficient_quota", "rate_limit_error", "server_error", "service_unavailable_error",
]);
const HTTP_ERROR_CODES = new Set([
  "invalid_json_schema", "invalid_value", "unsupported_value", "unsupported_parameter",
  "missing_required_parameter", "invalid_api_key", "model_not_found", "insufficient_quota",
  "rate_limit_exceeded", "slow_down", "organization_spend_limit_exceeded",
  "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "server_is_overloaded",
  "context_length_exceeded", "content_policy_violation",
]);
const HTTP_ERROR_PARAMETERS = new Set([
  "model", "input", "instructions", "reasoning", "reasoning.effort", "max_output_tokens",
  "text", "text.format", "text.format.schema", "text.format.name", "text.format.strict",
  "store", "stream", "background", "truncation", "service_tier",
]);
const PROPOSAL_KEYS = Object.freeze(["contextSha256", "dialogueText", "actionChoiceId"]);
const PAYLOAD_KEYS = Object.freeze([
  "background",
  "input",
  "instructions",
  "max_output_tokens",
  "model",
  "reasoning",
  "service_tier",
  "store",
  "stream",
  "text",
  "truncation",
]);
const RESPONSE_ROOT_REQUIRED_KEYS = Object.freeze([
  "id",
  "object",
  "status",
  "error",
  "incomplete_details",
  "model",
  "output",
  "usage",
]);
// This is a versioned *local* capability profile, not the complete Responses API
// schema. Every known field is checked below or explicitly bounded and discarded.
// New upstream fields are compatible API changes, but need classification before
// this no-tools, fixed-price profile may accept them. Names alone are not proof.
const RESPONSE_ENVELOPE_PROFILE = "matrix-oasis.responses-envelope/1";
const RESPONSE_ROOT_OPTIONAL_KEYS = Object.freeze([
  "agent",
  "background",
  "billing",
  "completed_at",
  "context_management",
  "conversation",
  "created_at",
  "frequency_penalty",
  "instructions",
  "max_output_tokens",
  "max_tool_calls",
  "metadata",
  "moderation",
  "parallel_tool_calls",
  "previous_response_id",
  "presence_penalty",
  "prompt",
  "prompt_cache_diagnostics",
  "prompt_cache_key",
  "prompt_cache_options",
  "prompt_cache_retention",
  "reasoning",
  "safety_identifier",
  "service_tier",
  "store",
  "temperature",
  "text",
  "tool_choice",
  "tool_usage",
  "tools",
  "top_logprobs",
  "top_p",
  "truncation",
  "user",
]);

export class NpcCognitionProviderOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "NpcCognitionProviderOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function operational() {
  throw new NpcCognitionProviderOperationalError();
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function captureRecord(value, required, optional = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let prototype;
  let descriptors;
  try {
    prototype = Object.getPrototypeOf(value);
    descriptors = Object.getOwnPropertyDescriptors(value);
  } catch {
    operational();
  }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const allowed = new Set([...required, ...optional]);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.some((key) => typeof key !== "string" || !allowed.has(key)) ||
    required.some((key) => !Object.hasOwn(descriptors, key))
  ) return null;
  const output = Object.create(null);
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (
      !descriptor.enumerable ||
      descriptor.get !== undefined ||
      descriptor.set !== undefined ||
      !Object.hasOwn(descriptor, "value")
    ) return null;
    output[key] = descriptor.value;
  }
  return output;
}

function failure(
  diagnosticCode,
  {
    requestCount = 0,
    costUncertain = false,
    returnedModel = null,
    usage = null,
    actualCostMicrousd = null,
    httpDiagnostic = null,
    responseDiagnostic = null,
  } = {},
) {
  return deepFreeze({
    ok: false,
    diagnosticCode,
    requestCount,
    costUncertain,
    returnedModel,
    usage,
    actualCostMicrousd,
    ...(httpDiagnostic === null ? {} : { httpDiagnostic }),
    ...(responseDiagnostic === null ? {} : { responseDiagnostic }),
  });
}

function ignoreLateSettlement(promise) {
  Promise.resolve(promise).catch(() => {});
}

function cancelReadable(target) {
  if (!target || typeof target.cancel !== "function") return;
  try {
    ignoreLateSettlement(target.cancel());
  } catch {
    // Cancellation is best-effort. The caller still fails closed.
  }
}

function cancelResponseBody(response) {
  try {
    cancelReadable(response?.body);
  } catch {
    // Hostile response accessors cannot turn a failure into an exception leak.
  }
}

function createAbsoluteDeadline(timeoutMs) {
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve(TIMEOUT_RESULT);
    }, timeoutMs);
  });
  return Object.freeze({
    controller,
    async race(operation) {
      const settlement = Promise.resolve(operation).then(
        (value) => ({ kind: "value", value }),
        (error) => ({ kind: "error", error }),
      );
      // The rejection branch above remains attached after a timeout, so a
      // late provider/body rejection can never become unhandled.
      return Promise.race([settlement, timeout]);
    },
    close() {
      clearTimeout(timer);
    },
  });
}

function sha256Text(text) {
  return `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
}

function wellFormedText(value) {
  if (typeof value !== "string") return false;
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function safeText(value, { allowLf = true, rejectBidi = true } = {}) {
  if (!wellFormedText(value) || value.normalize("NFC") !== value) return false;
  const controls = allowLf
    ? /[\u0000-\u0009\u000b-\u001f\u007f-\u009f]/u
    : /[\u0000-\u001f\u007f-\u009f]/u;
  if (controls.test(value)) return false;
  if (/[\u2028\u2029]/u.test(value)) return false;
  return !rejectBidi || !/[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/u.test(value);
}

function scanStrictJson(text, { rejectNumberUnderflow = false } = {}) {
  let offset = 0;
  const whitespace = () => {
    while (offset < text.length && /[\u0009\u000a\u000d\u0020]/u.test(text[offset])) offset += 1;
  };
  const stringToken = () => {
    if (text[offset] !== '"') throw new Error("json");
    const start = offset;
    offset += 1;
    while (offset < text.length) {
      const unit = text.charCodeAt(offset);
      if (unit === 0x22) {
        offset += 1;
        return JSON.parse(text.slice(start, offset));
      }
      if (unit <= 0x1f) throw new Error("json");
      if (unit === 0x5c) {
        offset += 1;
        const escape = text[offset];
        if (escape === "u") {
          if (!/^[0-9a-fA-F]{4}$/u.test(text.slice(offset + 1, offset + 5))) throw new Error("json");
          offset += 5;
          continue;
        }
        if (!['"', "\\", "/", "b", "f", "n", "r", "t"].includes(escape)) throw new Error("json");
        offset += 1;
        continue;
      }
      offset += 1;
    }
    throw new Error("json");
  };
  const value = (depth) => {
    if (depth > RESPONSE_DEPTH_LIMIT) throw new Error("depth");
    whitespace();
    const initial = text[offset];
    if (initial === '"') {
      stringToken();
      return;
    }
    if (initial === "{") {
      offset += 1;
      whitespace();
      const keys = new Set();
      if (text[offset] === "}") {
        offset += 1;
        return;
      }
      while (true) {
        whitespace();
        const key = stringToken();
        if (keys.has(key)) throw new Error("duplicate");
        keys.add(key);
        whitespace();
        if (text[offset] !== ":") throw new Error("json");
        offset += 1;
        value(depth + 1);
        whitespace();
        if (text[offset] === "}") {
          offset += 1;
          return;
        }
        if (text[offset] !== ",") throw new Error("json");
        offset += 1;
      }
    }
    if (initial === "[") {
      offset += 1;
      whitespace();
      if (text[offset] === "]") {
        offset += 1;
        return;
      }
      while (true) {
        value(depth + 1);
        whitespace();
        if (text[offset] === "]") {
          offset += 1;
          return;
        }
        if (text[offset] !== ",") throw new Error("json");
        offset += 1;
      }
    }
    for (const literal of ["true", "false", "null"]) {
      if (text.startsWith(literal, offset)) {
        offset += literal.length;
        return;
      }
    }
    const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u.exec(text.slice(offset));
    if (!match) throw new Error("json");
    // Used only by the expanded observed-zero response profile. JSON.parse can
    // round a nonzero counter such as 1e-999 to zero; it is not a neutral count.
    // Ignore exponent digits and quoted strings. Legacy parsing is unchanged.
    if (rejectNumberUnderflow && Number(match[0]) === 0 && /[1-9]/u.test(match[0].split(/[eE]/u)[0])) {
      throw new Error("number_underflow");
    }
    offset += match[0].length;
  };
  value(1);
  whitespace();
  if (offset !== text.length) throw new Error("json");
}

function parseStrictJson(text) {
  try {
    if (typeof text !== "string") return null;
    scanStrictJson(text);
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function exactKeys(value, keys) {
  const captured = captureRecord(value, keys);
  return captured && Reflect.ownKeys(captured).length === keys.length ? captured : null;
}

function validateDynamicResponseSchema(value, contextSha256) {
  const schema = exactKeys(value, ["type", "additionalProperties", "required", "properties"]);
  if (
    !schema ||
    schema.type !== "object" ||
    schema.additionalProperties !== false ||
    !Array.isArray(schema.required) ||
    schema.required.length !== PROPOSAL_KEYS.length ||
    !PROPOSAL_KEYS.every((key, index) => schema.required[index] === key)
  ) return null;
  const properties = exactKeys(schema.properties, PROPOSAL_KEYS);
  const context = properties && exactKeys(properties.contextSha256, ["type", "const"]);
  const dialogue = properties && exactKeys(properties.dialogueText, ["type", "minLength", "maxLength"]);
  const choice = properties && exactKeys(properties.actionChoiceId, ["type", "enum"]);
  if (
    !context || context.type !== "string" || context.const !== contextSha256 ||
    !dialogue || dialogue.type !== "string" || dialogue.minLength !== 1 ||
    dialogue.maxLength !== NPC_COGNITION_LIMITS.dialogueBytes ||
    !choice || !Array.isArray(choice.type) || choice.type.length !== 2 ||
    choice.type[0] !== "string" || choice.type[1] !== "null" ||
    !Array.isArray(choice.enum) || choice.enum.length < 1 ||
    choice.enum.length > NPC_COGNITION_LIMITS.candidateActionsPerTurn + 1
  ) return null;
  let nulls = 0;
  const choices = new Set();
  for (const item of choice.enum) {
    if (item === null) {
      nulls += 1;
    } else if (typeof item !== "string" || !CHOICE_ID.test(item) || choices.has(item)) {
      return null;
    } else {
      choices.add(item);
    }
  }
  return nulls === 1 ? Object.freeze({ schema: value, choices }) : null;
}

function validateProviderPayload(providerRequestJson, callPlan, diagnostic = false) {
  if (
    typeof providerRequestJson !== "string" ||
    new TextEncoder().encode(providerRequestJson).byteLength !== callPlan.requestBytes ||
    callPlan.requestBytes > NPC_COGNITION_LIMITS.providerRequestBytes ||
    sha256Text(providerRequestJson) !== callPlan.providerPayloadSha256
  ) return null;
  const parsed = parseStrictJson(providerRequestJson);
  if (!parsed) return null;
  let canonical;
  try {
    canonical = canonicalizeJsonValue(parsed);
  } catch {
    return null;
  }
  if (canonical !== providerRequestJson) return null;
  const body = exactKeys(parsed, diagnostic ? [...PAYLOAD_KEYS, "tools", "tool_choice"] : PAYLOAD_KEYS);
  if (diagnostic && (!body || !Array.isArray(body.tools) || body.tools.length !== 0 || body.tool_choice !== "none" ||
      callPlan.candidateChoices.length !== 0)) return null;
  if (
    !body || body.background !== false || body.stream !== false || body.store !== false ||
    body.truncation !== "disabled" || body.model !== NPC_COGNITION_MODEL ||
    body.service_tier !== "default" ||
    body.max_output_tokens !== NPC_COGNITION_LIMITS.maxOutputTokens ||
    body.instructions !== NPC_COGNITION_TRUSTED_INSTRUCTIONS ||
    typeof body.input !== "string" || !safeText(body.input) || !/\S/u.test(body.input)
  ) return null;
  if (sha256Text(body.input) !== callPlan.contextSha256) return null;
  const reasoning = exactKeys(body.reasoning, ["effort"]);
  const text = exactKeys(body.text, ["format"]);
  const format = text && exactKeys(text.format, ["type", "name", "strict", "schema"]);
  if (
    !reasoning || reasoning.effort !== "none" ||
    !format || format.type !== "json_schema" || format.name !== STRUCTURED_OUTPUT_NAME ||
    format.strict !== true
  ) return null;
  const responseSchema = validateDynamicResponseSchema(format.schema, callPlan.contextSha256);
  if (!responseSchema) return null;
  const expectedChoices = [null, ...callPlan.candidateChoices.map((candidate) => candidate.choiceId)];
  if (
    responseSchema.schema.properties.actionChoiceId.enum.length !== expectedChoices.length ||
    !expectedChoices.every((choiceId, index) => responseSchema.schema.properties.actionChoiceId.enum[index] === choiceId)
  ) return null;
  let schemaJson;
  try {
    schemaJson = canonicalizeJsonValue(format.schema);
  } catch {
    return null;
  }
  if (sha256Text(schemaJson) !== callPlan.responseSchemaSha256) return null;
  return Object.freeze({ body, responseSchema });
}

function validateExecutionInput(input, diagnostic = false) {
  const captured = captureRecord(input, ["callPlanJson", "providerRequestJson", "approvalHash"]);
  if (!captured || typeof captured.callPlanJson !== "string" || typeof captured.approvalHash !== "string") return null;
  let validation;
  try {
    validation = validateNpcCognitionCallPlanJson(captured.callPlanJson);
  } catch {
    operational();
  }
  if (!validation.valid) return null;
  const callPlan = parseStrictJson(captured.callPlanJson);
  if (!callPlan) return null;
  let expectedApproval;
  try {
    expectedApproval = computeNpcCognitionApprovalHash(callPlan);
  } catch {
    operational();
  }
  if (
    !SHA256.test(captured.approvalHash) ||
    captured.approvalHash !== callPlan.approval.hash ||
    callPlan.approval.hash !== expectedApproval ||
    callPlan.endpoint !== NPC_COGNITION_ENDPOINT ||
    callPlan.model !== NPC_COGNITION_MODEL ||
    callPlan.priceLock.inputMicrousdPerMillionTokens !== NPC_COGNITION_LIMITS.inputMicrousdPerMillionTokens ||
    callPlan.priceLock.cachedInputMicrousdPerMillionTokens !== NPC_COGNITION_LIMITS.cachedInputMicrousdPerMillionTokens ||
    callPlan.priceLock.cacheWriteInputMicrousdPerMillionTokens !== NPC_COGNITION_LIMITS.cacheWriteInputMicrousdPerMillionTokens ||
    callPlan.priceLock.outputMicrousdPerMillionTokens !== NPC_COGNITION_LIMITS.outputMicrousdPerMillionTokens ||
    callPlan.maxOutputTokens !== NPC_COGNITION_LIMITS.maxOutputTokens ||
    callPlan.timeoutMs !== NPC_COGNITION_LIMITS.timeoutMs ||
    callPlan.maxCostMicrousd !== NPC_COGNITION_LIMITS.perCallMicrousd ||
    callPlan.requestLimit !== 1 || callPlan.retryLimit !== 0
  ) return null;
  const payload = validateProviderPayload(captured.providerRequestJson, callPlan, diagnostic);
  return payload ? Object.freeze({ ...captured, callPlan, payload }) : null;
}

async function readBoundedResponse(response, deadline) {
  let declaredLength;
  try {
    declaredLength = response.headers?.get("content-length") ?? null;
  } catch {
    return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
  }
  if (declaredLength !== null) {
    if (!/^(?:0|[1-9][0-9]*)$/u.test(declaredLength)) {
      return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
    }
    if (Number(declaredLength) > NPC_COGNITION_LIMITS.providerResponseBytes) {
      return { ok: false, code: "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED" };
    }
  }
  let reader;
  try {
    reader = response.body?.getReader?.();
  } catch {
    return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
  }
  if (!reader) return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
  const chunks = [];
  let byteLength = 0;
  let chunkCount = 0;
  try {
    while (true) {
      if (deadline.controller.signal.aborted) {
        cancelReadable(reader);
        return { ok: false, code: "R22_PROVIDER_TIMEOUT" };
      }
      let pendingRead;
      try {
        pendingRead = reader.read();
      } catch {
        cancelReadable(reader);
        return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
      }
      const outcome = await deadline.race(pendingRead);
      if (outcome === TIMEOUT_RESULT) {
        cancelReadable(reader);
        return { ok: false, code: "R22_PROVIDER_TIMEOUT" };
      }
      if (outcome.kind === "error") {
        cancelReadable(reader);
        return {
          ok: false,
          code: deadline.controller.signal.aborted || outcome.error?.name === "AbortError"
            ? "R22_PROVIDER_TIMEOUT"
            : "R22_PROVIDER_NETWORK_AMBIGUOUS",
        };
      }
      const part = outcome.value;
      if (!part || typeof part !== "object") {
        cancelReadable(reader);
        return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
      }
      if (part.done) break;
      chunkCount += 1;
      if (!(part.value instanceof Uint8Array) || chunkCount > RESPONSE_CHUNK_LIMIT) {
        cancelReadable(reader);
        return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
      }
      byteLength += part.value.byteLength;
      if (byteLength > NPC_COGNITION_LIMITS.providerResponseBytes) {
        cancelReadable(reader);
        return { ok: false, code: "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED" };
      }
      chunks.push(part.value);
    }
  } catch {
    cancelReadable(reader);
    return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
  }
  const bytes = new Uint8Array(byteLength);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return {
      ok: true,
      byteLength,
      text: new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    };
  } catch {
    return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
  }
}

async function readHttpDiagnostic(response, deadline, status) {
  // Error bodies can contain echoed input and credentials. Read them only in
  // memory under the same absolute deadline/byte/depth limits as success. Never
  // return message, identifiers, body hashes or arbitrary provider strings.
  const body = await readBoundedResponse(response, deadline);
  if (!body.ok) cancelResponseBody(response);
  const parsed = body.ok ? parseStrictJson(body.text) : null;
  const error = parsed && typeof parsed === "object" && !Array.isArray(parsed) &&
    Object.hasOwn(parsed, "error") && parsed.error && typeof parsed.error === "object" &&
    !Array.isArray(parsed.error) ? parsed.error : null;
  const bodyStatus = body.ok ? (error ? "parsed" : "invalid") :
    body.code === "R22_PROVIDER_TIMEOUT" ? "timeout" :
    body.code === "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED" ? "limit_exceeded" :
    body.code === "R22_PROVIDER_NETWORK_AMBIGUOUS" ? "unavailable" : "invalid";
  return deepFreeze({ status, bodyStatus,
    errorType: HTTP_ERROR_TYPES.has(error?.type) ? error.type : null,
    errorCode: HTTP_ERROR_CODES.has(error?.code) ? error.code : null,
    parameter: HTTP_ERROR_PARAMETERS.has(error?.param) ? error.param : null,
  });
}

function usageEvidence(value, callPlan) {
  const usage = captureRecord(value, [
    "input_tokens",
    "input_tokens_details",
    "output_tokens",
    "output_tokens_details",
    "total_tokens",
  ]);
  if (!usage) return null;
  for (const field of ["input_tokens", "output_tokens", "total_tokens"]) {
    if (!Number.isSafeInteger(usage[field]) || usage[field] < 0) return null;
  }
  const inputDetails = exactKeys(usage.input_tokens_details, ["cached_tokens", "cache_write_tokens"]);
  const outputDetails = exactKeys(usage.output_tokens_details, ["reasoning_tokens"]);
  if (
    !inputDetails || !outputDetails ||
    !Number.isSafeInteger(inputDetails.cached_tokens) || inputDetails.cached_tokens < 0 ||
    !Number.isSafeInteger(inputDetails.cache_write_tokens) || inputDetails.cache_write_tokens < 0 ||
    !Number.isSafeInteger(inputDetails.cached_tokens + inputDetails.cache_write_tokens) ||
    inputDetails.cached_tokens + inputDetails.cache_write_tokens > usage.input_tokens ||
    !Number.isSafeInteger(outputDetails.reasoning_tokens) || outputDetails.reasoning_tokens < 0 ||
    outputDetails.reasoning_tokens > usage.output_tokens ||
    !Number.isSafeInteger(usage.input_tokens + usage.output_tokens) ||
    usage.total_tokens !== usage.input_tokens + usage.output_tokens ||
    usage.total_tokens === 0 ||
    usage.output_tokens > callPlan.maxOutputTokens
  ) return null;
  const regularInputTokens = usage.input_tokens - inputDetails.cached_tokens - inputDetails.cache_write_tokens;
  const costNumerator =
    (BigInt(regularInputTokens) * BigInt(callPlan.priceLock.inputMicrousdPerMillionTokens)) +
    (BigInt(inputDetails.cached_tokens) * BigInt(callPlan.priceLock.cachedInputMicrousdPerMillionTokens)) +
    (BigInt(inputDetails.cache_write_tokens) * BigInt(callPlan.priceLock.cacheWriteInputMicrousdPerMillionTokens)) +
    (BigInt(usage.output_tokens) * BigInt(callPlan.priceLock.outputMicrousdPerMillionTokens));
  const cost = (costNumerator + 999_999n) / 1_000_000n;
  if (cost > BigInt(callPlan.maxCostMicrousd) || cost > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return deepFreeze({
    usage: {
      inputTokens: usage.input_tokens,
      cachedInputTokens: inputDetails.cached_tokens,
      cacheWriteInputTokens: inputDetails.cache_write_tokens,
      outputTokens: usage.output_tokens,
      totalTokens: usage.total_tokens,
    },
    actualCostMicrousd: Number(cost),
  });
}

function parseFlatProposal(text) {
  const value = parseStrictJson(text);
  const proposal = value && exactKeys(value, PROPOSAL_KEYS);
  return proposal ?? null;
}

const RESPONSE_CHECK_RULES = Object.freeze([
  "echo_frequency_penalty", "echo_presence_penalty", "echo_tool_usage",
  "echo_background", "echo_store", "echo_truncation", "echo_max_output_tokens",
  "echo_instructions", "echo_previous_response_id", "echo_conversation", "echo_prompt",
  "echo_tools", "echo_tool_choice", "echo_parallel_tool_calls", "echo_metadata",
  "echo_service_tier", "echo_text_format", "echo_schema", "usage", "model",
  "completion", "output", "message", "content", "proposal_json",
  "proposal_context", "dialogue_text", "action_choice",
]);

function responseJsonType(parent, key) {
  if (parent === null) return "unavailable";
  if (!Object.hasOwn(parent, key)) return "absent";
  const value = parent[key];
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  const type = typeof value;
  return ["object", "string", "number", "boolean"].includes(type) ? type : "unavailable";
}

function inspectReasoningPolicy(value) {
  const record = captureRecord(value, [], ["effort", "summary", "mode", "context", "generate_summary"]);
  const nullableEnum = (key, values) => Boolean(record &&
    (!Object.hasOwn(record, key) || record[key] === null || values.includes(record[key])));
  const effort = Boolean(record && (!Object.hasOwn(record, "effort") || record.effort === "none"));
  const mode = Boolean(record && (!Object.hasOwn(record, "mode") || record.mode === "standard"));
  // The API returns the effective context mode; this request leaves it unset.
  // GPT-5.6 defaults to all_turns. Accepting that metadata does not add history:
  // conversation/previous_response_id and all non-message output gates remain.
  const context = nullableEnum("context", ["auto", "current_turn", "all_turns"]);
  const summary = nullableEnum("summary", ["auto", "concise", "detailed"]);
  const generateSummary = nullableEnum("generate_summary", ["auto", "concise", "detailed"]);
  return { record, effort, mode, context, summary, generateSummary,
    valid: Boolean(record && effort && mode && context && summary && generateSummary) };
}

function isObservedDeveloperBilling(value) {
  // Exact redacted observation, not an upstream billing schema or cost proof.
  // No coercion, optional charges, observer-derived authority or default payer.
  const billing = captureRecord(value, ["payer"]);
  return Boolean(billing && billing.payer === "developer");
}

function describeResponseFieldPolicy(envelope) {
  const reasoning = inspectReasoningPolicy(envelope.reasoning);
  const detail = (rule, checked, valid, parent, key) => ({ rule,
    status: checked ? valid ? "passed" : "failed" : "not_checked",
    jsonType: responseJsonType(parent, key) });
  // A fixed seven-rule diagnostic only. No values, dynamic names, object keys,
  // provider identifiers, counts or pricing inference are captured.
  return [
    // Preserve the legacy diagnostic vocabulary: the absence-only subrule is
    // inapplicable to the separate observed shape, not a failed billing gate.
    detail("billing_absent_or_null", !isObservedDeveloperBilling(envelope.billing),
      !Object.hasOwn(envelope, "billing") || envelope.billing === null, envelope, "billing"),
    detail("reasoning_closed_record_or_null", true,
      !Object.hasOwn(envelope, "reasoning") || envelope.reasoning === null || Boolean(reasoning.record), envelope, "reasoning"),
    detail("reasoning_effort_none", Boolean(reasoning.record), reasoning.effort, reasoning.record, "effort"),
    detail("reasoning_mode_standard", Boolean(reasoning.record), reasoning.mode, reasoning.record, "mode"),
    detail("reasoning_context_supported", Boolean(reasoning.record), reasoning.context, reasoning.record, "context"),
    detail("reasoning_summary_supported", Boolean(reasoning.record), reasoning.summary, reasoning.record, "summary"),
    detail("reasoning_generate_summary_supported", Boolean(reasoning.record), reasoning.generateSummary, reasoning.record, "generate_summary"),
  ];
}

function describeResponseEchoDetails(envelope) {
  // Observation only, after strict parsing and root capture. Inspect fixed keys,
  // never disclose provider values, unknown names, sizes or nested schema data.
  const jsonType = responseJsonType;
  const status = (checked, valid) => checked ? valid ? "passed" : "failed" : "not_checked";
  const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value)
    ? captureRecord(value, [], Object.keys(value)) : null;
  const requiredKeys = (value, keys) => Boolean(value && keys.every((key) => Object.hasOwn(value, key)));
  const allowedKeys = (value, keys) => Boolean(value && Object.keys(value).every((key) => keys.includes(key)));
  const toolUsageType = jsonType(envelope, "tool_usage");
  const toolUsage = record(envelope.tool_usage);
  const textType = jsonType(envelope, "text");
  const textRecord = record(envelope.text);
  const textConfig = captureRecord(envelope.text, ["format"], ["verbosity"]);
  const formatType = jsonType(textConfig, "format");
  const formatRecord = record(textConfig?.format);
  const format = textConfig && captureRecord(textConfig.format, ["type", "name", "strict", "schema"], ["description"]);
  const descriptionType = jsonType(format, "description");
  const descriptionPresent = Boolean(format && Object.hasOwn(format, "description"));
  const descriptionString = descriptionPresent && typeof format.description === "string";
  return {
    toolUsage: {
      jsonType: toolUsageType,
      objectRecord: status(toolUsageType !== "absent", Boolean(toolUsage)),
      emptyRecord: status(Boolean(toolUsage), Boolean(captureRecord(envelope.tool_usage, []))),
    },
    textFormat: {
      textJsonType: textType, formatJsonType: formatType, descriptionJsonType: descriptionType,
      textRecord: status(textType !== "absent", Boolean(textRecord)),
      textRequiredKeys: status(Boolean(textRecord), requiredKeys(textRecord, ["format"])),
      textAllowedKeys: status(Boolean(textRecord), allowedKeys(textRecord, ["format", "verbosity"])),
      formatRecord: status(Boolean(textConfig), Boolean(formatRecord)),
      formatRequiredKeys: status(Boolean(formatRecord), requiredKeys(formatRecord, ["type", "name", "strict", "schema"])),
      formatAllowedKeys: status(Boolean(formatRecord), allowedKeys(formatRecord, ["type", "name", "strict", "schema", "description"])),
      typeJsonSchema: status(Boolean(format), format?.type === "json_schema"),
      nameExact: status(Boolean(format), format?.name === STRUCTURED_OUTPUT_NAME),
      strictTrue: status(Boolean(format), format?.strict === true),
      descriptionString: status(descriptionPresent, descriptionString),
      descriptionSafeText: status(descriptionString, descriptionString && safeText(format.description)),
      descriptionUtf8Limit: status(descriptionString, descriptionString &&
        new TextEncoder().encode(format.description).byteLength <= NPC_COGNITION_LIMITS.dialogueBytes),
    },
  };
}

function createResponseChecks(envelope, retainReservationOnFailure = false) {
  const statuses = new Map(RESPONSE_CHECK_RULES.map((rule) => [rule, "not_checked"]));
  const fieldPolicyFailures = [];
  let firstFailure = null;
  return {
    check(rule, valid, stage, code = "R22_PROVIDER_RESPONSE_INVALID", evidence) {
      // All rule names and states are internal literals, never provider values.
      if (!statuses.has(rule) || statuses.get(rule) !== "not_checked") operational();
      statuses.set(rule, valid ? "passed" : "failed");
      if (!valid) firstFailure ??= responseFailure(code, stage, evidence);
      return Boolean(valid);
    },
    field(field, valid) {
      if (valid) return;
      // Only internal literals from validateResponseFieldPolicy, never response
      // names or values. Do not steal the original rules' refusal/failure code.
      fieldPolicyFailures.push(field);
    },
    finish() {
      if (fieldPolicyFailures.length > 0) {
        firstFailure ??= responseFailure("R22_PROVIDER_RESPONSE_INVALID", "envelope_profile");
        // An unexplained capability/billing field cannot be fully priced from
        // language tokens alone. Keep the old primary diagnostic, but never let
        // its usage evidence refund an uncertain reservation.
        if (firstFailure.evidence) firstFailure = { ...firstFailure, evidence: {
          ...firstFailure.evidence, usage: null, actualCostMicrousd: null, costUncertain: true,
        } };
      }
      if (firstFailure === null) return null;
      // Observed counters/payer are narrow compatibility shapes, not a complete
      // bill. A later invalid output/usage/choice must not refund the old
      // conservative reservation using language-token evidence alone.
      if (retainReservationOnFailure && firstFailure.evidence) {
        firstFailure = { ...firstFailure, evidence: {
          ...firstFailure.evidence, usage: null, actualCostMicrousd: null, costUncertain: true,
        } };
      }
      let echoDetails;
      try { echoDetails = describeResponseEchoDetails(envelope); }
      catch { /* A diagnostic failure must not change the original rejection or accounting. */ }
      let fieldPolicyChecks;
      if (fieldPolicyFailures.includes("billing") || fieldPolicyFailures.includes("reasoning")) {
        try { fieldPolicyChecks = describeResponseFieldPolicy(envelope); }
        catch { /* Observation only: never override rejection, cost or publication. */ }
      }
      return { ...firstFailure, responseDiagnostic: { ...firstFailure.responseDiagnostic,
        checks: RESPONSE_CHECK_RULES.map((rule) => ({ rule, status: statuses.get(rule) })),
        ...(fieldPolicyFailures.length === 0 ? {} : {
          envelopeProfile: RESPONSE_ENVELOPE_PROFILE, fieldPolicyFailures,
        }),
        ...(fieldPolicyChecks === undefined ? {} : { fieldPolicyChecks }),
        ...(echoDetails === undefined ? {} : { echoDetails }) } };
    },
  };
}

function validateResponseFieldPolicy(envelope, checks) {
  const optional = (key, valid) => checks.field(key, !Object.hasOwn(envelope, key) || valid);
  const nonnegativeInteger = (value) => Number.isSafeInteger(value) && value >= 0 && !Object.is(value, -0);
  const finiteRange = (value, min, max) => typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
  // Opaque service metadata: never returned, stored, interpreted as authority or
  // used to derive an action. Limits are local, not a vendor ID format guarantee.
  checks.field("id", typeof envelope.id === "string" && envelope.id.length >= 1 &&
    envelope.id.length <= 256 && safeText(envelope.id, { allowLf: false }));
  optional("created_at", nonnegativeInteger(envelope.created_at));
  optional("completed_at", envelope.completed_at === null || nonnegativeInteger(envelope.completed_at));
  // These capabilities/identities were not requested. null is accepted only as
  // absence; an empty object, known name or zero-looking nested value is NOT a
  // documented neutral configuration. Only the separately observed exact payer
  // record is accepted as discarded metadata, never as a price or authority.
  for (const key of ["agent", "billing", "context_management", "moderation", "prompt_cache_diagnostics",
    "max_tool_calls", "prompt_cache_key", "safety_identifier", "user"]) {
    optional(key, envelope[key] === null || (key === "billing" && isObservedDeveloperBilling(envelope[key])));
  }
  optional("reasoning", envelope.reasoning === null || inspectReasoningPolicy(envelope.reasoning).valid);
  // Public PromptCacheOptions (pinned source in the reference audit) describes
  // applied caching, not token charges. Charges still come only from strict usage.
  // This request declares no explicit cache key or comparison response identity.
  const cache = captureRecord(envelope.prompt_cache_options, ["mode", "ttl"], ["comparison_response_id"]);
  optional("prompt_cache_options", Boolean(cache && cache.mode === "implicit" && cache.ttl === "30m" &&
    (!Object.hasOwn(cache, "comparison_response_id") || cache.comparison_response_id === null)));
  optional("prompt_cache_retention", envelope.prompt_cache_retention === null ||
    ["in_memory", "24h"].includes(envelope.prompt_cache_retention));
  // Generation metadata is discarded. Output length, usage and the closed
  // proposal remain independently checked; no model configuration is executed.
  optional("temperature", envelope.temperature === null || finiteRange(envelope.temperature, 0, 2));
  optional("top_p", envelope.top_p === null || finiteRange(envelope.top_p, 0, 1));
  optional("top_logprobs", envelope.top_logprobs === null || Object.is(envelope.top_logprobs, 0));
  const text = captureRecord(envelope.text, ["format"], ["verbosity"]);
  checks.field("text_verbosity", !text || !Object.hasOwn(text, "verbosity") ||
    text.verbosity === null || ["low", "medium", "high"].includes(text.verbosity));
}

function classifyToolUsage(value, responseText) {
  if (captureRecord(value, [])) return "empty";
  // Exact shape observed in the separately approved diagnostic; not the full
  // upstream schema. Do not authorize from observer output, recurse over unknown
  // zero fields, coerce values, or fill missing counters. All other gates apply.
  const usage = captureRecord(value, ["image_gen", "web_search"]);
  const image = usage && captureRecord(usage.image_gen, [
    "input_tokens", "input_tokens_details", "output_tokens", "output_tokens_details", "total_tokens",
  ]);
  const web = usage && captureRecord(usage.web_search, ["num_requests"]);
  const input = image && captureRecord(image.input_tokens_details, ["image_tokens", "text_tokens"]);
  const output = image && captureRecord(image.output_tokens_details, ["image_tokens", "text_tokens"]);
  if (!image || !web || !input || !output || ![
    image.input_tokens, input.image_tokens, input.text_tokens,
    image.output_tokens, output.image_tokens, output.text_tokens, image.total_tokens, web.num_requests,
  ].every((counter) => Object.is(counter, 0))) return null;
  // Bounded second scan only for the new form; preserve original JSON and the
  // legacy absent/empty results. Ambiguous underflow anywhere fails closed.
  try { scanStrictJson(responseText, { rejectNumberUnderflow: true }); }
  catch { return null; }
  return "observed-zero";
}

function validateResponseRootEchoes(envelope, callPlan, providerBody, responseSchema, checks, toolUsageKind) {
  const echo = (key, valid) => checks.check(`echo_${key}`, valid, "root_echo");
  const optional = (key, valid) => echo(key, !Object.hasOwn(envelope, key) || valid);
  // The two penalty echoes still allow parsed positive zero only. Tool usage
  // separately accepts the complete observed zero-counter shape, never names alone.
  for (const key of ["frequency_penalty", "presence_penalty"]) {
    optional(key, Object.is(envelope[key], 0));
  }
  optional("tool_usage", toolUsageKind !== null);
  optional("background", envelope.background === false);
  optional("store", envelope.store === false);
  optional("truncation", envelope.truncation === "disabled");
  optional("max_output_tokens", envelope.max_output_tokens === callPlan.maxOutputTokens);
  optional("instructions", envelope.instructions === providerBody.instructions);
  optional("previous_response_id", envelope.previous_response_id === null);
  optional("conversation", envelope.conversation === null);
  optional("prompt", envelope.prompt === null);
  optional("tools", Array.isArray(envelope.tools) && envelope.tools.length === 0);
  optional("tool_choice", envelope.tool_choice === "auto" || envelope.tool_choice === "none");
  optional("parallel_tool_calls", typeof envelope.parallel_tool_calls === "boolean");
  optional("metadata", envelope.metadata === null || Boolean(captureRecord(envelope.metadata, [])));
  // Omitted requests inherit project settings. Both the approved request and
  // actual returned tier must be Standard; unknown tiers cannot use its prices.
  echo("service_tier", envelope.service_tier === "default");
  if (Object.hasOwn(envelope, "text")) {
    const textConfig = captureRecord(envelope.text, ["format"], ["verbosity"]);
    const format = textConfig && captureRecord(textConfig.format, ["type", "name", "strict", "schema"], ["description"]);
    checks.check("echo_text_format", Boolean(format && format.type === "json_schema" &&
      format.name === STRUCTURED_OUTPUT_NAME && format.strict === true &&
      // Observed null is a service normalization, not an official nullable-schema
      // assertion. A bounded description is discarded; name/strict/schema still
      // bind the approved format and the model's three-field result stays closed.
      (!Object.hasOwn(format, "description") || format.description === null || (typeof format.description === "string" &&
        safeText(format.description) && new TextEncoder().encode(format.description).byteLength <= NPC_COGNITION_LIMITS.dialogueBytes))),
    "text_format");
    if (format) {
      let schemaMatches = false;
      try {
        schemaMatches = Boolean(validateDynamicResponseSchema(format.schema, callPlan.contextSha256)) &&
          sha256Text(canonicalizeJsonValue(format.schema)) === callPlan.responseSchemaSha256 &&
          responseSchema.schema.properties.actionChoiceId.enum.includes(null);
      } catch { /* Fixed failure below, no raw schema or exception. */ }
      checks.check("echo_schema", schemaMatches, "schema_echo");
    }
  }
}

function describeRejectedResponseRoot(value) {
  // Called only after strict JSON.parse and the unchanged captureRecord gate.
  // The user-approved observer exposes at most 16 simple root names and their
  // shallow JSON types. Names remain untrusted data, not acceptance evidence.
  // Never disclose values, nested names, lengths or hashes. The fixed mask is:
  // id, object, status, error, incomplete_details, model, output, usage.
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { rootKind: Array.isArray(value) ? "array" : "non_object",
      missingRequiredMask: null, unknownKeysPresent: null };
  }
  const allowed = new Set([...RESPONSE_ROOT_REQUIRED_KEYS, ...RESPONSE_ROOT_OPTIONAL_KEYS]);
  const missingRequiredMask = RESPONSE_ROOT_REQUIRED_KEYS.reduce((mask, key, index) =>
    Object.hasOwn(value, key) ? mask : mask | (1 << index), 0);
  const unknownNames = Object.keys(value).filter((key) => !allowed.has(key));
  const rootFields = { rootKind: "record", missingRequiredMask, unknownKeysPresent: unknownNames.length > 0 };
  if (unknownNames.length === 0) return rootFields;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const unknownFields = [];
  let unknownFieldsOmitted = false;
  for (const name of unknownNames.sort()) {
    const descriptor = descriptors[name];
    if (!/^[a-z][a-z0-9_]{0,63}$/u.test(name) || !descriptor?.enumerable ||
        !Object.hasOwn(descriptor, "value") || unknownFields.length === 16) {
      unknownFieldsOmitted = true;
      continue;
    }
    const fieldValue = descriptor.value;
    const jsonType = fieldValue === null ? "null" : Array.isArray(fieldValue) ? "array" : typeof fieldValue;
    if (!["null", "array", "object", "string", "number", "boolean"].includes(jsonType)) {
      unknownFieldsOmitted = true;
      continue;
    }
    unknownFields.push({ name, jsonType });
  }
  return { ...rootFields, unknownFields, unknownFieldsOmitted };
}

function responseFailure(code, stage, evidence, rootFields = null) {
  // Internal enum literals identify the rejecting check. The bounded root-name
  // observer is the sole exception; values, IDs, body hashes and dialogue stay out.
  return { ok: false, code, evidence, responseDiagnostic: { status: 200, stage,
    ...(rootFields === null ? {} : { rootFields }) } };
}

function parseResponseEnvelope(text, callPlan, providerBody, responseSchema, observationState = null) {
  const parsedEnvelope = parseStrictJson(text);
  if (parsedEnvelope === null) return responseFailure("R22_PROVIDER_RESPONSE_INVALID", "json");
  const envelope = parsedEnvelope && captureRecord(
    parsedEnvelope,
    RESPONSE_ROOT_REQUIRED_KEYS,
    RESPONSE_ROOT_OPTIONAL_KEYS,
  );
  if (!envelope) return responseFailure("R22_PROVIDER_RESPONSE_INVALID", "root_fields", undefined,
    describeRejectedResponseRoot(parsedEnvelope));
  if (observationState !== null) {
    // Private one-way tap AFTER the same strict parser/root capture. Never feeds
    // the response checks, primary diagnostic, usage accounting, or proposal.
    observationState.value = observationState.billing
      ? observeBilling(envelope.billing, observationState.binding, { present: Object.hasOwn(envelope, "billing") })
      : observeToolUsage(envelope.tool_usage, observationState.binding, { present: Object.hasOwn(envelope, "tool_usage") });
  }
  const toolUsageKind = classifyToolUsage(envelope.tool_usage, text);
  const checks = createResponseChecks(envelope,
    toolUsageKind === "observed-zero" || isObservedDeveloperBilling(envelope.billing));
  validateResponseRootEchoes(envelope, callPlan, providerBody, responseSchema, checks, toolUsageKind);
  validateResponseFieldPolicy(envelope, checks);
  const model = typeof envelope.model === "string" && MODEL_ID.test(envelope.model)
    ? envelope.model
    : null;
  const usage = usageEvidence(envelope.usage, callPlan);
  // Still diagnose usage independently of tier/echo failures. Such failures
  // retain the original conservative first-failure evidence, never this cost.
  const pricedUsage = model === callPlan.model ? usage : null;
  const evidence = {
    returnedModel: model,
    usage: pricedUsage?.usage ?? null,
    actualCostMicrousd: pricedUsage?.actualCostMicrousd ?? null,
    costUncertain: pricedUsage === null,
  };
  checks.check("usage", Boolean(usage), "usage", "R22_PROVIDER_USAGE_INVALID", evidence);
  checks.check("model", model === NPC_COGNITION_MODEL, "model", "R22_PROVIDER_MODEL_MISMATCH", evidence);
  checks.check("completion", envelope.object === "response" && envelope.status === "completed" &&
    envelope.error === null && envelope.incomplete_details === null, "completion", "R22_PROVIDER_RESPONSE_INVALID", evidence);
  if (!Array.isArray(envelope.output) || envelope.output.length !== 1) {
    const refused = Array.isArray(envelope.output) && envelope.output.some((item) =>
      item?.type === "message" && Array.isArray(item.content) && item.content.some((part) => part?.type === "refusal"));
    checks.check("output", false, "output", refused ? "R22_PROVIDER_REFUSED" : "R22_UNTRUSTED_OUTPUT_REJECTED", evidence);
    return checks.finish();
  }
  checks.check("output", true, "output");
  const message = captureRecord(
    envelope.output[0],
    ["id", "type", "status", "role", "content"],
    ["phase"],
  );
  checks.check("message", Boolean(message && message.type === "message" && message.status === "completed" && message.role === "assistant" &&
    typeof message.id === "string" && message.id.length >= 1 && message.id.length <= 256 && safeText(message.id, { allowLf: false }) &&
    (!Object.hasOwn(message, "phase") || message.phase === null || message.phase === "final_answer")),
  "message", "R22_UNTRUSTED_OUTPUT_REJECTED", evidence);
  if (!message) return checks.finish();
  if (!Array.isArray(message.content) || message.content.length !== 1) {
    const refused = Array.isArray(message.content) && message.content.some((part) => part?.type === "refusal");
    checks.check("content", false, "content", refused ? "R22_PROVIDER_REFUSED" : "R22_UNTRUSTED_OUTPUT_REJECTED", evidence);
    return checks.finish();
  }
  if (message.content[0]?.type === "refusal") {
    checks.check("content", false, "content", "R22_PROVIDER_REFUSED", evidence);
    return checks.finish();
  }
  const content = captureRecord(message.content[0], ["type", "text", "annotations"], ["logprobs"]);
  checks.check("content", Boolean(content && content.type === "output_text" && Array.isArray(content.annotations) && content.annotations.length === 0 &&
    (!Object.hasOwn(content, "logprobs") || (Array.isArray(content.logprobs) && content.logprobs.length === 0))),
  "content", "R22_UNTRUSTED_OUTPUT_REJECTED", evidence);
  if (!content) return checks.finish();
  const proposal = parseFlatProposal(content.text);
  checks.check("proposal_json", Boolean(proposal), "proposal_json", "R22_PROVIDER_RESPONSE_INVALID", evidence);
  if (!proposal) return checks.finish();
  checks.check("proposal_context", proposal.contextSha256 === callPlan.contextSha256,
    "proposal_context", "R22_UNTRUSTED_OUTPUT_REJECTED", evidence);
  checks.check("dialogue_text", typeof proposal.dialogueText === "string" &&
    safeText(proposal.dialogueText) && /\S/u.test(proposal.dialogueText) &&
    new TextEncoder().encode(proposal.dialogueText).byteLength <= NPC_COGNITION_LIMITS.dialogueBytes &&
    proposal.dialogueText.split("\n").length <= NPC_COGNITION_LIMITS.dialogueLines,
  "dialogue_text", "R22_UNTRUSTED_OUTPUT_REJECTED", evidence);
  checks.check("action_choice", proposal.actionChoiceId === null
    ? responseSchema.schema.properties.actionChoiceId.enum.includes(null)
    : responseSchema.choices.has(proposal.actionChoiceId), "action_choice", "R22_ACTION_CHOICE_UNKNOWN", evidence);
  const rejected = checks.finish();
  if (rejected) return rejected;
  const normalized = deepFreeze({
    contextSha256: proposal.contextSha256,
    dialogueText: proposal.dialogueText,
    actionChoiceId: proposal.actionChoiceId,
  });
  return {
    ok: true,
    evidence,
    proposal: normalized,
    proposalJson: canonicalizeJsonValue(normalized),
  };
}

export function createOpenAiNpcCognitionProvider(config) {
  const captured = captureRecord(config, ["apiKey"], ["fetchImplementation"]);
  if (!captured || typeof captured.apiKey !== "string") operational();
  const fetchImplementation = captured.fetchImplementation ?? globalThis.fetch;
  if (typeof fetchImplementation !== "function") operational();
  const credential =
    /^[\u0021-\u007e]{1,8192}$/u.test(captured.apiKey)
      ? captured.apiKey
      : null;
  const provider = deepFreeze({
    kind: "openai-npc-cognition",
    endpoint: NPC_COGNITION_ENDPOINT,
    model: NPC_COGNITION_MODEL,
  });
  PROVIDER_STATE.set(provider, { credential, fetchImplementation, attemptedCallPlans: new Set() });
  return provider;
}

export async function executeApprovedNpcCognitionTurn(input, provider) {
  return executeProviderTurn(input, provider);
}

// Offline diagnostic conformance entry only. It accepts bytes, NOT a credential,
// fetch callback, environment reader, durable root, or Godot/Runtime handle. The
// shared parser is driven by an in-memory Response. The separately guarded CLI
// owns dispatch/approval/budget; this public fixture entry never grants a request.
export async function evaluateNpcCognitionToolUsageDiagnosticFixture(input, responseBytes) {
  return evaluateDiagnosticFixture(input, responseBytes, createNpcCognitionToolUsageDiagnosticPlan(), false);
}

export async function evaluateNpcCognitionBillingDiagnosticFixture(input, responseBytes) {
  return evaluateDiagnosticFixture(input, responseBytes, createNpcCognitionBillingDiagnosticPlan(), true);
}

async function evaluateDiagnosticFixture(input, responseBytes, expected, billing) {
  try {
    const captured = types.isProxy(input) ? null : captureRecord(input, Object.keys(expected));
    if (!captured || Object.keys(expected).some((key) => captured[key] !== expected[key]) ||
        types.isProxy(responseBytes) || !types.isUint8Array(responseBytes) ||
        types.isSharedArrayBuffer(BUFFER_GETTER.call(responseBytes)) ||
        BYTE_LENGTH_GETTER.call(responseBytes) > NPC_COGNITION_LIMITS.providerResponseBytes + 1) {
      return deepFreeze({ fixtureOnly: true, realRequestCount: 0, qualificationEligible: false,
        providerResult: failure("R22_APPROVAL_MISMATCH"), observation: null });
    }
    const binding = { callPlanSha256: sha256Text(captured.callPlanJson),
      diagnosticApprovalSha256: captured.diagnosticApprovalSha256 };
    const observationState = { binding, value: null, billing };
    const bytes = new Uint8Array(BYTE_LENGTH_GETTER.call(responseBytes));
    Uint8Array.prototype.set.call(bytes, responseBytes);
    const provider = createOpenAiNpcCognitionProvider({ apiKey: "offline-placeholder-fixture",
      fetchImplementation: async () => new Response(bytes, { headers: { "content-type": "application/json" } }) });
    const providerResult = await executeProviderTurn({ callPlanJson: captured.callPlanJson,
      providerRequestJson: captured.providerRequestJson, approvalHash: captured.approvalHash }, provider, observationState);
    const observation = observationState.value ?? (providerResult.requestCount === 1 ? {
      profile: expected.diagnosticProfile, capturePolicySha256: expected.capturePolicySha256,
      binding, semanticCoverage: "observation_only", qualificationEligible: false, status: "not_captured",
    } : null);
    return deepFreeze({ fixtureOnly: true, realRequestCount: 0, qualificationEligible: false, providerResult, observation });
  } catch { operational(); }
}

async function executeProviderTurn(input, provider, observationState = null) {
  try {
    const state = PROVIDER_STATE.get(provider);
    if (!state) operational();
    const execution = validateExecutionInput(input, observationState !== null);
    if (!execution) return failure("R22_APPROVAL_MISMATCH");
    if (state.credential === null) return failure("R22_PROVIDER_CREDENTIAL_UNAVAILABLE");

    const callPlanSha256 = sha256Text(execution.callPlanJson);
    if (state.attemptedCallPlans.has(callPlanSha256)) return failure("R22_CALL_IN_FLIGHT");
    state.attemptedCallPlans.add(callPlanSha256);

    const deadline = createAbsoluteDeadline(NPC_COGNITION_LIMITS.timeoutMs);
    try {
      let pendingFetch;
      try {
        pendingFetch = state.fetchImplementation(NPC_COGNITION_ENDPOINT, {
          method: "POST",
          redirect: "error",
          credentials: "omit",
          cache: "no-store",
          signal: deadline.controller.signal,
          headers: {
            accept: "application/json",
            authorization: `Bearer ${state.credential}`,
            "content-type": "application/json",
          },
          body: execution.providerRequestJson,
        });
      } catch (error) {
        return failure(
          deadline.controller.signal.aborted || error?.name === "AbortError"
            ? "R22_PROVIDER_TIMEOUT"
            : "R22_PROVIDER_NETWORK_AMBIGUOUS",
          { requestCount: 1, costUncertain: true },
        );
      }
      const fetchOutcome = await deadline.race(pendingFetch);
      if (fetchOutcome === TIMEOUT_RESULT) {
        return failure("R22_PROVIDER_TIMEOUT", { requestCount: 1, costUncertain: true });
      }
      if (fetchOutcome.kind === "error") {
        return failure(
          deadline.controller.signal.aborted || fetchOutcome.error?.name === "AbortError"
            ? "R22_PROVIDER_TIMEOUT"
            : "R22_PROVIDER_NETWORK_AMBIGUOUS",
          { requestCount: 1, costUncertain: true },
        );
      }
      const response = fetchOutcome.value;
      if (!response || typeof response !== "object") {
        return failure("R22_PROVIDER_NETWORK_AMBIGUOUS", { requestCount: 1, costUncertain: true });
      }
      let status;
      let redirected;
      try {
        status = response.status;
        redirected = response.redirected;
      } catch {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_RESPONSE_INVALID", { requestCount: 1, costUncertain: true });
      }
      if (
        !Number.isInteger(status) || status < 100 || status > 599 ||
        typeof redirected !== "boolean"
      ) {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_RESPONSE_INVALID", { requestCount: 1, costUncertain: true });
      }
      if (redirected || (status >= 300 && status < 400)) {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_NETWORK_AMBIGUOUS", { requestCount: 1, costUncertain: true });
      }
      if (status >= 400) {
        const httpDiagnostic = await readHttpDiagnostic(response, deadline, status);
        return failure(status >= 500 ? "R22_PROVIDER_NETWORK_AMBIGUOUS" : "R22_PROVIDER_REFUSED",
          { requestCount: 1, costUncertain: true, httpDiagnostic });
      }
      if (status !== 200) {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_RESPONSE_INVALID", { requestCount: 1, costUncertain: true });
      }
      let contentType;
      try {
        contentType = response.headers?.get("content-type") ?? "";
      } catch {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_RESPONSE_INVALID", { requestCount: 1, costUncertain: true,
          responseDiagnostic: { status: 200, stage: "content_type" } });
      }
      if (typeof contentType !== "string" || !JSON_CONTENT_TYPE.test(contentType)) {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_RESPONSE_INVALID", { requestCount: 1, costUncertain: true,
          responseDiagnostic: { status: 200, stage: "content_type" } });
      }
      const body = await readBoundedResponse(response, deadline);
      if (!body.ok) {
        cancelResponseBody(response);
        return failure(body.code, { requestCount: 1, costUncertain: true,
          responseDiagnostic: { status: 200, stage: "body" } });
      }
      if (deadline.controller.signal.aborted) {
        return failure("R22_PROVIDER_TIMEOUT", { requestCount: 1, costUncertain: true });
      }
      const parsed = parseResponseEnvelope(
        body.text,
        execution.callPlan,
        execution.payload.body,
        execution.payload.responseSchema,
        observationState,
      );
      if (deadline.controller.signal.aborted) {
        return failure("R22_PROVIDER_TIMEOUT", { requestCount: 1, costUncertain: true });
      }
      if (!parsed.ok) {
        return failure(parsed.code, { requestCount: 1, costUncertain: true, ...parsed.evidence,
          responseDiagnostic: parsed.responseDiagnostic });
      }
      return deepFreeze({
        ok: true,
        requestCount: 1,
        costUncertain: false,
        returnedModel: NPC_COGNITION_MODEL,
        usage: parsed.evidence.usage,
        actualCostMicrousd: parsed.evidence.actualCostMicrousd,
        responseBytes: body.byteLength,
        proposal: parsed.proposal,
        proposalJson: parsed.proposalJson,
      });
    } finally {
      deadline.close();
    }
  } catch (error) {
    if (error instanceof NpcCognitionProviderOperationalError) throw error;
    operational();
  }
}

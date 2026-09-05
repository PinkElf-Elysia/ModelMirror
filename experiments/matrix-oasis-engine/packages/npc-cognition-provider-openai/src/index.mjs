import { createHash } from "node:crypto";
import {
  NPC_COGNITION_ENDPOINT,
  NPC_COGNITION_LIMITS,
  NPC_COGNITION_MODEL,
  NPC_COGNITION_TRUSTED_INSTRUCTIONS,
  computeNpcCognitionApprovalHash,
  validateNpcCognitionCallPlanJson,
} from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const PROVIDER_STATE = new WeakMap();
const INTERNAL_CODE = "NPC_COGNITION_INTERNAL_ERROR";
const JSON_CONTENT_TYPE = /^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/iu;
const SHA256 = /^sha256:[0-9a-f]{64}$/u;
const CHOICE_ID = /^choice-[0-9a-f]{64}$/u;
const RESPONSE_DEPTH_LIMIT = 256;
const RESPONSE_CHUNK_LIMIT = 4096;
const STRUCTURED_OUTPUT_NAME = "matrix_oasis_npc_dialogue_proposal";
const TIMEOUT_RESULT = Object.freeze({ kind: "timeout" });
const MODEL_ID = /^[A-Za-z0-9._:-]{1,128}$/u;
const PROPOSAL_KEYS = Object.freeze(["contextSha256", "dialogueText", "actionChoiceId"]);
const PAYLOAD_KEYS = Object.freeze([
  "background",
  "input",
  "instructions",
  "max_output_tokens",
  "model",
  "reasoning",
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
// Current documented Responses fields. Unused fields remain inert, but an
// unknown root field is treated as provider-schema drift and fails closed.
const RESPONSE_ROOT_OPTIONAL_KEYS = Object.freeze([
  "agent",
  "background",
  "billing",
  "completed_at",
  "context_management",
  "conversation",
  "created_at",
  "instructions",
  "max_output_tokens",
  "max_tool_calls",
  "metadata",
  "moderation",
  "parallel_tool_calls",
  "previous_response_id",
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

function scanStrictJson(text) {
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
  const context = properties && exactKeys(properties.contextSha256, ["const"]);
  const dialogue = properties && exactKeys(properties.dialogueText, ["type", "minLength", "maxLength"]);
  const choice = properties && exactKeys(properties.actionChoiceId, ["enum"]);
  if (
    !context || context.const !== contextSha256 ||
    !dialogue || dialogue.type !== "string" || dialogue.minLength !== 1 ||
    dialogue.maxLength !== NPC_COGNITION_LIMITS.dialogueBytes ||
    !choice || !Array.isArray(choice.enum) || choice.enum.length < 1 ||
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

function validateProviderPayload(providerRequestJson, callPlan) {
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
  const body = exactKeys(parsed, PAYLOAD_KEYS);
  if (
    !body || body.background !== false || body.stream !== false || body.store !== false ||
    body.truncation !== "disabled" || body.model !== NPC_COGNITION_MODEL ||
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

function validateExecutionInput(input) {
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
  const payload = validateProviderPayload(captured.providerRequestJson, callPlan);
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

function validateResponseRootEchoes(envelope, callPlan, providerBody, responseSchema) {
  if (
    (Object.hasOwn(envelope, "background") && envelope.background !== false) ||
    (Object.hasOwn(envelope, "store") && envelope.store !== false) ||
    (Object.hasOwn(envelope, "truncation") && envelope.truncation !== "disabled") ||
    (Object.hasOwn(envelope, "max_output_tokens") && envelope.max_output_tokens !== callPlan.maxOutputTokens) ||
    (Object.hasOwn(envelope, "instructions") && envelope.instructions !== providerBody.instructions) ||
    (Object.hasOwn(envelope, "previous_response_id") && envelope.previous_response_id !== null) ||
    (Object.hasOwn(envelope, "conversation") && envelope.conversation !== null) ||
    (Object.hasOwn(envelope, "prompt") && envelope.prompt !== null)
  ) return false;
  if (Object.hasOwn(envelope, "tools") && (!Array.isArray(envelope.tools) || envelope.tools.length !== 0)) return false;
  if (
    Object.hasOwn(envelope, "tool_choice") &&
    envelope.tool_choice !== "auto" &&
    envelope.tool_choice !== "none"
  ) return false;
  if (
    Object.hasOwn(envelope, "parallel_tool_calls") &&
    typeof envelope.parallel_tool_calls !== "boolean"
  ) return false;
  if (Object.hasOwn(envelope, "metadata")) {
    const metadata = captureRecord(envelope.metadata, []);
    if (!metadata || Reflect.ownKeys(metadata).length !== 0) return false;
  }
  if (Object.hasOwn(envelope, "text")) {
    const textConfig = captureRecord(envelope.text, ["format"], ["verbosity"]);
    const format = textConfig && exactKeys(textConfig.format, ["type", "name", "strict", "schema"]);
    if (
      !format || format.type !== "json_schema" || format.name !== STRUCTURED_OUTPUT_NAME ||
      format.strict !== true || !validateDynamicResponseSchema(format.schema, callPlan.contextSha256)
    ) return false;
    let responseSchemaJson;
    try {
      responseSchemaJson = canonicalizeJsonValue(format.schema);
    } catch {
      return false;
    }
    if (sha256Text(responseSchemaJson) !== callPlan.responseSchemaSha256) return false;
  }
  return responseSchema.schema.properties.actionChoiceId.enum.includes(null);
}

function parseResponseEnvelope(text, callPlan, providerBody, responseSchema) {
  const parsedEnvelope = parseStrictJson(text);
  const envelope = parsedEnvelope && captureRecord(
    parsedEnvelope,
    RESPONSE_ROOT_REQUIRED_KEYS,
    RESPONSE_ROOT_OPTIONAL_KEYS,
  );
  if (!envelope || !validateResponseRootEchoes(envelope, callPlan, providerBody, responseSchema)) {
    return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID" };
  }
  const model = typeof envelope.model === "string" && MODEL_ID.test(envelope.model)
    ? envelope.model
    : null;
  const usage = usageEvidence(envelope.usage, callPlan);
  const evidence = {
    returnedModel: model,
    usage: usage?.usage ?? null,
    actualCostMicrousd: usage?.actualCostMicrousd ?? null,
    costUncertain: usage === null,
  };
  if (!usage) return { ok: false, code: "R22_PROVIDER_USAGE_INVALID", evidence };
  if (model !== NPC_COGNITION_MODEL) {
    return { ok: false, code: "R22_PROVIDER_MODEL_MISMATCH", evidence };
  }
  if (envelope.object !== "response" || envelope.status !== "completed" || envelope.error !== null || envelope.incomplete_details !== null) {
    return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID", evidence };
  }
  if (!Array.isArray(envelope.output) || envelope.output.length !== 1) {
    const refused = Array.isArray(envelope.output) && envelope.output.some((item) =>
      item?.type === "message" && Array.isArray(item.content) && item.content.some((part) => part?.type === "refusal"));
    return { ok: false, code: refused ? "R22_PROVIDER_REFUSED" : "R22_UNTRUSTED_OUTPUT_REJECTED", evidence };
  }
  const message = captureRecord(
    envelope.output[0],
    ["id", "type", "status", "role", "content"],
    ["phase"],
  );
  if (!message || message.type !== "message" || message.status !== "completed" || message.role !== "assistant" ||
      typeof message.id !== "string" || message.id.length < 1 || message.id.length > 256 || !safeText(message.id, { allowLf: false }) ||
      (Object.hasOwn(message, "phase") && message.phase !== "final_answer")) {
    return { ok: false, code: "R22_UNTRUSTED_OUTPUT_REJECTED", evidence };
  }
  if (!Array.isArray(message.content) || message.content.length !== 1) {
    const refused = Array.isArray(message.content) && message.content.some((part) => part?.type === "refusal");
    return { ok: false, code: refused ? "R22_PROVIDER_REFUSED" : "R22_UNTRUSTED_OUTPUT_REJECTED", evidence };
  }
  if (message.content[0]?.type === "refusal") {
    return { ok: false, code: "R22_PROVIDER_REFUSED", evidence };
  }
  const content = captureRecord(message.content[0], ["type", "text", "annotations"], ["logprobs"]);
  if (
    !content || content.type !== "output_text" || !Array.isArray(content.annotations) || content.annotations.length !== 0 ||
    (Object.hasOwn(content, "logprobs") && (!Array.isArray(content.logprobs) || content.logprobs.length !== 0))
  ) return { ok: false, code: "R22_UNTRUSTED_OUTPUT_REJECTED", evidence };
  const proposal = parseFlatProposal(content.text);
  if (!proposal) return { ok: false, code: "R22_PROVIDER_RESPONSE_INVALID", evidence };
  if (proposal.contextSha256 !== callPlan.contextSha256) {
    return { ok: false, code: "R22_UNTRUSTED_OUTPUT_REJECTED", evidence };
  }
  if (
    typeof proposal.dialogueText !== "string" ||
    !safeText(proposal.dialogueText) || !/\S/u.test(proposal.dialogueText) ||
    new TextEncoder().encode(proposal.dialogueText).byteLength > NPC_COGNITION_LIMITS.dialogueBytes ||
    proposal.dialogueText.split("\n").length > NPC_COGNITION_LIMITS.dialogueLines
  ) return { ok: false, code: "R22_UNTRUSTED_OUTPUT_REJECTED", evidence };
  if (proposal.actionChoiceId !== null && !responseSchema.choices.has(proposal.actionChoiceId)) {
    return { ok: false, code: "R22_ACTION_CHOICE_UNKNOWN", evidence };
  }
  if (proposal.actionChoiceId === null && !responseSchema.schema.properties.actionChoiceId.enum.includes(null)) {
    return { ok: false, code: "R22_ACTION_CHOICE_UNKNOWN", evidence };
  }
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
  try {
    const state = PROVIDER_STATE.get(provider);
    if (!state) operational();
    const execution = validateExecutionInput(input);
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
      if (redirected || (status >= 300 && status < 400) || status >= 500) {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_NETWORK_AMBIGUOUS", { requestCount: 1, costUncertain: true });
      }
      if (status >= 400) {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_REFUSED", { requestCount: 1, costUncertain: true });
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
        return failure("R22_PROVIDER_RESPONSE_INVALID", { requestCount: 1, costUncertain: true });
      }
      if (typeof contentType !== "string" || !JSON_CONTENT_TYPE.test(contentType)) {
        cancelResponseBody(response);
        return failure("R22_PROVIDER_RESPONSE_INVALID", { requestCount: 1, costUncertain: true });
      }
      const body = await readBoundedResponse(response, deadline);
      if (!body.ok) {
        cancelResponseBody(response);
        return failure(body.code, { requestCount: 1, costUncertain: true });
      }
      if (deadline.controller.signal.aborted) {
        return failure("R22_PROVIDER_TIMEOUT", { requestCount: 1, costUncertain: true });
      }
      const parsed = parseResponseEnvelope(
        body.text,
        execution.callPlan,
        execution.payload.body,
        execution.payload.responseSchema,
      );
      if (deadline.controller.signal.aborted) {
        return failure("R22_PROVIDER_TIMEOUT", { requestCount: 1, costUncertain: true });
      }
      if (!parsed.ok) {
        return failure(parsed.code, { requestCount: 1, costUncertain: true, ...parsed.evidence });
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

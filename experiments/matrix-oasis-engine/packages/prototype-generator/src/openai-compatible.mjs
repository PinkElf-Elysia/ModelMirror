import { GENERATION_PROPOSAL_SCHEMA } from "@matrix-oasis/prototype-generation-contracts";

const PROVIDER_STATE = new WeakMap();
const DEFAULT_ENDPOINT_PATH = "/v1/chat/completions";
const OPENROUTER_HOST = "openrouter.ai";
const OPENROUTER_ENDPOINT_PATH = "/api/v1/chat/completions";
const REQUEST_TIMEOUT_MS = 120_000;
const PROMPT_MAX_BYTES = 32_768;
const RESPONSE_MAX_BYTES = 1_048_576;
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
const PROVIDER_SCHEMA_OMITTED_KEYWORDS = new Set(["$id", "not", "uniqueItems"]);
const PROVIDER_SCHEMA_KEYWORD_TRANSFORMS = new Map([["oneOf", "anyOf"]]);
const PROVIDER_DEFINITION_SEPARATOR = "__";

function rewriteNamespacedDefinitionRefs(value, namespace) {
  if (Array.isArray(value)) {
    return value.map((item) => rewriteNamespacedDefinitionRefs(item, namespace));
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const output = {};
  const nestedPrefix = `#/$defs/${namespace}/$defs/`;
  for (const [key, item] of Object.entries(value)) {
    output[key] =
      key === "$ref" && typeof item === "string" && item.startsWith(nestedPrefix)
        ? `#/$defs/${namespace}${PROVIDER_DEFINITION_SEPARATOR}${item.slice(nestedPrefix.length)}`
        : rewriteNamespacedDefinitionRefs(item, namespace);
  }
  return output;
}

function flattenProviderDefinitions(schema) {
  const output = {};
  for (const [key, item] of Object.entries(schema)) {
    if (key !== "$defs") {
      output[key] = item;
    }
  }
  const definitions = {};
  for (const [namespace, definition] of Object.entries(schema.$defs ?? {})) {
    const namespacedDefinition = {};
    for (const [key, item] of Object.entries(definition)) {
      if (key !== "$defs") {
        namespacedDefinition[key] = rewriteNamespacedDefinitionRefs(item, namespace);
      }
    }
    definitions[namespace] = namespacedDefinition;
    for (const [name, nestedDefinition] of Object.entries(definition.$defs ?? {})) {
      definitions[`${namespace}${PROVIDER_DEFINITION_SEPARATOR}${name}`] =
        rewriteNamespacedDefinitionRefs(nestedDefinition, namespace);
    }
  }
  output.$defs = definitions;
  return output;
}

function projectProviderSchema(value) {
  if (Array.isArray(value)) {
    return Object.freeze(value.map((item) => projectProviderSchema(item)));
  }
  if (value && typeof value === "object") {
    const output = {};
    for (const [key, item] of Object.entries(value)) {
      if (!PROVIDER_SCHEMA_OMITTED_KEYWORDS.has(key)) {
        const providerKey = PROVIDER_SCHEMA_KEYWORD_TRANSFORMS.get(key) ?? key;
        output[providerKey] = projectProviderSchema(item);
      }
    }
    if (output.type === "object" && output.properties) {
      output.required = Object.freeze(Object.keys(output.properties));
    }
    return Object.freeze(output);
  }
  return value;
}

const GENERATION_PROPOSAL_PROVIDER_SCHEMA = projectProviderSchema(
  flattenProviderDefinitions(GENERATION_PROPOSAL_SCHEMA),
);

export class PrototypeGeneratorOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_GENERATOR_INTERNAL_ERROR");
    this.name = "PrototypeGeneratorOperationalError";
    this.code = "PROTOTYPE_GENERATOR_INTERNAL_ERROR";
  }
}

function fail() {
  throw new PrototypeGeneratorOperationalError();
}

function descriptorsOf(value) {
  try {
    if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
      fail();
    }
    return Object.getOwnPropertyDescriptors(value);
  } catch (error) {
    if (error instanceof PrototypeGeneratorOperationalError) {
      throw error;
    }
    fail();
  }
}

function exactRecord(value, allowedKeys, requiredKeys = allowedKeys) {
  const descriptors = descriptorsOf(value);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.some((key) => typeof key !== "string" || !allowedKeys.includes(key)) ||
    requiredKeys.some((key) => !keys.includes(key))
  ) {
    fail();
  }
  const output = {};
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (!descriptor.enumerable || !("value" in descriptor)) {
      fail();
    }
    output[key] = descriptor.value;
  }
  return output;
}

function selectedRecord(value, selectedKeys, requiredKeys = selectedKeys) {
  const descriptors = descriptorsOf(value);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.some(
      (key) =>
        typeof key !== "string" ||
        !descriptors[key].enumerable ||
        !("value" in descriptors[key]),
    ) ||
    requiredKeys.some((key) => !keys.includes(key))
  ) {
    fail();
  }
  const output = {};
  for (const key of selectedKeys) {
    if (keys.includes(key)) {
      output[key] = descriptors[key].value;
    }
  }
  return output;
}

function parseEndpoint(value) {
  if (typeof value !== "string") {
    fail();
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    fail();
  }
  const hostname = url.hostname.toLowerCase();
  const openRouter =
    url.protocol === "https:" &&
    hostname === OPENROUTER_HOST &&
    url.pathname === OPENROUTER_ENDPOINT_PATH;
  const defaultEndpoint = url.pathname === DEFAULT_ENDPOINT_PATH;
  const endpointPathAllowed =
    hostname === OPENROUTER_HOST ? openRouter : defaultEndpoint;
  if (
    !endpointPathAllowed ||
    url.search !== "" ||
    url.hash !== "" ||
    url.username !== "" ||
    url.password !== "" ||
    (url.protocol !== "https:" &&
      !(url.protocol === "http:" && LOOPBACK_HOSTS.has(hostname)))
  ) {
    fail();
  }
  return Object.freeze({ endpoint: url.href, openRouter });
}

function validateConfig(config) {
  const value = exactRecord(config, ["endpoint", "model", "apiKey"]);
  if (
    typeof value.model !== "string" ||
    value.model.length < 1 ||
    value.model.length > 256 ||
    /[\u0000-\u001f\u007f]/.test(value.model) ||
    typeof value.apiKey !== "string" ||
    value.apiKey.length < 1 ||
    value.apiKey.length > 8192 ||
    /[\r\n]/.test(value.apiKey)
  ) {
    fail();
  }
  const endpoint = parseEndpoint(value.endpoint);
  return {
    endpoint: endpoint.endpoint,
    openRouter: endpoint.openRouter,
    model: value.model,
    credential: value.apiKey,
  };
}

function validateDiagnosticList(value) {
  if (!Array.isArray(value) || value.length > 256) {
    fail();
  }
  return value.map((item) => {
    const diagnostic = exactRecord(item, ["code", "path"]);
    if (
      typeof diagnostic.code !== "string" ||
      !/^[A-Z][A-Z0-9_]{0,127}$/.test(diagnostic.code) ||
      typeof diagnostic.path !== "string" ||
      diagnostic.path.length > 1024 ||
      (diagnostic.path !== "" && !diagnostic.path.startsWith("/"))
    ) {
      fail();
    }
    return Object.freeze({ code: diagnostic.code, path: diagnostic.path });
  });
}

function validateRequest(value) {
  const request = exactRecord(
    value,
    ["kind", "prompt", "previousCandidate", "diagnostics"],
    ["kind"],
  );
  if (request.kind === "initial") {
    if (
      Reflect.ownKeys(request).some((key) => key !== "kind" && key !== "prompt") ||
      typeof request.prompt !== "string" ||
      request.prompt.trim().length === 0 ||
      new TextEncoder().encode(request.prompt).byteLength > PROMPT_MAX_BYTES
    ) {
      fail();
    }
    return Object.freeze({ kind: "initial", prompt: request.prompt });
  }
  if (request.kind === "repair") {
    if (
      Reflect.ownKeys(request).some(
        (key) => !["kind", "previousCandidate", "diagnostics"].includes(key),
      ) ||
      typeof request.previousCandidate !== "string" ||
      new TextEncoder().encode(request.previousCandidate).byteLength > RESPONSE_MAX_BYTES
    ) {
      fail();
    }
    return Object.freeze({
      kind: "repair",
      previousCandidate: request.previousCandidate,
      diagnostics: Object.freeze(validateDiagnosticList(request.diagnostics)),
    });
  }
  fail();
}

function requestMessages(request) {
  const system = {
    role: "system",
    content:
      "Return exactly one JSON value matching the supplied strict Generation Proposal schema. Do not use markdown or commentary.",
  };
  if (request.kind === "initial") {
    return [
      system,
      {
        role: "user",
        content: JSON.stringify({ requestKind: "initial", prompt: request.prompt }),
      },
    ];
  }
  return [
    system,
    {
      role: "user",
      content: JSON.stringify({
        requestKind: "repair",
        previousCandidate: request.previousCandidate,
        diagnostics: request.diagnostics,
        schema: GENERATION_PROPOSAL_SCHEMA,
      }),
    },
  ];
}

function requestBody(model, request, openRouter) {
  const body = {
    model,
    stream: false,
    messages: requestMessages(request),
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "matrix_oasis_generation_proposal",
        strict: true,
        schema: GENERATION_PROPOSAL_PROVIDER_SCHEMA,
      },
    },
  };
  if (openRouter) {
    body.provider = { require_parameters: true };
  }
  return body;
}

async function readBoundedBody(response) {
  const declaredLength = response.headers.get("content-length");
  if (
    declaredLength !== null &&
    (!/^\d+$/.test(declaredLength) || Number(declaredLength) > RESPONSE_MAX_BYTES)
  ) {
    fail();
  }
  const reader = response.body?.getReader();
  if (!reader) {
    fail();
  }
  const chunks = [];
  let total = 0;
  while (true) {
    const result = await reader.read();
    if (result.done) {
      break;
    }
    if (!(result.value instanceof Uint8Array)) {
      fail();
    }
    total += result.value.byteLength;
    if (total > RESPONSE_MAX_BYTES) {
      try {
        await reader.cancel();
      } catch {
        // Cancellation is best effort; the public failure remains static.
      }
      fail();
    }
    chunks.push(result.value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    fail();
  }
}

function usageFrom(value) {
  if (value === undefined) {
    return null;
  }
  const usage = selectedRecord(value, ["prompt_tokens", "completion_tokens", "total_tokens"]);
  for (const key of ["prompt_tokens", "completion_tokens", "total_tokens"]) {
    if (!Number.isSafeInteger(usage[key]) || usage[key] < 0) {
      fail();
    }
  }
  return Object.freeze({
    promptTokens: usage.prompt_tokens,
    completionTokens: usage.completion_tokens,
    totalTokens: usage.total_tokens,
  });
}

function candidateFromEnvelope(value, model) {
  const envelope = selectedRecord(
    value,
    ["choices", "usage"],
    ["choices"],
  );
  if (!Array.isArray(envelope.choices) || envelope.choices.length !== 1) {
    fail();
  }
  const choice = selectedRecord(
    envelope.choices[0],
    ["message", "finish_reason"],
    ["message", "finish_reason"],
  );
  const message = selectedRecord(choice.message, ["content"], ["content"]);
  if (choice.finish_reason !== "stop" || typeof message.content !== "string") {
    fail();
  }
  return Object.freeze({
    candidateText: message.content,
    model,
    usage: usageFrom(envelope.usage),
  });
}

async function requestProposalInternal(provider, requestValue) {
  const state = PROVIDER_STATE.get(provider);
  if (!state) {
    fail();
  }
  const request = validateRequest(requestValue);
  let response;
  try {
    response = await state.fetchImplementation(state.endpoint, {
      method: "POST",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${state.credential}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(requestBody(state.model, request, state.openRouter)),
      redirect: "error",
      signal: state.timeoutSignal(state.timeoutMs),
    });
  } catch {
    fail();
  }
  if (!response || typeof response !== "object" || response.ok !== true) {
    fail();
  }
  const contentType = response.headers?.get?.("content-type");
  if (typeof contentType !== "string" || !/^application\/json(?:\s*;|$)/i.test(contentType)) {
    fail();
  }
  const text = await readBoundedBody(response);
  let envelope;
  try {
    envelope = JSON.parse(text);
  } catch {
    fail();
  }
  return candidateFromEnvelope(envelope, state.model);
}

async function requestProposal(provider, requestValue) {
  try {
    return await requestProposalInternal(provider, requestValue);
  } catch (error) {
    if (error instanceof PrototypeGeneratorOperationalError) {
      throw error;
    }
    fail();
  }
}

export function createOpenAICompatibleProviderWithSeams(config, seams) {
  const validated = validateConfig(config);
  const seamValues = exactRecord(
    seams,
    ["fetchImplementation", "timeoutSignal", "timeoutMs"],
  );
  if (
    typeof seamValues.fetchImplementation !== "function" ||
    typeof seamValues.timeoutSignal !== "function" ||
    !Number.isSafeInteger(seamValues.timeoutMs) ||
    seamValues.timeoutMs < 1 ||
    seamValues.timeoutMs > REQUEST_TIMEOUT_MS
  ) {
    fail();
  }
  const provider = Object.freeze({
    kind: "openai-compatible",
    model: validated.model,
    requestProposal(request) {
      return requestProposal(provider, request);
    },
  });
  PROVIDER_STATE.set(
    provider,
    Object.freeze({
      ...validated,
      fetchImplementation: seamValues.fetchImplementation,
      timeoutSignal: seamValues.timeoutSignal,
      timeoutMs: seamValues.timeoutMs,
    }),
  );
  return provider;
}

export function createOpenAICompatibleProvider(config) {
  return createOpenAICompatibleProviderWithSeams(config, {
    fetchImplementation: globalThis.fetch,
    timeoutSignal: (milliseconds) => AbortSignal.timeout(milliseconds),
    timeoutMs: REQUEST_TIMEOUT_MS,
  });
}

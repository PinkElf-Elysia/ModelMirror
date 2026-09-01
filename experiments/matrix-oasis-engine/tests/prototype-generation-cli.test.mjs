import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  symlink,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  createOpenAICompatibleProvider,
  generatePrototype,
  PrototypeGeneratorOperationalError,
} from "@matrix-oasis/prototype-generator";
import { GENERATION_PROPOSAL_SCHEMA } from "@matrix-oasis/prototype-generation-contracts";
import { createOpenAICompatibleProviderWithSeams } from "../packages/prototype-generator/src/openai-compatible.mjs";
import {
  executeGeneratePrototypeCli,
  executePlanPrototypeCli,
  parseGeneratePrototypeArgs,
  parsePlanPrototypeArgs,
} from "../scripts/lib/prototype-cli-core.mjs";

const LOOPBACK_HOST = "127.0.0.1";
const API_PATH = "/v1/chat/completions";
const TEMP_ROOT = path.resolve(path.parse(process.cwd()).root, "tmp");
let outputSequence = 0;

function nextOutputPath(label = "output") {
  outputSequence += 1;
  return path.join(
    TEMP_ROOT,
    `matrix-oasis-r8-${label}-${process.pid}-${Date.now()}-${outputSequence}`,
  );
}

async function makeFixtureRoot() {
  return mkdtemp(path.join(TEMP_ROOT, "matrix-oasis-r8-cli-fixture-"));
}

async function frozenProposalText() {
  const authoringGamePack = JSON.parse(
    await readFile(
      new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
      "utf8",
    ),
  );
  const nodeBindings = authoringGamePack.nodes.map((node) => ({
    nodeId: node.id,
    zoneId: "zone-main",
    visiblePlacementIds: ["placement-environment"],
  }));
  return JSON.stringify({
    format: "matrix-oasis.prototype-generation-proposal",
    formatVersion: "0.1.0",
    authoringGamePack,
    sceneBlueprint: {
      format: "matrix-oasis.scene-blueprint",
      formatVersion: "0.1.0",
      scene: {
        id: authoringGamePack.id,
        contentVersion: authoringGamePack.contentVersion,
        title: "Neutral generated scene",
        environmentPrompt: "A bounded neutral room with a floor and solid walls.",
        visualStylePrompt: "Low-complexity industrial geometric prototype.",
      },
      zones: [
        { id: "zone-main", label: "Main zone", description: "The bounded prototype room." },
      ],
      assetBriefs: [
        {
          id: "asset-environment",
          kind: "environment",
          prompt: "A bounded room with a floor and solid walls.",
          entityId: null,
          roles: ["visual", "collider"],
        },
      ],
      placements: [
        {
          id: "placement-environment",
          assetBriefId: "asset-environment",
          zoneId: "zone-main",
          entityId: null,
        },
      ],
      nodeBindings,
    },
  });
}

function fakeProviderFor(candidateText) {
  return Object.freeze({
    kind: "fake",
    model: "fake-neutral-model",
    async requestProposal() {
      return Object.freeze({ candidateText, model: "fake-neutral-model", usage: null });
    },
  });
}

function cliEnvironment(endpoint = "https://model.example.invalid/v1/chat/completions") {
  return {
    MATRIX_OASIS_MODEL_ENDPOINT: endpoint,
    MATRIX_OASIS_MODEL_ID: "neutral-model",
    MATRIX_OASIS_MODEL_API_KEY: ["loopback", "placeholder", "value"].join("-"),
  };
}

function cliServices(overrides = {}) {
  return {
    readFile,
    openFile: open,
    mkdtemp,
    rename,
    rm,
    realpath,
    lstat,
    createOpenAICompatibleProvider,
    generatePrototype,
    ...overrides,
  };
}

async function runChild(script, args, environment) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, ...args], {
      cwd: process.cwd(),
      env: environment,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", reject);
    child.once("close", (exitCode) => resolve({ exitCode, stdout, stderr }));
  });
}

function responseEnvelope(candidate = '{"candidate":true}') {
  return {
    id: "response-id",
    object: "chat.completion",
    created: 1,
    service_tier: "default",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: candidate },
        finish_reason: "stop",
        logprobs: null,
      },
    ],
    usage: {
      prompt_tokens: 10,
      completion_tokens: 20,
      total_tokens: 30,
      prompt_tokens_details: { cached_tokens: 0 },
    },
  };
}

async function readRequest(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function withServer(handler, callback) {
  const server = createServer(handler);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, LOOPBACK_HOST, resolve);
  });
  const address = server.address();
  assert.equal(typeof address, "object");
  const endpoint = `http://${LOOPBACK_HOST}:${address.port}${API_PATH}`;
  try {
    return await callback(endpoint, server);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
}

function sendJson(response, status, value, headers = {}) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    ...headers,
  });
  response.end(body);
}

function provider(endpoint, credential = "loopback-placeholder-value") {
  return createOpenAICompatibleProvider({
    endpoint,
    model: "neutral-model",
    apiKey: credential,
  });
}

function schemaHasKeyword(value, keyword) {
  if (Array.isArray(value)) {
    return value.some((item) => schemaHasKeyword(item, keyword));
  }
  if (value && typeof value === "object") {
    return Object.entries(value).some(
      ([key, item]) => key === keyword || schemaHasKeyword(item, keyword),
    );
  }
  return false;
}

function schemaKeywordCount(value, keyword) {
  if (Array.isArray(value)) {
    return value.reduce((count, item) => count + schemaKeywordCount(item, keyword), 0);
  }
  if (value && typeof value === "object") {
    return Object.entries(value).reduce(
      (count, [key, item]) =>
        count + (key === keyword ? 1 : 0) + schemaKeywordCount(item, keyword),
      0,
    );
  }
  return 0;
}

function objectSchemasWithIncompleteRequired(value, paths = [], pathValue = "") {
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      objectSchemasWithIncompleteRequired(value[index], paths, `${pathValue}/${index}`);
    }
    return paths;
  }
  if (!value || typeof value !== "object") {
    return paths;
  }
  if (value.type === "object" && value.properties) {
    const propertyKeys = Object.keys(value.properties);
    if (
      !Array.isArray(value.required) ||
      !propertyKeys.every((key) => value.required.includes(key)) ||
      value.required.length !== propertyKeys.length
    ) {
      paths.push(pathValue || "/");
    }
  }
  for (const [key, item] of Object.entries(value)) {
    objectSchemasWithIncompleteRequired(item, paths, `${pathValue}/${key}`);
  }
  return paths;
}

function collectSchemaRefs(value, refs = []) {
  if (Array.isArray(value)) {
    for (const item of value) {
      collectSchemaRefs(item, refs);
    }
    return refs;
  }
  if (!value || typeof value !== "object") {
    return refs;
  }
  for (const [key, item] of Object.entries(value)) {
    if (key === "$ref" && typeof item === "string") {
      refs.push(item);
    } else {
      collectSchemaRefs(item, refs);
    }
  }
  return refs;
}

function assertOperational(error, forbidden = []) {
  assert.equal(error instanceof PrototypeGeneratorOperationalError, true);
  assert.equal(error.name, "PrototypeGeneratorOperationalError");
  assert.equal(error.code, "PROTOTYPE_GENERATOR_INTERNAL_ERROR");
  assert.equal(error.message, "PROTOTYPE_GENERATOR_INTERNAL_ERROR");
  assert.equal("cause" in error, false);
  for (const value of forbidden) {
    assert.equal(String(error).includes(value), false);
    assert.equal(JSON.stringify(error).includes(value), false);
  }
  return true;
}

test("public provider sends one strict non-streaming JSON Schema request", async () => {
  const requests = [];
  await withServer(async (request, response) => {
    requests.push({
      method: request.method,
      url: request.url,
      authorization: request.headers.authorization,
      body: JSON.parse(await readRequest(request)),
    });
    sendJson(response, 200, responseEnvelope());
  }, async (endpoint) => {
    const apiKey = ["loopback", "credential", "value"].join("-");
    const instance = provider(endpoint, apiKey);
    assert.deepEqual(Reflect.ownKeys(instance), ["kind", "model", "requestProposal"]);
    assert.equal(Object.isFrozen(instance), true);
    assert.equal(JSON.stringify(instance).includes(apiKey), false);
    const result = await instance.requestProposal({
      kind: "initial",
      prompt: "Create a neutral room with one console and one ending.",
    });
    assert.deepEqual(result, {
      candidateText: '{"candidate":true}',
      model: "neutral-model",
      usage: { promptTokens: 10, completionTokens: 20, totalTokens: 30 },
    });
    assert.equal(Object.isFrozen(result), true);
    assert.equal(Object.isFrozen(result.usage), true);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].method, "POST");
    assert.equal(requests[0].url, API_PATH);
    assert.equal(requests[0].authorization, `Bearer ${apiKey}`);
    assert.equal(requests[0].body.model, "neutral-model");
    assert.equal(requests[0].body.stream, false);
    assert.equal("tools" in requests[0].body, false);
    assert.equal("functions" in requests[0].body, false);
    assert.equal(requests[0].body.response_format.type, "json_schema");
    assert.equal(requests[0].body.response_format.json_schema.strict, true);
    assert.equal("provider" in requests[0].body, false);
    assert.equal(
      schemaHasKeyword(requests[0].body.response_format.json_schema.schema, "not"),
      false,
    );
    assert.equal(
      schemaHasKeyword(requests[0].body.response_format.json_schema.schema, "uniqueItems"),
      false,
    );
    assert.equal(
      schemaHasKeyword(requests[0].body.response_format.json_schema.schema, "$id"),
      false,
    );
    assert.equal(
      schemaHasKeyword(requests[0].body.response_format.json_schema.schema, "oneOf"),
      false,
    );
    assert.equal(
      schemaKeywordCount(requests[0].body.response_format.json_schema.schema, "anyOf"),
      schemaKeywordCount(GENERATION_PROPOSAL_SCHEMA, "anyOf") +
        schemaKeywordCount(GENERATION_PROPOSAL_SCHEMA, "oneOf"),
    );
    assert.deepEqual(
      objectSchemasWithIncompleteRequired(
        requests[0].body.response_format.json_schema.schema,
      ),
      [],
    );
    const providerSchema = requests[0].body.response_format.json_schema.schema;
    const providerRefs = collectSchemaRefs(providerSchema);
    assert.equal(providerRefs.length > 0, true);
    assert.equal(providerRefs.every((reference) => /^#\/\$defs\/[^/]+$/.test(reference)), true);
    assert.equal(
      providerRefs.every((reference) =>
        Object.hasOwn(providerSchema.$defs, reference.slice("#/$defs/".length)),
      ),
      true,
    );
    assert.equal(
      Object.values(providerSchema.$defs).every(
        (definition) => !Object.hasOwn(definition, "$defs"),
      ),
      true,
    );
    assert.equal(objectSchemasWithIncompleteRequired(GENERATION_PROPOSAL_SCHEMA).length > 0, true);
    assert.equal(
      collectSchemaRefs(GENERATION_PROPOSAL_SCHEMA).some(
        (reference) => /^#\/\$defs\/[^/]+\/\$defs\//.test(reference),
      ),
      true,
    );
    assert.equal(
      requests[0].body.response_format.json_schema.schema.properties.format.const,
      "matrix-oasis.prototype-generation-proposal",
    );
    const userMessage = JSON.parse(requests[0].body.messages[1].content);
    assert.match(requests[0].body.messages[0].content, /scene blueprint scene id and content version must exactly equal/u);
    assert.match(requests[0].body.messages[0].content, /exactly one environment asset brief and exactly one placement/u);
    assert.match(requests[0].body.messages[0].content, /single environment placement id in every node binding visiblePlacementIds list/u);
    assert.match(requests[0].body.messages[0].content, /every node binding must keep the environment placement visible/u);
    assert.match(requests[0].body.messages[0].content, /Every authoring node must be reachable from the entry node/u);
    assert.match(requests[0].body.messages[0].content, /must have a directed path to at least one ending/u);
    assert.match(requests[0].body.messages[0].content, /executable from the initial state under the generated conditions and effects/u);
    assert.match(requests[0].body.messages[0].content, /every min\/max count and reachability boolean is a hard constraint/u);
    assert.match(requests[0].body.messages[0].content, /every prop and character-placeholder exactly one entity-bound placement/u);
    assert.match(requests[0].body.messages[0].content, /at least one node binding visiblePlacementIds list/u);
    assert.match(requests[0].body.messages[0].content, /boolean variable initialized to true and never modify it/u);
    assert.deepEqual(userMessage, {
      requestKind: "initial",
      prompt: "Create a neutral room with one console and one ending.",
    });
  });
});

test("repair request contains only candidate, static diagnostics, safe directives, and the original schema", async () => {
  let requestBody;
  await withServer(async (request, response) => {
    requestBody = JSON.parse(await readRequest(request));
    sendJson(response, 200, responseEnvelope("{}"));
  }, async (endpoint) => {
    await provider(endpoint).requestProposal({
      kind: "repair",
      previousCandidate: '{"invalid":true}',
      diagnostics: [
        { code: "PACK_NODE_UNREACHABLE", path: "/authoringGamePack/nodes/6" },
        { code: "PROTOTYPE_ACCEPTANCE_ACTIVE_DEADLOCK", path: "/authoringGamePack/nodes" },
        { code: "PROTOTYPE_ACCEPTANCE_ASSET_VISIBILITY_REQUIRED", path: "/sceneBlueprint/nodeBindings" },
      ],
    });
  });
  const repair = JSON.parse(requestBody.messages[1].content);
  assert.deepEqual(Reflect.ownKeys(repair), [
    "requestKind",
    "previousCandidate",
    "diagnostics",
    "repairDirectives",
    "schema",
  ]);
  assert.equal(repair.requestKind, "repair");
  assert.equal("prompt" in repair, false);
  assert.equal(repair.schema.properties.format.const, "matrix-oasis.prototype-generation-proposal");
  assert.equal(schemaHasKeyword(repair.schema, "not"), true);
  assert.equal(schemaHasKeyword(repair.schema, "uniqueItems"), true);
  assert.equal(schemaHasKeyword(repair.schema, "oneOf"), true);
  assert.equal(schemaHasKeyword(repair.schema, "$id"), true);
  assert.equal(objectSchemasWithIncompleteRequired(repair.schema).length > 0, true);
  assert.deepEqual(repair.diagnostics, [
    { code: "PACK_NODE_UNREACHABLE", path: "/authoringGamePack/nodes/6" },
    { code: "PROTOTYPE_ACCEPTANCE_ACTIVE_DEADLOCK", path: "/authoringGamePack/nodes" },
    { code: "PROTOTYPE_ACCEPTANCE_ASSET_VISIBILITY_REQUIRED", path: "/sceneBlueprint/nodeBindings" },
  ]);
  assert.deepEqual(repair.repairDirectives, [
    "Rebuild the directed node graph from entryNodeId so every declared node is reached by at least one action target; keep nodeBindings synchronized and do not leave decorative disconnected nodes.",
    "Remove every reachable active deadlock: keep one boolean variable initialized true and never modified, then give every non-ending node at least one coherent fallback action whose when condition compares that variable equal to true.",
    "For every non-environment asset brief, find its entity-bound placement and include that exact placement id in at least one node binding visiblePlacementIds list; keep the environment placement visible in every node binding.",
  ]);
});

test("profile generation and repair use only normalized constraints and bounded provider schema", async () => {
  const requestBodies = [];
  const acceptanceProfile = {
    format: "matrix-oasis.prototype-acceptance-profile",
    formatVersion: "0.1.0",
    nodes: { min: 7, max: 16 },
    endings: { min: 3, max: 3 },
    actions: { min: 15, max: 1024 },
    zones: { min: 2, max: 4 },
    props: { min: 3, max: 3 },
    characterPlaceholders: { min: 3, max: 3 },
    requireReachableCycle: true,
    requireAllEndingsReachable: true,
    requireAllNonEnvironmentBriefsBound: true,
  };
  await withServer(async (request, response) => {
    requestBodies.push(JSON.parse(await readRequest(request)));
    sendJson(response, 200, responseEnvelope("{}"));
  }, async (endpoint) => {
    await provider(endpoint).requestProposal({
      kind: "initial",
      prompt: "Create a connected neutral prototype",
      acceptanceProfile,
    });
    await provider(endpoint).requestProposal({
      kind: "repair",
      previousCandidate: '{"invalid":true}',
      diagnostics: [
        { code: "PROTOTYPE_ACCEPTANCE_ACTION_COUNT", path: "/authoringGamePack/nodes" },
        { code: "PROTOTYPE_ACCEPTANCE_REACHABLE_CYCLE_REQUIRED", path: "/authoringGamePack/nodes" },
        { code: "PROTOTYPE_ACCEPTANCE_STATE_SPACE_LIMIT", path: "/authoringGamePack" },
      ],
      acceptanceProfile,
    });
  });
  const initialBody = requestBodies[0];
  const initial = JSON.parse(initialBody.messages[1].content);
  assert.match(initialBody.messages[0].content, /environmentPrompt must be at most 320 characters/u);
  assert.match(initialBody.messages[0].content, /visualStylePrompt at most 120 characters/u);
  assert.deepEqual(initial, {
    requestKind: "initial",
    prompt: "Create a connected neutral prototype",
    acceptanceProfile,
  });
  const boundedScene = initialBody.response_format.json_schema.schema.$defs.sceneBlueprint__scene;
  assert.equal(boundedScene.properties.environmentPrompt.maxLength, 320);
  assert.equal(boundedScene.properties.visualStylePrompt.maxLength, 120);
  const requestBody = requestBodies[1];
  const repair = JSON.parse(requestBody.messages[1].content);
  assert.match(requestBody.messages[0].content, /environmentPrompt must be at most 320 characters/u);
  assert.match(requestBody.messages[0].content, /visualStylePrompt at most 120 characters/u);
  assert.match(requestBody.messages[0].content, /cycle semantically finite or state-stable/u);
  assert.deepEqual(repair.acceptanceProfile, acceptanceProfile);
  assert.deepEqual(repair.repairDirectives, [
    "Adjust the total declared action count to the acceptanceProfile.actions range without adding unreachable nodes or unavailable-only states.",
    "Add an executable node-target back edge reachable from the entry while preserving executable paths to every ending.",
    "Make every reachable cycle semantically finite or state-stable: remove unbounded integer additions and enum rotations from back edges, preferably using a cycle action with no effects while preserving executable paths to every ending.",
  ]);
  assert.equal("prompt" in repair, false);
  assert.deepEqual(Reflect.ownKeys(repair), [
    "requestKind", "previousCandidate", "diagnostics", "repairDirectives", "acceptanceProfile", "schema",
  ]);
});

test("endpoint gate permits standard HTTPS and exact OpenRouter or loopback endpoints", () => {
  assert.doesNotThrow(() =>
    provider("https://model.example.invalid/v1/chat/completions"),
  );
  assert.doesNotThrow(() =>
    provider("https://openrouter.ai/api/v1/chat/completions"),
  );
  for (const endpoint of [
    "http://model.example.invalid/v1/chat/completions",
    "http://127.0.0.1/v1/models",
    "http://127.0.0.1/v1/chat/completions?x=1",
    "https://user:pass@model.example.invalid/v1/chat/completions",
    "https://model.example.invalid/api/v1/chat/completions",
    "https://evil.openrouter.ai/api/v1/chat/completions",
    "https://openrouter.ai.evil.invalid/api/v1/chat/completions",
    "https://openrouter.ai/v1/chat/completions",
    "http://openrouter.ai/api/v1/chat/completions",
    "https://openrouter.ai/api/v1/chat/completions?x=1",
  ]) {
    assert.throws(() => provider(endpoint), assertOperational);
  }
});

test("OpenRouter requests require structured-output parameter support", async () => {
  let capturedEndpoint;
  let capturedBody;
  const instance = createOpenAICompatibleProviderWithSeams(
    {
      endpoint: "https://openrouter.ai/api/v1/chat/completions",
      model: "openai/gpt-5.6-luna",
      apiKey: ["placeholder", "openrouter", "value"].join("-"),
    },
    {
      fetchImplementation: async (endpoint, options) => {
        capturedEndpoint = endpoint;
        capturedBody = JSON.parse(options.body);
        const envelope = responseEnvelope();
        envelope.choices[0].native_finish_reason = "stop";
        envelope.choices[0].message.reasoning_details = [];
        return new Response(JSON.stringify(envelope), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
      timeoutSignal: () => AbortSignal.abort(),
      timeoutMs: 1,
    },
  );
  await instance.requestProposal({ kind: "initial", prompt: "neutral" });
  assert.equal(capturedEndpoint, "https://openrouter.ai/api/v1/chat/completions");
  assert.deepEqual(capturedBody.provider, { require_parameters: true });
  assert.equal(capturedBody.model, "openai/gpt-5.6-luna");
});

test("provider never retries HTTP failures and redacts credentials and response bodies", async () => {
  let calls = 0;
  const sentinel = ["dynamic", "server", Date.now()].join("-");
  await withServer((_request, response) => {
    calls += 1;
    sendJson(response, 503, { error: sentinel });
  }, async (endpoint) => {
    const credential = ["dynamic", "credential", Date.now()].join("-");
    await assert.rejects(
      provider(endpoint, credential).requestProposal({ kind: "initial", prompt: "neutral" }),
      (error) => assertOperational(error, [sentinel, credential]),
    );
  });
  assert.equal(calls, 1);
});

test("redirects are rejected rather than followed", async () => {
  let calls = 0;
  await withServer((request, response) => {
    calls += 1;
    response.writeHead(302, { location: request.url });
    response.end();
  }, async (endpoint) => {
    await assert.rejects(
      provider(endpoint).requestProposal({ kind: "initial", prompt: "neutral" }),
      assertOperational,
    );
  });
  assert.equal(calls, 1);
});

test("provider forwards the configured timeout signal and aborts one in-flight request without a retry", { timeout: 5_000 }, async () => {
  let serverCalls = 0;
  let fetchCalls = 0;
  let fetchSignal;
  const timeoutCalls = [];
  let abortTriggered = false;
  const controller = new AbortController();
  await withServer(async (request, _response) => {
    serverCalls += 1;
    await readRequest(request);
    abortTriggered = true;
    controller.abort();
  }, async (endpoint) => {
    const credential = ["loopback", "placeholder", "value"].join("-");
    const instance = createOpenAICompatibleProviderWithSeams(
      { endpoint, model: "neutral-model", apiKey: credential },
      {
        fetchImplementation: (input, options) => {
          fetchCalls += 1;
          fetchSignal = options?.signal;
          return globalThis.fetch(input, options);
        },
        timeoutSignal: (milliseconds) => {
          timeoutCalls.push(milliseconds);
          return controller.signal;
        },
        timeoutMs: 20,
      },
    );
    await assert.rejects(
      instance.requestProposal({ kind: "initial", prompt: "neutral" }),
      assertOperational,
    );
  });
  assert.deepEqual(timeoutCalls, [20]);
  assert.equal(fetchCalls, 1);
  assert.equal(fetchSignal, controller.signal);
  assert.equal(serverCalls, 1);
  assert.equal(abortTriggered, true);
  assert.equal(controller.signal.aborted, true);
});

test("declared and streamed response bodies above one MiB are rejected", async (t) => {
  await t.test("declared length", async () => {
    await withServer((_request, response) => {
      response.writeHead(200, {
        "content-type": "application/json",
        "content-length": 1_048_577,
      });
      response.end("{}");
    }, async (endpoint) => {
      await assert.rejects(
        provider(endpoint).requestProposal({ kind: "initial", prompt: "neutral" }),
        assertOperational,
      );
    });
  });
  await t.test("streamed length", async () => {
    await withServer((_request, response) => {
      response.writeHead(200, { "content-type": "application/json" });
      response.end("x".repeat(1_048_577));
    }, async (endpoint) => {
      await assert.rejects(
        provider(endpoint).requestProposal({ kind: "initial", prompt: "neutral" }),
        assertOperational,
      );
    });
  });
});

test("malformed encoding, JSON, media type, and completion envelopes fail closed", async (t) => {
  const cases = [
    ["encoding", (_request, response) => {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(Buffer.from([0xff]));
    }],
    ["JSON", (_request, response) => {
      response.writeHead(200, { "content-type": "application/json" });
      response.end("{");
    }],
    ["media type", (_request, response) => {
      response.writeHead(200, { "content-type": "text/plain" });
      response.end("{}");
    }],
    ["choices", (_request, response) => sendJson(response, 200, { choices: [] })],
    ["finish", (_request, response) => {
      const value = responseEnvelope();
      value.choices[0].finish_reason = "length";
      sendJson(response, 200, value);
    }],
    ["content", (_request, response) => {
      const value = responseEnvelope();
      value.choices[0].message.content = [{ type: "text", text: "{}" }];
      sendJson(response, 200, value);
    }],
  ];
  for (const [name, handler] of cases) {
    await t.test(name, async () => {
      await withServer(handler, async (endpoint) => {
        await assert.rejects(
          provider(endpoint).requestProposal({ kind: "initial", prompt: "neutral" }),
          assertOperational,
        );
      });
    });
  }
});

test("request input is bounded and repair diagnostics expose only code and path", async () => {
  await withServer((_request, response) => sendJson(response, 200, responseEnvelope()), async (endpoint) => {
    const instance = provider(endpoint);
    await assert.rejects(
      instance.requestProposal({ kind: "initial", prompt: "x".repeat(32_769) }),
      assertOperational,
    );
    await assert.rejects(
      instance.requestProposal({
        kind: "repair",
        previousCandidate: "{}",
        diagnostics: [{ code: "SAFE_CODE", path: "", message: "forbidden" }],
      }),
      assertOperational,
    );
  });
});

test("hostile config descriptors and malformed fetch seams remain static", async () => {
  let getterCalls = 0;
  const config = {
    endpoint: "https://model.example.invalid/v1/chat/completions",
    model: "neutral-model",
  };
  Object.defineProperty(config, "apiKey", {
    enumerable: true,
    get() {
      getterCalls += 1;
      return "not-readable";
    },
  });
  assert.throws(() => createOpenAICompatibleProvider(config), assertOperational);
  assert.equal(getterCalls, 0);

  const sentinel = ["dynamic", "fetch", Date.now()].join("-");
  const instance = createOpenAICompatibleProviderWithSeams(
    {
      endpoint: "https://model.example.invalid/v1/chat/completions",
      model: "neutral-model",
      apiKey: ["placeholder", "credential", "value"].join("-"),
    },
    {
      fetchImplementation: async () => ({
        ok: true,
        get headers() {
          throw new Error(sentinel);
        },
      }),
      timeoutSignal: () => AbortSignal.abort(),
      timeoutMs: 1,
    },
  );
  await assert.rejects(
    instance.requestProposal({ kind: "initial", prompt: "neutral" }),
    (error) => assertOperational(error, [sentinel]),
  );
});

test("runtime source uses only native Web APIs and never reads host environment", async () => {
  const source = await readFile(
    new URL("../packages/prototype-generator/src/openai-compatible.mjs", import.meta.url),
    "utf8",
  );
  assert.equal(source.includes(["process", "env"].join(".")), false);
  assert.equal(source.includes("LLM_GATEWAY"), false);
  assert.equal(source.includes("OPENROUTER_API_KEY"), false);
  assert.equal(/from\s+["'](?:node:)?(?:http|https|net|tls|undici)["']/.test(source), false);
  assert.equal(source.includes("globalThis.fetch"), true);
  assert.equal(source.includes('redirect: "error"'), true);
  assert.equal(source.includes("const REQUEST_TIMEOUT_MS = 120_000;"), true);
  assert.equal(
    source.includes("timeoutSignal: (milliseconds) => AbortSignal.timeout(milliseconds)"),
    true,
  );
});

test("prototype CLI parsers reject missing, duplicate, unknown, and unacknowledged arguments", () => {
  const cases = [
    [() => parsePlanPrototypeArgs([]), "PROTOTYPE_PLAN_PROMPT_REQUIRED"],
    [() => parsePlanPrototypeArgs(["--unknown", "value"]), "PROTOTYPE_PLAN_ARGUMENT_INVALID"],
    [
      () => parsePlanPrototypeArgs(["--prompt-file", "one", "--prompt-file", "two"]),
      "PROTOTYPE_PLAN_ARGUMENT_INVALID",
    ],
    [
      () => parseGeneratePrototypeArgs(["--prompt-file", "one", "--output", "two"]),
      "PROTOTYPE_GENERATE_UPLOAD_ACK_REQUIRED",
    ],
    [
      () =>
        parseGeneratePrototypeArgs([
          "--prompt-file",
          "one",
          "--output",
          "two",
          "--acknowledge-external-upload",
          "--acknowledge-external-upload",
        ]),
      "PROTOTYPE_GENERATE_ARGUMENT_INVALID",
    ],
    [
      () =>
        parseGeneratePrototypeArgs([
          "--prompt-file",
          `bad${String.fromCodePoint(0)}path`,
          "--output",
          "two",
          "--acknowledge-external-upload",
        ]),
      "PROTOTYPE_GENERATE_PROMPT_INVALID",
    ],
  ];
  for (const [operation, code] of cases) {
    assert.throws(operation, (error) => error.code === code && error.message === code);
  }
});

test("call plan reads only a bounded fatal UTF-8 prompt and reveals no prompt or credential", async () => {
  const root = await makeFixtureRoot();
  try {
    const accepted = path.join(root, "accepted.txt");
    const oversized = path.join(root, "oversized.txt");
    const invalidUtf8 = path.join(root, "invalid.txt");
    await writeFile(accepted, "x".repeat(32_768));
    await writeFile(oversized, "x".repeat(32_769));
    await writeFile(invalidUtf8, Uint8Array.from([0xff]));
    const environment = cliEnvironment();
    const acceptedResult = await executePlanPrototypeCli({
      args: ["--prompt-file", accepted],
      tempRoot: TEMP_ROOT,
      environment,
      readFile,
      realpath,
      lstat,
    });
    assert.equal(acceptedResult.exitCode, 0);
    assert.match(acceptedResult.stdout, /maxRequests=3 promptBytes=32768 uploadsPrompt=true/);
    assert.equal(acceptedResult.stdout.includes("x".repeat(64)), false);
    assert.equal(
      acceptedResult.stdout.includes(environment.MATRIX_OASIS_MODEL_API_KEY),
      false,
    );
    for (const candidate of [oversized, invalidUtf8]) {
      const result = await executePlanPrototypeCli({
        args: ["--prompt-file", candidate],
        tempRoot: TEMP_ROOT,
        environment,
        readFile,
        realpath,
        lstat,
      });
      assert.equal(result.exitCode, 2);
      assert.equal(result.stdout, "");
      assert.match(result.stderr, /^PROTOTYPE_[A-Z0-9_]+\n$/);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("prompt containment rejects paths outside the trusted root and directory junction traversal", async () => {
  const root = await makeFixtureRoot();
  const target = await makeFixtureRoot();
  const junction = path.join(root, "linked");
  try {
    const targetPrompt = path.join(target, "prompt.txt");
    await writeFile(targetPrompt, "neutral prompt");
    await symlink(target, junction, "junction");
    for (const candidate of [
      path.resolve(path.parse(TEMP_ROOT).root, "Windows", "win.ini"),
      path.join(junction, "prompt.txt"),
    ]) {
      const result = await executePlanPrototypeCli({
        args: ["--prompt-file", candidate],
        tempRoot: TEMP_ROOT,
        environment: cliEnvironment(),
        readFile,
        realpath,
        lstat,
      });
      assert.equal(result.exitCode, 2);
      assert.equal(result.stdout, "");
      assert.match(result.stderr, /^PROTOTYPE_[A-Z0-9_]+\n$/);
    }
  } finally {
    await unlink(junction).catch(() => {});
    await rm(root, { recursive: true, force: true });
    await rm(target, { recursive: true, force: true });
  }
});

test("prompt identity swaps and environment accessors fail closed without invoking getters", async () => {
  const root = await makeFixtureRoot();
  try {
    const promptFile = path.join(root, "prompt.txt");
    const movedFile = path.join(root, "moved.txt");
    await writeFile(promptFile, "neutral prompt");
    const swapped = await executePlanPrototypeCli({
      args: ["--prompt-file", promptFile],
      tempRoot: TEMP_ROOT,
      environment: cliEnvironment(),
      readFile: async (candidate) => {
        const bytes = await readFile(candidate);
        await rename(candidate, movedFile);
        await writeFile(candidate, bytes);
        return bytes;
      },
      realpath,
      lstat,
    });
    assert.equal(swapped.exitCode, 2);
    assert.equal(swapped.stderr, "PROTOTYPE_PROMPT_READ_ERROR\n");

    let getterCalls = 0;
    const environment = cliEnvironment();
    Object.defineProperty(environment, "MATRIX_OASIS_MODEL_ENDPOINT", {
      enumerable: true,
      get() {
        getterCalls += 1;
        return "https://model.example.invalid/v1/chat/completions";
      },
    });
    const accessor = await executePlanPrototypeCli({
      args: ["--prompt-file", promptFile],
      tempRoot: TEMP_ROOT,
      environment,
      readFile,
      realpath,
      lstat,
    });
    assert.equal(accessor.exitCode, 2);
    assert.equal(accessor.stderr, "PROTOTYPE_MODEL_CONFIG_INVALID\n");
    assert.equal(getterCalls, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("generation publishes exactly five canonical files in one new output directory", async () => {
  const root = await makeFixtureRoot();
  const output = nextOutputPath("success");
  try {
    const promptFile = path.join(root, "prompt.txt");
    const candidate = await frozenProposalText();
    await writeFile(promptFile, "Build a neutral bounded prototype with basic interactions.");
    const result = await executeGeneratePrototypeCli({
      args: [
        "--prompt-file",
        promptFile,
        "--output",
        output,
        "--acknowledge-external-upload",
      ],
      tempRoot: TEMP_ROOT,
      environment: cliEnvironment(),
      ...cliServices({
        createOpenAICompatibleProvider: () => fakeProviderFor(candidate),
      }),
    });
    assert.deepEqual(result, {
      exitCode: 0,
      stdout: "PROTOTYPE_GENERATION_OK requests=1\n",
      stderr: "",
    });
    assert.deepEqual((await readdir(output)).sort(), [
      "authoring-game-pack.json",
      "generation-report.json",
      "runtime-game-pack.json",
      "runtime-receipt.json",
      "scene-blueprint.json",
    ]);
    for (const name of await readdir(output)) {
      const bytes = await readFile(path.join(output, name));
      assert.equal(bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf, false);
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      assert.equal(text.endsWith("\n"), false);
      assert.doesNotThrow(() => JSON.parse(text));
    }
  } finally {
    await rm(output, { recursive: true, force: true });
    await rm(root, { recursive: true, force: true });
  }
});

test("content failure and publication faults leave no candidate output", async () => {
  const root = await makeFixtureRoot();
  const rejectedOutput = nextOutputPath("rejected");
  const failedOutput = nextOutputPath("fault");
  try {
    const promptFile = path.join(root, "prompt.txt");
    await writeFile(promptFile, "neutral prompt");
    const base = {
      args: [
        "--prompt-file",
        promptFile,
        "--output",
        rejectedOutput,
        "--acknowledge-external-upload",
      ],
      tempRoot: TEMP_ROOT,
      environment: cliEnvironment(),
      ...cliServices({
        createOpenAICompatibleProvider: () => Object.freeze({}),
        generatePrototype: async () => ({
          ok: false,
          diagnostics: [
            {
              phase: "schema",
              severity: "error",
              code: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED",
              path: "/sceneBlueprint",
              message: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED",
            },
          ],
        }),
      }),
    };
    const rejected = await executeGeneratePrototypeCli(base);
    assert.deepEqual(rejected, {
      exitCode: 1,
      stdout: "",
      stderr: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED /sceneBlueprint\n",
    });
    await assert.rejects(lstat(rejectedOutput), { code: "ENOENT" });

    let opens = 0;
    const faulted = await executeGeneratePrototypeCli({
      ...base,
      args: base.args.map((value) => (value === rejectedOutput ? failedOutput : value)),
      generatePrototype: async (_request, _provider) =>
        generatePrototype({ prompt: "neutral prompt" }, fakeProviderFor(await frozenProposalText())),
      openFile: async (...arguments_) => {
        opens += 1;
        if (opens === 2) {
          throw new Error(["dynamic", "file", Date.now()].join("-"));
        }
        return open(...arguments_);
      },
    });
    assert.equal(faulted.exitCode, 2);
    assert.equal(faulted.stderr, "PROTOTYPE_GENERATE_IO_ERROR\n");
    await assert.rejects(lstat(failedOutput), { code: "ENOENT" });
    const names = await readdir(TEMP_ROOT);
    assert.equal(names.some((name) => name.includes(path.basename(failedOutput))), false);
  } finally {
    await rm(rejectedOutput, { recursive: true, force: true });
    await rm(failedOutput, { recursive: true, force: true });
    await rm(root, { recursive: true, force: true });
  }
});

test("an existing output is preserved and concurrent same-name publication has one winner", async () => {
  const root = await makeFixtureRoot();
  const existing = nextOutputPath("existing");
  const concurrent = nextOutputPath("concurrent");
  try {
    const promptFile = path.join(root, "prompt.txt");
    await writeFile(promptFile, "neutral prompt");
    await mkdir(existing);
    await writeFile(path.join(existing, "sentinel.txt"), "preserve-me");
    const candidate = await frozenProposalText();
    const execute = (output) =>
      executeGeneratePrototypeCli({
        args: [
          "--prompt-file",
          promptFile,
          "--output",
          output,
          "--acknowledge-external-upload",
        ],
        tempRoot: TEMP_ROOT,
        environment: cliEnvironment(),
        ...cliServices({ createOpenAICompatibleProvider: () => fakeProviderFor(candidate) }),
      });
    const preserved = await execute(existing);
    assert.equal(preserved.exitCode, 2);
    assert.equal(await readFile(path.join(existing, "sentinel.txt"), "utf8"), "preserve-me");

    const results = await Promise.all([execute(concurrent), execute(concurrent)]);
    assert.deepEqual(results.map((item) => item.exitCode).sort(), [0, 2]);
    assert.equal((await readdir(concurrent)).length, 5);
    const reportText = await readFile(path.join(concurrent, "generation-report.json"), "utf8");
    assert.doesNotThrow(() => JSON.parse(reportText));
  } finally {
    await rm(existing, { recursive: true, force: true });
    await rm(concurrent, { recursive: true, force: true });
    await rm(root, { recursive: true, force: true });
  }
});

test("output paths outside C tmp, reserved names, and existing junctions are rejected", async () => {
  const root = await makeFixtureRoot();
  const junction = nextOutputPath("junction");
  try {
    const promptFile = path.join(root, "prompt.txt");
    await writeFile(promptFile, "neutral prompt");
    await symlink(root, junction, "junction");
    const candidate = await frozenProposalText();
    const outputs = [
      path.resolve(path.parse(TEMP_ROOT).root, "matrix-oasis-r8-outside"),
      path.join(TEMP_ROOT, "con"),
      junction,
    ];
    for (const output of outputs) {
      const result = await executeGeneratePrototypeCli({
        args: [
          "--prompt-file",
          promptFile,
          "--output",
          output,
          "--acknowledge-external-upload",
        ],
        tempRoot: TEMP_ROOT,
        environment: cliEnvironment(),
        ...cliServices({ createOpenAICompatibleProvider: () => fakeProviderFor(candidate) }),
      });
      assert.equal(result.exitCode, 2);
      assert.equal(result.stdout, "");
      assert.match(result.stderr, /^PROTOTYPE_GENERATE_[A-Z0-9_]+\n$/);
    }
    assert.equal((await lstat(junction)).isSymbolicLink(), true);
  } finally {
    await unlink(junction).catch(() => {});
    await rm(root, { recursive: true, force: true });
  }
});

test("real generate CLI completes through one loopback OpenAI-compatible request", async () => {
  const root = await makeFixtureRoot();
  const output = nextOutputPath("loopback");
  const candidate = await frozenProposalText();
  let requests = 0;
  try {
    const promptFile = path.join(root, "prompt.txt");
    await writeFile(promptFile, "Build a neutral room with one basic interaction.");
    await withServer(async (request, response) => {
      requests += 1;
      const body = JSON.parse(await readRequest(request));
      assert.equal(body.response_format.json_schema.strict, true);
      sendJson(response, 200, responseEnvelope(candidate));
    }, async (endpoint) => {
      const script = path.resolve("scripts/generate-prototype.mjs");
      const result = await runChild(
        script,
        [
          "--prompt-file",
          promptFile,
          "--output",
          output,
          "--acknowledge-external-upload",
        ],
        cliEnvironment(endpoint),
      );
      assert.deepEqual(result, {
        exitCode: 0,
        stdout: "PROTOTYPE_GENERATION_OK requests=1\n",
        stderr: "",
      });
    });
    assert.equal(requests, 1);
    assert.equal((await readdir(output)).length, 5);
  } finally {
    await rm(output, { recursive: true, force: true });
    await rm(root, { recursive: true, force: true });
  }
});

import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  createOpenAICompatibleProvider,
  PrototypeGeneratorOperationalError,
} from "@matrix-oasis/prototype-generator";
import { createOpenAICompatibleProviderWithSeams } from "../packages/prototype-generator/src/openai-compatible.mjs";

const LOOPBACK_HOST = "127.0.0.1";
const API_PATH = "/v1/chat/completions";

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
    assert.equal(
      requests[0].body.response_format.json_schema.schema.properties.format.const,
      "matrix-oasis.prototype-generation-proposal",
    );
    const userMessage = JSON.parse(requests[0].body.messages[1].content);
    assert.deepEqual(userMessage, {
      requestKind: "initial",
      prompt: "Create a neutral room with one console and one ending.",
    });
  });
});

test("repair request contains only candidate, static diagnostics, and the original schema", async () => {
  let requestBody;
  await withServer(async (request, response) => {
    requestBody = JSON.parse(await readRequest(request));
    sendJson(response, 200, responseEnvelope("{}"));
  }, async (endpoint) => {
    await provider(endpoint).requestProposal({
      kind: "repair",
      previousCandidate: '{"invalid":true}',
      diagnostics: [
        { code: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED", path: "/sceneBlueprint" },
      ],
    });
  });
  const repair = JSON.parse(requestBody.messages[1].content);
  assert.deepEqual(Reflect.ownKeys(repair), [
    "requestKind",
    "previousCandidate",
    "diagnostics",
    "schema",
  ]);
  assert.equal(repair.requestKind, "repair");
  assert.equal("prompt" in repair, false);
  assert.equal(repair.schema.properties.format.const, "matrix-oasis.prototype-generation-proposal");
  assert.deepEqual(repair.diagnostics, [
    { code: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED", path: "/sceneBlueprint" },
  ]);
});

test("endpoint gate permits HTTPS and loopback HTTP only at the exact path", () => {
  assert.doesNotThrow(() =>
    provider("https://model.example.invalid/v1/chat/completions"),
  );
  for (const endpoint of [
    "http://model.example.invalid/v1/chat/completions",
    "http://127.0.0.1/v1/models",
    "http://127.0.0.1/v1/chat/completions?x=1",
    "https://user:pass@model.example.invalid/v1/chat/completions",
  ]) {
    assert.throws(() => provider(endpoint), assertOperational);
  }
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

test("timeout aborts one request without a retry", async () => {
  let calls = 0;
  await withServer((_request, _response) => {
    calls += 1;
  }, async (endpoint) => {
    const credential = ["loopback", "placeholder", "value"].join("-");
    const instance = createOpenAICompatibleProviderWithSeams(
      { endpoint, model: "neutral-model", apiKey: credential },
      {
        fetchImplementation: globalThis.fetch,
        timeoutSignal: (milliseconds) => AbortSignal.timeout(milliseconds),
        timeoutMs: 20,
      },
    );
    await assert.rejects(
      instance.requestProposal({ kind: "initial", prompt: "neutral" }),
      assertOperational,
    );
  });
  assert.equal(calls, 1);
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
  assert.equal(source.includes("OPENROUTER"), false);
  assert.equal(/from\s+["'](?:node:)?(?:http|https|net|tls|undici)["']/.test(source), false);
  assert.equal(source.includes("globalThis.fetch"), true);
  assert.equal(source.includes('redirect: "error"'), true);
});

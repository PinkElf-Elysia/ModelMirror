import assert from "node:assert/strict";
import test from "node:test";
import { createModelMirrorAdapter } from "../runtime/node.mjs";

const request = (maxTokens, extra = {}) => ({ sessionId: "session.fixture", generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: 0, input: { kind: "action", text: "wait" }, messages: [{ role: "system", content: "Return JSON." }, { role: "user", content: "Wait." }], modelId: "provider/model", settings: { temperature: 0, maxTokens }, ...extra });
const openapi = { openapi: "3.1.0", paths: { "/api/chat": { post: { requestBody: { content: { "application/json": { schema: { type: "object", properties: { require_managed_route: { type: "boolean" } } } } } } } } } };
const control = { contract_version: "modelmirror-provider-chat-routing-v1", feature_enabled: true, data_plane_integrated: true, model_id: "provider/model", capability: "chat_text", effective_mode: "newapi_preferred", available: true };
const receipt = (output) => ({ requested_model: "provider/model", actual_model: "provider/model", provider: null, strategy: "newapi_preferred", engine: "newapi", reason_codes: ["qualified"], latency_ms: null, ttft_ms: null, tokens: { input: 2, output, total: output + 2 }, response_cost_usd: null, cost_kind: "unavailable", fallback_attempts: 0, cache_hit: null, request_id: null, version: "2" });
const stream = (output) => `data: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { content: "ok" }, finish_reason: "stop" }] })}\n\nevent: route_receipt\ndata: ${JSON.stringify(receipt(output))}\n\ndata: [DONE]\n\n`;
const code = (report) => report.diagnostics[0]?.code;

async function withMockFetch(config, run, output = 1) {
  const original = globalThis.fetch, posts = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = new URL(url).pathname;
    if (path === "/openapi.json") return Response.json(openapi);
    if (path === "/api/models/provider-chat-control") return Response.json(control);
    posts.push(JSON.parse(options.body));
    return new Response(stream(output), { status: 200, headers: { "content-type": "text/event-stream" } });
  };
  try {
    const report = createModelMirrorAdapter({ baseUrl: "https://trusted.invalid/", ...config });
    await run(report, posts);
  } finally { globalThis.fetch = original; }
}

test("default budget remains 512 and legacy maxOutputTokens 513 remains invalid", async () => {
  assert.equal(code(createModelMirrorAdapter({ baseUrl: "https://trusted.invalid/", maxOutputTokens: 513 })), "RUNTIME_ADAPTER_CONFIG_INVALID");
  await withMockFetch({}, async (report, posts) => {
    assert.equal(report.valid, true);
    assert.equal((await report.value.initialize()).valid, true);
    assert.equal((await report.value.generate(request(512))).valid, true);
    assert.equal(code(await report.value.generate(request(513))), "RUNTIME_ADAPTER_REQUEST_INVALID");
    assert.equal(posts.length, 1);
    assert.equal(posts[0].max_tokens, 512);
  });
});

test("trusted host can explicitly raise the effective budget to 2048", async () => {
  await withMockFetch({ trustedOutputBudget: { maxTokens: 2048 } }, async (report, posts) => {
    assert.equal(report.valid, true);
    assert.equal((await report.value.initialize()).valid, true);
    const result = await report.value.generate(request(2048));
    assert.equal(result.valid, true, code(result));
    assert.equal(posts[0].max_tokens, 2048);
  }, 2048);
});

test("trusted budget rejects over-limit values and pseudo configuration shapes", () => {
  for (const trustedOutputBudget of [{ maxTokens: 4097 }, { max_tokens: 2048 }, { maxTokens: 2048, source: "card" }, 2048, null]) {
    assert.equal(code(createModelMirrorAdapter({ baseUrl: "https://trusted.invalid/", trustedOutputBudget })), "RUNTIME_ADAPTER_CONFIG_INVALID");
  }
});

test("request fields cannot raise the host budget and receipt usage uses the same effective limit", async () => {
  await withMockFetch({ trustedOutputBudget: { maxTokens: 1024 } }, async (report, posts) => {
    assert.equal((await report.value.initialize()).valid, true);
    assert.equal(code(await report.value.generate(request(1025))), "RUNTIME_ADAPTER_REQUEST_INVALID");
    assert.equal(code(await report.value.generate(request(1024, { trustedOutputBudget: { maxTokens: 2048 } }))), "RUNTIME_ADAPTER_REQUEST_INVALID");
    const result = await report.value.generate(request(1024));
    assert.equal(code(result), "RUNTIME_ADAPTER_OUTPUT_LIMIT");
    assert.equal(result.value.usage.output, 1025);
    assert.equal(posts.length, 1);
  }, 1025);
});

test("explicitly authorized host budget permits 4096",async()=>{await withMockFetch({trustedOutputBudget:{maxTokens:4096}},async(r,posts)=>{assert.equal(r.valid,true);assert.equal((await r.value.initialize()).valid,true);assert.equal((await r.value.generate(request(4096))).valid,true);assert.equal(posts[0].max_tokens,4096);},4096);});

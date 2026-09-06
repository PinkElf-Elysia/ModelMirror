import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { createModelMirrorAdapter } from "../runtime/node.mjs";
import { readSseEvents } from "../runtime/node/sse.mjs";

const request = () => ({ sessionId: "session.fixture", generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: 0, input: { kind: "action", text: "wait" }, messages: [{ role: "system", content: "Return JSON." }, { role: "user", content: "Wait." }], modelId: "provider/model", settings: { temperature: 0, maxTokens: 512 } });
const openapi = () => ({ openapi: "3.1.0", paths: { "/api/chat": { post: { requestBody: { $ref: "#/components/requestBodies/Chat" } } } }, components: { requestBodies: { Chat: { content: { "application/json": { schema: { $ref: "#/components/schemas/ChatRequest" } } } } }, schemas: { ChatRequest: { type: "object", properties: { require_managed_route: { type: "boolean" } } } } } });
const control = (overrides = {}) => ({ contract_version: "modelmirror-provider-chat-routing-v1", feature_enabled: true, data_plane_integrated: true, model_id: "provider/model", capability: "chat_text", effective_mode: "newapi_preferred", available: true, would_block: false, reason_code: "qualified", ...overrides });
const receipt = (overrides = {}) => ({ requested_model: "provider/model", actual_model: "provider/model", provider: null, strategy: "newapi_preferred", engine: "newapi", reason_codes: ["preferred_preflight_history", "qualified"], latency_ms: null, ttft_ms: null, tokens: { input: null, output: null, total: null }, response_cost_usd: null, cost_kind: "unavailable", fallback_attempts: 0, cache_hit: null, request_id: null, version: "2", ...overrides });
const sse = ({ model = "provider/model", route = receipt(), text = "好", finish = true, done = true, duplicate = false } = {}) => `${JSON.stringify({ model, choices: [{ delta: { content: text }, finish_reason: finish ? "stop" : null }] }).replace(/^/u, "data: ")}\r\n\r\nevent: route_receipt\r\ndata: ${JSON.stringify(route)}\r\n\r\n${duplicate ? `event: route_receipt\ndata: ${JSON.stringify(route)}\n\n` : ""}${done ? "data: [DONE]\n\n" : ""}`;
const code = (report) => report.diagnostics[0]?.code;
async function serve(handler) { const server = http.createServer(handler); await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve)); const address = server.address(); return { baseUrl: `http://127.0.0.1:${address.port}/`, close: () => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); }) }; }
function json(response, value, status = 200, headers = {}) { response.writeHead(status, { "content-type": "application/json", ...headers }); response.end(JSON.stringify(value)); }
async function configured(handler, config = {}) { const host = await serve(handler), report = createModelMirrorAdapter({ baseUrl: host.baseUrl, ...config }); assert.equal(report.valid, true); return { host, adapter: report.value }; }

test("configuration and legacy OpenAPI fail closed without a chat dispatch", async () => {
  assert.equal(code(createModelMirrorAdapter({ baseUrl: "http://user:pass@localhost/" })), "RUNTIME_ADAPTER_CONFIG_INVALID"); assert.equal(code(createModelMirrorAdapter({ baseUrl: "http://localhost/?x=1" })), "RUNTIME_ADAPTER_CONFIG_INVALID"); assert.equal(code(createModelMirrorAdapter({ baseUrl: "http://localhost/", maxOutputTokens: 513 })), "RUNTIME_ADAPTER_CONFIG_INVALID");
  let posts = 0; const { host, adapter } = await configured((req, res) => { if (req.method === "POST") posts += 1; json(res, { openapi: "3.1.0", paths: {} }); }); assert.equal(code(await adapter.initialize()), "RUNTIME_ADAPTER_OPENAPI_UNSUPPORTED"); assert.equal(posts, 0); await host.close();
});

test("adapter exposes immutable evidence kind and failed reinitialize clears readiness", async () => {
  let current = openapi(); const { host, adapter } = await configured((req, res) => json(res, current)); assert.equal(adapter.evidenceKind, "real"); assert.equal((await adapter.initialize()).valid, true); current = { openapi: "3.1.0", paths: {} }; assert.equal((await adapter.initialize()).valid, false); assert.equal(code(await adapter.generate(request())), "RUNTIME_ADAPTER_NOT_INITIALIZED"); await host.close();
});

test("control qualification is exact and no unqualified request is dispatched", async () => {
  for (const bad of [{ effective_mode: "legacy" }, { model_id: "provider/other" }, { available: false }, { data_plane_integrated: false }]) { let posts = 0; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") json(res, openapi()); else if (req.method === "GET") json(res, control(bad)); else { posts += 1; res.end(); } }); assert.equal((await adapter.initialize()).valid, true); const result = await adapter.generate(request()); assert.equal(code(result), "RUNTIME_ADAPTER_CONTROL_UNQUALIFIED"); assert.equal(result.value.dispatched, false); assert.equal(posts, 0); await host.close(); }
});

test("a pre-aborted signal never reaches control or POST", async () => {
  let calls = 0; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); calls += 1; json(res, control()); }); await adapter.initialize(); const controller = new AbortController(); controller.abort(); const result = await adapter.generate(request(), { signal: controller.signal }); assert.equal(code(result), "RUNTIME_ADAPTER_CANCELLED"); assert.equal(result.value.dispatched, false); assert.equal(calls, 0); await host.close();
});

test("successful stream sends only the strict managed text shape and preserves unknown usage", async () => {
  let body, calls = 0; const input = request(), before = structuredClone(input), chunks = []; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") { calls += 1; assert.equal(req.url, "/api/models/provider-chat-control?model_id=provider%2Fmodel&capability=chat_text"); return json(res, control()); } calls += 1; let raw = ""; req.setEncoding("utf8"); req.on("data", (part) => { raw += part; }); req.on("end", () => { body = JSON.parse(raw); res.writeHead(200, { "content-type": "text/event-stream" }); const bytes = Buffer.from(sse({ text: "雪" })); res.write(bytes.subarray(0, bytes.indexOf(0xe9) + 1)); res.end(bytes.subarray(bytes.indexOf(0xe9) + 1)); }); }); assert.equal((await adapter.initialize()).valid, true); const result = await adapter.generate(input, { onText: (part) => chunks.push(part) }); assert.equal(result.valid, true); assert.equal(result.value.status, "succeeded"); assert.equal(result.value.dispatched, true); assert.equal(result.value.text, "雪"); assert.equal(result.value.observedModel, "provider/model"); assert.deepEqual(result.value.usage, { input: null, output: null, total: null }); assert.equal(calls, 2); assert.deepEqual(body, { model_id: "provider/model", messages: input.messages, temperature: 0, max_tokens: 512, gateway: "default", tool_mode: "none", compression: { mode: "off" }, output_mode: "none", require_managed_route: true }); assert.deepEqual(input, before); assert.deepEqual(chunks, ["雪"]); await host.close();
});

test("controlled preferred backup engine succeeds without treating receipt identity as observed model", async () => {
  const stream = sse({ route: receipt({ engine: "openrouter" }) }).replace('"model":"provider/model",', ""); const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); }); await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(result.valid, true); assert.equal(result.value.observedModel, null); assert.equal(result.value.serverReceipt.actual_model, "provider/model"); await host.close();
});

test("standard usage tail is accepted only when it agrees with the qualified receipt", async () => {
  for (const [total, valid] of [[5, true], [6, false]]) { const route = receipt({ tokens: { input: 3, output: 2, total: 5 } }), stream = `data: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { content: "ok" }, finish_reason: "stop" }] })}\n\ndata: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 3, completion_tokens: 2, total_tokens: total } })}\n\nevent: route_receipt\ndata: ${JSON.stringify(route)}\n\ndata: [DONE]\n\n`; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); }); await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(valid ? result.valid : code(result), valid ? true : "RUNTIME_ADAPTER_RECEIPT_INVALID"); await host.close(); }
});

test("pinned newAPI usage extensions remain non-authoritative metadata", async () => {
  const base = { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5, prompt_tokens_details: { cached_tokens: 1, cached_creation_tokens: 2, cache_write_tokens: 3, text_tokens: 4, audio_tokens: 5, image_tokens: 6 }, completion_tokens_details: { text_tokens: 7, audio_tokens: 8, image_tokens: 9, reasoning_tokens: 10 }, input_tokens: 0, output_tokens: 99, claude_cache_creation_5_m_tokens: 7, claude_cache_creation_1_h_tokens: 11 };
  for (const details of [{ input_tokens_details: null }, { input_tokens_details: { cached_tokens: 1, cached_creation_tokens: 2, cache_write_tokens: 3, text_tokens: 4, audio_tokens: 5, image_tokens: 6 } }, { prompt_tokens_details: null, completion_tokens_details: null, input_tokens_details: null }]) {
    const usage = { ...base, ...details }, tail = `data: ${JSON.stringify({ model: "provider/model", choices: [], usage })}\n\n`, stream = sse({ route: receipt({ tokens: { input: 3, output: 2, total: 5 } }) }).replace("event: route_receipt", tail + "event: route_receipt");
    const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); });
    try { await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(result.valid, true, code(result)); assert.deepEqual(result.value.usage, { input: 3, output: 2, total: 5 }); } finally { await host.close(); }
  }
});

test("newAPI usage extensions reject unknown keys, bad scalars, bad detail objects, and incomplete terminals", async () => {
  const base = { prompt_tokens: 3, completion_tokens: 2, total_tokens: 5, input_tokens: 0, output_tokens: 99, input_tokens_details: { cached_tokens: 1 }, claude_cache_creation_5_m_tokens: 7, claude_cache_creation_1_h_tokens: 11 };
  const cases = [
    [{ ...base, future_private_token: 1 }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, input_tokens: -1 }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, output_tokens: 1.5 }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, claude_cache_creation_5_m_tokens: -1 }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, claude_cache_creation_1_h_tokens: "1" }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, input_tokens_details: [] }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, input_tokens_details: { cached_tokens: { nested: 1 } } }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, input_tokens_details: { cached_tokens: 1, private_tokens: 1 } }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, prompt_tokens_details: { cached_tokens: 1, private_tokens: 1 } }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [{ ...base, completion_tokens_details: { reasoning_tokens: 1, private_tokens: 1 } }, "RUNTIME_ADAPTER_EVENT_INVALID", true],
    [base, "RUNTIME_ADAPTER_STREAM_INCOMPLETE", false],
  ];
  for (const [usage, expected, done] of cases) {
    const tail = `data: ${JSON.stringify({ model: "provider/model", choices: [], usage })}\n\n`, original = sse({ route: receipt({ tokens: { input: 3, output: 2, total: 5 } }), done }), stream = original.replace("event: route_receipt", tail + "event: route_receipt");
    const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); });
    try { await adapter.initialize(); assert.equal(code(await adapter.generate(request())), expected); } finally { await host.close(); }
  }
});

test("SSE handles CRLF split across chunks and rejects an unterminated EOF event", async () => {
  for (const incomplete of [false, true]) { const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); if (incomplete) return res.end("data: [DONE]"); const bytes = Buffer.from(sse()); const split = bytes.indexOf(Buffer.from("\r\n")) + 1; res.write(bytes.subarray(0, split)); res.end(bytes.subarray(split)); }); await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(incomplete ? code(result) : result.valid, incomplete ? "RUNTIME_ADAPTER_STREAM_INCOMPLETE" : true); await host.close(); }
});

test("success requires stop, one qualified receipt, DONE, and complete EOF", async () => {
  for (const [stream, expected] of [["data: [DONE]\n\n", "RUNTIME_ADAPTER_STREAM_INCOMPLETE"], [sse({ finish: false }), "RUNTIME_ADAPTER_RECEIPT_INVALID"], [sse({ done: false }), "RUNTIME_ADAPTER_STREAM_INCOMPLETE"], [sse({ duplicate: true }), "RUNTIME_ADAPTER_RECEIPT_DUPLICATE"], [sse({ route: receipt({ reason_codes: ["old_preflight"] }) }), "RUNTIME_ADAPTER_RECEIPT_INVALID"], [sse({ route: receipt({ actual_model: "provider/other" }) }), "RUNTIME_ADAPTER_RECEIPT_INVALID"]]) { const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); }); await adapter.initialize(); assert.equal(code(await adapter.generate(request())), expected); await host.close(); }
});

test("choice model is the only observed identity and mismatches or tool payloads fail", async () => {
  for (const [stream, expected] of [[sse({ model: "provider/other" }), "RUNTIME_ADAPTER_MODEL_MISMATCH"], [`data: ${JSON.stringify({ choices: [{ delta: { tool_calls: [] }, finish_reason: "stop" }] })}\n\nevent: route_receipt\ndata: ${JSON.stringify(receipt())}\n\ndata: [DONE]\n\n`, "RUNTIME_ADAPTER_EVENT_INVALID"]]) { const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); }); await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(code(result), expected); assert.equal(result.value.dispatched, true); await host.close(); }
});

test("callbacks, invalid UTF-8, event limits, HTTP errors and redirects fail without retry", async () => {
  const cases = [
    { reply: (res) => { res.writeHead(200, { "content-type": "text/event-stream" }); res.end(sse()); }, options: { onText: () => { throw new Error("private"); } }, expected: "RUNTIME_ADAPTER_CALLBACK_FAILED" },
    { reply: (res) => { res.writeHead(200, { "content-type": "text/event-stream" }); res.end(Buffer.from([0xff])); }, expected: "RUNTIME_ADAPTER_STREAM_FAILED" },
    { reply: (res) => { res.writeHead(200, { "content-type": "text/event-stream" }); res.end(`data: ${"x".repeat(1024 * 1024 + 1)}\n\n`); }, expected: "RUNTIME_ADAPTER_EVENT_LIMIT" },
    { reply: (res) => json(res, { error: "x" }, 500), expected: "RUNTIME_ADAPTER_HTTP_FAILED" },
    { reply: (res) => { res.writeHead(307, { location: "/api/chat-2" }); res.end(); }, expected: "RUNTIME_ADAPTER_HTTP_FAILED" },
  ];
  for (const item of cases) { let posts = 0; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); posts += 1; item.reply(res); }); await adapter.initialize(); const result = await adapter.generate(request(), item.options); assert.equal(code(result), item.expected); assert.equal(posts, 1); assert.equal(JSON.stringify(result).includes("private"), false); await host.close(); }
});

test("structural failure receipts are retained while private HTTP error bodies never escape", async () => {
  const failedReceipt = receipt({ reason_codes: ["provider_chat_http_502"], engine: "managed_chat_blocked", actual_model: null, strategy: "newapi_preferred" });
  for (const [body, retained] of [[{ detail: { route_receipt: failedReceipt, private_error: "secret-body" } }, true], [{ error: "secret-body", route_receipt: { private: "secret-receipt" } }, false]]) { const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); json(res, body, 502); }); await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(code(result), "RUNTIME_ADAPTER_HTTP_FAILED"); assert.equal(result.value.dispatched, true); assert.equal(result.value.serverReceipt === null, !retained); if (retained) assert.deepEqual(result.value.serverReceipt, failedReceipt); assert.equal(JSON.stringify(result).includes("secret-body"), false); assert.equal(JSON.stringify(result).includes("secret-receipt"), false); await host.close(); }
});

test("hanging callback is interrupted by timeout and invalid content-presence fields are rejected", async () => {
  for (const [reply, options, expected] of [[sse(), { onText: () => new Promise(() => {}) }, "RUNTIME_ADAPTER_TIMEOUT"], [`data: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { content: false }, finish_reason: null }] })}\n\n`, {}, "RUNTIME_ADAPTER_EVENT_INVALID"], [`data: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { function_call: {} }, finish_reason: null }] })}\n\n`, {}, "RUNTIME_ADAPTER_EVENT_INVALID"]]) { const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(reply); }, { timeoutMs: 40 }); await adapter.initialize(); assert.equal(code(await adapter.generate(request(), options)), expected); await host.close(); }
});

test("a first callback cancellation stops later events from the same transport chunk", async () => {
  const controller = new AbortController(), stream = `data: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { content: "one" }, finish_reason: null }] })}\n\ndata: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { content: "two" }, finish_reason: null }] })}\n\n`; let callbacks = 0; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); }); await adapter.initialize(); const result = await adapter.generate(request(), { signal: controller.signal, onText: () => { callbacks += 1; controller.abort(); } }); assert.equal(code(result), "RUNTIME_ADAPTER_CANCELLED"); assert.equal(callbacks, 1); assert.equal(result.value.text, "one"); await host.close();
});

test("SSE bounds data-line count independently of character count", async () => {
  const result = await readSseEvents(new Response("data:\ndata:\ndata:\n\n").body, { maxEventLines: 2, onEvent() {} }); assert.equal(code(result), "RUNTIME_ADAPTER_EVENT_LIMIT");
});

test("usage metadata is accepted once before the receipt and cannot alter terminal evidence", async () => {
  const usage = { choices: [], model: "provider/model", id: "mock-chunk", object: "chat.completion.chunk", created: 1, system_fingerprint: null, usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3, prompt_tokens_details: { cached_tokens: 0 }, completion_tokens_details: { reasoning_tokens: 0 } } };
  const tail = `data: ${JSON.stringify(usage)}\n\n`, original = sse({ route: receipt({ tokens: { input: 2, output: 1, total: 3 } }) });
  for (const [stream, valid] of [[original.replace("event: route_receipt", tail + "event: route_receipt"), true], [original.replace("event: route_receipt", tail + tail + "event: route_receipt"), false], [original.replace("data: [DONE]", tail + "data: [DONE]"), false]]) {
    const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); });
    try { await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(result.valid, valid); if (!valid) assert.equal(code(result), "RUNTIME_ADAPTER_EVENT_INVALID"); } finally { await host.close(); }
  }
});

test("a structural failure receipt without stop is retained while the stream fails", async () => {
  const failed = receipt({ reason_codes: ["provider_chat_stream_failed"], engine: "managed_chat_blocked", actual_model: null }), stream = `data: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { content: "draft" }, finish_reason: null }] })}\n\nevent: route_receipt\ndata: ${JSON.stringify(failed)}\n\n`; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(stream); }); await adapter.initialize(); const result = await adapter.generate(request()); assert.equal(code(result), "RUNTIME_ADAPTER_RECEIPT_INVALID"); assert.deepEqual(result.value.serverReceipt, failed); assert.equal(result.value.text, "draft"); await host.close();
});

test("user cancellation and timeout are distinct and retain partial draft", async () => {
  for (const timeout of [false, true]) { const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.write(`data: ${JSON.stringify({ model: "provider/model", choices: [{ delta: { content: "draft" }, finish_reason: null }] })}\n\n`); }, timeout ? { timeoutMs: 40 } : {}); await adapter.initialize(); const controller = new AbortController(); if (!timeout) setTimeout(() => controller.abort(), 30); const result = await adapter.generate(request(), { signal: controller.signal }); assert.equal(code(result), timeout ? "RUNTIME_ADAPTER_TIMEOUT" : "RUNTIME_ADAPTER_CANCELLED"); assert.equal(result.value.text, "draft"); assert.equal(result.value.status, timeout ? "failed" : "cancelled"); assert.equal(result.value.cancellation.requested, !timeout); assert.equal(result.value.cancellation.clientAborted, true); await host.close(); }
});

test("late cancellation after complete success records requested without converting outcome", async () => {
  let reads = 0; const signal = { get aborted() { reads += 1; return reads > 1; }, addEventListener() {}, removeEventListener() {} }; const { host, adapter } = await configured((req, res) => { if (req.url === "/openapi.json") return json(res, openapi()); if (req.method === "GET") return json(res, control()); res.writeHead(200, { "content-type": "text/event-stream" }); res.end(sse()); }); await adapter.initialize(); const result = await adapter.generate(request(), { signal }); assert.equal(result.valid, true); assert.equal(result.value.cancellation.requested, true); assert.equal(result.value.cancellation.clientAborted, false); await host.close();
});

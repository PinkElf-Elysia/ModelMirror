import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { Buffer } from "node:buffer";
import { canonicalJson, sha256 } from "../tooling/bundle.mjs";
import * as acorn from "acorn";
import { createWorkerCapture } from "../tooling/worker-capture.mjs";
const { captureWorkerWorld, encodeWorkerEnvelope, encodeWorkerTransfer } = createWorkerCapture(acorn).value;

const URL = "https://afengy.cash/zh/explore/installed/e23bbc64-4fdd-46d8-92c0-64923961e5d8";
const TITLE = "无限重生系统 - 启动协议 v16.2 (Pyrite修复版)";
const fixture = async (name) => JSON.parse(await fs.readFile(new globalThis.URL("../fixtures/skill-generalization/" + name + "-world.capture.json", import.meta.url), "utf8"));
const assignment = (name = "测试世界", key = "one") => ({ format: "modelmirror.ai-rpg.worker-assignment", formatVersion: "0.1.0", jobId: "capture-test", owner: "offline-synthetic-test", sourceUrl: URL, authorizationRef: "synthetic-no-live-browser", capturedDate: "2026-09-05", worlds: [{ key, name }] });
const literal = (name = "测试世界", desc = "说明") => JSON.stringify({ name, desc, boss: "代表", identities: [{ name: "同名", items: ["物资", "物资"] }, { name: "同名", items: "物资二" }], talents: [{ name: "文本 root", color: "red", cost: -1, desc: "能力只是文本", type: "UR" }] });
const source = (raw, title = TITLE) => "<html><head><title>" + title + "</title></head><body><script>const worldDB = [" + raw + "];</script></body></html>";

function browser(sources, options = {}) {
  const reads = [];
  return { reads, playwright: { evaluate: async (fn, request) => {
    reads.push(structuredClone(request));
    if (options.throwRead) throw new Error("private absolute C:/private/path and raw source must not leak");
    const old = Object.getOwnPropertyDescriptor(globalThis, "document"), oldStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
    Object.defineProperty(globalThis, "getComputedStyle", { configurable: true, value: () => ({ visibility: "visible" }) });
    const html = typeof sources === "function" ? sources(reads.length) : sources;
    Object.defineProperty(globalThis, "document", { configurable: true, value: {
      URL: options.url ?? URL,
      querySelectorAll: (selector) => selector === "iframe[srcdoc]" ? Array.from({ length: options.frames ?? 1 }, () => ({ getAttribute: () => html })) : [{ getClientRects: () => options.interruption ? [{}] : [] }]
    } });
    try { return fn(request); } finally { if (old) Object.defineProperty(globalThis, "document", old); else delete globalThis.document; if (oldStyle) Object.defineProperty(globalThis, "getComputedStyle", oldStyle); else delete globalThis.getComputedStyle; }
  } } };
}
const code = (result) => result.diagnostics[0]?.code;

test("three retained real literals pass browser-shape simulation without expected inventory or source mutation", async () => {
  for (const name of ["gu", "cyberpunk", "genshin"]) {
    const captured = await fixture(name), input = assignment(captured.name), before = structuredClone(input);
    const tab = browser(source(captured.raw)), result = await captureWorkerWorld(tab, input, "one");
    assert.equal(result.valid, true, JSON.stringify(result.diagnostics));
    assert.equal(result.value.observation.raw, captured.raw);
    assert.equal(result.value.observation.rawSha256, captured.rawSha256);
    assert.equal(result.value.observation.dataSha256, captured.dataSha256);
    assert.equal(result.value.observation.rawUtf8Bytes, captured.rawUtf8Bytes);
    assert.equal(result.value.observation.openingTitle, TITLE);
    assert.equal(result.value.readCount, 3);
    assert.deepEqual(input, before);
    assert.equal(tab.reads[1].length, 8192);
    assert.equal(tab.reads[2].length, captured.raw.length);
    const encoded = encodeWorkerEnvelope(result.value);
    assert.equal(encoded.valid, true);
    assert.deepEqual(JSON.parse(Buffer.from(encoded.value, "base64").toString("utf8")), result.value);
  }
});

test("complete arrays, duplicate display names, source negatives and exact regex metacharacters survive", async () => {
  const name = "999.测试 (A+B)[x]$?", raw = literal(name);
  const result = await captureWorkerWorld(browser(source(raw)), assignment(name), "one");
  assert.equal(result.valid, true);
  const decoded = JSON.parse(result.value.observation.raw);
  assert.equal(decoded.identities.length, 2); assert.equal(decoded.talents[0].cost, -1);
  assert.equal(result.value.assignmentSha256, sha256(Buffer.from(canonicalJson(assignment(name)))));
});

test("missing and ambiguous names stop before any window read", async () => {
  for (const [raw, expected] of [[literal("别的世界"), "WORKER_WORLD_MISSING"], [literal() + "," + literal(), "WORKER_WORLD_AMBIGUOUS"]]) {
    const tab = browser(source(raw)), result = await captureWorkerWorld(tab, assignment(), "one");
    assert.equal(code(result), expected); assert.equal(tab.reads.length, 1);
  }
});

test("title comes from source head and changed or entity-encoded titles stop the frozen profile", async () => {
  const result = await captureWorkerWorld(browser(source(literal(), "A &amp; B")), assignment(), "one");
  assert.equal(code(result), "WORKER_PAGE_TITLE");
  for (const html of [source(literal()).replace("<title>", "<other>"), source(literal()).replace("</head>", "<title>second</title></head>")]) {
    assert.equal(code(await captureWorkerWorld(browser(html), assignment(), "one")), "WORKER_PAGE_TITLE");
  }
});

test("quoted braces and comments cannot terminate a world early; calls and executable AST stay inert", async () => {
  const raw = "{name:'测试世界',desc:'quote } and {',boss:'代表',identities:[],talents:[] /* } */}";
  assert.equal((await captureWorkerWorld(browser(source(raw)), assignment(), "one")).value.observation.raw, raw);
  globalThis.rpgWorkerExecuted = false;
  const unsafe = [
    "{name:'测试世界',desc:(globalThis.rpgWorkerExecuted=true),boss:'x',identities:[],talents:[]}",
    "{name:'测试世界',get desc(){return 'x'},boss:'x',identities:[],talents:[]}",
    "{name:'测试世界',desc:'a',desc:'b',boss:'x',identities:[],talents:[]}",
    "{name:'测试世界',desc:globalThis.unknown(),boss:'x',identities:[],talents:[]}"
  ];
  for (const item of unsafe) assert.equal(code(await captureWorkerWorld(browser(source(item)), assignment(), "one")), "WORKER_WORLD_LITERAL");
  assert.equal(globalThis.rpgWorkerExecuted, false); delete globalThis.rpgWorkerExecuted;
});

test("bounded window growth captures a world exceeding 8192 units and still fresh rereads only selected text", async () => {
  const raw = literal("测试世界", "长文本".repeat(5000)), tab = browser(source(raw));
  const result = await captureWorkerWorld(tab, assignment(), "one");
  assert.equal(result.valid, true); assert.equal(result.value.observation.raw, raw);
  assert.deepEqual(tab.reads.map((read) => read.length).filter(Boolean), [8192, 16384, raw.length]);
});

test("same-size selected data drift, source-length drift and title drift fail closed", async () => {
  const first = source(literal());
  for (const changed of [first.replace("说明", "改文"), first + " ", first.replace(TITLE, "Changed")]) {
    const tab = browser((count) => count < 3 ? first : changed);
    const result = await captureWorkerWorld(tab, assignment(), "one");
    assert.equal(code(result), "WORKER_PAGE_DRIFT");
    assert.equal(Object.hasOwn(result, "value"), false);
  }
});

test("wrong URL, zero or duplicate frames and interruption stop without returning source", async () => {
  for (const [options, expected] of [[{ url: "https://example.invalid/login" }, "WORKER_PAGE_URL"], [{ frames: 0 }, "WORKER_PAGE_FRAME"], [{ frames: 2 }, "WORKER_PAGE_FRAME"], [{ interruption: true }, "WORKER_PAGE_INTERRUPTION"]]) {
    const result = await captureWorkerWorld(browser(source(literal()), options), assignment(), "one");
    assert.equal(code(result), expected); assert.equal(Object.hasOwn(result, "value"), false);
  }
});

test("unassigned world, unknown assignment field and malformed assignment are rejected before browser access", async () => {
  for (const [input, key, expected] of [[assignment(), "other", "WORKER_WORLD_UNASSIGNED"], [{ ...assignment(), out: "C:/elsewhere" }, "one", "WORKER_ASSIGNMENT_SCHEMA"], [null, "one", "WORKER_ASSIGNMENT_SCHEMA"]]) {
    const tab = browser(source(literal())), result = await captureWorkerWorld(tab, input, key);
    assert.equal(code(result), expected); assert.equal(tab.reads.length, 0);
  }
  const duplicate = assignment(); duplicate.worlds.push({ ...duplicate.worlds[0] });
  assert.equal(code(await captureWorkerWorld(browser(""), duplicate, "one")), "WORKER_ASSIGNMENT_SCHEMA");
});

test("unknown source fields and incomplete literals stop without trimming, inventing or schema weakening", async () => {
  const added = JSON.parse(literal()); added.extra = "not allowed";
  assert.equal(code(await captureWorkerWorld(browser(source(JSON.stringify(added))), assignment(), "one")), "WORKER_WORLD_SCHEMA");
  const partial = "{name:'测试世界',desc:'incomplete";
  assert.equal(code(await captureWorkerWorld(browser(source(partial)), assignment(), "one")), "WORKER_DATABASE_SHAPE");
});

test("source unicode and byte caps are enforced independently of character positions", async () => {
  assert.equal(code(await captureWorkerWorld(browser(source(literal()) + "\ud800"), assignment(), "one")), "WORKER_PAGE_UNICODE");
  assert.equal(code(await captureWorkerWorld(browser(source(literal()) + "界".repeat(5592406)), assignment(), "one")), "WORKER_PAGE_LIMIT");
});

test("browser errors and envelope validation produce stable redacted diagnostics", async () => {
  const one = await captureWorkerWorld(browser("", { throwRead: true }), assignment(), "one");
  const two = await captureWorkerWorld(browser("", { throwRead: true }), assignment(), "one");
  assert.deepEqual(one, two); assert.equal(code(one), "WORKER_BROWSER_READ");
  assert.equal(JSON.stringify(one).includes("private"), false);
  assert.equal(code(encodeWorkerEnvelope({ arbitrary: "raw secret" })), "WORKER_ENVELOPE_SCHEMA");
});

test("worldDB membership excludes equal-shaped non-world objects and source-string decoys", async () => {
  const raw = literal(), html = source(raw).replace("const worldDB", "const other = " + raw + "; const text = 'name does not locate a resource'; const worldDB");
  const result = await captureWorkerWorld(browser(html), assignment(), "one");
  assert.equal(result.valid, true);
  assert.equal(result.value.observation.start, html.lastIndexOf(raw));
  assert.equal(result.value.observation.raw, raw);
});

test("comment-only or duplicate database markers and non-array expressions cannot impersonate a database", async () => {
  for (const html of [
    source(literal()).replace("const worldDB", "/* const worldDB").replace("];</script>", "]; */</script>"),
    source(literal()).replace("const worldDB", "const worldDB = []; const worldDB"),
    source(literal()).replace("const worldDB = [", "const worldDB = call([")
  ]) {
    const result = await captureWorkerWorld(browser(html), assignment(), "one");
    assert.equal(result.valid, false);
    assert.equal(code(result), "WORKER_DATABASE_DECLARATION");
  }
});

test("declaration-prefix change during fresh reread stops even if all selected raw bytes stay equal", async () => {
  const first = source(literal()), changed = first.replace("worldDB", "another");
  const result = await captureWorkerWorld(browser((count) => count < 3 ? first : changed), assignment(), "one");
  assert.equal(code(result), "WORKER_PAGE_DRIFT");
});


test("transfer text preserves escapes and neutralizes template or shell text without evaluation", async () => {
  const tick = String.fromCharCode(96);
  const desc = "literal " + tick + " $" + "{notRun()} $(notRun) " + tick + "\\n \\u0041 \\\\ quote \" single ' \n'@\n\u2028\u2029";
  const result = await captureWorkerWorld(browser(source(literal("测试世界", desc))), assignment(), "one");
  assert.equal(result.valid, true);
  const before = JSON.stringify(result.value), encoded = encodeWorkerTransfer(result.value);
  assert.equal(encoded.valid, true);
  for (const unit of [tick, "$", "\n", "\r", "\u2028", "\u2029"]) assert.equal(encoded.value.includes(unit), false);
  assert.deepEqual(JSON.parse(encoded.value), result.value);
  const parsed = acorn.parse("String.raw" + tick + encoded.value + tick, { ecmaVersion: 2022 });
  const expression = parsed.body[0].expression;
  assert.equal(expression.type, "TaggedTemplateExpression");
  assert.equal(expression.quasi.expressions.length, 0);
  assert.equal(expression.quasi.quasis[0].value.raw, encoded.value);
  assert.equal(JSON.stringify(result.value), before);
  assert.equal(code(encodeWorkerTransfer(structuredClone(result.value))), "WORKER_ENVELOPE_SCHEMA");
  assert.equal(code(createWorkerCapture(acorn).value.encodeWorkerTransfer(result.value)), "WORKER_ENVELOPE_SCHEMA");
});

test("transfer expansion beyond its byte cap is refused before copying", async () => {
  const result = await captureWorkerWorld(browser(source(literal("测试世界", "$".repeat(350000)))), assignment(), "one");
  assert.equal(result.valid, true);
  assert.equal(code(encodeWorkerTransfer(result.value)), "WORKER_TRANSFER_LIMIT");
});

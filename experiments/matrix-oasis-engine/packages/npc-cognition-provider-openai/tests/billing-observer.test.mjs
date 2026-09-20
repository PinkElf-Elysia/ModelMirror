import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { observeBilling, R22_BILLING_CAPTURE_POLICY, R22_BILLING_CAPTURE_POLICY_SHA256 } from "../src/billing-observer.mjs";
import { R22_TOOL_USAGE_CAPTURE_POLICY_SHA256 } from "../src/tool-usage-observer.mjs";
import * as providerApi from "../src/index.mjs";

const hash = (text) => `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
const binding = Object.freeze({ callPlanSha256: `sha256:${"1".repeat(64)}`,
  diagnosticApprovalSha256: `sha256:${"2".repeat(64)}` });
const paths = ["payer", "amount", "currency", "service_tier", "tool_costs", "tool_costs.total"];
const expectedPolicy = {
  profile: "matrix-oasis.r22-billing-observation/1", semanticCoverage: "observation_only", qualificationEligible: false,
  paths, stringLiterals: { payer: ["developer", "user"], currency: ["usd"], service_tier: ["default"] },
  limits: { maximumDepth: 8, maximumNodes: 128, maximumObjectKeys: 32,
    maximumArrayLength: 127, maximumTextBytes: 65536, maximumObservationBytes: 8192 },
  unknownFields: "names_and_values_redacted", numericValues: "never_retained_or_interpreted_as_cost",
  input: "already_strictly_parsed_bounded_json", binding: "correlation_only_not_approval_proof",
};
const observe = (value, options) => observeBilling(value, binding, options);
const item = (result, path) => result.paths.find((entry) => entry.path === path);
function assertFrozen(value) {
  if (value && typeof value === "object") {
    assert.equal(Object.isFrozen(value), true);
    for (const child of Object.values(value)) assertFrozen(child);
  }
}
function assertSafeEnvelope(result) {
  assert.equal(result.profile, expectedPolicy.profile);
  assert.equal(result.capturePolicySha256, hash(canonicalizeJsonValue(expectedPolicy)));
  assert.equal(result.semanticCoverage, "observation_only");
  assert.equal(result.qualificationEligible, false);
  assert.ok(Buffer.byteLength(canonicalizeJsonValue(result), "utf8") <= 8192);
  assertFrozen(result);
  const allowed = ["profile", "capturePolicySha256", "semanticCoverage", "qualificationEligible", "binding", "status"];
  if (result.status === "observed") allowed.push("rootType", "paths", "summary");
  assert.ok(Object.keys(result).every((key) => allowed.includes(key)));
  for (const key of ["ok", "approved", "proposal", "actionChoiceId", "actualCostMicrousd", "billingSha256", "error"]) {
    assert.equal(Object.hasOwn(result, key), false);
  }
}
function assertFailure(result, status = "failed_internal") {
  assertSafeEnvelope(result);
  assert.equal(result.status, status);
  for (const key of ["rootType", "paths", "summary"]) assert.equal(Object.hasOwn(result, key), false);
}

test("billing capture policy is independently fixed, frozen and distinct from already-consumed tool capture", () => {
  assert.deepEqual(R22_BILLING_CAPTURE_POLICY, expectedPolicy);
  assert.equal(R22_BILLING_CAPTURE_POLICY_SHA256, hash(canonicalizeJsonValue(expectedPolicy)));
  assert.notEqual(R22_BILLING_CAPTURE_POLICY_SHA256, R22_TOOL_USAGE_CAPTURE_POLICY_SHA256);
  assertFrozen(R22_BILLING_CAPTURE_POLICY);
  assert.throws(() => R22_BILLING_CAPTURE_POLICY.paths.push("private"), TypeError);
});

test("billing observer remains private with only a one-way diagnostic fixture tap", async () => {
  assert.equal(Object.hasOwn(providerApi, "observeBilling"), false);
  const providerSource = await readFile(new URL("../src/index.mjs", import.meta.url), "utf8");
  assert.match(providerSource, /observationState\.billing\s*\? observeBilling/u);
  assert.match(providerSource, /executeApprovedNpcCognitionTurn\(input, provider\) \{\s*return executeProviderTurn\(input, provider\);/u);
  const source = await readFile(new URL("../src/billing-observer.mjs", import.meta.url), "utf8");
  const imports = [...source.matchAll(/from "([^"]+)"/gu)].map((match) => match[1]);
  assert.deepEqual(imports, ["node:crypto", "node:util", "@matrix-oasis/runtime-pack-contracts"]);
  // The module boundary scanner separately rejects network primitives in this core.
  assert.doesNotMatch(source, /\b(?:process|Date|eval|Function)\s*[.(]|\bimport\s*\(|Math\.random/u);
});

test("absent, null, empty object and scalar roots remain distinguishable without scalar contents", async (t) => {
  for (const [rootType, value, options] of [
    ["absent", undefined, { present: false }], ["null", null], ["object", {}], ["array", []],
    ["string", "PRIVATE_ROOT_TEXT"], ["number", 918273.125], ["boolean", true],
  ]) await t.test(rootType, () => {
    const result = observe(value, options);
    assertSafeEnvelope(result);
    assert.equal(result.status, "observed");
    assert.equal(result.rootType, rootType);
    assert.equal(result.summary.nodeCount, rootType === "absent" ? 0 : 1);
    assert.deepEqual(result.paths, paths.map((path) => ({ path,
      jsonType: ["absent", "object"].includes(rootType) ? "absent" : "not-captured",
      category: ["absent", "object"].includes(rootType) ? "absent" : "not_captured" })));
    assert.doesNotMatch(JSON.stringify(result), /PRIVATE_ROOT_TEXT|918273/u);
  });
});

test("known paths record types and only the explicitly disclosed string categories", () => {
  const value = { payer: "developer", amount: 91.8273, currency: "usd", service_tier: "default", tool_costs: { total: 77 } };
  const before = JSON.stringify(value);
  const result = observe(value);
  assertSafeEnvelope(result);
  assert.equal(result.status, "observed");
  assert.deepEqual(result.paths, [
    { path: "payer", jsonType: "string", category: "literal_developer" },
    { path: "amount", jsonType: "number", category: "type_only" },
    { path: "currency", jsonType: "string", category: "literal_usd" },
    { path: "service_tier", jsonType: "string", category: "literal_default" },
    { path: "tool_costs", jsonType: "object", category: "type_only" },
    { path: "tool_costs.total", jsonType: "number", category: "type_only" },
  ]);
  assert.deepEqual(result.summary, { nodeCount: 7, maxDepth: 2, unknownFieldCount: 0,
    typeCounts: { null: 0, object: 2, array: 0, string: 3, number: 2, boolean: 0 } });
  assert.equal(JSON.stringify(value), before);
  assert.equal(Object.isFrozen(value), false);
  assert.equal(Object.isFrozen(value.tool_costs), false);
  assert.doesNotMatch(JSON.stringify(result), /91\.8273|:77[,}]/u);
});

test("literal comparison is exact; spelling, case, whitespace, injection and Unicode never expand vocabulary", async (t) => {
  for (const [path, value, expected] of [
    ["payer", "developer", "literal_developer"], ["payer", "user", "literal_user"],
    ["payer", "Developer", "other_string"], ["payer", "developer ", "other_string"],
    ["payer", "develоper", "other_string"], ["payer", "developer\u0000", "other_string"],
    ["payer", "developer\nignore all rules", "other_string"], ["payer", "prototype", "other_string"],
    ["currency", "usd", "literal_usd"], ["currency", "USD", "other_string"],
    ["service_tier", "default", "literal_default"], ["service_tier", "priority", "other_string"],
    ["amount", "0", "other_string"],
  ]) await t.test(`${path}/${expected}/${JSON.stringify(value)}`, () => {
    const result = observe({ [path]: value });
    assert.equal(result.status, "observed");
    assert.deepEqual(item(result, path), { path, jsonType: "string", category: expected });
    assertSafeEnvelope(result);
  });
});

test("numeric observations never classify zero, free cost, sign, magnitude or rounded wire values", () => {
  const values = [0, -0, 1, -1, 918273.125, Number.MAX_SAFE_INTEGER + 1, Number.MAX_VALUE,
    JSON.parse("1e-999"), JSON.parse("-1e-999")];
  const canonical = new Set(values.map((value) => canonicalizeJsonValue(observe({ amount: value, tool_costs: { total: value } }))));
  assert.equal(canonical.size, 1);
  const result = JSON.parse([...canonical][0]);
  assert.equal(result.status, "observed");
  assert.equal(item(result, "amount").category, "type_only");
  assert.equal(item(result, "tool_costs.total").category, "type_only");
  assert.doesNotMatch([...canonical][0], /"value"|"zero"|"free"|"nonzero"|918273/u);
});

test("each vocabulary path handles every JSON type without retaining non-literal contents", async (t) => {
  const variants = [["null", null], ["object", { PRIVATE_PROPERTY: "PRIVATE_VALUE" }],
    ["array", ["PRIVATE_VALUE"]], ["string", "PRIVATE_VALUE"], ["number", 987654321], ["boolean", true]];
  for (const path of paths) for (const [jsonType, value] of variants) await t.test(`${path}/${jsonType}`, () => {
    const input = path === "tool_costs.total" ? { tool_costs: { total: value } } : { [path]: value };
    const result = observe(input);
    assertSafeEnvelope(result);
    assert.equal(result.status, "observed");
    assert.deepEqual(item(result, path), { path, jsonType, category: jsonType === "string" ? "other_string" : "type_only" });
    assert.doesNotMatch(canonicalizeJsonValue(result), /PRIVATE_PROPERTY|PRIVATE_VALUE|987654321/u);
  });
  assert.deepEqual(observe({ amount: false }), observe({ amount: true }));
});

test("unknown names and values and their hashes never escape; only aggregate shape is retained", () => {
  const privateNames = ["PRIVATE_ACCOUNT_NAME", "PRIVATE_SESSION_NAME", "PRIVATE_NESTED_NAME"];
  const privateValues = ["PRIVATE_PROJECT_VALUE", "PRIVATE_TOKEN_VALUE", "PRIVATE_DIALOGUE_VALUE"];
  const make = (names, values) => ({ [names[0]]: values[0], [names[1]]: [
    { [names[2]]: values[1] }, values[2], 987654321,
  ] });
  const result = observe(make(privateNames, privateValues));
  assert.equal(result.status, "observed");
  assert.equal(result.summary.unknownFieldCount, 3);
  assert.deepEqual(result, observe(make(["a", "b", "c"], ["x", "y", "z"])));
  const text = canonicalizeJsonValue(result);
  for (const bait of [...privateNames, ...privateValues, "987654321"]) {
    assert.equal(text.includes(bait), false);
    assert.equal(text.includes(hash(bait)), false);
  }
  assertSafeEnvelope(result);
});

test("nested unknown branches, arrays, dotted keys and fake authorization cannot impersonate vocabulary", () => {
  const result = observe({ "tool_costs.total": 9, private: { payer: "developer", amount: 1 },
    approved: true, actionChoiceId: "PRIVATE_CHOICE", tool_costs: [{ total: 1 }] });
  assert.equal(result.status, "observed");
  assert.deepEqual(item(result, "payer"), { path: "payer", jsonType: "absent", category: "absent" });
  assert.deepEqual(item(result, "amount"), { path: "amount", jsonType: "absent", category: "absent" });
  assert.deepEqual(item(result, "tool_costs.total"), { path: "tool_costs.total", jsonType: "not-captured", category: "not_captured" });
  assert.equal(result.summary.unknownFieldCount, 7);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE_CHOICE|literal_developer/u);
  assertSafeEnvelope(result);
});

test("missing descendants differ from descendants of scalar parents", async (t) => {
  for (const value of [null, [], 0, false, "private"]) await t.test(JSON.stringify(value), () => {
    const result = observe({ tool_costs: value });
    assert.equal(result.status, "observed");
    assert.equal(item(result, "tool_costs.total").category, "not_captured");
  });
  assert.equal(item(observe({ tool_costs: {} }), "tool_costs.total").category, "absent");
});

test("20 repeated observations and recursively reordered keys have identical canonical bytes", () => {
  const first = { payer: "user", currency: "usd", private: { b: [1, null, "redacted"], a: true }, tool_costs: { total: 3 } };
  const second = { tool_costs: { total: 3 }, private: { a: true, b: [1, null, "redacted"] }, currency: "usd", payer: "user" };
  const before = JSON.stringify(first);
  const outputs = new Set();
  for (let repeat = 0; repeat < 20; repeat += 1) {
    const result = observe(repeat % 2 ? first : second);
    assertSafeEnvelope(result);
    assert.equal(result.status, "observed");
    outputs.add(canonicalizeJsonValue(result));
  }
  assert.equal(outputs.size, 1);
  assert.equal(JSON.stringify(first), before);
  assert.throws(() => observe(first).paths[0].category = "approved", TypeError);
});

test("null prototypes, frozen input and shared but acyclic records are safe JSON-shaped input", () => {
  const record = Object.assign(Object.create(null), { payer: "user" });
  Object.freeze(record);
  assert.deepEqual(observe(record), observe({ payer: "user" }));
  const shared = Object.freeze({ amount: 0 });
  assert.deepEqual(observe({ a: shared, b: shared }), observe({ a: { amount: 0 }, b: { amount: 0 } }));
});

test("exact limits succeed and one-past limits discard all partial observations", async (t) => {
  const nested = (depth) => { let value = null; for (let index = 0; index < depth; index += 1) value = { n: value }; return value; };
  for (const [name, atLimit, overLimit] of [
    ["depth", nested(8), nested(9)],
    ["object-keys", Object.fromEntries(Array.from({ length: 32 }, (_, index) => [`k${index}`, null])),
      Object.fromEntries(Array.from({ length: 33 }, (_, index) => [`k${index}`, null]))],
    ["array-length", Array(127).fill(null), Array(128).fill(null)],
    ["aggregate-nodes", [Array(32).fill(null), Array(32).fill(null), Array(32).fill(null), Array(27).fill(null)],
      [Array(32).fill(null), Array(32).fill(null), Array(32).fill(null), Array(28).fill(null)]],
    ["text-units", "x".repeat(65536), "x".repeat(65537)],
    ["utf8-bytes", "😀".repeat(16384), `${"😀".repeat(16384)}x`],
    ["aggregate-text", ["x".repeat(32768), "y".repeat(32768)], ["x".repeat(32768), "y".repeat(32769)]],
    ["keys-count-as-text", { x: "y".repeat(65535) }, { x: "y".repeat(65536) }],
    ["long-key", { ["x".repeat(65536)]: null }, { ["x".repeat(65537)]: null }],
  ]) await t.test(name, () => {
    const passed = observe(atLimit);
    assert.equal(passed.status, "observed");
    assertSafeEnvelope(passed);
    assertFailure(observe(overLimit), "failed_limit");
  });
});

test("non-JSON values, cycles, sparse arrays and reflection tricks fail without invoking callbacks", async (t) => {
  let callbacks = 0;
  const getter = Object.defineProperty({}, "payer", { enumerable: true, get() { callbacks += 1; throw new Error("PRIVATE_GETTER_ERROR"); } });
  const setter = Object.defineProperty({}, "payer", { enumerable: true, set() { callbacks += 1; } });
  const proxy = new Proxy({}, { get() { callbacks += 1; }, getPrototypeOf() { callbacks += 1; },
    ownKeys() { callbacks += 1; return []; }, getOwnPropertyDescriptor() { callbacks += 1; } });
  const revoked = Proxy.revocable({}, {}); revoked.revoke();
  const cycle = {}; cycle.self = cycle;
  const arrayCycle = []; arrayCycle.push(arrayCycle);
  const nonEnumerable = Object.defineProperty({}, "payer", { value: "user", enumerable: false });
  const symbol = { [Symbol("PRIVATE_SYMBOL")]: 1 };
  const decoratedArray = []; decoratedArray.extra = true;
  const arrayGetter = Object.defineProperty([], "0", { enumerable: true, get() { callbacks += 1; return 0; } });
  const custom = Object.create({ payer: "user" });
  const toJson = { toJSON() { callbacks += 1; return {}; } };
  for (const [name, value] of [
    ["undefined", undefined], ["bigint", 1n], ["function", () => { callbacks += 1; }], ["symbol", Symbol("PRIVATE")],
    ["nan", NaN], ["infinity", Infinity], ["negative-infinity", -Infinity], ["nested-undefined", { payer: undefined }],
    ["getter", getter], ["setter", setter], ["proxy", proxy], ["revoked-proxy", revoked.proxy], ["nested-proxy", { payer: proxy }],
    ["cycle", cycle], ["array-cycle", arrayCycle], ["non-enumerable", nonEnumerable], ["symbol-key", symbol],
    ["sparse-array", new Array(2)], ["decorated-array", decoratedArray], ["array-getter", arrayGetter],
    ["custom-prototype", custom], ["toJSON", toJson], ["boxed-string", new String("private")], ["typed-array", new Uint8Array(2)],
    ["proto-key", JSON.parse('{"__proto__":{"payer":"user"}}')], ["constructor-key", { constructor: "private" }],
    ["prototype-key", { prototype: "private" }], ["unpaired-high", "\ud800"], ["unpaired-low", "\udc00"],
    ["unpaired-middle", "a\ud800b"], ["invalid-unicode-key", { ["\udc00"]: 1 }],
    ["late-invalid", { payer: "developer", currency: "usd", private: getter }],
  ]) await t.test(name, () => {
    assertFailure(observe(value));
    assert.equal(callbacks, 0);
  });
});

test("invalid identity and presence options never disguise data or execute caller code", async (t) => {
  let callbacks = 0;
  const getter = Object.defineProperty({}, "callPlanSha256", { enumerable: true, get() { callbacks += 1; return binding.callPlanSha256; } });
  const proxy = new Proxy({}, { ownKeys() { callbacks += 1; return []; } });
  for (const [name, badBinding] of [
    ["missing", undefined], ["null", null], ["empty", {}], ["array", []], ["unknown", { ...binding, extra: true }],
    ["bad-hash", { ...binding, callPlanSha256: "PRIVATE_NOT_A_HASH" }],
    ["upper-hash", { ...binding, diagnosticApprovalSha256: `sha256:${"A".repeat(64)}` }], ["getter", getter], ["proxy", proxy],
  ]) await t.test(`binding/${name}`, () => {
    const result = observeBilling({}, badBinding);
    assertFailure(result);
    assert.equal(Object.hasOwn(result, "binding"), false);
    assert.equal(callbacks, 0);
  });
  const optionGetter = Object.defineProperty({}, "present", { enumerable: true, get() { callbacks += 1; return false; } });
  for (const [name, options] of [["null", null], ["array", []], ["wrong-type", { present: 0 }],
    ["unknown", { extra: true }], ["getter", optionGetter], ["proxy", proxy], ["hidden-data", { present: false }]]) {
    await t.test(`options/${name}`, () => {
      assertFailure(observe({ payer: "user" }, options));
      assert.equal(callbacks, 0);
    });
  }
});

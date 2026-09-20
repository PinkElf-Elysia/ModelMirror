import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { observeToolUsage } from "../src/tool-usage-observer.mjs";

const PROFILE = "matrix-oasis.r22-tool-usage-diagnostic/1";
const SEMANTIC_COVERAGE = "observation_only";
const CAPTURE_POLICY_SHA256 = "sha256:64314133dc13fa0bce6e078e10dffc8059f01cc6f716d3bcb832bd7a4d2a07ae";
const BINDING = Object.freeze({
  callPlanSha256: `sha256:${"a".repeat(64)}`,
  diagnosticApprovalSha256: `sha256:${"b".repeat(64)}`,
});

// Deliberately independent of the implementation: this is the complete approved
// observation vocabulary, written out in the test rather than imported from src.
const COUNTER_PATHS = Object.freeze([
  "image_gen.input_tokens",
  "image_gen.input_tokens_details.image_tokens",
  "image_gen.input_tokens_details.text_tokens",
  "image_gen.output_tokens",
  "image_gen.output_tokens_details.image_tokens",
  "image_gen.output_tokens_details.text_tokens",
  "image_gen.total_tokens",
  "web_search.num_requests",
]);

const JSON_TYPES = new Set([
  "absent",
  "not-captured",
  "null",
  "object",
  "array",
  "string",
  "number",
  "boolean",
]);
const COUNTER_STATUSES = new Set([
  "captured",
  "absent",
  "not_counter",
  "invalid_number",
  "not_captured",
]);
const SUCCESS_KEYS = [
  "binding",
  "capturePolicySha256",
  "coverage",
  "paths",
  "profile",
  "qualificationEligible",
  "rootType",
  "semanticCoverage",
  "status",
  "summary",
];
const FAILURE_KEYS = [
  "binding",
  "capturePolicySha256",
  "profile",
  "qualificationEligible",
  "semanticCoverage",
  "status",
];
const UNBOUND_FAILURE_KEYS = FAILURE_KEYS.filter((key) => key !== "binding");
const SUMMARY_KEYS = ["maxDepth", "nodeCount", "typeCounts", "unknownFieldCount"];
const TYPE_COUNT_KEYS = ["array", "boolean", "null", "number", "object", "string"];

function sha256(value) {
  return `sha256:${createHash("sha256").update(value, "utf8").digest("hex")}`;
}

function assertCommon(result) {
  assert.equal(result.profile, PROFILE);
  assert.equal(result.semanticCoverage, SEMANTIC_COVERAGE);
  assert.equal(result.qualificationEligible, false);
  assert.equal(result.capturePolicySha256, CAPTURE_POLICY_SHA256);
  assert.deepEqual(result.binding, BINDING);
}

function assertSummaryShape(summary) {
  assert.deepEqual(Object.keys(summary).sort(), SUMMARY_KEYS);
  assert.deepEqual(Object.keys(summary.typeCounts).sort(), TYPE_COUNT_KEYS);
  for (const key of ["nodeCount", "maxDepth", "unknownFieldCount", ...TYPE_COUNT_KEYS]) {
    const value = TYPE_COUNT_KEYS.includes(key) ? summary.typeCounts[key] : summary[key];
    assert.equal(Number.isSafeInteger(value), true, `${key} must be a safe integer`);
    assert.ok(value >= 0, `${key} must be non-negative`);
  }
  const typedNodes = Object.values(summary.typeCounts).reduce((sum, value) => sum + value, 0);
  assert.equal(typedNodes, summary.nodeCount);
}

function assertSuccess(result) {
  assertCommon(result);
  assert.deepEqual(Object.keys(result).sort(), SUCCESS_KEYS);
  assert.equal(result.status, "observed");
  assert.ok(["redacted", "vocabulary_only"].includes(result.coverage));
  assertSummaryShape(result.summary);
  assert.equal(result.paths.length, COUNTER_PATHS.length);
  assert.deepEqual(result.paths.map((entry) => entry.path), COUNTER_PATHS);
  assert.deepEqual([...result.paths].map((entry) => entry.path).sort(), COUNTER_PATHS);
  for (const entry of result.paths) {
    assert.deepEqual(
      Object.keys(entry).sort(),
      entry.counterStatus === "captured"
        ? ["counterStatus", "jsonType", "path", "value"]
        : ["counterStatus", "jsonType", "path"],
    );
    assert.equal(JSON_TYPES.has(entry.jsonType), true);
    assert.equal(COUNTER_STATUSES.has(entry.counterStatus), true);
    if (entry.counterStatus === "captured") {
      assert.equal(entry.jsonType, "number");
      assert.equal(Number.isSafeInteger(entry.value), true);
      assert.ok(entry.value >= 0 && entry.value <= 65_536);
      assert.equal(Object.is(entry.value, -0), false);
    } else {
      assert.equal(Object.hasOwn(entry, "value"), false);
    }
  }
  assertDeepFrozen(result);
}

function assertFailure(result, status) {
  assertCommon(result);
  assert.deepEqual(Object.keys(result).sort(), FAILURE_KEYS);
  assert.equal(result.status, status);
  for (const forbidden of ["paths", "summary", "rootType", "coverage"]) {
    assert.equal(Object.hasOwn(result, forbidden), false);
  }
  assertDeepFrozen(result);
}

function assertUnboundFailure(result, status) {
  assert.equal(result.profile, PROFILE);
  assert.equal(result.semanticCoverage, SEMANTIC_COVERAGE);
  assert.equal(result.qualificationEligible, false);
  assert.equal(result.capturePolicySha256, CAPTURE_POLICY_SHA256);
  assert.deepEqual(Object.keys(result).sort(), UNBOUND_FAILURE_KEYS);
  assert.equal(result.status, status);
  assert.equal(Object.hasOwn(result, "binding"), false);
  for (const forbidden of ["paths", "summary", "rootType", "coverage"]) {
    assert.equal(Object.hasOwn(result, forbidden), false);
  }
  assertDeepFrozen(result);
}

function assertDeepFrozen(value, seen = new Set()) {
  if (value === null || typeof value !== "object" || seen.has(value)) return;
  seen.add(value);
  assert.equal(Object.isFrozen(value), true);
  for (const child of Object.values(value)) assertDeepFrozen(child, seen);
}

function pathEntry(result, path) {
  const entry = result.paths.find((candidate) => candidate.path === path);
  assert.ok(entry, `missing fixed path ${path}`);
  return entry;
}

function completeKnownCounters(value = 0) {
  return {
    image_gen: {
      input_tokens: value,
      input_tokens_details: { image_tokens: value, text_tokens: value },
      output_tokens: value,
      output_tokens_details: { image_tokens: value, text_tokens: value },
      total_tokens: value,
    },
    web_search: { num_requests: value },
  };
}

function nestedArrayToDepth(depth) {
  let value = null;
  for (let index = 0; index < depth; index += 1) value = [value];
  return value;
}

function recordWithKeys(count) {
  return Object.fromEntries(
    Array.from({ length: count }, (_, index) => [`unknown_${String(index).padStart(2, "0")}`, null]),
  );
}

function canonicalBytes(value) {
  return new TextEncoder().encode(JSON.stringify(value));
}

test("presence=false is an explicit absent observation with eight absent paths and a zero summary", () => {
  const mutableBinding = { ...BINDING };
  let trapCalls = 0;
  const ignored = new Proxy({}, {
    get() { trapCalls += 1; throw new Error("absent-value-secret"); },
    getOwnPropertyDescriptor() { trapCalls += 1; throw new Error("absent-value-secret"); },
    getPrototypeOf() { trapCalls += 1; throw new Error("absent-value-secret"); },
    ownKeys() { trapCalls += 1; throw new Error("absent-value-secret"); },
  });
  const result = observeToolUsage(ignored, mutableBinding, { present: false });
  assertSuccess(result);
  assert.equal(result.rootType, "absent");
  assert.equal(result.coverage, "vocabulary_only");
  assert.deepEqual(result.summary, {
    nodeCount: 0,
    maxDepth: 0,
    unknownFieldCount: 0,
    typeCounts: { null: 0, object: 0, array: 0, string: 0, number: 0, boolean: 0 },
  });
  for (const entry of result.paths) {
    assert.equal(entry.jsonType, "absent");
    assert.equal(entry.counterStatus, "absent");
  }
  assert.notEqual(result.binding, mutableBinding);
  assert.equal(trapCalls, 0);
  assert.equal(Object.isFrozen(mutableBinding), false);
  assert.deepEqual(mutableBinding, BINDING);
});

test("every permitted present root type is reported using only the fixed root vocabulary", async (t) => {
  const cases = [
    ["null", null],
    ["object", {}],
    ["array", []],
    ["string", "synthetic"],
    ["number", 1],
    ["boolean", false],
  ];
  for (const [rootType, value] of cases) {
    await t.test(rootType, () => {
      const result = observeToolUsage(value, BINDING);
      assertSuccess(result);
      assert.equal(result.rootType, rootType);
    });
  }
});

test("all eight independently hard-coded counter paths are captured and sorted by full path", () => {
  const input = {
    image_gen: {
      total_tokens: 7,
      output_tokens_details: { text_tokens: 6, image_tokens: 5 },
      output_tokens: 4,
      input_tokens_details: { text_tokens: 3, image_tokens: 2 },
      input_tokens: 1,
    },
    web_search: { num_requests: 8 },
  };
  const before = structuredClone(input);
  const result = observeToolUsage(input, BINDING);
  assertSuccess(result);
  assert.equal(result.coverage, "vocabulary_only");
  assert.deepEqual(result.paths.map(({ path, value }) => [path, value]), [
    ["image_gen.input_tokens", 1],
    ["image_gen.input_tokens_details.image_tokens", 2],
    ["image_gen.input_tokens_details.text_tokens", 3],
    ["image_gen.output_tokens", 4],
    ["image_gen.output_tokens_details.image_tokens", 5],
    ["image_gen.output_tokens_details.text_tokens", 6],
    ["image_gen.total_tokens", 7],
    ["web_search.num_requests", 8],
  ]);
  assert.deepEqual(input, before);
});

test("known zero counters plus an unknown positive subtree remain redacted and never claim free usage", () => {
  const input = {
    ...completeKnownCounters(0),
    file_search: { num_requests: 31_337, billable_units: 9_001 },
  };
  const result = observeToolUsage(input, BINDING);
  assertSuccess(result);
  assert.equal(result.coverage, "redacted");
  assert.ok(result.summary.unknownFieldCount > 0);
  assert.ok(result.paths.every((entry) => entry.counterStatus === "captured" && entry.value === 0));
  assert.equal(JSON.stringify(result).includes("file_search"), false);
  assert.equal(JSON.stringify(result).includes("31337"), false);
  assert.equal(JSON.stringify(result).includes("9001"), false);
  assert.deepEqual(Object.keys(result).sort(), SUCCESS_KEYS);
  assert.equal(result.qualificationEligible, false);
});

test("leaf names under unknown parents never gain vocabulary status", () => {
  const input = {
    unknown_parent: {
      input_tokens: 41_001,
      num_requests: 41_002,
      image_tokens: 41_003,
    },
  };
  const result = observeToolUsage(input, BINDING);
  assertSuccess(result);
  assert.equal(result.coverage, "redacted");
  assert.equal(result.paths.some((entry) => entry.counterStatus === "captured"), false);
  for (const bait of ["unknown_parent", "41001", "41002", "41003"]) {
    assert.equal(JSON.stringify(result).includes(bait), false);
  }
});

test("dotted keys cannot impersonate an approved complete path", async (t) => {
  const cases = [
    ["dotted root key", { "image_gen.input_tokens": 54_321 }, "54321"],
    ["dotted key below a known parent", {
      image_gen: { "input_tokens_details.image_tokens": 54_322 },
    }, "54322"],
  ];
  for (const [name, input, secretNumber] of cases) {
    await t.test(name, () => {
      const before = structuredClone(input);
      const result = observeToolUsage(input, BINDING);
      assertSuccess(result);
      assert.equal(result.coverage, "redacted");
      assert.ok(result.summary.unknownFieldCount > 0);
      assert.equal(result.paths.some((entry) => entry.counterStatus === "captured"), false);
      assert.ok(result.paths.every((entry) => entry.counterStatus === "absent"));
      assert.equal(JSON.stringify(result).includes(secretNumber), false);
      assert.deepEqual(input, before);
    });
  }
});

test("missing leaves stay absent while an unreadable known parent is not_captured", () => {
  const missing = observeToolUsage({ image_gen: {}, web_search: {} }, BINDING);
  assertSuccess(missing);
  for (const entry of missing.paths) {
    assert.equal(entry.counterStatus, "absent");
    assert.equal(entry.jsonType, "absent");
  }

  const blocked = observeToolUsage({ image_gen: { input_tokens_details: "opaque" } }, BINDING);
  assertSuccess(blocked);
  for (const path of [
    "image_gen.input_tokens_details.image_tokens",
    "image_gen.input_tokens_details.text_tokens",
  ]) {
    const entry = pathEntry(blocked, path);
    assert.equal(entry.counterStatus, "not_captured");
    assert.equal(entry.jsonType, "not-captured");
  }
  assert.equal(pathEntry(blocked, "image_gen.input_tokens").counterStatus, "absent");
});

test("counter numeric and type boundaries never coerce or retain invalid values", async (t) => {
  const cases = [
    ["zero", 0, "captured", 0, "number"],
    ["one", 1, "captured", 1, "number"],
    ["upper bound", 65_536, "captured", 65_536, "number"],
    ["above upper bound", 65_537, "invalid_number", undefined, "number"],
    ["negative", -1, "invalid_number", undefined, "number"],
    ["negative zero", -0, "invalid_number", undefined, "number"],
    ["fraction", 0.5, "invalid_number", undefined, "number"],
    ["unsafe integer", Number.MAX_SAFE_INTEGER + 1, "invalid_number", undefined, "number"],
    ["numeric string", "1", "not_counter", undefined, "string"],
    ["boolean", true, "not_counter", undefined, "boolean"],
    ["null", null, "not_counter", undefined, "null"],
    ["object", {}, "not_counter", undefined, "object"],
    ["array", [], "not_counter", undefined, "array"],
  ];
  for (const [name, value, counterStatus, retainedValue, jsonType] of cases) {
    await t.test(name, () => {
      const input = { image_gen: { input_tokens: value } };
      const result = observeToolUsage(input, BINDING);
      assertSuccess(result);
      const entry = pathEntry(result, "image_gen.input_tokens");
      assert.equal(entry.counterStatus, counterStatus);
      assert.equal(entry.jsonType, jsonType);
      if (counterStatus === "captured") {
        assert.equal(Object.is(entry.value, retainedValue), true);
      } else {
        assert.equal(Object.hasOwn(entry, "value"), false);
      }
    });
  }
});

test("invalid numeric payloads and replacement strings cannot influence canonical observation bytes", () => {
  const invalidNumbers = [65_537, -1, -0, 0.5, Number.MAX_SAFE_INTEGER + 1];
  const invalidOutputs = invalidNumbers.map((value) => JSON.stringify(
    observeToolUsage({ image_gen: { input_tokens: value } }, BINDING),
  ));
  assert.equal(new Set(invalidOutputs).size, 1);

  const stringOutputs = [
    "private-string-canary-short",
    "private-string-canary-replacement-with-a-different-length",
  ].map((value) => JSON.stringify(
    observeToolUsage({ image_gen: { input_tokens: value } }, BINDING),
  ));
  assert.equal(new Set(stringOutputs).size, 1);
  assert.equal(stringOutputs[0].includes("private-string-canary"), false);

  const booleanOutputs = [true, false].map((value) => JSON.stringify(
    observeToolUsage({ image_gen: { input_tokens: value } }, BINDING),
  ));
  assert.equal(new Set(booleanOutputs).size, 1);
});

test("unknown keys, values, and their hashes cannot escape the redacted summary", () => {
  const secretKey = "private-key-canary-6c2f74a1";
  const secretValue = "private-placeholder-value-canary-1089";
  const input = {
    [secretKey]: {
      nested: secretValue,
      hash_bait: sha256(secretValue),
      control_bait: `prefix\u0000${secretValue}`,
    },
  };
  const before = structuredClone(input);
  const result = observeToolUsage(input, BINDING);
  assertSuccess(result);
  assert.equal(result.coverage, "redacted");
  assert.deepEqual(input, before);
  const serialized = JSON.stringify(result);
  for (const forbidden of [
    secretKey,
    secretValue,
    sha256(secretKey),
    sha256(secretValue),
    "private.invalid",
  ]) {
    assert.equal(serialized.includes(forbidden), false, `leaked ${forbidden}`);
  }
});

test("unknown key replacement, string replacement, and key reordering preserve exact output bytes", () => {
  const first = {
    unknown_alpha: { nested_alpha: "secret-alpha", count_alpha: 7 },
    image_gen: { total_tokens: 12, input_tokens: 11 },
    web_search: { num_requests: 13 },
  };
  const second = {
    web_search: { num_requests: 13 },
    image_gen: { input_tokens: 11, total_tokens: 12 },
    unknown_beta: { count_beta: 99, nested_beta: "secret-beta-replacement" },
  };
  const firstResult = observeToolUsage(first, BINDING);
  const secondResult = observeToolUsage(second, BINDING);
  assertSuccess(firstResult);
  assertSuccess(secondResult);
  assert.deepEqual(canonicalBytes(firstResult), canonicalBytes(secondResult));
  for (const bait of ["alpha", "beta", "secret", "99"]) {
    assert.equal(JSON.stringify(secondResult).includes(bait), false);
  }
});

test("the same fixed observation is byte-identical across twenty runs", () => {
  const input = {
    image_gen: { input_tokens: 0, total_tokens: 1 },
    unknown: [{ nested: "replace-me" }, true, null],
  };
  const before = structuredClone(input);
  const outputs = Array.from({ length: 20 }, () => canonicalBytes(observeToolUsage(input, BINDING)));
  assert.ok(outputs.every((output) => Buffer.compare(output, outputs[0]) === 0));
  assert.deepEqual(input, before);
});

test("depth 8 succeeds and depth 9 fails with no partial observation", () => {
  const allowed = observeToolUsage(nestedArrayToDepth(8), BINDING);
  assertSuccess(allowed);
  assert.equal(allowed.summary.maxDepth, 8);

  const rejected = observeToolUsage(nestedArrayToDepth(9), BINDING);
  assertFailure(rejected, "failed_limit");
  assert.notEqual(JSON.stringify(rejected), JSON.stringify(allowed));
});

test("128 nodes succeed and 129 nodes fail with the same fixed limit envelope", () => {
  const allowed = observeToolUsage(Array.from({ length: 127 }, () => null), BINDING);
  assertSuccess(allowed);
  assert.equal(allowed.summary.nodeCount, 128);

  const rejected = observeToolUsage(Array.from({ length: 128 }, () => null), BINDING);
  assertFailure(rejected, "failed_limit");
});

test("32 own keys succeed and 33 own keys fail without publishing accumulated counts", () => {
  const allowed = observeToolUsage(recordWithKeys(32), BINDING);
  assertSuccess(allowed);
  assert.equal(allowed.summary.unknownFieldCount, 32);

  const rejected = observeToolUsage(recordWithKeys(33), BINDING);
  assertFailure(rejected, "failed_limit");
});

test("all resource limit failures are byte-identical and contain no success prefix", () => {
  const failures = [
    observeToolUsage(nestedArrayToDepth(9), BINDING),
    observeToolUsage(Array.from({ length: 128 }, () => null), BINDING),
    observeToolUsage(recordWithKeys(33), BINDING),
  ];
  for (const failure of failures) assertFailure(failure, "failed_limit");
  const serialized = failures.map((value) => JSON.stringify(value));
  assert.equal(new Set(serialized).size, 1);
});

test("high-node and all-vocabulary large-count observations remain under the 8 KiB product bound", () => {
  const highNodeObservation = observeToolUsage(Array.from({ length: 127 }, () => null), BINDING);
  const fullVocabularyObservation = observeToolUsage({
    ...completeKnownCounters(65_536),
    bounded_unknown: recordWithKeys(16),
  }, BINDING);
  assertSuccess(highNodeObservation);
  assertSuccess(fullVocabularyObservation);
  assert.ok(fullVocabularyObservation.paths.every(
    (entry) => entry.counterStatus === "captured" && entry.value === 65_536,
  ));
  assert.equal(fullVocabularyObservation.coverage, "redacted");
  assert.ok(canonicalBytes(highNodeObservation).byteLength <= 8_192);
  assert.ok(canonicalBytes(fullVocabularyObservation).byteLength <= 8_192);
});

test("getters, toJSON, proxies, revoked proxies, cycles, and non-ordinary objects fail without executing code", async (t) => {
  const internalFailures = [];

  await t.test("getter", () => {
    let getterCalls = 0;
    const value = {};
    Object.defineProperty(value, "secret", {
      enumerable: true,
      get() {
        getterCalls += 1;
        throw new Error("getter-secret-canary");
      },
    });
    const result = observeToolUsage(value, BINDING);
    assertFailure(result, "failed_internal");
    assert.equal(getterCalls, 0);
    internalFailures.push(result);
  });

  await t.test("toJSON function", () => {
    let toJsonCalls = 0;
    const value = {
      toJSON() {
        toJsonCalls += 1;
        throw new Error("tojson-secret-canary");
      },
    };
    const result = observeToolUsage(value, BINDING);
    assertFailure(result, "failed_internal");
    assert.equal(toJsonCalls, 0);
    internalFailures.push(result);
  });

  await t.test("proxy", () => {
    let trapCalls = 0;
    const handler = new Proxy({}, {
      get(_target, property) {
        if (property === "get") return () => { trapCalls += 1; throw new Error("proxy-secret-canary"); };
        return () => { trapCalls += 1; throw new Error("proxy-secret-canary"); };
      },
    });
    const value = new Proxy({}, handler);
    const result = observeToolUsage(value, BINDING);
    assertFailure(result, "failed_internal");
    assert.equal(trapCalls, 0);
    internalFailures.push(result);
  });

  await t.test("revoked proxy", () => {
    const { proxy, revoke } = Proxy.revocable({}, {});
    revoke();
    const result = observeToolUsage(proxy, BINDING);
    assertFailure(result, "failed_internal");
    internalFailures.push(result);
  });

  await t.test("cycle", () => {
    const value = {};
    value.self = value;
    const result = observeToolUsage(value, BINDING);
    assertFailure(result, "failed_internal");
    internalFailures.push(result);
  });

  await t.test("prototype-pollution key", () => {
    const value = {};
    Object.defineProperty(value, "__proto__", {
      enumerable: true,
      value: { polluted: "prototype-secret-canary" },
    });
    const result = observeToolUsage(value, BINDING);
    assertFailure(result, "failed_internal");
    internalFailures.push(result);
  });

  await t.test("non-ordinary prototype", () => {
    const value = new Date(0);
    const result = observeToolUsage(value, BINDING);
    assertFailure(result, "failed_internal");
    internalFailures.push(result);
  });

  assert.equal(new Set(internalFailures.map((value) => JSON.stringify(value))).size, 1);
  const serialized = JSON.stringify(internalFailures[0]);
  for (const bait of ["getter-secret", "tojson-secret", "proxy-secret", "self", "field"]) {
    assert.equal(serialized.includes(bait), false);
  }
});

test("every success and failure result is recursively frozen without freezing or changing the input", () => {
  const input = { image_gen: { input_tokens: 1 }, unknown: [null, "redacted"] };
  const inputBefore = structuredClone(input);
  const mutableBinding = { ...BINDING };
  const success = observeToolUsage(input, mutableBinding);
  const failure = observeToolUsage(nestedArrayToDepth(9), mutableBinding);
  assertSuccess(success);
  assertFailure(failure, "failed_limit");
  assertDeepFrozen(success);
  assertDeepFrozen(failure);
  assert.deepEqual(input, inputBefore);
  assert.equal(Object.isFrozen(input), false);
  assert.equal(Object.isFrozen(input.image_gen), false);
  assert.equal(Object.isFrozen(mutableBinding), false);
  assert.throws(() => { success.paths[0].counterStatus = "captured"; }, TypeError);
  assert.throws(() => { failure.binding.callPlanSha256 = "changed"; }, TypeError);
});

test("invalid present options and proxy identities fail internally without executing traps", () => {
  let getterCalls = 0;
  const getterOptions = {};
  Object.defineProperty(getterOptions, "present", {
    enumerable: true,
    get() {
      getterCalls += 1;
      throw new Error("options-getter-secret");
    },
  });

  let optionTrapCalls = 0;
  const proxyOptions = new Proxy({}, {
    get() { optionTrapCalls += 1; throw new Error("options-proxy-secret"); },
    getOwnPropertyDescriptor() { optionTrapCalls += 1; throw new Error("options-proxy-secret"); },
    getPrototypeOf() { optionTrapCalls += 1; throw new Error("options-proxy-secret"); },
    ownKeys() { optionTrapCalls += 1; throw new Error("options-proxy-secret"); },
  });

  const optionFailures = [
    observeToolUsage({}, BINDING, { present: null }),
    observeToolUsage({}, BINDING, { present: undefined }),
    observeToolUsage({}, BINDING, getterOptions),
    observeToolUsage({}, BINDING, proxyOptions),
  ];
  for (const result of optionFailures) assertFailure(result, "failed_internal");
  assert.equal(new Set(optionFailures.map((result) => JSON.stringify(result))).size, 1);
  assert.equal(getterCalls, 0);
  assert.equal(optionTrapCalls, 0);

  let bindingTrapCalls = 0;
  const proxyBinding = new Proxy({ ...BINDING }, {
    get() { bindingTrapCalls += 1; throw new Error("binding-proxy-secret"); },
    getOwnPropertyDescriptor() { bindingTrapCalls += 1; throw new Error("binding-proxy-secret"); },
    getPrototypeOf() { bindingTrapCalls += 1; throw new Error("binding-proxy-secret"); },
    ownKeys() { bindingTrapCalls += 1; throw new Error("binding-proxy-secret"); },
  });
  const bindingFailure = observeToolUsage({}, proxyBinding);
  assertUnboundFailure(bindingFailure, "failed_internal");
  assert.equal(bindingTrapCalls, 0);
  const serialized = JSON.stringify([...optionFailures, bindingFailure]);
  for (const secret of ["options-getter-secret", "options-proxy-secret", "binding-proxy-secret", "network-must-not-run"]) {
    assert.equal(serialized.includes(secret), false);
  }
});

test("capture policy identity is stable across observed and failed outcomes and never derives from secrets", () => {
  const secret = "capture-policy-placeholder-canary-f18d";
  const observed = observeToolUsage({ unknown: secret }, BINDING);
  const limited = observeToolUsage(nestedArrayToDepth(9), BINDING);
  const internal = observeToolUsage(new Date(0), BINDING);
  assertSuccess(observed);
  assertFailure(limited, "failed_limit");
  assertFailure(internal, "failed_internal");
  assert.equal(observed.capturePolicySha256, limited.capturePolicySha256);
  assert.equal(observed.capturePolicySha256, internal.capturePolicySha256);
  assert.notEqual(observed.capturePolicySha256, sha256(secret));
  assert.equal(JSON.stringify(observed).includes(secret), false);
});

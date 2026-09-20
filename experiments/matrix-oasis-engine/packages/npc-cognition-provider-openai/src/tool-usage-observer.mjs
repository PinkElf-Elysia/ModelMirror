import { createHash } from "node:crypto";
import { types } from "node:util";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

function freeze(value) {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

// Observation vocabulary only. Not a supplier schema, fee map, or closed set of tools.
export const R22_TOOL_USAGE_CAPTURE_POLICY = freeze({
  profile: "matrix-oasis.r22-tool-usage-diagnostic/1",
  semanticCoverage: "observation_only",
  qualificationEligible: false,
  counterPaths: [
    "image_gen.input_tokens",
    "image_gen.input_tokens_details.image_tokens",
    "image_gen.input_tokens_details.text_tokens",
    "image_gen.output_tokens",
    "image_gen.output_tokens_details.image_tokens",
    "image_gen.output_tokens_details.text_tokens",
    "image_gen.total_tokens",
    "web_search.num_requests",
  ],
  limits: { maximumDepth: 8, maximumNodes: 128, maximumObjectKeys: 32,
    maximumCounter: 65536, maximumObservationBytes: 8192 },
  unknownFields: "names_and_values_redacted",
  invalidCounters: "category_only",
});
export const R22_TOOL_USAGE_CAPTURE_POLICY_SHA256 = `sha256:${createHash("sha256")
  .update(canonicalizeJsonValue(R22_TOOL_USAGE_CAPTURE_POLICY), "utf8").digest("hex")}`;

const HASH = /^sha256:[0-9a-f]{64}$/u;
const LIMIT = Symbol("limit");
const INVALID = Symbol("invalid");
const PATHS = R22_TOOL_USAGE_CAPTURE_POLICY.counterPaths;
const PREFIXES = new Set(PATHS.flatMap((path) => {
  const parts = path.split(".");
  return parts.map((_, index) => parts.slice(0, index + 1).join("."));
}));
const TYPES = ["null", "object", "array", "string", "number", "boolean"];

function descriptors(value, array = false) {
  // Check before reflective operations: even revoked proxies must execute no traps.
  if (types.isProxy(value)) throw INVALID;
  const prototype = Object.getPrototypeOf(value);
  if (array ? prototype !== Array.prototype : prototype !== Object.prototype && prototype !== null) throw INVALID;
  const result = Object.getOwnPropertyDescriptors(value);
  for (const key of Reflect.ownKeys(result)) {
    const d = result[key];
    if (typeof key !== "string" || !Object.hasOwn(d, "value") || d.get || d.set ||
        (!d.enumerable && !(array && key === "length"))) throw INVALID;
    if (["__proto__", "prototype", "constructor"].includes(key)) throw INVALID;
  }
  return result;
}

function safeBinding(binding) {
  if (!binding || typeof binding !== "object" || types.isProxy(binding) || Array.isArray(binding)) return null;
  const d = descriptors(binding), keys = Object.keys(d);
  if (keys.length !== 2 || !keys.includes("callPlanSha256") || !keys.includes("diagnosticApprovalSha256")) return null;
  if (![d.callPlanSha256.value, d.diagnosticApprovalSha256.value]
    .every((value) => typeof value === "string" && HASH.test(value))) return null;
  return { callPlanSha256: d.callPlanSha256.value, diagnosticApprovalSha256: d.diagnosticApprovalSha256.value };
}

function base(binding) {
  return { profile: R22_TOOL_USAGE_CAPTURE_POLICY.profile,
    capturePolicySha256: R22_TOOL_USAGE_CAPTURE_POLICY_SHA256,
    semanticCoverage: "observation_only", qualificationEligible: false,
    ...(binding ? { binding } : {}) };
}

// Private pure observer. The caller must supply an already strictly parsed response
// and approved identity. This function cannot perform I/O, read credentials, persist, or
// authorize anything. On failure discard every partially accumulated observation.
export function observeToolUsage(value, binding, options = {}) {
  let identity = null;
  try {
    identity = safeBinding(binding);
    if (!identity) throw INVALID;
    const optionDescriptors = descriptors(options);
    if (Object.keys(optionDescriptors).some((key) => key !== "present")) throw INVALID;
    const present = Object.hasOwn(optionDescriptors, "present") ? optionDescriptors.present.value : true;
    if (typeof present !== "boolean") throw INVALID;
    const summary = { nodeCount: 0, maxDepth: 0, unknownFieldCount: 0,
      typeCounts: Object.fromEntries(TYPES.map((type) => [type, 0])) };
    const observed = new Map(PATHS.map((path) => [path, { path, jsonType: "absent", counterStatus: "absent" }]));
    const seen = new Set();
    function walk(node, depth, prefix) {
      if (depth > 8 || ++summary.nodeCount > 128) throw LIMIT;
      summary.maxDepth = Math.max(summary.maxDepth, depth);
      if ((typeof node === "object" && node !== null) && types.isProxy(node)) throw INVALID;
      const type = node === null ? "null" : Array.isArray(node) ? "array" : typeof node;
      if (!TYPES.includes(type)) throw INVALID;
      summary.typeCounts[type] += 1;
      if (observed.has(prefix)) {
        const item = { path: prefix, jsonType: type, counterStatus: "not_counter" };
        if (type === "number") {
          item.counterStatus = Number.isSafeInteger(node) && !Object.is(node, -0) && node >= 0 && node <= 65536
            ? "captured" : "invalid_number";
          if (item.counterStatus === "captured") item.value = node;
        }
        observed.set(prefix, item);
      }
      if (prefix !== null && type !== "object") {
        for (const path of PATHS) {
          if (prefix === "" || path.startsWith(`${prefix}.`)) {
            observed.set(path, { path, jsonType: "not-captured", counterStatus: "not_captured" });
          }
        }
      }
      if (type !== "object" && type !== "array") return type;
      if (seen.has(node)) throw INVALID;
      seen.add(node);
      const d = descriptors(node, type === "array");
      if (type === "array") {
        const length = d.length.value;
        if (!Number.isSafeInteger(length) || length < 0 || length > 127) throw LIMIT;
        if (Object.keys(d).length !== length + 1) throw INVALID;
        for (let index = 0; index < length; index += 1) {
          if (!Object.hasOwn(d, String(index))) throw INVALID;
          walk(d[index].value, depth + 1, null);
        }
      } else {
        const keys = Object.keys(d);
        if (keys.length > 32) throw LIMIT;
        for (const key of keys) {
          const fullPath = prefix === null ? null : prefix === "" ? key : `${prefix}.${key}`;
          // Segments are exact object keys. A dotted key at an unknown/root
          // position must never impersonate several approved path segments.
          const known = !key.includes(".") && fullPath !== null && PREFIXES.has(fullPath);
          if (!known) summary.unknownFieldCount += 1;
          walk(d[key].value, depth + 1, known ? fullPath : null);
        }
      }
      seen.delete(node);
      return type;
    }
    const rootType = present ? walk(value, 0, "") : "absent";
    const result = { ...base(identity), status: "observed", rootType,
      coverage: summary.unknownFieldCount > 0 || summary.typeCounts.array > 0 ? "redacted" : "vocabulary_only",
      paths: [...observed.values()], summary };
    if (Buffer.byteLength(canonicalizeJsonValue(result), "utf8") > 8192) throw LIMIT;
    return freeze(result);
  } catch (error) {
    return freeze({ ...base(identity), status: error === LIMIT ? "failed_limit" : "failed_internal" });
  }
}

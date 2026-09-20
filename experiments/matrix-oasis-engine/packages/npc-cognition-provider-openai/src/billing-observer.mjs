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

// A local observation vocabulary, NOT a supplier schema, fee map or acceptance rule.
// Changing this policy requires a new capture disclosure; old approvals do not apply.
export const R22_BILLING_CAPTURE_POLICY = freeze({
  profile: "matrix-oasis.r22-billing-observation/1",
  semanticCoverage: "observation_only",
  qualificationEligible: false,
  paths: ["payer", "amount", "currency", "service_tier", "tool_costs", "tool_costs.total"],
  stringLiterals: { payer: ["developer", "user"], currency: ["usd"], service_tier: ["default"] },
  limits: { maximumDepth: 8, maximumNodes: 128, maximumObjectKeys: 32,
    maximumArrayLength: 127, maximumTextBytes: 65536, maximumObservationBytes: 8192 },
  unknownFields: "names_and_values_redacted",
  numericValues: "never_retained_or_interpreted_as_cost",
  input: "already_strictly_parsed_bounded_json",
  binding: "correlation_only_not_approval_proof",
});
export const R22_BILLING_CAPTURE_POLICY_SHA256 = `sha256:${createHash("sha256")
  .update(canonicalizeJsonValue(R22_BILLING_CAPTURE_POLICY), "utf8").digest("hex")}`;

const HASH = /^sha256:[0-9a-f]{64}$/u;
const LIMIT = Symbol("limit");
const INVALID = Symbol("invalid");
const POLICY = R22_BILLING_CAPTURE_POLICY;
const PATHS = POLICY.paths;
const PREFIXES = new Set(PATHS.flatMap((path) => {
  const parts = path.split(".");
  return parts.map((_, index) => parts.slice(0, index + 1).join("."));
}));
const TYPES = ["null", "object", "array", "string", "number", "boolean"];
const FORBIDDEN_KEYS = new Set(["__proto__", "prototype", "constructor"]);

function descriptors(value, array = false) {
  // Inspect no user property before rejecting proxies (including revoked ones).
  if (!value || typeof value !== "object" || types.isProxy(value)) throw INVALID;
  const prototype = Object.getPrototypeOf(value);
  if (array ? prototype !== Array.prototype : prototype !== Object.prototype && prototype !== null) throw INVALID;
  const keys = Reflect.ownKeys(value);
  if (keys.length > (array ? POLICY.limits.maximumArrayLength + 1 : POLICY.limits.maximumObjectKeys)) throw LIMIT;
  const result = Object.create(null);
  for (const key of keys) {
    if (typeof key !== "string" || FORBIDDEN_KEYS.has(key)) throw INVALID;
    const d = Object.getOwnPropertyDescriptor(value, key);
    if (!d || !Object.hasOwn(d, "value") || (!d.enumerable && !(array && key === "length"))) throw INVALID;
    result[key] = d;
  }
  return result;
}

function safeBinding(binding) {
  const d = descriptors(binding), keys = Object.keys(d);
  if (keys.length !== 2 || !keys.includes("callPlanSha256") || !keys.includes("diagnosticApprovalSha256")) throw INVALID;
  if (![d.callPlanSha256.value, d.diagnosticApprovalSha256.value]
    .every((value) => typeof value === "string" && HASH.test(value))) throw INVALID;
  return { callPlanSha256: d.callPlanSha256.value, diagnosticApprovalSha256: d.diagnosticApprovalSha256.value };
}

function base(binding) {
  return { profile: POLICY.profile, capturePolicySha256: R22_BILLING_CAPTURE_POLICY_SHA256,
    semanticCoverage: "observation_only", qualificationEligible: false,
    ...(binding ? { binding } : {}) };
}

function category(path, type, value) {
  if (type !== "string") return "type_only";
  const literals = Object.hasOwn(POLICY.stringLiterals, path) ? POLICY.stringLiterals[path] : [];
  return literals.includes(value) ? `literal_${value}` : "other_string";
}

// Private, pure diagnostic core. No live wiring or persistence. The caller must
// strictly parse a bounded response first; this is not a duplicate-key/number-lexeme
// validator. Supplied hashes correlate observations but do not authorize capture.
// Never use this output to accept a response, release budget or choose an Action.
export function observeBilling(value, binding, options = {}) {
  let identity = null;
  try {
    identity = safeBinding(binding);
    const optionDescriptors = descriptors(options);
    if (Object.keys(optionDescriptors).some((key) => key !== "present")) throw INVALID;
    const present = Object.hasOwn(optionDescriptors, "present") ? optionDescriptors.present.value : true;
    if (typeof present !== "boolean" || (!present && value !== undefined)) throw INVALID;
    const summary = { nodeCount: 0, maxDepth: 0, unknownFieldCount: 0,
      typeCounts: Object.fromEntries(TYPES.map((type) => [type, 0])) };
    const observed = new Map(PATHS.map((path) => [path, { path, jsonType: "absent", category: "absent" }]));
    const ancestors = new Set();
    let textBytes = 0;
    function checkText(text) {
      // Bound the scan before checking UTF-16. No coercion or user callbacks.
      if (text.length > POLICY.limits.maximumTextBytes) throw LIMIT;
      for (let index = 0; index < text.length; index += 1) {
        const unit = text.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) {
          const next = text.charCodeAt(++index);
          if (!(next >= 0xdc00 && next <= 0xdfff)) throw INVALID;
        } else if (unit >= 0xdc00 && unit <= 0xdfff) throw INVALID;
      }
      textBytes += Buffer.byteLength(text, "utf8");
      if (textBytes > POLICY.limits.maximumTextBytes) throw LIMIT;
    }
    function walk(node, depth, prefix) {
      if (depth > POLICY.limits.maximumDepth || ++summary.nodeCount > POLICY.limits.maximumNodes) throw LIMIT;
      summary.maxDepth = Math.max(summary.maxDepth, depth);
      if (node !== null && typeof node === "object" && types.isProxy(node)) throw INVALID;
      const type = node === null ? "null" : Array.isArray(node) ? "array" : typeof node;
      if (!TYPES.includes(type) || (type === "number" && !Number.isFinite(node))) throw INVALID;
      if (type === "string") checkText(node);
      summary.typeCounts[type] += 1;
      if (observed.has(prefix)) observed.set(prefix, { path: prefix, jsonType: type, category: category(prefix, type, node) });
      if (prefix !== null && type !== "object") {
        for (const path of PATHS) {
          if (prefix === "" || path.startsWith(`${prefix}.`)) {
            observed.set(path, { path, jsonType: "not-captured", category: "not_captured" });
          }
        }
      }
      if (type !== "object" && type !== "array") return type;
      if (ancestors.has(node)) throw INVALID;
      ancestors.add(node);
      const d = descriptors(node, type === "array");
      if (type === "array") {
        const length = d.length.value;
        if (!Number.isSafeInteger(length) || length < 0 || length > POLICY.limits.maximumArrayLength) throw LIMIT;
        if (Object.keys(d).length !== length + 1) throw INVALID;
        for (let index = 0; index < length; index += 1) {
          if (!Object.hasOwn(d, String(index))) throw INVALID;
          walk(d[index].value, depth + 1, null);
        }
      } else {
        for (const key of Object.keys(d)) {
          checkText(key);
          const fullPath = prefix === null ? null : prefix === "" ? key : `${prefix}.${key}`;
          // Literal dotted keys and nested unknown branches never impersonate paths.
          const known = !key.includes(".") && fullPath !== null && PREFIXES.has(fullPath);
          if (!known) summary.unknownFieldCount += 1;
          walk(d[key].value, depth + 1, known ? fullPath : null);
        }
      }
      ancestors.delete(node);
      return type;
    }
    const rootType = present ? walk(value, 0, "") : "absent";
    const result = { ...base(identity), status: "observed", rootType, paths: [...observed.values()], summary };
    if (Buffer.byteLength(canonicalizeJsonValue(result), "utf8") > POLICY.limits.maximumObservationBytes) throw LIMIT;
    return freeze(result);
  } catch (error) {
    // Discard all partial values, names, paths, counters and exception information.
    return freeze({ ...base(identity), status: error === LIMIT ? "failed_limit" : "failed_internal" });
  }
}

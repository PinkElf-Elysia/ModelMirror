import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { V2_CANDIDATE_LOCK_SCHEMA, V2_QUALIFICATION_LIMITS, V2_QUALIFICATION_REPORT_SCHEMA } from "./schema.mjs";

const ajv = new Ajv2020({ strict: true, allErrors: true, ownProperties: true, coerceTypes: false, useDefaults: false, removeAdditional: false, validateFormats: false });
const validators = Object.freeze({ lock: ajv.compile(V2_CANDIDATE_LOCK_SCHEMA), report: ajv.compile(V2_QUALIFICATION_REPORT_SCHEMA) });

function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

function item(phase, code, path = "") {
  return { phase, severity: "error", code, path, message: code };
}

function result(diagnostics, value) {
  const unique = [];
  const seen = new Set();
  for (const diagnostic of [...diagnostics].sort((a, b) => a.path.localeCompare(b.path) || a.code.localeCompare(b.code))) {
    const key = `${diagnostic.path}\0${diagnostic.code}`;
    if (!seen.has(key)) { seen.add(key); unique.push(freeze({ ...diagnostic })); }
  }
  return freeze({ reportVersion: 1, valid: unique.length === 0, diagnostics: unique, ...(unique.length === 0 ? { value: freeze(value) } : {}) });
}

function strictParse(text, prefix) {
  if (typeof text !== "string") return { diagnostics: [item("parse", `${prefix}_JSON_INPUT_TYPE`)] };
  if (new TextEncoder().encode(text).byteLength > V2_QUALIFICATION_LIMITS.documentBytes) return { diagnostics: [item("parse", `${prefix}_JSON_SIZE_EXCEEDED`)] };
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (const character of text) {
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
    } else if (character === '"') inString = true;
    else if (character === "{" || character === "[") { depth += 1; if (depth > V2_QUALIFICATION_LIMITS.documentDepth) return { diagnostics: [item("parse", `${prefix}_JSON_DEPTH_EXCEEDED`)] }; }
    else if (character === "}" || character === "]") depth -= 1;
  }
  const errors = [];
  const value = parse(text, errors, { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (errors.length > 0 || value === undefined) return { diagnostics: [item("parse", `${prefix}_JSON_SYNTAX`)] };
  const tree = parseTree(text, [], { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  const stack = tree ? [{ node: tree, path: "" }] : [];
  const duplicates = [];
  while (stack.length > 0) {
    const { node, path } = stack.pop();
    if (node.type === "object") {
      const keys = new Set();
      for (const property of node.children ?? []) {
        const key = property.children?.[0]?.value;
        const child = property.children?.[1];
        if (typeof key !== "string" || !child) continue;
        if (keys.has(key)) duplicates.push(item("parse", `${prefix}_JSON_DUPLICATE_KEY`, path));
        keys.add(key);
        stack.push({ node: child, path: `${path}/${key.replaceAll("~", "~0").replaceAll("/", "~1")}` });
      }
    } else if (node.type === "array") (node.children ?? []).forEach((child, index) => stack.push({ node: child, path: `${path}/${index}` }));
  }
  return duplicates.length > 0 ? { diagnostics: duplicates } : { value };
}

function wellFormed(value) {
  const stack = [value];
  while (stack.length > 0) {
    const current = stack.pop();
    if (typeof current === "string") {
      for (let index = 0; index < current.length; index += 1) {
        const unit = current.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) { const next = current.charCodeAt(index + 1); if (!(next >= 0xdc00 && next <= 0xdfff)) return false; index += 1; }
        else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
      }
    } else if (Array.isArray(current)) stack.push(...current);
    else if (current && typeof current === "object") stack.push(...Object.keys(current), ...Object.values(current));
  }
  return true;
}

function schemaDiagnostics(validator, prefix) {
  const suffix = { required: "REQUIRED", additionalProperties: "UNKNOWN_PROPERTY", type: "TYPE", const: "CONST", enum: "ENUM", pattern: "STRING_CONSTRAINT", minLength: "STRING_CONSTRAINT", maxLength: "STRING_CONSTRAINT", minimum: "NUMBER_CONSTRAINT", maximum: "NUMBER_CONSTRAINT", uniqueItems: "ARRAY_CONSTRAINT", minItems: "ARRAY_CONSTRAINT", maxItems: "ARRAY_CONSTRAINT" };
  return (validator.errors ?? []).map((error) => item("schema", `${prefix}_SCHEMA_${suffix[error.keyword] ?? "INVALID"}`, error.keyword === "required" ? `${error.instancePath}/${error.params.missingProperty}` : error.instancePath));
}

function validate(text, kind) {
  const prefix = kind === "lock" ? "V2_CANDIDATE_LOCK" : "V2_QUALIFICATION_REPORT";
  try {
    const parsed = strictParse(text, prefix);
    if (parsed.diagnostics) return result(parsed.diagnostics);
    if (!wellFormed(parsed.value)) return result([item("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`)]);
    const validator = validators[kind];
    if (!validator(parsed.value)) return result(schemaDiagnostics(validator, prefix));
    if (canonicalizeJsonValue(parsed.value) !== text) return result([item("integrity", `${prefix}_JSON_NON_CANONICAL`)]);
    return result([], parsed.value);
  } catch {
    return result([item("operation", `${prefix}_INTERNAL_ERROR`)]);
  }
}

export function validateV2CandidateLockJson(text) { return validate(text, "lock"); }
export function validateV2QualificationReportJson(text) { return validate(text, "report"); }

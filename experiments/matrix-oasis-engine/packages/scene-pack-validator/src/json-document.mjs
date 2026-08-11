import { parseTree } from "jsonc-parser";
import { SCENE_PACK_SCHEMA } from "@matrix-oasis/scene-pack-contracts";
import { diagnostic, pointer } from "./diagnostics.mjs";

const OPTIONS = Object.freeze({disallowComments: true, allowTrailingComma: false, allowEmptyContent: false});

function depthExceeded(text) {
  let depth = 0; let string = false; let escaped = false;
  for (const char of text) {
    if (string) { if (escaped) escaped = false; else if (char === "\\") escaped = true; else if (char === '"') string = false; continue; }
    if (char === '"') string = true;
    else if (char === "{" || char === "[") { depth += 1; if (depth > 256) return true; }
    else if (char === "}" || char === "]") depth -= 1;
  }
  return false;
}

function localRef(schema, ref) {
  if (typeof ref !== "string" || !ref.startsWith("#/")) return undefined;
  let current = schema;
  for (const raw of ref.slice(2).split("/")) { const key = raw.replaceAll("~1", "/").replaceAll("~0", "~"); if (!current || typeof current !== "object" || !Object.hasOwn(current, key)) return undefined; current = current[key]; }
  return current;
}
function expand(candidates) {
  const result = []; const pending = [...candidates]; const seen = new Set();
  while (pending.length) { const item = pending.pop(); if (!item || typeof item !== "object" || seen.has(item)) continue; seen.add(item); result.push(item); const ref = localRef(SCENE_PACK_SCHEMA, item.$ref); if (ref) pending.push(ref); for (const key of ["allOf", "anyOf", "oneOf"]) if (Array.isArray(item[key])) pending.push(...item[key]); }
  return result;
}
function propertyCandidates(candidates, name) { const result = []; for (const schema of expand(candidates)) if (schema.properties && Object.hasOwn(schema.properties, name)) result.push(schema.properties[name]); return result; }
function itemCandidates(candidates) { const result = []; for (const schema of expand(candidates)) if (schema.items && typeof schema.items === "object") result.push(schema.items); return result; }

function duplicateDiagnostics(root) {
  const output = []; const pending = [{node: root, path: "/scenePack", schemas: [SCENE_PACK_SCHEMA]}];
  while (pending.length) {
    const {node, path, schemas} = pending.pop();
    if (node.type === "object") {
      const first = new Map();
      for (const property of node.children ?? []) {
        const [keyNode, valueNode] = property.children ?? []; if (!keyNode || !valueNode) continue;
        const children = propertyCandidates(schemas, keyNode.value); const childPath = children.length ? pointer(path, keyNode.value) : path;
        if (first.has(keyNode.value)) output.push(diagnostic("parse", "SCENE_PACK_JSON_DUPLICATE_KEY", childPath, first.get(keyNode.value))); else first.set(keyNode.value, childPath);
        pending.push({node: valueNode, path: childPath, schemas: children});
      }
    } else if (node.type === "array") {
      const children = itemCandidates(schemas); for (const [index, child] of (node.children ?? []).entries()) pending.push({node: child, path: children.length ? pointer(path, index) : path, schemas: children});
    }
  }
  return output;
}

export function parseSceneJson(text) {
  if (typeof text !== "string") return {diagnostics: [diagnostic("parse", "SCENE_PACK_JSON_INPUT_TYPE", "/scenePack")]};
  if (new TextEncoder().encode(text).byteLength > 262144) return {diagnostics: [diagnostic("parse", "SCENE_PACK_JSON_SIZE_LIMIT", "/scenePack")]};
  if (text.charCodeAt(0) === 0xfeff) return {diagnostics: [diagnostic("parse", "SCENE_PACK_JSON_SYNTAX", "/scenePack")]};
  if (depthExceeded(text)) return {diagnostics: [diagnostic("parse", "SCENE_PACK_JSON_DEPTH_EXCEEDED", "/scenePack")]};
  const errors = []; const root = parseTree(text, errors, OPTIONS);
  if (!root || errors.length) return {diagnostics: [diagnostic("parse", "SCENE_PACK_JSON_SYNTAX", "/scenePack")]};
  const duplicates = duplicateDiagnostics(root); if (duplicates.length) return {diagnostics: duplicates};
  try { return {value: JSON.parse(text), diagnostics: []}; } catch { return {diagnostics: [diagnostic("parse", "SCENE_PACK_JSON_SYNTAX", "/scenePack")]}; }
}

import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { PROTOTYPE_SPATIAL_SOLUTION_LIMITS, PROTOTYPE_SPATIAL_SOLUTION_SCHEMA } from "./schema.mjs";

const INTERNAL_CODE = "PROTOTYPE_SPATIAL_SOLUTION_CONTRACT_INTERNAL_ERROR";
const PHASE_ORDER = Object.freeze({ parse: 0, schema: 1, semantic: 2, integrity: 3 });
export class PrototypeSpatialSolutionContractOperationalError extends Error {
  constructor() { super(INTERNAL_CODE); this.name = "PrototypeSpatialSolutionContractOperationalError"; this.code = INTERNAL_CODE; }
}
function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}
function token(value) { return String(value).replaceAll("~", "~0").replaceAll("/", "~1"); }
function at(path, value) { return `${path}/${token(value)}`; }
function diagnostic(phase, code, path) { return { phase, severity: "error", code, path, message: code }; }
function compare(left, right) {
  const text = (a, b) => (a === b ? 0 : a < b ? -1 : 1);
  return (PHASE_ORDER[left.phase] - PHASE_ORDER[right.phase]) || text(left.path, right.path) || text(left.code, right.code);
}
function report(items) {
  const seen = new Set(); const diagnostics = [];
  for (const item of [...items].sort(compare)) {
    const key = `${item.phase}\0${item.path}\0${item.code}`;
    if (!seen.has(key)) { seen.add(key); diagnostics.push(deepFreeze({ ...item })); }
  }
  return deepFreeze({ reportVersion: 1, valid: diagnostics.length === 0, diagnostics });
}
function depthExceeded(text) {
  let depth = 0; let string = false; let escaped = false;
  for (const character of text) {
    if (string) { if (escaped) escaped = false; else if (character === "\\") escaped = true; else if (character === '"') string = false; }
    else if (character === '"') string = true;
    else if (character === "{" || character === "[") { depth += 1; if (depth > PROTOTYPE_SPATIAL_SOLUTION_LIMITS.documentDepth) return true; }
    else if (character === "}" || character === "]") depth -= 1;
  }
  return false;
}
function duplicateDiagnostics(text) {
  const root = parseTree(text, [], { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (!root) return [];
  const output = []; const stack = [{ node: root, path: "" }];
  while (stack.length > 0) {
    const { node, path } = stack.pop();
    if (node.type === "object") {
      const keys = new Set();
      for (const property of node.children ?? []) {
        const key = property.children?.[0]; const child = property.children?.[1];
        if (!key || !child) continue;
        if (keys.has(key.value)) output.push(diagnostic("parse", "PROTOTYPE_SPATIAL_SOLUTION_JSON_DUPLICATE_KEY", path));
        keys.add(key.value); stack.push({ node: child, path: at(path, key.value) });
      }
    } else if (node.type === "array") {
      for (let index = (node.children?.length ?? 0) - 1; index >= 0; index -= 1) stack.push({ node: node.children[index], path: at(path, index) });
    }
  }
  return output;
}
function parseDocument(text) {
  if (typeof text !== "string") return { diagnostics: [diagnostic("parse", "PROTOTYPE_SPATIAL_SOLUTION_JSON_INPUT_TYPE", "")] };
  if (new TextEncoder().encode(text).byteLength > PROTOTYPE_SPATIAL_SOLUTION_LIMITS.documentBytes) return { diagnostics: [diagnostic("parse", "PROTOTYPE_SPATIAL_SOLUTION_JSON_SIZE_EXCEEDED", "")] };
  if (depthExceeded(text)) return { diagnostics: [diagnostic("parse", "PROTOTYPE_SPATIAL_SOLUTION_JSON_DEPTH_EXCEEDED", "")] };
  const errors = [];
  const value = parse(text, errors, { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (errors.length > 0 || value === undefined) return { diagnostics: [diagnostic("parse", "PROTOTYPE_SPATIAL_SOLUTION_JSON_SYNTAX", "")] };
  const duplicates = duplicateDiagnostics(text);
  return duplicates.length > 0 ? { diagnostics: duplicates } : { value };
}

const ajv = new Ajv2020({ strict: true, allErrors: true, coerceTypes: false, useDefaults: false, removeAdditional: false, ownProperties: true, validateFormats: false });
const validateStructure = ajv.compile(PROTOTYPE_SPATIAL_SOLUTION_SCHEMA);
function schemaDiagnostics(value) {
  if (validateStructure(value)) return [];
  const suffix = { required: "REQUIRED", additionalProperties: "UNKNOWN_PROPERTY", type: "TYPE", const: "CONST", enum: "ENUM", minItems: "MIN_ITEMS", maxItems: "MAX_ITEMS", uniqueItems: "DUPLICATE_ITEM", minimum: "NUMBER_CONSTRAINT", maximum: "NUMBER_CONSTRAINT", minLength: "STRING_CONSTRAINT", maxLength: "STRING_CONSTRAINT", pattern: "STRING_CONSTRAINT" };
  return (validateStructure.errors ?? []).map((error) => diagnostic("schema", `PROTOTYPE_SPATIAL_SOLUTION_SCHEMA_${suffix[error.keyword] ?? "INVALID"}`, error.keyword === "required" ? at(error.instancePath, error.params.missingProperty) : error.instancePath));
}
function wellFormed(value) {
  const stack = [value];
  while (stack.length > 0) {
    const current = stack.pop();
    if (typeof current === "string") {
      for (let index = 0; index < current.length; index += 1) {
        const unit = current.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) {
          const next = current.charCodeAt(index + 1); if (!(next >= 0xdc00 && next <= 0xdfff)) return false; index += 1;
        } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
      }
    } else if (Array.isArray(current)) stack.push(...current);
    else if (current && typeof current === "object") stack.push(...Object.keys(current), ...Object.values(current));
  }
  return true;
}
function duplicate(items, field, path, code) {
  const output = []; const seen = new Set();
  items.forEach((item, index) => { if (seen.has(item[field])) output.push(diagnostic("semantic", code, `${path}/${index}/${field}`)); seen.add(item[field]); });
  return output;
}
function semanticDiagnostics(value) {
  const output = [];
  const seeds = new Map(value.navigation.zoneSeeds.map((item) => [item.zoneId, item]));
  const domains = new Map(value.navigation.zoneDomains.map((item) => [item.zoneId, item]));
  output.push(
    ...duplicate(value.navigation.zoneSeeds, "zoneId", "/navigation/zoneSeeds", "PROTOTYPE_SPATIAL_SOLUTION_ZONE_DUPLICATE"),
    ...duplicate(value.navigation.zoneDomains, "zoneId", "/navigation/zoneDomains", "PROTOTYPE_SPATIAL_SOLUTION_ZONE_DUPLICATE"),
    ...duplicate(value.placements, "placementId", "/placements", "PROTOTYPE_SPATIAL_SOLUTION_PLACEMENT_DUPLICATE"),
    ...duplicate(value.nodeContexts, "nodeId", "/nodeContexts", "PROTOTYPE_SPATIAL_SOLUTION_NODE_CONTEXT_DUPLICATE"),
  );
  value.navigation.zoneDomains.forEach((domain, index) => {
    if (domain.componentIndex !== value.navigation.componentIndex) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_COMPONENT_MISMATCH", `/navigation/zoneDomains/${index}/componentIndex`));
    if (!seeds.has(domain.zoneId)) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_ZONE_SEED_MISSING", `/navigation/zoneDomains/${index}/zoneId`));
    else if (!domain.floorAnchorIds.includes(seeds.get(domain.zoneId).floorAnchorId)) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_ZONE_SEED_OUTSIDE_DOMAIN", `/navigation/zoneDomains/${index}/floorAnchorIds`));
  });
  value.navigation.zoneSeeds.forEach((seed, index) => { if (!domains.has(seed.zoneId)) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_ZONE_DOMAIN_MISSING", `/navigation/zoneSeeds/${index}/zoneId`)); });
  const placements = new Set(value.placements.map((item) => item.placementId));
  value.nodeContexts.forEach((context, index) => {
    const root = `/nodeContexts/${index}`; const domain = domains.get(context.zoneId);
    if (!domain) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_ZONE_REFERENCE_NOT_FOUND", `${root}/zoneId`));
    context.visiblePlacementIds.forEach((id, itemIndex) => { if (!placements.has(id)) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_PLACEMENT_REFERENCE_NOT_FOUND", `${root}/visiblePlacementIds/${itemIndex}`)); });
    if (!context.approachPathFloorAnchorIds.includes(context.actionTerminal.approachFloorAnchorId)) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_APPROACH_EVIDENCE_MISSING", `${root}/approachPathFloorAnchorIds`));
    if (domain && !domain.floorAnchorIds.includes(context.playerSpawn.floorAnchorId)) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_SPAWN_OUTSIDE_DOMAIN", `${root}/playerSpawn/floorAnchorId`));
    if (domain && !domain.floorAnchorIds.includes(context.actionTerminal.floorAnchorId)) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_TERMINAL_OUTSIDE_DOMAIN", `${root}/actionTerminal/floorAnchorId`));
    if (domain && context.approachPathFloorAnchorIds.some((id) => !domain.floorAnchorIds.includes(id))) output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_APPROACH_OUTSIDE_DOMAIN", `${root}/approachPathFloorAnchorIds`));
    const columns = Math.max(1, Math.min(8, context.actionTerminal.actionCount));
    const rows = Math.max(1, Math.ceil(context.actionTerminal.actionCount / 8));
    const expectedWidth = 1250 + ((columns - 1) * 1700);
    const expectedDepth = 500 + ((rows - 1) * 2250);
    const expectedOffset = [0, -2400 - (((rows - 1) * 2250) / 2)];
    if (context.actionTerminal.footprint.layoutWidthMm !== expectedWidth || context.actionTerminal.footprint.layoutDepthMm !== expectedDepth ||
      context.actionTerminal.footprint.layoutCenterOffsetMm[0] !== expectedOffset[0] || context.actionTerminal.footprint.layoutCenterOffsetMm[1] !== expectedOffset[1]) {
      output.push(diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_TERMINAL_FOOTPRINT_MISMATCH", `${root}/actionTerminal/footprint`));
    }
  });
  return output;
}
export function validatePrototypeSpatialSolutionJson(text) {
  try {
    const parsed = parseDocument(text);
    if (parsed.diagnostics) return report(parsed.diagnostics);
    const structure = schemaDiagnostics(parsed.value); if (structure.length > 0) return report(structure);
    if (!wellFormed(parsed.value)) return report([diagnostic("semantic", "PROTOTYPE_SPATIAL_SOLUTION_TEXT_UNPAIRED_SURROGATE", "")]);
    const semantics = semanticDiagnostics(parsed.value); if (semantics.length > 0) return report(semantics);
    if (canonicalizeJsonValue(parsed.value) !== text) return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_SOLUTION_JSON_NON_CANONICAL", "")]);
    return report([]);
  } catch (error) {
    if (error instanceof PrototypeSpatialSolutionContractOperationalError) throw error;
    throw new PrototypeSpatialSolutionContractOperationalError();
  }
}

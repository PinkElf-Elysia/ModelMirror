import Ajv2020 from "ajv/dist/2020.js";

export const SOURCE_SELECTION_FORMAT = "modelmirror.ai-rpg.source-selection";
export const SOURCE_SELECTION_VERSION = "0.1.0";
export const SOURCE_SELECTION_SCHEMA = Object.freeze({
  $schema: "https://json-schema.org/draft/2020-12/schema", type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "worlds", "commonTalentNames"],
  properties: {
    format: { const: SOURCE_SELECTION_FORMAT }, formatVersion: { const: SOURCE_SELECTION_VERSION },
    worlds: { type: "array", maxItems: 2, items: { type: "object", additionalProperties: false,
      required: ["name", "identityNames", "talentNames"], properties: {
        name: { type: "string", minLength: 1 }, identityNames: { type: "array", maxItems: 2, items: { type: "string", minLength: 1 } },
        talentNames: { type: "array", maxItems: 8, items: { type: "string", minLength: 1 } }
      } } },
    commonTalentNames: { type: "array", maxItems: 8, items: { type: "string", minLength: 1 } }
  }
});
const ajv = new Ajv2020({ allErrors: true, strict: true });
export const validateSourceSelectionSchema = ajv.compile(SOURCE_SELECTION_SCHEMA);

export function diagnostic(phase, code, path, relatedPath) {
  const value = { phase, severity: "error", code, path };
  if (relatedPath !== undefined) value.relatedPath = relatedPath;
  return Object.freeze(value);
}
export function sortDiagnostics(values) {
  return Object.freeze([...values].sort((a, b) => [a.phase, a.severity, a.code, a.path, a.relatedPath ?? ""].join("\u0000").localeCompare([b.phase, b.severity, b.code, b.path, b.relatedPath ?? ""].join("\u0000"))).map((value) => Object.freeze({ ...value })));
}
export function escapePointer(value) { return String(value).replaceAll("~", "~0").replaceAll("/", "~1"); }

export function inspectPlainJson(value, limits = {}) {
  const diagnostics = [], seen = new Set(), stack = [{ value, path: "", depth: 0 }];
  const maxDepth = limits.maxDepth ?? 32, maxNodes = limits.maxNodes ?? 4096;
  let count = 0;
  while (stack.length) {
    const current = stack.pop();
    if (++count > maxNodes) { diagnostics.push(diagnostic("selection", "SELECTION_TOO_COMPLEX", "")); break; }
    if (current.depth > maxDepth) { diagnostics.push(diagnostic("selection", "SELECTION_TOO_DEEP", current.path)); continue; }
    const candidate = current.value;
    if (candidate === null || typeof candidate === "string" || typeof candidate === "boolean" || typeof candidate === "number" && Number.isFinite(candidate)) continue;
    if (typeof candidate !== "object") { diagnostics.push(diagnostic("selection", "SELECTION_NOT_JSON", current.path)); continue; }
    if (seen.has(candidate)) { diagnostics.push(diagnostic("selection", "SELECTION_CYCLE", current.path)); continue; }
    seen.add(candidate);
    if (Array.isArray(candidate)) {
      if (Object.getOwnPropertySymbols(candidate).length) diagnostics.push(diagnostic("selection", "SELECTION_SYMBOL_PROPERTY", current.path));
      const names = Object.getOwnPropertyNames(candidate);
      for (const name of names) if (name !== "length" && !/^(?:0|[1-9][0-9]*)$/u.test(name)) diagnostics.push(diagnostic("selection", "SELECTION_ARRAY_EXTRA_PROPERTY", current.path + "/" + escapePointer(name)));
      for (let index = candidate.length - 1; index >= 0; index--) {
        const childPath = current.path + "/" + index, descriptor = Object.getOwnPropertyDescriptor(candidate, String(index));
        if (!descriptor) diagnostics.push(diagnostic("selection", "SELECTION_ARRAY_HOLE", childPath));
        else if (descriptor.get || descriptor.set) diagnostics.push(diagnostic("selection", "SELECTION_ACCESSOR", childPath));
        else stack.push({ value: descriptor.value, path: childPath, depth: current.depth + 1 });
      }
      continue;
    }
    if (Object.getPrototypeOf(candidate) !== Object.prototype) { diagnostics.push(diagnostic("selection", "SELECTION_NON_PLAIN_OBJECT", current.path)); continue; }
    if (Object.getOwnPropertySymbols(candidate).length) diagnostics.push(diagnostic("selection", "SELECTION_SYMBOL_PROPERTY", current.path));
    for (const key of Object.getOwnPropertyNames(candidate).sort().reverse()) {
      const descriptor = Object.getOwnPropertyDescriptor(candidate, key), childPath = current.path + "/" + escapePointer(key);
      if (!descriptor || descriptor.get || descriptor.set) diagnostics.push(diagnostic("selection", "SELECTION_ACCESSOR", childPath));
      else stack.push({ value: descriptor.value, path: childPath, depth: current.depth + 1 });
    }
  }
  return sortDiagnostics(diagnostics);
}

const id = { type: "string", pattern: "^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$" };
const sha = { type: "string", pattern: "^[a-fA-F0-9]{64}$" };
const text = { type: "string", minLength: 1, maxLength: 1048576 };
const strict = (required, properties) => ({ type: "object", additionalProperties: false, required, properties });
const names = { type: "array", minItems: 1, maxItems: 16, uniqueItems: true, items: { type: "string", minLength: 1, maxLength: 256 } };
const baseRecord = { kind: { enum: ["world", "identity", "talent"] }, worldName: { type: ["string", "null"] }, locator: text, dataSha256: sha, sourceRef: id, stableId: id, aliases: names };
const record = {
  oneOf: [
    strict(["kind", "worldName", "locator", "data", "dataSha256", "sourceRef", "stableId", "aliases"], { ...baseRecord, kind: { const: "world" }, worldName: { type: "string" }, data: strict(["name", "desc", "boss"], { name: text, desc: text, boss: text }) }),
    strict(["kind", "worldName", "locator", "data", "dataSha256", "sourceRef", "stableId", "aliases"], { ...baseRecord, kind: { const: "identity" }, worldName: { type: "string" }, data: strict(["name", "items"], { name: text, items: { oneOf: [text, { type: "array", minItems: 1, maxItems: 64, items: text }] } }) }),
    strict(["kind", "worldName", "locator", "data", "dataSha256", "sourceRef", "stableId", "aliases"], { ...baseRecord, kind: { const: "talent" }, data: strict(["name", "color", "cost", "desc", "type"], { name: text, color: text, cost: { type: "number" }, desc: text, type: text }) })
  ]
};
const source = strict(["id", "kind", "reference", "sha256", "rightsRefs", "hashStatus", "hashConvention"], { id, kind: { enum: ["original", "derived", "authored", "fixture"] }, reference: text, sha256: sha, rightsRefs: { type: "array", minItems: 1, uniqueItems: true, items: id }, hashStatus: { enum: ["tooling_verified_selected_carrier", "registered_unverified"] }, hashConvention: { enum: ["exact-file-bytes", "JSON.stringify(input.authored) UTF-8 no trailing LF"] } });
const simpleAuthored = (bodyName, bodySchema, world = false) => strict(["id", "displayName", bodyName].concat(world ? ["worldRef"] : []), { id, displayName: text, [bodyName]: bodySchema, ...(world ? { worldRef: id } : {}) });
const opening = strict(["id", "displayName", "content", "worldRef", "identityRefs", "talentRefs", "itemRefs", "backgroundRefs", "styleRefs", "worldbookRefs", "informationModuleRefs"], { id, displayName: text, content: text, worldRef: id, identityRefs: { type: "array", minItems: 1, maxItems: 2, uniqueItems: true, items: text }, talentRefs: { type: "array", minItems: 1, maxItems: 8, uniqueItems: true, items: text }, itemRefs: { type: "array", maxItems: 4, uniqueItems: true, items: id }, backgroundRefs: { type: "array", maxItems: 8, uniqueItems: true, items: id }, styleRefs: { type: "array", maxItems: 8, uniqueItems: true, items: id }, worldbookRefs: { type: "array", maxItems: 16, uniqueItems: true, items: id }, informationModuleRefs: { type: "array", maxItems: 8, uniqueItems: true, items: id } });
function deepFreeze(value) { if (value && typeof value === "object") { if (!Object.isFrozen(value)) Object.freeze(value); for (const child of Object.values(value)) deepFreeze(child); } return value; }
deepFreeze(SOURCE_SELECTION_SCHEMA);

export const COMPILE_INPUT_SCHEMA = deepFreeze({
  $schema: "https://json-schema.org/draft/2020-12/schema", type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "recordDataHashConvention", "package", "rights", "sources", "records", "stableIdMap", "items", "configuredRanks", "authored", "defaults"],
  properties: {
    format: { const: "modelmirror.ai-rpg.compile-input" }, formatVersion: { const: "0.1.0" }, recordDataHashConvention: { const: "JSON.stringify(record.data) UTF-8 no trailing LF" },
    package: strict(["id", "version", "displayName", "description", "locale"], { id, version: { const: "0.1.0" }, displayName: text, description: text, locale: { const: "zh-CN" } }),
    rights: strict(["id", "kind", "name", "reference"], { id, kind: { const: "authorization" }, name: text, reference: text }),
    sources: { type: "array", minItems: 2, maxItems: 16, items: source }, records: { type: "array", minItems: 1, maxItems: 64, items: record },
    stableIdMap: { type: "array", minItems: 1, maxItems: 64, items: strict(["id", "kind", "worldScope", "aliases", "sourceLocator", "expectedDataSha256"], { id, kind: { enum: ["world", "identity", "talent"] }, worldScope: { anyOf: [id, { type: "null" }] }, aliases: names, sourceLocator: text, expectedDataSha256: sha }) },
    items: { type: "array", minItems: 4, maxItems: 4, items: strict(["id", "displayName", "description", "identityRef", "sourceRef"], { id, displayName: text, description: text, identityRef: id, sourceRef: id }) },
    configuredRanks: { type: "array", minItems: 4, maxItems: 4, items: strict(["identityRef", "rankLabel", "provenance"], { identityRef: id, rankLabel: text, provenance: { const: "configured" } }) },
    authored: strict(["backgrounds", "styles", "worldbookEntries", "informationModules", "openings"], {
      backgrounds: { type: "array", minItems: 2, maxItems: 8, items: simpleAuthored("description", text, true) }, styles: { type: "array", minItems: 2, maxItems: 8, items: simpleAuthored("instruction", text) },
      worldbookEntries: { type: "array", minItems: 2, maxItems: 16, items: strict(["id", "displayName", "content", "tags", "visibility", "worldRefs"], { id, displayName: text, content: text, tags: { type: "array", maxItems: 16, uniqueItems: true, items: text }, visibility: { enum: ["player", "host", "shared"] }, worldRefs: { type: "array", minItems: 1, uniqueItems: true, items: id } }) },
      informationModules: { type: "array", minItems: 1, maxItems: 8, items: strict(["id", "displayName", "presentation", "description", "fields"], { id, displayName: text, presentation: { enum: ["text", "keyValue", "list", "meter"] }, description: text, fields: { type: "array", minItems: 1, maxItems: 16, items: strict(["id", "label", "valueType"], { id, label: text, valueType: { enum: ["text", "number", "boolean", "list"] } }) } }) },
      openings: { type: "array", minItems: 2, maxItems: 2, items: opening }
    }), defaults: strict(["worldRef", "openingRef"], { worldRef: id, openingRef: id }),
    player: strict(["text", "setupId", "openingRef", "activations", "backgroundRefs"], { text, setupId: id, openingRef: id, activations: { type: "array", maxItems: 16, items: strict(["talentRef", "active"], { talentRef: id, active: { type: "boolean" } }) }, backgroundRefs: { type: "array", maxItems: 16, uniqueItems: true, items: id } })
  }
});
export const CONTENT_INDEX_SCHEMA = deepFreeze(strict(["format", "formatVersion", "packageRef", "entries"], { format: { const: "modelmirror.ai-rpg.content-index" }, formatVersion: { const: "0.1.0" }, packageRef: id, entries: { type: "array", items: strict(["id", "kind", "sourceRefs"], { id, kind: { enum: ["world", "identity", "talent", "item", "background", "style", "worldbookEntry", "opening", "informationModule"] }, sourceRefs: { type: "array", minItems: 1, uniqueItems: true, items: id } }) } }));
const receiptProperties = { format: { const: "modelmirror.ai-rpg.conversion-receipt" }, formatVersion: { const: "0.1.0" }, packageRef: id, sourceRecordCount: { type: "integer", minimum: 1 }, resourceCount: { type: "integer", minimum: 1 }, recordDataHashConvention: text, sourceEvidence: { type: "array", minItems: 1, maxItems: 16, items: strict(["sourceRef", "reference", "sha256", "hashConvention"], { sourceRef: id, reference: text, sha256: sha, hashConvention: text }) }, losses: { type: "array", maxItems: 64, items: text }, warnings: { type: "array", maxItems: 64, items: text } };
const toolingVerification = strict(["converterVersion", "carrierSha256", "authoredSha256", "captureSha256", "selectionSha256", "playerTextSha256", "sourceRecordCount", "fragmentCount", "fullOriginalHtmlStored", "evidence"], { converterVersion: { const: "0.2.0" }, carrierSha256: sha, authoredSha256: sha, captureSha256: sha, selectionSha256: sha, playerTextSha256: { anyOf: [sha, { type: "null" }] }, sourceRecordCount: { type: "integer", minimum: 1 }, fragmentCount: { type: "integer", minimum: 1 }, fullOriginalHtmlStored: { const: false }, evidence: { const: "selected_dom_fragments" } });
export const CONVERSION_RECEIPT_SCHEMA = deepFreeze({ oneOf: [
  strict(["format", "formatVersion", "packageRef", "sourceRecordCount", "resourceCount", "recordDataHashConvention", "sourceEvidence", "hashVerification", "losses", "warnings"], { ...receiptProperties, hashVerification: { const: "tooling_required" } }),
  strict(["format", "formatVersion", "packageRef", "sourceRecordCount", "resourceCount", "recordDataHashConvention", "sourceEvidence", "hashVerification", "toolingVerification", "losses", "warnings"], { ...receiptProperties, hashVerification: { const: "verified_selected_evidence" }, toolingVerification })
] });
export const validateCompileInputSchema = ajv.compile(COMPILE_INPUT_SCHEMA);
export const validateContentIndexSchema = ajv.compile(CONTENT_INDEX_SCHEMA);
export const validateConversionReceiptSchema = ajv.compile(CONVERSION_RECEIPT_SCHEMA);

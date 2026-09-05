import Ajv2020 from "ajv/dist/2020.js";
import { createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import { crc32 } from "node:zlib";
import { validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { validateContentIndexSchema, validateConversionReceiptSchema } from "../content/schemas.mjs";

export const LIMITS = Object.freeze({ files: 64, fileBytes: 2097152, totalBytes: 16777216, zipBytes: 16777216, ratio: 100 });
const ID = "^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$", SHA = "^[a-f0-9]{64}$";
const strict = (required, properties) => ({ type: "object", additionalProperties: false, required, properties });
function deepFreeze(value, seen = new Set()) { if (value && typeof value === "object" && !seen.has(value)) { seen.add(value); for (const child of Object.values(value)) deepFreeze(child, seen); Object.freeze(value); } return value; }
export const BUNDLE_MANIFEST_SCHEMA = deepFreeze({
  $schema: "https://json-schema.org/draft/2020-12/schema", type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "converterVersion", "packageRef", "files"], properties: {
    format: { const: "modelmirror.ai-rpg.bundle-manifest" }, formatVersion: { const: "0.1.0" }, converterVersion: { const: "0.2.0" }, packageRef: { type: "string", pattern: ID },
    files: { type: "array", minItems: 4, maxItems: 63, items: strict(["path", "bytes", "sha256", "crc32"], { path: { type: "string", minLength: 1, maxLength: 512 }, bytes: { type: "integer", minimum: 0, maximum: LIMITS.fileBytes }, sha256: { type: "string", pattern: SHA }, crc32: { type: "integer", minimum: 0, maximum: 4294967295 } }) }
  }
});
const SOURCE_DOCUMENT_SCHEMA = deepFreeze(strict(["format", "formatVersion", "sourceId", "text"], { format: { const: "modelmirror.ai-rpg.source-document" }, formatVersion: { const: "0.1.0" }, sourceId: { type: "string", pattern: ID }, text: { type: "string", maxLength: LIMITS.fileBytes } }));
const ajv = new Ajv2020({ allErrors: true, strict: true });
const validateManifest = ajv.compile(BUNDLE_MANIFEST_SCHEMA), validateSourceDocument = ajv.compile(SOURCE_DOCUMENT_SCHEMA);
const decoder = new TextDecoder("utf-8", { fatal: true });
const FIXED = new Set(["card-package.json", "player-setup.json", "content-index.json", "conversion-receipt.json"]);
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/iu;

export function failure(code, path = "") { return Object.freeze({ valid: false, diagnostics: Object.freeze([Object.freeze({ phase: "archive", severity: "error", code, path })]) }); }
export function success(value) { return Object.freeze({ valid: true, diagnostics: Object.freeze([]), value }); }
export function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function validUnicode(text) {
  for (let index = 0; index < text.length; index++) {
    const unit = text.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) { const next = text.charCodeAt(index + 1); if (!(next >= 0xdc00 && next <= 0xdfff)) return false; index++; }
    else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}
export function canonicalJson(value) {
  const seen = new Set(); let nodes = 0;
  function sort(current, depth = 0) {
    nodes++; if (nodes > 100000 || depth > 64) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" });
    if (current === null || typeof current === "boolean" || typeof current === "number" && Number.isFinite(current)) return current;
    if (typeof current === "string") { if (!validUnicode(current)) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" }); return current; }
    if (Array.isArray(current)) {
      if (seen.has(current) || Reflect.ownKeys(current).some((key) => key !== "length" && (!/^\d+$/u.test(key) || Number(key) >= current.length))) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" });
      seen.add(current); const output = [];
      for (let index = 0; index < current.length; index++) {
        const descriptor = Object.getOwnPropertyDescriptor(current, String(index));
        if (!descriptor || !("value" in descriptor)) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" });
        output.push(sort(descriptor.value, depth + 1));
      }
      seen.delete(current); return output;
    }
    if (!current || typeof current !== "object" || Object.getPrototypeOf(current) !== Object.prototype || seen.has(current)) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" });
    if (Reflect.ownKeys(current).length !== Object.keys(current).length) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" });
    seen.add(current); const output = Object.create(null);
    for (const key of Object.keys(current).sort()) {
      if (!validUnicode(key)) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" });
      const descriptor = Object.getOwnPropertyDescriptor(current, key);
      if (!descriptor || !("value" in descriptor)) throw Object.assign(new Error(), { code: "BUNDLE_NON_JSON" });
      output[key] = sort(descriptor.value, depth + 1);
    }
    seen.delete(current); return output;
  }
  return JSON.stringify(sort(value), null, 2) + "\n";
}
export function safeMemberName(name) {
  if (typeof name !== "string" || name.length < 1 || name.length > 512 || name.includes("\\") || name.startsWith("/") || /^[A-Za-z]:/u.test(name) || /[\u0000-\u001f:]/u.test(name)) return false;
  const parts = name.split("/");
  return parts.every((part) => part && part !== "." && part !== ".." && !part.endsWith(".") && !part.endsWith(" ") && !RESERVED.test(part));
}
function bytesOf(value) { return value instanceof Uint8Array ? Buffer.from(value) : null; }
function nestedArchive(bytes) {
  const hex = bytes.subarray(0, 8).toString("hex");
  return hex.startsWith("504b0304") || hex.startsWith("504b0506") || hex.startsWith("504b0708") || hex.startsWith("1f8b") || hex.startsWith("52617221") || hex.startsWith("377abcaf271c");
}
function decode(bytes, member) {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return failure("BUNDLE_UTF8_BOM", "/" + member);
  let text; try { text = decoder.decode(bytes); } catch { return failure("BUNDLE_UTF8_INVALID", "/" + member); }
  if (text.includes("\u0000")) return failure("BUNDLE_NUL", "/" + member);
  if (!validUnicode(text)) return failure("BUNDLE_UNICODE_INVALID", "/" + member);
  return success(text);
}
function jsonDocument(files, name) {
  const decoded = decode(files.get(name), name); if (!decoded.valid) return decoded;
  let value; try { value = JSON.parse(decoded.value); } catch { return failure("BUNDLE_JSON_INVALID", "/" + name); }
  let canonical; try { canonical = canonicalJson(value); } catch (error) { return failure(error.code ?? "BUNDLE_JSON_INVALID", "/" + name); }
  if (decoded.value !== canonical) return failure("BUNDLE_JSON_NOT_CANONICAL", "/" + name);
  return success(value);
}
function kindEntries(card) {
  const kinds = { worlds: "world", identities: "identity", talents: "talent", items: "item", backgrounds: "background", styles: "style", worldbookEntries: "worldbookEntry", openings: "opening", informationModules: "informationModule" };
  const entries = new Map(); for (const [collection, kind] of Object.entries(kinds)) for (const value of card.resources[collection]) entries.set(value.id, { kind, sourceRefs: value.sourceRefs }); return entries;
}

export function validateBundleFiles(input) {
  if (!(input instanceof Map)) return failure("BUNDLE_FILES_NOT_MAP");
  if (input.size > LIMITS.files) return failure("BUNDLE_FILE_COUNT");
  const files = new Map(); let total = 0;
  for (const [name, raw] of input) {
    if (!safeMemberName(name)) return failure("BUNDLE_MEMBER_NAME", "");
    if (!(raw instanceof Uint8Array)) return failure("BUNDLE_MEMBER_BYTES", "/" + name);
    if (raw.byteLength > LIMITS.fileBytes) return failure("BUNDLE_FILE_LIMIT", "/" + name);
    total += raw.byteLength; if (total > LIMITS.totalBytes) return failure("BUNDLE_TOTAL_LIMIT");
    const bytes = Buffer.from(raw);
    if (nestedArchive(bytes)) return failure("BUNDLE_NESTED_ARCHIVE", "/" + name);
    const folded = name.toLowerCase(); if ([...files.keys()].some((entry) => entry.toLowerCase() === folded)) return failure("BUNDLE_CASE_COLLISION", "/" + name);
    files.set(name, bytes);
  }
  for (const required of ["bundle-manifest.json", "card-package.json", "content-index.json", "conversion-receipt.json"]) if (!files.has(required)) return failure("BUNDLE_REQUIRED_FILE", "/" + required);
  for (const name of files.keys()) if (!FIXED.has(name) && name !== "bundle-manifest.json" && !/^sources\/[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?\.(?:txt|json)$/u.test(name)) return failure("BUNDLE_UNDECLARED_MEMBER", "/" + name);
  const manifestResult = jsonDocument(files, "bundle-manifest.json"); if (!manifestResult.valid) return manifestResult; const manifest = manifestResult.value;
  if (!validateManifest(manifest)) return failure("BUNDLE_MANIFEST_SCHEMA", "/bundle-manifest.json");
  const listed = new Map(); for (const entry of manifest.files) { if (entry.path === "bundle-manifest.json" || listed.has(entry.path)) return failure("BUNDLE_MANIFEST_DUPLICATE", "/bundle-manifest.json/files"); listed.set(entry.path, entry); }
  const nonManifest = [...files.keys()].filter((name) => name !== "bundle-manifest.json").sort(); if (JSON.stringify([...listed.keys()].sort()) !== JSON.stringify(nonManifest)) return failure("BUNDLE_MANIFEST_FILE_SET", "/bundle-manifest.json/files");
  for (const name of nonManifest) { const bytes = files.get(name), entry = listed.get(name); if (entry.bytes !== bytes.length || entry.sha256 !== sha256(bytes) || entry.crc32 !== crc32(bytes)) return failure("BUNDLE_MANIFEST_INTEGRITY", "/" + name); }
  const cardResult = jsonDocument(files, "card-package.json"); if (!cardResult.valid) return cardResult; const cardPackage = cardResult.value;
  if (!validateCardPackage(cardPackage).valid) return failure("BUNDLE_CARD_PACKAGE", "/card-package.json"); if (manifest.packageRef !== cardPackage.package.id) return failure("BUNDLE_PACKAGE_REF", "/bundle-manifest.json/packageRef");
  const indexResult = jsonDocument(files, "content-index.json"); if (!indexResult.valid) return indexResult; const contentIndex = indexResult.value;
  if (!validateContentIndexSchema(contentIndex)) return failure("BUNDLE_CONTENT_INDEX_SCHEMA", "/content-index.json");
  const receiptResult = jsonDocument(files, "conversion-receipt.json"); if (!receiptResult.valid) return receiptResult; const conversionReceipt = receiptResult.value;
  if (!validateConversionReceiptSchema(conversionReceipt)) return failure("BUNDLE_RECEIPT_SCHEMA", "/conversion-receipt.json");
  if (contentIndex.packageRef !== cardPackage.package.id || conversionReceipt.packageRef !== cardPackage.package.id) return failure("BUNDLE_DOCUMENT_PACKAGE_REF");
  const expected = kindEntries(cardPackage), actual = new Map(); for (const entry of contentIndex.entries) { if (actual.has(entry.id)) return failure("BUNDLE_INDEX_DUPLICATE", "/content-index.json/entries"); actual.set(entry.id, entry); }
  if (expected.size !== actual.size) return failure("BUNDLE_INDEX_SET", "/content-index.json/entries"); for (const [id, value] of expected) { const entry = actual.get(id); if (!entry || entry.kind !== value.kind || JSON.stringify(entry.sourceRefs) !== JSON.stringify(value.sourceRefs)) return failure("BUNDLE_INDEX_DRIFT", "/content-index.json/entries"); }
  const recordCount = cardPackage.resources.worlds.length + cardPackage.resources.identities.length + cardPackage.resources.talents.length;
  if (conversionReceipt.resourceCount !== expected.size || conversionReceipt.sourceRecordCount !== recordCount || conversionReceipt.sourceEvidence.length !== cardPackage.provenance.sources.length) return failure("BUNDLE_RECEIPT_DRIFT", "/conversion-receipt.json");
  const sources = new Map(cardPackage.provenance.sources.map((source) => [source.id, source]));
  const evidenced = new Set();
  for (const evidence of conversionReceipt.sourceEvidence) { const source = sources.get(evidence.sourceRef); if (!source || evidenced.has(evidence.sourceRef) || source.reference !== evidence.reference || source.sha256 !== evidence.sha256) return failure("BUNDLE_RECEIPT_SOURCE_DRIFT", "/conversion-receipt.json/sourceEvidence"); evidenced.add(evidence.sourceRef); }
  if (evidenced.size !== sources.size) return failure("BUNDLE_RECEIPT_SOURCE_DRIFT", "/conversion-receipt.json/sourceEvidence");
  for (const source of sources.values()) {
    const candidates = ["sources/" + source.id + ".txt", "sources/" + source.id + ".json"].filter((name) => files.has(name));
    if (candidates.length !== 1) return failure("BUNDLE_SOURCE_FILE_COUNT", "/sources/" + source.id);
    const name = candidates[0], bytes = files.get(name), decoded = decode(bytes, name); if (!decoded.valid) return decoded;
    if (sha256(bytes) !== source.sha256) return failure("BUNDLE_SOURCE_HASH", "/" + name);
    if (name.endsWith(".json")) {
      let envelope, canonical;
      try { envelope = JSON.parse(decoded.value); canonical = canonicalJson(envelope); } catch { return failure("BUNDLE_SOURCE_JSON_INVALID", "/" + name); }
      if (decoded.value !== canonical || !validateSourceDocument(envelope) || envelope.sourceId !== source.id) return failure("BUNDLE_SOURCE_DOCUMENT", "/" + name);
    }
  }
  const sourceNames = [...files.keys()].filter((name) => name.startsWith("sources/")); if (sourceNames.length !== sources.size) return failure("BUNDLE_SOURCE_EXTRA", "/sources");
  let playerSetup; if (files.has("player-setup.json")) { const playerResult = jsonDocument(files, "player-setup.json"); if (!playerResult.valid) return playerResult; playerSetup = playerResult.value; if (!validatePlayerSetup(playerSetup, cardPackage).valid) return failure("BUNDLE_PLAYER_SETUP", "/player-setup.json"); }
  return success({ files: new Map([...files].map(([name, bytes]) => [name, Buffer.from(bytes)])), manifest, documents: { cardPackage, ...(playerSetup ? { playerSetup } : {}), contentIndex, conversionReceipt } });
}

export function createBundle(compiledValue, sourceFiles) {
  if (!(sourceFiles instanceof Map)) return failure("BUNDLE_SOURCE_FILES_NOT_MAP");
  const packageRef = compiledValue?.cardPackage?.package?.id;
  if (typeof packageRef !== "string") return failure("BUNDLE_DOCUMENT_INVALID");
  const files = new Map();
  try {
    files.set("card-package.json", Buffer.from(canonicalJson(compiledValue.cardPackage)));
    if (compiledValue.playerSetup) files.set("player-setup.json", Buffer.from(canonicalJson(compiledValue.playerSetup)));
    files.set("content-index.json", Buffer.from(canonicalJson(compiledValue.contentIndex)));
    files.set("conversion-receipt.json", Buffer.from(canonicalJson(compiledValue.conversionReceipt)));
  } catch (error) { return failure(error.code ?? "BUNDLE_DOCUMENT_INVALID"); }
  for (const [name, bytes] of sourceFiles) {
    if (typeof name !== "string" || !name.startsWith("sources/")) return failure("BUNDLE_SOURCE_MEMBER_NAME", "");
    const cloned = bytesOf(bytes); if (!cloned) return failure("BUNDLE_MEMBER_BYTES", "/" + name);
    files.set(name, cloned);
  }
  const entries = [...files].sort(([left], [right]) => left.localeCompare(right)).map(([path, bytes]) => ({ path, bytes: bytes.length, sha256: sha256(bytes), crc32: crc32(bytes) }));
  const manifest = { format: "modelmirror.ai-rpg.bundle-manifest", formatVersion: "0.1.0", converterVersion: "0.2.0", packageRef, files: entries };
  files.set("bundle-manifest.json", Buffer.from(canonicalJson(manifest)));
  return validateBundleFiles(files);
}

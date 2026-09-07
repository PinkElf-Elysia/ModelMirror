import Ajv2020 from "ajv/dist/2020.js";
import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { canonicalJson, safeMemberName } from "./bundle.mjs";
import { readLimitedFile } from "./cli.mjs";
import { parseStaticLiteral, parseStrictJson } from "./source-input.mjs";
import { inspectPlainJson } from "../content/schemas.mjs";

const MAX_BYTES = 2097152, SHA = "^[a-f0-9]{64}$";
const text = { type: "string", minLength: 1, maxLength: MAX_BYTES }, strict = (required, properties) => ({ type: "object", additionalProperties: false, required, properties });
const identity = strict(["name", "items"], { name: text, items: { oneOf: [text, { type: "array", minItems: 1, maxItems: 256, items: text }] } });
const talent = strict(["name", "color", "cost", "desc", "type"], { name: text, color: text, cost: { type: "number" }, desc: text, type: text });
const world = strict(["name", "desc", "boss", "identities", "talents"], { name: text, desc: text, boss: text, identities: { type: "array", maxItems: 256, items: identity }, talents: { type: "array", maxItems: 1024, items: talent } });
function deepFreeze(value) { if (value && typeof value === "object" && !Object.isFrozen(value)) { for (const child of Object.values(value)) deepFreeze(child); Object.freeze(value); } return value; }
export const WORLD_CAPTURE_SCHEMA = deepFreeze({ $schema: "https://json-schema.org/draft/2020-12/schema", ...strict(["format", "formatVersion", "capturedDate", "sourceUrl", "authorizationRef", "acquisition", "sessionLabel", "openingTitle", "fullOriginalHtmlStored", "rereadMatched", "websiteProbes", "modelCalls", "scope", "unavailable", "commonTalentsScope", "name", "start", "end", "sourceCharacters", "rawUtf8Bytes", "rawSha256", "dataSha256", "raw"], {
  format: { const: "modelmirror.ai-rpg.world-capture" }, formatVersion: { const: "0.1.0" }, capturedDate: text, sourceUrl: text, authorizationRef: text, acquisition: text, sessionLabel: text, openingTitle: text,
  fullOriginalHtmlStored: { const: false }, rereadMatched: { const: true }, websiteProbes: { const: 0 }, modelCalls: { const: 0 }, scope: { const: "complete_selected_world_object" }, unavailable: { type: "array", maxItems: 32, uniqueItems: true, items: text }, commonTalentsScope: text,
  name: text, start: { type: "integer", minimum: 0 }, end: { type: "integer", minimum: 0 }, sourceCharacters: { type: "integer", minimum: 1 }, rawUtf8Bytes: { type: "integer", minimum: 1, maximum: MAX_BYTES }, rawSha256: { type: "string", pattern: SHA }, dataSha256: { type: "string", pattern: SHA }, raw: text
}) });
export const WORLD_EXTRACTION_SCHEMA = deepFreeze({ $schema: "https://json-schema.org/draft/2020-12/schema", ...strict(["format", "formatVersion", "source", "world", "inventory", "receipt"], {
  format: { const: "modelmirror.ai-rpg.world-extraction" }, formatVersion: { const: "0.1.0" },
  source: strict(["url", "authorizationRef", "acquisition", "capturedDate", "sessionLabel", "openingTitle", "captureSha256", "captureHashConvention", "rawSha256", "rawUtf8Bytes", "start", "end", "sourceCharacters"], { url: text, authorizationRef: text, acquisition: text, capturedDate: text, sessionLabel: text, openingTitle: text, captureSha256: { type: "string", pattern: SHA }, captureHashConvention: { const: "JSON.stringify(capture) UTF-8 no trailing LF" }, rawSha256: { type: "string", pattern: SHA }, rawUtf8Bytes: { type: "integer", minimum: 1 }, start: { type: "integer", minimum: 0 }, end: { type: "integer", minimum: 0 }, sourceCharacters: { type: "integer", minimum: 1 } }),
  world,
  inventory: strict(["worlds", "identities", "talents", "total"], { worlds: { const: 1 }, identities: { type: "integer", minimum: 0 }, talents: { type: "integer", minimum: 0 }, total: { type: "integer", minimum: 1 } }),
  receipt: strict(["losses", "unavailable", "commonTalentsScope", "fullOriginalHtmlStored", "stableIdAssignment", "runtimePermissions", "websiteProbes", "modelCalls"], { losses: { type: "array", maxItems: 0 }, unavailable: { type: "array", maxItems: 32, uniqueItems: true, items: text }, commonTalentsScope: text, fullOriginalHtmlStored: { const: false }, stableIdAssignment: { const: "deferred_to_content_mapping" }, runtimePermissions: { type: "array", maxItems: 0 }, websiteProbes: { const: 0 }, modelCalls: { const: 0 } })
}) });
const ajv = new Ajv2020({ allErrors: true, strict: true }), validateCapture = ajv.compile(WORLD_CAPTURE_SCHEMA), validateExtraction = ajv.compile(WORLD_EXTRACTION_SCHEMA), validateWorld = ajv.compile(world);
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const failure = (code, pointer = "") => Object.freeze({ valid: false, diagnostics: Object.freeze([Object.freeze({ phase: "world-source", severity: "error", code, path: pointer })]) });
const success = (value) => Object.freeze({ valid: true, diagnostics: Object.freeze([]), value });

export function verifyWorldCapture(capture) {
  if (inspectPlainJson(capture, { maxDepth: 64, maxNodes: 100000 }).length) return failure("WORLD_CAPTURE_NOT_PLAIN");
  let isolated;
  try { isolated = structuredClone(capture); } catch { return failure("WORLD_CAPTURE_NOT_PLAIN"); }
  if (Buffer.byteLength(JSON.stringify(isolated), "utf8") > MAX_BYTES) return failure("WORLD_CAPTURE_LIMIT");
  if (!validateCapture(isolated)) return failure("WORLD_CAPTURE_SCHEMA");
  const parsed = parseStaticLiteral(isolated.raw); if (!parsed.valid) return failure("WORLD_CAPTURE_LITERAL");
  if (!validateWorld(parsed.value)) return failure("WORLD_DATA_SCHEMA");
  const rawBytes = Buffer.from(isolated.raw, "utf8"), dataBytes = Buffer.from(JSON.stringify(parsed.value), "utf8");
  if (rawBytes.length > MAX_BYTES || isolated.rawUtf8Bytes !== rawBytes.length || isolated.rawSha256 !== digest(rawBytes) || isolated.dataSha256 !== digest(dataBytes)) return failure("WORLD_CAPTURE_HASH");
  if (isolated.end < isolated.start || isolated.end - isolated.start !== isolated.raw.length || isolated.end > isolated.sourceCharacters || parsed.value.name !== isolated.name) return failure("WORLD_CAPTURE_RANGE");
  return success({ world: structuredClone(parsed.value), rawSha256: isolated.rawSha256, dataSha256: isolated.dataSha256, captureSha256: digest(Buffer.from(JSON.stringify(isolated), "utf8")) });
}

export function buildWorldExtraction(capture) {
  const verified = verifyWorldCapture(capture); if (!verified.valid) return verified; const value = structuredClone(capture), data = verified.value.world;
  const extraction = { format: "modelmirror.ai-rpg.world-extraction", formatVersion: "0.1.0", source: { url: value.sourceUrl, authorizationRef: value.authorizationRef, acquisition: value.acquisition, capturedDate: value.capturedDate, sessionLabel: value.sessionLabel, openingTitle: value.openingTitle, captureSha256: verified.value.captureSha256, captureHashConvention: "JSON.stringify(capture) UTF-8 no trailing LF", rawSha256: value.rawSha256, rawUtf8Bytes: value.rawUtf8Bytes, start: value.start, end: value.end, sourceCharacters: value.sourceCharacters }, world: data, inventory: { worlds: 1, identities: data.identities.length, talents: data.talents.length, total: 1 + data.identities.length + data.talents.length }, receipt: { losses: [], unavailable: [...value.unavailable], commonTalentsScope: value.commonTalentsScope, fullOriginalHtmlStored: false, stableIdAssignment: "deferred_to_content_mapping", runtimePermissions: [], websiteProbes: 0, modelCalls: 0 } };
  if (!validateExtraction(extraction)) return failure("WORLD_EXTRACTION_SCHEMA");
  return Buffer.byteLength(canonicalJson(extraction), "utf8") <= MAX_BYTES ? success(extraction) : failure("WORLD_EXTRACTION_LIMIT");
}

function argumentsFor(args) {
  if (!Array.isArray(args) || !["extract", "verify"].includes(args[0])) return null; const required = args[0] === "extract" ? ["capture", "out"] : ["capture", "input"], flags = {};
  for (let index = 1; index < args.length; index += 2) { const flag = args[index], key = typeof flag === "string" && flag.startsWith("--") ? flag.slice(2) : "", value = args[index + 1]; if (!required.includes(key) || typeof value !== "string" || Object.hasOwn(flags, key)) return null; flags[key] = value; }
  return required.every((key) => Object.hasOwn(flags, key)) && Object.keys(flags).length === required.length ? { command: args[0], flags } : null;
}
function decode(bytes) { try { if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return failure("WORLD_UTF8_BOM"); const textValue = new TextDecoder("utf-8", { fatal: true }).decode(bytes); return textValue.includes("\u0000") || !textValue.isWellFormed() ? failure("WORLD_UTF8_INVALID") : success(textValue); } catch { return failure("WORLD_UTF8_INVALID"); } }
async function loadJson(file) { const bytes = await readLimitedFile(file, MAX_BYTES); if (!bytes.valid) return failure("WORLD_INPUT_READ"); const textValue = decode(bytes.value); if (!textValue.valid) return textValue; const parsed = parseStrictJson(textValue.value); return parsed.valid ? success({ value: parsed.value, bytes: bytes.value }) : failure("WORLD_JSON_INVALID"); }
async function writeNew(bytes, destination) {
  let handle, created = false; const target = path.resolve(destination), parent = path.dirname(target);
  try {
    const name = path.basename(destination); if (!safeMemberName(name) || !name.endsWith(".json") || path.basename(target) !== name) return failure("WORLD_OUTPUT_PATH");
    const stat = await fs.lstat(parent), realParent = await fs.realpath(parent); if (!stat.isDirectory() || stat.isSymbolicLink() || path.resolve(parent).toLowerCase() !== path.resolve(realParent).toLowerCase()) return failure("WORLD_OUTPUT_PARENT");
    handle = await fs.open(target, "wx"); created = true; await handle.writeFile(bytes); await handle.close(); return success({ bytes: bytes.length, sha256: digest(bytes) });
  } catch (error) { if (handle) await handle.close().catch(() => {}); if (created) { try { if (path.dirname(target) !== parent) return failure("WORLD_ROLLBACK_FAILED"); await fs.unlink(target); } catch { return failure("WORLD_ROLLBACK_FAILED"); } } return failure(error?.code === "EEXIST" ? "WORLD_OUTPUT_EXISTS" : "WORLD_OUTPUT_FAILED"); }
}

export async function runWorldCli(args) {
  const parsedArgs = argumentsFor(args); if (!parsedArgs) return failure("WORLD_CLI_ARGUMENTS");
  try {
    const capture = await loadJson(parsedArgs.flags.capture); if (!capture.valid) return capture; const built = buildWorldExtraction(capture.value.value); if (!built.valid) return built; const expected = Buffer.from(canonicalJson(built.value), "utf8"); if (expected.length > MAX_BYTES) return failure("WORLD_EXTRACTION_LIMIT");
    if (parsedArgs.command === "extract") return writeNew(expected, parsedArgs.flags.out);
    const input = await readLimitedFile(parsedArgs.flags.input, MAX_BYTES); if (!input.valid) return failure("WORLD_INPUT_READ"); const decoded = decode(input.value); if (!decoded.valid) return decoded; const document = parseStrictJson(decoded.value); if (!document.valid || !validateExtraction(document.value) || !input.value.equals(expected)) return failure("WORLD_EXTRACTION_DRIFT");
    return success({ bytes: input.value.length, sha256: digest(input.value), inventory: structuredClone(built.value.inventory) });
  } catch { return failure("WORLD_CLI_OPERATION"); }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const report = await runWorldCli(process.argv.slice(2)); console.log(JSON.stringify(report)); if (!report.valid) process.exitCode = 1;
}

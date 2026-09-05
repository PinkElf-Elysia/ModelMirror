import { parse } from "acorn";
import { createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import { compileContent, extractSourceRecords } from "../content/index.mjs";
import { inspectPlainJson } from "../content/schemas.mjs";

const MAX_BYTES = 16777216, MAX_DEPTH = 64, MAX_NODES = 100000;
const dangerousKeys = new Set(["__proto__", "constructor", "prototype"]);
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const diagnostic = (code, path = "") => Object.freeze({ phase: "provenance", severity: "error", code, path });
const failure = (code, path = "") => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(code, path)]) });
const success = (value) => Object.freeze({ valid: true, diagnostics: Object.freeze([]), value });
function unicodeValid(text) {
  for (let index = 0; index < text.length; index++) { const unit = text.charCodeAt(index); if (unit >= 0xd800 && unit <= 0xdbff) { const next = text.charCodeAt(index + 1); if (!(next >= 0xdc00 && next <= 0xdfff)) return false; index++; } else if (unit >= 0xdc00 && unit <= 0xdfff) return false; }
  return true;
}
function literalValue(node, state, depth = 0) {
  if (++state.nodes > MAX_NODES || depth > MAX_DEPTH) throw new Error("SOURCE_JSON_COMPLEX");
  if (node.type === "Literal" && (node.value === null || typeof node.value === "boolean" || typeof node.value === "string" && unicodeValid(node.value) || typeof node.value === "number" && Number.isFinite(node.value)) && node.regex === undefined && node.bigint === undefined) return node.value;
  if (node.type === "UnaryExpression" && node.operator === "-" && node.prefix && node.argument.type === "Literal" && typeof node.argument.value === "number" && Number.isFinite(node.argument.value)) return -node.argument.value;
  if (node.type === "ArrayExpression") { const output = []; for (const child of node.elements) { if (!child) throw new Error("SOURCE_JSON_AST"); output.push(literalValue(child, state, depth + 1)); } return output; }
  if (node.type === "ObjectExpression") {
    const output = Object.create(null), keys = new Set();
    for (const property of node.properties) {
      if (property.type !== "Property" || property.kind !== "init" || property.method || property.computed || property.shorthand) throw new Error("SOURCE_JSON_AST");
      const key = property.key.type === "Identifier" ? property.key.name : property.key.type === "Literal" && typeof property.key.value === "string" ? property.key.value : null;
      if (key === null || !unicodeValid(key) || dangerousKeys.has(key) || keys.has(key)) throw new Error(keys.has(key) ? "SOURCE_JSON_DUPLICATE_KEY" : "SOURCE_JSON_KEY");
      keys.add(key); output[key] = literalValue(property.value, state, depth + 1);
    }
    return output;
  }
  throw new Error("SOURCE_JSON_AST");
}

export function parseStrictJson(text) {
  if (typeof text !== "string") return failure("SOURCE_JSON_TEXT");
  if (!unicodeValid(text) || Buffer.byteLength(text, "utf8") > MAX_BYTES) return failure("SOURCE_JSON_TEXT");
  let value, program;
  try { value = JSON.parse(text); program = parse("(" + text + ")", { ecmaVersion: 2022, sourceType: "script" }); }
  catch { return failure("SOURCE_JSON_SYNTAX"); }
  try {
    if (program.body.length !== 1 || program.body[0].type !== "ExpressionStatement") return failure("SOURCE_JSON_AST");
    literalValue(program.body[0].expression, { nodes: 0 });
  } catch (error) { return failure(error.message.startsWith("SOURCE_JSON_") ? error.message : "SOURCE_JSON_AST"); }
  return success(value);
}

export function parseStaticLiteral(text) {
  if (typeof text !== "string" || !unicodeValid(text) || Buffer.byteLength(text, "utf8") > 2097152) return failure("SOURCE_LITERAL_TEXT");
  try {
    const program = parse("(" + text + ")", { ecmaVersion: 2022, sourceType: "script" });
    if (program.body.length !== 1 || program.body[0].type !== "ExpressionStatement") return failure("SOURCE_LITERAL_AST");
    return success(literalValue(program.body[0].expression, { nodes: 0 }));
  } catch (error) { return failure(error.message.startsWith("SOURCE_JSON_") ? error.message.replace("SOURCE_JSON_", "SOURCE_LITERAL_") : "SOURCE_LITERAL_SYNTAX"); }
}

function same(left, right) { return JSON.stringify(left) === JSON.stringify(right); }
function recordKey(record) { return record.kind + "\u0000" + String(record.worldName) + "\u0000" + String(record.data?.name); }
function fragmentValue(fragment) {
  let program;
  try {
    program = parse("(" + fragment.text + ")", { ecmaVersion: 2022, sourceType: "script" });
    if (program.body.length !== 1 || program.body[0].type !== "ExpressionStatement") throw new Error("SOURCE_FRAGMENT_LITERAL");
    return literalValue(program.body[0].expression, { nodes: 0 });
  }
  catch { throw new Error("SOURCE_FRAGMENT_LITERAL"); }
}
function valueAt(data, pointer) {
  if (pointer === "") return data;
  let current = data;
  for (const part of pointer.slice(1).split("/").map((value) => value.replaceAll("~1", "/").replaceAll("~0", "~"))) current = current?.[part];
  return current;
}

function compileVerified(input, texts) {
  const metadataIssues = inspectPlainJson(input, { maxDepth: 64, maxNodes: 100000 });
  if (metadataIssues.length) return failure("SOURCE_INPUT_NOT_PLAIN_JSON");
  if (!texts || typeof texts !== "object") return failure("SOURCE_TEXTS_INVALID");
  const values = {};
  for (const key of ["htmlText", "selectionText", "captureText"]) { const descriptor = Object.getOwnPropertyDescriptor(texts, key); if (!descriptor || !("value" in descriptor) || typeof descriptor.value !== "string") return failure("SOURCE_TEXTS_INVALID"); values[key] = descriptor.value; }
  const selectionReport = parseStrictJson(values.selectionText), captureReport = parseStrictJson(values.captureText);
  if (!selectionReport.valid) return failure("SOURCE_SELECTION_INVALID"); if (!captureReport.valid) return failure("SOURCE_CAPTURE_INVALID");
  const capture = captureReport.value;
  if (capture.sourceAcquisitionAccepted !== true || capture.offlineVerification?.fullOriginalHtmlStored !== false || typeof capture.sourceUrl !== "string" || !capture.sourceUrl || typeof capture.acquisitionMethod !== "string" || !capture.acquisitionMethod || !Array.isArray(capture.authorizationReferences) || !capture.authorizationReferences.length) return failure("SOURCE_CAPTURE_POLICY");
  const compiledReport = compileContent(input); if (!compiledReport.valid) return compiledReport;
  const extractedReport = extractSourceRecords(values.htmlText, selectionReport.value); if (!extractedReport.valid) return failure("SOURCE_EXTRACTION_INVALID");
  if (!Array.isArray(capture.selectedRecords) || !Array.isArray(capture.stableIdMap) || !capture.derivedCarrier || capture.derivedCarrier.kind !== "derived") return failure("SOURCE_CAPTURE_SHAPE");
  const carrierBytes = Buffer.from(values.htmlText, "utf8"), authoredBytes = Buffer.from(JSON.stringify(input.authored), "utf8");
  const realSource = input.sources.find((source) => source.id === "source.real-card"), authoredSource = input.sources.find((source) => source.id === "source.authored-rpg02");
  if (!realSource || realSource.kind !== "derived" || realSource.sha256 !== hash(carrierBytes) || capture.derivedCarrier.sha256 !== hash(carrierBytes) || capture.derivedCarrier.utf8Bytes !== carrierBytes.length || capture.derivedCarrier.sourceRecordCount !== input.records.length) return failure("SOURCE_CARRIER_DRIFT");
  if (!authoredSource || authoredSource.kind !== "authored" || authoredSource.sha256 !== hash(authoredBytes)) return failure("SOURCE_AUTHORED_DRIFT");
  if (!same(capture.stableIdMap, input.stableIdMap)) return failure("SOURCE_STABLE_ID_MAP_DRIFT");
  const inputRecords = new Map(), capturedRecords = new Map(), extractedRecords = new Map();
  for (const record of input.records) { const key = recordKey(record); if (inputRecords.has(key)) return failure("SOURCE_RECORD_AMBIGUOUS"); inputRecords.set(key, record); }
  for (const record of capture.selectedRecords) { const key = recordKey(record); if (capturedRecords.has(key)) return failure("SOURCE_CAPTURE_AMBIGUOUS"); capturedRecords.set(key, record); }
  for (const record of extractedReport.value.records) { const key = recordKey(record); if (extractedRecords.has(key)) return failure("SOURCE_EXTRACT_AMBIGUOUS"); extractedRecords.set(key, record); }
  if (inputRecords.size !== capturedRecords.size || inputRecords.size !== extractedRecords.size) return failure("SOURCE_RECORD_SET_DRIFT");
  let fragmentCount = 0, fragmentBytes = 0;
  for (const [key, record] of inputRecords) {
    const captured = capturedRecords.get(key), extracted = extractedRecords.get(key), mapping = input.stableIdMap.find((entry) => entry.id === record.stableId);
    const dataHash = hash(Buffer.from(JSON.stringify(record.data), "utf8"));
    const dataBytes = Buffer.byteLength(JSON.stringify(captured?.data), "utf8");
    if (!captured || !extracted || !mapping || record.sourceRef !== realSource.id || record.locator !== captured.locator || record.locator !== mapping.sourceLocator || record.dataSha256 !== dataHash || captured.dataSha256 !== dataHash || captured.dataUtf8Bytes !== dataBytes || mapping.expectedDataSha256 !== dataHash || !same(record.data, captured.data) || !same(record.data, extracted.data)) return failure("SOURCE_RECORD_DRIFT");
    if (!Array.isArray(captured.fragments) || !captured.fragments.length) return failure("SOURCE_FRAGMENT_SET_DRIFT");
    const paths = new Set();
    for (const fragment of captured.fragments) {
      fragmentCount++; if (paths.has(fragment.path)) return failure("SOURCE_FRAGMENT_AMBIGUOUS"); paths.add(fragment.path);
      const raw = Buffer.from(fragment.text, "utf8");
      fragmentBytes += raw.length;
      if (!Number.isInteger(fragment.start) || !Number.isInteger(fragment.end) || fragment.start < 0 || fragment.end < fragment.start || fragment.end > capture.candidateSnapshot?.utf16Units || fragment.end - fragment.start !== fragment.text.length || fragment.utf8Bytes !== raw.length || fragment.sha256 !== hash(raw)) return failure("SOURCE_FRAGMENT_DRIFT");
      let decoded; try { decoded = fragmentValue(fragment); } catch { return failure("SOURCE_FRAGMENT_LITERAL"); }
      if (!same(decoded, valueAt(captured.data, fragment.path))) return failure("SOURCE_FRAGMENT_VALUE_DRIFT");
    }
    const expectedPaths = record.kind === "world" ? ["/name", "/desc", "/boss"] : [""];
    if (!same([...paths].sort(), expectedPaths.sort())) return failure("SOURCE_FRAGMENT_PATH_DRIFT");
  }
  if (fragmentCount !== 18 || inputRecords.size !== 14 || capture.selectedFragmentUtf8Bytes !== fragmentBytes) return failure("SOURCE_EVIDENCE_COUNT");
  const verification = Object.freeze({ converterVersion: "0.2.0", carrierSha256: hash(carrierBytes), authoredSha256: hash(authoredBytes), captureSha256: hash(Buffer.from(values.captureText, "utf8")), selectionSha256: hash(Buffer.from(values.selectionText, "utf8")), playerTextSha256: input.player ? hash(Buffer.from(input.player.text, "utf8")) : null, sourceRecordCount: inputRecords.size, fragmentCount, fullOriginalHtmlStored: false, evidence: "selected_dom_fragments" });
  const compiled = structuredClone(compiledReport.value); compiled.conversionReceipt.hashVerification = "verified_selected_evidence"; compiled.conversionReceipt.toolingVerification = structuredClone(verification);
  return success({ compiled, sourceFiles: new Map([["sources/source.real-card.txt", Buffer.from(carrierBytes)], ["sources/source.authored-rpg02.txt", Buffer.from(authoredBytes)]]), verification });
}

export function compileVerifiedContent(input, texts) { try { return compileVerified(input, texts); } catch { return failure("SOURCE_VERIFICATION_FAILED"); } }

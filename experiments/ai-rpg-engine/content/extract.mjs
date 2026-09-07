import { parse } from "parse5";
import { parse as parseScript } from "acorn";
import { diagnostic, escapePointer, inspectPlainJson, sortDiagnostics, validateSourceSelectionSchema } from "./schemas.mjs";

const MAX_BYTES = 16 * 1024 * 1024, MAX_NODES = 250000, MAX_DEPTH = 64;
const TARGETS = new Set(["worldDB", "commonTalents"]);
const WORLD_KEYS = new Set(["name", "desc", "boss", "identities", "talents"]);
const IDENTITY_KEYS = new Set(["name", "items"]);
const TALENT_KEYS = new Set(["name", "color", "cost", "desc", "type"]);
const string = (value) => typeof value === "string";
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const stringOrList = (value) => string(value) || Array.isArray(value) && value.every(string);

function report(diagnostics, value) {
  const sorted = sortDiagnostics(diagnostics);
  return sorted.length ? Object.freeze({ valid: false, diagnostics: sorted }) : Object.freeze({ valid: true, diagnostics: sorted, value });
}
function textDiagnostics(text) {
  if (typeof text !== "string") return [diagnostic("input", "HTML_NOT_STRING", "/htmlText")];
  const diagnostics = [];
  for (let i = 0; i < text.length; i++) {
    const unit = text.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) { const next = text.charCodeAt(i + 1); if (!(next >= 0xdc00 && next <= 0xdfff)) { diagnostics.push(diagnostic("input", "HTML_INVALID_UTF16", "/htmlText")); break; } i++; }
    else if (unit >= 0xdc00 && unit <= 0xdfff) { diagnostics.push(diagnostic("input", "HTML_INVALID_UTF16", "/htmlText")); break; }
  }
  if (new TextEncoder().encode(text).byteLength > MAX_BYTES) diagnostics.push(diagnostic("input", "HTML_UTF8_LIMIT", "/htmlText"));
  return diagnostics;
}
function selectionDiagnostics(selection) {
  const diagnostics = [...inspectPlainJson(selection)];
  if (diagnostics.length) return diagnostics;
  if (!validateSourceSelectionSchema(selection)) {
    for (const error of validateSourceSelectionSchema.errors ?? []) diagnostics.push(diagnostic("selection", "SELECTION_SCHEMA", error.instancePath || ""));
    return diagnostics;
  }
  const lists = [[selection?.worlds?.map((entry) => entry?.name), "/worlds"], [selection?.commonTalentNames, "/commonTalentNames"]];
  selection?.worlds?.forEach((entry, index) => lists.push([entry.identityNames, "/worlds/" + index + "/identityNames"], [entry.talentNames, "/worlds/" + index + "/talentNames"]));
  for (const [names, pointer] of lists) if (Array.isArray(names)) {
    const first = new Map(); names.forEach((name, index) => { if (typeof name === "string") { if (first.has(name)) diagnostics.push(diagnostic("selection", "SELECTION_DUPLICATE_NAME", pointer + "/" + index, pointer + "/" + first.get(name))); else first.set(name, index); } });
  }
  return diagnostics;
}
function classicScripts(document, html) {
  const scripts = [], stack = [document];
  while (stack.length) {
    const node = stack.pop(); for (const child of [...(node.childNodes ?? [])].reverse()) stack.push(child);
    const location = node.sourceCodeLocation;
    if (node.tagName !== "script" || !location?.startTag || !location?.endTag) continue;
    const attrs = new Map((node.attrs ?? []).map((entry) => [entry.name.toLowerCase(), entry.value.toLowerCase()]));
    if (attrs.has("src") || attrs.has("type") && !["", "text/javascript", "application/javascript"].includes(attrs.get("type"))) continue;
    scripts.push({ source: html.slice(location.startTag.endOffset, location.endTag.startOffset), offset: location.startTag.endOffset });
  }
  return scripts;
}
function keyOf(property) {
  if (property.type !== "Property" || property.computed || property.kind !== "init" || property.method || property.shorthand) throw Object.assign(new Error(), { code: "AST_OBJECT_PROPERTY" });
  if (property.key.type === "Identifier") return property.key.name;
  if (property.key.type === "Literal" && typeof property.key.value === "string") return property.key.value;
  throw Object.assign(new Error(), { code: "AST_OBJECT_KEY" });
}
function interpret(root) {
  const built = new Map(), stack = [{ node: root, depth: 0, visited: false }]; let count = 0;
  while (stack.length) {
    const current = stack.pop(), node = current.node;
    if (++count > MAX_NODES) throw Object.assign(new Error(), { code: "AST_TOO_COMPLEX" });
    if (current.depth > MAX_DEPTH) throw Object.assign(new Error(), { code: "AST_TOO_DEEP" });
    if (!current.visited) {
      stack.push({ ...current, visited: true });
      if (node.type === "ArrayExpression") { if (node.elements.some((entry) => entry === null)) throw Object.assign(new Error(), { code: "AST_ARRAY_HOLE" }); for (const child of [...node.elements].reverse()) stack.push({ node: child, depth: current.depth + 1, visited: false }); }
      else if (node.type === "ObjectExpression") {
        const keys = new Set(); for (const property of node.properties) { if (property.type === "SpreadElement") throw Object.assign(new Error(), { code: "AST_SPREAD" }); const key = keyOf(property); if (["__proto__", "prototype", "constructor"].includes(key)) throw Object.assign(new Error(), { code: "AST_FORBIDDEN_KEY" }); if (keys.has(key)) throw Object.assign(new Error(), { code: "AST_DUPLICATE_KEY" }); keys.add(key); }
        for (const property of [...node.properties].reverse()) stack.push({ node: property.value, depth: current.depth + 1, visited: false });
      } else if (node.type === "UnaryExpression") { if (node.operator !== "-" || node.argument.type !== "Literal" || typeof node.argument.value !== "number") throw Object.assign(new Error(), { code: "AST_UNARY" }); stack.push({ node: node.argument, depth: current.depth + 1, visited: false }); }
      else if (node.type !== "Literal") throw Object.assign(new Error(), { code: "AST_UNSAFE_NODE" });
      continue;
    }
    if (node.type === "Literal") { if (node.regex || typeof node.value === "bigint" || !(node.value === null || ["string", "number", "boolean"].includes(typeof node.value)) || typeof node.value === "number" && !Number.isFinite(node.value)) throw Object.assign(new Error(), { code: "AST_LITERAL" }); built.set(node, { value: node.value, node }); }
    else if (node.type === "UnaryExpression") built.set(node, { value: -built.get(node.argument).value, node });
    else if (node.type === "ArrayExpression") built.set(node, { value: node.elements.map((entry) => built.get(entry).value), node, elements: node.elements.map((entry) => built.get(entry)) });
    else { const value = Object.create(null), properties = new Map(); for (const property of node.properties) { const key = keyOf(property), child = built.get(property.value); value[key] = child.value; properties.set(key, child); } built.set(node, { value, node, properties }); }
  }
  return built.get(root);
}
function collectTargets(scripts, diagnostics) {
  const declarations = new Map([["worldDB", []], ["commonTalents", []]]);
  for (const script of scripts) {
    let program; try { program = parseScript(script.source, { ecmaVersion: 2022, sourceType: "script" }); } catch { if ([...TARGETS].some((name) => script.source.includes(name))) diagnostics.push(diagnostic("parse", "SCRIPT_PARSE_ERROR", "/scripts")); continue; }
    const stack = [program];
    while (stack.length) {
      const node = stack.pop(); if (!node || typeof node !== "object") continue;
      if (node.type === "VariableDeclarator" && node.id?.type === "Identifier" && TARGETS.has(node.id.name)) declarations.get(node.id.name).push({ node: node.init, offset: script.offset });
      if (node.type === "AssignmentExpression" && node.left?.type === "Identifier" && TARGETS.has(node.left.name)) diagnostics.push(diagnostic("parse", "TARGET_ASSIGNMENT_AMBIGUITY", "/" + node.left.name));
      for (const [key, value] of Object.entries(node)) if (!["start", "end", "loc"].includes(key)) {
        if (Array.isArray(value)) { for (const child of value) if (child?.type) stack.push(child); }
        else if (value?.type) stack.push(value);
      }
    }
  }
  const targets = new Map();
  for (const name of TARGETS) { const found = declarations.get(name); if (found.length !== 1 || !found[0].node) diagnostics.push(diagnostic("parse", found.length ? "TARGET_DUPLICATE" : "TARGET_MISSING", "/" + name)); else try { targets.set(name, { ...found[0], interpreted: interpret(found[0].node) }); } catch (error) { diagnostics.push(diagnostic("interpret", error.code ?? "AST_REJECTED", "/" + name)); } }
  return targets;
}
function exactObject(record, keys, types, pointer, diagnostics) {
  const startCount = diagnostics.length;
  if (!record || typeof record.value !== "object" || Array.isArray(record.value) || !record.properties) { diagnostics.push(diagnostic("record", "RECORD_NOT_OBJECT", pointer)); return false; }
  for (const key of record.properties.keys()) if (!keys.has(key)) diagnostics.push(diagnostic("record", "RECORD_UNKNOWN_FIELD", pointer + "/" + escapePointer(key)));
  for (const [key, predicate] of Object.entries(types)) if (!record.properties.has(key)) diagnostics.push(diagnostic("record", "RECORD_REQUIRED_FIELD", pointer + "/" + key)); else if (!predicate(record.value[key])) diagnostics.push(diagnostic("record", "RECORD_FIELD_TYPE", pointer + "/" + key));
  return diagnostics.length === startCount;
}
function range(entry, offset, recordPath = "") { return { path: recordPath, start: offset + entry.node.start, end: offset + entry.node.end }; }
function findUnique(entries, name, pointer, diagnostics) { const matches = entries.filter((entry) => entry?.value?.name === name); if (matches.length !== 1) { diagnostics.push(diagnostic("match", matches.length ? "NAME_AMBIGUOUS" : "NAME_NOT_FOUND", pointer)); return null; } return matches[0]; }

export function extractSourceRecords(htmlText, selection) {
  const diagnostics = [...textDiagnostics(htmlText), ...selectionDiagnostics(selection)];
  if (diagnostics.length) return report(diagnostics);
  let document; try { document = parse(htmlText, { sourceCodeLocationInfo: true, scriptingEnabled: false }); } catch { return report([diagnostic("parse", "HTML_PARSE_ERROR", "/htmlText")]); }
  const targets = collectTargets(classicScripts(document, htmlText), diagnostics);
  if (diagnostics.length) return report(diagnostics);
  const worldTarget = targets.get("worldDB"), commonTarget = targets.get("commonTalents");
  const worlds = worldTarget.interpreted.elements ?? (worldTarget.interpreted.properties ? [...worldTarget.interpreted.properties.values()] : null), common = commonTarget.interpreted.elements;
  if (!worlds) diagnostics.push(diagnostic("record", "WORLD_DB_SHAPE", "/worldDB")); if (!common) diagnostics.push(diagnostic("record", "COMMON_TALENTS_SHAPE", "/commonTalents")); if (diagnostics.length) return report(diagnostics);
  const records = [];
  for (let wi = 0; wi < selection.worlds.length; wi++) {
    const selected = selection.worlds[wi], world = findUnique(worlds, selected.name, "/worlds/" + wi + "/name", diagnostics); if (!world) continue;
    const foundKey = worldTarget.interpreted.properties ? [...worldTarget.interpreted.properties.entries()].find((entry) => entry[1] === world)[0] : null;
    const base = worldTarget.interpreted.elements ? "/worldDB/" + worlds.indexOf(world) : "/worldDB/" + escapePointer(foundKey);
    if (!exactObject(world, WORLD_KEYS, { name: string, desc: string, boss: string, identities: Array.isArray, talents: Array.isArray }, base, diagnostics)) continue;
    records.push({ kind: "world", worldName: world.value.name, locator: base, data: { name: world.value.name, desc: world.value.desc, boss: world.value.boss }, sourceRanges: ["name", "desc", "boss"].map((key) => range(world.properties.get(key), worldTarget.offset, "/" + key)) });
    for (const [kind, names, property, keys, types] of [["identity", selected.identityNames, "identities", IDENTITY_KEYS, { name: string, items: stringOrList }], ["talent", selected.talentNames, "talents", TALENT_KEYS, { name: string, color: string, cost: finite, desc: string, type: string }]]) {
      const entries = world.properties.get(property).elements;
      for (let i = 0; i < names.length; i++) { const match = findUnique(entries, names[i], "/worlds/" + wi + "/" + (kind === "identity" ? "identityNames" : "talentNames") + "/" + i, diagnostics); if (!match) continue; const locator = base + "/" + property + "/" + entries.indexOf(match); if (exactObject(match, keys, types, locator, diagnostics)) records.push({ kind, worldName: world.value.name, locator, data: structuredClone(match.value), sourceRanges: [range(match, worldTarget.offset)] }); }
    }
  }
  for (let i = 0; i < selection.commonTalentNames.length; i++) { const match = findUnique(common, selection.commonTalentNames[i], "/commonTalentNames/" + i, diagnostics); if (!match) continue; const locator = "/commonTalents/" + common.indexOf(match); if (exactObject(match, TALENT_KEYS, { name: string, color: string, cost: finite, desc: string, type: string }, locator, diagnostics)) records.push({ kind: "talent", worldName: null, locator, data: structuredClone(match.value), sourceRanges: [range(match, commonTarget.offset)] }); }
  if (diagnostics.length) return report(diagnostics);
  return report([], Object.freeze({ source: Object.freeze({ utf16Units: htmlText.length, utf8Bytes: new TextEncoder().encode(htmlText).byteLength }), records: Object.freeze(records.map((entry) => Object.freeze(entry))) }));
}

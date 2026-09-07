// Browser-side adapter: no Node imports, filesystem, network, or source-script execution.
// The caller supplies the pinned Acorn namespace and standard Web Crypto/TextEncoder globals.
export const WORKER_CAPTURE_VERSION = "0.1.0";
const MAX_BYTES = 2097152, INITIAL_WINDOW = 8192;
const SOURCE_URL = "https://afengy.cash/zh/explore/installed/e23bbc64-4fdd-46d8-92c0-64923961e5d8";
const OPENING_TITLE = "无限重生系统 - 启动协议 v16.2 (Pyrite修复版)";
const fail = (code) => ({ valid: false, diagnostics: [{ phase: "worker-capture", severity: "error", code, path: "" }] });
const ok = (value) => ({ valid: true, diagnostics: [], value });
/** Runs only through the supplied CUA tab's read-only DOM API. No source code is evaluated. */
export function readWorldDom(request) {
  const failRead = (code) => ({ valid: false, code });
  if (document.URL !== request.sourceUrl) return failRead("WORKER_PAGE_URL");
  const interruptions = document.querySelectorAll('dialog[open], [role="dialog"][aria-modal="true"], iframe[src*="captcha"]');
  if ([...interruptions].some((node) => node.getClientRects().length > 0 && getComputedStyle(node).visibility !== "hidden")) return failRead("WORKER_PAGE_INTERRUPTION");
  const frames = document.querySelectorAll("iframe[srcdoc]");
  if (frames.length !== 1) return failRead("WORKER_PAGE_FRAME");
  const source = frames[0].getAttribute("srcdoc");
  if (typeof source !== "string" || !source.length || source.length > 16777216) return failRead("WORKER_PAGE_LIMIT");
  const headEnd = source.indexOf("</head>");
  if (headEnd < 0 || headEnd > 65536) return failRead("WORKER_PAGE_TITLE");
  const titles = [...source.slice(0, headEnd).matchAll(/<title(?:\s[^>]*)?>[^<]*<\/title\s*>/gi)];
  if (titles.length !== 1 || titles[0][0].length > 2048) return failRead("WORKER_PAGE_TITLE");
  const metadata = { sourceUrl: document.URL, sourceCharacters: source.length, titleMarkup: titles[0][0] };
  if (request.mode === "locate") {
    let sourceBytes = 0;
    for (let index = 0; index < source.length; index++) {
      const unit = source.charCodeAt(index);
      if (unit >= 0xd800 && unit <= 0xdbff) { const next = source.charCodeAt(++index); if (!(next >= 0xdc00 && next <= 0xdfff)) return failRead("WORKER_PAGE_UNICODE"); sourceBytes += 4; }
      else if (unit >= 0xdc00 && unit <= 0xdfff) return failRead("WORKER_PAGE_UNICODE");
      else sourceBytes += unit < 128 ? 1 : unit < 2048 ? 2 : 3;
      if (sourceBytes > 16777216) return failRead("WORKER_PAGE_LIMIT");
    }
    const declarations = [];
    for (const script of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)) {
      const scriptStart = script.index + script[0].indexOf(">") + 1;
      for (const declaration of script[1].matchAll(/\b(?:const|let|var)\s+worldDB\s*=\s*\[/g)) {
        declarations.push({ scriptStart, prefix: script[1].slice(0, declaration.index + declaration[0].length), arrayStart: scriptStart + declaration.index + declaration[0].length - 1 });
        if (declarations.length > 1) return failRead("WORKER_DATABASE_DECLARATION");
      }
    }
    if (declarations.length !== 1 || declarations[0].prefix.length > 65536) return failRead("WORKER_DATABASE_DECLARATION");
    const declaration = declarations[0];
    const escaped = request.name.replace(/[.*+?^\x24{}()|[\]\\]/g, "\\$&");
    const pattern = new RegExp("^\\{\\s*(?:name|\"name\"|'name')\\s*:\\s*([\"'])" + escaped + "\\1");
    const matches = [];
    let cursor = declaration.arrayStart + 1, finished = false;
    const space = () => {
      while (cursor < source.length) {
        if (/\s/.test(source[cursor])) { cursor++; continue; }
        if (source.slice(cursor, cursor + 2) === "//") { const next = source.indexOf("\n", cursor + 2); if (next < 0) return false; cursor = next + 1; continue; }
        if (source.slice(cursor, cursor + 2) === "/*") { const next = source.indexOf("*/", cursor + 2); if (next < 0) return false; cursor = next + 2; continue; }
        break;
      }
      return true;
    };
    while (cursor < source.length) {
      if (!space()) return failRead("WORKER_DATABASE_SHAPE");
      if (source[cursor] === "]") { finished = true; break; }
      if (source[cursor] !== "{") return failRead("WORKER_DATABASE_SHAPE");
      const objectStart = cursor, stack = ["}"]; cursor++;
      while (cursor < source.length && stack.length) {
        if (!space()) return failRead("WORKER_DATABASE_SHAPE");
        const char = source[cursor++];
        if (char === "'" || char === '"') {
          let closed = false;
          while (cursor < source.length) { const unit = source[cursor++]; if (unit === "\\") { cursor++; continue; } if (unit === char) { closed = true; break; } }
          if (!closed) return failRead("WORKER_DATABASE_SHAPE");
        } else if (char === String.fromCharCode(96)) return failRead("WORKER_DATABASE_SHAPE");
        else if (char === "{") stack.push("}");
        else if (char === "[") stack.push("]");
        else if (char === "}" || char === "]") { if (stack.pop() !== char) return failRead("WORKER_DATABASE_SHAPE"); }
        if (stack.length > 64) return failRead("WORKER_DATABASE_SHAPE");
      }
      if (stack.length) return failRead("WORKER_DATABASE_SHAPE");
      if (pattern.test(source.slice(objectStart, Math.min(cursor, objectStart + 2048)))) matches.push({ start: objectStart, end: cursor });
      if (matches.length > 1) return failRead("WORKER_WORLD_AMBIGUOUS");
      if (!space()) return failRead("WORKER_DATABASE_SHAPE");
      if (source[cursor] === ",") cursor++;
      else if (source[cursor] !== "]") return failRead("WORKER_DATABASE_SHAPE");
    }
    if (!finished) return failRead("WORKER_DATABASE_SHAPE");
    if (matches.length !== 1) return failRead("WORKER_WORLD_MISSING");
    return { valid: true, ...metadata, start: matches[0].start, memberEnd: matches[0].end, scriptStart: declaration.scriptStart, prefix: declaration.prefix };
  }
  if (request.mode !== "slice" || !Number.isInteger(request.start) || !Number.isInteger(request.length) || request.start < 0 || request.length < 1 || request.length > 2097152 || request.start >= source.length) return failRead("WORKER_WINDOW_REQUEST");
  return { valid: true, ...metadata, start: request.start, prefix: source.slice(request.scriptStart, request.scriptStart + request.prefixLength), raw: source.slice(request.start, request.start + request.length) };
}


function exactKeys(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Object.keys(descriptors).sort().join() === [...keys].sort().join() && Object.values(descriptors).every((descriptor) => Object.hasOwn(descriptor, "value"));
}
function assignmentValid(value) {
  if (!exactKeys(value, ["format", "formatVersion", "jobId", "owner", "sourceUrl", "authorizationRef", "capturedDate", "worlds"])) return false;
  const safeId = (id) => typeof id === "string" && /^[a-z0-9][a-z0-9-]{0,63}$/.test(id) && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(id);
  const text = (item) => typeof item === "string" && item.length > 0 && item.length <= 1024 && item.isWellFormed();
  if (value.format !== "modelmirror.ai-rpg.worker-assignment" || value.formatVersion !== "0.1.0" || !safeId(value.jobId) || value.sourceUrl !== SOURCE_URL || !text(value.owner) || !text(value.authorizationRef) || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value.capturedDate)) return false;
  const date = new Date(value.capturedDate + "T00:00:00Z");
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value.capturedDate) return false;
  return Array.isArray(value.worlds) && value.worlds.length > 0 && value.worlds.length <= 8 && value.worlds.every((world) => exactKeys(world, ["key", "name"]) && safeId(world.key) && text(world.name)) && new Set(value.worlds.map((world) => world.key)).size === value.worlds.length && new Set(value.worlds.map((world) => world.name)).size === value.worlds.length;
}
function canonical(value) {
  const ordered = (item) => item === null || typeof item !== "object" ? item : Array.isArray(item) ? item.map(ordered) : Object.fromEntries(Object.keys(item).sort().map((key) => [key, ordered(item[key])]));
  return JSON.stringify(ordered(value), null, 2) + "\n";
}
function base64(bytes) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let result = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const n = bytes[i] * 65536 + (bytes[i + 1] ?? 0) * 256 + (bytes[i + 2] ?? 0);
    result += alphabet[n >>> 18] + alphabet[n >>> 12 & 63] + (i + 1 < bytes.length ? alphabet[n >>> 6 & 63] : "=") + (i + 2 < bytes.length ? alphabet[n & 63] : "=");
  }
  return result;
}

/** Literal-only AST decoder; Acorn is injected by the trusted host, never by source content. */
function decodeWorld(raw, parse) {
  const program = parse("(" + raw + ")", { ecmaVersion: 2022, sourceType: "script" });
  if (program.body.length !== 1 || program.body[0].type !== "ExpressionStatement") throw new Error("literal");
  let nodes = 0;
  const walk = (node, depth = 0) => {
    if (++nodes > 100000 || depth > 64 || !node) throw new Error("complex");
    if (node.type === "Literal" && node.regex === undefined && node.bigint === undefined && (node.value === null || typeof node.value === "boolean" || typeof node.value === "string" && node.value.isWellFormed() || typeof node.value === "number" && Number.isFinite(node.value))) return node.value;
    if (node.type === "UnaryExpression" && node.operator === "-" && node.prefix && node.argument.type === "Literal" && typeof node.argument.value === "number" && Number.isFinite(node.argument.value)) return -node.argument.value;
    if (node.type === "ArrayExpression") return node.elements.map((child) => walk(child, depth + 1));
    if (node.type !== "ObjectExpression") throw new Error("literal");
    const result = Object.create(null);
    for (const property of node.properties) {
      if (property.type !== "Property" || property.kind !== "init" || property.method || property.computed || property.shorthand) throw new Error("literal");
      const key = property.key.type === "Identifier" ? property.key.name : property.key.type === "Literal" && typeof property.key.value === "string" ? property.key.value : null;
      if (key === null || !key.isWellFormed() || ["__proto__", "constructor", "prototype"].includes(key) || Object.hasOwn(result, key)) throw new Error("key");
      result[key] = walk(property.value, depth + 1);
    }
    return result;
  };
  return walk(program.body[0].expression);
}
function worldValid(world, name) {
  const text = (value) => typeof value === "string" && value.length > 0 && value.length <= MAX_BYTES;
  return exactKeys(world, ["name", "desc", "boss", "identities", "talents"]) && world.name === name && text(world.desc) && text(world.boss) &&
    Array.isArray(world.identities) && world.identities.length <= 256 && world.identities.every((identity) => exactKeys(identity, ["name", "items"]) && text(identity.name) && (text(identity.items) || Array.isArray(identity.items) && identity.items.length > 0 && identity.items.length <= 256 && identity.items.every(text))) &&
    Array.isArray(world.talents) && world.talents.length <= 1024 && world.talents.every((talent) => exactKeys(talent, ["name", "color", "cost", "desc", "type"]) && text(talent.name) && text(talent.color) && typeof talent.cost === "number" && Number.isFinite(talent.cost) && text(talent.desc) && text(talent.type));
}
function objectEnd(window, tokenizer) {
  try {
    const scanner = tokenizer(window, { ecmaVersion: 2022 }), first = scanner.getToken();
    if (first.type.label !== "{" || first.start !== 0) return { invalid: true };
    let depth = 1, count = 1;
    while (count++ <= 100000) {
      const token = scanner.getToken(), label = token.type.label;
      if (label === "eof") return { incomplete: true };
      if (label === "$" + "{" || label === String.fromCharCode(96)) return { invalid: true };
      if (label === "{") depth++;
      if (label === "}" && --depth === 0) return { end: token.end };
    }
    return { invalid: true };
  } catch { return { incomplete: true }; }
}

export function createWorkerCapture(acorn, runtime = globalThis) {
  if (acorn?.version !== "8.18.0" || typeof acorn.parse !== "function" || typeof acorn.tokenizer !== "function" || !runtime.crypto?.subtle || typeof runtime.TextEncoder !== "function") return fail("WORKER_BROWSER_CAPABILITY");
  const encoder = new runtime.TextEncoder();
  const hash = async (bytes) => [...new Uint8Array(await runtime.crypto.subtle.digest("SHA-256", bytes))].map((unit) => unit.toString(16).padStart(2, "0")).join("");
  const capturedEnvelopes = new WeakSet();
  async function captureWorkerWorld(tab, assignment, resourceKey) {
    if (!assignmentValid(assignment)) return fail("WORKER_ASSIGNMENT_SCHEMA");
    const copied = JSON.parse(JSON.stringify(assignment)), selected = copied.worlds.find((world) => world.key === resourceKey);
    if (!selected) return fail("WORKER_WORLD_UNASSIGNED");
    if (!tab?.playwright || typeof tab.playwright.evaluate !== "function") return fail("WORKER_BROWSER_CAPABILITY");
    let readCount = 0;
    const read = async (request) => { readCount++; return tab.playwright.evaluate(readWorldDom, { sourceUrl: copied.sourceUrl, ...request }); };
    try {
      const located = await read({ mode: "locate", name: selected.name });
      if (!located?.valid) return fail(located?.code ?? "WORKER_BROWSER_READ");
      let prefixTokens;
      try { prefixTokens = [...acorn.tokenizer(located.prefix, { ecmaVersion: 2022 })]; } catch { return fail("WORKER_DATABASE_DECLARATION"); }
      const tail = prefixTokens.slice(-4);
      if (tail.length !== 4 || !["const", "var", "let"].includes(tail[0].value ?? tail[0].type.label) || tail[1].type.label !== "name" || tail[1].value !== "worldDB" || tail[2].type.label !== "=" || tail[3].type.label !== "[") return fail("WORKER_DATABASE_DECLARATION");
      const title = located.titleMarkup.replace(/^<title(?:\s[^>]*)?>/i, "").replace(/<\/title\s*>$/i, "");
      if (title !== OPENING_TITLE) return fail("WORKER_PAGE_TITLE");
      const consistent = (part) => part.sourceUrl === located.sourceUrl && part.sourceCharacters === located.sourceCharacters && part.titleMarkup === located.titleMarkup && part.start === located.start && part.prefix === located.prefix;
      let raw, end;
      for (let size = INITIAL_WINDOW; size <= MAX_BYTES; size *= 2) {
        const part = await read({ mode: "slice", start: located.start, length: size, scriptStart: located.scriptStart, prefixLength: located.prefix.length });
        if (!part?.valid) return fail(part?.code ?? "WORKER_BROWSER_READ");
        if (!consistent(part)) return fail("WORKER_PAGE_DRIFT");
        const boundary = objectEnd(part.raw, acorn.tokenizer);
        if (boundary.invalid) return fail("WORKER_WORLD_LITERAL");
        if (boundary.end) { raw = part.raw.slice(0, boundary.end); end = located.start + boundary.end; break; }
        if (part.raw.length < size || size === MAX_BYTES) return fail("WORKER_WORLD_INCOMPLETE");
      }
      if (end !== located.memberEnd) return fail("WORKER_DATABASE_SHAPE");
      if (typeof raw !== "string" || !raw.isWellFormed() || encoder.encode(raw).length > MAX_BYTES) return fail("WORKER_WORLD_LIMIT");
      let world;
      try { world = decodeWorld(raw, acorn.parse); } catch { return fail("WORKER_WORLD_LITERAL"); }
      if (!worldValid(world, selected.name)) return fail("WORKER_WORLD_SCHEMA");
      const reread = await read({ mode: "slice", start: located.start, length: raw.length, scriptStart: located.scriptStart, prefixLength: located.prefix.length });
      if (!reread?.valid) return fail(reread?.code ?? "WORKER_BROWSER_READ");
      if (!consistent(reread) || reread.raw !== raw) return fail("WORKER_PAGE_DRIFT");
      const bytes = encoder.encode(raw), rawUtf8Bytes = bytes.length, rawSha256 = await hash(bytes), dataSha256 = await hash(encoder.encode(JSON.stringify(world)));
      const observation = { sourceUrl: copied.sourceUrl, openingTitle: title, sourceCharacters: located.sourceCharacters, start: located.start, end, raw, rawUtf8Bytes, rawSha256, dataSha256, rereadMatched: true, sourceIdentity: "visible_opening_iframe_srcdoc" };
      const envelope = { format: "modelmirror.ai-rpg.worker-envelope", formatVersion: "0.1.0", jobId: copied.jobId, resourceKey, assignmentSha256: await hash(encoder.encode(canonical(copied))), observation, producerVersion: WORKER_CAPTURE_VERSION, readCount, diagnostics: [] };
      if (encoder.encode(canonical(envelope)).length > MAX_BYTES) return fail("WORKER_ENVELOPE_LIMIT");
      Object.freeze(observation); Object.freeze(envelope.diagnostics); Object.freeze(envelope);
      capturedEnvelopes.add(envelope);
      return ok(envelope);
    } catch { return fail("WORKER_BROWSER_READ"); }
  }
  function encodeWorkerEnvelope(envelope) {
    if (!capturedEnvelopes.has(envelope)) return fail("WORKER_ENVELOPE_SCHEMA");
    return ok(base64(encoder.encode(canonical(envelope))));
  }
  // Preserve envelope values while removing JavaScript template interpolation delimiters.
  // Only use this data inside the documented String.raw / PowerShell literal here-string.
  function encodeWorkerTransfer(envelope) {
    if (!capturedEnvelopes.has(envelope)) return fail("WORKER_ENVELOPE_SCHEMA");
    const text = JSON.stringify(envelope)
      .replaceAll(String.fromCharCode(96), "\\u0060")
      .replaceAll("$", "\\u0024")
      .replaceAll("\u2028", "\\u2028")
      .replaceAll("\u2029", "\\u2029");
    if (encoder.encode(text).length > MAX_BYTES) return fail("WORKER_TRANSFER_LIMIT");
    return ok(text);
  }
  return ok(Object.freeze({ captureWorkerWorld, encodeWorkerEnvelope, encodeWorkerTransfer }));
}

import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const RPG04_RUNTIME_PROTOCOL_SHA256 = "1a2a24fe757cd240802dcf4a1a4307e8846cc31022bca21be8487347b0998382";

const MAX_PROTOCOL_BYTES = 64 * 1024;
const SOURCE_REFERENCE = "docs/RPG04_PROTOCOL_RUNTIME.txt";
const diagnostic = (code, pointer = "/protocol") => Object.freeze({ phase: "protocol", severity: "error", code, path: pointer });
const fail = (code, pointer = "/protocol") => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(code, pointer)]) });
const succeed = (content, sha256) => Object.freeze({
  valid: true,
  diagnostics: Object.freeze([]),
  value: Object.freeze({
    id: "modelmirror.ai-rpg.rpg04-runtime-protocol",
    version: "0.1.0",
    content,
    sha256,
    sourceReference: SOURCE_REFERENCE,
  }),
});

export function validateRpg04RuntimeProtocolText(text) {
  try {
    if (typeof text !== "string") return fail("RPG04_PROTOCOL_TEXT_INVALID");
    if (text.length > MAX_PROTOCOL_BYTES) return fail("RPG04_PROTOCOL_TOO_LARGE");
    const bytes = Buffer.from(text, "utf8");
    if (bytes.byteLength > MAX_PROTOCOL_BYTES) return fail("RPG04_PROTOCOL_TOO_LARGE");
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    if (sha256 !== RPG04_RUNTIME_PROTOCOL_SHA256) return fail("RPG04_PROTOCOL_HASH_MISMATCH");
    return succeed(text, sha256);
  } catch {
    return fail("RPG04_PROTOCOL_VALIDATION_FAILED");
  }
}

function fixedProtocolPath() {
  const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const protocolPath = path.resolve(moduleRoot, SOURCE_REFERENCE);
  const relative = path.relative(moduleRoot, protocolPath);
  if (relative !== path.normalize(SOURCE_REFERENCE) || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    return { valid: false, code: "RPG04_PROTOCOL_PATH_ESCAPE" };
  }

  let current = moduleRoot;
  for (const segment of SOURCE_REFERENCE.split("/")) {
    current = path.join(current, segment);
    if (fs.lstatSync(current).isSymbolicLink()) return { valid: false, code: "RPG04_PROTOCOL_SYMLINK_REJECTED" };
  }

  const realRoot = fs.realpathSync(moduleRoot);
  const realProtocol = fs.realpathSync(protocolPath);
  if (path.relative(realRoot, realProtocol) !== path.normalize(SOURCE_REFERENCE)) {
    return { valid: false, code: "RPG04_PROTOCOL_PATH_ESCAPE" };
  }
  return { valid: true, value: protocolPath };
}

export function loadRpg04RuntimeProtocol(...args) {
  if (args.length !== 0) return fail("RPG04_PROTOCOL_ARGUMENTS_REJECTED", "");
  try {
    const fixedPath = fixedProtocolPath();
    if (!fixedPath.valid) return fail(fixedPath.code);
    const stat = fs.statSync(fixedPath.value);
    if (!stat.isFile()) return fail("RPG04_PROTOCOL_READ_FAILED");
    if (stat.size > MAX_PROTOCOL_BYTES) return fail("RPG04_PROTOCOL_TOO_LARGE");
    const bytes = fs.readFileSync(fixedPath.value);
    if (bytes.byteLength > MAX_PROTOCOL_BYTES) return fail("RPG04_PROTOCOL_TOO_LARGE");
    const content = bytes.toString("utf8");
    if (!Buffer.from(content, "utf8").equals(bytes)) return fail("RPG04_PROTOCOL_UTF8_INVALID");
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    if (sha256 !== RPG04_RUNTIME_PROTOCOL_SHA256) return fail("RPG04_PROTOCOL_HASH_MISMATCH");
    return succeed(content, sha256);
  } catch {
    return fail("RPG04_PROTOCOL_READ_FAILED");
  }
}

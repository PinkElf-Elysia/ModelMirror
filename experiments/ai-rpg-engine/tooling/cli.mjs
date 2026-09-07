import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { compileVerifiedContent, parseStrictJson } from "./source-input.mjs";
import { createBundle, failure, success, LIMITS, safeMemberName, sha256 } from "./bundle.mjs";
import { readBundleDirectory, writeBundleDirectory } from "./directory.mjs";
import { readArchive, writeArchive } from "./archive.mjs";

const SPEC = {
  compile: { required: ["input", "html", "selection", "capture", "out"], optional: ["player-text", "player-config"] },
  pack: { required: ["input", "out"], optional: [] },
  unpack: { required: ["input", "out"], optional: [] },
  verify: { required: ["input"], optional: [] }
};

function argumentsFor(args) {
  if (!Array.isArray(args) || args.some((value) => typeof value !== "string")) return null;
  const command = args[0], spec = SPEC[command]; if (!spec) return null;
  const flags = {};
  for (let index = 1; index < args.length; index += 2) {
    const flag = args[index], value = args[index + 1];
    if (!flag?.startsWith("--") || !value || value.startsWith("--")) return null;
    const name = flag.slice(2);
    if (![...spec.required, ...spec.optional].includes(name) || Object.hasOwn(flags, name)) return null;
    flags[name] = value;
  }
  if (!spec.required.every((name) => Object.hasOwn(flags, name))) return null;
  if (Object.hasOwn(flags, "player-text") !== Object.hasOwn(flags, "player-config")) return null;
  return { command, flags };
}

export async function readLimitedFile(file, maxBytes) {
  let handle;
  try {
    const stat = await fs.lstat(file);
    if (!stat.isFile() || stat.isSymbolicLink()) return failure("CLI_INPUT_UNSAFE");
    const resolved = path.resolve(file), real = await fs.realpath(file);
    const same = process.platform === "win32" ? resolved.toLowerCase() === real.toLowerCase() : resolved === real;
    if (!same) return failure("CLI_INPUT_UNSAFE");
    handle = await fs.open(file, "r");
    const before = await handle.stat(); if (!before.isFile() || before.size > maxBytes) return failure("CLI_INPUT_LIMIT");
    const bytes = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < bytes.length) {
      const read = await handle.read(bytes, offset, bytes.length - offset, offset);
      if (read.bytesRead === 0) return failure("CLI_INPUT_CHANGED");
      offset += read.bytesRead;
    }
    const after = await handle.stat();
    if (after.size !== before.size || after.mtimeMs !== before.mtimeMs) return failure("CLI_INPUT_CHANGED");
    return success(bytes);
  } catch { return failure("CLI_INPUT_READ_FAILED"); }
  finally { if (handle) await handle.close().catch(() => {}); }
}

function decodeText(bytes) {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return failure("CLI_UTF8_BOM");
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (!text.isWellFormed() || text.includes("\u0000")) return failure("CLI_TEXT_INVALID");
    return success(text);
  } catch { return failure("CLI_UTF8_INVALID"); }
}
async function loadText(file, limit = LIMITS.fileBytes) {
  const bytes = await readLimitedFile(file, limit);
  return bytes.valid ? decodeText(bytes.value) : bytes;
}
async function loadJson(file) {
  const text = await loadText(file); return text.valid ? parseStrictJson(text.value) : text;
}

async function writeNewArchive(bytes, destination) {
  let created = false, handle;
  const target = path.resolve(destination), parent = path.dirname(target), name = path.basename(target);
  if (!safeMemberName(name) || !name.endsWith(".zip")) return failure("CLI_ZIP_DESTINATION");
  try {
    const parentStat = await fs.lstat(parent), realParent = await fs.realpath(parent);
    const same = process.platform === "win32" ? parent.toLowerCase() === realParent.toLowerCase() : parent === realParent;
    if (!parentStat.isDirectory() || parentStat.isSymbolicLink() || !same) return failure("CLI_OUTPUT_PARENT_UNSAFE");
    handle = await fs.open(target, "wx"); created = true;
    await handle.writeFile(bytes); await handle.close(); handle = undefined;
    return success({ operation: "pack", archiveSha256: sha256(bytes), archiveBytes: bytes.length });
  } catch (error) {
    if (handle) await handle.close().catch(() => {});
    if (created) {
      if (path.dirname(target) !== parent) return failure("CLI_ROLLBACK_FAILED");
      try { await fs.unlink(target); } catch { return failure("CLI_ROLLBACK_FAILED"); }
    }
    return failure(error?.code === "EEXIST" ? "CLI_DESTINATION_EXISTS" : "CLI_OUTPUT_FAILED");
  }
}

async function readDelivery(input) {
  const stat = await fs.lstat(input).catch(() => null);
  if (!stat || stat.isSymbolicLink()) return failure("CLI_INPUT_UNSAFE");
  if (stat.isDirectory()) return readBundleDirectory(input);
  const bytes = await readLimitedFile(input, LIMITS.zipBytes);
  return bytes.valid ? readArchive(bytes.value) : bytes;
}

export async function runCli(args) {
  const parsed = argumentsFor(args); if (!parsed) return failure("CLI_ARGUMENTS");
  const { command, flags } = parsed;
  try {
    if (command === "compile") {
      const input = await loadJson(flags.input); if (!input.valid) return input;
      const html = await loadText(flags.html, 16 * 1024 * 1024); if (!html.valid) return html;
      const selection = await loadText(flags.selection); if (!selection.valid) return selection;
      const capture = await loadText(flags.capture); if (!capture.valid) return capture;
      if (flags["player-text"]) {
        if (input.value.player !== undefined) return failure("CLI_PLAYER_CONFIG_DUPLICATE");
        const text = await loadText(flags["player-text"], 1024 * 1024); if (!text.valid) return text;
        const config = await loadJson(flags["player-config"]); if (!config.valid) return config;
        if (!config.value || typeof config.value !== "object" || Array.isArray(config.value) || Object.hasOwn(config.value, "text")) return failure("CLI_PLAYER_CONFIG");
        input.value.player = { ...config.value, text: text.value };
      }
      const compiled = compileVerifiedContent(input.value, { htmlText: html.value, selectionText: selection.value, captureText: capture.value });
      if (!compiled.valid) return compiled;
      const bundle = createBundle(compiled.value.compiled, compiled.value.sourceFiles); if (!bundle.valid) return bundle;
      const written = await writeBundleDirectory(bundle.value.files, flags.out); if (!written.valid) return written;
      return success({ operation: "compile", filesWritten: written.value.filesWritten,
        sourceRecords: compiled.value.compiled.conversionReceipt.sourceRecordCount,
        resources: compiled.value.compiled.conversionReceipt.resourceCount,
        playerIncluded: compiled.value.compiled.playerSetup !== undefined,
        manifestSha256: sha256(bundle.value.files.get("bundle-manifest.json")) });
    }
    const delivery = await readDelivery(flags.input); if (!delivery.valid) return delivery;
    if (command === "verify") return success({ operation: "verify", files: delivery.value.files.size,
      packageRef: delivery.value.documents.cardPackage.package.id,
      playerIncluded: delivery.value.documents.playerSetup !== undefined,
      manifestSha256: sha256(delivery.value.files.get("bundle-manifest.json")) });
    if (command === "unpack") {
      const written = await writeBundleDirectory(delivery.value.files, flags.out);
      return written.valid ? success({ operation: "unpack", filesWritten: written.value.filesWritten }) : written;
    }
    const archived = await writeArchive(delivery.value.files); if (!archived.valid) return archived;
    return writeNewArchive(archived.value.bytes, flags.out);
  } catch { return failure("CLI_OPERATION_FAILED"); }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const report = await runCli(process.argv.slice(2));
  console.log(JSON.stringify(report));
  if (!report.valid) process.exitCode = 1;
}

import { lstat, mkdtemp, open, realpath, rename, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { synthesizePrototypeSpatialIntent } from "@matrix-oasis/prototype-spatial-solver";

const OUTPUT_NAME = "prototype-spatial-intent.json";
const LIMITS = Object.freeze({
  "--scene-blueprint": 2 * 1024 * 1024,
  "--runtime-pack": 16 * 1024 * 1024,
  "--runtime-receipt": 16 * 1024,
  "--asset-bundle": 256 * 1024,
});
function fail(code, exitCode = 2) { process.stderr.write(`${code}\n`); process.exitCode = exitCode; }
function parseArgs(args) {
  const allowed = new Set([...Object.keys(LIMITS), "--output"]); const values = new Map();
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index]; const value = args[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || value.length === 0 || values.has(key)) return null;
    values.set(key, value);
  }
  return values.size === allowed.size ? values : null;
}
function sameIdentity(left, right) { return left.dev === right.dev && left.ino === right.ino && left.size === right.size && left.mtimeNs === right.mtimeNs; }
function sameObject(left, right) { return left.dev === right.dev && left.ino === right.ino; }
async function readStableText(filePath, limit) {
  const absolute = path.resolve(filePath); const before = await lstat(absolute, { bigint: true });
  if (!before.isFile() || before.isSymbolicLink() || before.size < 1n || before.size > BigInt(limit)) throw new Error("INPUT");
  if (await realpath(absolute) !== absolute) throw new Error("INPUT");
  const handle = await open(absolute, "r");
  try {
    const handleBefore = await handle.stat({ bigint: true }); if (!sameIdentity(before, handleBefore)) throw new Error("INPUT");
    const bytes = Buffer.alloc(Number(before.size)); const read = await handle.read(bytes, 0, bytes.length, 0);
    if (read.bytesRead !== bytes.length) throw new Error("INPUT");
    const after = await handle.stat({ bigint: true }); if (!sameIdentity(before, after)) throw new Error("INPUT");
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } finally { await handle.close(); }
}
async function prepareOutput(raw) {
  const temporaryRoot = path.resolve(process.platform === "win32" ? path.join(path.parse(process.cwd()).root, "tmp") : os.tmpdir());
  const rootReal = await realpath(temporaryRoot); const target = path.resolve(raw); const parent = path.dirname(target);
  if (target === rootReal || !target.startsWith(`${rootReal}${path.sep}`) || await realpath(parent) !== parent) throw new Error("OUTPUT");
  try { await lstat(target); throw new Error("OUTPUT"); } catch (error) { if (error.code !== "ENOENT") throw error; }
  return { target, parent };
}
async function publish(target, parent, text) {
  let stage; let identity;
  try {
    stage = await mkdtemp(path.join(parent, ".matrix-oasis-r14-intent-")); identity = await lstat(stage, { bigint: true });
    const handle = await open(path.join(stage, OUTPUT_NAME), "wx");
    try { await handle.writeFile(text, { encoding: "utf8" }); await handle.sync(); } finally { await handle.close(); }
    const current = await lstat(stage, { bigint: true }); if (!sameObject(identity, current) || current.isSymbolicLink()) throw new Error("OUTPUT");
    await rename(stage, target); stage = undefined;
  } finally {
    if (stage) {
      try { const current = await lstat(stage, { bigint: true }); if (sameObject(identity, current) && !current.isSymbolicLink()) await rm(stage, { recursive: true }); } catch { /* fail closed and leave ambiguous staging */ }
    }
  }
}

const args = parseArgs(process.argv.slice(2));
if (!args) fail("PROTOTYPE_SPATIAL_SYNTHESIS_CLI_ARGUMENT_ERROR");
else {
  try {
    const output = await prepareOutput(args.get("--output"));
    const request = {
      sceneBlueprintJson: await readStableText(args.get("--scene-blueprint"), LIMITS["--scene-blueprint"]),
      runtimeGamePackJson: await readStableText(args.get("--runtime-pack"), LIMITS["--runtime-pack"]),
      runtimeReceiptJson: await readStableText(args.get("--runtime-receipt"), LIMITS["--runtime-receipt"]),
      assetBundleJson: await readStableText(args.get("--asset-bundle"), LIMITS["--asset-bundle"]),
    };
    const result = await synthesizePrototypeSpatialIntent(request);
    if (!result.ok) { for (const item of result.diagnostics) process.stderr.write(`${item.code} ${item.path}\n`); process.exitCode = 1; }
    else { await publish(output.target, output.parent, result.canonicalSpatialIntentJson); process.stdout.write(`${JSON.stringify({ ok: true, artifact: OUTPUT_NAME })}\n`); }
  } catch { fail("PROTOTYPE_SPATIAL_SYNTHESIS_CLI_INTERNAL_ERROR"); }
}

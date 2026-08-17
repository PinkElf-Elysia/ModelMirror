import { lstat, mkdtemp, open, realpath, rename, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { solvePrototypeSpatialLayout } from "@matrix-oasis/prototype-spatial-solver";

const OUTPUTS = Object.freeze({ solution: "prototype-spatial-solution.json", report: "prototype-spatial-solution-report.json" });
const LIMITS = Object.freeze({
  "--spatial-intent": 2 * 1024 * 1024,
  "--environment-facts": 16 * 1024 * 1024,
  "--asset-bundle": 256 * 1024,
  "--runtime-pack": 16 * 1024 * 1024,
  "--runtime-receipt": 16 * 1024,
});

function fail(code, exitCode = 2) { process.stderr.write(`${code}\n`); process.exitCode = exitCode; }
function parseArgs(args) {
  const allowed = new Set([...Object.keys(LIMITS), "--output"]); const values = new Map();
  if (args.length !== allowed.size * 2) return null;
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index]; const value = args[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || value.length === 0 || value.includes("\0") || values.has(key)) return null;
    values.set(key, value);
  }
  return values.size === allowed.size ? values : null;
}
function identity(stat) { return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint" ? { dev: stat.dev, ino: stat.ino } : null; }
function sameIdentity(stat, expected) { return expected && stat.dev === expected.dev && stat.ino === expected.ino; }
function sameFile(left, right) { return left.dev === right.dev && left.ino === right.ino && left.size === right.size && left.mtimeNs === right.mtimeNs && left.ctimeNs === right.ctimeNs; }

async function readStableText(filePath, limit) {
  const absolute = path.resolve(filePath); const before = await lstat(absolute, { bigint: true });
  if (!before.isFile() || before.isSymbolicLink() || before.size < 1n || before.size > BigInt(limit) || await realpath(absolute) !== absolute) throw new Error("INPUT");
  const handle = await open(absolute, "r");
  try {
    const opened = await handle.stat({ bigint: true });
    if (!sameFile(before, opened)) throw new Error("INPUT");
    const bytes = new Uint8Array(Number(before.size)); let offset = 0;
    while (offset < bytes.length) {
      const result = await handle.read(bytes, offset, bytes.length - offset, offset);
      if (!result || result.bytesRead < 1) throw new Error("INPUT");
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, bytes.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameFile(opened, after)) throw new Error("INPUT");
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } finally { await handle.close(); }
}

async function prepareOutput(raw) {
  const temporaryRoot = path.resolve(process.platform === "win32" ? path.join(path.parse(process.cwd()).root, "tmp") : os.tmpdir());
  const rootReal = path.resolve(await realpath(temporaryRoot)); const target = path.resolve(raw); const parent = path.dirname(target);
  if (target === rootReal || !target.startsWith(`${rootReal}${path.sep}`) || path.resolve(await realpath(parent)) !== parent) throw new Error("OUTPUT");
  try { await lstat(target); throw new Error("OUTPUT"); } catch (error) { if (error.code !== "ENOENT") throw error; }
  return { target, parent };
}

async function writeStable(stage, stageIdentity, name, text) {
  const target = path.join(stage, name); const handle = await open(target, "wx+");
  try {
    const before = await handle.stat({ bigint: true }); const fileIdentity = identity(before);
    if (!before.isFile() || before.isSymbolicLink() || !fileIdentity) throw new Error("OUTPUT");
    const bytes = new TextEncoder().encode(text); let offset = 0;
    while (offset < bytes.length) {
      const result = await handle.write(bytes, offset, bytes.length - offset, offset);
      if (!result || result.bytesWritten < 1) throw new Error("OUTPUT");
      offset += result.bytesWritten;
    }
    await handle.sync();
    const readback = new Uint8Array(bytes.length); offset = 0;
    while (offset < bytes.length) {
      const result = await handle.read(readback, offset, bytes.length - offset, offset);
      if (!result || result.bytesRead < 1) throw new Error("OUTPUT");
      offset += result.bytesRead;
    }
    if (Buffer.compare(Buffer.from(bytes), Buffer.from(readback)) !== 0) throw new Error("OUTPUT");
    const linked = await lstat(target, { bigint: true });
    if (!sameIdentity(linked, fileIdentity) || linked.isSymbolicLink()) throw new Error("OUTPUT");
    const currentStage = await lstat(stage, { bigint: true });
    if (!sameIdentity(currentStage, stageIdentity) || currentStage.isSymbolicLink()) throw new Error("OUTPUT");
  } finally { await handle.close(); }
}

async function publish(target, parent, files) {
  let stage; let stageIdentity; let renamed = false; let published = false;
  try {
    stage = await mkdtemp(path.join(parent, ".matrix-oasis-r14-solution-"));
    const observed = await lstat(stage, { bigint: true }); stageIdentity = identity(observed);
    if (!stageIdentity || observed.isSymbolicLink() || !observed.isDirectory() || await realpath(stage) !== stage) throw new Error("OUTPUT");
    for (const [name, text] of files) await writeStable(stage, stageIdentity, name, text);
    const current = await lstat(stage, { bigint: true });
    if (!sameIdentity(current, stageIdentity) || current.isSymbolicLink()) throw new Error("OUTPUT");
    await rename(stage, target); renamed = true;
    const final = await lstat(target, { bigint: true });
    if (!sameIdentity(final, stageIdentity) || final.isSymbolicLink() || await realpath(target) !== target) throw new Error("OUTPUT");
    published = true;
  } finally {
    if (!published && stageIdentity) {
      const cleanup = renamed ? target : stage;
      try { const current = await lstat(cleanup, { bigint: true }); if (sameIdentity(current, stageIdentity) && !current.isSymbolicLink()) await rm(cleanup, { recursive: true }); } catch { /* leave ambiguous state */ }
    }
  }
}

const args = parseArgs(process.argv.slice(2));
if (!args) fail("PROTOTYPE_SPATIAL_SOLVER_CLI_ARGUMENT_ERROR");
else {
  try {
    const output = await prepareOutput(args.get("--output"));
    const result = await solvePrototypeSpatialLayout({
      spatialIntentJson: await readStableText(args.get("--spatial-intent"), LIMITS["--spatial-intent"]),
      environmentFactsJson: await readStableText(args.get("--environment-facts"), LIMITS["--environment-facts"]),
      assetBundleJson: await readStableText(args.get("--asset-bundle"), LIMITS["--asset-bundle"]),
      runtimeGamePackJson: await readStableText(args.get("--runtime-pack"), LIMITS["--runtime-pack"]),
      runtimeReceiptJson: await readStableText(args.get("--runtime-receipt"), LIMITS["--runtime-receipt"]),
    });
    if (!result.ok) { for (const item of result.diagnostics) process.stderr.write(`${item.code} ${item.path}\n`); process.exitCode = 1; }
    else {
      await publish(output.target, output.parent, new Map([[OUTPUTS.solution, result.canonicalSpatialSolutionJson], [OUTPUTS.report, result.canonicalSpatialSolutionReportJson]]));
      process.stdout.write(`${JSON.stringify({ ok: true, artifacts: [OUTPUTS.solution, OUTPUTS.report] })}\n`);
    }
  } catch { fail("PROTOTYPE_SPATIAL_SOLVER_CLI_INTERNAL_ERROR"); }
}

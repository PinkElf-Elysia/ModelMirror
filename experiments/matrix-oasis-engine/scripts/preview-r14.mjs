import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { PROTOTYPE_HOST_MARKER, createPrototypeHost } from "./lib/prototype-host-core.mjs";
import { recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import {
  loadVerifiedR14SpatialPrototypeRun,
  recoverSolvedSpatialPrototypeRuns,
} from "./lib/solved-spatial-cache-core.mjs";
import { createR14PreviewOperations } from "./lib/r14-preview-core.mjs";
import { resolveGodotBinary } from "./lib/godot-core.mjs";
import { loadCreatorWebAssets } from "./preview-spatial-prototype.mjs";

export const R14_PREVIEW_HOST_MARKER = "MATRIX_OASIS_R14_SOLVED_SPATIAL_HOST";
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readFile, readdir, realpath, rename, rm, rmdir });

export function parseR14PreviewArguments(args, tempRoot = temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 6) throw new Error("R14_PREVIEW_ARGUMENT_INVALID");
  const names = Object.freeze({ "--prototype-run-root": "prototypeRunRoot", "--spatial-run-root": "spatialRunRoot", "--solved-run-root": "solvedRunRoot" });
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = names[args[index]]; const value = args[index + 1];
    if (!name || name in values || typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) throw new Error("R14_PREVIEW_ARGUMENT_INVALID");
    values[name] = path.resolve(value);
  }
  const root = path.resolve(tempRoot);
  if (Object.keys(values).length !== 3 || Object.values(values).some((value) => path.dirname(value) !== root)) throw new Error("R14_PREVIEW_ARGUMENT_INVALID");
  return Object.freeze(values);
}

async function main() {
  let parsed;
  try { parsed = parseR14PreviewArguments(process.argv.slice(2)); }
  catch { process.stderr.write("R14_PREVIEW_ARGUMENT_INVALID\n"); process.exitCode = 2; return; }
  const sourceOptions = Object.freeze({ loadVerifiedSpatialPrototypeRun: loadVerifiedR14SpatialPrototypeRun, cacheOptions: Object.freeze({
    runRoot: parsed.spatialRunRoot, prototypeRunRoot: parsed.prototypeRunRoot, temporaryRoot, services,
    recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue,
  }) });
  let recovered;
  try { recovered = await recoverSolvedSpatialPrototypeRuns({ runRoot: parsed.solvedRunRoot, temporaryRoot, sourceOptions, services, canonicalizeJsonValue }); }
  catch { process.stderr.write("R14_PREVIEW_CACHE_INVALID\n"); process.exitCode = 2; return; }
  const selected = recovered.runs.find((run) => run.runId === recovered.currentRunId) ?? recovered.runs[0];
  let godot = null;
  try { if (typeof process.env.GODOT_BIN === "string") godot = resolveGodotBinary({ environment: { GODOT_BIN: process.env.GODOT_BIN } }); }
  catch { /* readiness remains false */ }
  const operations = createR14PreviewOperations({ ...parsed, godot, moduleRoot, temporaryRoot, services });
  const host = createPrototypeHost({ configuration: {
    endpointHost: "offline.local", model: selected?.model ?? "verified-r14-cache", modelReady: false, assetsReady: false, godotReady: godot !== null,
  }, operations, webAssets: await loadCreatorWebAssets(moduleRoot) });
  try {
    const address = await host.start();
    process.stdout.write(`${R14_PREVIEW_HOST_MARKER} origin=${address.origin} api=${PROTOTYPE_HOST_MARKER}\n`);
    const stop = async () => { await host.stop(); process.exitCode = 0; };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
  } catch { process.stderr.write("R14_PREVIEW_INTERNAL_ERROR\n"); process.exitCode = 2; }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();

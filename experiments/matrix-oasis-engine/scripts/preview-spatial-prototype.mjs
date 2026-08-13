import { spawn } from "node:child_process";
import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import { PROTOTYPE_HOST_MARKER, createPrototypeHost } from "./lib/prototype-host-core.mjs";
import {
  findVerifiedSpatialPrototypeRun,
  loadVerifiedSpatialPrototypeRun,
  recoverSpatialPrototypeRuns,
} from "./lib/spatial-cache-core.mjs";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";

export const SPATIAL_PROTOTYPE_HOST_MARKER = "MATRIX_OASIS_R11_SPATIAL_PROTOTYPE_HOST";
export const SPATIAL_PROTOTYPE_READY_MARKER = "MATRIX_OASIS_R11_SPATIAL_READY";
const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");
const defaultServices = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });

function validAbsolute(value) {
  return typeof value === "string" && path.isAbsolute(value) && !value.includes("\0");
}

export function parseSpatialPreviewArguments(args, tempRoot = temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 4) throw new Error("SPATIAL_HOST_ARGUMENT_INVALID");
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const key = args[index] === "--prototype-run-root" ? "prototypeRunRoot" :
      args[index] === "--spatial-run-root" ? "spatialRunRoot" : null;
    const value = args[index + 1];
    if (!key || key in values || !validAbsolute(value)) throw new Error("SPATIAL_HOST_ARGUMENT_INVALID");
    values[key] = path.resolve(value);
  }
  const root = path.resolve(tempRoot);
  if (Object.keys(values).length !== 2 || Object.values(values).some((value) => path.dirname(value) !== root)) {
    throw new Error("SPATIAL_HOST_ARGUMENT_INVALID");
  }
  return Object.freeze(values);
}

export function spatialPrototypeGodotArguments({
  projectRoot,
  runDirectory,
  smoke = false,
  qualification = false,
  capture = false,
}) {
  if (!validAbsolute(projectRoot) || !validAbsolute(runDirectory) || typeof smoke !== "boolean" ||
      typeof qualification !== "boolean" || typeof capture !== "boolean" ||
      Number(smoke) + Number(qualification) + Number(capture) > 1) {
    throw new Error("SPATIAL_HOST_GODOT_ARGUMENT_INVALID");
  }
  return Object.freeze([
    ...(smoke ? ["--headless"] : []),
    "--path", projectRoot, "res://spatial_prototype/spatial_lab.tscn", "--",
    `--matrix-oasis-runtime-pack=${path.join(runDirectory, "runtime-game-pack.json")}`,
    `--matrix-oasis-runtime-receipt=${path.join(runDirectory, "runtime-receipt.json")}`,
    `--matrix-oasis-scene-pack=${path.join(runDirectory, "scene-pack.json")}`,
    `--matrix-oasis-spatial-assembly=${path.join(runDirectory, "spatial-assembly.json")}`,
    "--matrix-oasis-spatial-resource=res://spatial_run/assets/environment.compressed.ply",
    ...(smoke ? ["--matrix-oasis-spatial-smoke"] : []),
    ...(qualification ? ["--matrix-oasis-spatial-qualification"] : []),
    ...(capture ? ["--matrix-oasis-spatial-capture"] : []),
  ]);
}

export async function loadCreatorWebAssets(root = moduleRoot, dependencies = { realpath, readdir, readFile }) {
  const dist = path.join(root, "apps", "creator-web", "dist");
  try {
    const resolved = path.resolve(await dependencies.realpath(dist));
    if (path.dirname(resolved) !== path.join(root, "apps", "creator-web")) return undefined;
    const entries = await dependencies.readdir(resolved, { withFileTypes: true });
    const index = entries.find((entry) => entry.name === "index.html" && entry.isFile() && !entry.isSymbolicLink());
    const assets = entries.find((entry) => entry.name === "assets" && entry.isDirectory() && !entry.isSymbolicLink());
    if (!index || !assets) return undefined;
    const indexBytes = new Uint8Array(await dependencies.readFile(path.join(resolved, "index.html")));
    const output = new Map([
      ["/", { contentType: "text/html; charset=utf-8", bytes: indexBytes }],
      ["/index.html", { contentType: "text/html; charset=utf-8", bytes: indexBytes }],
    ]);
    const assetRoot = path.resolve(await dependencies.realpath(path.join(resolved, "assets")));
    if (path.dirname(assetRoot) !== resolved) return undefined;
    for (const entry of await dependencies.readdir(assetRoot, { withFileTypes: true })) {
      if (!entry.isFile() || entry.isSymbolicLink() || !/^[A-Za-z0-9._-]+\.(?:css|js)$/u.test(entry.name)) return undefined;
      output.set(`/assets/${entry.name}`, {
        contentType: path.extname(entry.name) === ".css" ? "text/css; charset=utf-8" : "text/javascript; charset=utf-8",
        bytes: new Uint8Array(await dependencies.readFile(path.join(assetRoot, entry.name))),
      });
    }
    return output;
  } catch { return undefined; }
}

function diagnostic(code) {
  return Object.freeze({ ok: false, diagnostics: Object.freeze([{ code, path: "" }]) });
}

function safePreviewRelative(relative) {
  return typeof relative === "string" && relative.length > 0 && !relative.includes("\0") && !relative.includes("\\") &&
    !path.posix.isAbsolute(relative) && relative.split("/").every((part) => part !== "" && part !== "." && part !== "..");
}

function sameFileIdentity(left, right) {
  return left && right && typeof left.dev === "bigint" && typeof left.ino === "bigint" &&
    left.dev === right.dev && left.ino === right.ino;
}

async function verifiedPreviewDirectory(candidate, parent, dependencies) {
  const absolute = path.resolve(candidate);
  const resolved = path.resolve(await dependencies.realpath(absolute));
  const stat = await dependencies.lstat(absolute, { bigint: true });
  if (resolved !== absolute || path.dirname(absolute) !== parent || !stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("SPATIAL_HOST_CACHE_INVALID");
  }
  return Object.freeze({ path: absolute, stat });
}

async function assertPreviewDirectory(directory, parent, dependencies) {
  const current = await verifiedPreviewDirectory(directory.path, parent, dependencies);
  if (!sameFileIdentity(current.stat, directory.stat)) throw new Error("SPATIAL_HOST_CACHE_INVALID");
}

export async function copySpatialPreviewFiles(projectRoot, files, dependencies) {
  if (!(files instanceof Map) || files.size < 5 || files.size > 32) throw new Error("SPATIAL_HOST_CACHE_INVALID");
  const captured = [];
  for (const [relative, bytes] of Map.prototype.entries.call(files)) {
    if (!safePreviewRelative(relative) || !(bytes instanceof Uint8Array) ||
        (!relative.startsWith("assets/") && relative.includes("/")) ||
        (relative.startsWith("assets/") && relative.slice(7).includes("/"))) throw new Error("SPATIAL_HOST_CACHE_INVALID");
    captured.push(Object.freeze({ relative, bytes: Uint8Array.prototype.slice.call(bytes) }));
  }
  const project = path.resolve(projectRoot);
  const projectReal = path.resolve(await dependencies.realpath(project));
  const projectStat = await dependencies.lstat(project, { bigint: true });
  if (projectReal !== project || !projectStat.isDirectory() || projectStat.isSymbolicLink()) {
    throw new Error("SPATIAL_HOST_CACHE_INVALID");
  }
  const runDirectory = path.join(projectRoot, "spatial_run");
  const assetsDirectory = path.join(runDirectory, "assets");
  await dependencies.mkdir(runDirectory, { recursive: false });
  const run = await verifiedPreviewDirectory(runDirectory, project, dependencies);
  await dependencies.mkdir(assetsDirectory, { recursive: false });
  const assets = await verifiedPreviewDirectory(assetsDirectory, run.path, dependencies);
  for (const record of captured) {
    const parent = record.relative.startsWith("assets/") ? assets : run;
    const name = record.relative.startsWith("assets/") ? record.relative.slice(7) : record.relative;
    await assertPreviewDirectory(run, project, dependencies);
    await assertPreviewDirectory(parent, parent === run ? project : run.path, dependencies);
    const candidate = path.join(parent.path, name);
    const handle = await dependencies.openFile(candidate, "wx+");
    try {
      const opened = await handle.stat({ bigint: true });
      const linked = await dependencies.lstat(candidate, { bigint: true });
      const resolved = path.resolve(await dependencies.realpath(candidate));
      if (!opened.isFile() || linked.isSymbolicLink() || resolved !== candidate ||
          !sameFileIdentity(opened, linked)) throw new Error("SPATIAL_HOST_CACHE_INVALID");
      await handle.writeFile(record.bytes);
      await handle.sync();
      const output = new Uint8Array(record.bytes.length);
      let offset = 0;
      while (offset < output.length) {
        const result = await handle.read(output, offset, output.length - offset, offset);
        if (!result || result.bytesRead < 1) throw new Error("SPATIAL_HOST_CACHE_INVALID");
        offset += result.bytesRead;
      }
      const tail = await handle.read(new Uint8Array(1), 0, 1, output.length);
      if (tail.bytesRead !== 0 || !output.every((byte, index) => byte === record.bytes[index])) {
        throw new Error("SPATIAL_HOST_CACHE_INVALID");
      }
    } finally {
      await handle.close();
    }
  }
  return runDirectory;
}

export function createSpatialPrototypeOperations({
  prototypeRunRoot,
  spatialRunRoot,
  godot,
  root = moduleRoot,
  tempRoot = temporaryRoot,
  services = defaultServices,
  cache = {},
  godotTools = {},
  spawnProcess = spawn,
  previewFiles = { mkdir, openFile: open, lstat, realpath },
}) {
  if (!validAbsolute(prototypeRunRoot) || !validAbsolute(spatialRunRoot) || !validAbsolute(root) || !validAbsolute(tempRoot)) {
    throw new Error("SPATIAL_HOST_ARGUMENT_INVALID");
  }
  const recoverSpatial = cache.recoverSpatialPrototypeRuns ?? recoverSpatialPrototypeRuns;
  const findSpatial = cache.findVerifiedSpatialPrototypeRun ?? findVerifiedSpatialPrototypeRun;
  const loadSpatial = cache.loadVerifiedSpatialPrototypeRun ?? loadVerifiedSpatialPrototypeRun;
  const createProject = godotTools.createRuntimePreviewProject ?? createRuntimePreviewProject;
  const removeProject = godotTools.removeRuntimePreviewProject ?? removeRuntimePreviewProject;
  const runGodot = godotTools.runGodotCommand ?? runGodotCommand;
  const assertClean = godotTools.assertGodotOutputClean ?? assertGodotOutputClean;
  const configureProject = godotTools.configureGdgsProject ?? configureGdgsProject;
  const common = { runRoot: spatialRunRoot, prototypeRunRoot, temporaryRoot: tempRoot, services,
    recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue };
  let activePreview = null;

  async function cleanup(preview) {
    if (!preview) return;
    try { removeProject(preview.project.temporaryRoot, { moduleRoot: root, identity: preview.project.identity }); }
    catch { /* raced preview root remains fail-closed */ }
  }
  async function stopLaunch() {
    const preview = activePreview; activePreview = null;
    if (!preview) return;
    if (preview.child.exitCode === null && preview.child.signalCode === null) {
      const exited = new Promise((resolve) => preview.child.once("exit", resolve));
      preview.child.kill();
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 5_000))]);
    }
    await cleanup(preview);
  }
  async function launch(runId) {
    if (!godot || !RUN_ID.test(runId)) return { ok: false };
    let verified;
    try { verified = await loadSpatial({ runId, ...common }); }
    catch { return { ok: false }; }
    await stopLaunch();
    const project = createProject({ moduleRoot: root });
    try {
      configureProject(project.projectRoot);
      const runDirectory = await copySpatialPreviewFiles(project.projectRoot, verified.previewFiles, previewFiles);
      const imported = runGodot({ command: godot.command,
        args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"], cwd: root, timeout: 120_000 });
      assertClean(imported);
      const child = spawnProcess(godot.command, spatialPrototypeGodotArguments({ projectRoot: project.projectRoot, runDirectory }), {
        cwd: root, shell: false, windowsHide: false, stdio: ["ignore", "pipe", "pipe"],
      });
      const preview = { child, project }; activePreview = preview; let output = ""; let settled = false;
      const started = await new Promise((resolve) => {
        const finish = (value) => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
        const collect = (chunk) => {
          if (output.length > 8 * 1024 * 1024) { child.kill(); finish(false); return; }
          output += chunk.toString("utf8");
          if (output.includes(SPATIAL_PROTOTYPE_READY_MARKER)) finish(!/\b(?:SCRIPT ERROR|ERROR:)\b/u.test(output));
        };
        child.stdout.on("data", collect); child.stderr.on("data", collect);
        child.once("error", () => finish(false)); child.once("exit", () => finish(false));
        const timer = setTimeout(() => { child.kill(); finish(false); }, 30_000);
      });
      child.once("exit", () => {
        if (activePreview === preview) {
          activePreview = null;
          void cleanup(preview);
        }
      });
      if (!started) { await stopLaunch(); return { ok: false }; }
      return { ok: true };
    } catch {
      await cleanup({ project, child: { exitCode: 0, signalCode: null } });
      return { ok: false };
    }
  }

  return Object.freeze({
    async findCache({ promptSha256, model }) { return findSpatial({ promptSha256, model, ...common }); },
    async generate() { return diagnostic("SPATIAL_HOST_OFFLINE_CACHE_ONLY"); },
    async describeAssets() { return diagnostic("SPATIAL_HOST_OFFLINE_CACHE_ONLY"); },
    async acquire() { return diagnostic("SPATIAL_HOST_OFFLINE_CACHE_ONLY"); },
    async publish() { return diagnostic("SPATIAL_HOST_OFFLINE_CACHE_ONLY"); },
    async launch({ runId }) { return launch(runId); },
    async recover() { return recoverSpatial(common); },
    stopLaunch,
  });
}

async function main() {
  let parsed;
  try { parsed = parseSpatialPreviewArguments(process.argv.slice(2)); }
  catch { process.stderr.write("SPATIAL_HOST_ARGUMENT_INVALID\n"); process.exitCode = 2; return; }
  let recovered;
  try {
    recovered = await recoverSpatialPrototypeRuns({ runRoot: parsed.spatialRunRoot,
      prototypeRunRoot: parsed.prototypeRunRoot, temporaryRoot, services: defaultServices,
      recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue });
  } catch { process.stderr.write("SPATIAL_HOST_CACHE_INVALID\n"); process.exitCode = 2; return; }
  const selected = recovered.runs.find((run) => run.runId === recovered.currentRunId) ?? recovered.runs[0];
  const model = selected?.model ?? "verified-spatial-cache";
  let godot = null;
  try {
    if (typeof process.env.GODOT_BIN === "string") {
      godot = resolveGodotBinary({ environment: { GODOT_BIN: process.env.GODOT_BIN } });
    }
  } catch { /* readiness remains false */ }
  const operations = createSpatialPrototypeOperations({ prototypeRunRoot: parsed.prototypeRunRoot,
    spatialRunRoot: parsed.spatialRunRoot, godot });
  const host = createPrototypeHost({ configuration: {
    endpointHost: "offline.local", model, modelReady: false, assetsReady: false, godotReady: godot !== null,
  }, operations, webAssets: await loadCreatorWebAssets() });
  try {
    const address = await host.start();
    process.stdout.write(`${SPATIAL_PROTOTYPE_HOST_MARKER} origin=${address.origin} api=${PROTOTYPE_HOST_MARKER}\n`);
    const stop = async () => { await host.stop(); process.exitCode = 0; };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
  } catch { process.stderr.write("SPATIAL_HOST_INTERNAL_ERROR\n"); process.exitCode = 2; }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();

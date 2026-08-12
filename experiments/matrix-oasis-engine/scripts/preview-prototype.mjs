import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import {
  MESHY_PROVIDER_ENDPOINT,
  createMeshyTextTo3DProvider,
  materializePrototypeAssetBundle,
  planPrototypeAssets,
} from "@matrix-oasis/prototype-asset-pipeline";
import {
  MARBLE_PROVIDER_ENDPOINT,
  createMarbleWorldProvider,
  materializePrototypeEnvironment,
  planPrototypeEnvironment,
} from "@matrix-oasis/prototype-environment-pipeline";
import { createOpenAICompatibleProvider, generatePrototype } from "@matrix-oasis/prototype-generator";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { findVerifiedPrototypeRun, publishPrototypeRun, recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import { PROTOTYPE_HOST_MARKER, createPrototypeHost } from "./lib/prototype-host-core.mjs";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";

const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });
const allowedMarbleAssetHosts = Object.freeze(["assets.worldlabs.ai", "cdn.worldlabs.ai", "storage.googleapis.com"]);
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const hash = (text) => `sha256:${createHash("sha256").update(new TextEncoder().encode(text)).digest("hex")}`;
const GODOT_READY_MARKER = "MATRIX_OASIS_R10_PROTOTYPE_READY";
const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;

export function prototypeGodotArguments({ projectRoot, runDirectory, smoke = false }) {
  if (![projectRoot, runDirectory].every((value) => typeof value === "string" && path.isAbsolute(value) && !value.includes("\0")) ||
      typeof smoke !== "boolean") throw new Error("PROTOTYPE_HOST_GODOT_ARGUMENT_INVALID");
  return Object.freeze([
    ...(smoke ? ["--headless"] : []),
    "--path", projectRoot, "res://prototype_builder/prototype_lab.tscn", "--",
    `--matrix-oasis-runtime-pack=${path.join(runDirectory, "runtime-game-pack.json")}`,
    `--matrix-oasis-runtime-receipt=${path.join(runDirectory, "runtime-receipt.json")}`,
    `--matrix-oasis-scene-pack=${path.join(runDirectory, "scene-pack.json")}`,
    `--matrix-oasis-environment-bundle=${path.join(runDirectory, "prototype-environment-bundle.json")}`,
    ...(smoke ? ["--matrix-oasis-prototype-smoke"] : []),
  ]);
}

function parseRunRoot(args) {
  if (!Array.isArray(args) || args.length !== 2 || args[0] !== "--run-root" || typeof args[1] !== "string" ||
      args[1].includes("\0") || !path.isAbsolute(args[1])) throw new Error("PROTOTYPE_HOST_ARGUMENT_INVALID");
  const resolved = path.resolve(args[1]);
  if (path.dirname(resolved) !== tempRoot) throw new Error("PROTOTYPE_HOST_ARGUMENT_INVALID");
  return resolved;
}

function configured(name) {
  return Object.prototype.hasOwnProperty.call(process.env, name);
}

async function creatorWebAssets() {
  const dist = path.join(moduleRoot, "apps", "creator-web", "dist");
  try {
    const resolved = await realpath(dist);
    if (path.dirname(resolved) !== path.join(moduleRoot, "apps", "creator-web")) return undefined;
    const entries = await readdir(resolved, { withFileTypes: true });
    const index = entries.find((entry) => entry.name === "index.html" && entry.isFile() && !entry.isSymbolicLink());
    const assetsEntry = entries.find((entry) => entry.name === "assets" && entry.isDirectory() && !entry.isSymbolicLink());
    if (!index || !assetsEntry) return undefined;
    const indexBytes = new Uint8Array(await readFile(path.join(resolved, "index.html")));
    const output = new Map([
      ["/", { contentType: "text/html; charset=utf-8", bytes: indexBytes }],
      ["/index.html", { contentType: "text/html; charset=utf-8", bytes: indexBytes }],
    ]);
    const assetRoot = await realpath(path.join(resolved, "assets"));
    if (path.dirname(assetRoot) !== resolved) return undefined;
    const assetEntries = await readdir(assetRoot, { withFileTypes: true });
    for (const entry of assetEntries) {
      if (!entry.isFile() || entry.isSymbolicLink() || !/^[A-Za-z0-9._-]+\.(?:css|js)$/u.test(entry.name)) return undefined;
      const extension = path.extname(entry.name);
      output.set(`/assets/${entry.name}`, {
        contentType: extension === ".css" ? "text/css; charset=utf-8" : "text/javascript; charset=utf-8",
        bytes: new Uint8Array(await readFile(path.join(assetRoot, entry.name))),
      });
    }
    return output;
  } catch {
    return undefined;
  }
}

function godotConfiguration() {
  if (!configured("GODOT_BIN")) return null;
  try {
    return resolveGodotBinary({ environment: { GODOT_BIN: process.env.GODOT_BIN } });
  } catch {
    return null;
  }
}

function secret(name) {
  const value = process.env[name];
  if (typeof value !== "string" || value.length < 1 || value.length > 8192 || /[\r\n]/u.test(value)) throw new Error("PROTOTYPE_HOST_CONFIG_INVALID");
  return value;
}

function modelConfiguration() {
  const endpoint = process.env.MATRIX_OASIS_MODEL_ENDPOINT;
  const model = process.env.MATRIX_OASIS_MODEL_ID;
  if (typeof endpoint !== "string" || typeof model !== "string") throw new Error("PROTOTYPE_HOST_CONFIG_INVALID");
  const credential = secret("MATRIX_OASIS_MODEL_API_KEY");
  return { endpoint, model, ["api" + "Key"]: credential };
}

async function completedMeshyTask(provider, taskId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await wait(5_000);
    const status = await provider.getTask({ taskId });
    if (!status.ok) return status;
    if (status.task.status === "failed") return { ok: false, diagnostics: [{ code: "MESHY_PROVIDER_GENERATION_FAILED", path: "" }] };
    if (status.task.status === "succeeded") return status;
  }
  return { ok: false, diagnostics: [{ code: "MESHY_PROVIDER_POLL_LIMIT", path: "" }] };
}

async function acquireMeshy(provider, plan) {
  const acquired = new Map();
  for (const brief of plan.plan.blueprint.assetBriefs) {
    if (brief.kind === "environment") continue;
    const preview = await provider.createPreview({ prompt: brief.prompt });
    if (!preview.ok) return preview;
    const previewStatus = await completedMeshyTask(provider, preview.taskId);
    if (!previewStatus.ok) return previewStatus;
    const refined = await provider.createRefine({ previewTaskId: preview.taskId });
    if (!refined.ok) return refined;
    const refineStatus = await completedMeshyTask(provider, refined.taskId);
    if (!refineStatus.ok) return refineStatus;
    const downloaded = await provider.downloadGlb({ url: refineStatus.task.glbUrl });
    if (!downloaded.ok) return downloaded;
    acquired.set(brief.id, downloaded.bytes);
  }
  return { ok: true, acquired };
}

function operations(runRoot, godot) {
  let activePreview = null;

  async function verifiedRunDirectory(runId) {
    if (!RUN_ID.test(runId)) return null;
    const recovered = await recoverPrototypeRuns({ runRoot, temporaryRoot: tempRoot, services,
      assemblePrototypeScene, canonicalizeJsonValue });
    return recovered.runs.some((run) => run.runId === runId)
      ? path.join(runRoot, "runs", runId)
      : null;
  }

  async function cleanupPreview(preview) {
    if (!preview) return;
    try {
      removeRuntimePreviewProject(preview.project.temporaryRoot, {
        moduleRoot,
        identity: preview.project.identity,
      });
    } catch {
      // A raced temporary root is intentionally preserved.
    }
  }

  async function stopActivePreview() {
    const preview = activePreview;
    activePreview = null;
    if (!preview) return;
    if (preview.child.exitCode === null && preview.child.signalCode === null) {
      preview.child.kill();
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, 5_000);
        preview.child.once("exit", () => { clearTimeout(timer); resolve(); });
      });
    }
    await cleanupPreview(preview);
  }

  async function launchPreview(runId) {
    if (!godot) return { ok: false };
    const runDirectory = await verifiedRunDirectory(runId);
    if (!runDirectory) return { ok: false };
    await stopActivePreview();
    const project = createRuntimePreviewProject({ moduleRoot });
    try {
      const imported = runGodotCommand({ command: godot.command,
        args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"],
        cwd: moduleRoot, timeout: 120_000 });
      assertGodotOutputClean(imported);
      const child = spawn(godot.command, prototypeGodotArguments({ projectRoot: project.projectRoot, runDirectory }), {
        cwd: moduleRoot, shell: false, windowsHide: false, stdio: ["ignore", "pipe", "pipe"],
      });
      const preview = { child, project }; activePreview = preview;
      let output = ""; let settled = false;
      const started = await new Promise((resolve) => {
        const finish = (value) => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
        const collect = (chunk) => {
          if (output.length > 8 * 1024 * 1024) { child.kill(); finish(false); return; }
          output += chunk.toString("utf8");
          if (output.includes(GODOT_READY_MARKER)) finish(!/\b(?:SCRIPT ERROR|ERROR:)\b/u.test(output));
        };
        child.stdout.on("data", collect); child.stderr.on("data", collect);
        child.once("error", () => finish(false));
        child.once("exit", () => finish(false));
        const timer = setTimeout(() => { child.kill(); finish(false); }, 30_000);
      });
      child.once("exit", () => {
        if (activePreview === preview) activePreview = null;
        void cleanupPreview(preview);
      });
      if (!started) { await stopActivePreview(); return { ok: false }; }
      return { ok: true };
    } catch {
      await cleanupPreview({ project, child: { exitCode: 0, signalCode: null } });
      return { ok: false };
    }
  }

  return Object.freeze({
    async findCache({ promptSha256, model }) {
      return findVerifiedPrototypeRun({ promptSha256, model, runRoot, temporaryRoot: tempRoot, services,
        assemblePrototypeScene, canonicalizeJsonValue });
    },
    async generate({ prompt }) {
      const provider = createOpenAICompatibleProvider(modelConfiguration());
      return generatePrototype({ prompt }, provider);
    },
    async describeAssets({ artifacts }) {
      try {
        const blueprint = JSON.parse(artifacts.sceneBlueprintJson);
        const briefs = blueprint.assetBriefs.filter((brief) => brief.kind !== "environment")
          .map(({ id, kind, prompt }) => ({ id, kind, prompt }));
        return { ok: true, blueprintSha256: hash(artifacts.sceneBlueprintJson),
          environmentPrompt: blueprint.scene.environmentPrompt, briefs };
      } catch { return { ok: false, diagnostics: [{ code: "PROTOTYPE_HOST_GENERATION_FAILED", path: "" }] }; }
    },
    async acquire({ artifacts, approval, onStage }) {
      const environmentPlan = planPrototypeEnvironment(artifacts.sceneBlueprintJson);
      if (!environmentPlan.ok) return environmentPlan;
      const marbleCredential = secret("MATRIX_OASIS_MARBLE_API_KEY");
      const environmentProvider = createMarbleWorldProvider({ endpoint: MARBLE_PROVIDER_ENDPOINT,
        ["api" + "Key"]: marbleCredential, allowedAssetHosts: allowedMarbleAssetHosts });
      const environment = await materializePrototypeEnvironment({ plan: environmentPlan, approval: {
        blueprintSha256: approval.blueprintSha256, model: "marble-1.1", maxCreateRequests: 1,
        maxPollAttempts: 180, maxWorldGets: 1, maxDownloads: 2, creditLimit: 1600, usdLimitCents: 150,
      } }, environmentProvider);
      if (!environment.ok) return environment;
      const assetPlan = await planPrototypeAssets(artifacts); if (!assetPlan.ok) return assetPlan;
      const meshyCredential = secret("MATRIX_OASIS_MESHY_API_KEY");
      const meshy = createMeshyTextTo3DProvider({ endpoint: MESHY_PROVIDER_ENDPOINT, ["api" + "Key"]: meshyCredential });
      const acquired = await acquireMeshy(meshy, assetPlan); if (!acquired.ok) return acquired;
      onStage("normalizing");
      const kenneyRoot = new URL("../examples/scene-bundles/kenney-prototype/assets/", import.meta.url);
      const assetMaterialization = await materializePrototypeAssetBundle({ plan: assetPlan,
        acquiredAssets: acquired.acquired,
        environmentAssets: new Map([
          ["floor-square", new Uint8Array(await readFile(new URL("floor-square.glb", kenneyRoot)))],
          ["wall", new Uint8Array(await readFile(new URL("wall.glb", kenneyRoot)))],
        ]),
        environmentTexture: new Uint8Array(await readFile(new URL("Textures/colormap.png", kenneyRoot))),
      });
      if (!assetMaterialization.ok) return assetMaterialization;
      return { ok: true,
        assetMaterialization: { canonicalBundleJson: assetMaterialization.canonicalBundleJson, files: [...assetMaterialization.files] },
        environmentMaterialization: { canonicalBundleJson: environment.canonicalBundleJson,
          canonicalReportJson: environment.canonicalReportJson, files: [...environment.files] } };
    },
    async publish({ prompt, artifacts, acquisition }) {
      const published = await publishPrototypeRun({ prompt, prototypeArtifacts: artifacts,
        assetMaterialization: acquisition.assetMaterialization,
        environmentMaterialization: acquisition.environmentMaterialization,
        runRoot, temporaryRoot: tempRoot, source: "live-provider", services,
        assemblePrototypeScene, canonicalizeJsonValue });
      return { ok: true, runId: published.runId };
    },
    async launch({ runId }) { return launchPreview(runId); },
    async recover() {
      return recoverPrototypeRuns({ runRoot, temporaryRoot: tempRoot, services,
        assemblePrototypeScene, canonicalizeJsonValue });
    },
    async stopLaunch() { await stopActivePreview(); },
  });
}

async function main() {
  let runRoot;
  try { runRoot = parseRunRoot(process.argv.slice(2)); }
  catch { process.stderr.write("PROTOTYPE_HOST_ARGUMENT_INVALID\n"); process.exitCode = 2; return; }
  const endpoint = process.env.MATRIX_OASIS_MODEL_ENDPOINT;
  const model = process.env.MATRIX_OASIS_MODEL_ID;
  const godot = godotConfiguration();
  let endpointHost = "unconfigured.local";
  try { endpointHost = typeof endpoint === "string" ? new URL(endpoint).host : endpointHost; } catch { /* readiness remains false */ }
  const host = createPrototypeHost({ configuration: {
    endpointHost,
    model: typeof model === "string" && /^[A-Za-z0-9._/-]{1,128}$/u.test(model) ? model : "unconfigured-model",
    modelReady: configured("MATRIX_OASIS_MODEL_ENDPOINT") && configured("MATRIX_OASIS_MODEL_ID") && configured("MATRIX_OASIS_MODEL_API_KEY"),
    assetsReady: configured("MATRIX_OASIS_MARBLE_API_KEY") && configured("MATRIX_OASIS_MESHY_API_KEY"),
    godotReady: godot !== null,
  }, operations: operations(runRoot, godot), webAssets: await creatorWebAssets() });
  try {
    const address = await host.start();
    process.stdout.write(`${PROTOTYPE_HOST_MARKER} origin=${address.origin}\n`);
    const stop = async () => { await host.stop(); process.exitCode = 0; };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
  } catch { process.stderr.write("PROTOTYPE_HOST_INTERNAL_ERROR\n"); process.exitCode = 2; }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  await main();
}

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";
import { lstat, mkdir, open, realpath, writeFile } from "node:fs/promises";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "../prepare-godot-runtime.mjs";
import { configureGdgsProject } from "../verify-godot-splat.mjs";
import { copySpatialPreviewFiles } from "../preview-spatial-prototype.mjs";
import { runGodotCommand, assertGodotOutputClean } from "./godot-core.mjs";
import { r14GodotArguments } from "./r14-preview-core.mjs";
import { startR22LoopbackServer } from "./r22-host-core.mjs";
import { collectR22LivePhysicalEvidence } from "./r22-live-evidence.mjs";
import { canonicalText } from "./r22-cli-core.mjs";
import { clearR22LiveProcess, guardR22ResidualProcess, markR22LiveProcessRunning, preflightR22ResidualProcess, reserveR22LiveProcess } from "./r22-live-process.mjs";

const READY_LIMIT = 8 * 1024 * 1024;
export const R22_LIVE_READY_MARKER = "MATRIX_OASIS_R22_COGNITION_PREVIEW_READY";

function fail(code) { throw new Error(code); }

export function parseR22LivePreviewArguments(args, temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 20) fail("R22_PREVIEW_ARGUMENT_INVALID");
  const names = { "--qualified-root": "qualifiedRoot", "--case-spec": "caseSpecPath", "--prototype-run-root": "prototypeRunRoot", "--spatial-run-root": "spatialRunRoot", "--solved-run-root": "solvedRunRoot", "--evidence-run-root": "evidenceRunRoot", "--creator-qualified-root": "creatorQualifiedRoot", "--run-root": "runRoot", "--resume-run-root": "resumeRunRoot", "--godot": "godotCommand", "--provider-mode": "providerMode" };
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = names[args[index]], value = args[index + 1];
    if (!name || Object.hasOwn(values, name) || typeof value !== "string" || value.includes("\0")) fail("R22_PREVIEW_ARGUMENT_INVALID");
    if (name === "providerMode") { if (!["offline-fake", "official-once"].includes(value)) fail("R22_PREVIEW_ARGUMENT_INVALID"); values[name] = value; }
    else { if (!path.isAbsolute(value)) fail("R22_PREVIEW_ARGUMENT_INVALID"); values[name] = path.resolve(value); }
  }
  const root = path.resolve(temporaryRoot);
  const resume = Object.hasOwn(values, "resumeRunRoot");
  if (resume === Object.hasOwn(values, "runRoot")) fail("R22_PREVIEW_ARGUMENT_INVALID");
  const runRoot = resume ? values.resumeRunRoot : values.runRoot;
  if (path.dirname(runRoot) !== root || /-(?:npc|cognition)$/u.test(runRoot)) fail("R22_PREVIEW_ARGUMENT_INVALID");
  const sourceRoots = [values.prototypeRunRoot, values.spatialRunRoot, values.solvedRunRoot, values.evidenceRunRoot, values.creatorQualifiedRoot];
  if (sourceRoots.some((value) => path.dirname(value) !== root) || new Set([...sourceRoots, runRoot, `${runRoot}-npc`, `${runRoot}-cognition`]).size !== 8) fail("R22_PREVIEW_ARGUMENT_INVALID");
  return Object.freeze({ ...values, runRoot, resume, temporaryRoot: root, npcRunRoot: `${runRoot}-npc`, cognitionRunRoot: `${runRoot}-cognition` });
}

function digest(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }
export function resolveR22GodotEnginePath(command) {
  const resolved = path.resolve(command);
  const name = path.basename(resolved);
  const match = /^(Godot_v4\.6\.3-stable_win64)_console\.exe$/u.exec(name);
  return match ? path.join(path.dirname(resolved), `${match[1]}.exe`) : resolved;
}
function verifyPreviewIdentity(selected, expected) {
  const files = selected?.evidence?.previewFiles;
  const names = { runtimePackSha256: "runtime-game-pack.json", runtimeReceiptSha256: "runtime-receipt.json", spatialSolutionSha256: "spatial-solution.json", spatialVerificationSha256: "spatial-verification-report.json" };
  if (!(files instanceof Map) || !expected || Object.entries(names).some(([field, name]) => !(files.get(name) instanceof Uint8Array) || digest(files.get(name)) !== expected[field])) fail("R22_PREVIEW_SOURCE_IDENTITY_MISMATCH");
  return files;
}

function recoveryOverlay(live, resume) {
  const commands = live.recoveryCommands, queued = live.resumeQueuedAction;
  if (!Array.isArray(commands) || commands.length > 10_000 || typeof queued !== "boolean") fail("R22_LIVE_RECOVERY_INVALID");
  if (!resume && (commands.length !== 0 || queued)) fail("R22_LIVE_RECOVERY_INVALID");
  let commandsJson; try { commandsJson = canonicalText(commands); } catch { fail("R22_LIVE_RECOVERY_INVALID"); }
  if (Buffer.byteLength(commandsJson) > 8 * 1024 * 1024) fail("R22_LIVE_RECOVERY_INVALID");
  const entityBindingSha256 = digest(Buffer.from(live.source?.npcEntityBindingJson ?? "", "utf8"));
  if (entityBindingSha256 !== live.previewIdentity?.entityBindingSha256) fail("R22_PREVIEW_SOURCE_IDENTITY_MISMATCH");
  return Object.freeze({ queued, recoveryStateJson: resume ? canonicalText({ format: "matrix-oasis.npc-godot-recovery", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", entityBindingSha256, commands }) : null });
}

function r22GodotArguments({ projectRoot, runDirectory }) {
  const args = [...r14GodotArguments({ projectRoot, runDirectory })];
  const scene = args.indexOf("res://solved_spatial_prototype/solved_spatial_lab.tscn");
  if (scene < 0) fail("R22_PREVIEW_GODOT_ARGUMENT_INVALID");
  args[scene] = "res://npc_cognition_prototype/npc_cognition_lab.tscn";
  return args;
}

function allowedEnvironment(extra = {}) {
  return { ...Object.fromEntries(["SystemRoot", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE"].filter((name) => typeof process.env[name] === "string").map((name) => [name, process.env[name]])), ...extra };
}

function probeGodot(command, probe = spawnSync) {
  const result = probe(command, ["--version"], { encoding: "utf8", shell: false, timeout: 5_000, windowsHide: true, env: allowedEnvironment() });
  const output = `${result?.stdout ?? ""} ${result?.stderr ?? ""}`;
  if (result?.status !== 0 || !/(?:^|\D)4\.6\.3(?:\D|$)/u.test(output)) fail("GODOT_4_6_3_NOT_AVAILABLE");
}

const childCloseStates = new WeakMap();
function observeChildClose(child) {
  const state = { closed: false, promise: null }; state.promise = new Promise((resolve) => child.once("close", () => { state.closed = true; resolve(); })); childCloseStates.set(child, state); return state;
}
function stopChild(child, timeoutMs = 5_000) {
  if (!child) return Promise.resolve(); const closeState = childCloseStates.get(child) ?? observeChildClose(child); if (closeState.closed) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let settled = false, timer = null; const finish = (error = null) => { if (settled) return; settled = true; if (timer !== null) clearTimeout(timer); error ? reject(error) : resolve(); };
    closeState.promise.then(() => finish()); child.once("error", () => { if (child.exitCode === null && child.signalCode === null) finish(new Error("R22_GODOT_TERMINATION_FAILED")); });
    if (child.exitCode === null && child.signalCode === null) try { child.kill(); } catch { finish(new Error("R22_GODOT_TERMINATION_FAILED")); return; }
    timer = setTimeout(() => finish(new Error("R22_GODOT_TERMINATION_TIMEOUT")), timeoutMs);
  });
}

function awaitReady(child, marker = R22_LIVE_READY_MARKER, timeoutMs = 120_000) {
  return new Promise((resolve, reject) => {
    let settled = false, observedBytes = 0, tail = "";
    const finish = (error = null) => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(); };
    const collect = (chunk) => {
      observedBytes += chunk.length; const current = tail + chunk.toString("utf8");
      if (observedBytes > READY_LIMIT) return finish(new Error("R22_PREVIEW_GODOT_OUTPUT_LIMIT"));
      if (/(?:SCRIPT ERROR:|(?:^|\n)ERROR:)/u.test(current)) return finish(new Error("R22_PREVIEW_GODOT_FAILED"));
      if (current.includes(marker)) return finish();
      tail = current.slice(-Math.max(marker.length, 64));
    };
    child.stdout?.on("data", collect); child.stderr?.on("data", collect);
    child.once?.("error", () => finish(new Error("R22_PREVIEW_GODOT_FAILED")));
    child.once?.("exit", () => finish(new Error("R22_PREVIEW_GODOT_FAILED")));
    const timer = setTimeout(() => finish(new Error("R22_PREVIEW_GODOT_TIMEOUT")), timeoutMs);
  });
}

function afterReadyFailure(child) {
  return new Promise((resolve) => {
    let observedBytes = 0, tail = "", done = false;
    const inspect = (chunk) => { if (done) return; observedBytes += chunk.length; const current = tail + chunk.toString("utf8");
      if (observedBytes > READY_LIMIT || /(?:SCRIPT ERROR:|(?:^|\n)ERROR:)/u.test(current)) { done = true; resolve("runtime-output-failure"); return; }
      tail = current.slice(-64);
    };
    child.stdout?.on("data", inspect); child.stderr?.on("data", inspect);
  });
}

function safeTerminationEvent(event) {
  const kinds = new Set(["exit", "error", "runtime-output-failure", "physical-evidence-failure"]);
  const source = typeof event === "string" ? { kind: event, code: null, signal: null } : event ?? {};
  return Object.freeze({ kind: kinds.has(source.kind) ? source.kind : "error",
    code: Number.isSafeInteger(source.code) && source.code >= 0 && source.code <= 255 ? source.code : null,
    signal: ["SIGTERM", "SIGKILL", "SIGINT", "SIGHUP"].includes(source.signal) ? source.signal : null });
}

export async function launchR22LivePreview(options, dependencies = {}) {
  if (!options) fail("R22_LIVE_COMPOSITION_UNAVAILABLE");
  const prepareLive = dependencies.prepareLive ?? (async (input) => {
    let imported; try { imported = await import("./r22-live-composition.mjs"); } catch { fail("R22_LIVE_COMPOSITION_UNAVAILABLE"); }
    if (typeof imported.prepareR22LivePreview !== "function") fail("R22_LIVE_COMPOSITION_UNAVAILABLE");
    return imported.prepareR22LivePreview(input, dependencies);
  });
  const createProject = dependencies.createRuntimePreviewProject ?? createRuntimePreviewProject;
  const removeProject = dependencies.removeRuntimePreviewProject ?? removeRuntimePreviewProject;
  const configureProject = dependencies.configureGdgsProject ?? configureGdgsProject;
  const copyFiles = dependencies.copySpatialPreviewFiles ?? copySpatialPreviewFiles;
  const importProject = dependencies.runGodotCommand ?? runGodotCommand;
  const assertClean = dependencies.assertGodotOutputClean ?? assertGodotOutputClean;
  const startServer = dependencies.startR22LoopbackServer ?? startR22LoopbackServer;
  const spawnProcess = dependencies.spawnProcess ?? spawn;
  const writeOverlay = dependencies.writeOverlay ?? (async ({ projectRoot, source, files, recoveryStateJson }) => {
    const overlay = path.join(projectRoot, "npc_authority_prototype"); await mkdir(overlay, { recursive: true });
    await writeFile(path.join(overlay, "entity-bindings.json"), source.npcEntityBindingJson, { flag: "wx" });
    await writeFile(path.join(overlay, "environment-facts.json"), files.get("environment-facts.json"), { flag: "wx" });
    await writeFile(path.join(overlay, "spatial-solution.json"), files.get("spatial-solution.json"), { flag: "wx" });
    if (recoveryStateJson !== null) await writeFile(path.join(overlay, "recovery-state.json"), recoveryStateJson, { flag: "wx" });
  });
  const previewFiles = dependencies.previewFiles ?? { lstat, mkdir, openFile: open, realpath };
  const processIo = { expectedTemporaryRoot: options.temporaryRoot };
  const processGuard = dependencies.processGuard ?? { clear: clearR22LiveProcess,
    guard: (input) => guardR22ResidualProcess(input, processIo), mark: markR22LiveProcessRunning,
    preflight: (input) => preflightR22ResidualProcess(input, processIo), reserve: (input) => reserveR22LiveProcess(input, processIo) };
  let live = null, server = null, project = null, child = null, physical = null, processLease = null, childStoppedForLease = true, cleaned = false, cleanupPromise = null;
  const cleanup = async () => {
    if (cleaned) return; if (cleanupPromise) return cleanupPromise;
    cleanupPromise = (async () => { const failures = []; let childStopped = child === null;
      try { await (dependencies.stopChild ?? stopChild)(child); childStopped = true; childStoppedForLease = true; child = null; } catch (error) { failures.push(error); }
      if (processLease && childStoppedForLease) try { await processGuard.clear(processLease); processLease = null; } catch (error) { failures.push(error); }
      if (server) try { await server.close(); server = null; } catch (error) { failures.push(error); }
      if (physical) { const finishing = physical; physical = null; try { await finishing.finish(); } catch (error) { failures.push(error); } }
      if (live) try { await live.close(); live = null; } catch (error) { failures.push(error); }
      if (project && childStopped) try { removeProject(project.temporaryRoot, { moduleRoot: options.moduleRoot, identity: project.identity }); project = null; } catch (error) { failures.push(error); }
      if (failures.length) throw new AggregateError(failures, "R22_PREVIEW_CLEANUP_FAILED"); cleaned = true;
    })();
    try { await cleanupPromise; } finally { if (!cleaned) cleanupPromise = null; }
  };
  try {
    const engineCommand = resolveR22GodotEnginePath(options.godotCommand);
    (dependencies.probeGodot ?? probeGodot)(engineCommand, dependencies.spawnSync ?? spawnSync);
    if (options.resume === true) await processGuard.preflight({ cognitionRunRoot: options.cognitionRunRoot });
    live = await prepareLive({ ...options, godotCommand: engineCommand });
    if (!live || typeof live.revalidate !== "function" || typeof live.close !== "function" || !live.controller || typeof live.sessionToken !== "string" || live.sessionToken.length < 32) fail("R22_LIVE_COMPOSITION_INVALID");
    const exactPreviewFiles = verifyPreviewIdentity(live.selectedCreatorEvidence, live.previewIdentity);
    const recovery = recoveryOverlay(live, options.resume === true);
    if (options.resume === true) await processGuard.guard({ cognitionRunRoot: options.cognitionRunRoot, expectedIdentity: live.processIdentity });
    await live.revalidate();
    project = createProject({ moduleRoot: options.moduleRoot }); configureProject(project.projectRoot);
    const runDirectory = await copyFiles(project.projectRoot, exactPreviewFiles, previewFiles);
    await writeOverlay({ projectRoot: project.projectRoot, source: live.source, files: exactPreviewFiles, recoveryStateJson: recovery.recoveryStateJson });
    assertClean(await importProject({ command: engineCommand, args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"], cwd: options.moduleRoot, timeout: 120_000,
      spawn: (command, args, childOptions) => (dependencies.spawnSync ?? spawnSync)(command, args, { ...childOptions, env: allowedEnvironment() }) }));
    await live.revalidate();
    server = await startServer({ controller: live.controller, port: 43122 });
    await live.revalidate();
    const env = allowedEnvironment({ MATRIX_OASIS_R20_SESSION_TOKEN: live.sessionToken,
      ...(options.resume === true && recovery.queued ? { MATRIX_OASIS_R22_RESUME_QUEUED_ACTION: "1" } : {}) });
    processLease = await processGuard.reserve({ cognitionRunRoot: options.cognitionRunRoot, identity: live.processIdentity });
    child = spawnProcess(engineCommand, r22GodotArguments({ projectRoot: project.projectRoot, runDirectory }), { cwd: options.moduleRoot, shell: false, windowsHide: false, stdio: ["ignore", "pipe", "pipe"], env });
    childStoppedForLease = false;
    observeChildClose(child);
    const runtimeFailure = (dependencies.afterReadyFailure ?? afterReadyFailure)(child);
    physical = collectR22LivePhysicalEvidence(child, async (text) => {
      if (typeof live?.recordPhysicalEvidence !== "function") fail("R22_LIVE_PHYSICAL_EVIDENCE_INVALID");
      const result = await live.recordPhysicalEvidence(text);
      options.onPhysicalEvidence?.(result);
    }, options.onPhysicalFailure);
    const physicalFailure = physical.failure;
    let processEnded = false;
    const processEnd = new Promise((resolve) => { child.once("exit", onExit); child.once("error", () => { processEnded = true; resolve({ kind: "error", code: null, signal: null }); });
      function onExit(code, signal) { processEnded = true; resolve({ kind: "exit", code, signal }); }
    });
    let readyRaw; try { readyRaw = (dependencies.awaitReady ?? awaitReady)(child); } catch (error) { readyRaw = Promise.reject(error); }
    const readyOutcome = Promise.resolve(readyRaw).then(() => ({ ok: true }), (error) => ({ ok: false, error }));
    await processGuard.mark(processLease, child.pid);
    const ready = await readyOutcome; if (!ready.ok) throw ready.error;
    if (processEnded) fail("R22_PREVIEW_GODOT_FAILED");
    await live.revalidate();
    if (processEnded) fail("R22_PREVIEW_GODOT_FAILED");
    const termination = Promise.race([processEnd, runtimeFailure, physicalFailure]).then(async (event) => { const normalized = safeTerminationEvent(event);
      let cleanupFailure = null; try { await cleanup(); } catch { cleanupFailure = "R22_PREVIEW_CLEANUP_FAILED"; }
      return Object.freeze({ ok: cleanupFailure === null && normalized.kind === "exit" && normalized.code === 0 && normalized.signal == null,
        stage: "runtime", reason: normalized.kind, code: normalized.code, signal: normalized.signal, cleanupFailure }); });
    return Object.freeze({ ok: true, ready: true, qualificationStatus: "unqualified-manual-observation", providerMode: options.providerMode,
      host: "127.0.0.1", port: 43122, child, cleanup, termination });
  } catch (error) { try { await cleanup(); } catch (cleanupError) { throw new AggregateError([error, cleanupError], "R22_PREVIEW_STARTUP_AND_CLEANUP_FAILED"); } throw error; }
}

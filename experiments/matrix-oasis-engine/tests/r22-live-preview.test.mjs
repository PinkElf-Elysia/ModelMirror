import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { lstatSync, mkdirSync, readdirSync, rmdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import test, { after } from "node:test";
import { launchR22LivePreview, parseR22LivePreviewArguments, resolveR22GodotEnginePath } from "../scripts/lib/r22-live-preview.mjs";

const tmp = path.resolve(os.tmpdir());
const abs = (name) => path.join(tmp, name);
let optionSequence = 0;
const ownedTestDirectories = [];
after(() => { for (const owned of ownedTestDirectories) { const now = lstatSync(owned.path, { bigint: true }); assert.equal(now.dev, owned.dev); assert.equal(now.ino, owned.ino); if (readdirSync(owned.path).length === 0) rmdirSync(owned.path); } });
const options = () => { const runRoot = abs(`run-${process.pid}-${optionSequence += 1}`); const cognitionRunRoot = `${runRoot}-cognition`; mkdirSync(cognitionRunRoot, { recursive: true }); const observed = lstatSync(cognitionRunRoot, { bigint: true }); ownedTestDirectories.push({ path: cognitionRunRoot, dev: observed.dev, ino: observed.ino }); return ({ qualifiedRoot: abs("qualified"), caseSpecPath: abs("cases.json"), runRoot, source: { authorityManifestJson: JSON.stringify({ identities: { ...previewIdentity, implementationSha256: `sha256:${"a".repeat(64)}`, godotBinarySha256: `sha256:${"b".repeat(64)}` } }) },
  creatorQualifiedRoot: abs("creator-qualified"),
  npcRunRoot: `${runRoot}-npc`, cognitionRunRoot, temporaryRoot: tmp,
  godotCommand: abs("godot.exe"), godotVersion: "4.6.3", moduleRoot: process.cwd(), qualification: { readyForPreview: false } }); };

function child() { const value = new EventEmitter(); value.pid = 424242; value.stdout = new EventEmitter(); value.stderr = new EventEmitter(); value.exitCode = null; value.signalCode = null; value.kill = () => { value.exitCode = 0; queueMicrotask(() => { value.emit("exit", 0); value.emit("close", 0); }); }; return value; }
const bytes = (value) => new TextEncoder().encode(value);
const preview = new Map([["runtime-game-pack.json", bytes("runtime")], ["runtime-receipt.json", bytes("receipt")], ["spatial-solution.json", bytes("solution")], ["spatial-verification-report.json", bytes("verification")], ["environment-facts.json", bytes("facts")]]);
const hash = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const bindingJson = "{}";
const previewIdentity = Object.freeze({ runtimePackSha256: hash(preview.get("runtime-game-pack.json")), runtimeReceiptSha256: hash(preview.get("runtime-receipt.json")), spatialSolutionSha256: hash(preview.get("spatial-solution.json")), spatialVerificationSha256: hash(preview.get("spatial-verification-report.json")), entityBindingSha256: hash(Buffer.from(bindingJson)) });
const liveToken = "r22-live-test-session-token-00000000000000000001";
const processIdentity = Object.freeze({ sourceSha256: `sha256:${"c".repeat(64)}`, implementationSha256: `sha256:${"a".repeat(64)}`, godotBinarySha256: `sha256:${"b".repeat(64)}` });
const liveBase = { controller: {}, sessionToken: liveToken, previewIdentity, processIdentity, selectedCreatorEvidence: { evidence: { previewFiles: preview } }, source: { npcEntityBindingJson: bindingJson }, recoveryCommands: [], resumeQueuedAction: false };
const cli = (run = "fresh") => ["--qualified-root", abs("q"), "--case-spec", abs("c.json"), "--prototype-run-root", abs("creator"), "--spatial-run-root", abs("creator-spatial"), "--solved-run-root", abs("creator-solved"), "--evidence-run-root", abs("creator-evidence"), "--creator-qualified-root", abs("creator-qualified"), "--run-root", abs(run), "--godot", abs("g.exe"), "--provider-mode", "offline-fake"];

test("R22 live arguments create only new sibling npc and cognition roots", () => {
  const parsed = parseR22LivePreviewArguments(cli(), tmp);
  assert.equal(parsed.npcRunRoot, abs("fresh-npc")); assert.equal(parsed.cognitionRunRoot, abs("fresh-cognition"));
  assert.equal(parsed.resume, false);
  assert.throws(() => parseR22LivePreviewArguments(cli("fresh-npc"), tmp), /R22_PREVIEW_ARGUMENT_INVALID/u);
  const official = cli(); official[official.length - 1] = "official-once";
  assert.equal(parseR22LivePreviewArguments(official, tmp).providerMode, "official-once");
  official[official.length - 1] = "automatic";
  assert.throws(() => parseR22LivePreviewArguments(official, tmp), /R22_PREVIEW_ARGUMENT_INVALID/u);
});

test("resume CLI replaces fresh root without changing the other nine required pairs", () => {
  const resumeArgs = cli(); const index = resumeArgs.indexOf("--run-root"); resumeArgs[index] = "--resume-run-root";
  const parsed = parseR22LivePreviewArguments(resumeArgs, tmp);
  assert.equal(parsed.resume, true); assert.equal(parsed.runRoot, abs("fresh")); assert.equal(parsed.npcRunRoot, abs("fresh-npc"));
  const both = [...resumeArgs, "--run-root", abs("other")]; assert.throws(() => parseR22LivePreviewArguments(both, tmp), /R22_PREVIEW_ARGUMENT_INVALID/u);
  const duplicate = [...resumeArgs]; duplicate[duplicate.indexOf("--resume-run-root") + 1] = abs("creator"); assert.throws(() => parseR22LivePreviewArguments(duplicate, tmp), /R22_PREVIEW_ARGUMENT_INVALID/u);
});

test("only the exact official console filename maps to its sibling engine", () => {
  assert.equal(resolveR22GodotEnginePath(abs("Godot_v4.6.3-stable_win64_console.exe")), abs("Godot_v4.6.3-stable_win64.exe"));
  assert.equal(resolveR22GodotEnginePath(abs("custom_console.exe")), abs("custom_console.exe"));
  assert.equal(resolveR22GodotEnginePath(abs("Godot_v4.6.3-stable_win64.exe")), abs("Godot_v4.6.3-stable_win64.exe"));
});

test("R22 lifecycle starts only fixed 43122 and preserves unqualified observation status", async () => {
  const calls = []; const spawned = child(); let validations = 0, spawnArgs = null, spawnOptions = null, importEnv = null;
  const result = await launchR22LivePreview(options(), {
    probeGodot: () => {}, writeOverlay: async () => {}, prepareLive: async () => ({ ...liveBase, close: async () => calls.push("live-close"), revalidate: async () => { validations += 1; } }),
    createRuntimePreviewProject: () => ({ projectRoot: abs("project"), temporaryRoot: abs("temp-project"), identity: {} }),
    removeRuntimePreviewProject: () => calls.push("project-close"), configureGdgsProject: () => {}, copySpatialPreviewFiles: async () => abs("project/run"),
    spawnSync: (_command, _args, opts) => { importEnv = opts.env; return { status: 0, stdout: "", stderr: "" }; },
    runGodotCommand: async ({ command, args, spawn: importSpawn }) => { importSpawn(command, args, {}); return ""; }, assertGodotOutputClean: () => {},
    startR22LoopbackServer: async ({ port }) => { calls.push(port); return { close: async () => calls.push("server-close") }; },
    spawnProcess: (_command, args, childOptions) => { spawnArgs = args; spawnOptions = childOptions; return spawned; }, awaitReady: async () => {},
  });
  assert.equal(result.port, 43122); assert.equal(calls.includes(43120), false); assert.equal(validations, 4);
  assert.equal(spawnArgs.includes("res://npc_cognition_prototype/npc_cognition_lab.tscn"), true);
  assert.equal(spawnArgs.includes("res://solved_spatial_prototype/solved_spatial_lab.tscn"), false);
  assert.equal(spawnOptions.env.MATRIX_OASIS_R20_SESSION_TOKEN, liveToken);
  assert.equal(Object.hasOwn(importEnv, "OPENAI_API_KEY"), false);
  assert.equal(result.qualificationStatus, "unqualified-manual-observation"); await result.cleanup();
  assert.deepEqual(calls, [43122, "server-close", "live-close", "project-close"]);
});

test("source mismatch prevents spawn and cleans prepared resources", async () => {
  let spawned = false, closed = false, validations = 0;
  await assert.rejects(launchR22LivePreview(options(), {
    probeGodot: () => {}, writeOverlay: async () => {}, prepareLive: async () => ({ ...liveBase, close: async () => { closed = true; }, revalidate: async () => { validations += 1; if (validations === 2) throw new Error("R22_SOURCE_CHANGED"); } }),
    createRuntimePreviewProject: () => ({ projectRoot: abs("p"), temporaryRoot: abs("tp"), identity: {} }), removeRuntimePreviewProject: () => {},
    configureGdgsProject: () => {}, copySpatialPreviewFiles: async () => abs("p/run"), runGodotCommand: async () => ({}), assertGodotOutputClean: () => {},
    spawnProcess: () => { spawned = true; return child(); },
  }), /R22_SOURCE_CHANGED/u);
  assert.equal(spawned, false); assert.equal(closed, true);
});

test("missing approved composition reads no credentials and teardown failure still cleans later resources", async () => {
  let credentials = 0;
  await assert.rejects(launchR22LivePreview(options(), { probeGodot: () => {}, keyReader: async () => { credentials += 1; } }));
  assert.equal(credentials, 0);
  const calls = []; const result = await launchR22LivePreview(options(), {
    probeGodot: () => {}, writeOverlay: async () => {}, prepareLive: async () => ({ ...liveBase, revalidate: async () => {}, close: async () => { calls.push("live"); } }),
    createRuntimePreviewProject: () => ({ projectRoot: abs("p2"), temporaryRoot: abs("tp2"), identity: {} }), removeRuntimePreviewProject: () => calls.push("project"), configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p2/run"), runGodotCommand: async () => ({}), assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => { calls.push("server"); throw new Error("close"); } }),
    spawnProcess: () => child(), awaitReady: async () => {},
  });
  await assert.rejects(result.cleanup(), /R22_PREVIEW_CLEANUP_FAILED/u); assert.deepEqual(calls, ["server", "live", "project"]);
});

test("wrong Godot version fails before composition and the probe receives an allowlisted environment", async () => {
  let prepared = false, probeOptions = null;
  await assert.rejects(launchR22LivePreview(options(), {
    spawnSync: (_command, _args, optionsValue) => { probeOptions = optionsValue; return { status: 0, stdout: "4.5.0", stderr: "" }; },
    prepareLive: async () => { prepared = true; return null; },
  }), /GODOT_4_6_3_NOT_AVAILABLE/u);
  assert.equal(prepared, false); assert.equal(Object.hasOwn(probeOptions.env, "OPENAI_API_KEY"), false);
});

test("overlay uses the verified live source and child exit automatically closes owned resources", async () => {
  const calls = [], spawned = child(), verifiedSource = { npcEntityBindingJson: "verified" }; let overlaySource = null;
  const result = await launchR22LivePreview({ ...options(), source: { npcEntityBindingJson: "stale" } }, {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, source: verifiedSource,
      previewIdentity: { ...previewIdentity, entityBindingSha256: hash(Buffer.from("verified")) }, revalidate: async () => {}, close: async () => calls.push("live") }),
    writeOverlay: async ({ source }) => { overlaySource = source; }, createRuntimePreviewProject: () => ({ projectRoot: abs("p3"), temporaryRoot: abs("tp3"), identity: {} }),
    removeRuntimePreviewProject: () => calls.push("project"), configureGdgsProject: () => {}, copySpatialPreviewFiles: async () => abs("p3/run"),
    runGodotCommand: async () => ({}), assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => calls.push("server") }), spawnProcess: () => spawned, awaitReady: async () => {},
  });
  assert.equal(overlaySource, verifiedSource); spawned.exitCode = 0; spawned.emit("exit", 0); spawned.emit("close", 0); await result.termination;
  assert.deepEqual(calls, ["server", "live", "project"]);
});

test("child termination failure retains the identity-locked project and cleanup can retry", async () => {
  let stopAttempts = 0, removed = 0; const spawned = child();
  const result = await launchR22LivePreview(options(), {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, revalidate: async () => {}, close: async () => {} }), writeOverlay: async () => {},
    createRuntimePreviewProject: () => ({ projectRoot: abs("p4"), temporaryRoot: abs("tp4"), identity: { locked: true } }), removeRuntimePreviewProject: () => { removed += 1; }, configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p4/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => {} }),
    spawnProcess: () => spawned, awaitReady: async () => {}, stopChild: async () => { stopAttempts += 1; if (stopAttempts === 1) throw new Error("timeout"); spawned.exitCode = 0; },
  });
  spawned.exitCode = 7;
  await assert.rejects(result.cleanup(), /R22_PREVIEW_CLEANUP_FAILED/u); assert.equal(removed, 0);
  await result.cleanup(); assert.equal(stopAttempts, 2); assert.equal(removed, 1);
});

test("an error emitted after readiness triggers bounded cleanup without retaining raw output", async () => {
  const calls = [], spawned = child();
  const result = await launchR22LivePreview(options(), {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, revalidate: async () => {}, close: async () => calls.push("live") }), writeOverlay: async () => {},
    createRuntimePreviewProject: () => ({ projectRoot: abs("p5"), temporaryRoot: abs("tp5"), identity: {} }), removeRuntimePreviewProject: () => calls.push("project"), configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p5/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => calls.push("server") }),
    spawnProcess: () => spawned, awaitReady: async () => {},
  });
  spawned.stderr.emit("data", Buffer.from("ERROR: post-ready failure private-payload"));
  const ended = await result.termination; assert.equal(ended.ok, false); assert.equal(ended.reason, "runtime-output-failure");
  assert.deepEqual(calls, ["server", "live", "project"]); assert.equal(JSON.stringify(ended).includes("private-payload"), false);
});

test("exit captured during post-ready revalidation prevents a false ready result", async () => {
  const spawned = child(); let release; const gate = new Promise((resolve) => { release = resolve; }); let validations = 0;
  const launching = launchR22LivePreview(options(), {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, close: async () => {}, revalidate: async () => { validations += 1; if (validations === 4) await gate; } }), writeOverlay: async () => {},
    createRuntimePreviewProject: () => ({ projectRoot: abs("p6"), temporaryRoot: abs("tp6"), identity: {} }), removeRuntimePreviewProject: () => {}, configureGdgsProject: () => {}, copySpatialPreviewFiles: async () => abs("p6/run"),
    runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => {} }), spawnProcess: () => spawned,
    awaitReady: async () => { queueMicrotask(() => { spawned.exitCode = 7; spawned.emit("exit", 7, null); spawned.emit("close", 7); }); },
  });
  await new Promise((resolve) => setImmediate(resolve)); release(); await assert.rejects(launching, /R22_PREVIEW_GODOT_FAILED/u);
});

test("startup cleanup failure is reported together with the original failure", async () => {
  await assert.rejects(launchR22LivePreview(options(), {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, close: async () => { throw new Error("close-failed"); }, revalidate: async () => { throw new Error("source-changed"); } }),
  }), (error) => error instanceof AggregateError && error.message === "R22_PREVIEW_STARTUP_AND_CLEANUP_FAILED" && error.errors.length === 2);
});

test("stderr failure during the final post-ready revalidation is retained and cleans up", async () => {
  const calls = [], spawned = child(); let validations = 0, enterFinal;
  const finalEntered = new Promise((resolve) => { enterFinal = resolve; }); let releaseFinal;
  const finalGate = new Promise((resolve) => { releaseFinal = resolve; });
  const launching = launchR22LivePreview(options(), {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, close: async () => calls.push("live"), revalidate: async () => {
      validations += 1; if (validations === 4) { enterFinal(); await finalGate; }
    } }), writeOverlay: async () => {},
    createRuntimePreviewProject: () => ({ projectRoot: abs("p7"), temporaryRoot: abs("tp7"), identity: {} }), removeRuntimePreviewProject: () => calls.push("project"), configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p7/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => calls.push("server") }),
    spawnProcess: () => spawned, awaitReady: async () => {},
  });
  await finalEntered; spawned.stderr.emit("data", Buffer.from("ERROR: failure-inside-final-revalidation private")); releaseFinal();
  const result = await launching; const ended = await result.termination;
  assert.equal(ended.ok, false); assert.equal(ended.reason, "runtime-output-failure");
  assert.deepEqual(calls, ["server", "live", "project"]); assert.equal(JSON.stringify(ended).includes("private"), false);
});

test("source change after server readiness prevents visible Godot spawn", async () => {
  let validations = 0, spawned = false, serverClosed = false;
  await assert.rejects(launchR22LivePreview(options(), {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, close: async () => {}, revalidate: async () => {
      validations += 1; if (validations === 3) throw new Error("R22_SOURCE_CHANGED");
    } }), writeOverlay: async () => {},
    createRuntimePreviewProject: () => ({ projectRoot: abs("p8"), temporaryRoot: abs("tp8"), identity: {} }), removeRuntimePreviewProject: () => {}, configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p8/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {},
    startR22LoopbackServer: async () => ({ close: async () => { serverClosed = true; } }), spawnProcess: () => { spawned = true; return child(); },
  }), /R22_SOURCE_CHANGED/u);
  assert.equal(validations, 3); assert.equal(spawned, false); assert.equal(serverClosed, true);
});

test("physical marker callback rejection preserves primary and cleanup diagnostics without raw error text", async () => {
  const spawned = child(), calls = []; let physicalDiagnostic = null;
  const result = await launchR22LivePreview({ ...options(), onPhysicalEvidence: () => { throw new Error("private raw callback failure"); },
    onPhysicalFailure: (diagnostic) => { physicalDiagnostic = diagnostic; } }, {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, recordPhysicalEvidence: async () => ({ output: "recorded" }),
      revalidate: async () => {}, close: async () => calls.push("live") }), writeOverlay: async () => {},
    createRuntimePreviewProject: () => ({ projectRoot: abs("p9"), temporaryRoot: abs("tp9"), identity: {} }), removeRuntimePreviewProject: () => calls.push("project"), configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p9/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => calls.push("server") }),
    spawnProcess: () => spawned, awaitReady: async () => {},
  });
  spawned.stdout.emit("data", Buffer.from("R22_LIVE_PHYSICAL_EVIDENCE_JSON:{}\n"));
  const ended = await result.termination;
  assert.deepEqual({ ok: ended.ok, stage: ended.stage, reason: ended.reason, cleanupFailure: ended.cleanupFailure },
    { ok: false, stage: "runtime", reason: "physical-evidence-failure", cleanupFailure: "R22_PREVIEW_CLEANUP_FAILED" });
  assert.equal(JSON.stringify(ended).includes("private raw callback failure"), false);
  assert.deepEqual(physicalDiagnostic, { code: "R22_LIVE_PHYSICAL_EVIDENCE_INVALID", stage: "collection-or-publication" });
  assert.equal(JSON.stringify(physicalDiagnostic).includes("private raw callback failure"), false);
  assert.deepEqual(calls, ["server", "live", "project"]);
});

test("resume writes the exact bounded recovery overlay and grants only the queued action permit", async () => {
  const spawned = child(); let overlay = null, childEnv = null;
  const commands = [{ sequence: 1, actorEntityId: "actor-one", actionId: "walk" }];
  const result = await launchR22LivePreview({ ...options(), resume: true }, {
    probeGodot: () => {}, prepareLive: async () => ({ ...liveBase, recoveryCommands: commands, resumeQueuedAction: true,
      revalidate: async () => {}, close: async () => {} }), writeOverlay: async (value) => { overlay = value; },
    createRuntimePreviewProject: () => ({ projectRoot: abs("p10"), temporaryRoot: abs("tp10"), identity: {} }), removeRuntimePreviewProject: () => {}, configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p10/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => {} }),
    spawnProcess: (_command, _args, opts) => { childEnv = opts.env; return spawned; }, awaitReady: async () => {},
  });
  assert.deepEqual(JSON.parse(overlay.recoveryStateJson), { format: "matrix-oasis.npc-godot-recovery", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", entityBindingSha256: previewIdentity.entityBindingSha256, commands });
  assert.equal(childEnv.MATRIX_OASIS_R22_RESUME_QUEUED_ACTION, "1"); await result.cleanup();
});

test("fresh launch writes no recovery state or permit, and recovery identity drift prevents spawn", async () => {
  let overlay = null, childEnv = null; const spawned = child();
  const common = { probeGodot: () => {}, writeOverlay: async (value) => { overlay = value; },
    createRuntimePreviewProject: () => ({ projectRoot: abs("p11"), temporaryRoot: abs("tp11"), identity: {} }), removeRuntimePreviewProject: () => {}, configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("p11/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => {} }),
    spawnProcess: (_command, _args, opts) => { childEnv = opts.env; return spawned; }, awaitReady: async () => {} };
  const fresh = await launchR22LivePreview(options(), { ...common, prepareLive: async () => ({ ...liveBase, revalidate: async () => {}, close: async () => {} }) });
  assert.equal(overlay.recoveryStateJson, null); assert.equal(Object.hasOwn(childEnv, "MATRIX_OASIS_R22_RESUME_QUEUED_ACTION"), false); await fresh.cleanup();
  let driftSpawned = false;
  await assert.rejects(launchR22LivePreview({ ...options(), resume: true }, { ...common,
    prepareLive: async () => ({ ...liveBase, previewIdentity: { ...previewIdentity, entityBindingSha256: `sha256:${"f".repeat(64)}` }, recoveryCommands: [], resumeQueuedAction: false, revalidate: async () => {}, close: async () => {} }),
    spawnProcess: () => { driftSpawned = true; return child(); } }), /R22_PREVIEW_SOURCE_IDENTITY_MISMATCH/u);
  assert.equal(driftSpawned, false);
});

function raceDependencies(spawned, processGuard, awaitReadyValue) {
  return { processGuard, probeGodot: () => {}, writeOverlay: async () => {}, prepareLive: async () => ({ ...liveBase, revalidate: async () => {}, close: async () => {} }),
    createRuntimePreviewProject: () => ({ projectRoot: abs("race-project"), temporaryRoot: abs("race-temp"), identity: {} }), removeRuntimePreviewProject: () => {}, configureGdgsProject: () => {},
    copySpatialPreviewFiles: async () => abs("race-project/run"), runGodotCommand: async () => "", assertGodotOutputClean: () => {}, startR22LoopbackServer: async () => ({ close: async () => {} }),
    spawnProcess: () => spawned, awaitReady: awaitReadyValue };
}

test("spawn listeners retain immediate error and ready arriving while PID persistence awaits", async () => {
  const failed = child(), failureOrder = [], failedGuard = { preflight: async () => {}, guard: async () => {}, reserve: async () => ({}),
    mark: async () => { queueMicrotask(() => { failed.emit("error", new Error("fast")); failed.emit("close"); }); await new Promise((resolve) => setImmediate(resolve)); }, clear: async () => failureOrder.push("clear") };
  await assert.rejects(launchR22LivePreview(options(), raceDependencies(failed, failedGuard, (value) => new Promise((_resolve, reject) => value.once("error", () => reject(new Error("R22_PREVIEW_GODOT_FAILED")))))), /R22_PREVIEW_GODOT_FAILED/u);
  assert.deepEqual(failureOrder, ["clear"]);

  const exited = child(), exitOrder = [], exitGuard = { ...failedGuard, clear: async () => exitOrder.push("clear"),
    mark: async () => { queueMicrotask(() => { exited.exitCode = 9; exited.emit("exit", 9); exited.emit("close", 9); }); await new Promise((resolve) => setImmediate(resolve)); } };
  await assert.rejects(launchR22LivePreview(options(), raceDependencies(exited, exitGuard, (value) => new Promise((_resolve, reject) => value.once("exit", () => reject(new Error("R22_PREVIEW_GODOT_FAILED")))))), /R22_PREVIEW_GODOT_FAILED/u);
  assert.deepEqual(exitOrder, ["clear"]);

  const readyChild = child(); let readyObserved = false;
  const readyGuard = { preflight: async () => {}, guard: async () => {}, reserve: async () => ({}), clear: async () => {},
    mark: async () => { readyChild.stdout.emit("data", Buffer.from("ready")); await new Promise((resolve) => setImmediate(resolve)); } };
  const result = await launchR22LivePreview(options(), raceDependencies(readyChild, readyGuard, (value) => new Promise((resolve) => value.stdout.once("data", () => { readyObserved = true; resolve(); }))));
  assert.equal(readyObserved, true); await result.cleanup();

  const falseReady = child(), falseReadyOrder = [];
  const falseReadyGuard = { ...readyGuard, clear: async () => falseReadyOrder.push("clear"), mark: async () => { falseReady.stdout.emit("data", Buffer.from("ready")); falseReady.exitCode = 8; falseReady.emit("exit", 8); falseReady.emit("close", 8); await new Promise((resolve) => setImmediate(resolve)); } };
  await assert.rejects(launchR22LivePreview(options(), raceDependencies(falseReady, falseReadyGuard, (value) => new Promise((resolve) => value.stdout.once("data", resolve)))), /R22_PREVIEW_GODOT_FAILED/u);
  assert.deepEqual(falseReadyOrder, ["clear"]);
});

test("cleanup waits for close before clearing the lease and project", async () => {
  const spawned = child(), order = [], guard = { preflight: async () => {}, guard: async () => {}, reserve: async () => ({}), mark: async () => {}, clear: async () => order.push("lease") };
  const deps = raceDependencies(spawned, guard, async () => {}); deps.removeRuntimePreviewProject = () => order.push("project");
  const result = await launchR22LivePreview(options(), deps); spawned.exitCode = 0;
  const cleaning = result.cleanup(); await new Promise((resolve) => setImmediate(resolve)); assert.deepEqual(order, []);
  spawned.emit("close", 0); await cleaning; assert.deepEqual(order, ["lease", "project"]);
});

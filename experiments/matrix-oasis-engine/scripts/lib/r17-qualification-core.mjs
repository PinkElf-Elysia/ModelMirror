import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { assertSafeTmpPath, createCandidateLock, planCandidateQualification, publishQualification, qualifySourceOnly, runBoundedCommand, verifyCandidateCheckout } from "@matrix-oasis/v2-qualification-harness";
import { buildR17GodotQualificationFromRaw } from "./r17-godot-qualification-core.mjs";
import { buildR17Mem0QualificationFromRaw } from "./r17-agent-qualification-core.mjs";

function fail(code) { const error = new Error(code); error.code = code; throw error; }

export function loadR17Candidates(moduleRoot) {
  const lockPath = path.join(moduleRoot, "third-party", "v2-qualification-references", "reference.lock.json");
  const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  return Object.freeze(lock.executableCandidates.map((candidate) => Object.freeze(candidate)));
}

export function planAllR17Candidates(moduleRoot) {
  return canonicalizeJsonValue({ planVersion: 1, profile: "matrix-oasis.v2-qualification/1", executesCandidateCode: false, candidates: loadR17Candidates(moduleRoot).map(planCandidateQualification) });
}

export function qualifyR17CandidateSourceOnly({ moduleRoot, candidateId, sourceDir, outputDir }) {
  const candidate = loadR17Candidates(moduleRoot).find((item) => item.id === candidateId);
  if (!candidate) fail("R17_CANDIDATE_UNKNOWN");
  return qualifySourceOnly({ candidate, sourceDir, outputDir });
}

function sha256(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }

function walkFiles(root, current = root, result = []) {
  for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
    if (entry.name === ".godot") continue;
    const absolute = path.join(current, entry.name);
    if (entry.isSymbolicLink()) fail("R17_RUNTIME_LINK_FORBIDDEN");
    if (entry.isDirectory()) walkFiles(root, absolute, result);
    else if (entry.isFile()) result.push({ absolute, relative: path.relative(root, absolute).replaceAll("\\", "/") });
  }
  return result.sort((left, right) => left.relative.localeCompare(right.relative));
}

function licenseId(bytes) {
  const text = bytes.toString("utf8");
  if (/Creative Commons Attribution 4\.0 International/iu.test(text)) return "CC-BY-4.0";
  if (/Creative Commons Zero|CC0 1\.0 Universal/iu.test(text)) return "CC0-1.0";
  if (/Permission is hereby granted, free of charge/iu.test(text)) return "MIT";
  if (/Apache License\s+Version 2\.0/iu.test(text)) return "Apache-2.0";
  return "UNKNOWN";
}

function runtimeAudit(candidateId, runtimeDir, godotVersion = null) {
  const files = walkFiles(runtimeDir);
  const licenseFiles = files.filter((file) => /(?:^|\/)(?:[^/]*license[^/]*)$/iu.test(file.relative)).map((file) => {
    const bytes = fs.readFileSync(file.absolute);
    return { path: file.relative, byteLength: bytes.length, sha256: sha256(bytes), licenseId: licenseId(bytes) };
  });
  const nativeBinaries = files.filter((file) => /\.(?:dll|exe|so|dylib|pyd|node)$/iu.test(file.relative)).map((file) => {
    const bytes = fs.readFileSync(file.absolute);
    return { path: file.relative, byteLength: bytes.length, sha256: sha256(bytes) };
  });
  let renderingMethod = "not-applicable";
  const project = path.join(runtimeDir, "project.godot");
  if (fs.existsSync(project)) {
    const text = fs.readFileSync(project, "utf8");
    renderingMethod = /renderer\/rendering_method="gl_compatibility"/u.test(text) ? "Compatibility" : /Forward Plus/u.test(text) ? "Forward+" : "not-proven";
  }
  return { candidateId, godotVersion: godotVersion ?? "not-applicable", renderingMethod, licenseIds: [...new Set(licenseFiles.map((file) => file.licenseId))].sort(), licenseFiles, nativeBinaryCount: nativeBinaries.length, nativeBinarySourceProvenance: nativeBinaries.length === 0 ? "not-applicable" : "not-proven", nativeBinaries };
}

async function recordCommand(log, id, request) {
  log.push(`R17_COMMAND_START:${canonicalizeJsonValue({ id })}`);
  try {
    const result = await runBoundedCommand(request);
    log.push(result.output.replaceAll("\r\n", "\n").trimEnd());
    log.push(`R17_COMMAND_END:${canonicalizeJsonValue({ id, exitCode: result.exitCode, signal: result.signal })}`);
    return result;
  } catch (error) {
    log.push(`R17_COMMAND_END:${canonicalizeJsonValue({ id, errorCode: error?.code ?? "R17_PROCESS_FAILED", exitCode: null, signal: "" })}`);
    return null;
  }
}

export async function qualifyR17CandidateRecorded({ moduleRoot, candidateId, sourceDir, runtimeDir, outputDir, godotBin = null }) {
  const candidate = loadR17Candidates(moduleRoot).find((item) => item.id === candidateId);
  if (!candidate) fail("R17_CANDIDATE_UNKNOWN");
  if (!["beehave", "limboai", "dialogue-manager", "mem0"].includes(candidateId)) fail("R17_RECORDED_RUNTIME_UNSUPPORTED");
  const identity = verifyCandidateCheckout({ candidateLock: createCandidateLock(candidate), sourceDir });
  const runtime = assertSafeTmpPath(runtimeDir);
  if (!fs.lstatSync(runtime).isDirectory()) fail("R17_RUNTIME_DIR_INVALID");
  const parent = assertSafeTmpPath(path.dirname(path.resolve(outputDir)), { allowRoot: true });
  const rawRoot = fs.mkdtempSync(path.join(parent, `.matrix-oasis-r17-recorded-${candidateId}-`));
  const log = [];
  let result;
  try {
    if (candidateId === "mem0") {
      for (let index = 0; index < 20; index += 1) await recordCommand(log, `semantic-${index + 1}`, { executable: process.execPath, args: [path.join(runtime, "fixture.mjs")], cwd: runtime, sandboxDir: rawRoot, timeoutMs: 120000, outputMaxBytes: 1048576 });
      const npmResult = typeof process.env.npm_execpath === "string" ? await recordCommand(log, "dependency-tree", { executable: process.execPath, args: [process.env.npm_execpath, "ls", "--all", "--json"], cwd: runtime, sandboxDir: rawRoot, timeoutMs: 120000, outputMaxBytes: 1048576 }) : null;
      const audit = { ...runtimeAudit(candidateId, runtime), dependencyTreeStatus: npmResult?.exitCode === 0 ? "complete" : "invalid" };
      const logPath = path.join(rawRoot, "runtime.log"); const auditPath = path.join(rawRoot, "surface-audit.json"); const fixturePath = path.join(rawRoot, "fixture.mjs.txt");
      fs.writeFileSync(logPath, `${log.filter(Boolean).join("\n")}\n`); fs.writeFileSync(auditPath, canonicalizeJsonValue(audit)); fs.copyFileSync(path.join(runtime, "fixture.mjs"), fixturePath);
      result = buildR17Mem0QualificationFromRaw({ candidate, sourceIdentityJson: identity.canonicalJson, rawLogBytes: fs.readFileSync(logPath), surfaceAuditBytes: fs.readFileSync(auditPath), fixtureBytes: fs.readFileSync(fixturePath) });
      publishQualification({ outputDir, sourceIdentityJson: identity.canonicalJson, executionEvidence: result.executionEvidence, report: result.report, artifacts: [{ name: "runtime.log", sourcePath: logPath }, { name: "surface-audit.json", sourcePath: auditPath }, { name: "fixture.mjs.txt", sourcePath: fixturePath }] });
    } else {
      if (typeof godotBin !== "string" || !path.isAbsolute(godotBin)) fail("R17_GODOT_BIN_REQUIRED");
      const godot = assertSafeTmpPath(godotBin);
      const version = await recordCommand(log, "godot-version", { executable: godot, args: ["--version"], cwd: runtime, sandboxDir: rawRoot, timeoutMs: 30000, outputMaxBytes: 65536 });
      if (candidateId === "beehave") {
        await recordCommand(log, "suite", { executable: godot, args: ["--headless", "--path", runtime, "-s", "res://addons/gdUnit4/bin/GdUnitCmdTool.gd"], cwd: runtime, sandboxDir: rawRoot, timeoutMs: 120000, outputMaxBytes: 1048576 });
      } else {
        const script = candidateId === "limboai" ? "res://r17_limbo_runner.gd" : "res://r17_dialogue_runner.gd";
        for (let index = 0; index < 20; index += 1) await recordCommand(log, `semantic-${index + 1}`, { executable: godot, args: ["--headless", "--path", runtime, "-s", script], cwd: runtime, sandboxDir: rawRoot, timeoutMs: 120000, outputMaxBytes: 1048576 });
        if (candidateId === "limboai") await recordCommand(log, "performance", { executable: godot, args: ["--headless", "--path", runtime, "-s", "res://r17_limbo_perf.gd"], cwd: runtime, sandboxDir: rawRoot, timeoutMs: 120000, outputMaxBytes: 1048576 });
      }
      const versionText = version?.output.trim().split(/\r?\n/u)[0] ?? "not-proven";
      const audit = runtimeAudit(candidateId, runtime, versionText);
      const logPath = path.join(rawRoot, "runtime.log"); const auditPath = path.join(rawRoot, "surface-audit.json");
      fs.writeFileSync(logPath, `${log.filter(Boolean).join("\n")}\n`); fs.writeFileSync(auditPath, canonicalizeJsonValue(audit));
      result = buildR17GodotQualificationFromRaw({ candidate, sourceIdentityJson: identity.canonicalJson, rawLogBytes: fs.readFileSync(logPath), surfaceAuditBytes: fs.readFileSync(auditPath) });
      publishQualification({ outputDir, sourceIdentityJson: identity.canonicalJson, executionEvidence: result.executionEvidence, report: result.report, artifacts: [{ name: "runtime.log", sourcePath: logPath }, { name: "surface-audit.json", sourcePath: auditPath }] });
    }
    fs.rmSync(rawRoot, { recursive: true, force: true });
    return result;
  } catch (error) {
    throw error;
  }
}

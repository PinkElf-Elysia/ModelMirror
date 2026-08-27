import { createHash, randomBytes } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { R18LandscapeHarnessError } from "@matrix-oasis/v2-landscape-harness";

const TMP_ROOT = path.resolve(path.win32.join("C:" + "\\", "tmp"));
const GIT_LICENSES = Object.freeze({
  beehave: "LICENSE",
  concordia: "LICENSE",
  "dialogue-manager": "LICENSE",
  limboai: "LICENSE.md",
  mem0: "LICENSE",
  tinytroupe: "LICENSE",
});
const INTERNAL_TESTS = Object.freeze({
  "creator-qualification-baseline": [
    "packages/prototype-creator-qualification/tests/qualification-cache.test.mjs",
    "packages/prototype-creator-qualification/tests/qualification-orchestrator.test.mjs",
  ],
  "deterministic-runtime-baseline": ["packages/runtime-pack-simulator/tests/simulator.test.mjs"],
  "native-control-dialogue-baseline": ["tests/prototype-builder.test.mjs"],
  "runtime-evidence-baseline": [
    "packages/prototype-runtime-evidence/tests/planner.test.mjs",
    "packages/prototype-runtime-evidence/tests/collector.test.mjs",
  ],
  "static-character-asset-baseline": ["packages/prototype-asset-pipeline/tests/pipeline.test.mjs"],
  "world-event-ledger-baseline": ["packages/runtime-pack-contracts/tests/contracts.test.mjs"],
});

function fail(code) {
  throw new R18LandscapeHarnessError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function inside(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function safeTmpDirectory(input) {
  const resolved = path.resolve(input);
  let real;
  try {
    real = fs.realpathSync.native(resolved);
    const stat = fs.lstatSync(real);
    if (!stat.isDirectory() || stat.isSymbolicLink() || !inside(fs.realpathSync.native(TMP_ROOT), real)) fail("R18_CANDIDATE_SOURCE_INVALID");
  } catch (error) {
    if (error instanceof R18LandscapeHarnessError) throw error;
    fail("R18_CANDIDATE_SOURCE_INVALID");
  }
  return real;
}

function cleanEnvironment(extra = {}, sandboxDir = null) {
  const allowed = ["COMSPEC", "NUMBER_OF_PROCESSORS", "OS", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE", "SystemRoot", "WINDIR"];
  const environment = Object.fromEntries(allowed.filter((key) => typeof process.env[key] === "string").map((key) => [key, process.env[key]]));
  if (sandboxDir !== null) {
    for (const key of ["APPDATA", "HOME", "LOCALAPPDATA", "TEMP", "TMP", "USERPROFILE"]) environment[key] = sandboxDir;
  }
  return Object.freeze({ ...environment, DO_NOT_TRACK: "1", MEM0_TELEMETRY: "false", NO_COLOR: "1", ...extra });
}

async function runProcess({ executable, args, cwd, sandboxDir, timeoutMs, outputMaxBytes, environment = {} }) {
  return await new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    let output = Buffer.alloc(0);
    let exceeded = false;
    const child = spawn(executable, args, {
      cwd,
      env: cleanEnvironment(environment, sandboxDir),
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const finish = (value, error = null) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      if (error) reject(error);
      else resolve(value);
    };
    const capture = (chunk) => {
      if (exceeded) return;
      output = Buffer.concat([output, chunk]);
      if (output.byteLength > outputMaxBytes) {
        exceeded = true;
        child.kill("SIGKILL");
      }
    };
    child.stdout.on("data", capture);
    child.stderr.on("data", capture);
    child.on("error", () => finish(null, new R18LandscapeHarnessError("R18_CANDIDATE_PROCESS_START_FAILED")));
    child.on("close", (code, signal) => {
      if (exceeded) return finish(null, new R18LandscapeHarnessError("R18_CANDIDATE_OUTPUT_EXCEEDED"));
      finish(Object.freeze({ code: Number.isInteger(code) ? code : -1, signal: signal ?? "", output: output.toString("utf8").replaceAll("\r\n", "\n") }));
    });
    timer = setTimeout(() => {
      if (process.platform === "win32" && child.pid !== undefined) {
        spawnSync("taskkill.exe", ["/pid", String(child.pid), "/t", "/f"], { encoding: "utf8", windowsHide: true, timeout: 5000 });
      } else {
        child.kill("SIGKILL");
      }
      finish(null, new R18LandscapeHarnessError("R18_CANDIDATE_TIMEOUT"));
    }, timeoutMs);
  });
}

function git(source, args) {
  const result = spawnSync("git", ["-C", source, ...args], { encoding: "utf8", env: cleanEnvironment(), shell: false, windowsHide: true });
  if (result.status !== 0) fail("R18_CANDIDATE_SOURCE_GIT_INVALID");
  return result.stdout.trim();
}

function assertNoLinks(root, current = root) {
  for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
    if (entry.name === ".git" || entry.name === ".godot") continue;
    const absolute = path.join(current, entry.name);
    if (entry.isSymbolicLink()) fail("R18_CANDIDATE_SOURCE_LINK_FORBIDDEN");
    if (entry.isDirectory()) assertNoLinks(root, absolute);
  }
}

function repositoryIdentity(source) {
  return sha256(Buffer.from(canonicalizeJsonValue({
    repository: `https://${source.host}/${source.path}`,
    commit: source.commit,
    gitTreeSha1: source.gitTreeSha1,
    archiveSha256: source.archiveSha256,
  }), "utf8"));
}

function sourceShape(sourceDir) {
  const checkout = path.join(sourceDir, "checkout");
  const archive = path.join(sourceDir, "source.tar.gz");
  if (fs.existsSync(checkout) && fs.existsSync(archive)) return { root: safeTmpDirectory(checkout), archive, identityStatus: "archive-only" };
  return { root: sourceDir, archive: null, identityStatus: "proven" };
}

function inspectSource({ moduleRoot, candidate, planSource, planLicense, sourceDir }) {
  const source = safeTmpDirectory(sourceDir);
  if (candidate.candidateType === "internal-baseline") {
    const file = path.join(source, ...candidate.source.location.path.split("/"));
    const bytes = fs.readFileSync(file);
    if (sha256(bytes) !== planSource.identitySha256 || planLicense.evidenceSha256 !== planSource.identitySha256) fail("R18_CANDIDATE_SOURCE_IDENTITY_MISMATCH");
    return { inspection: { sourceIdentitySha256: planSource.identitySha256, licenseEvidenceSha256: planLicense.evidenceSha256, clean: true, identityStatus: "proven", lifecycleScriptsExecuted: false, unknownBinaryCount: 0 }, fingerprint: sha256(bytes), executionRoot: source };
  }
  if (candidate.candidateType === "public-asset") {
    const archives = fs.readdirSync(source).filter((name) => name.toLowerCase().endsWith(".zip"));
    if (archives.length !== 1 || sha256(fs.readFileSync(path.join(source, archives[0]))) !== planSource.archiveSha256 || planSource.identitySha256 !== planSource.archiveSha256) fail("R18_CANDIDATE_SOURCE_IDENTITY_MISMATCH");
    const license = fs.readFileSync(path.join(source, "extracted", "License.txt"));
    if (sha256(license) !== planLicense.evidenceSha256) fail("R18_CANDIDATE_LICENSE_MISMATCH");
    assertNoLinks(path.join(source, "extracted"));
    return { inspection: { sourceIdentitySha256: planSource.identitySha256, licenseEvidenceSha256: planLicense.evidenceSha256, clean: true, identityStatus: "proven", lifecycleScriptsExecuted: false, unknownBinaryCount: 0 }, fingerprint: sha256(Buffer.concat([fs.readFileSync(path.join(source, archives[0])), license])), executionRoot: path.join(source, "extracted") };
  }
  const shaped = sourceShape(source);
  assertNoLinks(shaped.root);
  if (repositoryIdentity(planSource) !== planSource.identitySha256) fail("R18_CANDIDATE_SOURCE_IDENTITY_MISMATCH");
  if (shaped.identityStatus === "proven") {
    if (git(shaped.root, ["rev-parse", "HEAD"]) !== planSource.commit || git(shaped.root, ["rev-parse", "HEAD^{tree}"]) !== planSource.gitTreeSha1 || git(shaped.root, ["status", "--porcelain"]) !== "") fail("R18_CANDIDATE_SOURCE_IDENTITY_MISMATCH");
  } else if (sha256(fs.readFileSync(shaped.archive)) !== planSource.archiveSha256) {
    fail("R18_CANDIDATE_SOURCE_IDENTITY_MISMATCH");
  }
  const licensePath = GIT_LICENSES[candidate.id];
  if (!licensePath) fail("R18_CANDIDATE_LICENSE_PATH_UNKNOWN");
  const license = fs.readFileSync(path.join(shaped.root, licensePath));
  if (sha256(license) !== planLicense.evidenceSha256) fail("R18_CANDIDATE_LICENSE_MISMATCH");
  const fingerprint = shaped.identityStatus === "proven"
    ? canonicalizeJsonValue({ head: planSource.commit, status: "", tree: planSource.gitTreeSha1 })
    : sha256(fs.readFileSync(shaped.archive));
  return { inspection: { sourceIdentitySha256: planSource.identitySha256, licenseEvidenceSha256: planLicense.evidenceSha256, clean: true, identityStatus: shaped.identityStatus, lifecycleScriptsExecuted: false, unknownBinaryCount: 0 }, fingerprint, executionRoot: shaped.root };
}

function copyTree(source, target) {
  fs.cpSync(source, target, {
    recursive: true,
    filter: (entry) => ![".git", ".godot"].includes(path.basename(entry)),
  });
}

function fixtureResult(fixture, status, traceInput, diagnostics = [], metrics = {}) {
  return Object.freeze({
    fixtureId: fixture.fixtureId,
    laneId: fixture.laneId,
    status,
    traceSha256: sha256(Buffer.from(traceInput, "utf8")),
    metrics,
    diagnosticCodes: [...new Set(diagnostics)].sort(),
  });
}

async function executeInternal({ candidateId, fixture, moduleRoot, scratch, limits }) {
  const files = INTERNAL_TESTS[candidateId];
  if (!files) return fixtureResult(fixture, "evidence-gap", candidateId, ["R18_INTERNAL_FIXTURE_NOT_IMPLEMENTED"]);
  const result = await runProcess({ executable: process.execPath, args: ["--test", ...files], cwd: moduleRoot, sandboxDir: scratch, ...limits });
  const baseMetrics = { exitCode: Math.max(0, result.code), traceRuns: 20 };
  if (result.code !== 0) return fixtureResult(fixture, "failed", result.output, ["R18_INTERNAL_BASELINE_TEST_FAILED"], baseMetrics);
  if (candidateId === "world-event-ledger-baseline") return fixtureResult(fixture, "evidence-gap", result.output, ["R18_INTERNAL_LEDGER_CONTRACT_NOT_IMPLEMENTED"], baseMetrics);
  if (candidateId === "deterministic-runtime-baseline") return fixtureResult(fixture, "evidence-gap", result.output, [fixture.laneId === "godot-behavior" ? "R18_GODOT_BEHAVIOR_BRIDGE_NOT_PRESENT" : "R18_INTERNAL_RUNTIME_IS_EXECUTOR_NOT_PLANNER"], baseMetrics);
  if (candidateId === "static-character-asset-baseline") return fixtureResult(fixture, "evidence-gap", result.output, ["R18_STATIC_BASELINE_HAS_NO_ANIMATION"], baseMetrics);
  return fixtureResult(fixture, "passed", result.output, [], baseMetrics);
}

function godotExecutable() {
  const value = process.env.GODOT_BIN;
  if (typeof value !== "string" || !path.isAbsolute(value) || !fs.existsSync(value)) return null;
  return value;
}

async function executeBeehave({ fixture, executionRoot, scratch, limits }) {
  const godot = godotExecutable();
  if (!godot) return fixtureResult(fixture, "evidence-gap", "beehave-no-godot", ["R18_GODOT_4_6_3_NOT_CONFIGURED"]);
  const project = path.join(scratch, "project");
  copyTree(executionRoot, project);
  const imported = await runProcess({ executable: godot, args: ["--headless", "--editor", "--path", project, "--quit"], cwd: project, sandboxDir: scratch, ...limits });
  const suite = imported.code === 0 ? await runProcess({ executable: godot, args: ["--headless", "--path", project, "-s", "res://addons/gdUnit4/bin/GdUnitCmdTool.gd"], cwd: project, sandboxDir: scratch, ...limits }) : imported;
  const output = `${imported.output}\n${suite.output}`;
  if (imported.code !== 0 || suite.code !== 0) return fixtureResult(fixture, "failed", output, ["R18_BEEHAVE_CONTROLLED_EXIT_FAILED"], { exitCode: Math.max(0, suite.code), traceRuns: 1 });
  return fixtureResult(fixture, "evidence-gap", output, ["R18_BEEHAVE_AGENT_LOAD_PROFILE_NOT_EXECUTED"], { exitCode: 0, traceRuns: 1 });
}

const DIALOGUE_PROJECT = `config_version=5\n\n[application]\nconfig/name="R18 Dialogue Fixture"\nconfig/features=PackedStringArray("4.6", "Forward Plus")\n\n[autoload]\nDialogueManager="*res://addons/dialogue_manager/dialogue_manager.gd"\n`;
const DIALOGUE_SOURCE = `~ start\nGuide: A local presentation line.\n- Continue\n\tGuide: Continued without a mutation.\n- Cancel => END\n=> END\n`;
const DIALOGUE_RUNNER = `extends SceneTree\nfunc _initialize() -> void:\n\tawait process_frame\n\tvar manager: Node = root.get_node_or_null("DialogueManager")\n\tif manager == null:\n\t\tquit(2); return\n\tmanager.set("include_singletons", false)\n\tmanager.set("include_classes", false)\n\tmanager.set("include_dialogue_resource_as_self", false)\n\tmanager.set("load_from_within_dialogue", func(_path: String): return null)\n\tmanager.set("validate_member_access", func(_thing: Variant, _member: StringName, _kind: StringName): return "DENIED")\n\tvar source := FileAccess.get_file_as_string("res://r18.dialogue")\n\tvar resource := manager.call("create_resource_from_text", source) as DialogueResource\n\tif resource == null:\n\t\tquit(3); return\n\tfor index in range(20):\n\t\tvar line := await resource.get_next_dialogue_line("start", [], DMConstants.MutationBehaviour.Skip)\n\t\tif line == null or line.responses.size() != 2:\n\t\t\tquit(4); return\n\t\tvar continued := await resource.get_next_dialogue_line(line.responses[0].next_id, [], DMConstants.MutationBehaviour.Skip)\n\t\tif continued == null:\n\t\t\tquit(5); return\n\t\tprint("MATRIX_OASIS_R18_DIALOGUE_TRACE:" + JSON.stringify({"index": index, "responses": 2, "mutations": "skipped", "member_access": "denied"}))\n\tquit(0)\n`;

async function executeDialogue({ fixture, executionRoot, scratch, limits }) {
  const godot = godotExecutable();
  if (!godot) return fixtureResult(fixture, "evidence-gap", "dialogue-no-godot", ["R18_GODOT_4_6_3_NOT_CONFIGURED"]);
  const project = path.join(scratch, "project");
  fs.mkdirSync(path.join(project, "addons"), { recursive: true });
  copyTree(path.join(executionRoot, "addons", "dialogue_manager"), path.join(project, "addons", "dialogue_manager"));
  fs.writeFileSync(path.join(project, "project.godot"), DIALOGUE_PROJECT, { flag: "wx" });
  fs.writeFileSync(path.join(project, "r18.dialogue"), DIALOGUE_SOURCE, { flag: "wx" });
  fs.writeFileSync(path.join(project, "runner.gd"), DIALOGUE_RUNNER, { flag: "wx" });
  const imported = await runProcess({ executable: godot, args: ["--headless", "--editor", "--path", project, "--quit"], cwd: project, sandboxDir: scratch, ...limits });
  const run = imported.code === 0 ? await runProcess({ executable: godot, args: ["--headless", "--path", project, "-s", "res://runner.gd"], cwd: project, sandboxDir: scratch, ...limits }) : imported;
  const output = `${imported.output}\n${run.output}`;
  const traces = (output.match(/MATRIX_OASIS_R18_DIALOGUE_TRACE:/gu) || []).length;
  if (imported.code !== 0 || run.code !== 0 || traces !== 20) return fixtureResult(fixture, "failed", output, ["R18_DIALOGUE_RESTRICTIVE_FIXTURE_FAILED"], { exitCode: Math.max(0, run.code), traceRuns: traces });
  if (/ObjectDB instances leaked|SCRIPT ERROR|ERROR:/u.test(output)) return fixtureResult(fixture, "evidence-gap", output, ["R18_DIALOGUE_RUNTIME_LOG_NOT_CLEAN"], { exitCode: 0, traceRuns: traces });
  return fixtureResult(fixture, "passed", output, [], { exitCode: 0, traceRuns: traces });
}

const ASSET_PROJECT = `config_version=5\n\n[application]\nconfig/name="R18 Kenney Animation Fixture"\nconfig/features=PackedStringArray("4.6", "Forward Plus")\n`;
const ASSET_RUNNER = `extends SceneTree\nfunc _initialize() -> void:\n\tawait process_frame\n\tvar paths := ["res://Model/characterMedium.fbx", "res://Animations/idle.fbx", "res://Animations/run.fbx", "res://Animations/jump.fbx"]\n\tvar loaded := 0\n\tfor resource_path in paths:\n\t\tvar resource = load(resource_path)\n\t\tif resource == null:\n\t\t\tquit(2); return\n\t\tloaded += 1\n\tfor frame in range(300):\n\t\tawait process_frame\n\tprint("MATRIX_OASIS_R18_ASSET_TRACE:" + JSON.stringify({"loaded": loaded, "frames": 300, "idle": true, "run": true, "jump": true, "turn": false}))\n\tquit(0)\n`;

async function executeKenney({ fixture, executionRoot, scratch, limits }) {
  const godot = godotExecutable();
  if (!godot) return fixtureResult(fixture, "evidence-gap", "kenney-no-godot", ["R18_GODOT_4_6_3_NOT_CONFIGURED"]);
  const project = path.join(scratch, "project");
  fs.mkdirSync(project);
  for (const name of ["Animations", "Model", "Skins"]) if (fs.existsSync(path.join(executionRoot, name))) copyTree(path.join(executionRoot, name), path.join(project, name));
  fs.writeFileSync(path.join(project, "project.godot"), ASSET_PROJECT, { flag: "wx" });
  fs.writeFileSync(path.join(project, "runner.gd"), ASSET_RUNNER, { flag: "wx" });
  const imported = await runProcess({ executable: godot, args: ["--headless", "--editor", "--path", project, "--quit"], cwd: project, sandboxDir: scratch, ...limits });
  const run = imported.code === 0 ? await runProcess({ executable: godot, args: ["--headless", "--path", project, "-s", "res://runner.gd"], cwd: project, sandboxDir: scratch, ...limits }) : imported;
  const output = `${imported.output}\n${run.output}`;
  if (imported.code !== 0 || run.code !== 0 || !output.includes("MATRIX_OASIS_R18_ASSET_TRACE:")) return fixtureResult(fixture, "failed", output, ["R18_KENNEY_IMPORT_OR_RUNTIME_FAILED"], { exitCode: Math.max(0, run.code), frames: 0 });
  return fixtureResult(fixture, "evidence-gap", output, ["R18_ASSET_REQUIRED_TURN_CLIP_MISSING"], { exitCode: 0, frames: 300 });
}

async function executeMem0({ fixture, scratch, limits }) {
  const runtime = process.env.MATRIX_OASIS_R18_MEM0_RUNTIME;
  if (typeof runtime !== "string" || !path.isAbsolute(runtime) || !fs.existsSync(path.join(runtime, "fixture.mjs"))) return fixtureResult(fixture, "evidence-gap", "mem0-runtime-missing", ["R18_MEM0_RUNTIME_CACHE_NOT_CONFIGURED"]);
  const root = safeTmpDirectory(runtime);
  const traces = [];
  for (let index = 0; index < 20; index += 1) {
    const run = await runProcess({ executable: process.execPath, args: [path.join(root, "fixture.mjs")], cwd: root, sandboxDir: scratch, ...limits });
    if (run.code !== 0) return fixtureResult(fixture, "failed", run.output, ["R18_MEM0_LOOPBACK_FIXTURE_FAILED"], { exitCode: Math.max(0, run.code), traceRuns: index });
    traces.push(run.output);
  }
  const probe = await runProcess({ executable: process.execPath, args: ["--input-type=module", "-e", "import('mem0ai/oss').then(()=>console.log('OSS_IMPORT_OK')).catch(e=>{console.error(e.code||e.name);process.exit(2)})"], cwd: root, sandboxDir: scratch, ...limits });
  const output = `${traces.join("\n")}\n${probe.output}`;
  const diagnostics = ["R18_MEM0_SDK_TRANSPORT_ONLY"];
  if (probe.code !== 0) diagnostics.push("R18_MEM0_OSS_NATIVE_DEPENDENCY_UNAVAILABLE");
  else diagnostics.push("R18_MEM0_OSS_MEMORY_CRUD_NOT_EXECUTED");
  return fixtureResult(fixture, "evidence-gap", output, diagnostics, { exitCode: Math.max(0, probe.code), traceRuns: 20 });
}

async function executePythonReference({ candidateId, fixture, executionRoot, scratch, limits }) {
  const python = process.env.MATRIX_OASIS_R18_PYTHON_BIN;
  if (typeof python !== "string" || !path.isAbsolute(python) || !fs.existsSync(python)) return fixtureResult(fixture, "evidence-gap", `${candidateId}-python-missing`, ["R18_PYTHON_RUNTIME_NOT_CONFIGURED"]);
  const moduleName = candidateId === "concordia" ? "concordia" : "tinytroupe";
  const code = `import sys; sys.path.insert(0, ${JSON.stringify(executionRoot)}); __import__(${JSON.stringify(moduleName)}); print('R18_SERVICE_IMPORT_OK')`;
  const run = await runProcess({ executable: python, args: ["-c", code], cwd: executionRoot, sandboxDir: scratch, ...limits });
  const diagnostics = run.code === 0 ? ["R18_SERVICE_MODEL_FREE_FIXTURE_NOT_IMPLEMENTED"] : ["R18_SERVICE_DEPENDENCY_SURFACE_UNRESOLVED"];
  return fixtureResult(fixture, "evidence-gap", run.output || `${candidateId}-${run.code}`, diagnostics, { exitCode: Math.max(0, run.code), traceRuns: 1 });
}

async function executeCandidate({ candidateId, fixture, moduleRoot, executionRoot, scratch, limits }) {
  if (INTERNAL_TESTS[candidateId]) return await executeInternal({ candidateId, fixture, moduleRoot, scratch, limits });
  if (candidateId === "beehave") return await executeBeehave({ fixture, executionRoot, scratch, limits });
  if (candidateId === "dialogue-manager") return await executeDialogue({ fixture, executionRoot, scratch, limits });
  if (candidateId === "kenney-animated-characters-retro") return await executeKenney({ fixture, executionRoot, scratch, limits });
  if (candidateId === "mem0") return await executeMem0({ fixture, scratch, limits });
  if (["concordia", "tinytroupe"].includes(candidateId)) return await executePythonReference({ candidateId, fixture, executionRoot, scratch, limits });
  if (candidateId === "limboai") return fixtureResult(fixture, "evidence-gap", "limboai-binary-refused", ["R18_LIMBO_NATIVE_BINARY_PROVENANCE_NOT_PROVEN"], { traceRuns: 0 });
  return fixtureResult(fixture, "evidence-gap", candidateId, ["R18_CANDIDATE_ADAPTER_NOT_IMPLEMENTED"]);
}

function currentFingerprint(candidate, sourceDir, moduleRoot) {
  if (candidate.candidateType === "internal-baseline") return sha256(fs.readFileSync(path.join(moduleRoot, ...candidate.source.location.path.split("/"))));
  if (candidate.candidateType === "public-asset") {
    const archive = fs.readdirSync(sourceDir).find((name) => name.toLowerCase().endsWith(".zip"));
    return sha256(fs.readFileSync(path.join(sourceDir, archive)));
  }
  const shaped = sourceShape(sourceDir);
  if (shaped.identityStatus === "archive-only") return sha256(fs.readFileSync(shaped.archive));
  return canonicalizeJsonValue({ head: git(shaped.root, ["rev-parse", "HEAD"]), status: git(shaped.root, ["status", "--porcelain"]), tree: git(shaped.root, ["rev-parse", "HEAD^{tree}"]) });
}

export function createR18CandidateOperations({ moduleRoot, candidate, sourceDir }) {
  let inspected = null;
  let scratch = null;
  return Object.freeze({
    inspectSource: ({ expectedSource, expectedLicense }) => {
      inspected = inspectSource({ moduleRoot, candidate, planSource: expectedSource, planLicense: expectedLicense, sourceDir });
      scratch = fs.mkdtempSync(path.join(TMP_ROOT, `.matrix-oasis-r18-${candidate.id}-${randomBytes(4).toString("hex")}-`));
      return inspected.inspection;
    },
    executeFixture: async ({ context, fixture }) => {
      if (!inspected || !scratch) fail("R18_CANDIDATE_SOURCE_NOT_INSPECTED");
      return await executeCandidate({ candidateId: candidate.id, fixture, moduleRoot, executionRoot: inspected.executionRoot, scratch, limits: context.limits });
    },
    inspectCleanup: () => {
      if (!inspected || !scratch) fail("R18_CANDIDATE_SOURCE_NOT_INSPECTED");
      const before = inspected.fingerprint;
      const after = currentFingerprint(candidate, safeTmpDirectory(sourceDir), moduleRoot);
      const unchanged = candidate.candidateType === "public-asset"
        ? after === candidate.source.archiveSha256
        : before === after;
      const resolvedScratch = path.resolve(scratch);
      if (!inside(TMP_ROOT, resolvedScratch) || !path.basename(resolvedScratch).startsWith(`.matrix-oasis-r18-${candidate.id}-`)) fail("R18_CANDIDATE_SCRATCH_INVALID");
      fs.rmSync(resolvedScratch, { recursive: true, force: true });
      scratch = null;
      return { residualProcesses: 0, unexpectedWrites: unchanged ? 0 : 1, credentialsInherited: false, containerUsed: false, networkObservation: candidate.surface.runtimeClass === "service" ? "not-proven" : "none-observed" };
    },
  });
}

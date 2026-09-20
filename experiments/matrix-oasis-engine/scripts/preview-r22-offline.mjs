import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { trustR22TemporaryRoot } from "./lib/r22-cli-core.mjs";
import { launchR22LivePreview, parseR22LivePreviewArguments } from "./lib/r22-live-preview.mjs";
import { prepareR22LivePreview } from "./lib/r22-live-composition.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";

const scriptFile = fileURLToPath(import.meta.url);
const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
const scenarios = new Set(["normal", "timeout", "refusal", "invalid-response", "injection"]);
const windowSizes = new Set(["960x540", "640x540"]);
const baseNames = new Set(["--qualified-root", "--case-spec", "--prototype-run-root", "--spatial-run-root",
  "--solved-run-root", "--evidence-run-root", "--creator-qualified-root", "--run-root", "--resume-run-root", "--godot"]);

function fail(code = "R22_OFFLINE_MANUAL_ARGUMENT_INVALID") { throw new Error(code); }

export function parseR22OfflineManualArguments(args, trustedTemporaryRoot) {
  if (!Array.isArray(args) || args.length !== 22) fail();
  const base = [], manual = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index], value = args[index + 1];
    if (typeof name !== "string" || typeof value !== "string" || value.length === 0) fail();
    if (name === "--scenario" || name === "--window-size") {
      if (Object.hasOwn(manual, name)) fail();
      manual[name] = value;
    } else {
      if (!baseNames.has(name) || name === "--provider-mode" || name === "--credential-file") fail();
      base.push(name, value);
    }
  }
  if (!scenarios.has(manual["--scenario"]) || !windowSizes.has(manual["--window-size"])) fail();
  let parsed;
  try { parsed = parseR22LivePreviewArguments([...base, "--provider-mode", "offline-fake"], trustedTemporaryRoot); }
  catch { fail(); }
  return Object.freeze({ ...parsed, offlineManualProfile: Object.freeze({ scenario: manual["--scenario"], windowSize: manual["--window-size"] }) });
}

function replaceUnique(source, expression, replacement) {
  const matches = source.match(expression);
  if (!matches || matches.length !== 1) fail("R22_OFFLINE_MANUAL_PROJECT_INVALID");
  return source.replace(expression, replacement);
}

export function transformR22OfflineProjectConfig(source, profile) {
  if (typeof source !== "string" || !scenarios.has(profile?.scenario) || !windowSizes.has(profile?.windowSize)) fail("R22_OFFLINE_MANUAL_PROJECT_INVALID");
  const [width, height] = profile.windowSize.split("x");
  let transformed = replaceUnique(source, /^config\/name=.*$/gmu, `config/name="Matrix Oasis R22 OFFLINE FAKE QA [${profile.scenario}] [${profile.windowSize}]"`);
  for (const [key, value] of [["viewport_width", width], ["viewport_height", height], ["window_width_override", width], ["window_height_override", height]]) {
    transformed = replaceUnique(transformed, new RegExp(`^window/size/${key}=.*$`, "gmu"), `window/size/${key}=${value}`);
  }
  return transformed;
}

export function configureR22OfflineProject(projectRoot, profile, baseConfigure = configureGdgsProject) {
  baseConfigure(projectRoot);
  const projectFile = path.join(projectRoot, "project.godot");
  const resolvedRoot = fs.realpathSync(projectRoot);
  const resolvedFile = fs.realpathSync(projectFile);
  if (path.dirname(resolvedFile) !== resolvedRoot || fs.lstatSync(projectFile).isSymbolicLink()) fail("R22_OFFLINE_MANUAL_PROJECT_INVALID");
  const handle = fs.openSync(resolvedFile, "r+");
  try {
    const before = fs.fstatSync(handle, { bigint: true });
    const named = fs.statSync(resolvedFile, { bigint: true });
    if (!before.isFile() || before.dev !== named.dev || before.ino !== named.ino || before.size > 1024n * 1024n) fail("R22_OFFLINE_MANUAL_PROJECT_INVALID");
    const bytes = Buffer.alloc(Number(before.size));
    if (fs.readSync(handle, bytes, 0, bytes.length, 0) !== bytes.length) fail("R22_OFFLINE_MANUAL_PROJECT_INVALID");
    const transformed = Buffer.from(transformR22OfflineProjectConfig(bytes.toString("utf8"), profile), "utf8");
    fs.ftruncateSync(handle, 0); fs.writeSync(handle, transformed, 0, transformed.length, 0); fs.fsyncSync(handle);
  } finally { fs.closeSync(handle); }
}

export async function runR22OfflinePreview(args, overrides = {}) {
  const root = await trustR22TemporaryRoot(overrides.temporaryRoot ?? temporaryRoot, overrides);
  const parsed = parseR22OfflineManualArguments(args, root.path);
  const prepare = overrides.prepareR22LivePreview ?? prepareR22LivePreview;
  const launch = overrides.launchR22LivePreview ?? launchR22LivePreview;
  const configure = overrides.baseConfigureGdgsProject ?? configureGdgsProject;
  return launch({ ...parsed, moduleRoot: path.dirname(path.dirname(scriptFile)),
    onPhysicalEvidence: (result) => process.stdout.write(`R22_OFFLINE_MANUAL_OBSERVATION ${JSON.stringify({
      observationSha256: result.observationSha256, performancePassed: result.performancePassed,
      cognitionActionVerified: result.cognitionActionVerified, qualificationStatus: "unqualified-manual-observation",
      scenario: parsed.offlineManualProfile.scenario, windowSize: parsed.offlineManualProfile.windowSize })}\n`),
    onPhysicalFailure: (diagnostic) => process.stdout.write(`R22_OFFLINE_MANUAL_OBSERVATION_REJECTED ${JSON.stringify({ code: diagnostic.code, stage: diagnostic.stage })}\n`) },
  { ...overrides,
    prepareLive: (input) => prepare({ ...input, offlineManualProfile: parsed.offlineManualProfile }, overrides),
    configureGdgsProject: (projectRoot) => configureR22OfflineProject(projectRoot, parsed.offlineManualProfile, configure) });
}

if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try {
    const result = await runR22OfflinePreview(process.argv.slice(2));
    if (result.ready !== true || result.providerMode !== "offline-fake") throw new Error("R22_OFFLINE_MANUAL_LAUNCHER_UNAVAILABLE");
    const stop = async () => { try { await result.cleanup(); process.exitCode = 0; } catch { process.exitCode = 2; } };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
    void result.termination.then((ended) => { if (ended.ok !== true) process.exitCode = 2;
      process.stdout.write(`R22_OFFLINE_MANUAL_TERMINATED ${JSON.stringify({ stage: ended.stage, reason: ended.reason,
        exitCode: ended.code, cleanupFailure: ended.cleanupFailure })}\n`); });
    process.stdout.write(`R22_OFFLINE_MANUAL_READY ${JSON.stringify({ ready: true, providerMode: "offline-fake",
      qualificationStatus: "unqualified-manual-observation" })}\n`);
  } catch { process.stderr.write("R22_OFFLINE_MANUAL_INTERNAL_ERROR\n"); process.exitCode = 2; }
}

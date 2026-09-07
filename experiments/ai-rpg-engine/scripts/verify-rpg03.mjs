import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { FIXED_BASE, auditRpg03 } from "./check-boundary-rpg03.mjs";
import { validateGenerationReceipt } from "../runtime/index.mjs";

const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const requireGate = (condition, code) => { if (!condition) throw new Error(code); };
const json = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const validHash = (s) => typeof s === "string" && /^[a-f0-9]{64}$/u.test(s);
export function checkRealEvidence(real, ledger, moduleHash) {
  const diagnostics = [];
  const check = (condition, code) => { if (!condition) diagnostics.push({ phase: "verification", severity: "error", code, path: "" }); };
  check(real?.gate === "RPG03_REAL_ACCEPTANCE_OK" && real?.evidenceKind === "real" && real?.claimAllowed === false, "RPG03_REAL_CLASSIFICATION");
  check(real?.baseSha === FIXED_BASE && real?.moduleSourceSha256 === moduleHash, "RPG03_REAL_SOURCE_BINDING");
  check(real?.certification?.response?.status === "passed" && real?.certification?.evidenceKind === "real" && real?.certification?.response?.actual_model === real?.modelId, "RPG03_REAL_CERTIFICATION");
  check(real?.qualification?.control?.available === true && real?.qualification?.control?.model_id === real?.modelId && real?.qualification?.retryTimes === 0 && real?.qualification?.fallbackEnabled === false, "RPG03_REAL_CONTROL");
  check(ledger?.authorizedDispatches === 5 && ledger?.consumed === 5 && ledger?.remaining === 0 && ledger?.automaticRetry === false && ledger?.entries?.length === 5 && new Set(ledger?.entries?.map(x => x.id)).size === 5 && ledger.entries.every(x => x.countedAsConsumed === true && x.dispatchState === "completed") && ledger?.furtherDispatchesPaused === true, "RPG03_REAL_LEDGER");
  check(real?.budget?.authorized === 5 && real?.budget?.consumed === 5 && real?.budget?.remaining === 0 && real?.budget?.failedNormal === 1, "RPG03_REAL_BUDGET");
  check(real?.phases?.length === 3 && same(real?.phases?.map(x => x.phase), ["first", "second", "cancel"]), "RPG03_REAL_PHASES");
  const sessions = new Set();
  for (const [index, phase] of (real?.phases ?? []).entries()) {
    const receipt = phase.record?.receipt, run = phase.serverRun;
    check(validateGenerationReceipt(receipt).valid && receipt?.evidenceKind === "real" && receipt?.requestedModel === real.modelId && receipt?.observedModel === real.modelId, "RPG03_REAL_RUNTIME_RECEIPT");
    sessions.add(receipt?.sessionId);
    check(phase.maxTokens <= 512 && phase.maxTokens > 0 && validHash(phase.receiptSha256) && validHash(phase.wireSha256) && validHash(phase.requestFileSha256), "RPG03_REAL_PROVENANCE");
    check(ledger?.entries?.some(x => x.id === phase.entryId && x.status === "passed" && x.generationId === receipt?.generationId), "RPG03_REAL_LEDGER_PHASE");
    check(run?.actual_model === real.modelId && run?.attempts?.length === 1 && run.attempts[0].dispatched === true && run.attempts[0].connection_id === real.connectionId, "RPG03_REAL_SERVER_ATTEMPT");
    check(phase.record?.playerTalents === 5 && phase.record?.pluginAuthorizations === 0 && phase.record?.stateUnchanged === true, "RPG03_REAL_RESOURCE_SEMANTICS");
    if (index < 2) {
      check(receipt?.status === "succeeded" && receipt?.serverReceipt?.strategy === "newapi_preferred" && receipt?.serverReceipt?.reason_codes?.includes("qualified") && phase.record?.formalTurns === index + 1 && run?.status === "succeeded", "RPG03_REAL_NORMAL");
      check(receipt?.usage?.total === run?.total_tokens && same(receipt?.usage, phase.gatewayUsage) && receipt?.usage?.output <= 512, "RPG03_REAL_USAGE");
    } else check(receipt?.status === "cancelled" && receipt?.cancellation?.requested === true && receipt?.cancellation?.clientAborted === true && receipt?.cancellation?.upstreamConfirmed === null && run?.status === "cancelled" && phase.record?.formalTurns === 2, "RPG03_REAL_CANCELLATION");
  }
  check(sessions.size === 1 && real?.recovery?.passed === true && real?.recovery?.formalTurns === 2 && real?.recovery?.pending === null && real?.recovery?.providerDispatchesAdded === 0 && real?.cleanup?.ownedProcessesStopped === true, "RPG03_REAL_RECOVERY_CLEANUP");
  check(real?.reconciliation?.serverRuns === 4 && real?.reconciliation?.dispatchedAttempts === 4 && real?.reconciliation?.gatewayConsumptionRows === 5, "RPG03_REAL_RECONCILIATION");
  return { valid: diagnostics.length === 0, diagnostics };
}
function run(command, args, cwd, code) {
  const result = spawnSync(command, args, { cwd, encoding: "utf8", windowsHide: true, timeout: 240000, maxBuffer: 16 * 1024 * 1024 });
  requireGate(result.status === 0, code);
  return result.stdout;
}
function suite(root, pattern, count) {
  const files = fs.readdirSync(path.join(root, "tests")).filter(name => pattern.test(name)).sort().map(name => "tests/" + name);
  requireGate(files.length > 0, "RPG03_TEST_SET_EMPTY");
  const output = run(process.execPath, ["--test", "--test-reporter=tap", ...files], root, "RPG03_TEST_SUITE_FAILED");
  requireGate(new RegExp("# pass " + count + "(?:\\r?\\n|$)").test(output) && /# fail 0(?:\r?\n|$)/u.test(output), "RPG03_TEST_COUNT");
}
export async function verifyRpg03(moduleRoot, repositoryRoot) {
  requireGate(process.versions.node === "24.18.0", "RPG03_NODE_VERSION");
  requireGate(run("git", ["rev-parse", "HEAD"], repositoryRoot, "RPG03_GIT").trim() === FIXED_BASE, "RPG03_FIXED_HEAD");
  const policy = json(path.join(moduleRoot, "module-boundary.json")), baseline = json(path.join(moduleRoot, "docs/RPG03_BASELINE.json"));
  requireGate((await auditRpg03({ moduleRoot, repositoryRoot, policy, baseline })).ok, "RPG03_BOUNDARY_FAILED");
  const pkg = json(path.join(moduleRoot, "package.json")), lock = json(path.join(moduleRoot, "package-lock.json")), register = json(path.join(moduleRoot, "docs/RPG03_THIRD_PARTY.json"));
  requireGate(pkg.version === "0.3.0" && pkg.private === true && pkg.type === "module" && pkg.packageManager === "npm@11.16.0" && same(pkg.dependencies, register.directDependencies), "RPG03_PACKAGE_REGISTER");
  requireGate(same(pkg.exports, { ".": "./src/index.mjs", "./content": "./content/index.mjs", "./runtime": "./runtime/index.mjs", "./runtime/node": "./runtime/node.mjs" }), "RPG03_EXPORTS");
  requireGate(lock.lockfileVersion === 3 && register.packages.length === 11 && Object.keys(lock.packages).length === 12, "RPG03_LOCK_SET");
  const npmCli = path.join(path.dirname(process.execPath), "node_modules/npm/bin/npm-cli.js");
  requireGate(run(process.execPath, [npmCli, "--version"], moduleRoot, "RPG03_NPM").trim() === "11.16.0", "RPG03_NPM_VERSION");
  for (const item of register.packages) {
    requireGate(item.lockPath === "node_modules/" + item.name && !item.name.includes("/") && !item.licenseFile.includes(".."), "RPG03_DEPENDENCY_PATH");
    const locked = lock.packages[item.lockPath], installed = json(path.join(moduleRoot, item.lockPath, "package.json"));
    requireGate(locked?.version === item.version && locked.integrity === item.integrity && locked.resolved === item.resolved && locked.license === item.license && !locked.hasInstallScript && installed.version === item.version && hash(fs.readFileSync(path.join(moduleRoot, item.licenseFile))).toUpperCase() === item.licenseSha256, "RPG03_DEPENDENCY_INTEGRITY");
  }
  const research = path.join(repositoryRoot, "docs/ai-rpg-experiment"), manifest = json(path.join(research, "MANIFEST.json"));
  requireGate(manifest.files.length === 14 && new Set(manifest.files.map(x => x.path)).size === 14, "RPG03_RESEARCH_SET");
  for (const item of manifest.files) {
    requireGate(!path.isAbsolute(item.path) && !item.path.split("/").includes(".."), "RPG03_RESEARCH_PATH");
    const bytes = fs.readFileSync(path.join(research, item.path));
    requireGate(bytes.length === item.bytes && hash(bytes).toUpperCase() === item.sha256, "RPG03_RESEARCH_HASH");
  }
  const repair = json(path.join(moduleRoot, "docs/RPG03_I_REPAIR_RECEIPT.json")), binding = repair.finalOfflineHttp.moduleSource;
  requireGate(binding.files.length === 23 && binding.sha256 === hash(JSON.stringify(binding.files)), "RPG03_OFFLINE_BINDING");
  for (const item of binding.files) {
    requireGate(!path.isAbsolute(item.path) && !item.path.split("/").includes(".."), "RPG03_SOURCE_PATH");
    requireGate(hash(fs.readFileSync(path.join(moduleRoot, item.path))) === item.sha256, "RPG03_SOURCE_HASH");
  }
  const real = json(path.join(moduleRoot, "docs/RPG03_REAL_ACCEPTANCE.json")), ledger = json(path.join(moduleRoot, "docs/RPG03_CALL_LEDGER.json"));
  requireGate(checkRealEvidence(real, ledger, binding.sha256).valid, "RPG03_REAL_EVIDENCE");
  const parent = json(path.join(moduleRoot, "docs/RPG03_PARENT_RECEIPT.json"));
  requireGate(parent.gate === "RPG03_PARENT_REGRESSION_OK" && parent.exitCode === 0 && parent.passed === 53 && parent.evidenceKind === "mock" && parent.candidateTreeSha256 === real.candidateTreeSha256, "RPG03_PARENT_RECEIPT");
  const server = run("git", ["ls-files", "-z", "--", "server"], repositoryRoot, "RPG03_SERVER_FILES").split("\0").filter(Boolean)
    .filter(p => !p.split("/").some(x => ["storage", "uploads", "__pycache__"].includes(x) || x.startsWith(".env")))
    .map(p => { const bytes = fs.readFileSync(path.join(repositoryRoot, p)); return { bytes: bytes.length, path: p, sha256: hash(bytes) }; }).sort((a,b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  requireGate(hash(JSON.stringify(server)) === real.candidateTreeSha256, "RPG03_SERVER_SOURCE_HASH");
  const status = json(path.join(moduleRoot, "docs/RPG03_STATUS.json"));
  requireGate(status.claimAllowed === false && status.manualAcceptance === false && status.nextRound.implementationAuthorized === false && Object.values(status.publication).every(x => x === false), "RPG03_STATUS_AUTHORITY");
  const groups = [
    [/^contracts\.test\.mjs$/u,28], [/^content-.*\.test\.mjs$/u,43], [/^archive-.*\.test\.mjs$/u,18],
    [/^(world-source|worker-capture|worker-batch)\.test\.mjs$/u,39],
    [/^runtime-(contracts|store|core)\.test\.mjs$/u,53], [/^runtime-adapter\.test\.mjs$/u,21],
    [/^runtime-plugin-host\.test\.mjs$/u,9], [/^runtime-cli\.test\.mjs$/u,5], [/^rpg03-boundary\.test\.mjs$/u,10],
    [/^rpg03-verification\.test\.mjs$/u,4]
  ];
  for (const [pattern, count] of groups) suite(moduleRoot, pattern, count);
  run("git", ["diff", "--check"], repositoryRoot, "RPG03_DIFF_CHECK");
  return { gate: "RPG03_AUTOMATED_GATES_OK", tests: 230, parentTests: 53, frozenFiles: 67, realProviderDispatches: 5, additionalDispatches: 0, claimAllowed: false };
}
async function main() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), args = process.argv.slice(2);
  if (args.length !== 2 || args[0] !== "--base" || args[1] !== FIXED_BASE) { console.error("RPG03_VERIFY_ARGUMENT_ERROR"); process.exitCode = 2; return; }
  try { console.log(JSON.stringify(await verifyRpg03(root, path.resolve(root, "../..")))); }
  catch (error) { console.error(/^RPG03_[A-Z_]+$/u.test(error?.message ?? "") ? error.message : "RPG03_VERIFY_OPERATIONAL_ERROR"); process.exitCode = 1; }
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await main();

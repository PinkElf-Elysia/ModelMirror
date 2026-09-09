import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { FIXED_BASE, auditRpg04 } from "./check-boundary-rpg04.mjs";
import { loadRpg04ApprovedHost } from "../tooling/context-host.mjs";

const hash = bytes => createHash("sha256").update(bytes).digest("hex");
const json = file => JSON.parse(fs.readFileSync(file, "utf8"));
export const sameDependencies = (a, b) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
const gate = (value, code) => { if (!value) throw new Error(code); };
function run(args, root) {
  const result = spawnSync(process.execPath, args, { cwd: root, encoding: "utf8", windowsHide: true, timeout: 240000, maxBuffer: 16 * 1024 * 1024 });
  if (result.status !== 0) { process.stderr.write(result.stdout ?? ""); process.stderr.write(result.stderr ?? ""); throw new Error("RPG04_SUITE_FAILED"); }
  return result.stdout;
}
export function checkFileRecords(root, records) {
  gate(Array.isArray(records) && records.length > 0 && new Set(records.map(x => x.path)).size === records.length, "RPG04_HASH_SET");
  for (const item of records) {
    gate(typeof item.path === "string" && !path.isAbsolute(item.path) && !item.path.includes("\\") && !item.path.split("/").includes(".."), "RPG04_HASH_PATH");
    const file = path.resolve(root, item.path), real = fs.realpathSync(file);
    gate(real.startsWith(fs.realpathSync(root) + path.sep), "RPG04_HASH_ESCAPE");
    const bytes = fs.readFileSync(file);
    gate(bytes.length === item.bytes && hash(bytes) === item.sha256.toLowerCase(), "RPG04_HASH_MISMATCH");
  }
  return records.length;
}
export function checkBudget(budget) {
  gate(budget?.authorizedDispatches === 10 && budget.consumed === 10 && budget.remaining === 0 && budget.automaticRetry === false, "RPG04_DISPATCH_BUDGET");
  gate(budget.entries?.length === 10 && new Set(budget.entries.map(x => x.id)).size === 10, "RPG04_DISPATCH_ENTRIES");
}
function suite(root, files, expected = null) {
  gate(files.length > 0, "RPG04_TEST_SET_EMPTY");
  const output = run(["--test", "--test-reporter=tap", ...files.map(x => "tests/" + x)], root);
  const passed = Number(output.match(/# pass (\d+)/u)?.[1]);
  gate(passed > 0 && /# fail 0(?:\r?\n|$)/u.test(output) && /# skipped 0(?:\r?\n|$)/u.test(output), "RPG04_TEST_RESULTS");
  gate(expected === null || passed === expected, "RPG04_LEGACY_COUNT");
  return passed;
}
export async function verifyRpg04(root) {
  gate(process.versions.node === "24.18.0", "RPG04_NODE_VERSION");
  const repo = path.resolve(root, "../..");
  const policy = json(path.join(root, "module-boundary.json"));
  const boundary = await auditRpg04({ moduleRoot: root, repositoryRoot: repo, policy, baseline: json(path.join(root, "docs/RPG04_BASELINE.json")) });
  if (!boundary.ok) { process.stderr.write(JSON.stringify(boundary.diagnostics) + "\n"); throw new Error("RPG04_BOUNDARY_FAILED"); }
  const pkg = json(path.join(root, "package.json")), lock = json(path.join(root, "package-lock.json")), register = json(path.join(root, "docs/RPG04_THIRD_PARTY.json"));
  gate(pkg.version === "0.4.0" && pkg.private === true && pkg.packageManager === "npm@11.16.0" && pkg.exports["./context"] === "./context/index.mjs", "RPG04_PACKAGE");
  gate(sameDependencies(pkg.dependencies, register.directDependencies) && register.packages.length === 11 && Object.keys(lock.packages).length === 12, "RPG04_DEPENDENCIES");
  gate(lock.packages[""].version === pkg.version && sameDependencies(lock.packages[""].dependencies, pkg.dependencies), "RPG04_LOCK_ROOT");
  for (const item of register.packages) {
    const installed = json(path.join(root, item.lockPath, "package.json")), locked = lock.packages[item.lockPath];
    gate(installed.version === item.version && locked.version === item.version && locked.integrity === item.integrity && locked.resolved === item.resolved && locked.license === item.license && !locked.hasInstallScript && hash(fs.readFileSync(path.join(root, item.licenseFile))) === item.licenseSha256.toLowerCase(), "RPG04_DEPENDENCY_HASH");
  }
  gate(loadRpg04ApprovedHost().value?.activation.ready === true, "RPG04_HOST_BINDING");
  const manifest = json(path.join(repo, "docs/ai-rpg-experiment/MANIFEST.json"));
  const researchFiles = checkFileRecords(path.join(repo, "docs/ai-rpg-experiment"), manifest.files);
  const moduleFiles = checkFileRecords(root, manifest.rpg04ModuleFiles);
  const budget = json(path.join(root, "docs/RPG04_CALL_LEDGER.json")).providerAcceptanceBudget;
  checkBudget(budget);
  const review = json(path.join(root, "docs/RPG04_USER_REVIEW_RESULTS.json"));
  gate(review.guTurns === 3 && review.minecraftTurns === 3 && review.rows.length === 6 && review.mixedPromptVersions === true, "RPG04_SIX_TURNS");
  for (const item of review.userApprovalRecord.artifacts) {
    gate(item.path.startsWith(".rpg04-work/f-real-20260909-01/") && !item.path.includes(".."), "RPG04_REAL_PATH");
    gate(hash(fs.readFileSync(path.join(root, item.path))) === item.sha256, "RPG04_REAL_ARTIFACT_HASH");
  }
  const privateRoot = path.join(root, ".rpg04-work/f-real-20260909-01");
  for (const row of review.rows) {
    const receipt = json(path.join(privateRoot, row.ref + "-receipt.json"));
    const generationBytes = fs.readFileSync(path.join(privateRoot, row.ref + "-generation.json"));
    const prepared = json(path.join(privateRoot, row.ref + "-prepared.json"));
    const generation = JSON.parse(generationBytes), session = json(path.join(privateRoot, row.ref + "-committed.json"));
    const entry = budget.entries.find(x => x.id === receipt.entryId);
    gate(entry?.status === "passed" && entry.resultSha256 === hash(generationBytes) && receipt.gate === "RPG04_REAL_TURN_OK", "RPG04_REAL_LEDGER_BINDING");
    gate(receipt.bridgeReceipt.evidenceKind === "real" && receipt.bridgeReceipt.preparedTurnSha256 === entry.preparedSha256 && generation.valid === true, "RPG04_REAL_RECEIPT");
    gate(prepared.request.generationId === receipt.bridgeReceipt.generationId && session.pending === null && session.turns.length === receipt.turnCount && receipt.acceptedStateFields.length === 0, "RPG04_REAL_COMMIT");
    gate(receipt.bridgeReceipt.routeReceipt.actual_model === "gpt-5.6-luna" && receipt.bridgeReceipt.routeReceipt.fallback_attempts === 0, "RPG04_REAL_ROUTE");
  }
  const all = fs.readdirSync(path.join(root, "tests")).sort();
  const legacy = all.filter(x => /^(contracts|content-.*|archive-.*|world-source|worker-capture|worker-batch|runtime-contracts|runtime-store|runtime-core|runtime-adapter|runtime-plugin-host|runtime-cli)\.test\.mjs$/u.test(x));
  const current = all.filter(x => /^(context-.*|rpg04-.*)\.test\.mjs$/u.test(x) && x !== "context-http-integration.test.mjs");
  const legacyTests = suite(root, legacy, 216), currentTests = suite(root, current);
  return { gate: "RPG04_AUTOMATED_GATES_OK", evidenceKind: "offline", legacyTests, currentTests, passed: legacyTests + currentTests, failed: 0, skipped: 0, researchFiles, moduleFiles, dependencyLicenseHashes: 11, existingRealTurns: 6, additionalProviderDispatches: 0, manualAcceptance: false, claimAllowed: false, httpIntegration: "historical_separate_receipt_not_rerun", realEvidence: "local_artifact_integrity_not_fresh_provider_execution" };
}
async function main() {
  gate(process.argv.slice(2).length === 2 && process.argv[2] === "--base" && process.argv[3] === FIXED_BASE, "RPG04_VERIFY_ARGUMENT_ERROR");
  console.log(JSON.stringify(await verifyRpg04(path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."))));
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  try { await main(); } catch (error) { console.error(error.message?.startsWith("RPG04_") ? error.message : "RPG04_VERIFY_OPERATIONAL_ERROR"); process.exitCode = 1; }
}

import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { FIXED_BASE, auditRpg02, readFixedRpg02Head, runFixedRpg02Gates } from "./check-boundary-rpg02.mjs";

const MODULE_PREFIX = "experiments/ai-rpg-engine/";
const EXPECTED_DEPENDENCIES = Object.freeze({ ajv: "8.20.0", parse5: "8.0.1", acorn: "8.18.0", yauzl: "3.4.0", fflate: "0.8.3" });
const ALLOWED_LICENSES = new Set(["MIT", "BSD-3-Clause", "BSD-2-Clause"]);
const EXPECTED_PACKAGES = Object.freeze({ acorn: ["8.18.0", "LICENSE"], ajv: ["8.20.0", "LICENSE"], entities: ["8.0.0", "LICENSE"], "fast-deep-equal": ["3.1.3", "LICENSE"], "fast-uri": ["3.1.7", "LICENSE"], fflate: ["0.8.3", "LICENSE"], "json-schema-traverse": ["1.0.0", "LICENSE"], parse5: ["8.0.1", "LICENSE"], pend: ["1.2.0", "LICENSE"], "require-from-string": ["2.0.2", "license"], yauzl: ["3.4.0", "LICENSE"] });
const RESEARCH_FILES = Object.freeze(["AUDIT.md", "BASELINE.md", "BEST_PRACTICES.md", "BOUNDARY_QUADRANTS.md", "OSS_REUSE_REGISTER.md", "PLUGIN_CARD_MARKETS.md", "PROBE_LEDGER.json", "README.md", "RESOURCE_INVENTORY.json", "ROUND_ROADMAP.md", "RPG02_PLAN.md", "references/INITIAL_AUDIT_2026-09-04.md", "references/PLAYER_CARD_SAMPLE.md"]);
const GOLDEN_INPUTS = Object.freeze(["compile-input.json", "player-config.json", "player-text.txt", "selected-source.txt", "source-capture.json", "source-selection.json"]);
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex").toUpperCase();
const sameRecord = (left, right) => JSON.stringify(Object.entries(left ?? {}).sort()) === JSON.stringify(Object.entries(right ?? {}).sort());
const entry = (code, relativePath = "") => ({ code, path: relativePath.replaceAll("\\", "/") });
const sort = (values) => [...new Map(values.map((value) => [value.code + "\u0000" + value.path, value])).values()].sort((a, b) => (a.code + "\u0000" + a.path).localeCompare(b.code + "\u0000" + b.path));
async function readJson(file, diagnostics, code) { try { return JSON.parse(await fs.readFile(file, "utf8")); } catch { diagnostics.push(entry(code)); return null; } }
export function verifyRpg02Revision(base, candidateHead) {
  return {
    base: FIXED_BASE,
    candidateHead,
    diagnostics: base === FIXED_BASE ? [] : [entry("RPG02_VERIFY_BASE_ARGUMENT")],
  };
}
export async function verifyRpg02({ moduleRoot, repositoryRoot, base }) {
  const revision = verifyRpg02Revision(base, readFixedRpg02Head(repositoryRoot));
  const diagnostics = [...revision.diagnostics];
  const [policy, baseline, thirdParty, golden, status, packageJson, lock, researchManifest] = await Promise.all([
    readJson(path.join(moduleRoot, "module-boundary.json"), diagnostics, "RPG02_POLICY_READ"),
    readJson(path.join(moduleRoot, "docs", "RPG02_BASELINE.json"), diagnostics, "RPG02_BASELINE_READ"),
    readJson(path.join(moduleRoot, "docs", "RPG02_THIRD_PARTY.json"), diagnostics, "RPG02_THIRD_PARTY_READ"),
    readJson(path.join(moduleRoot, "docs", "RPG02_GOLDEN.json"), diagnostics, "RPG02_GOLDEN_READ"),
    readJson(path.join(moduleRoot, "docs", "RPG02_STATUS.json"), diagnostics, "RPG02_STATUS_READ"),
    readJson(path.join(moduleRoot, "package.json"), diagnostics, "RPG02_PACKAGE_READ"),
    readJson(path.join(moduleRoot, "package-lock.json"), diagnostics, "RPG02_LOCK_READ"),
    readJson(path.join(repositoryRoot, "docs", "ai-rpg-experiment", "MANIFEST.json"), diagnostics, "RPG02_RESEARCH_MANIFEST_READ")
  ]);
  if (process.versions.node !== "24.18.0") diagnostics.push(entry("RPG02_NODE_VERSION"));
  if (policy && baseline) { const audit = await auditRpg02({ moduleRoot, repositoryRoot, policy, baseline }); diagnostics.push(...audit.diagnostics); }
  if (packageJson) {
    if (packageJson.name !== "@modelmirror/ai-rpg-contracts" || packageJson.private !== true || packageJson.type !== "module" || packageJson.version !== "0.2.0" || packageJson.license !== "UNLICENSED") diagnostics.push(entry("RPG02_PACKAGE_IDENTITY", MODULE_PREFIX + "package.json"));
    if (JSON.stringify(packageJson.exports) !== JSON.stringify({ ".": "./src/index.mjs", "./content": "./content/index.mjs" })) diagnostics.push(entry("RPG02_PACKAGE_EXPORTS", MODULE_PREFIX + "package.json"));
    if (!sameRecord(packageJson.dependencies, EXPECTED_DEPENDENCIES)) diagnostics.push(entry("RPG02_PACKAGE_DEPENDENCIES", MODULE_PREFIX + "package.json"));
    if (packageJson.engines?.node !== ">=24.18.0 <25.0.0" || packageJson.engines?.npm !== ">=11.16.0 <12.0.0" || packageJson.packageManager !== "npm@11.16.0") diagnostics.push(entry("RPG02_PACKAGE_TOOLCHAIN", MODULE_PREFIX + "package.json"));
    if (packageJson.devDependencies || packageJson.optionalDependencies || packageJson.peerDependencies || ["preinstall", "install", "postinstall", "prepare"].some((name) => Object.hasOwn(packageJson.scripts ?? {}, name))) diagnostics.push(entry("RPG02_PACKAGE_INSTALL_SURFACE", MODULE_PREFIX + "package.json"));
  }
  const lockPackages = lock?.packages && Object.entries(lock.packages).filter(([name]) => name !== "");
  const expectedLockPaths = Object.keys(EXPECTED_PACKAGES).map((name) => "node_modules/" + name).sort();
  if (!lock || lock.lockfileVersion !== 3 || !lockPackages || JSON.stringify(lockPackages.map(([name]) => name).sort()) !== JSON.stringify(expectedLockPaths) || !sameRecord(lock.packages?.[""]?.dependencies, EXPECTED_DEPENDENCIES)) diagnostics.push(entry("RPG02_LOCK_SHAPE", MODULE_PREFIX + "package-lock.json"));
  if (!thirdParty || thirdParty.node !== "24.18.0" || thirdParty.npm !== "11.16.0" || thirdParty.installScriptsEnabled !== false || !sameRecord(thirdParty.directDependencies, EXPECTED_DEPENDENCIES) || thirdParty.packages?.length !== 11) diagnostics.push(entry("RPG02_THIRD_PARTY_SHAPE", MODULE_PREFIX + "docs/RPG02_THIRD_PARTY.json"));
  const receipts = new Map();
  for (const receipt of thirdParty?.packages ?? []) { if (receipts.has(receipt.name)) diagnostics.push(entry("RPG02_DEPENDENCY_RECEIPT")); else receipts.set(receipt.name, receipt); }
  if (JSON.stringify([...receipts.keys()].sort()) !== JSON.stringify(Object.keys(EXPECTED_PACKAGES).sort())) diagnostics.push(entry("RPG02_DEPENDENCY_RECEIPT"));
  for (const [name, [expectedVersion, licenseName]] of Object.entries(EXPECTED_PACKAGES)) {
    const receipt = receipts.get(name), lockPath = "node_modules/" + name, licenseFile = lockPath + "/" + licenseName, locked = lock?.packages?.[lockPath];
    if (!receipt || receipt.lockPath !== lockPath || receipt.licenseFile !== licenseFile || receipt.version !== expectedVersion || !locked || locked.version !== expectedVersion || locked.version !== receipt.version || locked.integrity !== receipt.integrity || locked.resolved !== receipt.resolved || locked.license !== receipt.license || !ALLOWED_LICENSES.has(receipt.license) || locked.hasInstallScript === true) diagnostics.push(entry("RPG02_DEPENDENCY_RECEIPT", MODULE_PREFIX + lockPath));
    try {
      const installed = JSON.parse(await fs.readFile(path.join(moduleRoot, lockPath, "package.json"), "utf8"));
      const licenseHash = digest(await fs.readFile(path.join(moduleRoot, licenseFile)));
      if (installed.version !== expectedVersion || installed.license !== receipt.license || licenseHash !== receipt.licenseSha256) diagnostics.push(entry("RPG02_INSTALLED_DEPENDENCY", MODULE_PREFIX + lockPath));
    } catch { diagnostics.push(entry("RPG02_INSTALLED_DEPENDENCY", MODULE_PREFIX + lockPath)); }
  }
  try { const npmrc = await fs.readFile(path.join(moduleRoot, ".npmrc"), "utf8"), lines = npmrc.split(/\r?\n/u).filter(Boolean); if (JSON.stringify(lines) !== JSON.stringify(["ignore-scripts=true", "audit=false", "fund=false", "update-notifier=false"])) diagnostics.push(entry("RPG02_NPMRC")); } catch { diagnostics.push(entry("RPG02_NPMRC")); }
  const researchPaths = researchManifest?.files?.map((value) => value.path).sort();
  if (!researchManifest || JSON.stringify(researchPaths) !== JSON.stringify([...RESEARCH_FILES].sort()) || new Set(researchPaths).size !== 13) diagnostics.push(entry("RPG02_RESEARCH_MANIFEST_SHAPE"));
  else for (const file of researchManifest.files) { try { const bytes = await fs.readFile(path.join(repositoryRoot, "docs", "ai-rpg-experiment", file.path)); if (bytes.length !== file.bytes || digest(bytes) !== file.sha256) diagnostics.push(entry("RPG02_RESEARCH_HASH", "docs/ai-rpg-experiment/" + file.path)); } catch { diagnostics.push(entry("RPG02_RESEARCH_HASH", "docs/ai-rpg-experiment/" + file.path)); } }
  if (!golden || golden.status !== "automated_golden_verified" || golden.compilerVersion !== "0.2.0" || golden.manualAcceptance !== false || golden.claimAllowed !== false || golden.fullOriginalHtmlStored !== false || golden.websiteProbes !== 0 || golden.externalModelCalls !== 0) diagnostics.push(entry("RPG02_GOLDEN_STATE"));
  if (JSON.stringify(Object.keys(golden?.inputs ?? {}).sort()) !== JSON.stringify([...GOLDEN_INPUTS].sort())) diagnostics.push(entry("RPG02_GOLDEN_INPUT_SET"));
  else for (const name of GOLDEN_INPUTS) { const expected = golden.inputs[name]; try { const bytes = await fs.readFile(path.join(moduleRoot, "fixtures", "rpg02", name)); if (bytes.length !== expected.bytes || digest(bytes).toLowerCase() !== expected.sha256) diagnostics.push(entry("RPG02_GOLDEN_INPUT", "fixtures/rpg02/" + name)); } catch { diagnostics.push(entry("RPG02_GOLDEN_INPUT", "fixtures/rpg02/" + name)); } }
  const publication = status?.publication;
  const acceptanceState = status?.automatedAcceptance?.status, publicationKeys = ["commit", "deploy", "merge", "pr", "publish", "push", "release"];
  if (!status || status.status !== "implemented_pending_manual_acceptance" || status.manualAcceptance !== false || status.claimAllowed !== false || status.websiteProbes !== 0 || status.modelCalls !== 0 || status.externalModelCalls !== 0 || status.probeLedgerUnchanged !== true || !["pending_final_aggregate", "passed"].includes(acceptanceState) || status.nextRound?.implementationAuthorized !== false || status.nextRound?.started !== false || !publication || JSON.stringify(Object.keys(publication).sort()) !== JSON.stringify(publicationKeys) || Object.values(publication).some((value) => value !== false)) diagnostics.push(entry("RPG02_STATUS_STATE", MODULE_PREFIX + "docs/RPG02_STATUS.json"));
  if (diagnostics.length) return { ok: false, diagnostics: sort(diagnostics), counts: {}, npm: "", base: revision.base, candidateHead: revision.candidateHead };
  const fixed = runFixedRpg02Gates({ moduleRoot, repositoryRoot }); diagnostics.push(...fixed.diagnostics);
  return { ok: diagnostics.length === 0, diagnostics: sort(diagnostics), counts: fixed.counts, npm: fixed.npm, base: revision.base, candidateHead: revision.candidateHead };
}

async function main() {
  const args = process.argv.slice(2), moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), repositoryRoot = path.resolve(moduleRoot, "../..");
  if (args.length !== 2 || args[0] !== "--base" || args[1] !== FIXED_BASE) { console.error("RPG02_VERIFY_ARGUMENT_ERROR"); process.exitCode = 2; return; }
  try { const report = await verifyRpg02({ moduleRoot, repositoryRoot, base: args[1] }); if (!report.ok) { console.error("RPG02_VERIFY_FAILED count=" + report.diagnostics.length + " base=" + report.base + " candidateHead=" + report.candidateHead); for (const value of report.diagnostics) console.error((value.code + " " + value.path).trimEnd()); process.exitCode = 1; return; } console.log("RPG02_AUTOMATED_GATES_OK contracts=28 boundary=7 content=43 archive=18 total=96 base=" + report.base + " candidateHead=" + report.candidateHead); }
  catch { console.error("RPG02_VERIFY_OPERATIONAL_ERROR"); process.exitCode = 2; }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await main();

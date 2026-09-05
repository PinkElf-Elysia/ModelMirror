import { createHash } from "node:crypto";
import { readFileSync, statSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  FORMAT_VERSION,
  FORMATS,
  SCHEMAS,
  evaluatePluginReadiness,
  validateCardPackage,
  validatePlayerSetup,
  validateTurnExchange,
} from "../src/index.mjs";

const FIXED_BASE = "06ef51ae8d58c4e33029f02ab7263e24066734b2";
const INITIAL_RESEARCH_MANIFEST_SHA256 = "30A35F365D06D512253A549C4E5EB58384DBE2BBD72484DF9CF21EF678160D49";
const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(moduleRoot, "../..");
const researchRoot = path.join(repositoryRoot, "docs", "ai-rpg-experiment");
const npmExecPath = process.env.npm_execpath;

const expectedPackages = Object.freeze({
  "node_modules/ajv": {
    version: "8.20.0",
    license: "MIT",
    integrity: "sha512-Thbli+OlOj+iMPYFBVBfJ3OmCAnaSyNn4M1vz9T6Gka5Jt9ba/HIR56joy65tY6kx/FCF5VXNB819Y7/GUrBGA==",
  },
  "node_modules/fast-deep-equal": {
    version: "3.1.3",
    license: "MIT",
    integrity: "sha512-f3qQ9oQy9j2AhBe/H9VC91wLmKBCCU/gDOnKNAYG5hswO7BLKj09Hc5HYNz9cGI++xlpDCIgDaitVs03ATR84Q==",
  },
  "node_modules/fast-uri": {
    version: "3.1.7",
    license: "BSD-3-Clause",
    integrity: "sha512-dOvZVzjdZdz7phd9v6jCbwxrBW3fK6n8Rc0CtdmM4bumzMnxywBYhuph6J819RRw/ku+rLbelwfMunktuzVVHg==",
  },
  "node_modules/json-schema-traverse": {
    version: "1.0.0",
    license: "MIT",
    integrity: "sha512-NM8/P9n3XjXhIZn1lLhkFaACTOURQXjWhV4BA/RnOv8xvgqtqpAX9IO4mRQxSx1Rlo4tqzeqb0sOlruaOy3dug==",
  },
  "node_modules/require-from-string": {
    version: "2.0.2",
    license: "MIT",
    integrity: "sha512-Xf0nWe6RseziFMu+Ap9biiUbmplq6S9/p+7w7YXP/JBHhrUDDUhwa+vANyubuqfZWTveU//DYVGsDG7RKL/vEw==",
  },
});

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function readJson(relativePath, root = moduleRoot) {
  return JSON.parse(readFileSync(path.join(root, relativePath), "utf8"));
}

function sha256(filePath) {
  return createHash("sha256").update(readFileSync(filePath)).digest("hex").toUpperCase();
}

function parseArgs(args) {
  if (args.length !== 2 || args[0] !== "--base" || args[1] !== FIXED_BASE) fail("RPG01_VERIFY_FIXED_BASE_REQUIRED");
  return args[1];
}

function run(command, args, code) {
  const result = spawnSync(command, args, {
    cwd: moduleRoot,
    encoding: "utf8",
    windowsHide: true,
    stdio: "inherit",
  });
  if (result.error || result.status !== 0) fail(code);
}

function runNpm(args, code) {
  if (typeof npmExecPath !== "string" || !path.isAbsolute(npmExecPath) || path.basename(npmExecPath) !== "npm-cli.js") {
    fail("RPG01_NPM_EXEC_PATH_INVALID");
  }
  run(process.execPath, [npmExecPath, ...args], code);
}

function checkToolchain() {
  if (process.version !== "v24.18.0") fail("RPG01_NODE_VERSION_MISMATCH");
  if (typeof npmExecPath !== "string" || !path.isAbsolute(npmExecPath) || path.basename(npmExecPath) !== "npm-cli.js") {
    fail("RPG01_NPM_EXEC_PATH_INVALID");
  }
  const npm = spawnSync(process.execPath, [npmExecPath, "--version"], { cwd: moduleRoot, encoding: "utf8", windowsHide: true });
  if (npm.error || npm.status !== 0 || npm.stdout.trim() !== "11.16.0") fail("RPG01_NPM_VERSION_MISMATCH");
}

function checkDependencyRegister() {
  const lock = readJson("package-lock.json");
  const register = readJson("docs/THIRD_PARTY.json");
  if (lock.lockfileVersion !== 3 || JSON.stringify(lock.packages[""].dependencies) !== JSON.stringify({ ajv: "8.20.0" })) {
    fail("RPG01_LOCK_DIRECT_DEPENDENCY_MISMATCH");
  }
  const actualPackageNames = Object.keys(lock.packages).filter(Boolean).sort();
  const expectedPackageNames = Object.keys(expectedPackages).sort();
  if (JSON.stringify(actualPackageNames) !== JSON.stringify(expectedPackageNames)) fail("RPG01_LOCK_PACKAGE_SET_MISMATCH");
  for (const [name, expected] of Object.entries(expectedPackages)) {
    const actual = lock.packages[name];
    if (!actual || actual.version !== expected.version || actual.license !== expected.license || actual.integrity !== expected.integrity) {
      fail("RPG01_LOCK_PACKAGE_METADATA_MISMATCH");
    }
  }
  if (JSON.stringify(register.packages) !== JSON.stringify(Object.entries(expectedPackages).map(([lockPath, value]) => ({ lockPath, ...value })))) {
    fail("RPG01_THIRD_PARTY_REGISTER_MISMATCH");
  }
}

function checkFixturesAndExports() {
  if (FORMAT_VERSION !== "0.1.0" || Object.keys(FORMATS).length !== 4 || Object.keys(SCHEMAS).length !== 4) {
    fail("RPG01_EXPORT_SET_MISMATCH");
  }
  const card = readJson("fixtures/zero-plugin.card-package.json");
  const player = readJson("fixtures/bai-yu-ling-yin.player-setup.json");
  const turn = readJson("fixtures/minimal.turn-exchange.json");
  if (!validateCardPackage(card).valid) fail("RPG01_CARD_FIXTURE_INVALID");
  if (!validatePlayerSetup(player, card).valid) fail("RPG01_PLAYER_FIXTURE_INVALID");
  if (!validateTurnExchange(turn, card).valid) fail("RPG01_TURN_FIXTURE_INVALID");
  if (!evaluatePluginReadiness(card, []).ready) fail("RPG01_ZERO_PLUGIN_READINESS_FAILED");
}

function checkMachineStatus() {
  const status = readJson("docs/RPG01_STATUS.json");
  if (status.round !== "RPG-01" || status.status !== "accepted" ||
      status.claimAllowed !== true || status.manualAcceptanceRequired !== true || status.manualAcceptanceSatisfied !== true ||
      status.manualAcceptance?.status !== "accepted" || status.manualAcceptance?.date !== "2026-09-04" ||
      status.nextRoundAllowed !== true || status.nextRoundRequiresSeparatePlan !== true ||
      status.nextRoundImplementationAuthorized !== false || status.baseSha !== FIXED_BASE ||
      status.probes.firstDevelopmentRound.completed !== 0 || status.probes.firstDevelopmentRound.remaining !== 20) {
    fail("RPG01_MACHINE_STATUS_INVALID");
  }
}

function checkResearchSnapshot() {
  const manifestPath = path.join(researchRoot, "MANIFEST.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (manifest.initialManifestSha256 !== INITIAL_RESEARCH_MANIFEST_SHA256 ||
      manifest.status !== "rpg01_accepted" || manifest.implementationStarted !== true ||
      manifest.manualAcceptance?.status !== "accepted" || manifest.manualAcceptance?.date !== "2026-09-04" ||
      manifest.moduleDelivery?.status !== "accepted" || manifest.moduleDelivery?.claimAllowed !== true ||
      manifest.moduleDelivery?.manualAcceptanceSatisfied !== true ||
      manifest.moduleDelivery?.nextRoundImplementationAuthorized !== false) {
    fail("RPG01_RESEARCH_MANIFEST_STATUS_INVALID");
  }
  for (const entry of manifest.files) {
    const filePath = path.join(researchRoot, entry.path);
    if (!statSync(filePath).isFile() || statSync(filePath).size !== entry.bytes || sha256(filePath) !== entry.sha256) {
      fail("RPG01_RESEARCH_MANIFEST_HASH_MISMATCH");
    }
  }
  const ledger = JSON.parse(readFileSync(path.join(researchRoot, "PROBE_LEDGER.json"), "utf8"));
  const budget = ledger.budgets.firstDevelopmentRound;
  if (budget.submitted !== 0 || budget.completed !== 0 || budget.remaining !== 20 || budget.implementationStarted !== true) {
    fail("RPG01_PROBE_LEDGER_INVALID");
  }
  if (ledger.developmentActivity?.status !== "accepted" ||
      ledger.developmentActivity?.manualAcceptance?.status !== "accepted" ||
      ledger.developmentActivity?.manualAcceptance?.date !== "2026-09-04") {
    fail("RPG01_PROBE_LEDGER_STATUS_INVALID");
  }
}

try {
  const base = parseArgs(process.argv.slice(2));
  checkToolchain();
  console.log("RPG01_VERIFY_TOOLCHAIN_OK");
  checkDependencyRegister();
  console.log("RPG01_VERIFY_DEPENDENCIES_OK");
  checkFixturesAndExports();
  console.log("RPG01_VERIFY_FIXTURES_OK");
  checkMachineStatus();
  checkResearchSnapshot();
  console.log("RPG01_VERIFY_DOCUMENTS_OK");
  runNpm(["run", "test:boundary"], "RPG01_VERIFY_BOUNDARY_TEST_FAILED");
  runNpm(["run", "test:contracts"], "RPG01_VERIFY_CONTRACT_TEST_FAILED");
  runNpm(["run", "check:boundary"], "RPG01_VERIFY_BOUNDARY_FAILED");
  runNpm(["run", "check:parent-scope", "--", "--base", base], "RPG01_VERIFY_PARENT_SCOPE_FAILED");
  console.log("RPG01_AUTOMATED_GATES_OK");
} catch (error) {
  console.error(error?.code ?? "RPG01_VERIFY_OPERATIONAL_ERROR");
  process.exitCode = 1;
}

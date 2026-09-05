import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { auditBoundary, FIXED_BASE } from "../scripts/check-boundary.mjs";
import { collectChangedPaths, parseBaseArgument, validateChangedPaths } from "../scripts/check-parent-scope.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(moduleRoot, "../..");

async function loadPolicy(root = moduleRoot) {
  return JSON.parse(await fs.readFile(path.join(root, "module-boundary.json"), "utf8"));
}

async function createFixture() {
  const repository = await fs.mkdtemp(path.join(os.tmpdir(), "rpg01-boundary-"));
  const module = path.join(repository, "experiments", "ai-rpg-engine");
  await fs.mkdir(path.join(module, "src"), { recursive: true });
  await fs.mkdir(path.join(repository, "docs", "ai-rpg-experiment"), { recursive: true });
  await fs.copyFile(path.join(moduleRoot, "module-boundary.json"), path.join(module, "module-boundary.json"));
  await fs.writeFile(path.join(module, "package.json"), JSON.stringify({ dependencies: { ajv: "8.20.0" } }));
  await fs.writeFile(path.join(module, "src", "index.mjs"), 'import Ajv2020 from "ajv/dist/2020.js";\nvoid Ajv2020;\n');
  return { repository, module };
}

function runGit(repository, args) {
  const result = spawnSync("git", args, { cwd: repository, encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

test("real worktree satisfies the module boundary and fixed parent scope", async () => {
  const policy = await loadPolicy();
  const report = await auditBoundary({ moduleRoot, repositoryRoot, policy });
  assert.deepEqual(report, { ok: true, diagnostics: [] });
  assert.equal(parseBaseArgument(["--base", FIXED_BASE]), FIXED_BASE);
  const changed = collectChangedPaths(repositoryRoot, FIXED_BASE);
  assert.deepEqual(validateChangedPaths(changed), []);
});

test("parent scope rejects fake prefixes, generated paths, and wrong base", () => {
  assert.throws(() => parseBaseArgument(["--base", "0".repeat(40)]), { code: "PARENT_SCOPE_FIXED_BASE_MISMATCH" });
  assert.deepEqual(validateChangedPaths([
    "docs/ai-rpg-experiment-evil/readme.md",
    "experiments/ai-rpg-engine/dist/bundle.js",
    "server/main.py",
  ]).map((item) => item.code), [
    "PARENT_SCOPE_GENERATED_PATH",
    "PARENT_SCOPE_OUTSIDE_ALLOWLIST",
    "PARENT_SCOPE_OUTSIDE_ALLOWLIST",
  ]);
});

test("parent scope accepts a descendant HEAD and includes its committed allowlisted paths", async (t) => {
  const fixture = await createFixture();
  t.after(() => fs.rm(fixture.repository, { recursive: true, force: true }));
  runGit(fixture.repository, ["init"]);
  runGit(fixture.repository, ["add", "."]);
  runGit(fixture.repository, ["-c", "user.name=RPG-01 Test", "-c", "user.email=rpg01@example.invalid", "commit", "-m", "base"]);
  const base = runGit(fixture.repository, ["rev-parse", "HEAD"]);
  const receipt = path.join(fixture.repository, "docs", "ai-rpg-experiment", "receipt.md");
  await fs.writeFile(receipt, "accepted\n");
  runGit(fixture.repository, ["add", "docs/ai-rpg-experiment/receipt.md"]);
  runGit(fixture.repository, ["-c", "user.name=RPG-01 Test", "-c", "user.email=rpg01@example.invalid", "commit", "-m", "receipt"]);
  assert.notEqual(runGit(fixture.repository, ["rev-parse", "HEAD"]), base);
  const changed = collectChangedPaths(fixture.repository, base);
  assert.deepEqual(changed, ["docs/ai-rpg-experiment/receipt.md"]);
  assert.deepEqual(validateChangedPaths(changed), []);
});

test("module audit rejects parent imports, runtime network, local dependency protocols, and secrets without leaking content", async (t) => {
  const fixture = await createFixture();
  t.after(() => fs.rm(fixture.repository, { recursive: true, force: true }));
  const policy = await loadPolicy(fixture.module);
  const parentImport = "im" + "port parent from '../../../server/main.js';\nvoid parent;\n";
  const networkCall = "fe" + "tch('https://invalid.example');\n";
  const bareNetworkImport = "im" + "port https from 'https';\nvoid https;\n";
  const dynamicLoader = "const specifier = './local.mjs';\n" + "im" + "port(specifier);\n";
  await fs.writeFile(path.join(fixture.module, "src", "parent.mjs"), parentImport);
  await fs.writeFile(path.join(fixture.module, "src", "network.mjs"), networkCall);
  await fs.writeFile(path.join(fixture.module, "src", "bare-network.mjs"), bareNetworkImport);
  await fs.writeFile(path.join(fixture.module, "src", "dynamic.mjs"), dynamicLoader);
  await fs.mkdir(path.join(fixture.module, "dist"));
  await fs.writeFile(path.join(fixture.module, "dist", "bundle.js"), "void 0;\n");
  await fs.writeFile(path.join(fixture.module, ".env"), "TOKEN=" + "sk-" + "x".repeat(32));
  await fs.writeFile(path.join(fixture.module, "package.json"), JSON.stringify({ dependencies: { ajv: "file:../ajv" } }));
  const report = await auditBoundary({ moduleRoot: fixture.module, repositoryRoot: fixture.repository, policy });
  const codes = report.diagnostics.map((item) => item.code);
  assert.equal(report.ok, false);
  for (const expected of [
    "BOUNDARY_PARENT_IMPORT",
    "BOUNDARY_NETWORK_GLOBAL",
    "BOUNDARY_NETWORK_MODULE",
    "BOUNDARY_RUNTIME_IO_MODULE",
    "BOUNDARY_NON_LITERAL_LOADER",
    "BOUNDARY_GENERATED_PATH",
    "BOUNDARY_SECRET_FILENAME",
    "BOUNDARY_SECRET_CONTENT",
    "BOUNDARY_PACKAGE_DEPENDENCIES",
    "BOUNDARY_PACKAGE_LOCAL_PROTOCOL",
  ]) assert.equal(codes.includes(expected), true, expected);
  const serialized = JSON.stringify(report);
  assert.equal(serialized.includes(fixture.repository), false);
  assert.equal(serialized.includes("sk-" + "x".repeat(32)), false);
});

test("module audit rejects an external directory junction when the host permits creating it", async (t) => {
  const fixture = await createFixture();
  t.after(() => fs.rm(fixture.repository, { recursive: true, force: true }));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), "rpg01-outside-"));
  t.after(() => fs.rm(outside, { recursive: true, force: true }));
  try {
    await fs.symlink(outside, path.join(fixture.module, "src", "outside"), process.platform === "win32" ? "junction" : "dir");
  } catch (error) {
    if (error?.code === "EPERM") return t.skip("host does not allow test symlinks");
    throw error;
  }
  const report = await auditBoundary({ moduleRoot: fixture.module, repositoryRoot: fixture.repository, policy: await loadPolicy(fixture.module) });
  assert.equal(report.diagnostics.some((item) => item.code === "BOUNDARY_EXTERNAL_SYMLINK"), true);
});

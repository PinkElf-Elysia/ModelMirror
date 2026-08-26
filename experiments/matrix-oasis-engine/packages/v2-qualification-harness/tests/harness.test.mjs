import assert from "node:assert/strict";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { createCandidateLock, qualifySourceOnly, runBoundedCommand, V2QualificationOperationalError, verifyCandidateCheckout, verifyQualificationDirectory } from "../src/index.mjs";

const TEST_ROOT = path.win32.join("C:" + "\\", "tmp");
const fixtures = [];

test.after(() => {
  for (const root of fixtures.reverse()) {
    const resolved = path.resolve(root);
    assert.equal(path.dirname(resolved), TEST_ROOT);
    assert.match(path.basename(resolved), /^matrix-oasis-r17-harness-/u);
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

function git(cwd, args) { return execFileSync("git", args, { cwd, encoding: "utf8", windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }); }
function sha256(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }

function sourceFixture() {
  const root = fs.mkdtempSync(path.join(TEST_ROOT, "matrix-oasis-r17-harness-"));
  fixtures.push(root);
  fs.writeFileSync(path.join(root, "LICENSE"), "fixture-license\n");
  fs.writeFileSync(path.join(root, "package.json"), '{"name":"fixture","scripts":{"test":"node test.mjs"}}\n');
  fs.writeFileSync(path.join(root, "test.mjs"), "export {};\n");
  git(root, ["init", "--quiet"]);
  git(root, ["config", "user.name", "R17 Test"]);
  git(root, ["config", "user.email", "r17@example.invalid"]);
  git(root, ["add", "."]);
  git(root, ["commit", "--quiet", "-m", "fixture"]);
  const commit = git(root, ["rev-parse", "HEAD"]).trim();
  const tree = git(root, ["rev-parse", "HEAD^{tree}"]).trim();
  const treeList = git(root, ["ls-tree", "-r", "--full-tree", "HEAD"]).replaceAll("\r\n", "\n");
  const candidate = {
    id: "fixture",
    lane: "memory-adapter",
    repository: ["https:", "", "github.com", "example", "fixture"].join("/"),
    tag: "v1.0.0",
    commit,
    gitTreeSha1: tree,
    treeListSha256: sha256(Buffer.from(treeList.endsWith("\n") ? treeList : `${treeList}\n`)),
    license: "MIT",
    upstreamLicense: { path: "LICENSE", byteLength: 16, sha256: sha256(Buffer.from("fixture-license\n")) },
    sourceArchive: { sha256: "a".repeat(64) },
  };
  return { root, candidate, lock: createCandidateLock(candidate) };
}

function expectCode(fn, code) { assert.throws(fn, (error) => error instanceof V2QualificationOperationalError && error.code === code); }

test("fixed clean source identity is verified without running candidate code", () => {
  const { root, lock } = sourceFixture();
  const identity = verifyCandidateCheckout({ candidateLock: lock, sourceDir: root });
  assert.equal(identity.value.inspection.clean, true);
  assert.equal(identity.value.inspection.lifecycleScriptsExecuted, false);
  assert.equal(identity.value.inspection.unknownNativeBinaries, 0);
});

test("dirty source and unknown native binary fail closed", () => {
  const dirty = sourceFixture();
  fs.appendFileSync(path.join(dirty.root, "test.mjs"), "// drift\n");
  expectCode(() => verifyCandidateCheckout({ candidateLock: dirty.lock, sourceDir: dirty.root }), "R17_SOURCE_DIRTY");
  const binary = sourceFixture();
  fs.writeFileSync(path.join(binary.root, "payload.dll"), "not-a-binary");
  git(binary.root, ["add", "."]);
  git(binary.root, ["commit", "--quiet", "-m", "binary"]);
  const commit = git(binary.root, ["rev-parse", "HEAD"]).trim();
  const tree = git(binary.root, ["rev-parse", "HEAD^{tree}"]).trim();
  const treeList = git(binary.root, ["ls-tree", "-r", "--full-tree", "HEAD"]).replaceAll("\r\n", "\n");
  const lock = JSON.parse(JSON.stringify(binary.lock));
  lock.candidate.commit = commit;
  lock.candidate.gitTreeSha1 = tree;
  lock.candidate.treeListSha256 = sha256(Buffer.from(treeList.endsWith("\n") ? treeList : `${treeList}\n`));
  expectCode(() => verifyCandidateCheckout({ candidateLock: lock, sourceDir: binary.root }), "R17_SOURCE_BINARY_FORBIDDEN");
});

test("source with an undisclosed lifecycle script is rejected", () => {
  const fixture = sourceFixture();
  fs.writeFileSync(path.join(fixture.root, "package.json"), '{"name":"fixture","scripts":{"postinstall":"node lifecycle.mjs"}}\n');
  git(fixture.root, ["add", "."]);
  git(fixture.root, ["commit", "--quiet", "-m", "script"]);
  const commit = git(fixture.root, ["rev-parse", "HEAD"]).trim();
  const tree = git(fixture.root, ["rev-parse", "HEAD^{tree}"]).trim();
  const treeList = git(fixture.root, ["ls-tree", "-r", "--full-tree", "HEAD"]).replaceAll("\r\n", "\n");
  const lock = JSON.parse(JSON.stringify(fixture.lock));
  lock.candidate.commit = commit;
  lock.candidate.gitTreeSha1 = tree;
  lock.candidate.treeListSha256 = sha256(Buffer.from(treeList.endsWith("\n") ? treeList : `${treeList}\n`));
  expectCode(() => verifyCandidateCheckout({ candidateLock: lock, sourceDir: fixture.root }), "R17_SOURCE_LIFECYCLE_SCRIPT_FORBIDDEN");
});

test("source-only qualification publishes an atomic deferred report that revalidates", () => {
  const fixture = sourceFixture();
  const output = path.join(TEST_ROOT, `matrix-oasis-r17-harness-${crypto.randomBytes(8).toString("hex")}`);
  fixtures.push(output);
  const result = qualifySourceOnly({ candidate: fixture.candidate, sourceDir: fixture.root, outputDir: output });
  assert.equal(result.evaluation.conclusion, "deferred");
  assert.equal(result.publication.files.length, 3);
  assert.equal(verifyQualificationDirectory(output).candidateId, "fixture");
  expectCode(() => qualifySourceOnly({ candidate: fixture.candidate, sourceDir: fixture.root, outputDir: output }), "R17_OUTPUT_EXISTS");
});

test("bounded process enforces output and time budgets with a sanitized environment", async () => {
  const sandbox = fs.mkdtempSync(path.join(TEST_ROOT, "matrix-oasis-r17-harness-"));
  fixtures.push(sandbox);
  const ok = await runBoundedCommand({ executable: process.execPath, args: ["-e", "process.stdout.write(process.env.MATRIX_TEST || 'missing')"], cwd: sandbox, sandboxDir: sandbox, timeoutMs: 5000, outputMaxBytes: 1024, environment: { MATRIX_TEST: "ok" } });
  assert.equal(ok.exitCode, 0);
  assert.equal(ok.output, "ok");
  assert.equal(ok.securityObservations.filesystemIsolation, "not-proven");
  assert.equal(ok.securityObservations.networkIsolation, "not-proven");
  await assert.rejects(runBoundedCommand({ executable: process.execPath, args: ["-e", "process.stdout.write('x'.repeat(4096))"], cwd: sandbox, sandboxDir: sandbox, timeoutMs: 5000, outputMaxBytes: 128 }), (error) => error.code === "R17_PROCESS_OUTPUT_EXCEEDED");
  await assert.rejects(runBoundedCommand({ executable: process.execPath, args: ["-e", "setInterval(()=>{},1000)"], cwd: sandbox, sandboxDir: sandbox, timeoutMs: 50, outputMaxBytes: 128 }), (error) => error.code === "R17_PROCESS_TIMEOUT");
});

test("timeout terminates a descendant process tree", async () => {
  const sandbox = fs.mkdtempSync(path.join(TEST_ROOT, "matrix-oasis-r17-harness-"));
  fixtures.push(sandbox);
  const childPath = path.join(sandbox, "child.mjs");
  const parentPath = path.join(sandbox, "parent.mjs");
  const pidPath = path.join(sandbox, "child.pid");
  fs.writeFileSync(childPath, "setInterval(() => {}, 1000);\n");
  fs.writeFileSync(parentPath, `import fs from "node:fs"; import { spawn } from "node:child_process"; const child = spawn(process.execPath, [${JSON.stringify(childPath)}], { detached: true, stdio: "ignore" }); fs.writeFileSync(${JSON.stringify(pidPath)}, String(child.pid)); child.unref(); setInterval(() => {}, 1000);\n`);
  await assert.rejects(runBoundedCommand({ executable: process.execPath, args: [parentPath], cwd: sandbox, sandboxDir: sandbox, timeoutMs: 500, outputMaxBytes: 1024 }), (error) => error.code === "R17_PROCESS_TIMEOUT" && error.processTreeTerminated === true);
  const pid = Number(fs.readFileSync(pidPath, "utf8"));
  await new Promise((resolve) => setTimeout(resolve, 200));
  let alive = true;
  try { process.kill(pid, 0); } catch { alive = false; }
  assert.equal(alive, false);
});

test("bounded process does not claim filesystem isolation it cannot enforce", async () => {
  const sandbox = fs.mkdtempSync(path.join(TEST_ROOT, "matrix-oasis-r17-harness-"));
  fixtures.push(sandbox);
  const outside = path.join(TEST_ROOT, `matrix-oasis-r17-harness-${crypto.randomBytes(8).toString("hex")}`);
  fixtures.push(outside);
  const result = await runBoundedCommand({ executable: process.execPath, args: ["-e", `require("node:fs").writeFileSync(${JSON.stringify(outside)}, "outside")`], cwd: sandbox, sandboxDir: sandbox, timeoutMs: 5000, outputMaxBytes: 1024 });
  assert.equal(result.exitCode, 0);
  assert.equal(fs.readFileSync(outside, "utf8"), "outside");
  assert.equal(result.securityObservations.filesystemIsolation, "not-proven");
});

test("forged report cannot reference missing raw evidence", () => {
  const fixture = sourceFixture();
  const output = path.join(TEST_ROOT, `matrix-oasis-r17-harness-${crypto.randomBytes(8).toString("hex")}`);
  fixtures.push(output);
  qualifySourceOnly({ candidate: fixture.candidate, sourceDir: fixture.root, outputDir: output });
  const reportPath = path.join(output, "qualification-report.json");
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  report.evidence.files.push({ name: "nonexistent-proof.log", byteLength: 1, sha256: "f".repeat(64) });
  fs.writeFileSync(reportPath, canonicalizeJsonValue(report));
  expectCode(() => verifyQualificationDirectory(output), "R17_EVIDENCE_FILE_SET_INVALID");
});

test("linked qualification reports are rejected before their bytes are trusted", () => {
  const fixture = sourceFixture();
  const output = path.join(TEST_ROOT, `matrix-oasis-r17-harness-${crypto.randomBytes(8).toString("hex")}`);
  const linked = path.join(TEST_ROOT, `matrix-oasis-r17-harness-${crypto.randomBytes(8).toString("hex")}`);
  fixtures.push(output, linked);
  qualifySourceOnly({ candidate: fixture.candidate, sourceDir: fixture.root, outputDir: output });
  const reportPath = path.join(output, "qualification-report.json");
  fs.copyFileSync(reportPath, linked);
  fs.unlinkSync(reportPath);
  fs.linkSync(linked, reportPath);
  expectCode(() => verifyQualificationDirectory(output), "R17_EVIDENCE_FILE_INVALID");
});

test("paths outside C tmp cannot be treated as candidate evidence", () => {
  const fixture = sourceFixture();
  const outside = path.win32.join("C:" + "\\", "Windows");
  expectCode(() => verifyCandidateCheckout({ candidateLock: fixture.lock, sourceDir: outside }), "R17_PATH_OUTSIDE_TMP");
});

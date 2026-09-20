import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  runR22OfflineQualification,
  validateR22OfflineCaseSpecJson,
} from "../scripts/lib/r22-qualification-core.mjs";
import { sha256 } from "../scripts/lib/r22-cli-core.mjs";

const canonical = (value) => canonicalizeJsonValue(value);
async function root(t, label) { const value = await mkdtemp(path.join(tmpdir(), `r22-${label}-`)); t.after(() => rm(value, { recursive: true, force: true })); return value; }

function fakeQualifier(counter = { calls: 0 }) {
  return async () => {
    counter.calls += 1;
    throw new Error("unverified qualification callback must not run");
  };
}

async function setup(t, label = "qualification") {
  const temporaryRoot = await root(t, label); const cases = [];
  for (const caseId of ["case-alpha", "case-beta", "case-gamma"]) {
    const npcRunRoot = path.join(temporaryRoot, `${caseId}-npc`); const derivedStateRoot = path.join(temporaryRoot, `${caseId}-derived`);
    await mkdir(npcRunRoot); await mkdir(derivedStateRoot);
    const currentJson = canonical({ format: "fixture-current", caseId }); const bundleJson = canonical({ format: "fixture-derived-bundle", caseId });
    await writeFile(path.join(npcRunRoot, "npc-current.json"), currentJson, { flag: "wx" });
    await writeFile(path.join(derivedStateRoot, "npc-derived-state-bundle.json"), bundleJson, { flag: "wx" });
    cases.push({ caseId, sourceKind: caseId === "case-gamma" ? "synthetic-fixture" : "qualified-cache", npcRunRoot, derivedStateRoot, expectedNpcCurrentSha256: sha256(currentJson), expectedDerivedStateBundleSha256: sha256(bundleJson) });
  }
  const specJson = canonical({ format: "matrix-oasis.r22-offline-case-spec", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", primaryCaseId: "case-beta", cases });
  const caseSpec = path.join(temporaryRoot, "case-spec.json"); const output = path.join(temporaryRoot, "qualified-output");
  await writeFile(caseSpec, specJson, { flag: "wx" });
  return { temporaryRoot, caseSpec, output, cases, args: ["--case-spec", caseSpec, "--output", output] };
}

test("offline case spec is closed, ordered and binds exactly three fixed sources", async (t) => {
  const fixture = await setup(t, "spec"); assert.equal(validateR22OfflineCaseSpecJson(await readFile(fixture.caseSpec, "utf8")).primaryCaseId, "case-beta");
  const value = JSON.parse(await readFile(fixture.caseSpec, "utf8")); value.secret = "not-allowed";
  assert.throws(() => validateR22OfflineCaseSpecJson(canonical(value)), /R22_CASE_SPEC_INVALID/u);
  value.secret = undefined; delete value.secret; value.cases.reverse();
  assert.throws(() => validateR22OfflineCaseSpecJson(canonical(value)), /R22_CASE_SPEC_INVALID/u);
});

test("placeholder source shells cannot reach an injected qualifier or publish evidence", async (t) => {
  const fixture = await setup(t); const counter = { calls: 0 };
  await assert.rejects(runR22OfflineQualification(fixture.args, {
    temporaryRoot: fixture.temporaryRoot,
    qualifyCase: fakeQualifier(counter),
  }), /R22_R20_CURRENT_INVALID/u);
  assert.equal(counter.calls, 0);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
});

test("repeated shallow source attempts fail at the same qualification boundary", async (t) => {
  const fixture = await setup(t, "determinism"); const second = path.join(fixture.temporaryRoot, "qualified-output-two");
  await assert.rejects(runR22OfflineQualification(fixture.args, { temporaryRoot: fixture.temporaryRoot, qualifyCase: fakeQualifier() }), /R22_R20_CURRENT_INVALID/u);
  await assert.rejects(runR22OfflineQualification(["--case-spec", fixture.caseSpec, "--output", second], { temporaryRoot: fixture.temporaryRoot, qualifyCase: fakeQualifier() }), /R22_R20_CURRENT_INVALID/u);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  await assert.rejects(stat(second), /ENOENT/u);
});

test("source identity drift and missing fixed caches stop before publication", async (t) => {
  const fixture = await setup(t, "drift");
  await writeFile(path.join(fixture.cases[0].npcRunRoot, "npc-current.json"), canonical({ changed: true }));
  await assert.rejects(runR22OfflineQualification(fixture.args, { temporaryRoot: fixture.temporaryRoot, qualifyCase: fakeQualifier() }), /R22_SOURCE_IDENTITY_MISMATCH/u);
  await assert.rejects(stat(fixture.output), /ENOENT/u);

  const missing = await setup(t, "missing"); await rm(path.join(missing.cases[0].derivedStateRoot, "npc-derived-state-bundle.json"));
  await assert.rejects(runR22OfflineQualification(missing.args, { temporaryRoot: missing.temporaryRoot, qualifyCase: fakeQualifier() }), /R22_FILE_IDENTITY_INVALID/u);
  await assert.rejects(stat(missing.output), /ENOENT/u);
});

test("an injected qualifier cannot transplant policy across unverified sources", async (t) => {
  const fixture = await setup(t, "transplant"); const counter = { calls: 0 };
  await assert.rejects(runR22OfflineQualification(fixture.args, { temporaryRoot: fixture.temporaryRoot, qualifyCase: fakeQualifier(counter) }), /R22_R20_CURRENT_INVALID/u);
  assert.equal(counter.calls, 0);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
});

test("existing output is never overwritten", async (t) => {
  const fixture = await setup(t, "existing"); await mkdir(fixture.output); await writeFile(path.join(fixture.output, "sentinel.txt"), "owned");
  await assert.rejects(runR22OfflineQualification(fixture.args, { temporaryRoot: fixture.temporaryRoot, qualifyCase: fakeQualifier() }), /R22_OUTPUT_EXISTS/u);
  assert.equal(await readFile(path.join(fixture.output, "sentinel.txt"), "utf8"), "owned");
});

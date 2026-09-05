import assert from "node:assert/strict";
import { access, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  qualifyR22OfflineCase,
  runR22OfflineQualification,
  verifyR22OfflineQualification,
  verifyR22QualifiedSourcePair,
} from "../scripts/lib/r22-qualification-core.mjs";
import { observeR22SourceDirectory, sha256 } from "../scripts/lib/r22-cli-core.mjs";

const TEMPORARY_ROOT = path.join(path.parse(fileURLToPath(import.meta.url)).root, "tmp");

const FIXED_CASES = Object.freeze([
  Object.freeze({
    caseId: "neutral-cache",
    sourceKind: "qualified-cache",
    npcRunRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r20-neutral-r10-npc"),
    derivedStateRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r21-real-20260901-b-case-1"),
    expectedNpcCurrentSha256: "sha256:187221f904f7eee7fc97c0194fe16f075365b1b489050ac00a04be0c4da91135",
    expectedDerivedStateBundleSha256: "sha256:7cfc9b43e6318076b57ee97e34a759a9b9a52c31377a9beaaf5d8d829d1a008b",
  }),
  Object.freeze({
    caseId: "last-train-cache",
    sourceKind: "qualified-cache",
    npcRunRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r20-last-train-r10-npc"),
    derivedStateRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r21-real-20260901-b-case-2"),
    expectedNpcCurrentSha256: "sha256:b9a1a488ff84de42c12ee99ed7bf16ddfcd4fbdcd94a26f2cd3c9bbc9af99232",
    expectedDerivedStateBundleSha256: "sha256:4feb459d9e64b66a45403bd66aaae7d6056d3ca859696e4afb0f867085a295e7",
  }),
  Object.freeze({
    caseId: "synthetic-dual-actor",
    sourceKind: "synthetic-fixture",
    npcRunRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r22-synthetic-dual-v2-npc"),
    derivedStateRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r22-synthetic-dual-r21-v1"),
    expectedNpcCurrentSha256: "sha256:81f06d8ac3903f8a55bfaf676a5c4e6e8283b505a77ac0642c9779d668862f72",
    expectedDerivedStateBundleSha256: "sha256:4b5e9292a13872eab6956df85b144e639eea7c60280fc2d61af66d69efdbc0e1",
  }),
]);

async function fixedCachesAvailable() {
  try {
    for (const item of FIXED_CASES) {
      await access(path.join(item.npcRunRoot, "npc-current.json"));
      await access(path.join(item.derivedStateRoot, "npc-derived-state-bundle.json"));
    }
    return true;
  } catch {
    return false;
  }
}

test("a named fixed cache is evidence only after its expected content hash matches", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "r22-cache-")); t.after(() => rm(root, { recursive: true, force: true }));
  await assert.rejects(observeR22SourceDirectory(path.join(root, "absent-cache"), "npc-current.json", `sha256:${"0".repeat(64)}`, root), /R22_DIRECTORY_IDENTITY_INVALID/u);
});

test("the two real caches and qualified synthetic dual-actor pair execute the offline controller and authority chain", async (t) => {
  if (process.platform !== "win32" || !await fixedCachesAvailable()) {
    t.skip("fixed Windows qualification caches are unavailable");
    return;
  }
  for (const item of FIXED_CASES) {
    const source = await verifyR22QualifiedSourcePair(item, TEMPORARY_ROOT);
    const result = await qualifyR22OfflineCase({ caseId: item.caseId, source, temporaryRoot: TEMPORARY_ROOT });
    const repeated = await qualifyR22OfflineCase({ caseId: item.caseId, source, temporaryRoot: TEMPORARY_ROOT });
    assert.deepEqual(repeated, result, `${item.caseId} must be byte-deterministic`);
    const trace = JSON.parse(result.traceJson);
    const projection = JSON.parse(result.projectionQualificationReportJson);
    const godot = JSON.parse(result.godotEvidenceJson);
    assert.deepEqual(trace.totals, {
      turns: 2,
      providerRequests: 1,
      actualMicrousd: 100,
      fallbackTurns: 1,
      actionChoiceTurns: 1,
      adjudicatedTurns: 1,
    });
    assert.equal(projection.baseQualifiedR21BundleSha256, item.expectedDerivedStateBundleSha256);
    assert.equal(projection.r21Qualified, false);
    assert.match(projection.ephemeralProjectionSha256, /^sha256:[0-9a-f]{64}$/u);
    assert.equal(godot.r20ControllerRoutesExecuted, true);
    assert.equal(godot.displayedAckPersisted, true);
    assert.equal(godot.physicalMovementVerified, false);
    assert.equal(JSON.parse(result.finalWorldEventLedgerJson).revision, 1);
  }
});

test("source-bound verifier reopens all fixed pairs and rejects a structurally re-signed source identity", async (t) => {
  if (process.platform !== "win32" || !await fixedCachesAvailable()) {
    t.skip("fixed Windows qualification caches are unavailable");
    return;
  }
  const suffix = randomUUID();
  const caseSpec = path.join(TEMPORARY_ROOT, `r22-source-bound-${suffix}.json`);
  const output = path.join(TEMPORARY_ROOT, `r22-source-bound-${suffix}`);
  t.after(() => Promise.all([
    rm(caseSpec, { force: true }),
    rm(output, { recursive: true, force: true }),
  ]));
  const ordered = [...FIXED_CASES].sort((left, right) => left.caseId.localeCompare(right.caseId));
  await writeFile(caseSpec, canonicalizeJsonValue({
    format: "matrix-oasis.r22-offline-case-spec",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    primaryCaseId: "last-train-cache",
    cases: ordered,
  }), { flag: "wx" });
  const qualified = await runR22OfflineQualification([
    "--case-spec", caseSpec,
    "--output", output,
  ], { temporaryRoot: TEMPORARY_ROOT });
  assert.equal(qualified.readyForPreview, false);
  const verified = await verifyR22OfflineQualification(output, TEMPORARY_ROOT, { caseSpecPath: caseSpec });
  assert.equal(verified.sourceBound, true);
  assert.match(verified.caseSpecSha256, /^sha256:[0-9a-f]{64}$/u);
  await assert.rejects(
    verifyR22OfflineQualification(output, TEMPORARY_ROOT),
    /R22_QUALIFICATION_SOURCE_BINDING_REQUIRED/u,
  );
  const evidence = JSON.parse(await readFile(path.join(output, "offline-case-evidence.json"), "utf8"));
  assert.equal(evidence.cases.length, 3);
  assert.equal(evidence.cases.every((item) => item.provider.realRequests === 0), true);
  const attacked = evidence.cases.find((item) => item.caseId === "neutral-cache");
  const forgedCurrentSha256 = `sha256:${"f".repeat(64)}`;
  const authoritySession = JSON.parse(attacked.ephemeralAuthoritySessionManifestJson);
  authoritySession.source.baseQualifiedR20CurrentSha256 = forgedCurrentSha256;
  attacked.ephemeralAuthoritySessionManifestJson = canonicalizeJsonValue(authoritySession);
  attacked.source.npcCurrentSha256 = forgedCurrentSha256;
  const authoritySessionSha256 = sha256(attacked.ephemeralAuthoritySessionManifestJson);
  const projection = JSON.parse(attacked.projectionQualificationReportJson);
  projection.ephemeralAuthoritySessionManifestSha256 = authoritySessionSha256;
  attacked.projectionQualificationReportJson = canonicalizeJsonValue(projection);
  const godot = JSON.parse(attacked.godotEvidenceJson);
  godot.ephemeralAuthoritySessionManifestSha256 = authoritySessionSha256;
  attacked.godotEvidenceJson = canonicalizeJsonValue(godot);
  const reportFile = path.join(output, "npc-cognition-qualification-report.json");
  const report = JSON.parse(await readFile(reportFile, "utf8"));
  report.offlineCases.find((item) => item.caseId === attacked.caseId).evidenceSha256 =
    sha256(canonicalizeJsonValue(attacked));
  await writeFile(path.join(output, "offline-case-evidence.json"), canonicalizeJsonValue(evidence));
  await writeFile(reportFile, canonicalizeJsonValue(report));
  await assert.rejects(
    verifyR22OfflineQualification(output, TEMPORARY_ROOT, { caseSpecPath: caseSpec }),
    /R22_OFFLINE_CASE_SOURCE_BINDING_MISMATCH/u,
  );
});

test("source-bound verifier rejects a whole-package re-signed forged controller command", async (t) => {
  if (process.platform !== "win32" || !await fixedCachesAvailable()) {
    t.skip("fixed Windows qualification caches are unavailable");
    return;
  }
  const suffix = randomUUID();
  const caseSpec = path.join(TEMPORARY_ROOT, `r22-whole-package-${suffix}.json`);
  const output = path.join(TEMPORARY_ROOT, `r22-whole-package-${suffix}`);
  t.after(() => Promise.all([
    rm(caseSpec, { force: true }),
    rm(output, { recursive: true, force: true }),
  ]));
  const ordered = [...FIXED_CASES].sort((left, right) => left.caseId.localeCompare(right.caseId));
  await writeFile(caseSpec, canonicalizeJsonValue({
    format: "matrix-oasis.r22-offline-case-spec",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    primaryCaseId: "last-train-cache",
    cases: ordered,
  }), { flag: "wx" });
  await runR22OfflineQualification(["--case-spec", caseSpec, "--output", output], { temporaryRoot: TEMPORARY_ROOT });
  const evidencePath = path.join(output, "offline-case-evidence.json");
  const reportPath = path.join(output, "npc-cognition-qualification-report.json");
  const godotPath = path.join(output, "godot-evidence.json");
  const evidenceSet = JSON.parse(await readFile(evidencePath, "utf8"));
  const report = JSON.parse(await readFile(reportPath, "utf8"));
  const attacked = evidenceSet.cases.find((item) => item.caseId === evidenceSet.primaryCaseId);
  const godot = JSON.parse(attacked.godotEvidenceJson);
  godot.executedCommand.ruleIndex += 1;
  godot.commandSha256 = sha256(canonicalizeJsonValue(godot.executedCommand));
  attacked.godotEvidenceJson = canonicalizeJsonValue(godot);
  report.offlineCases.find((item) => item.caseId === attacked.caseId).evidenceSha256 =
    sha256(canonicalizeJsonValue(attacked));
  report.godotEvidenceSha256 = sha256(attacked.godotEvidenceJson);
  await writeFile(evidencePath, canonicalizeJsonValue(evidenceSet));
  await writeFile(godotPath, attacked.godotEvidenceJson);
  await writeFile(reportPath, canonicalizeJsonValue(report));
  await assert.rejects(
    verifyR22OfflineQualification(output, TEMPORARY_ROOT, { caseSpecPath: caseSpec }),
    /R22_PERSISTED_COMMAND_REBUILD_MISMATCH/u,
  );
});

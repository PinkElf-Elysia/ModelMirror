import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { access } from "node:fs/promises";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  buildR22OfflineQualificationArtifacts,
  qualifyR22OfflineCase,
  verifyR22QualifiedSourcePair,
} from "../scripts/lib/r22-qualification-core.mjs";

const TEMPORARY_ROOT = path.join(path.parse(fileURLToPath(import.meta.url)).root, "tmp");

const NEUTRAL_SOURCE = Object.freeze({
  caseId: "neutral-cache",
  sourceKind: "qualified-cache",
  npcRunRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r20-neutral-r10-npc"),
  derivedStateRoot: path.join(TEMPORARY_ROOT, "matrix-oasis-r21-real-20260901-b-case-1"),
  expectedNpcCurrentSha256: "sha256:187221f904f7eee7fc97c0194fe16f075365b1b489050ac00a04be0c4da91135",
  expectedDerivedStateBundleSha256: "sha256:7cfc9b43e6318076b57ee97e34a759a9b9a52c31377a9beaaf5d8d829d1a008b",
});

test("qualification builder rejects unverified objects instead of trusting claimed zero-network evidence", () => {
  const spec = { cases: [{ caseId: "case-alpha" }, { caseId: "case-beta" }, { caseId: "case-gamma" }], primaryCaseId: "case-alpha" };
  const forged = spec.cases.map(({ caseId }) => ({ source: {}, result: { caseId, policyJson: "{}", traceJson: "{}", worldEventLedgerReplayReportJson: "{}", projectionQualificationReportJson: "{}", godotEvidenceJson: "{}", performanceEvidenceJson: "{}", providerCallsFake: 1, networkRequests: 0 } }));
  assert.throws(() => buildR22OfflineQualificationArtifacts({ spec, cases: forged }), /R22_OFFLINE_CASE_SET_INVALID/u);
});

test("qualification API has no provider credential or network callback surface", async () => {
  const module = await import("../scripts/lib/r22-qualification-core.mjs");
  for (const name of Object.keys(module)) assert.equal(/fetch|credential|apiKey|network/iu.test(name), false, name);
});

test("source-bound case validation rejects forged R21 qualification, Godot, performance and receipt evidence", async (t) => {
  if (process.platform !== "win32") {
    t.skip("fixed Windows qualification cache is unavailable");
    return;
  }
  try {
    await access(`${NEUTRAL_SOURCE.npcRunRoot}\\npc-current.json`);
    await access(`${NEUTRAL_SOURCE.derivedStateRoot}\\npc-derived-state-bundle.json`);
  } catch {
    t.skip("fixed Windows qualification cache is unavailable");
    return;
  }
  const source = await verifyR22QualifiedSourcePair(NEUTRAL_SOURCE, TEMPORARY_ROOT);
  const result = await qualifyR22OfflineCase({ caseId: "attack-alpha", source, temporaryRoot: TEMPORARY_ROOT });
  const spec = Object.freeze({
    primaryCaseId: "attack-alpha",
    cases: Object.freeze([
      Object.freeze({ caseId: "attack-alpha" }),
      Object.freeze({ caseId: "attack-beta" }),
      Object.freeze({ caseId: "attack-gamma" }),
    ]),
  });
  const casesWith = (attacked) => spec.cases.map(({ caseId }, index) => ({
    source,
    result: Object.freeze({ ...(index === 0 ? attacked : result), caseId }),
  }));

  const forgedProjection = JSON.parse(result.projectionQualificationReportJson);
  forgedProjection.r21Qualified = true;
  assert.throws(() => buildR22OfflineQualificationArtifacts({
    spec,
    cases: casesWith({ ...result, projectionQualificationReportJson: canonicalizeJsonValue(forgedProjection) }),
  }), /R22_(?:EPHEMERAL_DERIVED_CONTEXT|OFFLINE_CASE)/u);

  const forgedGodot = JSON.parse(result.godotEvidenceJson);
  forgedGodot.r22GodotExecuted = true;
  forgedGodot.r22GodotQualified = true;
  forgedGodot.physicalMovementVerified = true;
  assert.throws(() => buildR22OfflineQualificationArtifacts({
    spec,
    cases: casesWith({ ...result, godotEvidenceJson: canonicalizeJsonValue(forgedGodot) }),
  }), /R22_OFFLINE_GODOT_BOUNDARY_INVALID/u);

  const forgedPerformance = JSON.parse(result.performanceEvidenceJson);
  forgedPerformance.r22PerformanceMeasured = true;
  forgedPerformance.r22PerformanceQualified = true;
  assert.throws(() => buildR22OfflineQualificationArtifacts({
    spec,
    cases: casesWith({ ...result, performanceEvidenceJson: canonicalizeJsonValue(forgedPerformance) }),
  }), /R22_OFFLINE_PERFORMANCE_BOUNDARY_INVALID/u);

  const forgedTurnRecords = result.turnRecords.map((record, index) => {
    if (index === 0) return record;
    const receipt = JSON.parse(record.turnReceiptJson);
    receipt.budget.actualMicrousd += 1;
    return Object.freeze({ ...record, turnReceiptJson: canonicalizeJsonValue(receipt) });
  });
  assert.throws(() => buildR22OfflineQualificationArtifacts({
    spec,
    cases: casesWith({ ...result, turnRecords: forgedTurnRecords }),
  }), /R22_OFFLINE_CASE_RESULT_INVALID/u);
});

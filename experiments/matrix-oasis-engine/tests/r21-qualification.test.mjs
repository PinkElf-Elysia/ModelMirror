import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, open, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  R21_PROJECT_FILES,
  R21_QUALIFICATION_FILES,
  R21_QUALIFICATION_MARKERS,
  runR21Qualification,
  runR21Verify,
} from "../scripts/lib/r21-cli-core.mjs";

const canonical = (value) => canonicalizeJsonValue(value);
const sha = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const fake = (character) => `sha256:${character.repeat(64)}`;

async function qualificationFixture(t, label) {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), `r21-qualification-${label}-`));
  t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const npcRunRoot = path.join(temporaryRoot, `${label}-npc`);
  const timelineId = `timeline-${label}`;
  const authorityManifestJson = canonical({ timelineId });
  const manifestId = sha(authorityManifestJson).slice(7);
  const headSha256 = fake("b");
  const qualificationReceiptSha256 = fake("c");
  const timelineRoot = path.join(npcRunRoot, "timelines", manifestId);
  await mkdir(timelineRoot, { recursive: true });
  const runtimePack = path.join(temporaryRoot, `${label}-pack.json`);
  const runtimeReceipt = path.join(temporaryRoot, `${label}-receipt.json`);
  const authorityPolicy = path.join(temporaryRoot, `${label}-authority.json`);
  const personaSeed = path.join(temporaryRoot, `${label}-persona.json`);
  const relationshipPolicy = path.join(temporaryRoot, `${label}-relationship.json`);
  const output = path.join(temporaryRoot, `${label}-qualified`);
  const runtimePackJson = canonical({ id: "pack" });
  const runtimeReceiptJson = canonical({ id: "receipt" });
  const authorityPolicyJson = canonical({ id: "authority" });
  const entityBindingJson = canonical({ bindings: [{ actorEntityId: "actor-one" }] });
  const authority = { runtimePackSha256: sha(runtimePackJson), runtimeReceiptSha256: sha(runtimeReceiptJson), authorityPolicySha256: sha(authorityPolicyJson), npcEntityBindingSha256: sha(entityBindingJson) };
  const personaSeedJson = canonical({
    format: "matrix-oasis.npc-persona-seed", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    id: "persona-one", contentVersion: "1.0.0", authority, traitIds: ["calm"],
    actors: [{ actorEntityId: "actor-one", traits: [{ traitId: "calm", value: 0 }] }],
  });
  const relationshipPolicyJson = canonical({
    format: "matrix-oasis.npc-relationship-projection-policy", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    id: "relationship-one", contentVersion: "1.0.0", authority, personaSeedSha256: sha(personaSeedJson),
    repeatMode: "first-accepted-per-rule-actor-target-timeline", rules: [],
  });
  const ledger = { timeline: { id: timelineId }, revision: 1, headSha256, entries: [{ decision: { status: "accepted" } }] };
  const ledgerJson = canonical(ledger);
  const current = { format: "matrix-oasis.npc-current", formatVersion: "0.1.0", manifestSha256: `sha256:${manifestId}`, timelineId, revision: 1, headSha256, qualificationReceiptSha256 };
  const paths = new Map([
    [path.join(npcRunRoot, "npc-current.json"), canonical(current)],
    [path.join(timelineRoot, "authority-manifest.json"), authorityManifestJson],
    [path.join(timelineRoot, "entity-bindings.json"), entityBindingJson],
    [path.join(timelineRoot, "world-event-ledger.json"), ledgerJson],
    [path.join(timelineRoot, "qualification-evidence.json"), canonical({ fixture: true })],
    [runtimePack, runtimePackJson], [runtimeReceipt, runtimeReceiptJson], [authorityPolicy, authorityPolicyJson],
    [personaSeed, personaSeedJson], [relationshipPolicy, relationshipPolicyJson],
  ]);
  for (const [file, text] of paths) await writeFile(file, text, { flag: "wx" });
  const evidence = { formatVersion: "0.2.0", legacy: false, runtimeGamePackJson: runtimePackJson, runtimeReceiptJson, authorityPolicyJson, runtimeGamePackSha256: sha(runtimePackJson), runtimeReceiptSha256: sha(runtimeReceiptJson), authorityPolicySha256: sha(authorityPolicyJson), qualificationReceiptSha256 };
  const audit = { ok: true, current, pendingCurrent: null, timelines: [{ manifestId, timelineId, revision: 1, headSha256, qualificationReceiptSha256, qualified: true, status: "qualified" }] };
  let lease = false;
  const operations = {
    async acquireWriterLease() { assert.equal(lease, false); lease = true; return Object.freeze({ label }); },
    async auditTimelineStore() { assert.equal(lease, true); return structuredClone(audit); },
    async releaseWriterLease() { assert.equal(lease, true); lease = false; },
    validateQualificationEvidence() { return Object.freeze({ ...evidence }); },
  };
  const args = ["--npc-run-root", npcRunRoot, "--runtime-pack", runtimePack, "--runtime-receipt", runtimeReceipt, "--authority-policy", authorityPolicy, "--persona-seed", personaSeed, "--relationship-policy", relationshipPolicy, "--output", output];
  const verifyArgs = ["--npc-run-root", npcRunRoot, "--runtime-pack", runtimePack, "--runtime-receipt", runtimeReceipt, "--authority-policy", authorityPolicy, "--projection-dir", output];
  return { temporaryRoot, output, args, verifyArgs, operations, ledgerJson, authority, personaSeed, isLeaseActive: () => lease };
}

function runtimeFixture({ nondeterministicAt = null } = {}) {
  let projections = 0;
  const reducer = (id, character) => ({ id, version: "1.0.0", sourceSha256: fake(character) });
  return {
    projectionCount: () => projections,
    async prepareNpcDerivedState(input) { return { ok: true, prepared: Object.freeze({ input }) }; },
    async projectNpcDerivedState({ prepared, worldEventLedgerJson }) {
      projections += 1;
      const ledger = JSON.parse(worldEventLedgerJson);
      const ledgerIdentity = { timelineId: ledger.timeline.id, canonicalSha256: sha(worldEventLedgerJson), throughRevision: ledger.revision, throughHeadSha256: ledger.headSha256 };
      const changed = projections === nondeterministicAt;
      const memory = canonical({
        format: "matrix-oasis.npc-memory-projection", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
        authority: prepared.input.personaSeedJson ? JSON.parse(prepared.input.personaSeedJson).authority : {}, personaSeedSha256: sha(prepared.input.personaSeedJson),
        ledger: ledgerIdentity, reducer: reducer("memory-reducer", changed ? "8" : "1"), scopeActorEntityIds: ["actor-one"], episodes: [],
      });
      const relationship = canonical({
        format: "matrix-oasis.npc-relationship-projection", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
        authority: JSON.parse(prepared.input.personaSeedJson).authority, personaSeedSha256: sha(prepared.input.personaSeedJson), relationshipPolicySha256: sha(prepared.input.relationshipPolicyJson),
        ledger: ledgerIdentity, reducer: reducer("relationship-reducer", "2"), scopeActorEntityIds: ["actor-one"], relationships: [],
      });
      const manifest = (projectionKind, artifactJson, reducerIdentity) => canonical({
        format: "matrix-oasis.derived-projection-manifest", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
        projectionKind, reducer: reducerIdentity, ledger: ledgerIdentity, scopeEntityIds: ["actor-one"],
        artifact: { format: projectionKind === "memory" ? "matrix-oasis.npc-memory-projection" : "matrix-oasis.npc-relationship-projection", byteLength: Buffer.byteLength(artifactJson), sha256: sha(artifactJson) },
      });
      const replay = canonical({
        format: "matrix-oasis.world-event-ledger-replay-report", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
        timelineId: ledger.timeline.id, ledgerSha256: sha(worldEventLedgerJson), throughRevision: ledger.revision, throughHeadSha256: ledger.headSha256,
        verifiedEntries: ledger.revision, acceptedEntries: ledger.revision, rejectedEntries: 0, finalSnapshotSha256: fake("4"), finalInspectionSha256: changed ? fake("9") : fake("5"),
      });
      return {
        ok: true,
        canonicalWorldEventLedgerReplayReportJson: replay,
        canonicalNpcMemoryProjectionJson: memory,
        canonicalNpcRelationshipProjectionJson: relationship,
        canonicalMemoryDerivedProjectionManifestJson: manifest("memory", memory, JSON.parse(memory).reducer),
        canonicalRelationshipDerivedProjectionManifestJson: manifest("relationship", relationship, JSON.parse(relationship).reducer),
      };
    },
    async bindNpcDerivedStateSource({ projected, sourceIdentity, personaSeedJson, relationshipPolicyJson }) {
      const memory = projected.canonicalNpcMemoryProjectionJson;
      const relationship = projected.canonicalNpcRelationshipProjectionJson;
      const memoryManifest = projected.canonicalMemoryDerivedProjectionManifestJson;
      const relationshipManifest = projected.canonicalRelationshipDerivedProjectionManifestJson;
      const replay = JSON.parse(projected.canonicalWorldEventLedgerReplayReportJson);
      const memoryDocument = JSON.parse(memory);
      const relationshipDocument = JSON.parse(relationship);
      const ref = (format, text) => ({ format, canonicalSha256: sha(text), byteLength: Buffer.byteLength(text) });
      return { ok: true, canonicalNpcDerivedStateBundleJson: canonical({
        format: "matrix-oasis.npc-derived-state-bundle", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
        source: sourceIdentity, authority: memoryDocument.authority, ledger: memoryDocument.ledger,
        replay: { reportSha256: sha(projected.canonicalWorldEventLedgerReplayReportJson), finalSnapshotSha256: replay.finalSnapshotSha256, finalInspectionSha256: replay.finalInspectionSha256 },
        reducers: { memory: memoryDocument.reducer, relationship: relationshipDocument.reducer },
        profile: { timelineMode: "single", authorityMode: "runtime-and-ledger-only", personaMode: "trusted-static-seed", memoryScope: "actor-self-accepted-actions", relationshipScope: "accepted-explicit-policy-rules", deletionMode: "whole-derived-state", selectiveForgetting: false, externalModelCalls: false, semanticRetrieval: false },
        artifacts: {
          personaSeed: ref("matrix-oasis.npc-persona-seed", personaSeedJson), relationshipPolicy: ref("matrix-oasis.npc-relationship-projection-policy", relationshipPolicyJson),
          memoryProjection: ref("matrix-oasis.npc-memory-projection", memory), relationshipProjection: ref("matrix-oasis.npc-relationship-projection", relationship),
          memoryManifest: ref("matrix-oasis.derived-projection-manifest", memoryManifest), relationshipManifest: ref("matrix-oasis.derived-projection-manifest", relationshipManifest),
        },
      }) };
    },
    async verifyNpcDerivedState() { return { ok: true, diagnostics: [] }; },
  };
}

test("qualification performs twenty repeat builds, deletes its owned probe, rebuilds, and atomically publishes markers", async (t) => {
  const fixture = await qualificationFixture(t, "qualified");
  const runtime = runtimeFixture();
  const result = await runR21Qualification(fixture.args, runtime, { temporaryRoot: fixture.temporaryRoot, ...fixture.operations });
  assert.equal(result.ok, true);
  assert.deepEqual(result.markers, R21_QUALIFICATION_MARKERS);
  assert.equal(runtime.projectionCount() >= 21, true);
  assert.deepEqual((await readdir(fixture.output)).sort(), R21_QUALIFICATION_FILES);
  const report = JSON.parse(await readFile(path.join(fixture.output, "npc-projection-qualification-report.json"), "utf8"));
  assert.equal(report.rebuilds.repeatedBuildCount, 20);
  assert.equal(report.deletion.derivedArtifactsRemoved, true);
  assert.deepEqual(report.markers, R21_QUALIFICATION_MARKERS);
  assert.equal(fixture.isLeaseActive(), false);
  const leftovers = (await readdir(fixture.temporaryRoot)).filter((name) => name.startsWith(".r21-qualified-qualified-deletion-probe-"));
  assert.deepEqual(leftovers, []);
  const quarantines = (await readdir(fixture.temporaryRoot)).filter((name) => name.startsWith(".r21-quarantine-"));
  assert.equal(quarantines.length, 1);
  const tombstoneRoot = path.join(fixture.temporaryRoot, quarantines[0], "owned");
  assert.deepEqual((await readdir(tombstoneRoot)).sort(), R21_PROJECT_FILES);
  for (const name of R21_PROJECT_FILES) assert.equal((await stat(path.join(tombstoneRoot, name))).size > 0, true);
});

test("qualification detects a nondeterministic reducer before deletion publication and leaves no output", async (t) => {
  const fixture = await qualificationFixture(t, "nondeterministic");
  const runtime = runtimeFixture({ nondeterministicAt: 2 });
  await assert.rejects(runR21Qualification(fixture.args, runtime, { temporaryRoot: fixture.temporaryRoot, ...fixture.operations }), /R21_PROJECTION_REBUILD_MISMATCH/u);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  assert.equal(fixture.isLeaseActive(), false);
});

test("qualification refuses an existing output without acquiring the R20 source lease", async (t) => {
  const fixture = await qualificationFixture(t, "existing");
  await mkdir(fixture.output);
  await writeFile(path.join(fixture.output, "sentinel.txt"), "owned");
  await assert.rejects(runR21Qualification(fixture.args, runtimeFixture(), { temporaryRoot: fixture.temporaryRoot, ...fixture.operations }), /R21_OUTPUT_EXISTS/u);
  assert.equal(fixture.isLeaseActive(), false);
  assert.equal(await readFile(path.join(fixture.output, "sentinel.txt"), "utf8"), "owned");
});

test("a namespace deletion failure stops before the second build and never publishes a qualification", async (t) => {
  const fixture = await qualificationFixture(t, "delete-failure");
  const runtime = runtimeFixture();
  let deletes = 0;
  await assert.rejects(runR21Qualification(fixture.args, runtime, {
    temporaryRoot: fixture.temporaryRoot, ...fixture.operations,
    async rename(from, to) {
      if (to.includes(".r21-quarantine-")) { deletes += 1; throw new Error("quarantine-denied"); }
      return rename(from, to);
    },
  }), /R21_PROBE_ROLLBACK_FAILED/u);
  assert.equal(deletes >= 1, true);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  assert.equal(fixture.isLeaseActive(), false);
});

test("a staging open failure leaves neither a success path nor an unquarantined stage", async (t) => {
  const fixture = await qualificationFixture(t, "stage-open-failure");
  await assert.rejects(runR21Qualification(fixture.args, runtimeFixture(), {
    temporaryRoot: fixture.temporaryRoot,
    ...fixture.operations,
    async openFile() { throw new Error("open-denied"); },
  }), /open-denied/u);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  assert.equal(fixture.isLeaseActive(), false);
  assert.deepEqual((await readdir(fixture.temporaryRoot)).filter((name) => name.startsWith(".r21-stage-open-failure-qualified-")), []);
});

test("a staging sync failure quarantines only the proven wx-created partial file", async (t) => {
  const fixture = await qualificationFixture(t, "stage-sync-failure");
  let failed = false;
  await assert.rejects(runR21Qualification(fixture.args, runtimeFixture(), {
    temporaryRoot: fixture.temporaryRoot,
    ...fixture.operations,
    async openFile(candidate, flags) {
      const handle = await open(candidate, flags);
      return {
        stat: (...args) => handle.stat(...args),
        writeFile: (...args) => handle.writeFile(...args),
        async sync() {
          if (!failed) { failed = true; throw new Error("sync-denied"); }
          return handle.sync();
        },
        read: (...args) => handle.read(...args),
        close: () => handle.close(),
      };
    },
  }), /sync-denied/u);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  assert.equal(fixture.isLeaseActive(), false);
  assert.deepEqual((await readdir(fixture.temporaryRoot)).filter((name) => name.startsWith(".r21-stage-sync-failure-qualified-")), []);
  const quarantine = (await readdir(fixture.temporaryRoot)).find((name) => name.startsWith(".r21-quarantine-"));
  const tombstones = await readdir(path.join(fixture.temporaryRoot, quarantine, "owned"));
  assert.equal(tombstones.length, 1);
  const tombstone = await readFile(path.join(fixture.temporaryRoot, quarantine, "owned", tombstones[0]));
  assert.equal(tombstone.length > 0, true);
});

test("an unknown staging entry is quarantined and surfaces a stable cleanup failure", async (t) => {
  const fixture = await qualificationFixture(t, "stage-unknown-entry");
  let injected = false;
  await assert.rejects(runR21Qualification(fixture.args, runtimeFixture(), {
    temporaryRoot: fixture.temporaryRoot,
    ...fixture.operations,
    async openFile(candidate, flags) {
      const handle = await open(candidate, flags);
      return {
        stat: (...args) => handle.stat(...args),
        async writeFile(...args) {
          await handle.writeFile(...args);
          if (!injected) {
            injected = true;
            await writeFile(path.join(path.dirname(candidate), "unexpected.bin"), "preserve");
            throw new Error("write-interrupted");
          }
        },
        sync: () => handle.sync(),
        read: (...args) => handle.read(...args),
        truncate: (...args) => handle.truncate(...args),
        close: () => handle.close(),
      };
    },
  }), /R21_STAGING_CLEANUP_FAILED/u);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  const quarantine = (await readdir(fixture.temporaryRoot)).find((name) => name.startsWith(".r21-quarantine-"));
  assert.equal(typeof quarantine, "string");
  assert.equal(await readFile(path.join(fixture.temporaryRoot, quarantine, "owned", "unexpected.bin"), "utf8"), "preserve");
  assert.equal(fixture.isLeaseActive(), false);
});

test("qualification file set is exactly one report beyond the immutable project artifact set", () => {
  assert.deepEqual(R21_QUALIFICATION_FILES.filter((name) => name !== "npc-projection-qualification-report.json"), R21_PROJECT_FILES);
});

test("verify rejects a schema-valid qualification report transplanted from another source", async (t) => {
  const target = await qualificationFixture(t, "verify-target");
  const donor = await qualificationFixture(t, "verify-donor");
  const targetRuntime = runtimeFixture();
  await runR21Qualification(target.args, targetRuntime, { temporaryRoot: target.temporaryRoot, ...target.operations });
  await runR21Qualification(donor.args, runtimeFixture(), { temporaryRoot: donor.temporaryRoot, ...donor.operations });
  assert.equal((await runR21Verify(target.verifyArgs, targetRuntime, { temporaryRoot: target.temporaryRoot, ...target.operations })).ok, true);
  const donorReport = await readFile(path.join(donor.output, "npc-projection-qualification-report.json"));
  await writeFile(path.join(target.output, "npc-projection-qualification-report.json"), donorReport);
  await assert.rejects(runR21Verify(target.verifyArgs, targetRuntime, { temporaryRoot: target.temporaryRoot, ...target.operations }), /R21_QUALIFICATION_REPORT_IDENTITY_MISMATCH/u);
  assert.equal(target.isLeaseActive(), false);
});

test("persona input drift after the publication rename rolls back the success path", async (t) => {
  const fixture = await qualificationFixture(t, "persona-drift");
  let renames = 0;
  await assert.rejects(runR21Qualification(fixture.args, runtimeFixture(), {
    temporaryRoot: fixture.temporaryRoot,
    ...fixture.operations,
    async rename(from, to) {
      renames += 1;
      const result = await rename(from, to);
      if (to === fixture.output) await writeFile(fixture.personaSeed, canonical({ changed: true }));
      return result;
    },
  }), /R21_INPUT_CHANGED/u);
  assert.equal(renames >= 3, true);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  assert.equal(fixture.isLeaseActive(), false);
});

test("a post-effect R20 lease release failure removes the just-published qualification", async (t) => {
  const fixture = await qualificationFixture(t, "lease-release");
  const release = fixture.operations.releaseWriterLease;
  fixture.operations.releaseWriterLease = async (lease) => {
    await release(lease);
    throw new Error("post-effect-release-error");
  };
  await assert.rejects(runR21Qualification(fixture.args, runtimeFixture(), {
    temporaryRoot: fixture.temporaryRoot,
    ...fixture.operations,
  }), /R21_SOURCE_LEASE_RELEASE_FAILED/u);
  await assert.rejects(stat(fixture.output), /ENOENT/u);
  assert.equal(fixture.isLeaseActive(), false);
});

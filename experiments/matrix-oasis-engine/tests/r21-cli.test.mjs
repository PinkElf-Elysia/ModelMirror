import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, open, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  R21_PROJECT_FILES,
  acquireQualifiedR20Source,
  inspectQualifiedR20Source,
  parseR21ProjectArguments,
  publishR21Artifacts,
  releaseQualifiedR20Source,
  revalidateQualifiedR20Source,
} from "../scripts/lib/r21-cli-core.mjs";

const canonical = (value) => canonicalizeJsonValue(value);
const fake = (character) => `sha256:${character.repeat(64)}`;
const sha = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;

async function temporary(t, label) {
  const root = await mkdtemp(path.join(tmpdir(), `r21-cli-${label}-`));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

function artifactMap(label = "stable") {
  return new Map(R21_PROJECT_FILES.map((name) => [name, new TextEncoder().encode(canonical({ label, name }))]));
}

async function sourceFixture(t, label = "source") {
  const temporaryRoot = await temporary(t, label);
  const npcRunRoot = path.join(temporaryRoot, `${label}-npc`);
  const timelineId = "timeline-one";
  const authorityManifestJson = canonical({ timelineId });
  const manifestId = sha(authorityManifestJson).slice(7);
  const headSha256 = fake("b");
  const qualificationReceiptSha256 = fake("c");
  const current = {
    format: "matrix-oasis.npc-current",
    formatVersion: "0.1.0",
    manifestSha256: `sha256:${manifestId}`,
    timelineId,
    revision: 1,
    headSha256,
    qualificationReceiptSha256,
  };
  const timelineRoot = path.join(npcRunRoot, "timelines", manifestId);
  await mkdir(timelineRoot, { recursive: true });
  const runtimePack = path.join(temporaryRoot, `${label}-runtime-pack.json`);
  const runtimeReceipt = path.join(temporaryRoot, `${label}-runtime-receipt.json`);
  const authorityPolicy = path.join(temporaryRoot, `${label}-authority-policy.json`);
  const runtimePackJson = canonical({ id: "pack" });
  const runtimeReceiptJson = canonical({ id: "receipt" });
  const authorityPolicyJson = canonical({ id: "policy" });
  const evidence = {
    formatVersion: "0.2.0",
    legacy: false,
    runtimeGamePackJson: runtimePackJson,
    runtimeReceiptJson,
    authorityPolicyJson,
    runtimeGamePackSha256: null,
    runtimeReceiptSha256: null,
    authorityPolicySha256: null,
    qualificationReceiptSha256,
  };
  const files = new Map([
    [path.join(npcRunRoot, "npc-current.json"), canonical(current)],
    [path.join(timelineRoot, "authority-manifest.json"), authorityManifestJson],
    [path.join(timelineRoot, "entity-bindings.json"), canonical({ bindings: [] })],
    [path.join(timelineRoot, "world-event-ledger.json"), canonical({ timeline: { id: timelineId }, revision: 1, headSha256 })],
    [path.join(timelineRoot, "qualification-evidence.json"), canonical({ fixture: "qualification" })],
    [runtimePack, runtimePackJson],
    [runtimeReceipt, runtimeReceiptJson],
    [authorityPolicy, authorityPolicyJson],
  ]);
  for (const [file, text] of files) await writeFile(file, text, { flag: "wx" });
  evidence.runtimeGamePackSha256 = sha(await readFile(runtimePack));
  evidence.runtimeReceiptSha256 = sha(await readFile(runtimeReceipt));
  evidence.authorityPolicySha256 = sha(await readFile(authorityPolicy));
  const audit = {
    ok: true,
    current,
    pendingCurrent: null,
    timelines: [{ manifestId, timelineId, revision: 1, headSha256, qualificationReceiptSha256, qualified: true, status: "qualified" }],
  };
  let leaseActive = false;
  const operations = {
    async acquireWriterLease() { assert.equal(leaseActive, false); leaseActive = true; return Object.freeze({ lease: label }); },
    async auditTimelineStore() { assert.equal(leaseActive, true); return structuredClone(audit); },
    async releaseWriterLease() { assert.equal(leaseActive, true); leaseActive = false; },
    validateQualificationEvidence() { return Object.freeze({ ...evidence }); },
  };
  return { temporaryRoot, npcRunRoot, timelineRoot, runtimePack, runtimeReceipt, authorityPolicy, current, audit, evidence, operations, isLeaseActive: () => leaseActive };
}

test("R21 argument parsing fixes all inputs and the output to one new temp child", async (t) => {
  const root = await temporary(t, "arguments");
  const values = [
    "--npc-run-root", path.join(root, "run-npc"),
    "--runtime-pack", path.join(root, "pack.json"),
    "--runtime-receipt", path.join(root, "receipt.json"),
    "--authority-policy", path.join(root, "policy.json"),
    "--persona-seed", path.join(root, "persona.json"),
    "--relationship-policy", path.join(root, "relationship.json"),
    "--output", path.join(root, "derived-output"),
  ];
  assert.equal(parseR21ProjectArguments(values, root).output, path.join(root, "derived-output"));
  await assert.rejects(async () => parseR21ProjectArguments([...values.slice(0, -1), path.join(root, "nested", "output")], root), /R21_CLI_ARGUMENT_INVALID/u);
  await assert.rejects(async () => parseR21ProjectArguments([...values.slice(0, -1), path.join(root, "CON")], root), /R21_CLI_ARGUMENT_INVALID/u);
});

test("qualified R20 loader keeps one lease, binds exact embedded inputs, and detects source drift", async (t) => {
  const fixture = await sourceFixture(t, "qualified");
  const handle = await acquireQualifiedR20Source({
    npcRunRoot: fixture.npcRunRoot,
    runtimePack: fixture.runtimePack,
    runtimeReceipt: fixture.runtimeReceipt,
    authorityPolicy: fixture.authorityPolicy,
    temporaryRoot: fixture.temporaryRoot,
  }, fixture.operations);
  assert.equal(fixture.isLeaseActive(), true);
  const source = inspectQualifiedR20Source(handle);
  assert.equal(source.current.timelineId, fixture.current.timelineId);
  assert.equal(source.qualificationEvidence.runtimeGamePackJson, source.runtimeGamePackJson);
  await revalidateQualifiedR20Source(handle);
  await writeFile(path.join(fixture.timelineRoot, "world-event-ledger.json"), canonical({ timeline: { id: "timeline-one" }, revision: 1, headSha256: fake("d") }));
  await assert.rejects(revalidateQualifiedR20Source(handle), /R21_R20_SOURCE_CHANGED/u);
  await releaseQualifiedR20Source(handle);
  assert.equal(fixture.isLeaseActive(), false);
});

test("loader refuses pending current, mismatched evidence, and an active writer before publishing", async (t) => {
  for (const mode of ["pending", "mismatch", "active"]) {
    const fixture = await sourceFixture(t, mode);
    if (mode === "pending") fixture.audit.pendingCurrent = structuredClone(fixture.current);
    if (mode === "mismatch") fixture.evidence.runtimeGamePackJson = canonical({ id: "other-pack" });
    if (mode === "active") fixture.operations.acquireWriterLease = async () => { throw new Error("R20_STORE_WRITER_ACTIVE"); };
    await assert.rejects(acquireQualifiedR20Source({
      npcRunRoot: fixture.npcRunRoot,
      runtimePack: fixture.runtimePack,
      runtimeReceipt: fixture.runtimeReceipt,
      authorityPolicy: fixture.authorityPolicy,
      temporaryRoot: fixture.temporaryRoot,
    }, fixture.operations), mode === "pending" ? /R21_R20_SOURCE_NOT_QUIESCENT/u : mode === "mismatch" ? /R21_R20_QUALIFICATION_IDENTITY_MISMATCH/u : /R20_STORE_WRITER_ACTIVE/u);
  }
});

test("a source-validation failure plus lease-release failure is surfaced as a stable lease error", async (t) => {
  const fixture = await sourceFixture(t, "release-on-invalid");
  fixture.audit.pendingCurrent = structuredClone(fixture.current);
  const release = fixture.operations.releaseWriterLease;
  fixture.operations.releaseWriterLease = async (lease) => {
    await release(lease);
    throw new Error("post-effect-release-error");
  };
  await assert.rejects(acquireQualifiedR20Source({
    npcRunRoot: fixture.npcRunRoot,
    runtimePack: fixture.runtimePack,
    runtimeReceipt: fixture.runtimeReceipt,
    authorityPolicy: fixture.authorityPolicy,
    temporaryRoot: fixture.temporaryRoot,
  }, fixture.operations), /R21_SOURCE_LEASE_RELEASE_FAILED/u);
  assert.equal(fixture.isLeaseActive(), false);
});

test("publication uses one directory rename and verifies the final bytes", async (t) => {
  const root = await temporary(t, "publish");
  const output = path.join(root, "result");
  let renames = 0; let verifies = 0;
  await publishR21Artifacts({
    artifacts: artifactMap(), output, temporaryRoot: root,
    verifyDirectory: async (directory) => { verifies += 1; assert.equal((await stat(directory)).isDirectory(), true); },
  }, { async rename(from, to) { renames += 1; return rename(from, to); } });
  assert.equal(renames, 1);
  assert.equal(verifies, 2);
  assert.deepEqual((await readdir(output)).sort(), R21_PROJECT_FILES);
});

test("post-effect rename error recovers exact output while pre-effect conflict preserves the competitor", async (t) => {
  const root = await temporary(t, "rename");
  const recovered = path.join(root, "recovered");
  await publishR21Artifacts({ artifacts: artifactMap("post"), output: recovered, temporaryRoot: root, verifyDirectory: async () => {} }, {
    async rename(from, to) { await rename(from, to); throw new Error("post-effect"); },
  });
  assert.equal((await stat(recovered)).isDirectory(), true);

  const conflict = path.join(root, "conflict");
  await assert.rejects(publishR21Artifacts({ artifacts: artifactMap("pre"), output: conflict, temporaryRoot: root, verifyDirectory: async () => {} }, {
    async rename(_from, to) { await mkdir(to); await writeFile(path.join(to, "sentinel.txt"), "owned"); const error = new Error("pre-effect"); error.code = "EEXIST"; throw error; },
  }), /pre-effect/u);
  assert.equal(await readFile(path.join(conflict, "sentinel.txt"), "utf8"), "owned");
});

test("a failed post-publication source check removes only the exact newly-owned output", async (t) => {
  const root = await temporary(t, "rollback");
  const output = path.join(root, "rolled-back");
  await assert.rejects(publishR21Artifacts({
    artifacts: artifactMap("rollback"), output, temporaryRoot: root,
    verifyDirectory: async () => {}, afterRename: async () => { throw new Error("source-drift"); },
  }), /source-drift/u);
  await assert.rejects(stat(output), /ENOENT/u);
});

test("rollback quarantines a directory swapped after validation and never deletes the competitor", async (t) => {
  const root = await temporary(t, "rollback-swap");
  const output = path.join(root, "swapped-output");
  const displacedOwned = path.join(root, "displaced-owned");
  let renames = 0;
  await assert.rejects(publishR21Artifacts({
    artifacts: artifactMap("swap"), output, temporaryRoot: root,
    verifyDirectory: async () => {}, afterRename: async () => { throw new Error("source-drift"); },
  }, {
    async rename(from, to) {
      renames += 1;
      if (renames === 2) {
        await rename(from, displacedOwned);
        await mkdir(from);
        await writeFile(path.join(from, "sentinel.txt"), "competitor");
      }
      return rename(from, to);
    },
  }), /R21_OUTPUT_ROLLBACK_FAILED/u);
  assert.equal(await readFile(path.join(displacedOwned, "npc-derived-state-bundle.json"), "utf8"), canonical({ label: "swap", name: "npc-derived-state-bundle.json" }));
  assert.equal(await readFile(path.join(output, "sentinel.txt"), "utf8"), "competitor");
});

test("rollback preserves both directories when a replacement path is occupied during recovery", async (t) => {
  const root = await temporary(t, "rollback-double-swap");
  const output = path.join(root, "double-swapped-output");
  const displacedOwned = path.join(root, "double-displaced-owned");
  let renames = 0;
  await assert.rejects(publishR21Artifacts({
    artifacts: artifactMap("double-swap"), output, temporaryRoot: root,
    verifyDirectory: async () => {}, afterRename: async () => { throw new Error("source-drift"); },
  }, {
    async rename(from, to) {
      renames += 1;
      if (renames === 2) {
        await rename(from, displacedOwned);
        await mkdir(from);
        await writeFile(path.join(from, "sentinel.txt"), "first-competitor");
        await rename(from, to);
        await mkdir(from);
        await writeFile(path.join(from, "sentinel.txt"), "second-competitor");
        return;
      }
      return rename(from, to);
    },
  }), /R21_OUTPUT_ROLLBACK_FAILED/u);
  assert.equal(await readFile(path.join(output, "sentinel.txt"), "utf8"), "second-competitor");
  assert.equal(await readFile(path.join(displacedOwned, "npc-derived-state-bundle.json"), "utf8"), canonical({ label: "double-swap", name: "npc-derived-state-bundle.json" }));
  const quarantine = (await readdir(root)).find((name) => name.startsWith(".r21-quarantine-"));
  assert.equal(typeof quarantine, "string");
  assert.equal(await readFile(path.join(root, quarantine, "owned", "sentinel.txt"), "utf8"), "first-competitor");
});

test("rollback never recursively deletes an unexpected file injected into the published directory", async (t) => {
  const root = await temporary(t, "rollback-extra");
  const output = path.join(root, "extra-output");
  await assert.rejects(publishR21Artifacts({
    artifacts: artifactMap("extra"), output, temporaryRoot: root,
    verifyDirectory: async () => {},
    afterRename: async () => {
      await writeFile(path.join(output, "sentinel.txt"), "preserve-me");
      throw new Error("source-drift");
    },
  }), /source-drift/u);
  await assert.rejects(stat(output), /ENOENT/u);
  const quarantine = (await readdir(root)).find((name) => name.startsWith(".r21-quarantine-"));
  assert.equal(typeof quarantine, "string");
  assert.equal(await readFile(path.join(root, quarantine, "owned", "sentinel.txt"), "utf8"), "preserve-me");
});

test("rollback detects an isolated-parent swap during validation and preserves both the owned bytes and competitor", async (t) => {
  const root = await temporary(t, "rollback-before-truncate-swap");
  const output = path.join(root, "before-truncate-output");
  const displacedOwned = path.join(root, "before-truncate-displaced-owned");
  let competitor = null;
  let swapped = false;
  await assert.rejects(publishR21Artifacts({
    artifacts: artifactMap("during-validation"), output, temporaryRoot: root,
    verifyDirectory: async () => {}, afterRename: async () => { throw new Error("source-drift"); },
  }, {
    async readStableFile(candidate, ...rest) {
      if (candidate.includes(".r21-quarantine-") && !swapped) {
        swapped = true;
        const isolated = path.dirname(candidate);
        competitor = path.join(isolated, path.basename(candidate));
        await rename(isolated, displacedOwned);
        await mkdir(isolated);
        await writeFile(competitor, "competitor-during-validation");
      }
      return (await import("../scripts/lib/r20-cli-core.mjs")).readStableR20File(candidate, ...rest);
    },
  }), /source-drift/u);
  assert.equal(swapped, true);
  await assert.rejects(stat(output), /ENOENT/u);
  assert.equal(await readFile(competitor, "utf8"), "competitor-during-validation");
  assert.equal(
    await readFile(path.join(displacedOwned, path.basename(competitor)), "utf8"),
    canonical({ label: "during-validation", name: path.basename(competitor) }),
  );
});

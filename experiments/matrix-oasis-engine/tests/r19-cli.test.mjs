import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import * as contracts from "@matrix-oasis/npc-authority-contracts";
import * as operations from "@matrix-oasis/npc-authority-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  executeAdjudicateNpcIntentCli,
  executeCreateNpcAuthorityTimelineCli,
  executeReplayWorldEventLedgerCli,
  executeValidateNpcAuthorityCli,
  R19_TEMP_ROOT,
} from "../scripts/lib/r19-cli-core.mjs";

const services = Object.freeze({ lstat: fs.lstat, realpath: fs.realpath, openFile: fs.open, mkdtemp: fs.mkdtemp, rename: fs.rename, rm: fs.rm });
const tempRoot = R19_TEMP_ROOT;
const authoringText = await fs.readFile(new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8");
const compiled = await compileAuthoringGamePackJson(authoringText);
assert.equal(compiled.ok, true);

function policyJson() {
  const pack = compiled.runtimePack;
  const receipt = compiled.receipt;
  return canonicalizeJsonValue({
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "cli-authority",
    contentVersion: "1.0.0",
    runtime: {
      format: pack.format,
      formatVersion: pack.formatVersion,
      id: pack.source.id,
      contentVersion: pack.source.contentVersion,
      sourceSha256: `sha256:${pack.source.canonicalSha256}`,
      artifactSha256: `sha256:${receipt.artifact.sha256}`,
      receiptSha256: operations.hashCanonicalValue(receipt),
    },
    actorGrants: [{ actorEntityId: "actor-unit", grants: [{ nodeId: "node-start", actionId: "action-initialize" }] }],
  });
}

async function fixtureRoot(t) {
  const root = await fs.mkdtemp(path.join(tempRoot, "matrix-oasis-r19-cli-test-"));
  t.after(async () => { await fs.rm(root, { recursive: true, force: true }); });
  const inputs = path.join(root, "inputs");
  await fs.mkdir(inputs);
  const paths = {
    runtime: path.join(inputs, "runtime.json"),
    receipt: path.join(inputs, "receipt.json"),
    policy: path.join(inputs, "policy.json"),
  };
  await fs.writeFile(paths.runtime, compiled.canonicalJson, "utf8");
  await fs.writeFile(paths.receipt, canonicalizeJsonValue(compiled.receipt), "utf8");
  await fs.writeFile(paths.policy, policyJson(), "utf8");
  return { root, paths };
}

function shaFile(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

test("CLI creates, adjudicates and replays a canonical authority directory", async (t) => {
  const fixture = await fixtureRoot(t);
  const timeline = path.join(fixture.root, "timeline");
  const created = await executeCreateNpcAuthorityTimelineCli({
    args: ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output", timeline],
    tempRoot,
    services,
    operations,
  });
  assert.deepEqual(created, { exitCode: 0, stdout: "R19_AUTHORITY_TIMELINE_CREATED\n", stderr: "" });
  const snapshot = JSON.parse(await fs.readFile(path.join(timeline, "runtime-snapshot.json"), "utf8"));
  const ledger = JSON.parse(await fs.readFile(path.join(timeline, "world-event-ledger.json"), "utf8"));
  const intent = canonicalizeJsonValue({
    format: "matrix-oasis.npc-intent",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "intent-cli",
    actorEntityId: "actor-unit",
    timelineId: "timeline-cli",
    nodeId: "node-start",
    actionId: "action-initialize",
    observed: { revision: ledger.revision, headSha256: ledger.headSha256, runtimeSnapshotSha256: operations.hashCanonicalValue(snapshot) },
  });
  const intentPath = path.join(fixture.root, "intent.json");
  await fs.writeFile(intentPath, intent, "utf8");
  const adjudicatedDir = path.join(fixture.root, "adjudicated");
  const adjudicated = await executeAdjudicateNpcIntentCli({ args: ["--authority-dir", timeline, "--intent", intentPath, "--output", adjudicatedDir], tempRoot, services, operations });
  assert.equal(adjudicated.exitCode, 0, adjudicated.stderr);
  const result = JSON.parse(await fs.readFile(path.join(adjudicatedDir, "adjudication-result.json"), "utf8"));
  assert.equal(result.decision.status, "accepted");
  const replayDir = path.join(fixture.root, "replay");
  const replayed = await executeReplayWorldEventLedgerCli({ args: ["--authority-dir", adjudicatedDir, "--output", replayDir], tempRoot, services, operations });
  assert.equal(replayed.exitCode, 0, replayed.stderr);
  const report = JSON.parse(await fs.readFile(path.join(replayDir, "world-event-ledger-replay-report.json"), "utf8"));
  assert.equal(report.verifiedEntries, 1);
  assert.equal(report.finalSnapshotSha256, operations.hashCanonicalValue(JSON.parse(await fs.readFile(path.join(adjudicatedDir, "runtime-snapshot.json"), "utf8"))));
});

test("CLI validation emits only the canonical static report", async (t) => {
  const fixture = await fixtureRoot(t);
  const result = await executeValidateNpcAuthorityCli({
    args: ["--kind", "policy", "--file", fixture.paths.policy],
    services,
    validators: { policy: contracts.validateNpcAuthorityPolicyJson },
  });
  assert.equal(result.exitCode, 0);
  assert.deepEqual(JSON.parse(result.stdout), { diagnostics: [], reportVersion: 1, valid: true });
  assert.equal(result.stderr, "");
});

test("existing output is never overwritten", async (t) => {
  const fixture = await fixtureRoot(t);
  const output = path.join(fixture.root, "timeline");
  const args = ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output", output];
  assert.equal((await executeCreateNpcAuthorityTimelineCli({ args, tempRoot, services, operations })).exitCode, 0);
  const before = shaFile(await fs.readFile(path.join(output, "world-event-ledger.json"), "utf8"));
  const second = await executeCreateNpcAuthorityTimelineCli({ args, tempRoot, services, operations });
  assert.equal(second.exitCode, 2);
  assert.equal(second.stderr, "NPC_AUTHORITY_CLI_OUTPUT_EXISTS\n");
  assert.equal(shaFile(await fs.readFile(path.join(output, "world-event-ledger.json"), "utf8")), before);
});

test("invalid output scope and mid-publish failure leave no target", async (t) => {
  const fixture = await fixtureRoot(t);
  const baseArgs = ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output"];
  const escapedPath = path.win32.join("C:" + path.win32.sep, "matrix-oasis-r19-escape");
  const escaped = await executeCreateNpcAuthorityTimelineCli({ args: [...baseArgs, escapedPath], tempRoot, services, operations });
  assert.equal(escaped.stderr, "NPC_AUTHORITY_CLI_OUTPUT_INVALID\n");
  const target = path.join(fixture.root, "publish-failure");
  const failingServices = { ...services, rename: async () => { throw new Error("injected"); } };
  const failed = await executeCreateNpcAuthorityTimelineCli({ args: [...baseArgs, target], tempRoot, services: failingServices, operations });
  assert.equal(failed.stderr, "NPC_AUTHORITY_INTERNAL_ERROR\n");
  await assert.rejects(fs.lstat(target), { code: "ENOENT" });
  const leftovers = (await fs.readdir(fixture.root)).filter((name) => name.startsWith(".matrix-oasis-r19-"));
  assert.deepEqual(leftovers, []);

  const tamperedTarget = path.join(fixture.root, "post-rename-tamper");
  const tamperingServices = {
    ...services,
    rename: async (source, destination) => {
      await fs.rename(source, destination);
      await fs.writeFile(path.join(destination, "npc-authority-policy.json"), "{}", "utf8");
    },
  };
  const tampered = await executeCreateNpcAuthorityTimelineCli({ args: [...baseArgs, tamperedTarget], tempRoot, services: tamperingServices, operations });
  assert.equal(tampered.stderr, "NPC_AUTHORITY_INTERNAL_ERROR\n");
  await assert.rejects(fs.lstat(tamperedTarget), { code: "ENOENT" });
});

test("concurrent publication has exactly one winner and no staging residue", async (t) => {
  const fixture = await fixtureRoot(t);
  const output = path.join(fixture.root, "concurrent");
  const args = ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output", output];
  const results = await Promise.all([
    executeCreateNpcAuthorityTimelineCli({ args, tempRoot, services, operations }),
    executeCreateNpcAuthorityTimelineCli({ args, tempRoot, services, operations }),
  ]);
  assert.deepEqual(results.map((result) => result.exitCode).sort(), [0, 2]);
  assert.equal(contracts.validateWorldEventLedgerJson(await fs.readFile(path.join(output, "world-event-ledger.json"), "utf8")).valid, true);
  assert.deepEqual((await fs.readdir(fixture.root)).filter((name) => name.startsWith(".matrix-oasis-r19-")), []);
});

test("authority junctions and an input identity change fail closed", async (t) => {
  const fixture = await fixtureRoot(t);
  const timeline = path.join(fixture.root, "timeline");
  const created = await executeCreateNpcAuthorityTimelineCli({
    args: ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output", timeline],
    tempRoot,
    services,
    operations,
  });
  assert.equal(created.exitCode, 0);
  const junction = path.join(fixture.root, "authority-junction");
  await fs.symlink(timeline, junction, "junction");
  const junctionResult = await executeReplayWorldEventLedgerCli({ args: ["--authority-dir", junction, "--output", path.join(fixture.root, "junction-output")], tempRoot, services, operations });
  assert.equal(junctionResult.stderr, "NPC_AUTHORITY_CLI_AUTHORITY_DIR_INVALID\n");

  let handleStatCalls = 0;
  const changingServices = {
    ...services,
    openFile: async (...args) => {
      const handle = await fs.open(...args);
      return {
        read: (...readArgs) => handle.read(...readArgs),
        close: () => handle.close(),
        stat: async (options) => {
          const value = await handle.stat(options);
          handleStatCalls += 1;
          if (handleStatCalls !== 2) return value;
          return new Proxy(value, {
            get(target, property) {
              if (property === "mtimeNs") return target.mtimeNs + 1n;
              const observed = Reflect.get(target, property, target);
              return typeof observed === "function" ? observed.bind(target) : observed;
            },
          });
        },
      };
    },
  };
  const changed = await executeValidateNpcAuthorityCli({
    args: ["--kind", "policy", "--file", fixture.paths.policy],
    services: changingServices,
    validators: { policy: contracts.validateNpcAuthorityPolicyJson },
  });
  assert.equal(changed.stderr, "NPC_AUTHORITY_CLI_INPUT_CHANGED\n");
});

test("authority directory identity replacement during a read fails closed", async (t) => {
  const fixture = await fixtureRoot(t);
  const timeline = path.join(fixture.root, "timeline");
  const created = await executeCreateNpcAuthorityTimelineCli({
    args: ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output", timeline],
    tempRoot,
    services,
    operations,
  });
  assert.equal(created.exitCode, 0);

  let directoryChecks = 0;
  const changingServices = {
    ...services,
    lstat: async (candidate, options) => {
      const value = await fs.lstat(candidate, options);
      if (path.resolve(candidate) !== path.resolve(timeline)) return value;
      directoryChecks += 1;
      if (directoryChecks === 1) return value;
      return new Proxy(value, {
        get(target, property) {
          if (property === "ino") return target.ino + 1n;
          const observed = Reflect.get(target, property, target);
          return typeof observed === "function" ? observed.bind(target) : observed;
        },
      });
    },
  };
  const result = await executeReplayWorldEventLedgerCli({
    args: ["--authority-dir", timeline, "--output", path.join(fixture.root, "replay")],
    tempRoot,
    services: changingServices,
    operations,
  });
  assert.equal(result.stderr, "NPC_AUTHORITY_CLI_INPUT_CHANGED\n");
  assert.equal(directoryChecks >= 2, true);
  await assert.rejects(fs.lstat(path.join(fixture.root, "replay")), { code: "ENOENT" });
});

test("output parent identity replacement before staging fails closed", async (t) => {
  const fixture = await fixtureRoot(t);
  const output = path.join(fixture.root, "timeline");
  let parentChecks = 0;
  const changingServices = {
    ...services,
    lstat: async (candidate, options) => {
      const value = await fs.lstat(candidate, options);
      if (path.resolve(candidate) !== path.resolve(fixture.root)) return value;
      parentChecks += 1;
      if (parentChecks === 1) return value;
      return new Proxy(value, {
        get(target, property) {
          if (property === "ino") return target.ino + 1n;
          const observed = Reflect.get(target, property, target);
          return typeof observed === "function" ? observed.bind(target) : observed;
        },
      });
    },
  };
  const result = await executeCreateNpcAuthorityTimelineCli({
    args: ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output", output],
    tempRoot,
    services: changingServices,
    operations,
  });
  assert.equal(result.stderr, "NPC_AUTHORITY_INTERNAL_ERROR\n");
  assert.equal(parentChecks >= 2, true);
  await assert.rejects(fs.lstat(output), { code: "ENOENT" });
});

test("output parent identity replacement after staging fails before rename", async (t) => {
  const fixture = await fixtureRoot(t);
  const output = path.join(fixture.root, "timeline");
  let parentChecks = 0;
  const changingServices = {
    ...services,
    lstat: async (candidate, options) => {
      const value = await fs.lstat(candidate, options);
      if (path.resolve(candidate) !== path.resolve(fixture.root)) return value;
      parentChecks += 1;
      if (parentChecks < 4) return value;
      return new Proxy(value, {
        get(target, property) {
          if (property === "ino") return target.ino + 1n;
          const observed = Reflect.get(target, property, target);
          return typeof observed === "function" ? observed.bind(target) : observed;
        },
      });
    },
  };
  const result = await executeCreateNpcAuthorityTimelineCli({
    args: ["--runtime-pack", fixture.paths.runtime, "--runtime-receipt", fixture.paths.receipt, "--policy", fixture.paths.policy, "--timeline", "timeline-cli", "--output", output],
    tempRoot,
    services: changingServices,
    operations,
  });
  assert.equal(result.stderr, "NPC_AUTHORITY_INTERNAL_ERROR\n");
  assert.equal(parentChecks >= 4, true);
  await assert.rejects(fs.lstat(output), { code: "ENOENT" });
  assert.deepEqual((await fs.readdir(fixture.root)).filter((name) => name.startsWith(".matrix-oasis-r19-")), []);
});

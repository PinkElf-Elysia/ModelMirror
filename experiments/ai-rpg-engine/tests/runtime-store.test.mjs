import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import test, { mock } from "node:test";
import { fileURLToPath } from "node:url";
import { openFileSessionStore, recoverSession, sha256 } from "../runtime/node.mjs";
import { canonicalJson, computeProposalSha256, validateModelProposal } from "../runtime/index.mjs";
import { baseRuntimeFixture } from "./runtime-fixtures.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), workRoot = path.join(moduleRoot, ".rpg03-work", "runtime-store-tests");
const options = (fixture) => ({ cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup });
const code = (report) => report.diagnostics[0]?.code;
const checkpointPath = (root, id = "session.fixture") => path.join(root, `session-${id}.json`);
async function clean(name) { await fsp.mkdir(workRoot, { recursive: true }); return fsp.mkdtemp(path.join(workRoot, `${name}-`)); }
async function writeCanonical(file, value) { await fsp.writeFile(file, `${canonicalJson(value).value}\n`, "utf8"); }
function checkpointWrapper(session, formatVersion = "0.1.0") { const canonical = canonicalJson(session); return { format: "modelmirror.ai-rpg.runtime-checkpoint", formatVersion, sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, sessionSha256: sha256(canonical.value), session }; }

test("file store serializes creation, enforces CAS, preserves bytes on rejection, and excludes concurrent owners", async () => {
  const root = await clean("cas"), fixture = baseRuntimeFixture(), opened = await openFileSessionStore({ rootDirectory: root }); assert.equal(opened.valid, true); const store = opened.value;
  assert.equal(code(await openFileSessionStore({ rootDirectory: root })), "RUNTIME_STORE_OWNER_ACTIVE");
  assert.equal((await store.write(fixture.session, { ...options(fixture), expectedRevision: null })).valid, true); const original = await fsp.readFile(checkpointPath(root));
  const next = structuredClone(fixture.session); next.revision = 1;
  assert.equal(code(await store.write(next, { ...options(fixture), expectedRevision: 9 })), "RUNTIME_STORE_REVISION_CONFLICT"); assert.deepEqual(await fsp.readFile(checkpointPath(root)), original);
  assert.equal((await store.write(next, { ...options(fixture), expectedRevision: 0 })).valid, true); assert.equal((await store.read(next.sessionId, options(fixture))).value.revision, 1); assert.equal((await store.close()).valid, true);
});

test("queued read and write use synchronous caller-input snapshots", async () => {
  const root = await clean("snapshots"), fixture = baseRuntimeFixture(), store = (await openFileSessionStore({ rootDirectory: root })).value; assert.equal((await store.write(fixture.session, { ...options(fixture), expectedRevision: null })).valid, true);
  const next = structuredClone(fixture.session); next.revision = 1; const card = structuredClone(fixture.cardPackage), player = structuredClone(fixture.playerSetup), writing = store.write(next, { expectedRevision: 0, cardPackage: card, playerSetup: player }); next.revision = 99; card.package.displayName = "mutated"; player.displayName = "mutated";
  const written = await writing; assert.equal(written.valid, true); assert.equal(written.value.revision, 1);
  const readCard = structuredClone(fixture.cardPackage), reading = store.read(fixture.session.sessionId, { cardPackage: readCard, playerSetup: fixture.playerSetup }); readCard.package.displayName = "mutated again"; assert.equal((await reading).value.revision, 1); assert.equal((await store.close()).valid, true);
});

test("simultaneous opens elect exactly one owner", async () => {
  const root = await clean("concurrent-open"), reports = await Promise.all([openFileSessionStore({ rootDirectory: root }), openFileSessionStore({ rootDirectory: root })]), winners = reports.filter((entry) => entry.valid), losers = reports.filter((entry) => !entry.valid);
  assert.equal(winners.length, 1); assert.equal(losers.length, 1); assert.equal(["RUNTIME_STORE_CLAIMED", "RUNTIME_STORE_OWNER_ACTIVE"].includes(code(losers[0])), true); assert.equal((await winners[0].value.close()).valid, true);
});

test("session filename prefix safely preserves contract-valid Windows reserved logical IDs", async () => {
  const root = await clean("reserved-id"), fixture = baseRuntimeFixture(); fixture.session.sessionId = "con"; const store = (await openFileSessionStore({ rootDirectory: root })).value; assert.equal((await store.write(fixture.session, { ...options(fixture), expectedRevision: null })).valid, true); assert.equal((await store.read("con", options(fixture))).value.sessionId, "con"); assert.equal(fs.existsSync(path.join(root, "session-con.json")), true); await store.close();
});

test("checkpoint parser rejects corrupt bytes, unknown wrappers, duplicate JSON, resource drift, and hardlinks", async () => {
  const root = await clean("invalid"), fixture = baseRuntimeFixture(), store = (await openFileSessionStore({ rootDirectory: root })).value, file = checkpointPath(root);
  await fsp.writeFile(file, Buffer.from([0xff])); assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID");
  await fsp.writeFile(file, Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from('{}\n')])); assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID");
  { const handle = await fsp.open(file, "w"); await handle.truncate(16 * 1024 * 1024 + 1); await handle.close(); } assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_TOO_LARGE");
  await fsp.writeFile(file, '{"format":"x","format":"y"}\n'); assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID");
  const wrapper = checkpointWrapper(fixture.session, "9.0.0"); await writeCanonical(file, wrapper); assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID");
  wrapper.formatVersion = "0.1.0"; wrapper.session.resources.cardPackage.sha256 = "0".repeat(64); wrapper.cardPackageSha256 = wrapper.session.resources.cardPackage.sha256; wrapper.sessionSha256 = sha256(canonicalJson(wrapper.session).value); await writeCanonical(file, wrapper); assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_SESSION_INVALID");
  await fsp.rm(file); const other = path.join(root, "other.json"); await fsp.writeFile(other, "{}\n"); await fsp.link(other, file); assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID"); await fsp.rm(file); await fsp.rm(other);
  try { await fsp.symlink(path.join(root, "missing-target"), file, process.platform === "win32" ? "junction" : "file"); } catch (error) { assert.fail(`dangling link fixture unavailable: ${error?.code}`); } assert.equal(code(await store.read(fixture.session.sessionId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID"); await store.close();
});

test("ownership loss after durable temp write preserves original checkpoint and failed temp evidence", async () => {
  const root = await clean("ownership-loss"), fixture = baseRuntimeFixture(), store = (await openFileSessionStore({ rootDirectory: root })).value; assert.equal((await store.write(fixture.session, { ...options(fixture), expectedRevision: null })).valid, true); const original = await fsp.readFile(checkpointPath(root));
  const next = structuredClone(fixture.session); next.revision = 1;
  const probe = await fsp.open(path.join(root, "sync-probe"), "wx"), prototype = Object.getPrototypeOf(probe), originalSync = prototype.sync; await probe.close(); await fsp.unlink(path.join(root, "sync-probe")); let intercepted = 0;
  const syncMock = mock.method(prototype, "sync", async function () { await originalSync.call(this); intercepted += 1; await writeCanonical(path.join(root, ".owner.lock"), { pid: process.pid, token: "0".repeat(32) }); });
  try { const report = await store.write(next, { ...options(fixture), expectedRevision: 0 }); assert.equal(code(report), "RUNTIME_STORE_OWNERSHIP_LOST"); } finally { syncMock.mock.restore(); }
  assert.equal(intercepted, 1); assert.deepEqual(await fsp.readFile(checkpointPath(root)), original); assert.equal((await fsp.readdir(root)).some((name) => name.endsWith(".tmp")), true); assert.equal(code(await store.close()), "RUNTIME_STORE_OWNERSHIP_LOST");
});

test("session digest and wrapper identity reject canonical payload tampering", async () => {
  const root = await clean("session-digest"), fixture = baseRuntimeFixture(), store = (await openFileSessionStore({ rootDirectory: root })).value, session = fixture.session, requestedId = session.sessionId; session.revision = 1; session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "d".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "active", requestRevision: 0, startedRevision: 1, draftText: "before" }); const wrapper = checkpointWrapper(session); wrapper.session.generations[0].draftText = "tampered"; await writeCanonical(checkpointPath(root), wrapper); assert.equal(code(await store.read(requestedId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID");
  wrapper.sessionSha256 = sha256(canonicalJson(wrapper.session).value); wrapper.session.sessionId = "session.other"; wrapper.sessionSha256 = sha256(canonicalJson(wrapper.session).value); await writeCanonical(checkpointPath(root), wrapper); assert.equal(code(await store.read(requestedId, options(fixture))), "RUNTIME_STORE_CHECKPOINT_INVALID"); await store.close();
});

test("claim and unknown owner locks fail closed without deleting unknown evidence", async () => {
  const claimRoot = await clean("claim"); await fsp.writeFile(path.join(claimRoot, ".claim.lock"), "evidence"); assert.equal(code(await openFileSessionStore({ rootDirectory: claimRoot })), "RUNTIME_STORE_CLAIMED"); assert.equal(await fsp.readFile(path.join(claimRoot, ".claim.lock"), "utf8"), "evidence");
  const ownerRoot = await clean("unknown-owner"); await fsp.writeFile(path.join(ownerRoot, ".owner.lock"), "unknown\n"); assert.equal(code(await openFileSessionStore({ rootDirectory: ownerRoot })), "RUNTIME_STORE_OWNER_UNKNOWN"); assert.equal(await fsp.readFile(path.join(ownerRoot, ".owner.lock"), "utf8"), "unknown\n");
});

test("file and directory links are refused", async () => {
  const root = await clean("links"), target = path.join(root, "target"); await fsp.mkdir(target); const linked = path.join(root, "linked");
  try { await fsp.symlink(target, linked, process.platform === "win32" ? "junction" : "dir"); } catch (error) { assert.fail(`link fixture unavailable: ${error?.code}`); }
  assert.equal(code(await openFileSessionStore({ rootDirectory: linked })), "RUNTIME_STORE_ROOT_UNSAFE");
});

test("pending recovery increments only session revision and preserves receipt bytes", () => {
  const fixture = baseRuntimeFixture(), proposal = { narrative: "Quiet", suggestedActions: [], informationModules: [], stateProposals: [], uncertainties: [] }, exchange = validateModelProposal(proposal, "exchange.1", { kind: "action", text: "wait" }, fixture.cardPackage).value, receipt = { format: "modelmirror.ai-rpg.generation-receipt", formatVersion: "0.1.0", sessionId: fixture.session.sessionId, cardPackageSha256: fixture.session.resources.cardPackage.sha256, playerSetupSha256: fixture.session.resources.playerSetup.sha256, generationId: "generation.1", exchangeId: "exchange.1", revision: 2, evidenceKind: "mock", status: "succeeded", outcome: "completed", requestedModel: "provider/model", observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, outputSha256: computeProposalSha256(proposal, sha256).value, usage: { input: null, output: null, total: null }, costUsd: null };
  fixture.session.revision = 2; fixture.session.pending = { generationId: "generation.1", exchangeId: "exchange.1" }; fixture.session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "c".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "pending", requestRevision: 0, startedRevision: 1, finishedRevision: 2, draftText: "", exchange, receipt }); const before = structuredClone(fixture.session);
  const recovered = recoverSession(fixture.session, fixture.cardPackage, fixture.playerSetup, sha256); assert.equal(recovered.valid, true); assert.equal(recovered.value.revision, 3); assert.deepEqual(recovered.value.generations[0], before.generations[0]); assert.deepEqual(fixture.session, before);
});

test("recovery rejects revision overflow without mutation", () => {
  const fixture = baseRuntimeFixture(); fixture.session.revision = Number.MAX_SAFE_INTEGER; const before = structuredClone(fixture.session), result = recoverSession(fixture.session, fixture.cardPackage, fixture.playerSetup, sha256); assert.equal(code(result), "RUNTIME_RECOVERY_REVISION_OVERFLOW"); assert.deepEqual(fixture.session, before);
});

test("recovery requires resources and a synchronous hash function", () => {
  const fixture = baseRuntimeFixture(); assert.equal(code(recoverSession(fixture.session, null, fixture.playerSetup, sha256)), "RUNTIME_RECOVERY_ARGUMENT"); assert.equal(code(recoverSession(fixture.session, fixture.cardPackage, fixture.playerSetup, null)), "RUNTIME_RECOVERY_ARGUMENT"); assert.equal(recoverSession(fixture.session, fixture.cardPackage, fixture.playerSetup, async () => "a".repeat(64)).valid, false);
});

test("dead child owner is archived and committed state recovers with active generation interrupted without replay", async () => {
  const root = await clean("crash"), child = spawnSync(process.execPath, [path.join(moduleRoot, "tests", "runtime-store-child.mjs"), root], { cwd: moduleRoot, encoding: "utf8", windowsHide: true }); assert.equal(child.status, 0, child.stderr);
  const fixture = baseRuntimeFixture(), opened = await openFileSessionStore({ rootDirectory: root }); assert.equal(opened.valid, true); const store = opened.value, loaded = await store.read(fixture.session.sessionId, options(fixture)); assert.equal(loaded.valid, true); assert.equal(loaded.value.revision, 4); assert.equal(loaded.value.turns.length, 1); assert.equal(loaded.value.state.find((entry) => entry.fieldRef === "state.scene-note").value, "quiet");
  const recovered = recoverSession(loaded.value, fixture.cardPackage, fixture.playerSetup, sha256); assert.equal(recovered.valid, true); assert.equal(recovered.value.revision, 5); assert.equal(recovered.value.turns.length, 1); const interrupted = recovered.value.generations[1]; assert.equal(interrupted.status, "interrupted"); assert.equal(interrupted.finishedRevision, 5); assert.equal(interrupted.modelId, "provider/model"); assert.equal(interrupted.evidenceKind, "mock"); assert.equal(interrupted.draftText, "partial draft"); assert.equal(interrupted.receipt.outputSha256, null); assert.deepEqual(interrupted.receipt.usage, { input: null, output: null, total: null });
  assert.equal((await store.write(recovered.value, { ...options(fixture), expectedRevision: 4 })).valid, true); assert.equal((await store.close()).valid, true); assert.equal((await fsp.readdir(root)).some((name) => name.startsWith(".owner.dead-")), true);
});

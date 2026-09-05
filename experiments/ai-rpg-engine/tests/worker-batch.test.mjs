import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { canonicalJson, sha256 } from "../tooling/bundle.mjs";
import { runWorkerBatchCli } from "../tooling/worker-batch.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), batches = path.join(root, ".rpg02-work", "worker-batches"), fixture = path.join(root, "fixtures", "skill-generalization"); let serial = 0;
const sourceUrl = "https://afengy.cash/zh/explore/installed/e23bbc64-4fdd-46d8-92c0-64923961e5d8";
function assignment(jobId, worlds) { return { format: "modelmirror.ai-rpg.worker-assignment", formatVersion: "0.1.0", jobId, owner: "test worker", sourceUrl, authorizationRef: "user:authorized-test", capturedDate: "2026-09-05", worlds }; }
function envelope(job, key, capture) { return { format: "modelmirror.ai-rpg.worker-envelope", formatVersion: "0.1.0", jobId: job.jobId, resourceKey: key, assignmentSha256: sha256(Buffer.from(canonicalJson(job))), observation: { sourceUrl, openingTitle: capture.openingTitle, sourceCharacters: capture.sourceCharacters, start: capture.start, end: capture.end, raw: capture.raw, rawUtf8Bytes: capture.rawUtf8Bytes, rawSha256: capture.rawSha256, dataSha256: capture.dataSha256, rereadMatched: true, sourceIdentity: "visible_opening_iframe_srcdoc" }, producerVersion: "0.1.0", readCount: 2, diagnostics: [] }; }
async function setup(t, captures) { const jobId = "worker-test-" + process.pid + "-" + ++serial, worlds = captures.map(([key, capture]) => ({ key, name: capture.name })), value = assignment(jobId, worlds), assignments = path.join(batches, "assignments"); await fs.mkdir(assignments, { recursive: true }); const file = path.join(assignments, jobId + ".json"); await fs.writeFile(file, canonicalJson(value), { flag: "wx" }); const initialized = await runWorkerBatchCli(["init", "--assignment", file]); assert.equal(initialized.valid, true, JSON.stringify(initialized)); const jobRoot = path.join(batches, "jobs", jobId); t.after(async () => { const resolved = path.resolve(jobRoot); assert.equal(resolved.startsWith(path.resolve(batches) + path.sep), true); await fs.rm(resolved, { recursive: true, force: true }); await fs.unlink(file).catch(() => {}); }); return { jobId, value, jobRoot, file }; }
const readCapture = async (name) => JSON.parse(await fs.readFile(path.join(fixture, name), "utf8"));

test("init, resumable ingest, idempotent replay, finalize, and audit are file-derived", async (t) => {
  const gu = await readCapture("gu-world.capture.json"), cyber = await readCapture("cyberpunk-world.capture.json"), job = await setup(t, [["gu", gu], ["cyber", cyber]]);
  const resumed = await runWorkerBatchCli(["init", "--assignment", job.file]); assert.equal(resumed.valid, true); assert.equal(resumed.value.resumed, true);
  let status = await runWorkerBatchCli(["status", "--job", job.jobId]); assert.deepEqual(status.value.pending, ["gu", "cyber"]); assert.equal(status.value.jobRoot.startsWith(root), true);
  const firstEnvelope = envelope(job.value, "gu", gu), first = await runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin: JSON.stringify(firstEnvelope) }); assert.equal(first.valid, true, JSON.stringify(first)); assert.equal(first.value.idempotent, false);
  const replay = await runWorkerBatchCli(["ingest", "--job", job.jobId, "--base64"], { stdin: Buffer.from(JSON.stringify(firstEnvelope)).toString("base64") }); assert.equal(replay.valid, true); assert.equal(replay.value.idempotent, true);
  status = await runWorkerBatchCli(["status", "--job", job.jobId]); assert.deepEqual(status.value.completed, ["gu"]); assert.deepEqual(status.value.pending, ["cyber"]);
  assert.equal((await runWorkerBatchCli(["finalize", "--job", job.jobId])).valid, false);
  assert.equal((await runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin: JSON.stringify(envelope(job.value, "cyber", cyber)) })).valid, true);
  const finalized = await runWorkerBatchCli(["finalize", "--job", job.jobId]); assert.equal(finalized.valid, true); assert.equal(finalized.value.worlds, 2); assert.equal((await runWorkerBatchCli(["audit", "--job", job.jobId])).valid, true);
  const receiptPath = path.join(job.jobRoot, "receipt.json"), originalReceipt = await fs.readFile(receiptPath); const retried = await runWorkerBatchCli(["finalize", "--job", job.jobId]); assert.equal(retried.valid, true); assert.equal(retried.value.idempotent, true); assert.equal(sha256(await fs.readFile(receiptPath)), sha256(originalReceipt));
  const receipt = JSON.parse(originalReceipt); receipt.events += 1; await fs.writeFile(receiptPath, canonicalJson(receipt)); assert.equal((await runWorkerBatchCli(["audit", "--job", job.jobId])).diagnostics[0].code, "WORKER_RECEIPT_DRIFT");
});

test("binding, title, source, schema, and changed replay failures leave immutable history", async (t) => {
  const capture = await readCapture("gu-world.capture.json"), job = await setup(t, [["gu", capture]]), valid = envelope(job.value, "gu", capture);
  const invalid = structuredClone(valid); invalid.execute = true; assert.equal((await runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin: JSON.stringify(invalid) })).valid, false);
  assert.equal((await runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin: JSON.stringify(valid) })).diagnostics[0].code, "WORKER_JOB_STOPPED");
  const events = await fs.readdir(path.join(job.jobRoot, "events")); assert.equal(events.length, 2);
});

test("unexpected files, incomplete transactions, links, and held job locks fail closed", async (t) => {
  const capture = await readCapture("genshin-world.capture.json"), job = await setup(t, [["genshin", capture]]);
  await fs.writeFile(path.join(job.jobRoot, "unexpected.txt"), "x", { flag: "wx" }); assert.equal((await runWorkerBatchCli(["audit", "--job", job.jobId])).diagnostics[0].code, "WORKER_UNRECORDED_FILE"); await fs.unlink(path.join(job.jobRoot, "unexpected.txt"));
  await fs.writeFile(path.join(job.jobRoot, "outputs", "unrecorded.json"), "{}", { flag: "wx" }); assert.equal((await runWorkerBatchCli(["audit", "--job", job.jobId])).diagnostics[0].code, "WORKER_UNRECORDED_FILE"); await fs.unlink(path.join(job.jobRoot, "outputs", "unrecorded.json"));
  await fs.writeFile(path.join(job.jobRoot, ".txn"), "x", { flag: "wx" }); assert.equal((await runWorkerBatchCli(["status", "--job", job.jobId])).diagnostics[0].code, "WORKER_INCOMPLETE_TRANSACTION"); await fs.unlink(path.join(job.jobRoot, ".txn"));
  await fs.writeFile(path.join(job.jobRoot, ".lock"), "x", { flag: "wx" }); assert.equal((await runWorkerBatchCli(["status", "--job", job.jobId])).diagnostics[0].code, "WORKER_JOB_LOCKED"); await fs.unlink(path.join(job.jobRoot, ".lock"));
  const link = path.join(job.jobRoot, "outputs", "linked.json"); try { await fs.symlink(path.join(job.jobRoot, "assignment.json"), link, "file"); assert.equal((await runWorkerBatchCli(["audit", "--job", job.jobId])).valid, false); } catch (error) { if (error?.code !== "EPERM") throw error; t.diagnostic("Windows link creation unavailable (EPERM); link rejection branch was not exercised on this host"); }
});

test("separate job IDs remain isolated and stop codes are recorded without source text", async (t) => {
  const capture = await readCapture("cyberpunk-world.capture.json"), left = await setup(t, [["cyber", capture]]), right = await setup(t, [["cyber", capture]]);
  assert.notEqual(left.jobRoot, right.jobRoot); const stopped = await runWorkerBatchCli(["stop", "--job", left.jobId, "--code", "DOM_DRIFT"]); assert.equal(stopped.valid, false); assert.equal(stopped.diagnostics[0].code, "DOM_DRIFT");
  assert.deepEqual((await runWorkerBatchCli(["status", "--job", right.jobId])).value.pending, ["cyber"]);
});

test("invalid calendar dates and malformed stdin fail with stable recorded diagnostics", async (t) => {
  const capture = await readCapture("gu-world.capture.json"), jobId = "worker-test-invalid-" + process.pid + "-" + ++serial, invalid = assignment(jobId, [{ key: "gu", name: capture.name }]); invalid.capturedDate = "2026-02-30"; const inputs = path.join(batches, "assignments"), file = path.join(inputs, jobId + ".json"); await fs.mkdir(inputs, { recursive: true }); await fs.writeFile(file, canonicalJson(invalid), { flag: "wx" }); t.after(() => fs.unlink(file).catch(() => {})); assert.equal((await runWorkerBatchCli(["init", "--assignment", file])).diagnostics[0].code, "WORKER_ASSIGNMENT_DATE");
  const validJob = await setup(t, [["gu", capture]]), before = (await fs.readdir(path.join(validJob.jobRoot, "events"))).length; assert.equal((await runWorkerBatchCli(["ingest", "--job", validJob.jobId], { stdin: "{broken" })).diagnostics[0].code, "WORKER_ENVELOPE_JSON"); assert.equal((await fs.readdir(path.join(validJob.jobRoot, "events"))).length, before + 1);
});

test("title, assignment hash, and capture separation drift each stop an independent job", async (t) => {
  const capture = await readCapture("gu-world.capture.json");
  for (const mutate of [(v) => { v.observation.openingTitle = "drift"; }, (v) => { v.assignmentSha256 = "0".repeat(64); }]) { const job = await setup(t, [["gu", capture]]), value = envelope(job.value, "gu", capture); mutate(value); assert.equal((await runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin: JSON.stringify(value) })).valid, false); assert.equal((await runWorkerBatchCli(["status", "--job", job.jobId])).value.terminal, true); }
  const job = await setup(t, [["gu", capture]]), value = envelope(job.value, "gu", capture); assert.equal((await runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin: JSON.stringify(value) })).valid, true); const capturePath = path.join(job.jobRoot, "captures", "gu.json"), changed = JSON.parse(await fs.readFile(capturePath, "utf8")); changed.raw += " "; await fs.writeFile(capturePath, canonicalJson(changed)); assert.equal((await runWorkerBatchCli(["audit", "--job", job.jobId])).diagnostics[0].code, "WORKER_ARTIFACT_DRIFT");
});

test("simultaneous ingests serialize through the exclusive job lock", async (t) => {
  const capture = await readCapture("cyberpunk-world.capture.json"), job = await setup(t, [["cyber", capture]]), stdin = JSON.stringify(envelope(job.value, "cyber", capture)); const reports = await Promise.all([runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin }), runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin })]); assert.equal(reports.filter((report) => report.valid).length, 1); assert.equal(reports.some((report) => report.diagnostics[0]?.code === "WORKER_JOB_LOCKED"), true);
});

test("Windows directory junction is rejected before status or ingest can write through it", async (t) => {
  const capture = await readCapture("gu-world.capture.json"), job = await setup(t, [["gu", capture]]), outputs = path.resolve(job.jobRoot, "outputs"), saved = path.resolve(batches, "test-junction-saved", job.jobId), target = path.resolve(batches, "test-junction-targets", job.jobId), boundary = path.resolve(batches) + path.sep;
  for (const candidate of [outputs, saved, target]) assert.equal(candidate.startsWith(boundary), true);
  assert.equal((await runWorkerBatchCli(["status", "--job", job.jobId])).valid, true);
  await fs.mkdir(path.dirname(saved), { recursive: true }); await fs.mkdir(target, { recursive: true });
  t.after(async () => { const outputStat = await fs.lstat(outputs).catch(() => null); if (outputStat?.isSymbolicLink()) await fs.rmdir(outputs); const savedStat = await fs.lstat(saved).catch(() => null); if (savedStat?.isDirectory()) await fs.rename(saved, outputs); for (const candidate of [saved, target]) { const resolved = path.resolve(candidate); assert.equal(resolved.startsWith(boundary), true); await fs.rm(resolved, { recursive: true, force: true }); } });
  await fs.rename(outputs, saved);
  try { await fs.symlink(target, outputs, "junction"); } catch (error) { assert.fail("directory junction creation must be available for this Windows gate: " + String(error?.code ?? "unknown")); }
  const before = await fs.readdir(target), status = await runWorkerBatchCli(["status", "--job", job.jobId]); assert.equal(status.valid, false); assert.equal(["WORKER_PATH_UNSAFE", "WORKER_UNRECORDED_FILE"].includes(status.diagnostics[0].code), true);
  const attempted = await runWorkerBatchCli(["ingest", "--job", job.jobId], { stdin: JSON.stringify(envelope(job.value, "gu", capture)) }); assert.equal(attempted.valid, false); assert.deepEqual(await fs.readdir(target), before);
  await fs.rmdir(outputs); await fs.rename(saved, outputs); assert.equal((await runWorkerBatchCli(["status", "--job", job.jobId])).valid, true);
});

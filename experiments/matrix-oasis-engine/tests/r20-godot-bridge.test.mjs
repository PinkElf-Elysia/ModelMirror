import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { appendWorldEventLedgerEntryCore, hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validateWorldEventLedgerJson } from "@matrix-oasis/npc-authority-contracts";
import { createNpcAuthoritySession, restoreNpcAuthoritySession, verifyNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { auditGodotBoundary } from "../scripts/check-godot-boundary.mjs";
import { summarizeR20GodotEvents } from "../scripts/lib/r20-host-core.mjs";
import { assertR20FinalizedQualificationIdentity, auditR20PreviewQualificationBasis, calculateR20ImplementationIdentity, createRetryableR20WriterRelease, releaseR20WriterWithRetry, resumeR20EmptyQualifierTimeline, resumeR20QualifiedPendingCurrent, validateR20QualifierRecoveryIdentity, waitForR20GodotCloseAfterTrace } from "../scripts/qualify-r20-npc-bridge.mjs";

const actorSource = await readFile(new URL("../apps/runtime-godot/npc_authority_prototype/npc_actor_controller.gd", import.meta.url), "utf8");
const bridgeSource = await readFile(new URL("../apps/runtime-godot/npc_authority_prototype/npc_authority_lab.gd", import.meta.url), "utf8");
const projectSource = await readFile(new URL("../apps/runtime-godot/project.godot", import.meta.url), "utf8");
const p1ProbeSource = await readFile(new URL("../apps/runtime-godot/npc_authority_prototype/npc_p1_probe.gd", import.meta.url), "utf8");
const godotImportSource = await readFile(new URL("../scripts/verify-r20-godot-import.mjs", import.meta.url), "utf8");
const qualificationSource = await readFile(new URL("../scripts/qualify-r20-npc-bridge.mjs", import.meta.url), "utf8");
const previewSource = await readFile(new URL("../scripts/preview-r20.mjs", import.meta.url), "utf8");
const hostSource = await readFile(new URL("../scripts/lib/r20-host-core.mjs", import.meta.url), "utf8");
const moduleRoot = fileURLToPath(new URL("..", import.meta.url));

const sha256 = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const fakeSha256 = (character) => `sha256:${character.repeat(64)}`;

async function previewQualificationBasisFixture(t) {
  const npcRunRoot = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-preview-basis-"));
  t.after(() => rm(npcRunRoot, { recursive: true, force: true }));
  const timelineId = "timeline-qualified-preview-basis";
  const implementationSha256 = fakeSha256("1");
  const godotBinarySha256 = fakeSha256("2");
  const headSha256 = fakeSha256("3");
  const manifestJson = canonicalizeJsonValue({ timelineId, identities: { implementationSha256, godotBinarySha256 } });
  const manifestSha256 = sha256(manifestJson);
  const manifestId = manifestSha256.slice(7);
  const worldEventLedgerJson = canonicalizeJsonValue({ timeline: { id: timelineId }, revision: 1, headSha256, entries: [] });
  const processLogUtf8 = "Godot Engine v4.6.3.stable.official\n";
  const processLogSha256 = sha256(processLogUtf8);
  const runtimeProjectManifestJson = canonicalizeJsonValue({format:"matrix-oasis.r20-runtime-project-manifest",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",files:[{path:"project.godot",byteLength:1,sha256:fakeSha256("9")} ]});
  const runtimeProjectSha256 = sha256(runtimeProjectManifestJson);
  const qualificationReceiptJson = canonicalizeJsonValue({ manifestSha256, timelineId, revision: 1, headSha256, implementationSha256, godotBinarySha256, processLogSha256, runtimeProjectSha256 });
  const qualificationReceiptSha256 = sha256(qualificationReceiptJson);
  const runtimeGamePackJson=canonicalizeJsonValue({fixture:"runtime-pack"}),runtimeReceiptJson=canonicalizeJsonValue({fixture:"runtime-receipt"}),policyJson=canonicalizeJsonValue({fixture:"policy"}),activationJson=canonicalizeJsonValue({}),activatedJson=canonicalizeJsonValue({});
  const evidenceJson = canonicalizeJsonValue({format:"matrix-oasis.r20-qualification-evidence",formatVersion:"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",processLogUtf8, processLogSha256, runtimeProjectManifestJson, runtimeProjectSha256, qualificationReceiptJson, qualificationReceiptSha256,activationJson,activatedJson,runtimeGamePackJson,runtimeGamePackSha256:sha256(runtimeGamePackJson),runtimeReceiptJson,runtimeReceiptSha256:sha256(runtimeReceiptJson),authorityPolicyJson:policyJson,authorityPolicySha256:sha256(policyJson)});
  const timelineRoot = path.join(npcRunRoot, "timelines", manifestId);
  await mkdir(timelineRoot, { recursive: true });
  await writeFile(path.join(timelineRoot, "authority-manifest.json"), manifestJson, "utf8");
  await writeFile(path.join(timelineRoot, "qualification-evidence.json"), evidenceJson, "utf8");
  await writeFile(path.join(timelineRoot, "world-event-ledger.json"), worldEventLedgerJson, "utf8");
  const current = Object.freeze({ manifestSha256, timelineId, revision: 1, headSha256, qualificationReceiptSha256 });
  const audit = Object.freeze({ ok: true, current, timelines: Object.freeze([Object.freeze({ manifestId, timelineId, revision: 1, headSha256, qualificationReceiptSha256, implementationSha256, godotBinarySha256, qualified: true, status: "qualified" })]) });
  const session = Object.freeze(Object.create(null));
  const calls = { audits: [], reads: [], restores: [], verifies: [] };
  const operations = {
    auditTimelineStore: async (request) => { calls.audits.push(request); return audit; },
    readStableFile: async (file) => { calls.reads.push(file); return readFile(file); },
    restoreAuthoritySession: async (request) => { calls.restores.push(request); return Object.freeze({ ok: true, session, canonicalWorldEventLedgerJson: worldEventLedgerJson }); },
    verifyAuthoritySession: async (received) => {
      calls.verifies.push(received);
      return Object.freeze({ ok: true, canonicalWorldEventLedgerJson: worldEventLedgerJson, fullReplayCount: 2, canonicalWorldEventLedgerReplayReportJson: canonicalizeJsonValue({ timelineId, ledgerSha256: sha256(worldEventLedgerJson), throughRevision: 1, throughHeadSha256: headSha256 }) });
    },
  };
  const request = Object.freeze({ npcRunRoot, temporaryRoot: tmpdir(), writerLease: Object.freeze(Object.create(null)), expectedImplementationSha256: implementationSha256, expectedGodotBinarySha256: godotBinarySha256, manifestFor: () => manifestJson, runtimeGamePackJson, runtimeReceiptJson, policyJson });
  return Object.freeze({ timelineRoot, manifestSha256, timelineId, implementationSha256, godotBinarySha256, worldEventLedgerJson, evidenceJson, audit, operations, request, calls, session });
}

async function auditBridgeFixture(source, relativePath = "npc_authority_prototype/npc_authority_lab.gd") {
  const root = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-godot-boundary-"));
  try {
    const target = path.join(root, ...relativePath.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, source, "utf8");
    return auditGodotBoundary({ root });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function hasViolation(report, code) {
  return report.violations.some((violation) => violation.code === code);
}

test("R20 bridge is the sole exact Godot loopback exception", () => {
  const report = auditGodotBoundary();
  assert.equal(report.ok, true, JSON.stringify(report.violations));
  assert.match(bridgeSource, /http:\/\/127\.0\.0\.1:43120\/v1\//u);
  assert.doesNotMatch(bridgeSource, /https:\/\//u);
  assert.doesNotMatch(bridgeSource, /\b(?:WebSocket|StreamPeerTCP|PacketPeerUDP|ENetMultiplayerPeer|TCPServer)\b/u);
  assert.equal([...bridgeSource.matchAll(/OS\.get_environment\(/gu)].length, 1);
});

test("R20 reset is physical R, idle-only, and applies locally only after an HTTP 200", () => {
  const resetMappingStart = projectSource.indexOf("reset_session={");
  const physicsStart = projectSource.indexOf("[physics]", resetMappingStart);
  const resetMapping = projectSource.slice(resetMappingStart, physicsStart);
  assert.ok(resetMappingStart >= 0 && physicsStart > resetMappingStart);
  assert.match(resetMapping, /physical_keycode":82/u);

  const inputStart = bridgeSource.indexOf("func _unhandled_input");
  const processStart = bridgeSource.indexOf("func _process", inputStart);
  const inputBlock = bridgeSource.slice(inputStart, processStart);
  const physicalResetIndex = inputBlock.indexOf('event.is_action_pressed(&"reset_session")');
  const idleIndex = inputBlock.indexOf("_active_command.is_empty() and _pending_route.is_empty()");
  const postIndex = inputBlock.indexOf('_post("reset", {})');
  assert.ok(inputStart >= 0 && processStart > inputStart);
  assert.ok(physicalResetIndex >= 0 && idleIndex > physicalResetIndex && postIndex > idleIndex);

  const responseStart = bridgeSource.indexOf("func _on_request_completed");
  const commandHandlerStart = bridgeSource.indexOf("func _accept_command_response", responseStart);
  const responseBlock = bridgeSource.slice(responseStart, commandHandlerStart);
  const responseGateIndex = responseBlock.indexOf("response_code != 200");
  const resetDispatchIndex = responseBlock.indexOf('"reset": _accept_reset_response(response)');
  assert.ok(responseStart >= 0 && commandHandlerStart > responseStart);
  assert.ok(responseGateIndex >= 0 && resetDispatchIndex > responseGateIndex);

  const resetStart = bridgeSource.indexOf("func _accept_reset_response");
  const verificationStart = bridgeSource.indexOf("func _submit_verification", resetStart);
  const resetBlock = bridgeSource.slice(resetStart, verificationStart);
  const statusIndex = resetBlock.indexOf('response.get("status") != "reset"');
  const runtimeResetIndex = resetBlock.indexOf("_scene_lab._reset_session()");
  const actorHomeIndex = resetBlock.indexOf("actor.hide_at_home()");
  const collisionIndex = resetBlock.indexOf("actor.refresh_visibility_collision()");
  const actorClearIndex = resetBlock.indexOf("_active_actor = null");
  const commandClearIndex = resetBlock.indexOf("_active_command = {}");
  const traceIndex = resetBlock.indexOf("_trace.clear()");
  const performanceIndex = resetBlock.indexOf("_frame_micros.clear()");
  const verificationIndex = resetBlock.indexOf("_verification_started = false");
  const commandIndex = resetBlock.indexOf("_request_command()");
  assert.ok(resetStart >= 0 && verificationStart > resetStart);
  assert.ok(statusIndex >= 0 && runtimeResetIndex > statusIndex);
  assert.ok(actorHomeIndex > runtimeResetIndex && collisionIndex > actorHomeIndex);
  assert.ok(actorClearIndex > collisionIndex && commandClearIndex > actorClearIndex);
  assert.ok(traceIndex > commandClearIndex && performanceIndex > traceIndex);
  assert.ok(verificationIndex > performanceIndex && commandIndex > verificationIndex);
});

test("R20 Godot boundary rejects HTTPS, another port, a second environment read, and another path", async () => {
  const httpsReport = await auditBridgeFixture(bridgeSource.replace(
    "http://127.0.0.1:43120/v1/",
    "https://127.0.0.1:43120/v1/",
  ));
  assert.equal(hasViolation(httpsReport, "GODOT_FIRST_PARTY_NETWORK"), true);

  const otherPortReport = await auditBridgeFixture(bridgeSource.replace(
    "http://127.0.0.1:43120/v1/",
    "http://127.0.0.1:43121/v1/",
  ));
  assert.equal(hasViolation(otherPortReport, "GODOT_FIRST_PARTY_NETWORK"), true);

  const secondEnvironmentReport = await auditBridgeFixture(
    `${bridgeSource}\nvar _forbidden_second_environment := OS.get_environment("SECOND_ENV")\n`,
  );
  assert.equal(hasViolation(secondEnvironmentReport, "GODOT_FIRST_PARTY_ENVIRONMENT"), true);

  const otherPathReport = await auditBridgeFixture(
    bridgeSource,
    "unapproved_bridge/npc_authority_lab.gd",
  );
  assert.equal(hasViolation(otherPathReport, "GODOT_FIRST_PARTY_NETWORK"), true);
  assert.equal(hasViolation(otherPathReport, "GODOT_FIRST_PARTY_ENVIRONMENT"), true);
});

test("NPC movement follows the locked 60 Hz profile without avoidance or teleport fallback", () => {
  assert.match(actorSource, /const PHYSICS_TICKS_PER_SECOND := 60/u);
  assert.match(actorSource, /const SPEED_PER_TICK := 0\.05/u);
  assert.match(actorSource, /const TURN_RADIANS_PER_TICK := deg_to_rad\(3\.0\)/u);
  assert.match(actorSource, /const MOVEMENT_TICK_LIMIT := 1800/u);
  assert.match(actorSource, /const MAXIMUM_PATH_LENGTH := 100\.0/u);
  assert.match(actorSource, /_agent\.get_next_path_position\(\)/u);
  assert.match(actorSource, /move_and_slide\(\)/u);
  assert.match(actorSource, /_agent\.avoidance_enabled = false/u);
  assert.doesNotMatch(actorSource, /global_position\s*=\s*target/u);
  assert.match(actorSource, /home_transform\s*=\s*Transform3D\(placement_transform\.basis\.orthonormalized\(\), placement_transform\.origin\)/u);
  assert.match(actorSource, /placement\.reparent\(self, true\)/u);
});

test("authority is submitted only after four physics arrival proofs and mirror hash agreement", () => {
  const arrivalIndex = bridgeSource.indexOf('_post("arrived", request_body)');
  const moveIndex = bridgeSource.indexOf("actor.begin_move(_floor_anchors[anchor_id])");
  const actionIndex = bridgeSource.indexOf("_scene_lab._apply_action(_active_command");
  const mirrorIndex = bridgeSource.indexOf('_post("mirror"');
  assert.ok(moveIndex >= 0 && arrivalIndex > moveIndex && actionIndex > arrivalIndex && mirrorIndex > actionIndex);
  for (const field of ["pathComplete", "floorVerified", "capsuleVerified", "domainVerified"]) {
    assert.ok(actorSource.includes(`\"${field}\"`));
  }
  assert.match(bridgeSource, /before != response\.get\("beforeSnapshotSha256"\)/u);
  assert.match(bridgeSource, /after != response\.get\("afterSnapshotSha256"\)/u);
  assert.match(bridgeSource, /_scene_lab\.interaction_ray\.enabled = false/u);
  assert.match(bridgeSource, /FileAccess\.get_sha256\(BINDING_PATH\)/u);
  assert.match(bridgeSource, /_entity_binding_sha256 = "sha256:" \+ binding_hash\.to_lower\(\)/u);
  assert.match(bridgeSource, /"entityBindingSha256": _entity_binding_sha256/u);
});

test("Godot 4.6 HTTP and navigation boundaries preserve canonical R20 semantics", () => {
  assert.doesNotMatch(bridgeSource, /func _get\(/u);
  assert.match(bridgeSource, /revision_is_integer: bool/u);
  assert.match(bridgeSource, /revision_value == floor\(revision_value\)/u);
  assert.match(bridgeSource, /revision_value > 10000/u);
  assert.doesNotMatch(bridgeSource, /domain_ids\.has\(anchor_id\)/u);
  assert.match(bridgeSource, /domain_ids\[anchor_id\] = true/u);
});

test("full spatial qualification keeps Forward+ instead of the dummy headless renderer", () => {
  assert.doesNotMatch(qualificationSource, /if\(headless\)args\.unshift\("--headless"\)/u);
  assert.match(qualificationSource, /r14GodotArguments\(\{projectRoot:project\.projectRoot,runDirectory,smoke:false\}\)/u);
  assert.match(qualificationSource, /windowsHide:headless/u);
});

test("R20 qualification mode is an owned closed overlay artifact and cannot leak into frozen R14 user arguments", () => {
  assert.doesNotMatch(qualificationSource, /args\.push\("--matrix-oasis-r20-qualification"\)/u);
  assert.match(qualificationSource, /qualification-request\.json"\),QUALIFICATION_REQUEST_JSON,\{flag:"wx"\}/u);
  assert.doesNotMatch(bridgeSource, /QUALIFICATION_ARGUMENT/u);
  assert.match(bridgeSource, /FileAccess\.open\("res:\/\/npc_authority_prototype\/qualification-request\.json", FileAccess\.READ\)/u);
  assert.match(bridgeSource, /_qualification_mode = qualification_file != null/u);
  assert.match(bridgeSource, /_exact\(qualification_request, \["format", "formatVersion"\]\)/u);
  assert.match(bridgeSource, /matrix-oasis\.r20-qualification-request/u);
});

test("product performance uses wall-clock frames and interactive preview has no qualification timeout", () => {
  assert.match(bridgeSource, /Time\.get_ticks_usec\(\)/u);
  assert.match(bridgeSource, /now - _previous_frame_usec/u);
  assert.doesNotMatch(bridgeSource, /delta \* 1000000\.0/u);
  assert.match(qualificationSource, /const tracePromise=waitForTrace\?new Promise/u);
  assert.doesNotMatch(qualificationSource, /const tracePromise=new Promise/u);
});

test("P1 physics and performance guards are wired to an official-Godot executable probe", () => {
  assert.match(actorSource, /vertical_distance <= ARRIVAL_TOLERANCE/u);
  assert.match(actorSource, /verified_floor_position\.y - _target_floor_position\.y/u);
  assert.match(actorSource, /verified_floor_normal\.normalized\(\)\.dot\(Vector3\.UP\)/u);
  assert.match(actorSource, /closest\.distance_to\(global_position\) <= ARRIVAL_TOLERANCE/u);

  const validationIndex = bridgeSource.indexOf("if not _arrival_evidence_valid(evidence)");
  const returningIndex = bridgeSource.indexOf("if actor.is_returning()");
  const samplingIndex = bridgeSource.indexOf("_performance_sampling = true");
  const movementIndex = bridgeSource.indexOf("actor.begin_move(_floor_anchors[anchor_id])");
  const cycleIndex = bridgeSource.indexOf("_performance_cycle_complete = true");
  const mirrorIndex = bridgeSource.indexOf("func _accept_mirror_response");
  assert.ok(validationIndex >= 0 && returningIndex > validationIndex);
  assert.ok(samplingIndex >= 0 && movementIndex > samplingIndex);
  assert.ok(mirrorIndex >= 0 && cycleIndex > mirrorIndex);

  assert.match(p1ProbeSource, /R20_NPC_P1_STACKED_FLOOR_WRONG_Y_ACCEPTED/u);
  assert.match(p1ProbeSource, /R20_NPC_P1_STACKED_DOMAIN_WRONG_Y_ACCEPTED/u);
  assert.match(p1ProbeSource, /R20_NPC_P1_RETURN_FALSE_EVIDENCE_ACCEPTED/u);
  assert.match(p1ProbeSource, /R20_NPC_P1_PHYSICS_ROOT_SCALE_INHERITED/u);
  assert.match(p1ProbeSource, /R20_NPC_P1_VISUAL_TRANSFORM_CHANGED/u);
  assert.match(p1ProbeSource, /frames\.append\(10000 if index < 240 else 50000\)/u);
  assert.match(p1ProbeSource, /selected\.front\(\) != frames\.front\(\)/u);
  assert.match(p1ProbeSource, /selected\.back\(\) != frames\.back\(\)/u);
  assert.match(p1ProbeSource, /median_micros != 50000 or median_fps_milli >= 30000/u);
  assert.match(godotImportSource, /res:\/\/npc_authority_prototype\/npc_p1_probe\.gd/u);
  assert.match(godotImportSource, /R20_NPC_P1_PROBE_OK/u);
});

test("official Godot 4.6.3 rejects stacked-floor wrong-Y and return false evidence", {
  skip: typeof process.env.GODOT_BIN !== "string" || process.env.GODOT_BIN.length === 0,
}, () => {
  const result = spawnSync(process.execPath, [path.join(moduleRoot, "scripts", "verify-r20-godot-import.mjs")], {
    cwd: moduleRoot,
    encoding: "utf8",
    env: process.env,
    windowsHide: true,
    timeout: 180_000,
    maxBuffer: 16 * 1024 * 1024,
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const output = `${result.stdout}\n${result.stderr}`;
  assert.match(output, /R20_NPC_P1_PROBE_OK version=4\.6\.3/u);
  assert.match(output, /R20_GODOT_IMPORT_OK version=4\.6\.3/u);
});

test("Godot submits a bounded event digest while Node derives the authoritative event sequence", () => {
  assert.match(bridgeSource, /_trace\.size\(\) > 20000/u);
  assert.match(bridgeSource, /"eventCount": _trace\.size\(\)/u);
  assert.match(bridgeSource, /"eventsSha256": "sha256:" \+ JSON\.stringify\(_trace, "", true\)\.sha256_text\(\)/u);
  assert.doesNotMatch(bridgeSource, /"events": _trace/u);
  assert.match(bridgeSource, /"sequence": int\(command\["sequence"\]\)/u);
  assert.match(bridgeSource, /"sequence": int\(_active_command\["sequence"\]\)/u);
  assert.match(bridgeSource, /"movementTicks": int\(_active_arrival_evidence\["movementTicks"\]\)/u);
  assert.match(bridgeSource, /"pathLengthMm": int\(_active_arrival_evidence\["pathLengthMm"\]\)/u);
  assert.match(hostSource, /summarizeR20GodotEvents\(commands\)/u);
  assert.match(hostSource, /terminal\.status!=="quiescent"&&terminal\.status!=="ended"/u);

  const sha = (character) => `sha256:${character.repeat(64)}`;
  const command = (index) => ({
    sequence: index + 1,
    actorEntityId: `actor-${index % 64}`,
    actionId: `action-${index}`,
    state: index % 2 === 0 ? "accepted" : "rejected",
    arrivalEvidence: {
      pathComplete: true,
      floorVerified: true,
      capsuleVerified: true,
      domainVerified: true,
      movementTicks: index % 1801,
      pathLengthMm: index % 100001,
    },
    mirrorEvidence: {
      beforeSnapshotSha256: sha("a"),
      afterSnapshotSha256: sha("b"),
    },
  });
  const first = command(0);
  const firstEvents = [{
    sequence: first.sequence,
    actorEntityId: first.actorEntityId,
    actionId: first.actionId,
    state: "arrived",
    arrivalEvidence: first.arrivalEvidence,
  }, {
    sequence: first.sequence,
    actorEntityId: first.actorEntityId,
    actionId: first.actionId,
    state: "mirrored",
    decision: first.state,
    beforeSnapshotSha256: first.mirrorEvidence.beforeSnapshotSha256,
    afterSnapshotSha256: first.mirrorEvidence.afterSnapshotSha256,
  }];
  assert.deepEqual(summarizeR20GodotEvents([first]), {
    eventCount: 2,
    eventsSha256: hashCanonicalValue(firstEvents),
  });

  const payloadBytes = (count) => Buffer.byteLength(canonicalizeJsonValue({
    traceVersion: 1,
    entityBindingSha256: sha("c"),
    navigationSynchronized: true,
    renderer: "forward_plus",
    ...summarizeR20GodotEvents(Array.from({ length: count }, (_, index) => command(index))),
    performance: { sampleCount: 300, medianFrameMicros: 16667, medianFpsMilli: 59998 },
  }));
  const sixtyFourBytes = payloadBytes(64);
  const maximumBytes = payloadBytes(10_000);
  assert.ok(sixtyFourBytes < 32 * 1024);
  assert.ok(maximumBytes < 32 * 1024);
  assert.ok(maximumBytes - sixtyFourBytes < 16);
});

test("qualification fails closed when a valid trace is followed by a child that never closes", async () => {
  assert.match(qualificationSource, /waitForR20GodotCloseAfterTrace\(\{closePromise:childClosePromise,terminate:\(\)=>child\.kill\(\)\}\)/u);
  assert.doesNotMatch(qualificationSource, /observed=await tracePromise,closed=await childClosePromise/u);

  const ordinaryClose = Object.freeze({ code: 0, signal: null, output: "ok", error: null });
  assert.equal(await waitForR20GodotCloseAfterTrace({
    closePromise: Promise.resolve(ordinaryClose),
    terminate: () => assert.fail("an already closed child must not be terminated"),
    closeTimeoutMs: 10,
    terminationWaitMs: 10,
  }), ordinaryClose);

  let resolveClose;
  let terminationCount = 0;
  const closePromise = new Promise((resolve) => { resolveClose = resolve; });
  await assert.rejects(waitForR20GodotCloseAfterTrace({
    closePromise,
    terminate: () => {
      terminationCount += 1;
      resolveClose(Object.freeze({ code: null, signal: "SIGTERM", output: "ok", error: null }));
    },
    closeTimeoutMs: 1,
    terminationWaitMs: 25,
  }), /R20_GODOT_CLOSE_TIMEOUT/u);
  assert.equal(terminationCount, 1);
});

test("manual R20 preview rejects a missing qualified current before replay or launch", async (t) => {
  const fixture = await previewQualificationBasisFixture(t);
  let auditRequest = null;
  const operations = {
    ...fixture.operations,
    auditTimelineStore: async (request) => {
      auditRequest = request;
      throw new Error("R20_STORE_IMPLEMENTATION_IDENTITY_INVALID");
    },
  };
  await assert.rejects(auditR20PreviewQualificationBasis(fixture.request, operations), /R20_PREVIEW_QUALIFICATION_REQUIRED/u);
  assert.equal(auditRequest.expectedImplementationSha256, fixture.implementationSha256);
  assert.equal(auditRequest.expectedGodotBinarySha256, fixture.godotBinarySha256);
  assert.equal(fixture.calls.restores.length, 0);
  assert.equal(fixture.calls.verifies.length, 0);
});

test("manual R20 preview preserves a stale current implementation or Godot identity failure", async (t) => {
  const fixture = await previewQualificationBasisFixture(t);
  const operations = {
    ...fixture.operations,
    auditTimelineStore: async (request) => {
      assert.equal(request.expectedImplementationSha256, fixture.implementationSha256);
      assert.equal(request.expectedGodotBinarySha256, fixture.godotBinarySha256);
      throw new Error("R20_QUALIFICATION_RECEIPT_INVALID");
    },
  };
  await assert.rejects(auditR20PreviewQualificationBasis(fixture.request, operations), /R20_QUALIFICATION_RECEIPT_INVALID/u);
  assert.equal(fixture.calls.reads.length, 0);
  assert.equal(fixture.calls.restores.length, 0);
});

test("manual R20 preview rejects exact-read qualification evidence drift", async (t) => {
  const fixture = await previewQualificationBasisFixture(t);
  const stale = JSON.parse(fixture.evidenceJson);
  stale.processLogUtf8 += "stale\n";
  await writeFile(path.join(fixture.timelineRoot, "qualification-evidence.json"), canonicalizeJsonValue(stale), "utf8");
  await assert.rejects(auditR20PreviewQualificationBasis(fixture.request, fixture.operations), /R20_PREVIEW_QUALIFICATION_BASIS_INVALID/u);
  assert.equal(fixture.calls.restores.length, 0);
  assert.equal(fixture.calls.verifies.length, 0);
});

test("manual R20 preview fails closed when the qualified ledger cannot be fully replayed", async (t) => {
  const fixture = await previewQualificationBasisFixture(t);
  const operations = {
    ...fixture.operations,
    restoreAuthoritySession: async () => Object.freeze({ ok: false, diagnostics: Object.freeze([{ code: "WORLD_EVENT_LEDGER_REPLAY_DECISION_MISMATCH" }]) }),
    verifyAuthoritySession: async () => assert.fail("verification must not run after restore replay fails"),
  };
  await assert.rejects(auditR20PreviewQualificationBasis(fixture.request, operations), /R20_PREVIEW_QUALIFICATION_REPLAY_INVALID/u);
});

test("manual R20 preview is attributed only to an exact audited and replayed current profile", async (t) => {
  const fixture = await previewQualificationBasisFixture(t);
  const result = await auditR20PreviewQualificationBasis(fixture.request, fixture.operations);
  assert.equal(result.qualificationBasisManifestSha256, fixture.manifestSha256);
  assert.equal(result.qualificationBasisTimelineId, fixture.timelineId);
  assert.equal(result.qualificationBasisRevision, 1);
  assert.equal(result.timelineCount, 1);
  assert.equal(fixture.calls.audits.length, 2);
  assert.equal(fixture.calls.reads.length, 6);
  assert.equal(fixture.calls.restores.length, 1);
  assert.equal(fixture.calls.restores[0].worldEventLedgerJson, fixture.worldEventLedgerJson);
  assert.equal(fixture.calls.verifies.length, 1);
  assert.equal(fixture.calls.verifies[0], fixture.session);
  for (const request of fixture.calls.audits) {
    assert.equal(request.expectedImplementationSha256, fixture.implementationSha256);
    assert.equal(request.expectedGodotBinarySha256, fixture.godotBinarySha256);
    assert.equal(request.writerLease, fixture.request.writerLease);
  }
  assert.match(qualificationSource, /if\(!waitForTrace\)qualificationBasis=await auditR20PreviewQualificationBasis/u);
  assert.match(qualificationSource, /qualificationStatus:"unqualified-observation"/u);
  assert.match(previewSource, /qualificationBasisManifestSha256=\$\{result\.qualificationBasisManifestSha256\}/u);
});

test("R20 qualifier binds every resumable timeline to the current exact authority manifest", async (t) => {
  const npcRunRoot = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-qualifier-recovery-"));
  t.after(() => rm(npcRunRoot, { recursive: true, force: true }));
  const timelineId = "timeline-recovery-fixture";
  const authorityManifestJson = canonicalizeJsonValue({
    format: "matrix-oasis.npc-authority-manifest",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    timelineId,
    sourceRunId: "source-fixture",
    identities: { implementationSha256: `sha256:${"1".repeat(64)}`, godotBinarySha256: `sha256:${"2".repeat(64)}` },
  });
  const manifestId = createHash("sha256").update(authorityManifestJson).digest("hex");
  const timelineRoot = path.join(npcRunRoot, "timelines", manifestId);
  await mkdir(timelineRoot, { recursive: true });
  await writeFile(path.join(timelineRoot, "authority-manifest.json"), authorityManifestJson, "utf8");
  const emptyTimeline = Object.freeze({ manifestId, timelineRoot, timelineId, authorityManifestJson, status: "empty-timeline" });
  const recovery = Object.freeze({ recovered: null, emptyTimeline, evidencePending: Object.freeze([]), qualificationPending: Object.freeze([]) });
  const verified = await validateR20QualifierRecoveryIdentity({ recovery, npcRunRoot, manifestFor: () => authorityManifestJson });
  assert.deepEqual(verified.map(({ manifestId: id, status }) => ({ manifestId: id, status })), [{ manifestId, status: "empty-timeline" }]);

  await assert.rejects(validateR20QualifierRecoveryIdentity({
    recovery,
    npcRunRoot,
    manifestFor: () => canonicalizeJsonValue({ ...JSON.parse(authorityManifestJson), sourceRunId: "different-source" }),
  }), /R20_STORE_RECOVERY_IDENTITY_MISMATCH/u);
});

test("R20 qualifier resumes an exact empty timeline and leaves genesis append enabled", async () => {
  const authorityManifestJson = canonicalizeJsonValue({ timelineId: "timeline-empty-resume" });
  const emptyTimeline = Object.freeze({ authorityManifestJson, status: "empty-timeline" });
  const writerLease = Object.freeze(Object.create(null));
  const authoritySession = Object.freeze({ ok: true, session: Object.freeze({ id: "session" }) });
  const timelineStore = Object.freeze({ append: async () => undefined });
  let sessionRequest = null;
  let storeRequest = null;
  const resumed = await resumeR20EmptyQualifierTimeline({
    emptyTimeline,
    manifestFor: () => authorityManifestJson,
    runtimeGamePackJson: "runtime-pack",
    runtimeReceiptJson: "runtime-receipt",
    policyJson: "policy",
    npcRunRoot: "npc-root",
    temporaryRoot: "temporary-root",
    behaviorPolicyJson: "behavior-policy",
    entityBindingJson: "entity-bindings",
    writerLease,
  }, {
    createNpcAuthoritySession: async (request) => { sessionRequest = request; return authoritySession; },
    resumeR20EmptyTimelineStore: async (request) => { storeRequest = request; return timelineStore; },
  });
  assert.equal(sessionRequest.timelineId, "timeline-empty-resume");
  assert.equal(storeRequest.recovery, emptyTimeline);
  assert.equal(storeRequest.writerLease, writerLease);
  assert.equal(resumed.session, authoritySession);
  assert.equal(resumed.store, timelineStore);
  assert.equal(resumed.resumedTimeline, false);
  assert.match(qualificationSource, /if\(!resumedTimeline\)await store\.append\(exportR20Coordinator\(coordinator\)\)/u);
});

async function semanticForgeryFixture() {
  const authoringGamePackJson = canonicalizeJsonValue({
    format: "matrix-oasis.authoring-game-pack",
    formatVersion: "0.1.0",
    id: "r20-pending-replay-forgery",
    contentVersion: "1",
    language: "en",
    title: "Pending replay forgery",
    entryNodeId: "node-alpha",
    entities: [{ id: "actor-unit", label: "Actor", description: "Actor" }],
    variables: [],
    cues: [],
    nodes: [
      { id: "node-alpha", title: "Alpha", entityIds: ["actor-unit"], entryCueIds: [], actions: [{ id: "action-forward", label: "Forward", entityIds: [], effects: [], target: { kind: "node", id: "node-beta" } }] },
      { id: "node-beta", title: "Beta", entityIds: ["actor-unit"], entryCueIds: [], actions: [{ id: "action-finish", label: "Finish", entityIds: [], effects: [], target: { kind: "ending", id: "ending-complete" } }] },
    ],
    endings: [{ id: "ending-complete", title: "Complete", cueIds: [] }],
  });
  const compiled = await compileAuthoringGamePackJson(authoringGamePackJson);
  assert.equal(compiled.ok, true, JSON.stringify(compiled.diagnostics));
  const runtime = compiled.runtimePack;
  const runtimeGamePackJson = compiled.canonicalJson;
  const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
  const policyJson = canonicalizeJsonValue({
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "r20-pending-replay-policy",
    contentVersion: "1",
    runtime: {
      format: runtime.format,
      formatVersion: runtime.formatVersion,
      id: runtime.source.id,
      contentVersion: runtime.source.contentVersion,
      sourceSha256: `sha256:${runtime.source.canonicalSha256}`,
      artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
      receiptSha256: hashCanonicalValue(compiled.receipt),
    },
    actorGrants: [{ actorEntityId: "actor-unit", grants: [{ nodeId: "node-alpha", actionId: "action-forward" }] }],
  });
  const timelineId = "timeline-semantic-forgery";
  const created = await createNpcAuthoritySession({ runtimeGamePackJson, runtimeReceiptJson, policyJson, timelineId, stepLimit: 10 });
  assert.equal(created.ok, true, JSON.stringify(created.diagnostics));
  const initialSnapshotSha256 = JSON.parse(created.canonicalWorldEventLedgerJson).authority.initialSnapshotSha256;
  const npcIntentJson = canonicalizeJsonValue({
    format: "matrix-oasis.npc-intent",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "intent-forged-target",
    actorEntityId: "actor-unit",
    timelineId,
    nodeId: "node-alpha",
    actionId: "action-forward",
    observed: { revision: 0, headSha256: null, runtimeSnapshotSha256: initialSnapshotSha256 },
  });
  const appended = appendWorldEventLedgerEntryCore({
    worldEventLedgerJson: created.canonicalWorldEventLedgerJson,
    npcIntentJson,
    decision: { status: "accepted", reason: "NPC_INTENT_ACCEPTED" },
    beforeSnapshotSha256: initialSnapshotSha256,
    afterSnapshotSha256: fakeSha256("b"),
    transition: {
      transitionVersion: 1,
      step: 1,
      from: { kind: "node", index: 0, id: "node-alpha" },
      actionId: "action-forward",
      // The real Runtime target is node-beta. The Ledger contract and hash chain
      // are valid, but only an authoritative Runtime replay can reject this target.
      to: { kind: "ending", index: 0, id: "ending-complete" },
      emittedCues: [],
    },
  });
  assert.equal(appended.ok, true, JSON.stringify(appended.diagnostics));
  assert.equal(validateWorldEventLedgerJson(appended.canonicalWorldEventLedgerJson).valid, true);
  const semanticReplay = await restoreNpcAuthoritySession({ runtimeGamePackJson, runtimeReceiptJson, policyJson, worldEventLedgerJson: appended.canonicalWorldEventLedgerJson });
  assert.equal(semanticReplay.ok, false);
  assert.equal(semanticReplay.diagnostics[0].code, "WORLD_EVENT_LEDGER_REPLAY_TRANSITION_MISMATCH");
  return Object.freeze({
    timelineId,
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson,
    worldEventLedgerJson: appended.canonicalWorldEventLedgerJson,
    revision: 1,
    headSha256: JSON.parse(appended.canonicalWorldEventLedgerJson).headSha256,
  });
}

test("R20 qualifier resumes only an identity-bound qualified-pending-current publication", async (t) => {
  const npcRunRoot = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-qualified-pending-"));
  t.after(() => rm(npcRunRoot, { recursive: true, force: true }));
  const timelineId = "timeline-qualified-pending";
  const implementationSha256 = `sha256:${"3".repeat(64)}`;
  const godotBinarySha256 = `sha256:${"4".repeat(64)}`;
  const authorityManifestJson = canonicalizeJsonValue({ timelineId, identities: { implementationSha256, godotBinarySha256 } });
  const manifestId = createHash("sha256").update(authorityManifestJson).digest("hex");
  const timelineRoot = path.join(npcRunRoot, "timelines", manifestId);
  await mkdir(timelineRoot, { recursive: true });
  await writeFile(path.join(timelineRoot, "authority-manifest.json"), authorityManifestJson, "utf8");
  const headSha256 = `sha256:${"8".repeat(64)}`;
  const worldEventLedgerJson = canonicalizeJsonValue({ timeline: { id: timelineId }, revision: 1, headSha256 });
  await writeFile(path.join(timelineRoot, "world-event-ledger.json"), worldEventLedgerJson, "utf8");
  const traceJson = canonicalizeJsonValue({
    traceVersion: 1,
    entityBindingSha256: `sha256:${"5".repeat(64)}`,
    navigationSynchronized: true,
    renderer: "forward_plus",
    eventCount: 2,
    eventsSha256: `sha256:${"6".repeat(64)}`,
    performance: { sampleCount: 300, medianFrameMicros: 16667, medianFpsMilli: 59998 },
  });
  await writeFile(path.join(timelineRoot, "godot-trace.json"), traceJson, "utf8");
  const processLogUtf8 = "Godot Engine v4.6.3.stable.official\n";
  const processLogSha256 = `sha256:${createHash("sha256").update(processLogUtf8).digest("hex")}`;
  const runtimeProjectManifestJson=canonicalizeJsonValue({format:"matrix-oasis.r20-runtime-project-manifest",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",files:[{path:"project.godot",byteLength:1,sha256:fakeSha256("7")}]}),runtimeProjectSha256=sha256(runtimeProjectManifestJson),runtimeGamePackJson=canonicalizeJsonValue({fixture:"runtime-pack"}),runtimeReceiptJson=canonicalizeJsonValue({fixture:"runtime-receipt"}),policyJson=canonicalizeJsonValue({fixture:"policy"});
  const qualificationReceiptJson = canonicalizeJsonValue({ godotTraceSha256: `sha256:${createHash("sha256").update(traceJson).digest("hex")}`, implementationSha256, godotBinarySha256 });
  const qualificationReceiptSha256 = `sha256:${createHash("sha256").update(qualificationReceiptJson).digest("hex")}`;
  const evidenceJson = canonicalizeJsonValue({format:"matrix-oasis.r20-qualification-evidence",formatVersion:"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",processLogUtf8,processLogSha256,runtimeProjectManifestJson,runtimeProjectSha256,qualificationReceiptJson,qualificationReceiptSha256,activationJson:canonicalizeJsonValue({}),activatedJson:canonicalizeJsonValue({}),runtimeGamePackJson,runtimeGamePackSha256:sha256(runtimeGamePackJson),runtimeReceiptJson,runtimeReceiptSha256:sha256(runtimeReceiptJson),authorityPolicyJson:policyJson,authorityPolicySha256:sha256(policyJson)});
  await writeFile(path.join(timelineRoot, "qualification-evidence.json"), evidenceJson, "utf8");
  const qualificationPending = Object.freeze({ manifestId, timelineId, revision: 1, headSha256, status: "qualified-pending-current", qualificationReceiptSha256 });
  const current = canonicalizeJsonValue({ format: "matrix-oasis.npc-current", formatVersion: "0.1.0", manifestSha256: `sha256:${manifestId}`, timelineId, revision: 1, headSha256, qualificationReceiptSha256 });
  const currentSha256 = sha256(current);
  const activationIdentity = Object.freeze({ manifestId, manifestSha256: `sha256:${manifestId}`, timelineId, revision: 1, headSha256, qualificationReceiptSha256, current, currentSha256, authorityManifestSha256: sha256(authorityManifestJson), checkpointSha256: fakeSha256("1"), worldEventLedgerSha256: sha256(worldEventLedgerJson), behaviorTraceSha256: fakeSha256("2"), qualificationEvidenceSha256: sha256(evidenceJson) });
  let resumed = 0;
  let activated = 0;
  let released = 0;
  const replayEvents = [];
  const replaySession = Object.freeze(Object.create(null));
  const operations = {
    readStableFile: async (file) => readFile(file),
    publishProcessLog: async (bytes) => ({ path: "recovered.log", sha256: `sha256:${createHash("sha256").update(bytes).digest("hex")}` }),
    restoreAuthoritySession: async (request) => {
      replayEvents.push("restore");
      assert.equal(request.runtimeGamePackJson, runtimeGamePackJson);
      assert.equal(request.runtimeReceiptJson, runtimeReceiptJson);
      assert.equal(request.policyJson, policyJson);
      assert.equal(request.worldEventLedgerJson, worldEventLedgerJson);
      return Object.freeze({ ok: true, session: replaySession, canonicalWorldEventLedgerJson: worldEventLedgerJson });
    },
    verifyAuthoritySession: async (session) => {
      replayEvents.push("verify");
      assert.equal(session, replaySession);
      return Object.freeze({
        ok: true,
        fullReplayCount: 2,
        canonicalWorldEventLedgerJson: worldEventLedgerJson,
        canonicalWorldEventLedgerReplayReportJson: canonicalizeJsonValue({ timelineId, ledgerSha256: sha256(worldEventLedgerJson), throughRevision: 1, throughHeadSha256: headSha256 }),
      });
    },
    resumeQualificationPublication: async (request) => {
      resumed += 1;
      assert.equal(request.qualificationPending, qualificationPending);
      assert.equal(request.expectedImplementationSha256, implementationSha256);
      assert.equal(request.expectedGodotBinarySha256, godotBinarySha256);
      return Object.freeze({
        timelineRoot,
        manifestId,
        timelineId,
        revision: 1,
        headSha256,
        current,
        currentSha256,
        activationIdentity,
        persistedProcessLogSha256: processLogSha256,
        persistedRuntimeProjectSha256: runtimeProjectSha256,
        qualificationReceiptJson,
        qualificationReceiptSha256,
        activateQualification: async (options) => {
          assert.deepEqual(options.expectedActivationIdentity, activationIdentity);
          await options.verifyBeforePublish({ identity: activationIdentity, worldEventLedgerJson });
          replayEvents.push("activate"); activated += 1;
          await writeFile(path.join(npcRunRoot, "npc-current.json"), current, "utf8");
          await options.verifyAfterPublish({ identity: activationIdentity, worldEventLedgerJson });
          return Object.freeze({ manifestId, timelineRoot, timelineId, revision: 1, headSha256, current, currentSha256, qualificationReceiptSha256, activationIdentity, prepared: true, qualified: true });
        },
      });
    },
  };
  const base = {
    qualificationPending,
    manifestFor: () => authorityManifestJson,
    npcRunRoot,
    temporaryRoot: tmpdir(),
    writerLease: Object.freeze(Object.create(null)),
    releaseWriter: async () => { released += 1; },
    expectedImplementationSha256: implementationSha256,
    expectedGodotBinarySha256: godotBinarySha256,
    sourceRunId: "source",
    qualificationRunId: "qualification",
    bindingCount: 2,
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson,
  };
  await assert.rejects(resumeR20QualifiedPendingCurrent({ ...base, waitForTrace: false }, operations), /R20_QUALIFICATION_RESUME_REQUIRES_QUALIFICATION/u);
  assert.equal(resumed, 0);
  const result = await resumeR20QualifiedPendingCurrent({ ...base, waitForTrace: true }, operations);
  assert.equal(result.recoveredPublication, true);
  assert.equal(result.trace.performance.medianFpsMilli, 59998);
  assert.equal(result.published.qualified, true);
  assert.equal(resumed, 1);
  assert.equal(activated, 1);
  assert.equal(released, 1);
  assert.deepEqual(replayEvents, ["restore", "verify", "restore", "verify", "activate", "restore", "verify"]);
  let persistentReleaseAttempts = 0;
  await assert.rejects(resumeR20QualifiedPendingCurrent({ ...base, waitForTrace: true, releaseWriter: async () => { persistentReleaseAttempts += 1; throw new Error("locked"); } }, operations), /R20_WRITER_RELEASE_FAILED/u);
  assert.equal(persistentReleaseAttempts, 2);
  assert.equal(activated, 2);
  assert.deepEqual(replayEvents, ["restore", "verify", "restore", "verify", "activate", "restore", "verify", "restore", "verify", "restore", "verify", "activate", "restore", "verify"]);
});

test("qualified-pending-current recovery rejects a schema-valid hash-chained Ledger whose transition disagrees with Runtime", async (t) => {
  const forged = await semanticForgeryFixture();
  const npcRunRoot = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-pending-forgery-"));
  t.after(() => rm(npcRunRoot, { recursive: true, force: true }));
  const implementationSha256 = fakeSha256("c");
  const godotBinarySha256 = fakeSha256("d");
  const authorityManifestJson = canonicalizeJsonValue({ timelineId: forged.timelineId, identities: { implementationSha256, godotBinarySha256 } });
  const manifestId = sha256(authorityManifestJson).slice(7);
  const timelineRoot = path.join(npcRunRoot, "timelines", manifestId);
  await mkdir(timelineRoot, { recursive: true });
  await writeFile(path.join(timelineRoot, "authority-manifest.json"), authorityManifestJson, "utf8");
  await writeFile(path.join(timelineRoot, "world-event-ledger.json"), forged.worldEventLedgerJson, "utf8");
  const processLogUtf8="Godot Engine v4.6.3.stable.official\n",runtimeProjectManifestJson=canonicalizeJsonValue({format:"matrix-oasis.r20-runtime-project-manifest",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",files:[{path:"project.godot",byteLength:1,sha256:fakeSha256("1")}]}),qualificationReceiptJson=canonicalizeJsonValue({}),evidenceJson=canonicalizeJsonValue({format:"matrix-oasis.r20-qualification-evidence",formatVersion:"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",processLogUtf8,processLogSha256:sha256(processLogUtf8),runtimeProjectManifestJson,runtimeProjectSha256:sha256(runtimeProjectManifestJson),qualificationReceiptJson,qualificationReceiptSha256:sha256(qualificationReceiptJson),activationJson:canonicalizeJsonValue({}),activatedJson:canonicalizeJsonValue({}),runtimeGamePackJson:forged.runtimeGamePackJson,runtimeGamePackSha256:sha256(forged.runtimeGamePackJson),runtimeReceiptJson:forged.runtimeReceiptJson,runtimeReceiptSha256:sha256(forged.runtimeReceiptJson),authorityPolicyJson:forged.policyJson,authorityPolicySha256:sha256(forged.policyJson)});
  await writeFile(path.join(timelineRoot,"qualification-evidence.json"),evidenceJson,"utf8");
  const qualificationPending = Object.freeze({
    manifestId,
    timelineId: forged.timelineId,
    revision: forged.revision,
    headSha256: forged.headSha256,
    status: "qualified-pending-current",
    qualificationReceiptSha256: fakeSha256("e"),
  });
  let activated = 0;
  const operations = {
    readStableFile: async (file) => readFile(file),
    publishProcessLog: async () => assert.fail("a replay failure must precede process-log publication"),
    restoreAuthoritySession: restoreNpcAuthoritySession,
    verifyAuthoritySession: verifyNpcAuthoritySession,
    resumeQualificationPublication: async () => Object.freeze({
      timelineRoot,
      manifestId,
      activateQualification: async () => { activated += 1; },
    }),
  };
  await assert.rejects(resumeR20QualifiedPendingCurrent({
    waitForTrace: true,
    qualificationPending,
    manifestFor: () => authorityManifestJson,
    npcRunRoot,
    temporaryRoot: tmpdir(),
    writerLease: Object.freeze(Object.create(null)),
    releaseWriter: async () => undefined,
    expectedImplementationSha256: implementationSha256,
    expectedGodotBinarySha256: godotBinarySha256,
    sourceRunId: "source",
    qualificationRunId: "qualification",
    bindingCount: 1,
    runtimeGamePackJson: forged.runtimeGamePackJson,
    runtimeReceiptJson: forged.runtimeReceiptJson,
    policyJson: forged.policyJson,
  }, operations), /R20_STORE_RECOVERY_REPLAY_INVALID/u);
  assert.equal(activated, 0);
});

test("qualification receipt input remains bound to the exact finalized manifest and Ledger tip", () => {
  const timelineId = "timeline-finalized-binding";
  const manifestJson = canonicalizeJsonValue({ timelineId });
  const manifestId = sha256(manifestJson).slice(7);
  const headSha256 = fakeSha256("f");
  const ledgerJson = canonicalizeJsonValue({ timeline: { id: timelineId }, revision: 3, headSha256 });
  const qualified = Object.freeze({ manifestId, timelineRoot: "unused", revision: 3, headSha256 });
  assert.doesNotThrow(() => assertR20FinalizedQualificationIdentity({ qualified, manifestJson, ledgerJson }));
  assert.throws(() => assertR20FinalizedQualificationIdentity({ qualified: { ...qualified, manifestId: "0".repeat(64) }, manifestJson, ledgerJson }), /R20_QUALIFICATION_FINALIZED_IDENTITY_MISMATCH/u);
  assert.throws(() => assertR20FinalizedQualificationIdentity({ qualified: { ...qualified, revision: 2 }, manifestJson, ledgerJson }), /R20_QUALIFICATION_FINALIZED_IDENTITY_MISMATCH/u);
  assert.throws(() => assertR20FinalizedQualificationIdentity({ qualified: { ...qualified, headSha256: fakeSha256("0") }, manifestJson, ledgerJson }), /R20_QUALIFICATION_FINALIZED_IDENTITY_MISMATCH/u);
});

test("R20 qualifier writer lease release retries after a transient failure", async () => {
  const writerLease = Object.freeze(Object.create(null));
  let attempts = 0;
  const release = createRetryableR20WriterRelease(writerLease, async (received) => {
    assert.equal(received, writerLease);
    attempts += 1;
    if (attempts === 1) throw new Error("transient-release-failure");
  });
  await assert.rejects(release.release(), /transient-release-failure/u);
  assert.equal(release.isReleased(), false);
  await release.release();
  await release.release();
  assert.equal(release.isReleased(), true);
  assert.equal(attempts, 2);
});

test("R20 writer release helper retries once and reports a persistent cleanup failure", async () => {
  let transientAttempts = 0;
  await releaseR20WriterWithRetry(async () => { transientAttempts += 1; if (transientAttempts === 1) throw new Error("transient"); });
  assert.equal(transientAttempts, 2);
  let persistentAttempts = 0;
  await assert.rejects(releaseR20WriterWithRetry(async () => { persistentAttempts += 1; throw new Error("persistent"); }), /R20_WRITER_RELEASE_FAILED/u);
  assert.equal(persistentAttempts, 2);
});

test("R20 implementation identity follows workspace exports and transitive first-party sources", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-implementation-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const packageRoot = path.join(root, "packages", "runtime");
  const externalRoot = path.join(root, "node_modules", "external-fixture");
  await mkdir(path.join(packageRoot, "src"), { recursive: true });
  await mkdir(externalRoot, { recursive: true });
  await writeFile(path.join(root, "package.json"), JSON.stringify({ private: true, type: "module", workspaces: ["packages/*"] }), "utf8");
  await writeFile(path.join(root, "package-lock.json"), JSON.stringify({ name: "fixture", lockfileVersion: 3 }), "utf8");
  await writeFile(path.join(root, "entry.mjs"), 'import "@fixture/runtime";\nimport "external-fixture";\n', "utf8");
  const manifestFile = path.join(packageRoot, "package.json");
  const manifest = { name: "@fixture/runtime", type: "module", exports: { ".": { import: "./src/index.mjs", default: "./src/index.mjs" } } };
  await writeFile(manifestFile, JSON.stringify(manifest), "utf8");
  await writeFile(path.join(packageRoot, "src", "index.mjs"), 'export { value } from "./transitive.mjs";\n', "utf8");
  await writeFile(path.join(packageRoot, "src", "transitive.mjs"), "export const value = 1;\n", "utf8");
  await writeFile(path.join(packageRoot, "src", "alternate.mjs"), "export const value = 3;\n", "utf8");
  await writeFile(path.join(externalRoot, "package.json"), JSON.stringify({ name: "external-fixture", type: "module", exports: "./index.mjs" }), "utf8");
  await writeFile(path.join(externalRoot, "index.mjs"), 'export { external } from "./second.mjs";\n', "utf8");
  await writeFile(path.join(externalRoot, "second.mjs"), "export const external = 1;\n", "utf8");
  const options = { entryFiles: ["entry.mjs"] };
  const initial = await calculateR20ImplementationIdentity(root, options);
  const initialFiles = JSON.parse(initial.manifestJson).files.map((file) => file.path);
  for (const required of ["package.json", "package-lock.json", "packages/runtime/package.json", "packages/runtime/src/transitive.mjs", "node_modules/external-fixture/package.json", "node_modules/external-fixture/index.mjs", "node_modules/external-fixture/second.mjs"]) assert.equal(initialFiles.includes(required), true, required);
  await writeFile(path.join(externalRoot, "second.mjs"), "export const external = 2;\n", "utf8");
  assert.notEqual((await calculateR20ImplementationIdentity(root, options)).sha256, initial.sha256);
  await writeFile(path.join(externalRoot, "second.mjs"), "export const external = 1;\n", "utf8");
  assert.equal((await calculateR20ImplementationIdentity(root, options)).sha256, initial.sha256);
  await writeFile(path.join(packageRoot, "src", "transitive.mjs"), "export const value = 2;\n", "utf8");
  assert.notEqual((await calculateR20ImplementationIdentity(root, options)).sha256, initial.sha256);
  await writeFile(path.join(packageRoot, "src", "transitive.mjs"), "export const value = 1;\n", "utf8");
  assert.equal((await calculateR20ImplementationIdentity(root, options)).sha256, initial.sha256);
  manifest.exports["."].import = "./src/alternate.mjs";
  manifest.exports["."].default = "./src/alternate.mjs";
  await writeFile(manifestFile, JSON.stringify(manifest), "utf8");
  const exportDrift = await calculateR20ImplementationIdentity(root, options);
  const exportFiles = JSON.parse(exportDrift.manifestJson).files.map((file) => file.path);
  assert.notEqual(exportDrift.sha256, initial.sha256);
  assert.equal(exportFiles.includes("packages/runtime/src/alternate.mjs"), true);
  assert.equal(exportFiles.includes("packages/runtime/src/transitive.mjs"), false);
});

test("R20 implementation identity binds lockfiles, simulator closure and resolved external entries", async () => {
  const manifest = JSON.parse((await calculateR20ImplementationIdentity()).manifestJson);
  const files = new Set(manifest.files.map((file) => file.path));
  for (const required of ["package.json", "package-lock.json", "packages/runtime-pack-contracts/package.json", "packages/runtime-pack-contracts/src/index.mjs", "packages/runtime-pack-contracts/schemas/0.1.0/runtime-game-pack.schema.json", "packages/runtime-pack-simulator/package.json", "packages/runtime-pack-simulator/src/session.mjs", "scripts/lib/runtime-evidence-cache-core.mjs", "scripts/preview-r20.mjs", "node_modules/ajv/package.json", "node_modules/ajv/dist/2020.js", "node_modules/ajv/dist/core.js", "node_modules/property-graph/package.json", "node_modules/property-graph/dist/index.mjs", "node_modules/playcanvas/package.json", "node_modules/playcanvas/build/playcanvas.js", "apps/runtime-godot/solved_spatial_prototype/spatial_solution_loader.gd", "apps/runtime-godot/solved_spatial_prototype/solved_spatial_lab.tscn", "apps/runtime-godot/npc_authority_prototype/npc_authority_lab.gd", "apps/runtime-godot/addons/gdgs/runtime/nodes/gaussian_splat_node.gd", "apps/runtime-godot/addons/gdgs/runtime/render/compute/gaussian_renderer.gd"]) assert.equal(files.has(required), true, required);
  assert.equal(manifest.files.some((file) => file.path==="apps/runtime-godot/.godot"||file.path.startsWith("apps/runtime-godot/.godot/")), false);
});

test("R20 implementation identity binds the complete copied Godot tree deterministically", async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-godot-tree-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const godotRoot = path.join(root, "apps", "runtime-godot");
  const r14Root = path.join(godotRoot, "solved_spatial_prototype");
  await mkdir(path.join(godotRoot, ".godot", "generated"), { recursive: true });
  await mkdir(r14Root, { recursive: true });
  await writeFile(path.join(root, "package.json"), JSON.stringify({ private: true, type: "module", workspaces: [] }), "utf8");
  await writeFile(path.join(root, "package-lock.json"), JSON.stringify({ name: "fixture", lockfileVersion: 3 }), "utf8");
  await writeFile(path.join(root, "entry.mjs"), "export const fixture = true;\n", "utf8");
  await writeFile(path.join(godotRoot, "project.godot"), "[application]\n", "utf8");
  await writeFile(path.join(r14Root, "spatial_solution_loader.gd"), "extends Node\n", "utf8");
  await writeFile(path.join(r14Root, "solved_spatial_lab.tscn"), "[gd_scene format=3]\n", "utf8");
  await writeFile(path.join(r14Root, "empty-resource.gd"), "", "utf8");
  await writeFile(path.join(godotRoot, ".godot", "generated", "ignored.cache"), "machine-local", "utf8");
  const options = { entryFiles: ["entry.mjs"], resourceTrees: ["apps/runtime-godot"] };
  const initial = await calculateR20ImplementationIdentity(root, options);
  const initialManifest = JSON.parse(initial.manifestJson);
  const paths = initialManifest.files.map((file) => file.path);
  assert.deepEqual(paths, [...paths].sort((left, right) => left.localeCompare(right)));
  assert.equal(paths.every((file) => !file.includes("\\")), true);
  assert.equal(paths.some((file) => file.includes("/.godot/")), false);
  const empty = initialManifest.files.find((file) => file.path==="apps/runtime-godot/solved_spatial_prototype/empty-resource.gd");
  assert.deepEqual(empty, { path: "apps/runtime-godot/solved_spatial_prototype/empty-resource.gd", sha256: sha256(""), byteLength: 0 });

  await writeFile(path.join(r14Root, "spatial_solution_loader.gd"), "extends Node\nconst DRIFT = true\n", "utf8");
  assert.notEqual((await calculateR20ImplementationIdentity(root, options)).sha256, initial.sha256);
  await writeFile(path.join(r14Root, "spatial_solution_loader.gd"), "extends Node\n", "utf8");
  assert.equal((await calculateR20ImplementationIdentity(root, options)).sha256, initial.sha256);
  await writeFile(path.join(r14Root, "solved_spatial_lab.tscn"), "[gd_scene format=3]\n[node name=\"Drift\" type=\"Node\"]\n", "utf8");
  assert.notEqual((await calculateR20ImplementationIdentity(root, options)).sha256, initial.sha256);
});

test("official Godot 4.6.3 canonicalizes parsed event integers to the same digest as Node", {
  skip: typeof process.env.GODOT_BIN !== "string" || process.env.GODOT_BIN.length === 0,
}, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r20-digest-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const command = {
    sequence: 1,
    actorEntityId: "actor-1",
    actionId: "action-1",
    state: "accepted",
    arrivalEvidence: {
      pathComplete: true,
      floorVerified: true,
      capsuleVerified: true,
      domainVerified: true,
      movementTicks: 7,
      pathLengthMm: 1250,
    },
    mirrorEvidence: {
      beforeSnapshotSha256: `sha256:${"a".repeat(64)}`,
      afterSnapshotSha256: `sha256:${"b".repeat(64)}`,
    },
  };
  const commandJson = canonicalizeJsonValue(command);
  const expectedEvents = [{
    sequence: 1,
    actorEntityId: "actor-1",
    actionId: "action-1",
    state: "arrived",
    arrivalEvidence: command.arrivalEvidence,
  }, {
    sequence: 1,
    actorEntityId: "actor-1",
    actionId: "action-1",
    state: "mirrored",
    decision: "accepted",
    beforeSnapshotSha256: command.mirrorEvidence.beforeSnapshotSha256,
    afterSnapshotSha256: command.mirrorEvidence.afterSnapshotSha256,
  }];
  const script = `extends SceneTree
func _init() -> void:
\tvar command: Variant = JSON.parse_string(${JSON.stringify(commandJson)})
\tvar arrival: Dictionary = command["arrivalEvidence"]
\tvar mirror: Dictionary = command["mirrorEvidence"]
\tvar events: Array = [{
\t\t"sequence": int(command["sequence"]), "actorEntityId": command["actorEntityId"], "actionId": command["actionId"], "state": "arrived",
\t\t"arrivalEvidence": {"pathComplete": arrival["pathComplete"], "floorVerified": arrival["floorVerified"], "capsuleVerified": arrival["capsuleVerified"], "domainVerified": arrival["domainVerified"], "movementTicks": int(arrival["movementTicks"]), "pathLengthMm": int(arrival["pathLengthMm"])},
\t}, {
\t\t"sequence": int(command["sequence"]), "actorEntityId": command["actorEntityId"], "actionId": command["actionId"], "state": "mirrored", "decision": command["state"],
\t\t"beforeSnapshotSha256": mirror["beforeSnapshotSha256"], "afterSnapshotSha256": mirror["afterSnapshotSha256"],
\t}]
\tvar canonical := JSON.stringify(events, "", true)
\tprint("R20_DIGEST_JSON:" + canonical)
\tprint("R20_DIGEST_SHA256:sha256:" + canonical.sha256_text())
\tquit(0)
`;
  await writeFile(path.join(root, "project.godot"), "[application]\nconfig/name=\"R20 Digest Fixture\"\n[rendering]\nrenderer/rendering_method=\"gl_compatibility\"\n", "utf8");
  await writeFile(path.join(root, "digest_fixture.gd"), script, "utf8");
  const result = spawnSync(process.env.GODOT_BIN, [
    "--headless",
    "--log-file",
    path.join(root, "digest-fixture.log"),
    "--path",
    root,
    "--script",
    "res://digest_fixture.gd",
  ], {
    cwd: root,
    encoding: "utf8",
    windowsHide: true,
    timeout: 30_000,
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const output = `${result.stdout}\n${result.stderr}`;
  const canonicalLine = output.split(/\r?\n/u).find((line) => line.startsWith("R20_DIGEST_JSON:"));
  const digestLine = output.split(/\r?\n/u).find((line) => line.startsWith("R20_DIGEST_SHA256:"));
  assert.equal(canonicalLine?.slice("R20_DIGEST_JSON:".length), canonicalizeJsonValue(expectedEvents));
  assert.equal(digestLine?.slice("R20_DIGEST_SHA256:".length), hashCanonicalValue(expectedEvents));
});

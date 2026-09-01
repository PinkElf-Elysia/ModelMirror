import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import {
  validateNpcBehaviorTraceJson,
} from "@matrix-oasis/npc-behavior-contracts";
import {
  hashCanonicalValue,
} from "@matrix-oasis/npc-authority-runtime";
import {
  createNpcAuthoritySession,
} from "@matrix-oasis/npc-authority-session";
import {
  prepareDeterministicNpcBehavior,
  synthesizeNpcBehaviorPolicy,
} from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createR20Coordinator,
  exportR20Coordinator,
  handleR20CoordinatorRequest,
} from "../scripts/lib/r20-host-core.mjs";

const formalMarkers = [
  "R20_GODOT_ENTITY_BRIDGE_QUALIFIED",
  "R20_MULTI_AGENT_TRACE_DETERMINISTIC",
  "R20_RUNTIME_REMAINS_AUTHORITATIVE",
];

const source = await Promise.all([
  readFile(new URL("../scripts/verify-r20.mjs", import.meta.url), "utf8"),
  readFile(new URL("../apps/runtime-godot/npc_authority_prototype/npc_load_probe.gd", import.meta.url), "utf8"),
  readFile(new URL("../docs/R20_TASK_CARD.md", import.meta.url), "utf8"),
  readFile(new URL("../docs/V2_STATUS.json", import.meta.url), "utf8"),
]);
const [verifySource, probeSource, taskCard, v2StatusJson] = source;

test("automated R20 gates cannot print formal qualification markers", () => {
  for (const marker of formalMarkers) {
    assert.equal(verifySource.includes(`console.log("${marker}")`), false);
    assert.equal(verifySource.includes(`console.log('${marker}')`), false);
    assert.doesNotMatch(probeSource, new RegExp(marker, "u"));
  }
  assert.match(probeSource, /R20_NPC_LOAD_PROBE_OK/u);
  assert.match(verifySource, /R20_AUTOMATED_GATES_OK/u);
});

test("load probes execute from an owned temporary Godot project", () => {
  assert.match(verifySource, /createRuntimePreviewProject\(\{ moduleRoot \}\)/u);
  assert.match(verifySource, /configureGdgsProject\(project\.projectRoot\)/u);
  assert.match(verifySource, /"--path",\s*project\.projectRoot/u);
  assert.match(verifySource, /removeRuntimePreviewProject\(project\.temporaryRoot/u);
  assert.doesNotMatch(
    verifySource,
    /projectRoot\s*=\s*path\.join\(moduleRoot,\s*["']apps["'],\s*["']runtime-godot["']\)/u,
  );
  assert.match(
    verifySource,
    /\["behavior-runtime", \["test", "--workspace", "@matrix-oasis\/npc-behavior-runtime"\]\]/u,
  );
});

test("task card records R20.7 acceptance without advancing the V2 completion claim", () => {
  assert.match(
    taskCard,
    /R20\.7已在用户确认中性与末班地铁隔离预览均“基本通过”后收口/u,
  );
  assert.match(taskCard, /R20没有切换Creator默认入口/u);
  assert.match(taskCard, /没有增加动画或AI能力/u);
  const status = JSON.parse(v2StatusJson);
  assert.equal(status.status, "r20-entity-bridge-qualified");
  assert.equal(status.claimAllowed, false);
  assert.equal(status.blockingRound, "R25");
});

const authoring = await readFile(
  new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
  "utf8",
);
const compiled = await compileAuthoringGamePackJson(authoring);
const runtimeGamePackJson = compiled.canonicalJson;
const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
const sha = (character) => `sha256:${character.repeat(64)}`;
const authorityPolicyJson = canonicalizeJsonValue({
  format: "matrix-oasis.npc-authority-policy",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  id: "r20-gate-truthfulness-policy",
  contentVersion: "1",
  runtime: {
    format: compiled.runtimePack.format,
    formatVersion: compiled.runtimePack.formatVersion,
    id: compiled.runtimePack.source.id,
    contentVersion: compiled.runtimePack.source.contentVersion,
    sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
    artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
    receiptSha256: hashCanonicalValue(compiled.receipt),
  },
  actorGrants: [{
    actorEntityId: "actor-unit",
    grants: [{ nodeId: "node-start", actionId: "action-initialize" }],
  }],
});
const behavior = synthesizeNpcBehaviorPolicy({ authorityPolicyJson });
assert.equal(behavior.ok, true);
const entityBindingJson = canonicalizeJsonValue({
  format: "matrix-oasis.npc-entity-binding",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  identities: {
    sceneBlueprintSha256: sha("a"),
    scenePackSha256: sha("b"),
    assetBundleSha256: sha("c"),
    spatialSolutionSha256: sha("d"),
    spatialVerificationSha256: sha("e"),
    authorityPolicySha256: behavior.npcBehaviorPolicy.authorityPolicySha256,
  },
  bindings: [{
    actorEntityId: "actor-unit",
    assetBriefId: "brief-one",
    placementId: "placement-one",
    runtimeEntityId: "actor-unit",
    homeFloorAnchorId: "floor-one",
    homePositionMm: { x: 0, y: 0, z: 0 },
    visibleNodeIds: ["node-start"],
  }],
});
const entityBindingSha256 = hashCanonicalValue(JSON.parse(entityBindingJson));
const behaviorPolicySha256 = hashCanonicalValue(
  JSON.parse(behavior.canonicalNpcBehaviorPolicyJson),
);
const token = "t".repeat(64);

function request(method, url, body = undefined) {
  return {
    remoteAddress: "127.0.0.1",
    method,
    url,
    headers: {
      authorization: `Bearer ${token}`,
      ...(method === "POST" ? { "content-type": "application/json" } : {}),
    },
    ...(body === undefined ? {} : { body: canonicalizeJsonValue(body) }),
  };
}

async function calculateTrace() {
  const authority = await createNpcAuthoritySession({
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson: authorityPolicyJson,
    timelineId: "r20-gate-truthfulness-timeline",
  });
  assert.equal(authority.ok, true);
  const prepared = prepareDeterministicNpcBehavior({
    behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson,
    entityBindingJson,
    authorityPolicyJson,
  });
  assert.equal(prepared.ok, true);
  const coordinator = createR20Coordinator({
    authoritySession: authority.session,
    preparedBehavior: prepared.prepared,
    initialBehaviorState: prepared.initialState,
    entityBindingSha256,
    sessionToken: token,
  });
  assert.ok(coordinator);

  const commandResponse = handleR20CoordinatorRequest(
    coordinator,
    request("GET", "/v1/command"),
  );
  assert.equal(commandResponse.statusCode, 200);
  const command = JSON.parse(commandResponse.body).command;
  const arrived = handleR20CoordinatorRequest(
    coordinator,
    request("POST", "/v1/arrived", {
      sequence: command.sequence,
      pathComplete: true,
      floorVerified: true,
      capsuleVerified: true,
      domainVerified: true,
      movementTicks: 20,
      pathLengthMm: 1000,
    }),
  );
  assert.equal(arrived.statusCode, 200);
  const verdict = JSON.parse(arrived.body);
  const mirrored = handleR20CoordinatorRequest(
    coordinator,
    request("POST", "/v1/mirror", {
      sequence: command.sequence,
      beforeSnapshotSha256: verdict.beforeSnapshotSha256,
      afterSnapshotSha256: verdict.afterSnapshotSha256,
    }),
  );
  assert.equal(mirrored.statusCode, 200);

  const final = exportR20Coordinator(coordinator);
  const ledger = JSON.parse(final.authority.canonicalWorldEventLedgerJson);
  const next = JSON.parse(handleR20CoordinatorRequest(
    coordinator,
    request("GET", "/v1/command"),
  ).body);
  assert.ok(next.status === "quiescent" || next.status === "ended");
  const traceJson = canonicalizeJsonValue({
    format: "matrix-oasis.npc-behavior-trace",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    timelineId: ledger.timeline.id,
    behaviorPolicySha256,
    entityBindingSha256,
    commands: final.commands,
    finalRevision: ledger.revision,
    finalHeadSha256: ledger.headSha256,
    terminalState: next.status,
  });
  assert.equal(validateNpcBehaviorTraceJson(traceJson).valid, true);
  return traceJson;
}

test("twenty independent scheduler runs calculate the same canonical trace", async () => {
  const traces = [];
  for (let run = 0; run < 20; run += 1) {
    traces.push(await calculateTrace());
  }
  assert.equal(new Set(traces).size, 1);
});

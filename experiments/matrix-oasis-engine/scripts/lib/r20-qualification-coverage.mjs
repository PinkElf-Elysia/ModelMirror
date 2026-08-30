import { createHash } from "node:crypto";
import { validateWorldEventLedgerJson } from "@matrix-oasis/npc-authority-contracts";
import { validateNpcBehaviorTraceJson } from "@matrix-oasis/npc-behavior-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const CANONICALIZATION = "matrix-oasis.canonical-json/1";
const PROFILE = "matrix-oasis.r20-runtime-coverage/1";
const REQUIREMENT_FORMAT = "matrix-oasis.r20-qualification-coverage-requirement";
const EVIDENCE_FORMAT = "matrix-oasis.r20-qualification-coverage-evidence";
const FORMAT_VERSION = "0.1.0";
const SHA256 = /^sha256:[0-9a-f]{64}$/u;

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}
function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freeze(child);
  return Object.freeze(value);
}
function exact(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}
function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
function canonicalInput(valueOrJson) {
  try {
    if (typeof valueOrJson === "string") {
      if (Buffer.byteLength(valueOrJson) > 16 * 1024 * 1024) fail("R20_QUALIFICATION_COVERAGE_INVALID");
      const value = JSON.parse(valueOrJson);
      if (canonicalizeJsonValue(value) !== valueOrJson) fail("R20_QUALIFICATION_COVERAGE_INVALID");
      return { value, canonicalJson: valueOrJson };
    }
    const canonicalJson = canonicalizeJsonValue(valueOrJson);
    return { value: JSON.parse(canonicalJson), canonicalJson };
  } catch (error) {
    if (error?.code === "R20_QUALIFICATION_COVERAGE_INVALID") throw error;
    fail("R20_QUALIFICATION_COVERAGE_INVALID");
  }
}
function validReport(report) {
  return report?.valid === true && Array.isArray(report.diagnostics) && report.diagnostics.length === 0;
}

export function validateR20QualificationCoverageRequirement(valueOrJson) {
  const { value } = canonicalInput(valueOrJson);
  if (!exact(value, ["format", "formatVersion", "canonicalization", "profile", "runtimePackSha256", "endingRequired", "loopRequirement"])
    || value.format !== REQUIREMENT_FORMAT || value.formatVersion !== FORMAT_VERSION
    || value.canonicalization !== CANONICALIZATION || value.profile !== PROFILE
    || !SHA256.test(value.runtimePackSha256 ?? "") || value.endingRequired !== true
    || !exact(value.loopRequirement, ["required", "minimumDistinctNodes"])
    || typeof value.loopRequirement.required !== "boolean"
    || ![0, 1, 2].includes(value.loopRequirement.minimumDistinctNodes)
    || (value.loopRequirement.required
      ? value.loopRequirement.minimumDistinctNodes < 1
      : value.loopRequirement.minimumDistinctNodes !== 0)) {
    fail("R20_QUALIFICATION_COVERAGE_INVALID");
  }
  return freeze(value);
}

export function validateR20QualificationCoverageEvidence(valueOrJson, requirementOrNull = null) {
  const { value } = canonicalInput(valueOrJson);
  const requirement = requirementOrNull === null ? null : validateR20QualificationCoverageRequirement(requirementOrNull);
  if (!exact(value, ["format", "formatVersion", "canonicalization", "profile", "requirementSha256", "satisfied", "endingRevision", "loopWitness"])
    || value.format !== EVIDENCE_FORMAT || value.formatVersion !== FORMAT_VERSION
    || value.canonicalization !== CANONICALIZATION || value.profile !== PROFILE
    || !SHA256.test(value.requirementSha256 ?? "")
    || !Array.isArray(value.satisfied)
    || !["ending", "ending\0loop"].includes(value.satisfied.join("\0"))
    || !Number.isSafeInteger(value.endingRevision) || value.endingRevision < 1) {
    fail("R20_QUALIFICATION_COVERAGE_INVALID");
  }
  if (value.loopWitness !== null) {
    if (!exact(value.loopWitness, ["firstVisitRevision", "repeatVisitRevision", "distinctNodeCount"])
      || !Number.isSafeInteger(value.loopWitness.firstVisitRevision) || value.loopWitness.firstVisitRevision < 0
      || !Number.isSafeInteger(value.loopWitness.repeatVisitRevision) || value.loopWitness.repeatVisitRevision <= value.loopWitness.firstVisitRevision
      || !Number.isSafeInteger(value.loopWitness.distinctNodeCount) || value.loopWitness.distinctNodeCount < 1) {
      fail("R20_QUALIFICATION_COVERAGE_INVALID");
    }
  }
  if (value.satisfied.includes("loop") !== (value.loopWitness !== null)) fail("R20_QUALIFICATION_COVERAGE_INVALID");
  if (requirement !== null) {
    const requirementJson = canonicalizeJsonValue(requirement);
    const loopRequired = requirement.loopRequirement.required;
    if (value.requirementSha256 !== sha256(requirementJson)
      || value.satisfied.join("\0") !== (loopRequired ? "ending\0loop" : "ending")
      || (loopRequired
        ? value.loopWitness === null || value.loopWitness.distinctNodeCount < requirement.loopRequirement.minimumDistinctNodes
        : value.loopWitness !== null)) {
      fail("R20_QUALIFICATION_COVERAGE_INVALID");
    }
  }
  return freeze(value);
}

function runtimeGraph(runtimeGamePackJson) {
  const { value: runtime, canonicalJson } = canonicalInput(runtimeGamePackJson);
  if (!runtime || runtime.format !== "matrix-oasis.runtime-game-pack" || runtime.formatVersion !== "0.1.0"
    || runtime.canonicalization !== CANONICALIZATION || !Array.isArray(runtime.nodes) || runtime.nodes.length < 1
    || !Array.isArray(runtime.endings) || runtime.endings.length < 1
    || !Number.isSafeInteger(runtime.entryNodeIndex) || runtime.entryNodeIndex < 0 || runtime.entryNodeIndex >= runtime.nodes.length) {
    fail("R20_QUALIFICATION_COVERAGE_INVALID");
  }
  const adjacency = runtime.nodes.map(() => []);
  const reverse = runtime.nodes.map(() => []);
  const endingSources = new Set();
  const nodeIds = new Set();
  for (const ending of runtime.endings) if (!ending || typeof ending.id !== "string") fail("R20_QUALIFICATION_COVERAGE_INVALID");
  if (new Set(runtime.endings.map((ending) => ending.id)).size !== runtime.endings.length) fail("R20_QUALIFICATION_COVERAGE_INVALID");
  for (let nodeIndex = 0; nodeIndex < runtime.nodes.length; nodeIndex += 1) {
    const node = runtime.nodes[nodeIndex];
    if (!node || typeof node.id !== "string" || !Array.isArray(node.actions)) fail("R20_QUALIFICATION_COVERAGE_INVALID");
    if (nodeIds.has(node.id)) fail("R20_QUALIFICATION_COVERAGE_INVALID");
    nodeIds.add(node.id);
    for (const action of node.actions) {
      const target = action?.target;
      if (!target || !["node", "ending"].includes(target.kind) || !Number.isSafeInteger(target.index) || target.index < 0) fail("R20_QUALIFICATION_COVERAGE_INVALID");
      if (target.kind === "node") {
        if (target.index >= runtime.nodes.length) fail("R20_QUALIFICATION_COVERAGE_INVALID");
        adjacency[nodeIndex].push(target.index);
        reverse[target.index].push(nodeIndex);
      } else {
        if (target.index >= runtime.endings.length) fail("R20_QUALIFICATION_COVERAGE_INVALID");
        endingSources.add(nodeIndex);
      }
    }
  }
  for (const edges of adjacency) edges.sort((left, right) => left - right);
  for (const edges of reverse) edges.sort((left, right) => left - right);
  const reachable = new Set([runtime.entryNodeIndex]);
  const pending = [runtime.entryNodeIndex];
  while (pending.length) for (const next of adjacency[pending.shift()]) if (!reachable.has(next)) { reachable.add(next); pending.push(next); }
  const productive = new Set(endingSources);
  const reversePending = [...endingSources].sort((left, right) => left - right);
  while (reversePending.length) for (const prior of reverse[reversePending.shift()]) if (!productive.has(prior)) { productive.add(prior); reversePending.push(prior); }
  const eligible = new Set([...reachable].filter((index) => productive.has(index)));
  if (!eligible.has(runtime.entryNodeIndex)) fail("R20_QUALIFICATION_COVERAGE_INVALID");
  return { runtime, canonicalJson, adjacency, eligible };
}

function minimumLoopNodes({ adjacency, eligible }) {
  let nextIndex = 0;
  const indexes = new Map();
  const lowlinks = new Map();
  const stack = [];
  const onStack = new Set();
  const components = [];
  function visit(node) {
    indexes.set(node, nextIndex);
    lowlinks.set(node, nextIndex);
    nextIndex += 1;
    stack.push(node);
    onStack.add(node);
    for (const target of adjacency[node]) {
      if (!eligible.has(target)) continue;
      if (!indexes.has(target)) {
        visit(target);
        lowlinks.set(node, Math.min(lowlinks.get(node), lowlinks.get(target)));
      } else if (onStack.has(target)) lowlinks.set(node, Math.min(lowlinks.get(node), indexes.get(target)));
    }
    if (lowlinks.get(node) !== indexes.get(node)) return;
    const component = [];
    for (;;) {
      const member = stack.pop();
      onStack.delete(member);
      component.push(member);
      if (member === node) break;
    }
    components.push(component.sort((left, right) => left - right));
  }
  for (const node of [...eligible].sort((left, right) => left - right)) if (!indexes.has(node)) visit(node);
  if (components.some((component) => component.length >= 2)) return 2;
  return [...eligible].some((node) => adjacency[node].includes(node)) ? 1 : 0;
}

export function deriveR20QualificationCoverageRequirement(runtimeGamePackJson) {
  const graph = runtimeGraph(runtimeGamePackJson);
  const minimumDistinctNodes = minimumLoopNodes(graph);
  const requirement = validateR20QualificationCoverageRequirement({
    format: REQUIREMENT_FORMAT,
    formatVersion: FORMAT_VERSION,
    canonicalization: CANONICALIZATION,
    profile: PROFILE,
    runtimePackSha256: sha256(graph.canonicalJson),
    endingRequired: true,
    loopRequirement: { required: minimumDistinctNodes > 0, minimumDistinctNodes },
  });
  const canonicalRequirementJson = canonicalizeJsonValue(requirement);
  return freeze({ requirement, canonicalRequirementJson, requirementSha256: sha256(canonicalRequirementJson) });
}

function acceptedTransitions(ledger, trace) {
  if (trace.timelineId !== ledger.timeline.id || trace.finalRevision !== ledger.revision || trace.finalHeadSha256 !== ledger.headSha256
    || trace.commands.length !== ledger.entries.length || !["quiescent", "ended"].includes(trace.terminalState)) {
    fail("R20_QUALIFICATION_COVERAGE_INVALID");
  }
  const accepted = [];
  let expectedNodeId = null;
  let ended = false;
  for (let index = 0; index < ledger.entries.length; index += 1) {
    const entry = ledger.entries[index];
    const command = trace.commands[index];
    const commandIdentity = {
      sequence: command.sequence,
      actorEntityId: command.actorEntityId,
      ruleIndex: command.ruleIndex,
      intentId: command.intentId,
      nodeId: command.nodeId,
      actionId: command.actionId,
    };
    if (command.actorEntityId !== entry.intent.actorEntityId
      || command.intentId !== entry.intent.id || command.nodeId !== entry.intent.nodeId || command.actionId !== entry.intent.actionId
      || command.revisionStarted !== entry.revision - 1 || command.revisionFinished !== entry.revision
      || command.state !== entry.decision.status
      || command.mirrorEvidence.beforeSnapshotSha256 !== entry.beforeSnapshotSha256
      || command.mirrorEvidence.afterSnapshotSha256 !== entry.afterSnapshotSha256
      || command.mirrorEvidence.entityBindingSha256 !== trace.entityBindingSha256
      || command.mirrorEvidence.commandSha256 !== sha256(canonicalizeJsonValue(commandIdentity))) {
      fail("R20_QUALIFICATION_COVERAGE_INVALID");
    }
    if (entry.decision.status === "accepted") {
      if (ended || expectedNodeId !== null && entry.transition.from.id !== expectedNodeId) fail("R20_QUALIFICATION_COVERAGE_INVALID");
      accepted.push(entry);
      ended = entry.transition.to.kind === "ending";
      expectedNodeId = ended ? null : entry.transition.to.id;
    }
  }
  return accepted;
}

function findLoopWitness(entries, minimumDistinctNodes) {
  const visits = [];
  for (const entry of entries) {
    if (visits.length === 0) visits.push({ nodeId: entry.transition.from.id, revision: entry.revision - 1 });
    if (entry.transition.to.kind === "node") visits.push({ nodeId: entry.transition.to.id, revision: entry.revision });
  }
  for (let repeatIndex = 1; repeatIndex < visits.length; repeatIndex += 1) {
    for (let firstIndex = 0; firstIndex < repeatIndex; firstIndex += 1) {
      if (visits[firstIndex].nodeId !== visits[repeatIndex].nodeId) continue;
      const distinctNodeCount = new Set(visits.slice(firstIndex, repeatIndex + 1).map((visit) => visit.nodeId)).size;
      if (distinctNodeCount >= minimumDistinctNodes) return {
        firstVisitRevision: visits[firstIndex].revision,
        repeatVisitRevision: visits[repeatIndex].revision,
        distinctNodeCount,
      };
    }
  }
  return null;
}

export function evaluateR20QualificationCoverage({ requirement, worldEventLedgerJson, behaviorTraceJson } = {}) {
  const validatedRequirement = validateR20QualificationCoverageRequirement(requirement);
  if (typeof worldEventLedgerJson !== "string" || typeof behaviorTraceJson !== "string"
    || !validReport(validateWorldEventLedgerJson(worldEventLedgerJson))
    || !validReport(validateNpcBehaviorTraceJson(behaviorTraceJson))) {
    fail("R20_QUALIFICATION_COVERAGE_INVALID");
  }
  let ledger;
  let trace;
  try {
    ledger = JSON.parse(worldEventLedgerJson);
    trace = JSON.parse(behaviorTraceJson);
  } catch {
    fail("R20_QUALIFICATION_COVERAGE_INVALID");
  }
  if (ledger.authority.runtime.artifactSha256 !== validatedRequirement.runtimePackSha256) fail("R20_QUALIFICATION_COVERAGE_INVALID");
  const accepted = acceptedTransitions(ledger, trace);
  const endingEntry = accepted.find((entry) => entry.transition.to.kind === "ending") ?? null;
  if (endingEntry === null) fail("R20_QUALIFICATION_COVERAGE_INCOMPLETE");
  if (trace.terminalState !== "ended") fail("R20_QUALIFICATION_COVERAGE_INVALID");
  const loopRequired = validatedRequirement.loopRequirement.required;
  const loopWitness = loopRequired ? findLoopWitness(accepted, validatedRequirement.loopRequirement.minimumDistinctNodes) : null;
  if (loopRequired && loopWitness === null) fail("R20_QUALIFICATION_COVERAGE_INCOMPLETE");
  const requirementJson = canonicalizeJsonValue(validatedRequirement);
  const evidence = validateR20QualificationCoverageEvidence({
    format: EVIDENCE_FORMAT,
    formatVersion: FORMAT_VERSION,
    canonicalization: CANONICALIZATION,
    profile: PROFILE,
    requirementSha256: sha256(requirementJson),
    satisfied: loopRequired ? ["ending", "loop"] : ["ending"],
    endingRevision: endingEntry.revision,
    loopWitness,
  }, validatedRequirement);
  return freeze({ evidence, canonicalEvidenceJson: canonicalizeJsonValue(evidence) });
}

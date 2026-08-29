import { createHash } from "node:crypto";
import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  DERIVED_PROJECTION_MANIFEST_SCHEMA,
  NPC_ADJUDICATION_RESULT_SCHEMA,
  NPC_AUTHORITY_LIMITS,
  NPC_AUTHORITY_POLICY_SCHEMA,
  NPC_INTENT_SCHEMA,
  WORLD_EVENT_LEDGER_REPLAY_REPORT_SCHEMA,
  WORLD_EVENT_LEDGER_SCHEMA,
} from "./schema.mjs";

const INTERNAL_CODE = "NPC_AUTHORITY_CONTRACT_INTERNAL_ERROR";
const PHASE = Object.freeze({ parse: 0, schema: 1, semantic: 2, integrity: 3, canonical: 4 });

export class NpcAuthorityContractOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "NpcAuthorityContractOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}
function token(value) { return String(value).replaceAll("~", "~0").replaceAll("/", "~1"); }
function at(path, value) { return `${path}/${token(value)}`; }
function item(phase, code, path = "") { return { phase, severity: "error", code, path, message: code }; }
function compareText(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
function report(items) {
  const compare = (left, right) => (PHASE[left.phase] - PHASE[right.phase]) || compareText(left.path, right.path) || compareText(left.code, right.code);
  const seen = new Set();
  const diagnostics = [];
  for (const value of [...items].sort(compare)) {
    const key = `${value.phase}\0${value.path}\0${value.code}`;
    if (!seen.has(key)) { seen.add(key); diagnostics.push(deepFreeze({ ...value })); }
  }
  return deepFreeze({ reportVersion: 1, valid: diagnostics.length === 0, diagnostics });
}
function tooDeep(text) {
  let depth = 0; let quoted = false; let escaped = false;
  for (const character of text) {
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') quoted = false;
    } else if (character === '"') quoted = true;
    else if (character === "{" || character === "[") { depth += 1; if (depth > NPC_AUTHORITY_LIMITS.documentDepth) return true; }
    else if (character === "}" || character === "]") depth -= 1;
  }
  return false;
}
function duplicateKeys(text, prefix) {
  const tree = parseTree(text, [], { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (!tree) return [];
  const output = []; const stack = [{ node: tree, path: "" }];
  while (stack.length) {
    const current = stack.pop();
    if (current.node.type === "object") {
      const keys = new Set();
      for (const property of current.node.children ?? []) {
        const key = property.children?.[0]; const value = property.children?.[1];
        if (!key || !value) continue;
        if (keys.has(key.value)) output.push(item("parse", `${prefix}_JSON_DUPLICATE_KEY`, current.path));
        keys.add(key.value); stack.push({ node: value, path: at(current.path, key.value) });
      }
    } else if (current.node.type === "array") {
      for (let i = 0; i < (current.node.children?.length ?? 0); i += 1) stack.push({ node: current.node.children[i], path: at(current.path, i) });
    }
  }
  return output;
}
function parseDocument(text, prefix, byteLimit) {
  if (typeof text !== "string") return { ok: false, diagnostics: [item("parse", `${prefix}_JSON_INPUT_TYPE`)] };
  if (new TextEncoder().encode(text).byteLength > byteLimit) return { ok: false, diagnostics: [item("parse", `${prefix}_JSON_SIZE_EXCEEDED`)] };
  if (tooDeep(text)) return { ok: false, diagnostics: [item("parse", `${prefix}_JSON_DEPTH_EXCEEDED`)] };
  const errors = []; const value = parse(text, errors, { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (errors.length || value === undefined) return { ok: false, diagnostics: [item("parse", `${prefix}_JSON_SYNTAX`)] };
  const duplicates = duplicateKeys(text, prefix);
  return duplicates.length ? { ok: false, diagnostics: duplicates } : { ok: true, value };
}

const ajv = new Ajv2020({ strict: true, allErrors: true, coerceTypes: false, useDefaults: false, removeAdditional: false, ownProperties: true, validateFormats: false });
const validators = new Map([
  ["POLICY", ajv.compile(NPC_AUTHORITY_POLICY_SCHEMA)],
  ["INTENT", ajv.compile(NPC_INTENT_SCHEMA)],
  ["RESULT", ajv.compile(NPC_ADJUDICATION_RESULT_SCHEMA)],
  ["LEDGER", ajv.compile(WORLD_EVENT_LEDGER_SCHEMA)],
  ["PROJECTION", ajv.compile(DERIVED_PROJECTION_MANIFEST_SCHEMA)],
  ["REPLAY", ajv.compile(WORLD_EVENT_LEDGER_REPLAY_REPORT_SCHEMA)],
]);
function schemaDiagnostics(validate, prefix, value) {
  if (validate(value)) return [];
  const codes = { required: "REQUIRED", additionalProperties: "UNKNOWN_PROPERTY", type: "TYPE", const: "CONST", enum: "ENUM", minItems: "MIN_ITEMS", maxItems: "MAX_ITEMS", uniqueItems: "DUPLICATE_ITEM", minimum: "NUMBER_CONSTRAINT", maximum: "NUMBER_CONSTRAINT", minLength: "STRING_CONSTRAINT", maxLength: "STRING_CONSTRAINT", pattern: "STRING_CONSTRAINT", oneOf: "SHAPE" };
  return (validate.errors ?? []).map((error) => item(
    "schema", `${prefix}_SCHEMA_${codes[error.keyword] ?? "INVALID"}`,
    error.keyword === "required" ? at(error.instancePath, error.params.missingProperty) : error.instancePath,
  ));
}
function wellFormed(value) {
  for (let i = 0; i < value.length; i += 1) {
    const unit = value.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) { const next = value.charCodeAt(i + 1); if (!Number.isInteger(next) || next < 0xdc00 || next > 0xdfff) return false; i += 1; }
    else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}
function textDiagnostics(value, prefix) {
  const output = []; const stack = [{ value, path: "" }];
  while (stack.length) {
    const current = stack.pop();
    if (typeof current.value === "string") { if (!wellFormed(current.value)) output.push(item("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`, current.path)); }
    else if (Array.isArray(current.value)) current.value.forEach((child, index) => stack.push({ value: child, path: at(current.path, index) }));
    else if (current.value && typeof current.value === "object") Object.entries(current.value).forEach(([key, child]) => {
      if (!wellFormed(key)) output.push(item("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`, current.path));
      stack.push({ value: child, path: at(current.path, key) });
    });
  }
  return output;
}
function hashCanonical(value) {
  return `sha256:${createHash("sha256").update(canonicalizeJsonValue(value), "utf8").digest("hex")}`;
}
function policySemantics(value) {
  const output = []; const actors = new Set();
  value.actorGrants.forEach((actor, actorIndex) => {
    if (actors.has(actor.actorEntityId)) output.push(item("semantic", "NPC_AUTHORITY_POLICY_ACTOR_DUPLICATE", `/actorGrants/${actorIndex}/actorEntityId`));
    actors.add(actor.actorEntityId); const grants = new Set();
    actor.grants.forEach((grant, grantIndex) => {
      const key = `${grant.nodeId}\0${grant.actionId}`;
      if (grants.has(key)) output.push(item("semantic", "NPC_AUTHORITY_POLICY_GRANT_DUPLICATE", `/actorGrants/${actorIndex}/grants/${grantIndex}`));
      grants.add(key);
    });
  });
  return output;
}
function decisionSemantics(decision, transition, before, after, root = "") {
  const output = [];
  if (decision.status === "accepted" && transition === null) output.push(item("semantic", "NPC_AUTHORITY_ACCEPTED_TRANSITION_REQUIRED", `${root}/transition`));
  if (decision.status === "rejected" && transition !== null) output.push(item("semantic", "NPC_AUTHORITY_REJECTED_TRANSITION_FORBIDDEN", `${root}/transition`));
  if (decision.status === "rejected" && before !== after) output.push(item("semantic", "NPC_AUTHORITY_REJECTED_SNAPSHOT_CHANGED", `${root}/afterSnapshotSha256`));
  return output;
}
function ledgerSemantics(value) {
  const output = []; let previous = null; let previousSnapshot = value.authority.initialSnapshotSha256; let accepted = 0; const ids = new Map();
  value.entries.forEach((entry, index) => {
    const root = `/entries/${index}`;
    if (entry.revision !== index + 1) output.push(item("semantic", "WORLD_EVENT_LEDGER_REVISION_NONCONTIGUOUS", `${root}/revision`));
    if (entry.intent.timelineId !== value.timeline.id) output.push(item("semantic", "WORLD_EVENT_LEDGER_TIMELINE_MISMATCH", `${root}/intent/timelineId`));
    if (entry.intent.observed.revision !== index || entry.intent.observed.headSha256 !== previous || entry.intent.observed.runtimeSnapshotSha256 !== entry.beforeSnapshotSha256) {
      output.push(item("semantic", "WORLD_EVENT_LEDGER_OBSERVED_STATE_MISMATCH", `${root}/intent/observed`));
    }
    if (entry.previousEntrySha256 !== previous) output.push(item("integrity", "WORLD_EVENT_LEDGER_PREVIOUS_HASH_MISMATCH", `${root}/previousEntrySha256`));
    if (entry.beforeSnapshotSha256 !== previousSnapshot) output.push(item("integrity", "WORLD_EVENT_LEDGER_SNAPSHOT_CHAIN_MISMATCH", `${root}/beforeSnapshotSha256`));
    const { entrySha256: ignored, ...body } = entry;
    if (entry.entrySha256 !== hashCanonical(body)) output.push(item("integrity", "WORLD_EVENT_LEDGER_ENTRY_HASH_MISMATCH", `${root}/entrySha256`));
    output.push(...decisionSemantics(entry.decision, entry.transition, entry.beforeSnapshotSha256, entry.afterSnapshotSha256, root));
    if (entry.decision.status === "accepted") {
      accepted += 1;
      if (entry.transition !== null) {
        if (entry.transition.step !== accepted) output.push(item("semantic", "WORLD_EVENT_LEDGER_TRANSITION_STEP_MISMATCH", `${root}/transition/step`));
        if (entry.transition.from.id !== entry.intent.nodeId || entry.transition.actionId !== entry.intent.actionId) {
          output.push(item("semantic", "WORLD_EVENT_LEDGER_TRANSITION_INTENT_MISMATCH", `${root}/transition`));
        }
      }
    }
    const intentBytes = canonicalizeJsonValue(entry.intent);
    if (ids.has(entry.intent.id)) {
      output.push(item(
        "semantic",
        ids.get(entry.intent.id) === intentBytes ? "WORLD_EVENT_LEDGER_INTENT_DUPLICATE" : "WORLD_EVENT_LEDGER_INTENT_ID_COLLISION",
        `${root}/intent/id`,
      ));
    }
    ids.set(entry.intent.id, intentBytes); previous = entry.entrySha256; previousSnapshot = entry.afterSnapshotSha256;
  });
  if (accepted > value.timeline.stepLimit) output.push(item("semantic", "WORLD_EVENT_LEDGER_STEP_LIMIT_EXCEEDED", "/timeline/stepLimit"));
  if (value.revision !== value.entries.length) output.push(item("semantic", "WORLD_EVENT_LEDGER_REVISION_MISMATCH", "/revision"));
  if (value.headSha256 !== previous) output.push(item("integrity", "WORLD_EVENT_LEDGER_HEAD_MISMATCH", "/headSha256"));
  return output;
}
function resultSemantics(value) {
  const output = decisionSemantics(value.decision, value.transition, value.beforeSnapshotSha256, value.afterSnapshotSha256);
  if ((value.revision === 0) !== (value.headSha256 === null)) output.push(item("semantic", "NPC_ADJUDICATION_RESULT_HEAD_MISMATCH", "/headSha256"));
  return output;
}
function projectionSemantics(value) {
  return value.ledger.throughRevision === 0 !== (value.ledger.throughHeadSha256 === null)
    ? [item("semantic", "DERIVED_PROJECTION_LEDGER_HEAD_MISMATCH", "/ledger/throughHeadSha256")] : [];
}
function replaySemantics(value) {
  const output = [];
  if (value.throughRevision === 0 !== (value.throughHeadSha256 === null)) output.push(item("semantic", "WORLD_EVENT_LEDGER_REPLAY_HEAD_MISMATCH", "/throughHeadSha256"));
  if (value.verifiedEntries !== value.throughRevision || value.acceptedEntries + value.rejectedEntries !== value.verifiedEntries) output.push(item("semantic", "WORLD_EVENT_LEDGER_REPLAY_COUNT_MISMATCH", "/verifiedEntries"));
  return output;
}
function validateDocument(text, { prefix, limit, validator, semantics = () => [] }) {
  try {
    const parsed = parseDocument(text, prefix, limit);
    if (!parsed.ok) return report(parsed.diagnostics);
    const schema = schemaDiagnostics(validator, prefix, parsed.value);
    if (schema.length) return report(schema);
    const semantic = [...textDiagnostics(parsed.value, prefix), ...semantics(parsed.value)];
    if (semantic.length) return report(semantic);
    if (canonicalizeJsonValue(parsed.value) !== text) return report([item("canonical", `${prefix}_JSON_NON_CANONICAL`)]);
    return report([]);
  } catch (error) {
    if (error instanceof NpcAuthorityContractOperationalError) throw error;
    throw new NpcAuthorityContractOperationalError();
  }
}

export const validateNpcAuthorityPolicyJson = (text) => validateDocument(text, { prefix: "NPC_AUTHORITY_POLICY", limit: NPC_AUTHORITY_LIMITS.policyBytes, validator: validators.get("POLICY"), semantics: policySemantics });
export const validateNpcIntentJson = (text) => validateDocument(text, { prefix: "NPC_INTENT", limit: NPC_AUTHORITY_LIMITS.intentBytes, validator: validators.get("INTENT") });
export const validateNpcAdjudicationResultJson = (text) => validateDocument(text, { prefix: "NPC_ADJUDICATION_RESULT", limit: NPC_AUTHORITY_LIMITS.resultBytes, validator: validators.get("RESULT"), semantics: resultSemantics });
export const validateWorldEventLedgerJson = (text) => validateDocument(text, { prefix: "WORLD_EVENT_LEDGER", limit: NPC_AUTHORITY_LIMITS.ledgerBytes, validator: validators.get("LEDGER"), semantics: ledgerSemantics });
export const validateDerivedProjectionManifestJson = (text) => validateDocument(text, { prefix: "DERIVED_PROJECTION_MANIFEST", limit: NPC_AUTHORITY_LIMITS.projectionBytes, validator: validators.get("PROJECTION"), semantics: projectionSemantics });
export const validateWorldEventLedgerReplayReportJson = (text) => validateDocument(text, { prefix: "WORLD_EVENT_LEDGER_REPLAY_REPORT", limit: NPC_AUTHORITY_LIMITS.replayReportBytes, validator: validators.get("REPLAY"), semantics: replaySemantics });

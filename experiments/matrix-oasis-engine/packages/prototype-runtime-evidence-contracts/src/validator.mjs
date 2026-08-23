import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { PROTOTYPE_RUNTIME_EVIDENCE_LIMITS, PROTOTYPE_RUNTIME_EVIDENCE_SCHEMA, PROTOTYPE_RUNTIME_REPLAY_PLAN_SCHEMA } from "./schema.mjs";

const INTERNAL_CODE = "PROTOTYPE_RUNTIME_EVIDENCE_CONTRACT_INTERNAL_ERROR";
const ajv = new Ajv2020({ strict: true, allErrors: true, ownProperties: true, coerceTypes: false, useDefaults: false, removeAdditional: false, validateFormats: false });
const validators = { plan: ajv.compile(PROTOTYPE_RUNTIME_REPLAY_PLAN_SCHEMA), evidence: ajv.compile(PROTOTYPE_RUNTIME_EVIDENCE_SCHEMA) };
export class PrototypeRuntimeEvidenceContractOperationalError extends Error { constructor() { super(INTERNAL_CODE); this.name = "PrototypeRuntimeEvidenceContractOperationalError"; this.code = INTERNAL_CODE; } }
function deepFreeze(value) { if (!value || typeof value !== "object" || Object.isFrozen(value)) return value; for (const child of Object.values(value)) deepFreeze(child); return Object.freeze(value); }
function diagnostic(phase, code, path = "") { return { phase, severity: "error", code, path, message: code }; }
function report(items) { const output = [], seen = new Set(); for (const item of items.sort((a,b) => a.path.localeCompare(b.path) || a.code.localeCompare(b.code))) { const key = `${item.path}\0${item.code}`; if (!seen.has(key)) { seen.add(key); output.push(deepFreeze({ ...item })); } } return deepFreeze({ reportVersion: 1, valid: output.length === 0, diagnostics: output }); }
function parseStrict(text, prefix) {
  if (typeof text !== "string") return { diagnostics: [diagnostic("parse", `${prefix}_JSON_INPUT_TYPE`)] };
  if (new TextEncoder().encode(text).byteLength > PROTOTYPE_RUNTIME_EVIDENCE_LIMITS.documentBytes) return { diagnostics: [diagnostic("parse", `${prefix}_JSON_SIZE_EXCEEDED`)] };
  let depth = 0, inString = false, escaped = false;
  for (const char of text) { if (inString) { if (escaped) escaped = false; else if (char === "\\") escaped = true; else if (char === '"') inString = false; } else if (char === '"') inString = true; else if (char === "{" || char === "[") { if (++depth > PROTOTYPE_RUNTIME_EVIDENCE_LIMITS.documentDepth) return { diagnostics: [diagnostic("parse", `${prefix}_JSON_DEPTH_EXCEEDED`)] }; } else if (char === "}" || char === "]") depth -= 1; }
  const errors = [], value = parse(text, errors, { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (errors.length || value === undefined) return { diagnostics: [diagnostic("parse", `${prefix}_JSON_SYNTAX`)] };
  const tree = parseTree(text, [], { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  const stack = tree ? [{ node: tree, path: "" }] : [], duplicates = [];
  while (stack.length) { const { node, path } = stack.pop(); if (node.type === "object") { const keys = new Set(); for (const property of node.children ?? []) { const key = property.children?.[0]?.value, child = property.children?.[1]; if (typeof key !== "string" || !child) continue; if (keys.has(key)) duplicates.push(diagnostic("parse", `${prefix}_JSON_DUPLICATE_KEY`, path)); keys.add(key); stack.push({ node: child, path: `${path}/${key.replaceAll("~","~0").replaceAll("/","~1")}` }); } } else if (node.type === "array") (node.children ?? []).forEach((child,index) => stack.push({ node: child, path: `${path}/${index}` })); }
  return duplicates.length ? { diagnostics: duplicates } : { value };
}
function wellFormed(value) { const stack=[value]; while(stack.length){ const current=stack.pop(); if(typeof current==="string"){ for(let i=0;i<current.length;i+=1){const u=current.charCodeAt(i);if(u>=0xd800&&u<=0xdbff){const n=current.charCodeAt(i+1);if(!(n>=0xdc00&&n<=0xdfff))return false;i+=1;}else if(u>=0xdc00&&u<=0xdfff)return false;}} else if(Array.isArray(current))stack.push(...current);else if(current&&typeof current==="object")stack.push(...Object.keys(current),...Object.values(current));}return true; }
function schemaDiagnostics(validate, prefix) { const suffix={required:"REQUIRED",additionalProperties:"UNKNOWN_PROPERTY",type:"TYPE",const:"CONST",enum:"ENUM",minItems:"MIN_ITEMS",maxItems:"MAX_ITEMS",uniqueItems:"DUPLICATE_ITEM",minimum:"NUMBER_CONSTRAINT",maximum:"NUMBER_CONSTRAINT",minLength:"STRING_CONSTRAINT",maxLength:"STRING_CONSTRAINT",pattern:"STRING_CONSTRAINT",oneOf:"UNION"}; return (validate.errors??[]).map((error)=>diagnostic("schema",`${prefix}_SCHEMA_${suffix[error.keyword]??"INVALID"}`,error.keyword==="required"?`${error.instancePath}/${error.params.missingProperty}`:error.instancePath)); }
function planSemantics(value) {
  const output=[], ids=new Set(), kinds=new Set();
  value.replays.forEach((replay,index)=>{ const root=`/replays/${index}`; if(ids.has(replay.id))output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_ID_DUPLICATE",`${root}/id`)); ids.add(replay.id); kinds.add(replay.kind); if(replay.expectedLocationIds.length!==replay.actionIds.length+1)output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_LOCATION_COUNT_MISMATCH",`${root}/expectedLocationIds`)); if((replay.kind==="disabled-action") !== (replay.probeActionId!==null))output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_PROBE_MISMATCH",`${root}/probeActionId`)); if(replay.resetAfter !== replay.kind.startsWith("reset-"))output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_RESET_MISMATCH",`${root}/resetAfter`)); });
  if(value.coverage.declaredEndingCount!==value.coverage.reachableEndingCount)output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_ENDING_COVERAGE_INCOMPLETE","/coverage/reachableEndingCount"));
  if(value.coverage.activeNodeCount!==value.coverage.coveredNodeCount)output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_NODE_COVERAGE_INCOMPLETE","/coverage/coveredNodeCount"));
  if(value.coverage.loop==="covered"&&!kinds.has("loop"))output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_LOOP_MISSING","/replays"));
  if(value.coverage.disabledAction==="covered"&&!kinds.has("disabled-action"))output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_REPLAY_DISABLED_ACTION_MISSING","/replays"));
  return output;
}
function evidenceSemantics(value) {
  const output=[], ids=new Set();
  value.observations.forEach((observation,index)=>{ if(ids.has(observation.replayId))output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_EVIDENCE_REPLAY_DUPLICATE",`/observations/${index}/replayId`)); ids.add(observation.replayId); observation.checkpoints.forEach((checkpoint,item)=>{ if(checkpoint.sequence!==item)output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_EVIDENCE_SEQUENCE_INVALID",`/observations/${index}/checkpoints/${item}/sequence`)); }); });
  if(value.status==="passed"&&(value.observations.some((item)=>item.outcome!=="passed")||value.performance.medianFpsMilli<30_000))output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_EVIDENCE_PASS_PROOF_INVALID","/status"));
  value.repairs.forEach((repair,index)=>{ if(repair.round!==index+1)output.push(diagnostic("semantic","PROTOTYPE_RUNTIME_EVIDENCE_REPAIR_ORDER_INVALID",`/repairs/${index}/round`)); });
  return output;
}
function validateDocument(text, kind) { try { const prefix=kind==="plan"?"PROTOTYPE_RUNTIME_REPLAY":"PROTOTYPE_RUNTIME_EVIDENCE", parsed=parseStrict(text,prefix); if(parsed.diagnostics)return report(parsed.diagnostics); const validate=validators[kind]; if(!validate(parsed.value))return report(schemaDiagnostics(validate,prefix)); if(!wellFormed(parsed.value))return report([diagnostic("semantic",`${prefix}_TEXT_UNPAIRED_SURROGATE`)]); const semantics=kind==="plan"?planSemantics(parsed.value):evidenceSemantics(parsed.value); if(semantics.length)return report(semantics); if(canonicalizeJsonValue(parsed.value)!==text)return report([diagnostic("integrity",`${prefix}_JSON_NON_CANONICAL`)]); return report([]); } catch(error){ if(error instanceof PrototypeRuntimeEvidenceContractOperationalError)throw error; throw new PrototypeRuntimeEvidenceContractOperationalError(); } }
export function validatePrototypeRuntimeReplayPlanJson(text) { return validateDocument(text,"plan"); }
export function validatePrototypeRuntimeEvidenceJson(text) { return validateDocument(text,"evidence"); }

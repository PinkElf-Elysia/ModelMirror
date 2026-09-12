import fs from 'node:fs/promises';
import path from 'node:path';
import { hashText, hashValue } from './setup.mjs';
import { KEYED_COMPILER_VERSION, KEYED_CODEC_VERSION } from './keyed-turn.mjs';
import { KEYED_AUTHORIZATION } from './keyed-ledger.mjs';
import { PROTOCOL_AUTHORIZATION, REVIEWED_PROTOCOL_AUTHORIZATION } from './protocol-ledger.mjs';
import { KEYED_PROTOCOL_VERSION, KEYED_PROTOCOL_TEXT_SHA256, KEYED_PROTOCOL_INPUT_LIMIT, REVIEWED_PROTOCOL_VERSION, REVIEWED_SEMANTICS_SHA256 } from './keyed-protocol.mjs';
const fail = code => Object.assign(Error(code), { code, status: 409 });
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value);
const safePath = value => typeof value === 'string' && value.length <= 512 && !value.includes('\\') && value.split('/').every(part => /^[a-zA-Z0-9._-]+$/u.test(part) && part !== '.' && part !== '..');
async function bytes(root, relative) {
  if (!safePath(relative)) throw fail('FREEZE_PATH_INVALID');
  const target = path.join(root, relative); let cursor = target;
  while (true) {
    const stat = await fs.lstat(cursor);
    if (stat.isSymbolicLink()) throw fail('FREEZE_LINK_REJECTED');
    const parent = path.dirname(cursor); if (parent === cursor) break; cursor = parent;
  }
  const stat = await fs.stat(target);
  if (!stat.isFile() || stat.size > 16 * 1024 * 1024) throw fail('FREEZE_FILE_INVALID');
  return fs.readFile(target);
}
// Host-provided immutable approval record. Never accept it from an HTTP payload.
export async function createExecutionFreeze({ root, manifest, expectedSha256 }) {
  if (!path.isAbsolute(root) || root.startsWith('\\\\') || root.startsWith('//') || !digest(expectedSha256) || hashValue(manifest) !== expectedSha256) throw fail('FREEZE_APPROVAL_MISMATCH');
  const snapshot = structuredClone(manifest);
  if (snapshot.format !== 'modelmirror.ai-rpg.rpg05-execution-freeze/0.5.0' || snapshot.base !== '81fc14f6e0dada2447a63c1e532ee55b1f914ded' || snapshot.branch !== 'codex/ai-rpg-rpg05-ui' || snapshot.modelId !== 'gpt-5.6-luna' || snapshot.baseUrl !== 'http://127.0.0.1:18305' || snapshot.maxTokens !== 4096 || snapshot.temperature !== 0 || snapshot.maxDispatches !== (snapshot.reviewedContinuation ? 26 : snapshot.protocolContinuation ? 24 : snapshot.targetedContinuation ? 20 : snapshot.structuredContinuation ? (snapshot.structuredContinuation.authorization === 'user-approved-total14-including-expired-recertification' ? 14 : 13) : snapshot.continuation?.extension ? 11 : snapshot.continuation ? 10 : 8) || snapshot.automaticRetry !== false) throw fail('FREEZE_SCOPE_INVALID');
  if (snapshot.continuation && (snapshot.continuation.format !== 'rpg05-authorized-continuation/1' || snapshot.continuation.authorization !== 'user-approved-plus-two-after-slot3-failure' || !digest(snapshot.continuation.priorFreezeSha256) || !Array.isArray(snapshot.continuation.priorRecords) || snapshot.continuation.priorRecords.length !== 3 || snapshot.continuation.priorRecords.some(value => !digest(value)))) throw fail('FREEZE_CONTINUATION_INVALID');
  const extension = snapshot.continuation?.extension;
  if (extension && (extension.authorization !== 'user-approved-plus-one-after-slot5-failure' || !digest(extension.priorFreezeSha256) || !digest(extension.priorPolicySha256) || !Array.isArray(extension.priorRecords) || extension.priorRecords.length !== 5 || extension.priorRecords.some(value => !digest(value)))) throw fail('FREEZE_EXTENSION_INVALID');
  if (!Array.isArray(snapshot.files) || snapshot.files.length === 0 || new Set(snapshot.files.map(file => file.path)).size !== snapshot.files.length || snapshot.files.some(file => !safePath(file.path) || !digest(file.sha256))) throw fail('FREEZE_FILES_INVALID');
  if (!Array.isArray(snapshot.journeys) || snapshot.journeys.length !== 2 || new Set(snapshot.journeys.map(journey => journey.id)).size !== 2 || ['gu', 'minecraft'].some(world => snapshot.journeys.filter(j => j.world === world).length !== 1) || snapshot.journeys.some(j => !digest(j.initialSha256))) throw fail('FREEZE_JOURNEYS_INVALID');
  if (!snapshot.gates || ['offline', 'mock', 'agentUi', 'prototype'].some(gate => snapshot.gates[gate]?.passed !== true || !digest(snapshot.gates[gate]?.sha256))) throw fail('FREEZE_GATES_INCOMPLETE');
  const structured = snapshot.structuredOutput;
  const keyed = structured?.format === 'rpg05-strict-output/2';
  const targeted = snapshot.targetedContinuation, protocol = snapshot.protocolContinuation, reviewed = snapshot.reviewedContinuation;
  if (reviewed) {
    const binding = snapshot.protocolProjection;
    if (protocol || targeted || snapshot.structuredContinuation || snapshot.continuation || !keyed || reviewed.authorization !== REVIEWED_PROTOCOL_AUTHORIZATION || reviewed.maxDispatches !== 26 || reviewed.priorRecords?.length !== 18 || reviewed.priorRecords.some(v=>!digest(v)) || snapshot.journeys.some(j=>j.startTurnCount!==0||j.turns?.length!==3) || binding?.format !== REVIEWED_PROTOCOL_VERSION || binding.textSha256 !== KEYED_PROTOCOL_TEXT_SHA256 || binding.semanticTextSha256 !== REVIEWED_SEMANTICS_SHA256 || binding.inputLimit !== KEYED_PROTOCOL_INPUT_LIMIT || binding.qualificationSlot !== 19 || !digest(binding.qualificationRecordSha256) || !digest(binding.qualificationSha256) || binding.qualificationSha256 !== structured.keyedQualificationSha256 || ['ui-host/keyed-protocol.mjs','ui-host/protocol-ledger.mjs','ui-host/real-cli.mjs','docs/RPG05_KEYED_PROTOCOL_CANDIDATE.txt','docs/RPG05_SEMANTIC_CLARIFICATION.txt'].some(p=>!snapshot.files.some(f=>f.path===p)) || snapshot.files.find(f=>f.path==='docs/RPG05_SEMANTIC_CLARIFICATION.txt')?.sha256 !== REVIEWED_SEMANTICS_SHA256) throw fail('FREEZE_REVIEWED_BINDING_INVALID');
  } else if (protocol) {
    const binding = snapshot.protocolProjection;
    if (targeted || snapshot.structuredContinuation || snapshot.continuation || !keyed || protocol.authorization !== PROTOCOL_AUTHORIZATION || protocol.maxDispatches !== 24 || protocol.priorRecords?.length !== 16 || protocol.priorRecords.some(v=>!digest(v)) || snapshot.journeys.some(j=>j.startTurnCount!==0||j.turns?.length!==3) || binding?.format !== KEYED_PROTOCOL_VERSION || binding.textSha256 !== KEYED_PROTOCOL_TEXT_SHA256 || binding.inputLimit !== KEYED_PROTOCOL_INPUT_LIMIT || !digest(binding.qualificationSha256) || binding.qualificationSha256 !== structured.keyedQualificationSha256 || ['ui-host/keyed-protocol.mjs','ui-host/protocol-ledger.mjs','ui-host/real-cli.mjs','docs/RPG05_KEYED_PROTOCOL_CANDIDATE.txt'].some(p=>!snapshot.files.some(f=>f.path===p))) throw fail('FREEZE_PROTOCOL_BINDING_INVALID');
  } else if (snapshot.protocolProjection !== undefined) throw fail('FREEZE_PROTOCOL_BINDING_INVALID');
  if (targeted && (!keyed || targeted.authorization !== KEYED_AUTHORIZATION || targeted.maxDispatches !== 20 || targeted.priorRecords?.length !== 12 || targeted.priorRecords.some(v=>!digest(v)) || snapshot.journeys.some(j=>j.startTurnCount!==(j.world==='gu'?3:1)||j.turns?.length!==3) || ['ui-host/keyed-ledger.mjs','ui-host/real-cli.mjs'].some(p=>!snapshot.files.some(f=>f.path===p)))) throw fail('FREEZE_TARGETED_BINDING_INVALID');
  if (snapshot.structuredContinuation && (!structured || !['user-approved-total13-after-slot7','user-approved-total14-including-expired-recertification'].includes(snapshot.structuredContinuation.authorization) || snapshot.structuredContinuation.maxDispatches !== snapshot.maxDispatches || snapshot.structuredContinuation.priorRecords?.length !== 7 || snapshot.structuredContinuation.priorRecords.some(v=>!digest(v)))) throw fail('FREEZE_STRUCTURED_CONTINUATION_INVALID');
  if (structured !== undefined) {
    if (!['rpg05-strict-output/1', 'rpg05-strict-output/2'].includes(structured?.format) || structured.compilerVersion !== (keyed ? KEYED_COMPILER_VERSION : 'rpg05-structured/1') || !digest(structured.backendSha256) || !digest(structured.backendTestSha256) || !digest(structured.qualificationSha256) || ['ui-host/structured-schema.mjs','ui-host/structured-http.mjs','ui-host/real-adapter.mjs','ui-host/execution-freeze.mjs','ui-host/keyed-turn.mjs'].some(p=>!snapshot.files.some(f=>f.path===p))) throw fail('FREEZE_STRUCTURED_BINDING_INVALID');
    if (keyed && (structured.codecVersion !== KEYED_CODEC_VERSION || !digest(structured.codecSha256) || structured.codecSha256 !== snapshot.files.find(f=>f.path==='ui-host/keyed-turn.mjs').sha256 || !digest(structured.keyedQualificationSha256))) throw fail('FREEZE_KEYED_BINDING_INVALID');
    if (!keyed && ['codecVersion','codecSha256','keyedQualificationSha256'].some(key=>structured[key]!==undefined)) throw fail('FREEZE_KEYED_BINDING_INVALID');
  }
  const actualRoot = await fs.realpath(root);
  async function verify() {
    if (await fs.realpath(root) !== actualRoot) throw fail('FREEZE_ROOT_DRIFT');
    if (structured) {
      const repo = path.resolve(root, '../..');
      if (hashText(await bytes(repo, 'server/main.py')) !== structured.backendSha256 || hashText(await bytes(repo, 'server/tests/test_provider_chat_structured_output.py')) !== structured.backendTestSha256) throw fail('FREEZE_BACKEND_DRIFT');
    }
    for (const file of snapshot.files) if (hashText(await bytes(root, file.path)) !== file.sha256) throw fail('FREEZE_SOURCE_DRIFT');
  }
  await verify();
  return Object.freeze({
    sha256: expectedSha256,
    async admit({ sessionId, initial, input, turnCount, kind }) {
      await verify();
      const journey = snapshot.journeys.find(item => item.id === sessionId);
      if (!journey || hashValue(initial) !== journey.initialSha256) throw fail('FREEZE_INITIAL_DRIFT');
      const offset = targeted || protocol || reviewed ? journey.startTurnCount : 0, step = turnCount - offset;
      if (kind !== 'generate' || !Number.isSafeInteger(turnCount) || step < 0 || step > 3) throw fail('FREEZE_OPERATION_FORBIDDEN');
      const allowed = step === 3 ? journey.cancellation : journey.turns?.[step];
      if (!allowed || hashValue(input) !== hashValue(allowed.input)) throw fail('FREEZE_INPUT_DRIFT');
      return Object.freeze({ ...(structured ? {responseMode:'json_schema', ...(keyed ? {wireFormat:'fixed_information_v1', ...(reviewed ? {protocolMode:'keyed_protocol_v2'} : protocol ? {protocolMode:'keyed_protocol_v1'} : {})} : {})} : {}), evidenceKind: 'real', modelId: snapshot.modelId, maxTokens: snapshot.maxTokens, temperature: snapshot.temperature, dispatchKind: step === 3 ? 'cancellation' : journey.world });
    },
    verify,
  });
}

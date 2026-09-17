import fs from 'node:fs';
import { TURN_EXCHANGE_SCHEMA } from '../src/index.mjs';
import { canonicalJson, validateGenerateTurnRequest } from '../runtime/contracts.mjs';
import { compileKeyedSchema, parseUniqueJson } from './keyed-turn.mjs';
import { hashText, hashValue } from './setup.mjs';

export const KEYED_PROTOCOL_VERSION = 'rpg05-keyed-projection/1';
export const KEYED_PROTOCOL_TEXT_SHA256 = '7fdb6ee29eb125597105483c4951efe33c42f9ea48c2b7eb7528d7fe5aea9941';
export const KEYED_PROTOCOL_INPUT_LIMIT = 65536;
const sourceSystemSha256 = 'd2e2c5a2241459d406f1385e7ec0a5790ce22010f3a021306718922a729b9e16';
const sourceClauseSha256 = 'f4efca3e0123a704e47717c5afba3ccc2792d5d9e074cc881d2d6dfdb490810e';
const sourceSchemaSha256 = 'ec31c892b3dc7a84e6dc4bc92698669999a456a73ec6ea6c7aeebc288a40c7aa';
const fail = code => { throw Object.assign(Error(code), { code }); };
function freeze(value) { if (value && typeof value === 'object') { for (const child of Object.values(value)) freeze(child); Object.freeze(value); } return value; }

// Host-only projection of an already prepared request. The original RPG04 request
// and receipt stay unchanged. No user-provided text can replace the pinned text.
export function projectKeyedRequest(card, request, { text = fs.readFileSync(new URL('../docs/RPG05_KEYED_PROTOCOL_CANDIDATE.txt', import.meta.url), 'utf8') } = {}) {
  if (typeof text !== 'string' || hashText(text) !== KEYED_PROTOCOL_TEXT_SHA256) fail('KEYED_PROTOCOL_TEXT_DRIFT');
  if (!validateGenerateTurnRequest(request).valid) fail('KEYED_PROTOCOL_REQUEST_INVALID');
  const original = structuredClone(request), projected = structuredClone(original), messages = projected.messages;
  if (messages[0]?.role !== 'system' || messages.filter(m => m.role === 'system').length !== 1 || hashText(messages[0].content) !== sourceSystemSha256) fail('KEYED_PROTOCOL_HOST_DRIFT');
  const clauses = messages[0].content.split('\n').filter(line => hashText(line) === sourceClauseSha256);
  if (clauses.length !== 1 || messages[0].content.split(clauses[0]).length !== 2) fail('KEYED_PROTOCOL_CLAUSE_DRIFT');
  const contracts = [];
  for (const [index, message] of messages.entries()) {
    let data; try { data = parseUniqueJson(message.content); } catch { continue; }
    if (data?.kind === 'output_contract') contracts.push({ index, message, data });
  }
  const contract = contracts[0];
  if (contracts.length !== 1 || contract.index !== 2 || contract.message.role !== 'user' || hashValue(TURN_EXCHANGE_SCHEMA) !== sourceSchemaSha256 || contract.message.content !== canonicalJson({ kind: 'output_contract', schema: TURN_EXCHANGE_SCHEMA }).value) fail('KEYED_PROTOCOL_CONTRACT_DRIFT');
  const compiled = compileKeyedSchema(card, original);
  messages[0].content = messages[0].content.replace(clauses[0], text);
  messages[2].content = canonicalJson({ kind: 'output_contract', format: 'modelmirror.ai-rpg.rpg05-keyed-turn', formatVersion: '1.0.0', schemaSource: 'response_format.json_schema.schema', responseFormatSha256: compiled.sha256, compilerVersion: compiled.compilerVersion, codecVersion: compiled.codecVersion }).value;
  const messageBytes = messages.reduce((sum, m) => sum + Buffer.byteLength(m.content, 'utf8') + 16, 0);
  const responseFormatBytes = Buffer.byteLength(JSON.stringify(compiled.responseFormat), 'utf8');
  if (messageBytes + responseFormatBytes > KEYED_PROTOCOL_INPUT_LIMIT || !validateGenerateTurnRequest(projected).valid) fail('KEYED_PROTOCOL_INPUT_LIMIT');
  const requestSha256 = hashValue({ request: projected, responseFormat: compiled.responseFormat });
  return freeze({ request: projected, responseFormat: compiled.responseFormat, requestSha256, compilerVersion: compiled.compilerVersion, codecVersion: compiled.codecVersion,
    binding: { format: KEYED_PROTOCOL_VERSION, textSha256: KEYED_PROTOCOL_TEXT_SHA256, sourceSystemSha256, sourceClauseSha256, sourceSchemaSha256,
      originalRequestSha256: hashValue(original), originalMessagesSha256: hashValue(original.messages), projectedMessagesSha256: hashValue(messages), responseFormatSha256: compiled.sha256, requestSha256,
      messageBytes, responseFormatBytes, estimatedInputBytes: messageBytes + responseFormatBytes, inputLimit: KEYED_PROTOCOL_INPUT_LIMIT,
      changedMessageIndexes: [0, 2], scope: 'serialization only; other messages byte-identical; byte estimate is not Provider token usage' } });
}


export const REVIEWED_PROTOCOL_VERSION = 'rpg05-keyed-projection/2';
export const REVIEWED_SEMANTICS_SHA256 = '26833e42c00839ca5aced6368438df4f37bf683a250179a825be3b268e4eba1d';
// Exact, separately approved semantic clarification; the RPG04 source and the
// v1 serialization projection remain byte-identical and independently usable.
export function projectReviewedKeyedRequest(card, request, { semanticText = fs.readFileSync(new URL('../docs/RPG05_SEMANTIC_CLARIFICATION.txt', import.meta.url), 'utf8'), ...options } = {}) {
  if (typeof semanticText !== 'string' || hashText(semanticText) !== REVIEWED_SEMANTICS_SHA256) fail('KEYED_PROTOCOL_SEMANTIC_DRIFT');
  const base = projectKeyedRequest(card, request, options), projected = structuredClone(base.request);
  projected.messages[0].content += '\n\n' + semanticText;
  const messageBytes = projected.messages.reduce((sum, m) => sum + Buffer.byteLength(m.content, 'utf8') + 16, 0);
  const estimatedInputBytes = messageBytes + base.binding.responseFormatBytes;
  if (estimatedInputBytes > KEYED_PROTOCOL_INPUT_LIMIT || !validateGenerateTurnRequest(projected).valid) fail('KEYED_PROTOCOL_INPUT_LIMIT');
  const requestSha256 = hashValue({request: projected, responseFormat: base.responseFormat});
  return freeze({...base, request: projected, requestSha256, binding: {...base.binding, format: REVIEWED_PROTOCOL_VERSION, semanticTextSha256: REVIEWED_SEMANTICS_SHA256,
    projectedMessagesSha256: hashValue(projected.messages), requestSha256, messageBytes, estimatedInputBytes,
    scope: 'serialization plus approved resource-status and NPC-knowledge clarification; other messages byte-identical; byte estimate is not Provider token usage' } });
}

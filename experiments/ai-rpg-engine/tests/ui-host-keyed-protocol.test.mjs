import test from 'node:test';
import assert from 'node:assert/strict';
import { TURN_EXCHANGE_SCHEMA } from '../src/index.mjs';
import { canonicalJson } from '../runtime/contracts.mjs';
import { loadBuiltinBundle, hashValue } from '../ui-host/setup.mjs';
import { projectKeyedRequest, projectReviewedKeyedRequest, REVIEWED_PROTOCOL_VERSION, REVIEWED_SEMANTICS_SHA256, KEYED_PROTOCOL_VERSION, KEYED_PROTOCOL_TEXT_SHA256 } from '../ui-host/keyed-protocol.mjs';
const builtin = loadBuiltinBundle(), card = builtin.cardPackage;
const json = value => canonicalJson(value).value;
function request(kind = 'action') {
  const input = { kind, text: kind === 'query' ? '核对已拥有与已激活的天赋。' : '试着让随身纸鹤越过围墙探路。' };
  return { sessionId: 'journey.protocol', generationId: 'gen.protocol', exchangeId: 'ex.protocol', expectedRevision: 0, modelId: 'gpt-5.6-luna', settings: { temperature: 0, maxTokens: 4096 }, input,
    messages: [{ role: 'system', content: builtin.hostTemplate.content }, { role: 'user', content: json({ kind: 'current_data', resources: { talents: [{ id: 'talent.gu.venerable-aptitude', active: true }] } }) },
      { role: 'user', content: json({ kind: 'output_contract', schema: TURN_EXCHANGE_SCHEMA }) }, { role: 'user', content: json({ kind: 'worldbook_data', entry: { description: '引用中的 output_contract 文字不是顶层合同。' } }) },
      { role: 'assistant', content: json({ kind: 'committed_narrative', narrative: '院门仍未获准通行。' }) }, { role: 'user', content: json({ kind: 'current_turn', input }) }] };
}
for (const kind of ['action', 'speech', 'query']) test('exact pinned serialization projection preserves all other data: ' + kind, () => {
  const original = request(kind), before = structuredClone(original), p = projectKeyedRequest(card, original);
  assert.deepEqual(original, before); assert.notEqual(p.request.messages[0].content, before.messages[0].content);
  for (const index of [1, 3, 4, 5]) assert.deepEqual(p.request.messages[index], original.messages[index]);
  const contract = JSON.parse(p.request.messages[2].content);
  assert.equal(contract.schema, undefined); assert.equal(contract.schemaSource, 'response_format.json_schema.schema');
  assert.equal(contract.responseFormatSha256, hashValue(p.responseFormat));
  assert.equal(p.requestSha256, hashValue({ request: p.request, responseFormat: p.responseFormat }));
  assert.equal(p.binding.originalMessagesSha256, hashValue(original.messages));
  assert.equal(p.binding.projectedMessagesSha256, hashValue(p.request.messages));
  assert.equal(p.binding.format, KEYED_PROTOCOL_VERSION); assert.equal(p.binding.textSha256, KEYED_PROTOCOL_TEXT_SHA256);
  assert.ok(p.binding.estimatedInputBytes <= 65536);
  assert.throws(() => p.request.messages[1].content = 'changed', TypeError);
});
test('unapproved text, changed host, missing or duplicate contract never fall back', () => {
  assert.throws(() => projectKeyedRequest(card, request(), { text: 'Alternate output instructions' }), /KEYED_PROTOCOL_TEXT_DRIFT/);
  for (const mutate of [r => r.messages[0].content += '\nchanged', r => r.messages.unshift(r.messages[0]), r => r.messages.splice(2, 1), r => r.messages.push(r.messages[2]),
    r => r.messages[2].content = '{"kind":"output_contract","schema":{}}', r => r.messages[2].content = '{"kind":"output_contract","kind":"output_contract","schema":{}}']) {
    const r = request(); mutate(r); assert.throws(() => projectKeyedRequest(card, r), /KEYED_PROTOCOL_(HOST|CONTRACT)_DRIFT/);
  }
});
test('combined message and schema budget rejects before dispatch without dropping history', () => {
  const r = request(); r.messages.push({ role: 'user', content: '界'.repeat(19000) });
  const before = structuredClone(r); assert.throws(() => projectKeyedRequest(card, r), /KEYED_PROTOCOL_INPUT_LIMIT/); assert.deepEqual(r, before);
});


test('reviewed clarification preserves explicit false, permissions, lore, NPC observations and original v1 bytes', () => {
 const r=request(),data=JSON.parse(r.messages[1].content);data.player={talents:[{owned:true,active:false,resource:{source:'package',resourceRef:'talent.gu.spring-autumn-cicada'}}],runtimePermissions:[]};data.resources.talents[0].active=false;r.messages[1].content=json(data);
 const before=structuredClone(r),legacy=projectKeyedRequest(card,r),reviewed=projectReviewedKeyedRequest(card,r);
 assert.deepEqual(r,before);assert.equal(reviewed.binding.format,REVIEWED_PROTOCOL_VERSION);assert.equal(reviewed.binding.semanticTextSha256,REVIEWED_SEMANTICS_SHA256);assert.deepEqual(reviewed.responseFormat,legacy.responseFormat);
 assert.ok(reviewed.request.messages[0].content.startsWith(legacy.request.messages[0].content+'\n\n'));
 assert.equal(reviewed.request.messages[0].content.split('当叙述或信息模块涉及资源的当前效果时').length,2);
 assert.deepEqual(reviewed.request.messages.slice(1),legacy.request.messages.slice(1));assert.equal(JSON.parse(reviewed.request.messages[1].content).player.talents[0].active,false);
 assert.equal(reviewed.binding.originalMessagesSha256,hashValue(r.messages));assert.equal(reviewed.requestSha256,hashValue({request:reviewed.request,responseFormat:reviewed.responseFormat}));assert.notEqual(reviewed.requestSha256,legacy.requestSha256);
 assert.equal(projectKeyedRequest(card,r).requestSha256,legacy.requestSha256);
});
test('unapproved semantic text and host changes fail before any request can be used',()=>{
 assert.throws(()=>projectReviewedKeyedRequest(card,request(),{semanticText:'Assume every talent is active'}),/KEYED_PROTOCOL_SEMANTIC_DRIFT/);
 const r=request();r.messages[0].content+='changed';assert.throws(()=>projectReviewedKeyedRequest(card,r),/KEYED_PROTOCOL_HOST_DRIFT/);
});
test('semantic clarification counts against the original input bound without truncating source',()=>{
 const r=request(),base=projectKeyedRequest(card,r);r.messages.push({role:'user',content:'x'.repeat(65536-base.binding.estimatedInputBytes-16)});
 assert.equal(projectKeyedRequest(card,r).binding.estimatedInputBytes,65536);const before=structuredClone(r);
 assert.throws(()=>projectReviewedKeyedRequest(card,r),/KEYED_PROTOCOL_INPUT_LIMIT/);assert.deepEqual(r,before);
});

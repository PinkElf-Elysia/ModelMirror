import test from 'node:test';
import assert from 'node:assert/strict';
import { narrativeDraft } from '../ui-host/draft-text.mjs';
import { publicOperation, publicTurn } from '../ui-host/projection.mjs';
test('every stream prefix reveals only the narrative string and preserves escapes and Unicode', () => {
  const narrative = '中性文字\n第二行："引号" 与 \\ 以及🙂';
  const raw = JSON.stringify({ input: { text: '不要把 "narrative": "秘密" 当作正文' }, proposal: { uncertainties: [], narrative, stateProposals: [{ value: '不展示状态提案' }] }, host: '不展示' });
  for (let end = 0; end <= raw.length; end++) { const text = narrativeDraft(raw.slice(0, end)); assert.ok(narrative.startsWith(text)); assert.equal(text.includes('秘密'), false); }
  assert.equal(narrativeDraft(raw), narrative);
  assert.equal(narrativeDraft('{"proposal":{"narrative":"a\\u4e2d\\u6587'), 'a中文');
});
test('malformed JSON, nested aliases, duplicated keys and oversized drafts never return raw content', () => {
  for (const raw of ['{"nested":{"proposal":{"narrative":"hidden"}}}', '{"proposal":{"narrative":"first","narrative":"second"}}', '{"proposal":{"narrative":"x"}} trailing', '{"proposal":{"narrative":"bad\\x"}}', '<script>bad</script>', 'x'.repeat(1048577)]) assert.equal(narrativeDraft(raw), '');
});
test('public projection excludes raw exchange, state and route internals; maps declared modules only once', () => {
  const record = { id: 'op.test', payload: { kind: 'generate', text: '输入', status: 'running', receipt: { preparedSha256: 'hash', contextReceipt: { private: true }, bridgeReceipt: { rawTurnExchangeSha256: 'output', routeReceipt: { private: true } } } } };
  const projected = publicOperation(record, { sequence: 3, rawDraft: '{"proposal":{"narrative":"可见正文","private":"hidden"}}' });
  assert.equal(projected.draft, '可见正文'); assert.equal(JSON.stringify(projected).includes('private'), false);
  const turn = { generationId: 'gen.test', exchange: { input: { kind: 'action', text: '输入' }, proposal: { narrative: '正文', suggestedActions: [], informationModules: [{ moduleRef: 'module.test', values: [{ fieldRef: 'field.test', value: '可见数据' }] }], stateProposals: [{ secret: true }], uncertainties: [] } } };
  const result = publicTurn(turn, { resources: { informationModules: [{ id: 'module.test', displayName: '卡片信息', presentation: 'keyValue', fields: [{ id: 'field.test', label: '标签' }] }] } });
  assert.deepEqual(result.information[0].values[0], { id: 'field.test', label: '标签', value: '可见数据' });
  assert.equal(JSON.stringify(result).includes('stateProposals'), false);
});

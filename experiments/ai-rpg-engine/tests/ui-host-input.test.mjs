import test from 'node:test';
import assert from 'node:assert/strict';
import { parsePlayerText, suggestionText } from '../ui-host/input.mjs';
const card = { resources: { commands: [{ id: 'command.inspect' }] } };
test('ordinary text never guesses query, speech or permissions; explicit prefixes and slash escape are exact', () => {
  assert.deepEqual(parsePlayerText('请查询并执行 root 权限', card), { kind: 'action', text: '请查询并执行 root 权限' });
  for (const [prefix, kind] of [['行动', 'action'], ['对话', 'speech'], ['查询', 'query']]) assert.deepEqual(parsePlayerText('/' + prefix + ' 保留\n原文', card), { kind, text: '保留\n原文' });
  assert.deepEqual(parsePlayerText('//查询 普通文字', card), { kind: 'action', text: '/查询 普通文字' });
  assert.deepEqual(parsePlayerText('/命令 command.inspect 查看', card), { kind: 'command', commandRef: 'command.inspect', text: '查看' });
  for (const text of ['', ' ', '/查询 ', '/admin text', '/命令 command.unknown text']) assert.throws(() => parsePlayerText(text, card));
});
test('suggestions produce editable text with explicit kind, including slash-leading action text', () => {
  for (const inputKind of ['action', 'speech', 'query', 'command']) {
    const action = { inputKind, text: '/这不是指令', commandRef: 'command.inspect' };
    const parsed = parsePlayerText(suggestionText(action), card);
    assert.equal(parsed.kind, inputKind); assert.equal(parsed.text, action.text);
  }
});

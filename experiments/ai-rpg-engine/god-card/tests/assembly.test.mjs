import test from 'node:test';
import assert from 'node:assert/strict';
import {assemble, loadFrozen, hash} from '../lib/assembly.mjs';

test('frozen author text matches the registered Unicode count and digest', () => {
  const {system, manifest} = loadFrozen();
  assert.equal(hash(system), manifest.system.sha256);
  assert.equal(Array.from(system).length, manifest.system.unicodeCharacters);
  assert.equal(manifest.source.sha256, 'aef510caf2cdb47df97922d54e2fc991584e6c453cb280d1b55df03176e006fe');
});
test('opening is exactly one frozen system and the supplied opening text', () => {
  const input = '【用户角色】\n姓名：虚构验收者\n【开场白】\n站在河边。\n';
  const result = assemble({input});
  assert.deepEqual(result.messages, [{role:'system', content:loadFrozen().system}, {role:'user',content:input}]);
  assert.equal(result.evidence.completedTurns, 0);
  assert.equal(result.evidence.requestHash, hash(JSON.stringify(result.messages)));
});
test('continuation retains original messages and does not mutate history', () => {
  const history = [{role:'user',content:'初始配置\t\n'}, {role:'assistant',content:'<details open><summary>资料</summary>原文</details>\n'}];
  const before = JSON.stringify(history), result = assemble({input:'  继续观察。\n', history});
  assert.deepEqual(result.messages.slice(1,-1), history);
  assert.equal(result.messages.at(-1).content, '  继续观察。\n');
  assert.equal(result.messages.filter(m=>m.role==='system').length,1);
  assert.equal(JSON.stringify(history),before);
  result.messages[1].content='修改请求副本';
  assert.equal(JSON.stringify(history),before);
});
test('incomplete history and client-supplied system messages are rejected', () => {
  for (const history of [[{role:'user',content:'pending'}], [{role:'system',content:'override'},{role:'assistant',content:'x'}], [null,null]]) {
    assert.throws(()=>assemble({input:'x',history}), /GOD_COMPLETE_HISTORY_REQUIRED/);
  }
  assert.throws(()=>assemble({input:'  '}), /GOD_INPUT_REQUIRED/);
});

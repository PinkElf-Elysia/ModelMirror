import test from 'node:test';import assert from 'node:assert/strict';
import {loadAdapter,stripPanels} from './adapter.mjs';import {exampleState} from './example.mjs';
import {validateState,blankState,relationshipLabel} from './definition.mjs';
test('original fingerprint retained, only managed instructions removed',async()=>{
 const a=await loadAdapter();assert.ok(a.original.includes('复制上轮【能力档案·金库】的完整内容'));
 assert.ok(!a.system.includes('复制上轮【能力档案·金库】的完整内容'));
 assert.ok(!a.system.includes('<summary>🧍 自身状态</summary>'));assert.ok(!a.system.includes('<summary>🔗 关系网络</summary>'));
 assert.ok(a.system.includes('<summary>🌍 世界信息</summary>'));assert.ok(a.system.includes('## 关系系统'));
 assert.ok(a.system.includes('初始关系值/信任值=0'));assert.ok(a.system.includes('<state_delta>'));
 assert.ok(a.system.length<a.original.length);
});
test('unknown is not zero; fixture is adult and locally validated',()=>{
 const s=blankState();validateState(s);assert.equal(s.balances.low,null);const e=exampleState();assert.equal(e.profile.initialAge,'24');assert.equal(e.items[0].quantity,5);
 assert.equal(relationshipLabel(-100),'不共戴天');assert.equal(relationshipLabel(100,'trust'),'托命相付');
});
test('remove only managed panels',()=>assert.equal(stripPanels('<details open><summary>🧍 自身状态</summary>x</details>正文<details><summary>🌍 世界信息</summary>y</details>'),'正文<details><summary>🌍 世界信息</summary>y</details>'));

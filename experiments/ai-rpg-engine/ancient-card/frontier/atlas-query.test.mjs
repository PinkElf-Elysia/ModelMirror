import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {emptySession,play,publicSession,prompts} from './session.mjs';
import {offlineNarrator} from './transport.mjs';
import {queryAtlas} from './atlas-query.mjs';

const scroll='【人生路线】帝王之路\n【帝王】崇祯帝朱由检\n【参照方式】历史基础上的推演';
const send=(s,input,generate)=>play(s,{input,revision:s.revision,requestId:randomUUID()},generate);

test('同档比较两地，按需返回资料；下一次军报查询仍按京师获报时点',async()=>{
  const s=emptySession(true),packets=[];
  const generate=p=>{packets.push(p);return offlineNarrator(p);};
  await send(s,scroll,generate);
  const before=publicSession(s).frontier;
  await send(s,'请比较吐鲁番与叶尔羌。',generate);
  const follow=packets.at(-1),tool=follow.messages.at(-1);
  assert.deepEqual(follow.tools,[]);
  assert.equal(tool.tool_call_id,follow.messages.at(-2).tool_calls[0].id);
  assert.deepEqual(JSON.parse(tool.content).rows.map(r=>r.match.id).sort(),['turfan','yarkand']);
  assert.ok(!JSON.stringify(follow).includes(s.world.facts[0].id));
  assert.deepEqual(publicSession(s).frontier,before);
  assert.deepEqual(s.world.agent.events,[]);
  assert.match(s.turns.at(-1).raw,/吐鲁番/);
  await send(s,prompts.ask,generate);
  assert.match(s.turns.at(-1).raw,/尚无新报/);
  await send(s,prompts.report,generate);
  await send(s,prompts.ask,generate);
  assert.match(s.turns.at(-1).raw,/欠饷鼓噪/);
  for(const action of [prompts.draft,prompts.decree,prompts.receive,prompts.handover,prompts.settle])await send(s,action,generate);
  assert.deepEqual(publicSession(s).frontier.locations.map(p=>p.control),['明','明','后金','明']);
});

test('旧模式保持原菜单；无匹配、歧义与越权查询',async()=>{
  const s=emptySession(),menus=[];
  const generate=p=>{menus.push(p.tools.map(t=>t.function.name));return offlineNarrator(p);};
  await send(s,scroll,generate);await send(s,prompts.ask,generate);
  assert.ok(menus.every(m=>!m.includes('query_atlas')));
  assert.deepEqual(queryAtlas({names:['无此地名']}).rows[0].candidates,[]);
  assert.ok(queryAtlas({names:['喀尔喀']}).rows[0].candidates.length>1);
  assert.throws(()=>queryAtlas({names:['后金'],recipient:'camp'}));
  assert.throws(()=>queryAtlas({names:['大明','后金','叶尔羌','吐鲁番']}));
});

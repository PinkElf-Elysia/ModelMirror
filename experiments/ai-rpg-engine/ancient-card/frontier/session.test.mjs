import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mkdtemp,rm} from 'node:fs/promises';
import {emptySession,play,publicSession,knowledge,prompts} from './session.mjs';
import {offlineNarrator} from './transport.mjs';
import {openHost} from './server.mjs';
import {assemble} from '../assembly.mjs';
const scroll='【人生路线】帝王之路\n【帝王】崇祯帝朱由检\n【参照方式】历史基础上的推演';
const req=(s,input)=>({requestId:randomUUID(),revision:s.revision,input});
test('原卡上下文、首轮及工具续答只收到京师已知；发生与获报可分别回查',async()=>{
  const s=emptySession(),packets=[];
  const send=async input=>play(s,req(s,input),p=>{packets.push(structuredClone(p));return offlineNarrator(p);});
  await send(scroll);
  const [source]=knowledge(s.world,'camp');assert.ok(source);assert.deepEqual(knowledge(s.world,'court'),[]);
  await send(prompts.ask);
  assert.equal(packets.length,3);
  for(const p of packets){
    assert.ok(p.messages[0].content.startsWith(assemble({input:'x'})[0].content));
    assert.ok(!JSON.stringify(p).includes(source.id));assert.ok(!JSON.stringify(p).includes(source.text));
  }
  assert.equal(packets[1].messages[1].content,scroll);
  assert.equal(packets[2].messages.at(-1).role,'tool');
  assert.equal(packets[2].messages.at(-1).tool_call_id,packets[2].messages.at(-2).tool_calls[0].id);
  assert.match(s.turns.at(-1).raw,/尚无新报/);
  assert.ok(!JSON.stringify(publicSession(s)).includes(source.id));
  await send(prompts.report);await send(prompts.ask);
  const [delivered]=knowledge(s.world,'court');
  assert.equal(delivered.id,source.id);assert.ok(delivered.receivedAt>delivered.occurredAt);
  assert.equal(knowledge(s.world,'court',delivered.receivedAt-1).length,0);
  assert.match(s.turns.at(-1).raw,/欠饷鼓噪/);
  assert.equal(JSON.parse(packets.at(-1).messages.at(-1).content).evidence[0].id,source.id);
  await send(prompts.draft);assert.equal(s.world.control,'明');
  await send(prompts.cancel);assert.equal(s.world.control,'明');
  await send(prompts.draft);await send(prompts.decree);
  const receipt=knowledge(s.world,'camp').find(e=>e.kind==='receipt');
  assert.ok(receipt);assert.ok(!JSON.stringify(publicSession(s)).includes(receipt.id));
  await send(prompts.ask);assert.ok(!JSON.stringify(packets.at(-1)).includes(receipt.id));
  await send(prompts.receive);await send(prompts.handover);
  assert.equal(s.world.control,'明');assert.equal(s.world.agent.pending.source,receipt.id);
  await send(prompts.settle);await send('读档后接着说，宁远如今由谁接防？');
  assert.match(s.turns.at(-1).raw,/现登记为后金/);
  const map=publicSession(s).frontier;assert.deepEqual(map.locations.map(p=>p.control),['明','明','后金','明']);
  assert.equal(map.known.find(e=>e.kind==='settled').source,receipt.id);
  assert.deepEqual(s.world.agent.events.map(e=>e.kind),['report','cancelled','decree','receipt','settled']);
});
test('读档、错误确认、重复送出及并发确认共用一份权威记录',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'ancient-map-session-'));let h=await openHost(dir);
  try{
    let s=await h.create();const send=async input=>s=await h.send(s.id,req(s,input));
    await assert.rejects(()=>send('【人生路线】芸芸众生\n【姓名】小陈'),/崇祯/);
    await send(scroll);
    await assert.rejects(()=>send(prompts.settle),/当前不可执行/);
    for(const action of [prompts.ask,prompts.report,prompts.ask,prompts.draft,prompts.decree,prompts.receive,prompts.handover])await send(action);
    const action=req(s,prompts.settle);
    await Promise.all([h.send(s.id,action),h.send(s.id,action)]);
    await h.close();h=await openHost(dir);s=await h.get(s.id);
    assert.equal(s.frontier.known.filter(e=>e.kind==='settled').length,1);
    assert.equal((await h.send(s.id,action)).revision,s.revision);
    await assert.rejects(()=>h.send(s.id,{...action,input:'撤回'}),/编号内容不同/);
    await assert.rejects(()=>h.send(s.id,{...action,requestId:randomUUID()}),/版本已变更/);
    await send('接着说，宁远此刻归谁？');assert.match(s.turns.at(-1).raw,/后金/);
  }finally{await h.close();await rm(dir,{recursive:true,force:true});}
});

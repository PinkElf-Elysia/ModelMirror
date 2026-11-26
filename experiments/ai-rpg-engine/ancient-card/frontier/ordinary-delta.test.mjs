import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp,readFile,rm,cp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {preview} from './ordinary-preview.mjs';
import {startRegister,registerView,adoptStoryDelta,storyHistoryText} from './place-register.mjs';

const opening='【人生路线】帝王之路\n【帝王】崇祯帝朱由检';
const quote='商人说鸡笼换了旗，尚待核实。';
const suffix=(node,quote)=>'<map_delta>'+JSON.stringify({kind:'rumor',node,quote})+'</map_delta>';
const valid=quote+suffix('鸡笼',quote);
const fixture='案上是一份通州驿舍的转述。\n'+suffix('通州驿舍','通州驿舍的转述');
const saved=process.env.ANCIENT_OPENING_EVIDENCE;
const json=async path=>JSON.parse(await readFile(path,'utf8'));
async function session(dir,reply){
  const packets=[];
  const host=await preview(dir,0,true,async p=>{
    packets.push(p);
    return {status:'completed',finishReason:'stop',message:{role:'assistant',content:reply(p)}};
  });
  async function api(suffix,body){
    const url=host.origin+'/rpg-app/ancient/chat/api/sessions'+suffix;
    const res=await fetch(url,body?{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    const data=await res.json();assert.ok(res.ok,JSON.stringify(data));return data;
  }
  return {...host,packets,create:()=>api('',{opening:''}),load:id=>api('/'+id),async send(s,input){
    const route='/'+s.id,mem=await api(route+'/memory-palace');
    const updated=await api(route+'/send',{operationId:randomUUID(),input,expectedSessionRevision:s.revision,expectedMemoryRevision:mem.memoryRevision,expectedContextPolicyRevision:mem.effectivePolicy.revision});
    assert.equal(updated.turns.length,s.turns.length+1,JSON.stringify(updated.operation));return updated;
  }};
}
const reference=registerView(startRegister());
test('普通卡首轮回放：未知地点隔离，归档原文与采用正文分别可核对；续轮只登记有出处的传闻',async()=>{
  const archive=saved?await json(join(saved,'accepted.json')):null;
  let raw=fixture;
  if(saved){
    const stream=await readFile(join(saved,'provider-1.sse'),'utf8');
    raw=stream.split('\n').filter(line=>line.startsWith('data:')&&!line.includes('[DONE]'))
      .map(line=>JSON.parse(line.slice(5)).choices?.[0]?.delta?.content??'').join('');
    assert.equal(raw,archive.turns[0].raw);
  }
  const input=archive?.turns[0].input??opening;
  const body=raw.slice(0,raw.indexOf('<map_delta>')).trimEnd();
  assert.ok(body);assert.match(raw,/<map_delta>.*通州驿舍/);
  const dir=await mkdtemp(join(tmpdir(),'ancient-opening-delta-'));
  let host=await session(dir,()=>raw);
  try{
    let s=await host.send(await host.create(),input);
    assert.ok(s.turns[0].raw===body,'采用正文仍含无效尾块，或正文被修改');
    const afterOpening=s.frontier;assert.deepEqual(afterOpening,reference);
    const evidence=await json(join(dir,'archive-chat/ancient/offline/atlas-offline-evidence',s.turns[0].requestId+'.json'));
    assert.equal(evidence.stages[0].output.message.content,raw);
    assert.equal(evidence.deltaRejection,'unknown_location');
    await host.close();host=await session(dir,()=>valid);s=await host.load(s.id);
    s=await host.send(s,'请王承恩呈上奏疏。');
    assert.equal(host.packets[0].messages.find(m=>m.role==='assistant').content,body);
    assert.equal(s.frontier.known.length,1);assert.equal(s.frontier.known[0].kind,'rumor');
    assert.equal(s.frontier.known[0].source,s.turns[1].requestId);
    assert.deepEqual(s.frontier.locations,reference.locations);assert.equal(s.turns[1].raw,quote);
    if(process.env.ANCIENT_DELTA_REPORT)await writeFile(process.env.ANCIENT_DELTA_REPORT,JSON.stringify({
      sourceRequest:archive?.turns[0].requestId,offlineReplay:true,providerCalls:0,
      raw,adopted:body,rejection:evidence.deltaRejection,afterOpening,
      nextRequest:host.packets[0],positiveControl:s.frontier.known
    },null,2),{flag:'wx'});
  }finally{await host.close();await rm(dir,{recursive:true,force:true});}
});
test('普通卡合法开局传闻只入账一次，第二回合可见；无原文支持的续轮增量不改账',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'ancient-valid-opening-'));
  const host=await session(dir,p=>p.messages.at(-1).content===opening?valid:'奏疏仍封着。'+suffix('宁远','宁远已经易手。'));
  try{
    let s=await host.send(await host.create(),opening);
    assert.equal(s.turns[0].raw,quote);assert.equal(s.frontier.known.length,1);
    const known=s.frontier.known;assert.equal(known[0].source,s.turns[0].requestId);
    s=await host.send(s,'继续朝议。');
    assert.match(host.packets[1].messages[0].content,/御前新到记录：未经核实的转述/);
    assert.equal(s.turns[1].raw,'奏疏仍封着。');assert.deepEqual(s.frontier.known,known);
    assert.deepEqual(s.frontier.locations,reference.locations);
  }finally{await host.close();await rm(dir,{recursive:true,force:true});}
});
test('修复前的真实旧档续写时仅清理输入投影，保留已存历史原文',{skip:!saved},async()=>{
  const old=await json(join(saved,'accepted.json')),dir=await mkdtemp(join(tmpdir(),'ancient-delta-legacy-'));
  await cp(join(saved,'saves'),dir,{recursive:true});const host=await session(dir,()=>'王承恩呈上奏疏。');
  try{
    const s=await host.send(await host.load(old.id),'请王承恩把第一份奏疏呈上来。');
    assert.equal(s.turns[0].raw,old.turns[0].raw);
    assert.equal(host.packets[0].messages.find(m=>m.role==='assistant').content,old.turns[0].raw.split('<map_delta>')[0].trimEnd());
    assert.deepEqual(s.frontier.known,old.frontier.known);
  }finally{await host.close();await rm(dir,{recursive:true,force:true});}
});

test('尾块残缺或重复时拒绝新回合；读取同样的旧历史只取正文',()=>{
  for(const suffix of ['<map_delta>{','</map_delta>',valid.slice(quote.length).repeat(2)]){
    const raw=quote+suffix,world=startRegister();
    assert.throws(()=>adoptStoryDelta(world,raw,'attempt'),/地点增量格式无效/);
    assert.deepEqual(world,startRegister());assert.equal(storyHistoryText(raw),quote);
  }
});

test('鸡笼引句须来自正文内已经发生的转述，登记保留说话者与转述限定',()=>{
  const claim='鸡笼已经易手', delta=suffix('鸡笼',claim);
  const source='海商在御前说道：“鸡笼已经易手。小人也是听船户转述，并未亲见。”';
  const panel='<details open><summary>🎯 接下来</summary>[B] 问海商可听说鸡笼已经易手</details>';
  for(const prose of [
    '王承恩回道：“无人禀报鸡笼已经易手，奴婢手上也没有这份文书。”',
    '你低头核看账目。'+panel,
    '今日并无台湾来报。<details><summary>🌐 世界风云</summary>〔通政司进呈〕鸡笼已经易手</details>',
    '海商在御前说道：“鸡笼已经易手吗？”',
    '你低头核看账目。<!--'+source+'-->'
  ]){
    const world=startRegister();
    let refusal;
    assert.equal(adoptStoryDelta(world,prose+delta,'test',reason=>{refusal=reason;}),prose);
    assert.equal(refusal,'unsupported_quote');
    assert.deepEqual(world,startRegister());
  }
  const world=startRegister();
  assert.equal(adoptStoryDelta(world,source+panel+delta,'positive'),source+panel);
  assert.equal(world.facts.length,1);
  const fact=world.facts[0];
  assert.equal(fact.source,'positive');assert.equal(fact.sourceText,source);
  assert.match(fact.text,/鸡笼已经易手。小人也是听船户转述，并未亲见/);
  assert.match(fact.text,/海商/);
  const negativeClaim='鸡笼守军并未换旗';
  adoptStoryDelta(world,'海商在御前说道：“'+negativeClaim+'。”'+suffix('鸡笼',negativeClaim),'negative-fact');
  assert.equal(world.facts.length,2);
  assert.match(world.facts[1].text,/守军并未换旗/);
  assert.deepEqual(registerView(world).locations,reference.locations);
});

test('话到嘴边未出口：普通聊天保留原话，重开续写时没有新到传闻',async()=>{
  const prose='海商正要说：“鸡笼已经易手。”话到嘴边，又咽了回去。';
  const dir=await mkdtemp(join(tmpdir(),'ancient-unspoken-'));
  let host=await session(dir,()=>prose+suffix('鸡笼','鸡笼已经易手'));
  try{
    const s=await host.send(await host.create(),opening);
    assert.equal(s.turns[0].raw,prose);
    assert.deepEqual(s.frontier,reference);
    await host.close();host=await session(dir,()=>valid);
    const reopened=await host.load(s.id);
    assert.deepEqual(reopened.frontier,reference);
    const next=await host.send(reopened,'召海商当面禀明。');
    assert.doesNotMatch(host.packets[0].messages[0].content,/御前新到记录：/);
    assert.ok(host.packets[0].messages.some(m=>m.role==='assistant'&&m.content===prose));
    assert.equal(next.frontier.known.length,1);
    assert.equal(next.frontier.known[0].source,next.turns[1].requestId);
    assert.deepEqual(next.frontier.locations,reference.locations);
  }finally{await host.close();await rm(dir,{recursive:true,force:true});}
});

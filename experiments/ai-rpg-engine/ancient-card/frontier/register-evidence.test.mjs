import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {cp,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {preview} from './ordinary-preview.mjs';
import {decodeSSE} from '../../agent-core/actions/transport.mjs';
import {registerTools,registerTurn,registerView,startRegister,storyMapRead} from './place-register.mjs';

const query=args=>({status:'completed',finishReason:'tool_calls',message:{role:'assistant',content:null,
  tool_calls:[{type:'function',id:'map-reader',function:{name:'query_map',arguments:JSON.stringify(args)}}]}});
const final=content=>({status:'completed',finishReason:'stop',message:{role:'assistant',content}});
const read=async path=>JSON.parse(await readFile(path,'utf8'));

test('同一份登记按地点、送达时刻和类型呈卷；核卷不派发续答，不消费或改写世界事件',async()=>{
  const world=startRegister();world.clock=4;
  const fact=(id,node,kind,text,receivedAt)=>({id,node,kind,text,receivedAt,occurredAt:1});
  world.facts.push(fact('heard','鸡笼','rumor','海商转述船只停港',2),
    fact('order','大员','order','已签发核港旨意',2),
    fact('receipt','大员','receipt','已收到港册',4),
    fact('late','鸡笼','receipt','次日才到的港册',5),
    fact('inland','锦州','receipt','别处的兵册',2));
  const before=structuredClone(world);
  async function lookup(args,input='请呈卷'){
    const audit=[];let calls=0;
    const text=await registerTurn(world,input,async()=>{
      assert.equal(++calls,1,'核卷不应请求模型重写答案');return query(args);
    },audit,storyMapRead);
    assert.equal(audit.length,1);assert.equal(calls,input.startsWith('/查卷')?0:1);
    assert.deepEqual(world,before);
    return {text,result:audit[0].result};
  }
  const port=await lookup({node:'大员',use:'documents'});
  assert.match(port.text,/已签旨意｜已签发核港旨意/);assert.match(port.text,/已送达回执｜已收到港册/);
  assert.doesNotMatch(port.text,/别处|次日才到/);
  const island=await lookup({node:'台湾',use:'documents'});
  assert.deepEqual(island.result.entries.map(row=>row.evidence.map(f=>f.id)),[['order','receipt'],['heard'],[]]);
  const heard=await lookup({node:'鸡笼'});assert.match(heard.text,/未经核实的转述/);assert.match(heard.text,/尚无接收回执/);
  assert.deepEqual((await lookup(null,'/查卷 鸡笼')),heard);
  await assert.rejects(()=>registerTurn(world,'查卷',async()=>query({node:'鸡笼',use:'unsafe'}),[],storyMapRead),/读取用途无效/);
  await assert.rejects(()=>registerTurn(world,'查卷',async()=>query({node:'鸡笼',use:'documents',observer:'别处'}),[],storyMapRead),/地图地点无效/);
  let stages=0;
  const story=await registerTurn(world,'看过簿册，接着讲下去。',async packet=>{
    if(++stages===1)return query({node:'鸡笼',use:'scene'});
    const data=JSON.parse(packet.messages.at(-1).content);assert.deepEqual(data,heard.result);
    return final('海商说罢，殿里又响起翻页声。');
  },[],storyMapRead);
  assert.equal(stages,2);assert.match(story,/翻页声/);
});

const openingRun=process.env.ANCIENT_OPENING_EVIDENCE, queryRun=process.env.ANCIENT_QUERY_EVIDENCE;
test('普通聊天回放真实开局及工具选择：无凭据时只采用本档核卷结果，读档后下一轮继承同一答复',
  {skip:!(openingRun&&queryRun)},async()=>{
  const dir=await mkdtemp(join(tmpdir(),'ancient-court-docket-'));
  const opening=await read(join(openingRun,'accepted.json'));
  const failed=await read(join(queryRun,'accepted.json'));
  const savedCall=decodeSSE(await readFile(join(queryRun,'provider-1.sse'),'utf8'),registerTools);
  assert.match(opening.turns[0].raw,/宁远十三营军士缺饷已达四月/);
  assert.match(failed.turns.at(-1).raw,/昨日酉正一刻送抵通政司/);
  const packets=[];let host;
  async function api(path,body){
    const res=await fetch(host.origin+'/rpg-app/ancient/chat/api/sessions/'+opening.id+path,body?{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    const data=await res.json();assert.ok(res.ok,JSON.stringify(data));return data;
  }
  async function send(s,input){
    const m=await api('/memory-palace');
    const next=await api('/send',{operationId:randomUUID(),input,expectedSessionRevision:s.revision,expectedMemoryRevision:m.memoryRevision,expectedContextPolicyRevision:m.effectivePolicy.revision});
    assert.equal(next.turns.length,s.turns.length+1);return next;
  }
  try{
    await cp(join(openingRun,'saves'),dir,{recursive:true});
    host=await preview(dir,0,true,async packet=>{
      packets.push(packet);return packets.length===1?savedCall:final(failed.turns.at(-1).raw);
    });
    const initial=await api('');
    const s=await send(initial,failed.turns.at(-1).input);
    assert.equal(packets.length,1);
    const t=s.turns.at(-1);
    assert.match(t.raw,/宁远，簿上登记为明/);assert.match(t.raw,/无从核实/);
    assert.doesNotMatch(t.raw,/昨日酉正|太仓银二十万|通政司/);
    const event=await read(join(dir,'archive-chat/ancient/offline/atlas-offline-evidence',t.requestId+'.json'));
    assert.equal(event.toolResults[0].tool_call_id,savedCall.message.tool_calls[0].id);
    assert.deepEqual(event.toolResults[0].result.entries[0].evidence,[]);
    assert.deepEqual(s.frontier.locations,registerView(startRegister()).locations);
    assert.deepEqual(s.frontier.known,[]);
    await host.close();host=await preview(dir,0,true,async p=>{packets.push(p);return final('王承恩合上卷宗。');});
    const reopened=await api('');
    assert.deepEqual(reopened.turns,s.turns);
    await send(reopened,'继续朝议。');
    assert.equal(packets.length,2);
    const basis=reopened.turns.at(-1).atlasLookup;
    assert.deepEqual(basis.entries,[{node:'宁远',holder:'明',evidence:[]}]);
    assert.equal(basis.asOf,event.toolResults[0].result.scope.asOf);
    assert.ok(packets[1].messages[0].content.includes(JSON.stringify(basis)));
    assert.ok(packets[1].messages.some(m=>m.role==='assistant'&&m.content===t.raw));
    assert.ok(!JSON.stringify(packets[1].messages).includes('昨日酉正一刻送抵通政司'));
    if(process.env.ANCIENT_DOCKET_REPORT)await writeFile(process.env.ANCIENT_DOCKET_REPORT,JSON.stringify({
      mode:'offline-replay',providerCalls:0,question:failed.turns.at(-1).input,
      originalReply:failed.turns.at(-1).raw,savedToolCall:savedCall.message.tool_calls.map(({id,function:fn})=>({id,function:fn})),
      adoptedReply:t.raw,lookup:event.toolResults[0],afterReload:reopened.turns.at(-1).atlasLookup,
      followupSystemIncludesBasis:true,known:s.frontier.known,locations:s.frontier.locations
    },null,2),{flag:'wx'});
  }finally{await host?.close();await rm(dir,{recursive:true,force:true});}
});

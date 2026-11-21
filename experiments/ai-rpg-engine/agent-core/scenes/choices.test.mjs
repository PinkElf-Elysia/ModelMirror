import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {decodeSSE, simulatedWire} from '../actions/transport.mjs';
import {mkdtemp, mkdir, readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startServer} from './server.mjs';

async function fixture(t) {
  const root = fileURLToPath(new URL('../../.rpg03-work/r10-tests/', import.meta.url));
  await mkdir(root, {recursive:true});
  const directory = await mkdtemp(join(root, 'scene-'));
  const open = () => startServer({directory, port:0, offlinePreview:true, delayMs:0, sceneChoices:true});
  let app = await open();
  t.after(() => app.close());
  async function request(path, data) {
    const response = await fetch(new URL('/api/runs' + path, app.url), data && {
      method:'POST', headers:{'content-type':'application/json','x-rpg-scene':'offline-preview'},
      body:JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    return result;
  }
  let run;
  return {
    get app(){return app;}, request,
    async start(adapterId='cultivation'){const v=await request('',{operationId:'start',adapterId});run='/'+v.id;return v;},
    async reopen(){await app.close();app=await open();return request(run);},
    view:()=>request(run),
    post:(name,data)=>request(run+'/'+name,data),
    edit:(v,data)=>request(run+'/choice-edit',{revision:v.choices.revision,...data})
  };
}
const selfUse = '我决定自己服用 2 份养息丹。';
const quantity = v => v.world.resources[0].quantity;
const last = v => v.choices.saved.at(-1);
const propose = v => {
  const [proposalId, row] = Object.entries(v.world.proposals).at(-1);
  return {operationId:'execute-'+proposalId, revision:v.world.revision, proposalId, proposalHash:row.proposal.hash};
};

test('saved edited words → one task → confirmed receipt → scoped speech → reopen and continue', async t => {
  const f=await fixture(t); let v=await f.start();
  const initial=structuredClone(v.world.state);
  v=await f.edit(v,{kind:'suggest',goal:'我想向他表达谢意。'});
  assert.ok(v.choices.result.question);
  v=await f.edit(v,{kind:'suggest',goal:v.choices.result.goal,answer:'周禾'});
  v=await f.edit(v,{kind:'save',optionId:'thanks',text:selfUse});
  const choice=last(v);
  assert.notEqual(choice.modelSuggestion.text,choice.text);
  assert.deepEqual(v.world.state,initial); assert.equal(v.tasks.length,0);
  assert.equal(last(await f.reopen()).key,choice.key);
  const send=()=>f.post('choice-send',{key:choice.key});
  const [first,duplicate]=await Promise.all([send(),send()]);
  assert.equal(last(first).taskId,last(duplicate).taskId);
  assert.equal(first.tasks.length,1);assert.equal(first.tasks[0].input,selfUse);
  assert.equal(quantity(first),5);
  const confirmation=propose(first);
  v=await f.post('confirm',confirmation); assert.equal(quantity(v),3);
  v=await f.post('confirm',confirmation); assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
  assert.equal(last(v).eventId,confirmation.operationId);
  const expected=structuredClone(initial);
  const event=v.world.events.find(e=>e.id===confirmation.operationId);
  assert.equal(event.changes.length,1);
  assert.deepEqual([event.changes[0].before,event.changes[0].after],[5,3]);
  const item=expected.items.find(x=>x.id==='medicine');
  item.quantity=3;
  assert.deepEqual(v.world.state,expected);
  const after=await f.reopen();assert.equal((await send()).budget.calls,after.budget.calls);
  v=await f.post('narrate',{});
  assert.ok(v.world.turns.at(-1).body);
  v=await f.post('submit',{operationId:'ask',ownerId:'participant-keeper',input:'查询登记余量多少？'});
  assert.match(v.tasks.at(-1).body,/3/);assert.equal(v.tasks.at(-1).stages.length,2);
  v=await f.post('adopt',{taskId:v.tasks.at(-1).id});
  v=await f.post('submit',{operationId:'visitor',ownerId:'participant-visitor',input:'查询登记余量多少？'});
  assert.equal(v.tasks.at(-1).stages.at(-1).queryResults[0].result.status,'unavailable');
  v=await f.post('dismiss',{taskId:v.tasks.at(-1).id});
  const reopened=await f.reopen();assert.equal(reopened.budget.calls,v.budget.calls);
  v=await f.post('submit',{operationId:'next',ownerId:'world-dialogue',input:'继续眼前的场景'});
  assert.match(v.world.turns.at(-1).body,/沈砚/);assert.match(v.world.turns.at(-1).body,/3/);
  assert.deepEqual(v.world.state,expected);assert.equal(last(v).key,choice.key);
});

test('structured gift, dismissed directions, oral thanks, stale intent and isolated checkpoint', async t=>{
  const f=await fixture(t);let v=await f.start();
  v=await f.edit(v,{kind:'suggest',goal:'想感谢周禾。'});
  v=await f.edit(v,{kind:'save',optionId:'gift',quantity:2,text:'我想赠送周禾2份养息丹。'});
  assert.ok(last(v).plan);await assert.rejects(f.post('choice-send',{key:last(v).key}),/INTENT_ONLY/);
  v=await f.edit(v,{kind:'dismiss'});
  assert.equal(v.choices.result,null);assert.equal(quantity(v),5);assert.equal(v.tasks.length,0);
  v=await f.edit(v,{kind:'save',text:selfUse});const stale=last(v);
  v=await f.edit(v,{kind:'save',text:'我想口头向周禾道谢。'});const words=last(v);
  v=await f.post('choice-send',{key:words.key});assert.equal(quantity(v),5);assert.equal(v.tasks.length,1);
  assert.equal(Object.keys(v.world.proposals).length,0);assert.equal(v.scene.entries.length,0);
  await assert.rejects(f.post('choice-send',{key:stale.key}),/STALE/);
  await f.post('checkpoint',{operationId:'after-thanks'});
  const child=await f.post('fork',{operationId:'branch',checkpointId:'after-thanks'});
  assert.equal(quantity(child),5);assert.deepEqual(child.choices.saved,[]);
  await assert.rejects(f.request('/'+child.id+'/choice-send',{key:words.key}),/SCOPE/);
  v=await f.edit(v,{kind:'save',text:stale.text}); // review and save against current scene
  v=await f.post('choice-send',{key:last(v).key});
  const c=propose(v);
  v=await f.post('cancel',{...c,operationId:'abandon'});
  assert.equal(last(v).proposalStatus,'cancelled'); assert.equal(quantity(v),5);
  assert.equal(v.world.events.filter(e=>e.kind==='action').length,0);
});

test('edited gift and negative self-use never produce a self-consumption proposal', async t=>{
  const f=await fixture(t);let v=await f.start();
  for(const text of ['把 2 份养息丹送给周禾，给他服用，我自己不服用。','我自己不服用 2 份养息丹。']){
    v=await f.edit(v,{kind:'suggest',goal:'感谢周禾'});
    v=await f.edit(v,{kind:'save',optionId:'gift',text});
    v=await f.post('choice-send',{key:last(v).key});
    assert.equal(v.tasks.at(-1).input,text);assert.equal(last(v).status,'completed');
    assert.deepEqual(v.world.proposals,{});assert.equal(quantity(v),5);
    assert.equal(v.world.events.filter(e=>e.kind==='action').length,0);
  }
});

test('same native Gemini proposal after a read: gift remains dialogue; explicit self-use settles once', async t=>{
  const f=await fixture(t);
  const calls=[
    {id:'call_1217960083848929203',name:'query_local_state',args:{queries:[{domain:'items',id:'medicine'},{id:'zhou',domain:'relations'}]}},
    {id:'call_4060224486759331143',name:'prepare_action',args:{quantity:2,actionId:'use_medicine',itemId:'medicine'}}
  ];
  const wires=await Promise.all(calls.map(async (c,i)=>{
    if(process.env.R10_REPLAY_DIR){
      const file=join(process.env.R10_REPLAY_DIR,i?'real-resume-01':'real-01','stage-1.json');
      const bytes=await readFile(file);
      assert.equal(createHash('sha256').update(bytes).digest('hex'),[
        '4d6bd9bedd93ac142db467aaf16450ffff5bd3470485ac2ea4803de2593aa3ae',
        '75056625c58c16e369c7793d5b00cf087464da69a8762c6df3d6cfbaa7cd1105'
      ][i]);
      return JSON.parse(bytes).response.body.sse;
    }
    return simulatedWire({finishReason:'tool_calls',message:{role:'assistant',content:null,tool_calls:[
      {id:c.id,type:'function',function:{name:c.name,arguments:JSON.stringify(c.args)}}]}});
  }));
  const replay=async (_session,_id,request,meta)=>{
    const index=meta.stage-1;
    assert.match(request.messages[0].content,/use_medicine 仅供玩家本人服药/);
    const reply=decodeSSE(wires[index],request.tools);
    assert.equal(reply.message.tool_calls[0].id,calls[index].id);
    if(index===1){
      const result=request.messages.at(-1);
      assert.equal(result.tool_call_id,calls[0].id);
      assert.equal(JSON.parse(result.content).results[0].entries[0].quantity,5);
    }
    return {...reply,provider:'offline-captured-reply'};
  };
  let v=await f.start();
  const before=structuredClone(v.world.state);
  const gift='把 2 份养息丹送给周禾，给他服用，我自己不服用。';
  for(const text of [gift,'送给周禾，我自己不服用','我决定自己服用 1 份养息丹。',selfUse]){
    f.app.runtime.ports.get('cultivation').transport.world.generate=replay;
    v=await f.edit(v,{kind:'save',text});
    v=await f.post('choice-send',{key:last(v).key});
    assert.deepEqual(v.world.state,before);
    assert.equal(v.tasks.at(-1).stages.length,2);
    if(text!==selfUse){
      assert.deepEqual(v.world.proposals,{});
      assert.equal(v.tasks.at(-1).adoption,'completed');assert.equal(v.world.pending,null);
      assert.match(v.world.turns.at(-1).body,/丹药仍按原数登记/);
      assert.equal(v.world.events.filter(e=>e.kind==='action').length,0);
      v=await f.reopen();assert.deepEqual(v.world.proposals,{});
    }
  }
  assert.equal(v.tasks.at(-1).adoption,'proposed');
  const confirmation=propose(v);
  v=await f.post('confirm',confirmation);v=await f.reopen();
  v=await f.post('confirm',confirmation);
  const expected=structuredClone(before);expected.items.find(x=>x.id==='medicine').quantity=3;
  assert.deepEqual(v.world.state,expected);
  assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
});

test('unsettled medicine gift: captured final prose and all its relationship effects stay out of the story', async t=>{
  const f=await fixture(t);
  let v=await f.start();
  const before=structuredClone(v.world.state);
  const gift='把 2 份养息丹送给周禾，给他服用，我自己不服用。';
  const delta=[
    {type:'relation',id:'zhou',axis:'relation',event:'help',delta:8,reason:'受赠丹药'},
    {type:'relation',id:'zhou',axis:'trust',event:'promise',delta:3,reason:'受赠丹药'},
    {type:'relationInfo',id:'zhou',field:'impression',value:'赠药解危',reason:'受赠丹药'},
    {type:'relationInfo',id:'zhou',field:'lastContact',value:'药圃·赠药调息',reason:'受赠丹药'}
  ];
  let queryWire, finalWire;
  if(process.env.R10_REPLAY_DIR){
    const load=async (file,digest)=>{
      const bytes=await readFile(join(process.env.R10_REPLAY_DIR,file));
      assert.equal(createHash('sha256').update(bytes).digest('hex'),digest);
      return JSON.parse(bytes);
    };
    queryWire=(await load('real-01/stage-1.json','4d6bd9bedd93ac142db467aaf16450ffff5bd3470485ac2ea4803de2593aa3ae')).response.body.sse;
    finalWire=(await load('real-gift-policy-01/raw.json','8b07b803bc6bf9b4fb12c90aad4cde3b77b155ab9247da3d0849a4fae342b6f5')).calls[0].response.body.sse;
  }else{
    const call={id:'call_1217960083848929203',type:'function',function:{name:'query_local_state',arguments:JSON.stringify({queries:[{domain:'items',id:'medicine'},{domain:'relations',id:'zhou'}]})}};
    queryWire=simulatedWire({finishReason:'tool_calls',message:{role:'assistant',content:null,tool_calls:[call]}});
    finalWire=simulatedWire({finishReason:'stop',message:{role:'assistant',content:'周禾仰头便将两枚养息丹送入口中。\n<state_delta>'+JSON.stringify({operations:delta})+'</state_delta>'}});
  }
  const port=f.app.runtime.ports.get('cultivation');
  let rawText;
  port.transport.world.generate=async (_session,_id,req,meta)=>{
    const result=decodeSSE(meta.stage===1?queryWire:finalWire,req.tools);
    if(meta.stage===2){
      assert.equal(req.messages.at(-1).tool_call_id,'call_1217960083848929203');
      rawText=result.message.content;
      assert.match(rawText,/两枚养息丹送入口中/);
      assert.equal(JSON.parse(rawText.match(/<state_delta>\s*([\s\S]*?)<\/state_delta>/u)[1]).operations.length,4);
    }
    return {...result,provider:'captured-offline'};
  };
  v=await f.edit(v,{kind:'save',text:gift});
  const key=last(v).key;
  v=await f.post('choice-send',{key});
  assert.deepEqual(v.world.state,before);
  assert.deepEqual(v.world.proposals,{});
  assert.equal(v.world.events.filter(e=>e.kind==='action').length,0);
  const turn=v.world.turns.at(-1);
  assert.equal(turn.input,gift);assert.deepEqual(turn.changes,[]);
  assert.match(turn.body,/打算|意向|意图/);assert.doesNotMatch(turn.body,/送入口中|受赠|赠药调息/);
  const saved=await port.store.read(v.worldId);
  const op=saved.operations[turn.id];
  assert.equal(op.stages.at(-1).result.message.content,rawText);
  assert.equal(op.receipt.provider,'host-clarification');
  const budget=v.budget.calls;
  v=await f.reopen();v=await f.post('choice-send',{key});
  assert.equal(v.world.turns.length,1);assert.equal(v.budget.calls,budget);
  assert.deepEqual(v.world.state,before);
  const current=f.app.runtime.ports.get('cultivation').transport.world;
  const original=current.generate.bind(current);
  current.generate=async (session,id,req,meta)=>{
    const history=req.messages.filter(m=>m.role==='assistant').map(m=>m.content).join('\n');
    assert.ok(history.includes(turn.body));assert.doesNotMatch(history,/送入口中|药圃·赠药调息/);
    return original(session,id,req,meta);
  };
  v=await f.post('submit',{operationId:'after-intent',ownerId:'world-dialogue',input:'继续聊聊药圃的天气。'});
  assert.equal(v.tasks.at(-1).adoption,'completed');assert.deepEqual(v.world.state,before);
});

test('prose alone cannot settle a gift; an ordinary conversation can change an impression', async t=>{
  const f=await fixture(t);
  let v=await f.start();
  const before=structuredClone(v.world.state);
  const world=f.app.runtime.ports.get('cultivation').transport.world;
  const impression='耐心讲解养息丹用法的同门。';
  world.generate=async (_session,_id,_req,{input})=>{
    const lesson=input.includes('讲解');
    const operations=lesson?[{type:'relationInfo',id:'zhou',field:'impression',value:impression,reason:'当面讲解用法'}]:[];
    const body=lesson?'你讲解药理，周禾认真听着。':'周禾接过丹药吞下，气息平稳下来。';
    return {status:'completed',finishReason:'stop',provider:'offline',message:{role:'assistant',content:body+'\n<state_delta>'+JSON.stringify({operations})+'</state_delta>'}};
  };
  v=await f.post('submit',{operationId:'gift-only',ownerId:'world-dialogue',input:'我想把养息丹给周禾。'});
  assert.deepEqual(v.world.state,before);assert.match(v.world.turns.at(-1).body,/打算/);
  assert.doesNotMatch(v.world.turns.at(-1).body,/接过|吞下/);
  v=await f.post('submit',{operationId:'lesson',ownerId:'world-dialogue',input:'我给周禾讲解养息丹的用法。'});
  const expected=structuredClone(before);expected.relations.find(x=>x.id==='zhou').impression=impression;
  assert.deepEqual(v.world.state,expected);assert.match(v.world.turns.at(-1).body,/认真听着/);
  assert.equal(v.world.events.filter(e=>e.kind==='action').length,0);
});

test('lost task receipt reconciles the saved request after restart; never re-dispatch', async t=>{
  const f=await fixture(t);let v=await f.start();
  v=await f.edit(v,{kind:'save',text:selfUse});const key=last(v).key;
  const transport=f.app.runtime.ports.get('cultivation').transport;
  const generate=transport.generate.bind(transport);
  transport.generate=async packet=>{await generate(packet);return {status:'unknown',error:'TEST_LOST_RESPONSE'};};
  v=await f.post('choice-send',{key});
  assert.equal(last(v).status,'unknown'); const calls=v.budget.calls,taskId=last(v).taskId;
  v=await f.reopen();assert.equal(last(v).status,'unknown');
  assert.equal((await f.post('choice-send',{key})).budget.calls,calls);
  v=await f.post('recover',{taskId});
  assert.equal(last(v).status,'proposed');assert.equal(v.budget.calls,calls);assert.equal(quantity(v),5);
});

test('lighthouse uses the same scene executor and does not offer cultivation choices',async t=>{
  const f=await fixture(t);let v=await f.start('lighthouse');
  assert.equal(v.choices,undefined);
  await assert.rejects(f.post('choice-edit',{kind:'suggest',goal:'向周禾致谢',revision:0}),/UNAVAILABLE/);
  v=await f.post('submit',{operationId:'oil',ownerId:'world-dialogue',input:'添油 2'});
  v=await f.post('confirm',propose(v));v=await f.post('narrate',{});
  const fresh=await f.reopen();assert.equal(quantity(fresh),3);assert.deepEqual(fresh.world,v.world);
});

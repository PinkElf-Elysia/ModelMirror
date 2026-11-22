import test from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {parameterMock,token} from '../revisions/parameter-mock.mjs';
import {source} from '../revisions/test-support.mjs';
import {digest,copy} from '../revisions/contracts.mjs';
import {ContextStore} from '../manual-memory/store.mjs';
import {openContextControl} from './composition.mjs';
import {LiveDesk} from './desk.mjs';

async function setup(t,options={}){
  const mock=await parameterMock(t,{live:async url=>{
    const cursor=Number(url.searchParams.get('cursor'));return {format:'modelmirror.rpg.live-progress/1',cursor,next:cursor,text:'',status:'active',stopRequested:false};
  },text:p=>{
    if(p.purpose==='story')return '来自中性夹具的回应';
    const rows=JSON.parse(p.messages[1].content),{id,revisionId}=rows.at(-1);
    return JSON.stringify({content:'待核对的约定',refs:[{id,revisionId}]});
  },...options.mock});
  const choice=await mock.control.selectParameters(mock.selection('story',{maxOutputTokens:{mode:'explicit',value:128}}));
  const model={id:'synthetic/story',label:'中性夹具',choice,contextTokens:256000,outputTokens:128,
    price:{revision:'fixture',overheadTokens:256,inputMicroUsdPerMillion:1000000,outputMicroUsdPerMillion:1000000}};
  const dir=process.env.CHARACTER_R4C_TEST_ROOT??tmpdir();await mkdir(dir,{recursive:true});const directory=await mkdtemp(join(dir,'lc-'));
  const context=await openContextControl({directory:join(directory,'control'),upstream:mock.origin,serviceToken:token,models:[model],
    authorization:{id:'mock',calls:8,microUsd:100000,initial:{calls:0,microUsd:0}}});
  const s=source();s.kind=options.kind??'group';s.budget.calls=8;s.current.members[1].model=copy(model);
  s.messages[2].content='缺席的秘密';s.current.members[1].intervals=[{from:1,until:3},{from:4,until:null}];
  delete s.sourceHash;s.sourceHash=digest(s);await context.guard.register(s.id,s.budget);
  const memory=await ContextStore.open({directory:join(directory,'memory'),sources:{read:async()=>copy(s),verify:async()=>copy(s)},
    parameters:context.parameters,authorization:{id:'mock',mode:'controlled',...s.budget}});
  const v=await memory.importSource({operationId:'load',expectedRevision:null,reference:{kind:'context',familyId:'f',branchId:'b'}});
  const args={directory:join(directory,'live'),memory,context,proposalLimit:{calls:2,microUsd:16000}};
  let desk=await LiveDesk.open(args);
  const key=await desk.create({operationId:'start',familyId:v.id,branchId:v.branchId,expectedRevision:v.revision});
  t.after(async()=>{await desk.close();await memory.close();await context.close();});
  const view=()=>desk.view(key,'actor');
  let n=0;
  async function act(action,payload,op){
    const result=await desk.act(key,{operationId:op??'op-'+ ++n,expectedRevision:(await view()).revision,action,actor:'actor',payload});
    while(desk.jobs.size)await Promise.all([...desk.jobs.values()]);return result;
  }
  return {key,mock,context,memory,v,view,act,get desk(){return desk;},async restart(){await desk.close();desk=await LiveDesk.open(args);}};
}
test('on-demand proposal stays private and outside chat until reviewed; only visible sources are sent',async t=>{
  const a=await setup(t);const before=digest(await a.memory.read(a.v.id));
  await a.act('propose',{});const proposal=(await a.view()).requests[0];
  assert.equal(proposal.reviewState,'pending');assert.doesNotMatch(JSON.stringify(a.mock.tasks[0]),/缺席的秘密|derived-secret/);
  assert.equal(a.mock.tasks[0].purpose,'memory');
  await a.act('send',{input:'继续商量'});assert.doesNotMatch(JSON.stringify(a.mock.tasks[1].messages),/待核对的约定/);
  await a.act('adopt',{requestId:proposal.id,content:'人工纠正后的约定'});
  await a.act('send',{input:'请回忆'});assert.match(JSON.stringify(a.mock.tasks[2].messages),/人工纠正后的约定/);
  await a.restart();assert.equal((await a.view()).requests[0].reviewState,'adopted');
  assert.equal(digest(await a.memory.read(a.v.id)),before);assert.equal((await a.context.guard.status()).usedCalls,3);
});
test('automatic proposal counts six new visible confirmed messages and never restarts generation',async t=>{
  const a=await setup(t);await a.act('auto',{enabled:true});
  for(let i=0;i<3;i++){
    await a.act('send',{input:'问题 '+i});const r=(await a.view()).requests.filter(r=>r.candidate).at(-1);
    assert.ok(r);await a.act('select',{requestId:r.id});
  }
  assert.deepEqual(a.mock.tasks.map(p=>p.purpose),['story','story','story','memory']);
  assert.equal(JSON.parse(a.mock.tasks[3].messages[1].content).length,6);
  await a.restart();assert.equal(a.mock.tasks.length,4);
  await a.act('reject',{requestId:(await a.view()).requests.at(-1).id});
  assert.equal((await a.view()).requests.at(-1).reviewState,'rejected');
});
test('lost response retains reservation; restart and explicit lookup never POST twice',async t=>{
  const a=await setup(t,{mock:{dropPurpose:'memory'}});await a.act('propose',{});
  let v=await a.view();assert.equal(v.requests[0].status,'unknown');assert.equal((await a.context.guard.status()).pending.length,1);
  await a.restart();await a.desk.recover(a.key,'actor',v.requests[0].id);
  assert.equal((await a.view()).requests[0].reviewState,'pending');assert.equal(a.mock.tasks.length,1);
});
test('invalid references cannot be adopted; proposal sub-budget survives rejection',async t=>{
  const a=await setup(t,{mock:{text:()=>JSON.stringify({content:'未验证',refs:[{id:'missing',revisionId:'invented'}]})}});
  await a.act('propose',{});await a.act('propose',{});assert.equal(a.mock.tasks.length,1);
  await a.act('send',{input:'新来源一'});await a.act('propose',{});await a.act('send',{input:'新来源二'});
  assert.ok((await a.view()).requests.filter(r=>r.kind==='proposal').every(r=>r.status==='invalid-proposal'&&!r.review));
  await assert.rejects(a.act('propose',{}),/PROPOSAL_BUDGET/);assert.equal(a.mock.tasks.length,4);
});

test('visible delta before receipt, stop once, durable original attachment when control is offline',async t=>{
  let release,started,offline=false,stopped=false;
  const gate=new Promise(r=>release=r),waiting=new Promise(r=>started=r);
  const a=await setup(t,{mock:{
    async onTask(p,f){started();await gate;const v=f.results.get(p.sessionId+'/'+p.requestId);
      v.status=v.receipt.status='unknown';v.receipt.error='rpg_authoring_host_stopped';v.receipt.finishReason=null;},
    live(url,method){if(offline)return {};if(method==='POST'){stopped=true;release();}
      const cursor=Number(url.searchParams.get('cursor')),prefix=[...'中🌱'];
      return {format:'modelmirror.rpg.live-progress/1',cursor,next:2,text:prefix.slice(cursor).join(''),status:stopped?'stopping':'active',stopRequested:stopped};}
  }});
  const v=await a.view(),cmd={operationId:'send-once',actor:v.actor,expectedRevision:v.revision,action:'send',payload:{input:'提议先等等'}};
  await a.desk.act(a.key,cmd);await waiting;
  try{
    await a.desk.act(a.key,cmd);await assert.rejects(a.desk.act(a.key,{...cmd,payload:{input:'changed'}}),/COLLISION/);
    const rid=(await a.view()).requests[0].id;
    assert.equal((await a.desk.inspect(a.key,'actor',rid,0)).text,'中🌱');
    assert.equal((await a.desk.inspect(a.key,'actor',rid,2)).text,'');
    await assert.rejects(a.desk.inspect(a.key,'different',rid,0,true),/SCOPE/);
    await a.desk.inspect(a.key,'actor',rid,0,true);
    await Promise.all([...a.desk.jobs.values()]);await a.restart();offline=true;
    const latest=await a.view();
    assert.equal(latest.requests[0].status,'unknown');assert.equal(latest.requests[0].candidate,null);
    assert.doesNotMatch(JSON.stringify(latest.history),/中🌱/);
    assert.doesNotMatch(await a.desk.transcript(a.key,'actor'),/中🌱|缺席的秘密|fixture|sourceRequestHash/);
    assert.equal((await a.desk.inspect(a.key,'actor',rid,0)).text,'中🌱');
    assert.equal(a.mock.tasks.length,1);assert.equal((await a.context.guard.status()).pending.length,1);
  }finally{release();}
});
for(const action of ['send','propose'])test('restart before dispatch preserves intent until explicit close: '+action,async t=>{
  const a=await setup(t);
  a.desk.launch=()=>{}; // Simulate process loss immediately after durable prepare.
  const v=await a.view();await a.desk.act(a.key,{operationId:'interrupted',expectedRevision:v.revision,action,actor:'actor',
    payload:action==='send'?{input:'准备阶段'}:{}});
  await a.restart();const rid=(await a.view()).requests[0].id;
  assert.equal((await a.view()).requests[0].status,'prepared');
  await a.desk.inspect(a.key,'actor',rid,0,true);
  assert.equal((await a.view()).requests[0].status,'failed');
  assert.equal((await a.context.guard.status()).usedCalls,0);assert.equal(a.mock.tasks.length,0);
});

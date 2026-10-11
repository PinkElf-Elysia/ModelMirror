import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {createCardContext} from '../../studio/card-context.mjs';
import {assembleHistoryRequest} from '../../studio/history-assembly.mjs';
import {initialHistoryState,effectiveHistoryPolicy} from '../../studio/history-state.mjs';
import {initialSummaryState,sealSummary,modelSummaryVersion,withSummaryVersion,SUMMARY_WRAPPER} from '../../studio/summary-state.mjs';
import {initialMemoryState,sealMemory} from '../../studio/memory-state.mjs';
import {initialMemoryTasks,memoryContextPolicy} from '../../studio/memory-task-state.mjs';
import {MEMORY_WRAPPER,settleMemoryStory} from '../../studio/memory-assembly.mjs';
import {changeEntries} from '../../plugins/memory-palace.mjs';
import {initialModelState,controlledChoice} from '../../studio/model-runtime.mjs';
import {canonical,sha} from '../../plugins/catalog.mjs';
import {godAdapter,godRuntime} from '../lib/adapter.mjs';
import {newSetup} from '../lib/setup.mjs';
import {assemble,loadFrozen} from '../lib/assembly.mjs';
import {assembleWithOutputSuffix,loadOutputSuffix} from '../lib/output-suffix.mjs';
const context=createCardContext({cardId:'god',assemble:godAdapter.assemble});
const supplemented=createCardContext({cardId:'god',assemble:assembleWithOutputSuffix});
const model=controlledChoice({model:'synthetic/context-fixture',selectionId:'fixture',selectionRevision:'v1'});
const auth=enabled=>({enabled,revision:sha('fixture-'+enabled)});
const policy=(s,{m1=false,m2=false,m3=false}={})=>memoryContextPolicy(s,{history:auth(m1),summary:auth(m2),memory:auth(m3)});
function fresh(){
 const setup=newSetup();setup.user.姓名='虚构神明';setup.opening='初临钟楼';
 return {...godAdapter.create({setup,mode:'offline'}),id:'fixture',revision:0,history:[],turns:[],requests:{},
 runtime:{hash:godRuntime.hash,descriptor:godRuntime.descriptor},modelState:initialModelState(model),historyWindow:initialHistoryState(),rollingSummary:initialSummaryState(),memoryPalace:initialMemoryState(),memoryTasks:initialMemoryTasks()};
}
function window(s,n,include=false){
 const input={operationId:'window',expectedSessionRevision:s.revision,expectedConfigRevision:0,turns:n,includeInitialCharacter:include};
 s.historyWindow={...initialHistoryState(),revision:1,config:{turns:n,includeInitialCharacter:include},operations:{window:{hash:sha(canonical(input)),input,status:'complete',configRevision:1}}};
}
function seed(n=4){
 const s=fresh();for(let i=0;i<n;i++){const input=i?'原始玩家输入'+i:s.characterText,raw='第'+(i+1)+'回合：钟楼钟声。<details><summary>记忆</summary>承诺未兑现。</details>';
 s.history.push({role:'user',content:input},{role:'assistant',content:raw});s.turns.push({requestId:'seed-'+i,input,raw,rawHash:sha(raw)});}s.revision=n;return s;
}
function memory(s,keyword='钟楼',content='钟楼的钟已停止，修复尚未完成。'){
 const value={name:'钟楼记录',keywords:[keyword],match:'any',roles:['user','assistant'],content,enabled:true};
 s.memoryPalace=sealMemory({...s.memoryPalace,entries:changeEntries(s.memoryPalace.entries,{action:'create',value},'memory-'+s.memoryPalace.entries.length)});
}
function summarize(s,text='神明发现钟楼失修，承诺仍未兑现。'){
 s.rollingSummary=sealSummary({...s.rollingSummary,config:{timing:'before',model}});
 const v=modelSummaryVersion(s,{raw:text,finishReason:'stop',model,receipt:{requestId:'summary-'+s.turns.length,requestHash:sha('source'),responseHash:sha(text)},expectedActiveVersionId:s.rollingSummary.activeVersionId,targetThrough:s.turns.length-1});
 s.rollingSummary=withSummaryVersion(s,v);
}

test('user supplement stays out of system and UI input, is appended once per turn under all memory policies',()=>{
 const suffix=loadOutputSuffix(),append=x=>x+'\n\n'+suffix.text;
 assert.equal(sha(suffix.text),suffix.textIntegrity.sha256);
 assert.equal(suffix.source.sha256,'d0730384048886d4b22b7e752f7ea5b6f0073b722f9d9a9f53f75016317cda9d');
 const first=fresh(),opening=supplemented.memory(first,first.characterText,policy(first));
 assert.deepEqual(opening.messages,[{role:'system',content:loadFrozen().system},{role:'user',content:append(first.characterText)}]);
 assert.equal(opening.input,first.characterText);
 const s=seed();s.history=s.history.map(m=>m.role==='user'?{...m,content:append(m.content)}:m);
 window(s,1,false);memory(s);summarize(s);const before=canonical(s);
 for(const flags of [{},{m1:true},{m2:true},{m3:true},{m1:true,m3:true},{m2:true,m3:true}]){
  const a=supplemented.memory(s,'返回钟楼',policy(s,flags));
  assert.equal(a.input,'返回钟楼');assert.equal(a.current,append('返回钟楼'));
  assert.ok(a.messages[0].content.startsWith(loadFrozen().system));
  assert.ok(!a.messages[0].content.includes(suffix.text));
  for(const m of a.messages.filter(m=>m.role==='user'))assert.equal(m.content.split(suffix.text).length,2);
  assert.deepEqual(a.messages.slice(1,-1),flags.m1||flags.m2?s.history.slice(-2):s.history);
  assert.equal(canonical(s),before);
 }
});
test('zero plugins and windows preserve God text; Earth default refuses another card',()=>{
 for(const n of [0,1,4]){
  const s=seed(n),input=n?' 继续\n原样 ':s.characterText,before=canonical(s);
  assert.deepEqual(context.memory(s,input,policy(s)).messages,assemble({input,history:s.history}).messages);
  assert.equal(canonical(s),before);
  assert.throws(()=>assembleHistoryRequest(s,input,effectiveHistoryPolicy(s,auth(false))),/CARD_CONTEXT_ADAPTER_REQUIRED/);
 }
 for(const n of [1,3,50])for(const include of [false,true]){
  const s=seed();window(s,n,include);const before=canonical(s),a=context.memory(s,'继续',policy(s,{m1:true}));
  assert.deepEqual(a.historyPolicy.selectedTurns,Array.from({length:Math.min(4,n)},(_,i)=>4-Math.min(4,n)+i+1));
  assert.equal(a.messages[0].content,loadFrozen().system);
  assert.equal(a.historyPolicy.initialSnapshotAdded,include&&n<4);
  assert.equal(a.current,'继续');assert.equal(canonical(s),before);
  if(!include||n>=4)assert.deepEqual(a.messages.slice(1,-1),s.history.slice(-Math.min(4,n)*2));
 }
});
test('M2/M3 combination is one system, exact coverage and raw recent history without persistent mutation',()=>{
 const s=seed();window(s,1,true);memory(s);memory(s,'仅摘要关键词','不应由摘要递归触发');
 summarize(s,'仅摘要关键词；神明答应修复钟楼。');const before=canonical(s);
 for(const enabled of [{m1:true,m3:true},{m2:true,m3:true},{m1:true,m2:true,m3:true}]){
  const a=context.memory(s,'返回钟楼',policy(s,enabled));
  assert.equal(a.messages.filter(x=>x.role==='system').length,1);
  assert.ok(a.messages[0].content.startsWith(loadFrozen().system));
  assert.ok(a.messages[0].content.includes('钟楼的钟已停止'));
  assert.ok(!a.messages[0].content.includes('不应由摘要递归触发'));
  assert.equal(a.current,'返回钟楼');
  if(enabled.m2){
   assert.equal(a.contextPolicy.coveredThrough,3);assert.deepEqual(a.contextPolicy.selectedTurns,[4]);
   assert.deepEqual(a.messages.slice(1,-1),s.history.slice(-2));
   assert.ok(a.messages[0].content.indexOf('【历史摘要 · 数据】')<a.messages[0].content.indexOf('【记忆宫殿·历史资料】'));
   assert.ok(!a.messages.slice(1).some(x=>x.content.includes('开局角色资料（原始快照）')));
  }
  assert.equal(canonical(s),before);
 }
 const noSummary=seed();assert.throws(()=>context.memory(noSummary,'继续',policy(noSummary,{m2:true})),/SUMMARY_POLICY_NOT_READY/);
});
test('adapter rejects wrong card, history changes, extra system and client-like serialized adapters',()=>{
 const s=seed(),p=policy(s);
 assert.throws(()=>createCardContext({cardId:'god',assemble:'javascript'}),/ADAPTER_INVALID/);
 assert.throws(()=>context.memory({...s,cardId:'earth'},'继续',p),/BINDING_INVALID/);
 for(const mutate of [r=>r.messages.splice(1,1),r=>r.messages.splice(1,0,{role:'system',content:'wrong'}),r=>r.messages[1].content='changed']){
  const invalid=createCardContext({cardId:'god',assemble:(s,i)=>{const r=godAdapter.assemble(s,i);mutate(r);return r;}});
  assert.throws(()=>invalid.memory(s,'继续',p),/ASSEMBLY_INVALID/);
 }
});
test('loopback wire and committed receipts reconstruct across rolling updates and later memory edits',async()=>{
 const directory=fileURLToPath(new URL('../../.rpg04-work/god-card/b3-context/',import.meta.url));await mkdir(directory,{recursive:true});
 const out=await mkdtemp(join(directory,'wire-')),wires=[];
 const server=createServer(async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);wires.push(JSON.parse(Buffer.concat(chunks)));res.setHeader('content-type','application/json');res.end(JSON.stringify({raw:'钟楼仍在修缮，第'+wires.length+'轮原文。'}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  const s=fresh();memory(s);
  for(let i=0;i<4;i++){
   if(i>=2)summarize(s,'第'+i+'轮之前：钟楼修复约定尚未完成。');
   const input=i?'第'+i+'次观察钟楼':s.characterText,p=policy(s,{m1:true,m2:i>=2,m3:true});
   const a=context.memory(s,input,p),requestId='wire-'+i,messagesHash=sha(canonical(a.messages));
   s.summaryTasks={operations:{[requestId]:{status:'pending',tasks:[{requestId}]}}};
   const response=await fetch('http://127.0.0.1:'+server.address().port,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({messages:a.messages})});
   const {raw}=await response.json();
   assert.deepEqual(wires.at(-1).messages,a.messages);
   await context.commitMemory(s,{requestId,selection:model,story:a,messagesHash},{raw,evidence:{record:{actualModel:model.model}}});
   settleMemoryStory(s,{status:'complete',tasks:[{purpose:'story',requestId,status:'complete'}]});delete s.summaryTasks;
   assert.equal(context.verifyMemory(s),true);
  }
  const original=canonical(s.history),oldHash=s.turns[2].memoryPolicy.requestHash;
  memory(s,'别名','父路线后加信息，不应进入旧回执');
  assert.equal(context.verifyMemory(s),true);assert.equal(canonical(s.history),original);
  assert.equal(s.turns[2].memoryPolicy.requestHash,oldHash);
  assert.equal(s.turns[3].contextPolicy.coveredThrough,2);
  assert.deepEqual(wires[3].messages.slice(1,-1),s.history.slice(4,6));
  const tampered=structuredClone(s);tampered.history[0].content+='tampered';
  assert.throws(()=>context.verifyMemory(tampered));
  await writeFile(join(out,'wire-proof.json'),JSON.stringify({kind:'synthetic-loopback-only; not production plugin task lifecycle',providerCalls:0,wires,turns:s.turns},null,2));
 }finally{await new Promise(r=>server.close(r));}
});

test('minimal non-Earth card shares summary commit and reconstruction without world or Earth fields',async()=>{
 const s=fresh();s.cardId='minimal';s.runtime={hash:sha('minimal-runtime'),descriptor:{cardId:'minimal'}};s.characterText='初始场景：空房间';delete s.world;delete s.params;
 const c=createCardContext({cardId:'minimal',assemble:(session,input)=>({messages:[{role:'system',content:'合成卡片系统'},...session.history,{role:'user',content:input}],current:input})});
 for(let i=0;i<3;i++){
  if(i===2)summarize(s,'已打开房门。');
  const p=policy(s,{m2:i===2}),a=c.summary(s,'观察'+i,p),requestId='summary-story-'+i;
  a.input='观察'+i;
  await c.commitSummary(s,{requestId,selection:model,story:a,messagesHash:sha(canonical(a.messages))},{raw:'合成回复'+i,evidence:{record:{actualModel:model.model}}});
  assert.equal(c.verifySummary(s),true);
 }
 assert.equal(s.turns[2].contextPolicy.coveredThrough,1);
 assert.equal(s.history[0].content,'观察0');
 assert.equal('world' in s,false);assert.equal('params' in s,false);
 const changed=structuredClone(s);changed.turns[0].input='改写';assert.throws(()=>c.verifySummary(changed),/SUMMARY_TURN_BINDING_INVALID/);
});

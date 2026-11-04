import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {JointBudget} from './joint-budget.mjs';
import {stageOperationId} from './joint-plan.mjs';
import {jointSessionId,validateJointSession} from './joint-session-state.mjs';
import {OfflineModelControl,modelCapacity} from './model-control.mjs';
import {memoryAt} from './memory-state.mjs';
import {memoryContextPolicy,assembleMemoryContext} from './memory-context.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6u-tests/',import.meta.url));await mkdir(root,{recursive:true});
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),reply=(text,finishReason='stop',status='completed')=>({status,text,finishReason});
const reject=(fn,code)=>assert.rejects(async()=>fn(),e=>{assert.equal(e.code,code,e.stack);return true;});
const id=jointSessionId('session'),opening='原始开局：雾港的一封信。',H='rpg.history-window',M2='rpg.rolling-summary',M3='rpg.memory-palace';
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve};};
const catalog={catalogId:'joint-fixture',models:['story','summary','memory'].map(id=>({
 selectionId:id,selectionRevision:sha256(id),modelId:'fixture/'+id,name:'离线 '+id,available:true,contextLength:256000,maxOutputTokens:16000,
 parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]},
}))};
const proposal={operations:[{type:'create',name:'港口承诺',keywords:['雾港','信'],match:'any',roles:['user','assistant'],content:'双方计划次日见面，尚未兑现。',sourceTurns:[1]}]};
function replies(options={}){
 const result={};
 for(const name of ['one','two','three','four','five','manual','retry']){
  result[name]=reply(name==='one'?'古老灯塔。<details>卡内总结：约定明天见面</details>':'完整原文 '+name);
  for(const phase of ['summary-before','summary-after'])result[stageOperationId(id,name,phase)]=reply(options.summaryText??'两人有一个未兑现的港口承诺。');
  for(const phase of ['compression-before','compression-after'])result[stageOperationId(id,name,phase)]=reply('压缩后：承诺尚未兑现。');
  result[stageOperationId(id,name,'memory-after')]=reply(options.memoryText??JSON.stringify(name==='one'?proposal:{operations:[]}));
 }
 return {...result,...options.replies};
}
async function fixture(t,options={}){
 const directory=await mkdtemp(join(root,'run-'));let store=await AuthoringStore.open(join(directory,'works'));
 const doc=blankDocument();doc.metadata.name='独立作品 '+randomUUID();doc.metadata.audience='male';doc.sources.system=' FROZEN SYSTEM\r\n';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 doc.enhancements.cg={categories:[],entries:[cgEntry(),cgEntry({id:'background',target:'background',mode:'regular',images:[{alias:'b',weight:100}]}),cgEntry({id:'later',keywords:['three'],target:'background',mode:'regular',images:[{alias:'a',weight:100}]})]};
 if(options.many)doc.enhancements.cg.entries=Array.from({length:33},(_,i)=>cgEntry({id:'many-'+i,mode:'regular',images:[{alias:'a',weight:100}]}));
 doc.settings.media.desktop='a';doc.settings.media.mobile='b';doc.settings.media.bgm='bgm';if(!options.legacyBgm)doc.settings.bgmAutoplay=true;
 const work=await store.create({operationId:'work',expectedRevision:null,document:doc});
 for(const [index,alias]of ['a','b'].entries())await store.putAsset(work.workId,{operationId:'asset-'+alias,expectedRevision:index,alias,fileName:alias+'.png',mediaType:'image/png',bytes:index?pngB:pngA});
 await store.putAsset(work.workId,{operationId:'audio',expectedRevision:2,alias:'bgm',fileName:'test.mp3',mediaType:'audio/mpeg',bytes:audio});
 const pub=await store.publish(work.workId,{operationId:'publish',expectedRevision:3}),reference={workId:work.workId,versionId:pub.result.versionId};
 const resolveRuntime=async ref=>{await options.beforeResolveRuntime?.();return openPublishedRuntime(store,ref);},modelControl=new OfflineModelControl(catalog);
 let host,control,provider,budget;
 async function open(faults=true){
  control=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:x=>host.lookupSession(x),resolveRuntime});
  provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:replies(options),fault:faults?options.providerFault:undefined});
  budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'shared',limit:options.limit??30,storageFault:faults?options.budgetFault:undefined});
  host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,modelControl,historyControl:control,fault:faults?options.fault:undefined,storageFault:faults?options.storageFault:undefined});
 }
 await open();await host.create({operationId:'session',expectedRevision:null,reference,openingText:opening,presetId:null});
 async function close(){await host.close();await control.close();await provider.close();await budget.close();}
 t.after(async()=>{await close();await store.close();});
 const f={directory,id,reference,resolveRuntime,get host(){return host;},get provider(){return provider;},get budget(){return budget;},get service(){return control.pluginService;},
  read:()=>host.read(id),idle:()=>host.waitForIdle(id),
  async select(purpose){const s=(await f.read()).session,c=modelControl.catalog();return host.configureModel({sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
   choice:{catalogRevision:c.revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});},
  async change(action,pluginId){const c=await f.service.catalog(),p=c.plugins.find(p=>p.id===pluginId);return f.service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
   ...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:(await host.lookupSession(id)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async enable(plugin){await f.change('install',plugin);await f.change('enable',plugin);},
  async input(operationId='one',input=opening,kind='send'){const v=await f.read();return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,kind,input};},
  async send(...args){return host.send(await f.input(...args));},
  async sent(...args){const r=await f.send(...args);await f.idle();return r;},
  async update(operationId='manual'){return host.update(await f.input(operationId,null,'update'));},
  async recover(operationId='one'){const s=(await f.read()).session;return host.recover({sessionId:id,operationId,expectedRevision:s.revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision});},
  async reopen(){await close();await store.close();store=await AuthoringStore.open(join(directory,'works'));await open(false);},
 };
 for(const p of ['story','summary','memory'])if(options[p+'Model']!==false)await f.select(p);
 if(options.summary!==false)await f.enable(M2);if(options.memory!==false)await f.enable(M3);
 return f;
}

import {TurnDisplayJournal,turnDisplayView} from './turn-display.mjs';
import {compilePresentationBounded} from './presentation.mjs';
const pngA=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=','base64');
const pngB=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
const audio=Buffer.concat([Buffer.from('ID3'),Buffer.alloc(10)]);
const cgEntry=(extra={})=>({id:'chat',name:'独立插图',categoryId:null,enabled:true,roles:['assistant'],rule:'or',keywords:['古老'],target:'chat',mode:'weighted',images:[{alias:'a',weight:1},{alias:'b',weight:1}],...extra});

test('persistent CG uses raw completed turns and freezes desktop/mobile display state',async t=>{
 const f=await fixture(t,{summary:false,memory:false});await f.sent();await f.sent('two','继续');await f.sent('three','再继续');
 const s=(await f.read()).session,before=canonical(s),budget=await f.budget.status();let seeds=0,journal;
 const directory=join(f.directory,'display'),open=()=>TurnDisplayJournal.open({directory,resolveRuntime:f.resolveRuntime,seed:()=>{seeds++;return sha256('seed'+seeds);}});
 journal=await open();
 try{
  await t.test('missing predecessor rejects without drawing or creating partial chain',async()=>{await reject(()=>journal.capture(s,2),'TURN_DISPLAY_PREVIOUS_REQUIRED');assert.equal(seeds,0);});
  const one=(await journal.capture(s,1)).record,two=(await journal.capture(s,2)).record,three=(await journal.capture(s,3)).record;
  await t.test('weighted illustration and backgrounds persist, replay never redraws',async()=>{
   assert.equal(one.state,'ready');assert.equal(one.result.plan.chat.length,1);assert.equal(one.result.plan.binding.sourceHash,digest(one.source.turn));
   assert.equal(one.result.backgrounds.desktop.image.alias,'b');assert.equal(one.result.backgrounds.mobile.image.alias,'b');
   assert.equal(two.result.plan.chat.length,0);assert.equal(two.result.backgrounds.desktop.source,'inherited');assert.equal(two.result.backgrounds.desktop.image.alias,'b');
   assert.equal(three.result.backgrounds.desktop.image.alias,'a');assert.equal((await journal.read(id,1)).result.backgrounds.desktop.image.alias,'b');
   assert.equal(one.result.bgm.alias,'bgm');assert.equal(one.result.bgmAutoplay,true);
   for(let n=1;n<=3;n++)assert.equal((await journal.capture(s,n)).replayed,true);assert.equal(seeds,3);
  });
  await t.test('restart and explicit recovery keep the original selected image',async()=>{
   await journal.close();journal=await open();assert.deepEqual(await journal.read(id,1),one);assert.equal((await journal.recover(id,1)).replayed,true);assert.equal(seeds,3);
  });
  await t.test('same generic renderer consumes persisted CG without rewriting source',async()=>{
   const resources=one.source.assets.map(a=>({...a,bytes:a.alias==='a'?pngA:a.alias==='b'?pngB:audio}));
   const input={mode:'reply',html:one.source.turn.assistant,css:'',origin:'http://127.0.0.1:18588',token:'a'.repeat(48),resources};
   const plain=await compilePresentationBounded(input),render=await compilePresentationBounded({...input,cgView:turnDisplayView(one,'mobile')});
   assert.equal(render.manifest.sourceHash,plain.manifest.sourceHash);assert.notEqual(render.manifest.renderHash,plain.manifest.renderHash);
   assert.match(render.patch.bodyHtml,/data-cg-entry="chat"/);assert.match(render.patch.styleText,/background-image/);assert.equal(render.manifest.authorScriptsExecuted,false);
  });
  assert.equal(canonical(s),before);assert.deepEqual(await f.budget.status(),budget);assert.equal(f.provider.dispatchCount,3);
 }finally{await journal.close();}
});

test('prepared display and lost save acknowledgement recover without a new draw',async t=>{
 const f=await fixture(t,{summary:false,memory:false});await f.sent();const s=(await f.read()).session;
 for(const failure of ['afterPrepared','afterCommit'])await t.test(failure,async()=>{
  const directory=join(f.directory,failure);let seeds=0,armed=true,journal;
  const open=faults=>TurnDisplayJournal.open({directory,resolveRuntime:f.resolveRuntime,seed:()=>{seeds++;return 'b'.repeat(64);},
   fault:faults?phase=>{if(armed&&phase===failure){armed=false;throw Error('interrupted');}}:undefined,
   storageFault:faults?(phase,r)=>{if(armed&&failure==='afterCommit'&&phase==='afterCommit'&&r.data.state==='ready'){armed=false;throw Error('lost');}}:undefined});
  journal=await open(true);
  try{
   await assert.rejects(journal.capture(s,1));const frozen=await journal.read(id,1);assert.equal(frozen.state,failure==='afterPrepared'?'prepared':'ready');
   await journal.close();journal=await open(false);await journal.reconcileStorage();const resumed=await journal.recover(id,1);
   assert.equal(resumed.record.state,'ready');assert.equal(resumed.record.seed,frozen.seed);assert.equal(seeds,1);
   assert.equal((await journal.capture(s,1)).replayed,true);assert.equal(seeds,1);assert.equal(f.provider.dispatchCount,1);
  }finally{await journal.close();}
 });
});

test('no match retains device defaults and legacy BGM defaults to manual playback',async t=>{
 const f=await fixture(t,{summary:false,memory:false,legacyBgm:true,replies:{one:reply('普通谈话，没有任何触发词。')}});await f.sent();const s=(await f.read()).session;
 const journal=await TurnDisplayJournal.open({directory:join(f.directory,'display'),resolveRuntime:f.resolveRuntime});
 try{const r=(await journal.capture(s,1)).record;assert.equal(r.result.plan.chat.length,0);assert.equal(r.result.backgrounds.desktop.image.alias,'a');assert.equal(r.result.backgrounds.mobile.image.alias,'b');assert.equal(r.result.bgmAutoplay,false);assert.equal(r.result.backgrounds.mobile.source,'default');}
 finally{await journal.close();}
});

test('capacity failure is retained without truncating raw reply or retrying selection',async t=>{
 const f=await fixture(t,{summary:false,memory:false,many:true});await f.sent();const s=(await f.read()).session,before=canonical(s);let seeds=0;
 const journal=await TurnDisplayJournal.open({directory:join(f.directory,'display'),resolveRuntime:f.resolveRuntime,seed:()=>{seeds++;return 'c'.repeat(64);}});
 try{
  const r=(await journal.capture(s,1)).record;assert.equal(r.state,'failed');assert.equal(r.diagnostic,'AUTHORING_CG_OUTPUT_CAPACITY');assert.equal(r.result.plan,null);
  assert.equal(r.source.turn.assistant,s.turns[0].assistant);assert.equal(canonical(s),before);assert.equal((await journal.recover(id,1)).replayed,true);
  assert.throws(()=>turnDisplayView(r,'desktop'),{code:'AUTHORING_CG_OUTPUT_CAPACITY'});assert.equal(seeds,1);assert.equal(f.provider.dispatchCount,1);
 }finally{await journal.close();}
});

test('changing the saved CG result and resealing its envelope is detected',async t=>{
 const f=await fixture(t,{summary:false,memory:false});await f.sent();const directory=join(f.directory,'display'),journal=await TurnDisplayJournal.open({directory,resolveRuntime:f.resolveRuntime});
 try{
  const r=(await journal.capture((await f.read()).session,1)).record,file=join(directory,'records',r.id+'.json');
  const envelope=JSON.parse(await readFile(file,'utf8'));envelope.data.result.plan.chat[0].alias='other-image';envelope.sha256=digest(envelope.data);await writeFile(file,canonical(envelope));
  await reject(()=>journal.read(id,1),'TURN_DISPLAY_RECORD_INVALID');assert.equal(f.provider.dispatchCount,1);
 }finally{await journal.close();}
});

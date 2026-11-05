import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {startAuthoringServer} from './server.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {stageOperationId} from './joint-plan.mjs';
import {jointSessionId} from './joint-session-state.mjs';
import {turnDisplayId} from './turn-display.mjs';
import {blankDocument,sha256,canonical} from './contracts.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6v-tests/',import.meta.url));await mkdir(root,{recursive:true});
const base='/rpg-app/api/authoring',id=jointSessionId('session'),pb='/joint/playback',png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=','base64');
const catalog={catalogId:'playback-http',models:['story','memory'].map(k=>({selectionId:k,selectionRevision:sha256(k),modelId:'fixture/'+k,name:'离线 '+k,available:true,contextLength:128000,maxOutputTokens:16000,
 parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}))};
const reply=text=>({status:'completed',finishReason:'stop',text});
const raw='**蓝信**：明早见面。<details><summary>细节</summary>完整记录</details><script>globalThis.steal=true</script>\n<suggested_questions>\n1. 查看信封\n2. 等待清晨\n3. 走向码头\n</suggested_questions>';
const cgEntry=(more={})=>({id:'illustration',name:'蓝信插图',categoryId:null,enabled:true,roles:['assistant'],rule:'or',keywords:['蓝信'],target:'chat',mode:'weighted',images:[{alias:'a',weight:1},{alias:'b',weight:1}],...more});
async function fixture(t,options={}){
 const directory=await mkdtemp(join(root,'case-')),control=new OfflineModelControl(catalog);let server,provider,seeds=0;
 const replies={one:reply(raw),two:reply('继续散步，未出现约定的关键词。'),[stageOperationId(id,'one','memory-after')]:reply('{"operations":[]}')};
 async function open(faults=true){
  provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies,fault:faults?options.providerFault:undefined});
  server=await startAuthoringServer({directory:join(directory,'works'),modelSettings:{directory:join(directory,'sessions'),provider,control},historySettings:{directory:join(directory,'history')},
   jointSettings:{directory:join(directory,'joint'),allowance:{id:'same-allowance',limit:20}},
   ...(options.disabled?{}:{playbackSettings:{directory:join(directory,'display'),seed:()=>{seeds++;return sha256('seed-'+seeds);},...(faults&&options.displayFault?{fault:options.displayFault}:{}),...(faults&&options.storageFault?{storageFault:options.storageFault}:{})}})});
 }
 await open();t.after(async()=>{await server.close();await provider.close();});
 const req=async(path,body,headers={})=>{const r=await fetch(server.origin+base+path,body===undefined?{headers}:{method:'POST',headers:{Origin:server.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
 const ok=async(path,body)=>{const r=await req(path,body);assert.equal(r.status,200,JSON.stringify(r.value));return r.value;};
 const doc=blankDocument();doc.metadata.name='不可变回放样例';doc.metadata.audience='female';doc.sources.system='AUTHOR';doc.sources.prefix='PRE';doc.sources.suffix='POST';doc.sources.outputCss='details { color: #123456; }';
 doc.enhancements.suggestions=true;doc.enhancements.displayRules=[{id:'display-only',name:'仅显示替换',enabled:true,pattern:'蓝信',replacement:'展柜',flags:'g',scope:'assistant'}];
 doc.enhancements.cg.entries=options.many?Array.from({length:33},(_,i)=>cgEntry({id:'many-'+i,mode:'regular',images:[{alias:'a',weight:100}]})):[cgEntry(),cgEntry({id:'background',target:'background',mode:'regular',images:[{alias:'b',weight:100}]})];
 if(options.cg)doc.enhancements.cg=structuredClone(options.cg);
 doc.settings.media.desktop='a';doc.settings.media.mobile='b';doc.settings.media.bgm='music';doc.settings.bgmAutoplay=false;
 const w=await ok('/works',{operationId:'work',expectedRevision:null,document:doc});
 for(const [i,alias]of ['a','b','music'].entries())await ok('/works/'+w.workId+'/assets',{operationId:'asset-'+alias,expectedRevision:i,alias,fileName:alias+(i===2?'.mp3':'.png'),mediaType:i===2?'audio/mpeg':'image/png',base64:(i===2?Buffer.concat([Buffer.from('ID3'),Buffer.alloc(10)]):png).toString('base64')});
 const v=await ok('/works/'+w.workId+'/publish',{operationId:'publish',expectedRevision:3});
 await ok('/model-settings/sessions',{operationId:'session',expectedRevision:null,reference:{workId:w.workId,versionId:v.result.versionId},openingText:'原始开局：找信',presetId:null});
 const f={directory,req,ok,doc,workId:w.workId,versionId:v.result.versionId,get seeds(){return seeds;},get server(){return server;},get provider(){return provider;},view:()=>ok('/joint/sessions/'+id),
  async select(purpose){const s=(await f.view()).session;return ok('/model-settings/configure',{sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
   choice:{catalogRevision:control.catalog().revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});},
  async memory(){await f.select('memory');for(const action of ['install','enable']){const s=await ok('/joint/settings/'+id);await ok('/joint/settings/change',{purpose:'memory',action,sessionId:id,operationId:randomUUID(),expectedRevision:s.sessionRevision,expectedModelRevision:s.modelRevision,expectedHistoryRevision:s.historyRevision,
   expectedSummaryRevision:s.summaryRevision,expectedMemoryRevision:s.memoryRevision,expectedContextPolicyRevision:s.effectivePolicy.revision,expectedRegistryRevision:s.registryRevision,...(action==='enable'?{permissions:s.plugins.memory.permissions}:{})});}},
  async intent(name='one'){const v=await f.view();return {sessionId:id,operationId:name,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,
   expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,kind:'send',input:name==='one'?'原始开局：找信':'继续'};},
  async send(name='one'){const r=await ok('/joint/send',await f.intent(name));await server.sessionHost.waitForIdle(id);return r;},
  prepare:(turnNumber=1,role='assistant',device='desktop')=>ok(pb+'/prepare',{sessionId:id,turnNumber,role,device}),
  status:(n=1)=>ok(pb+'/status/'+id+'/'+n),
  async recovery(n=1){const s=await f.status(n);return {sessionId:id,turnNumber:n,...s.recovery};},
  async frame(p){const r=await fetch(server.origin+p.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}});assert.equal(r.status,200);return {text:await r.text(),csp:r.headers.get('content-security-policy')};},
  async reopen(){await server.close();await provider.close();await open(false);},
 };
 await f.select('story');return f;
}
const rejects=async(f,path,body,code)=>{const r=await f.req(path,body);assert.equal(r.value.error?.code,code,JSON.stringify(r));};

test('HTTP complete story renders from its version and persists CG before page replay',async t=>{
 const f=await fixture(t);await f.memory();const sent=await f.send(),before=await f.view();assert.equal(sent.display.state,'ready');
 const p=await f.prepare(),frame=await f.frame(p),s=await f.status();
 await t.test('raw history, M3 source and controlled request are not display-transformed',async()=>{
  assert.equal(before.session.turns[0].assistant,raw);assert.equal(before.session.memory.jobs[0].request.source.turns[0].assistant,raw);assert.equal(f.provider.dispatchCount,2);
  assert.match(frame.text,/<strong>展柜<\/strong>/);assert.doesNotMatch(frame.text,/globalThis.steal/);assert.equal(p.manifest.authorScriptsExecuted,false);assert.match(frame.csp,/connect-src 'none'/);
  assert.equal(p.manifest.binding.versionId,f.versionId);assert.equal(p.manifest.cg.plan.chat.length,1);assert.equal(p.manifest.cg.background.alias,'b');assert.equal(p.manifest.suggestions.length,3);assert.equal(p.bgm.autoplay,false);
  assert.equal((await fetch(f.server.origin+p.bgm.url)).status,200);
 });
 await t.test('read/prepare/recover never resample or charge; frozen draft edits do not leak',async()=>{
  const seeds=f.seeds;await f.status();const phone=await f.prepare(1,'assistant','mobile');assert.equal(phone.display.resultHash,p.display.resultHash);
  const recovered=await f.ok(pb+'/recover',await f.recovery());assert.equal(recovered.resultHash,s.resultHash);
  const current=await f.ok('/works/'+f.workId);const changed=structuredClone(current.draft);changed.sources.outputCss='details { color: red; }';
  await f.ok('/works/'+f.workId+'/save',{operationId:'future-draft',expectedRevision:current.revision,document:changed});
  const same=await f.prepare();assert.equal(same.manifest.renderHash,p.manifest.renderHash);assert.equal(f.seeds,seeds);assert.deepEqual((await f.view()).budget,before.budget);
 });
 await t.test('caller supplied trusted fields and foreign origin are rejected',async()=>{
  const body={sessionId:id,turnNumber:1,role:'assistant',device:'desktop'};
  for(const extra of [{reply:'forged'},{document:{}},{draws:[]},{providerUrl:'https://invalid.local'}])await rejects(f,pb+'/prepare',{...body,...extra},'AUTHORING_UNKNOWN_FIELD');
  assert.equal((await f.req(pb+'/prepare',body,{Origin:'https://foreign.invalid'})).status,403);
  const recovery=await f.recovery();await rejects(f,pb+'/recover',{...recovery,operationId:'new-id'},'TURN_DISPLAY_OPERATION_MISMATCH');await rejects(f,pb+'/recover',{...recovery,expectedRevision:999},'OFFLINE_REVISION_CONFLICT');
  await rejects(f,pb+'/prepare',{...body,turnNumber:2},'AUTHORING_CG_COMPLETE_TURN_REQUIRED');
 });
 await t.test('restart keeps original chosen picture and does not replay model operations',async()=>{
  await f.reopen();const same=await f.prepare();assert.equal(same.display.resultHash,p.display.resultHash);assert.equal(same.manifest.renderHash,p.manifest.renderHash);assert.equal(f.provider.dispatchCount,0);assert.equal(f.seeds,1);
 });
 await t.test('next non-matching turn inherits saved background without new illustrations',async()=>{
  await f.send('two');const next=await f.prepare(2);assert.equal(next.manifest.cg.plan.chat.length,0);assert.equal(next.manifest.cg.background.alias,'b');assert.equal(next.manifest.cg.backgroundSource,'inherited');
  const first=await f.prepare();assert.equal(first.display.resultHash,p.display.resultHash);assert.equal(f.seeds,2);
 });
});

for(const phase of ['afterPrepared','afterCommit'])test('HTTP '+phase+' uncertainty requires original display recovery, not a model resend',async t=>{
 let armed=true;const f=await fixture(t,{displayFault:phase==='afterPrepared'?(p)=>{if(armed&&p===phase){armed=false;throw Error('interrupted');}}:undefined,
  storageFault:phase==='afterCommit'?(p,r)=>{if(armed&&p===phase&&r.data.state==='ready'){armed=false;throw Error('ack-lost');}}:undefined});
 const result=await f.send();assert.equal(result.operation.state,'completed');assert.equal(result.display.state,'unconfirmed');assert.equal(f.provider.dispatchCount,1);
 const status=await f.status();assert.equal(status.state,phase==='afterPrepared'?'prepared':'ready');const budget=(await f.view()).budget;
 await f.reopen();assert.equal((await f.status()).state,status.state);assert.equal(f.seeds,1);assert.equal(f.provider.dispatchCount,0);
 if(phase==='afterPrepared')await rejects(f,pb+'/prepare',{sessionId:id,turnNumber:1,role:'assistant',device:'desktop'},'TURN_DISPLAY_PENDING');
 await f.ok(pb+'/recover',await f.recovery());assert.equal((await f.status()).state,'ready');await f.prepare();assert.equal(f.seeds,1);assert.equal(f.provider.dispatchCount,0);assert.deepEqual((await f.view()).budget,budget);
});

test('a missing display from a restart is visible and can be explicitly restored once',async t=>{
 const f=await fixture(t);await f.server.sessionHost.send(await f.intent());await f.server.sessionHost.waitForIdle(id);assert.equal((await f.status()).state,'missing');assert.equal(f.seeds,0);
 await f.reopen();await rejects(f,pb+'/prepare',{sessionId:id,turnNumber:1,role:'assistant',device:'desktop'},'TURN_DISPLAY_MISSING');assert.equal(f.seeds,0);
 await f.ok(pb+'/recover',await f.recovery());await f.ok(pb+'/recover',await f.recovery());assert.equal(f.seeds,1);assert.equal(f.provider.dispatchCount,0);
});

test('capacity failure renders full story with an explicit CG diagnostic and no partial pictures',async t=>{
 const f=await fixture(t,{many:true});const result=await f.send();assert.equal(result.display.state,'failed');const p=await f.prepare(),frame=await f.frame(p);
 assert.match(frame.text,/<strong>展柜<\/strong>/);assert.equal(p.manifest.cg,undefined);assert.ok(p.manifest.diagnostics.some(d=>d.code==='AUTHORING_CG_OUTPUT_CAPACITY'));
 assert.equal((await f.view()).session.turns[0].assistant,raw);await f.ok(pb+'/recover',await f.recovery());assert.equal(f.seeds,1);assert.equal(f.provider.dispatchCount,1);
});

test('disabled playback preserves old send contract and unknown story has no fabricated display',async t=>{
 const old=await fixture(t,{disabled:true});const r=await old.send();assert.equal(r.display,undefined);assert.equal((await old.req(pb+'/status/'+id+'/1')).status,404);
 const f=await fixture(t,{providerFault:(phase)=>{if(phase==='afterAccepted')throw Error('unknown');}});await f.send();assert.equal((await f.view()).session.turns.length,0);assert.equal(f.seeds,0);
 await rejects(f,pb+'/prepare',{sessionId:id,turnNumber:1,role:'assistant',device:'desktop'},'AUTHORING_CG_COMPLETE_TURN_REQUIRED');assert.equal(f.provider.dispatchCount,1);
});

test('特殊 CG 画廊只从已落盘的版本和回合取首次命中，锁定项不泄露素材或剧情',async t=>{
 const special={enabled:true,showNameWhenLocked:false,hintEnabled:true,hint:'去北岸看那束光。'};
 const rows=[cgEntry({id:'beacon',name:'铜灯',mode:'weighted',keywords:['蓝信','继续散步'],special}),
   cgEntry({id:'map',name:'地图背面的文字',mode:'regular',keywords:['未出现的口令'],special:{...special,hintEnabled:false}}),
   cgEntry({id:'ordinary',name:'普通海图',mode:'regular'})];
 const f=await fixture(t,{cg:{categories:[],entries:rows}});
 const gallery=()=>f.ok(pb+'/gallery/'+id);
 const before=await gallery();
 assert.equal(before.unlocked,0);assert.equal(before.cards.length,2);
 assert.deepEqual(before.cards[0],{group:'默认',groupId:null,name:'神秘CG',hint:special.hint,unlock:null});
 assert.doesNotMatch(JSON.stringify(before.cards),/地图背面|imageUrl|铜灯|普通海图|未出现的口令/);
 await f.server.sessionHost.send(await f.intent());await f.server.sessionHost.waitForIdle(id);
 const missing=await gallery();assert.equal(missing.waitingForTurn,1);assert.equal(missing.unlocked,0);assert.equal(f.seeds,0);
 await f.ok(pb+'/recover',await f.recovery());const after=await gallery();
 assert.equal(after.unlocked,1);assert.equal(after.cards[0].name,'铜灯');assert.equal(after.cards[0].unlock.turnNumber,1);
 assert.equal((await fetch(f.server.origin+after.cards[0].unlock.imageUrl)).status,200);
 const first=await f.prepare(),second=await f.prepare();assert.equal(first.manifest.renderHash,second.manifest.renderHash);
 assert.match((await f.frame(first)).text,/aria-label="放大CG插图"/);assert.equal(first.manifest.authorScriptsExecuted,false);
 await f.send('two');assert.deepEqual(await gallery(),after);
 const independent=await f.ok('/model-settings/sessions',{operationId:'another-story',expectedRevision:null,
    reference:{workId:f.workId,versionId:f.versionId},openingText:'另一段故事',presetId:null});
 assert.equal((await f.ok(pb+'/gallery/'+independent.session.id)).unlocked,0);
 await f.reopen();assert.deepEqual(await gallery(),after);assert.equal(f.provider.dispatchCount,0);
 assert.equal((await f.view()).session.turns[0].assistant,raw);
});

test('分支画廊继承分支点前的实图与触发回合，父路线后续命中保持锁定',async t=>{
 const special={enabled:true,showNameWhenLocked:true,hintEnabled:false,hint:''};
 const f=await fixture(t,{cg:{categories:[],entries:[
   cgEntry({id:'early',name:'晨潮',mode:'weighted',special}),
   cgEntry({id:'late',name:'晚潮',mode:'regular',keywords:['继续散步'],special}),
 ]}});
 await f.send();await f.send('two');
 for(const action of ['install','enable']){
   const s=await f.ok('/joint/settings/'+id);
   await f.ok('/joint/settings/change',{purpose:'branch',action,sessionId:id,operationId:randomUUID(),expectedRevision:s.sessionRevision,
     expectedModelRevision:s.modelRevision,expectedHistoryRevision:s.historyRevision,expectedSummaryRevision:s.summaryRevision,
     expectedMemoryRevision:s.memoryRevision,expectedContextPolicyRevision:s.effectivePolicy.revision,expectedRegistryRevision:s.registryRevision,
     ...(action==='enable'?{permissions:s.plugins.branch.permissions}:{})});
 }
 const v=await f.view();
 const parent=await f.ok(pb+'/gallery/'+id);
 assert.deepEqual(parent.cards.map(c=>c.unlock.turnNumber),[1,2]);
 const fork=await f.ok('/joint/branch',{sessionId:id,operationId:'fork-gallery',expectedRevision:v.session.revision,
   expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,
   expectedContextPolicyRevision:v.effectivePolicy.revision,turn:1,name:'启航日'});
 const child=await f.ok(pb+'/gallery/'+fork.session.id);
 assert.equal(child.unlocked,1);assert.deepEqual(child.cards[0].unlock,parent.cards[0].unlock);
 assert.deepEqual(child.cards[1],{group:'默认',groupId:null,name:'晚潮',hint:null,unlock:null});
 assert.equal(f.provider.dispatchCount,2);
});

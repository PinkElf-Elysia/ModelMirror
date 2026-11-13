// P2-A browser draft / HTTP acceptance and optional isolated preview.
// All endpoints are loopback fixtures; no credential or external model calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {ControlPlaneModelControl,PARAMETER_SELECTION_FORMAT} from './model-control.mjs';
import {PARAMETER_PROFILE,PARAMETER_CONFIG} from './generation-settings.mjs';
import {ControlledProvider} from './controlled-provider.mjs';
import {startAuthoringServer} from './server.mjs';

import {typedParameterDraft,switchParameterDraft,typedParameterConfig,typedDraftIssues,typedDraftFingerprint,typedSelection,savedOperation,profileDraft,withRecommendedProfile,pendingPresetOperation,presetReceiptMatches} from './model-ui.mjs';
import {parameterIssues} from './generation-settings.mjs';
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),code=c=>e=>e?.code===c;
const token='local-test-only-parameter-session-'+ 'x'.repeat(32);
const python=process.env.RPG_TEST_PYTHON||'python';
const parameterSource=fileURLToPath(new URL('../../../server/model_router/rpg_parameters.py',import.meta.url));
const resolver=`import json,sys,runpy,hashlib\nm=runpy.run_path(sys.argv[1]);v=json.load(sys.stdin)\ntry:\n r=m['resolve_parameters'](v['policy'],v['parameters'],v['purpose']);r={'format':'modelmirror.rpg.parameter-resolution/2','capabilityRevision':v['capabilityRevision'],**r};r['sha256']=hashlib.sha256(json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest();print(json.dumps(r))\nexcept m['ParameterError'] as e:print(json.dumps({'error':e.code}))`;
const ffield=(type,wireName,extra={},defaultValue=null)=>({type,wireName,required:false,default:defaultValue===null?{mode:'omit'}:{mode:'value',value:defaultValue},...extra});
const policy=()=>({kind:'editable',fields:{
 temperature:ffield('number','temperature',{minimum:0,maximum:2}),topP:ffield('number','top_p',{minimum:0,maximum:1}),
 maxOutputTokens:ffield('integer','max_tokens',{minimum:1,maximum:16000,required:true},4096),
 topK:ffield('integer','top_k',{minimum:0,maximum:100}),seed:ffield('integer','seed',{minimum:0,maximum:9999}),
 frequencyPenalty:ffield('number','frequency_penalty',{minimum:-2,maximum:2}),presencePenalty:ffield('number','presence_penalty',{minimum:-2,maximum:2}),
 stop:ffield('text-list','stop',{minItems:0,maxItems:8,maxItemLength:80}),
 'thinking.enabled':ffield('boolean','reasoning.enabled'),
 'thinking.effort':ffield('enum','reasoning.effort',{options:['low','high']}),
 'thinking.budgetTokens':ffield('integer','reasoning.max_tokens',{minimum:0,maximum:8192}),
},exclusive:[],requires:[],lessThan:[['thinking.budgetTokens','maxOutputTokens']],purposes:['story','summary','memory']});
const profile=(purpose,values={})=>({format:PARAMETER_PROFILE,purpose,modelId:'synthetic/'+purpose,parameters:{format:PARAMETER_CONFIG,values:clone(values)}});
async function controlFixture(t,options={}) {
 const f={tasks:[],requests:[],lookups:[],results:new Map(),policies:Object.fromEntries(['story','summary','memory'].map(k=>[k,policy()])),onCheck:null,corrupt:null,unavailable:false};
 f.policies.summary.exclusive=[['temperature','topP']];delete f.policies.summary.fields.topK;
 f.policies.memory.requires=[{if:{field:'thinking.budgetTokens',op:'set'},then:{field:'thinking.enabled',op:'eq',value:true}}];
 f.catalog=(version=2)=>{
  const models=Object.entries(f.policies).map(([id,p])=>({selectionId:id,selectionRevision:sha256(id+'-qualification'),modelId:'synthetic/'+id,name:({story:'澄明 · 全参数样例',summary:'简约 · 互斥样例',memory:'深思 · 依赖样例'})[id],available:true,contextLength:256000,maxOutputTokens:16000,parameterEvidence:'provider-declared',
   ...(version===2?{capabilityRevision:digest(p),parameterPolicy:{...clone(p),fields:Object.fromEntries(Object.entries(p.fields).map(([k,{wireName,...v}])=>[k,v]))}}:
     {parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}})}));
  const body={format:'modelmirror.rpg.control-model-catalog/'+version,source:'control-plane-cache',catalogId:'parameter-ui-test',models};return {...body,revision:digest(body)};
 };
 f.selection=(purpose,values={})=>{const c=f.catalog(),e=c.models.find(m=>m.selectionId===purpose);return {format:PARAMETER_SELECTION_FORMAT,catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision,profile:profile(purpose,values)};};
 f.resolve=p=>JSON.parse(execFileSync(python,['-X','utf8','-B','-c',resolver,parameterSource],{input:JSON.stringify({policy:f.policies[p.selectionId],parameters:p.parameters,purpose:p.purpose,capabilityRevision:p.capabilityRevision}),encoding:'utf8',windowsHide:true}));
 const json=(res,value,status=200)=>{res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(value));};
 let dropped=false;
 const server=createServer(async(req,res)=>{
  try {
   assert.equal(req.headers.authorization,'Bearer '+token);const url=new URL(req.url,'http://localhost');
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const p=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
   f.requests.push({method:req.method,path:req.url,body:p});
   if(options.captureDirectory)await writeFile(join(options.captureDirectory,'control-capture.json'),JSON.stringify({requests:f.requests,tasks:f.tasks},null,2));
   if(url.pathname.endsWith('/models'))return f.unavailable?json(res,{},503):json(res,f.catalog(url.searchParams.get('parameterVersion')==='2'?2:1));
   if(url.pathname.endsWith('/readiness'))return json(res,{format:'modelmirror.rpg.authoring-readiness/1',executionEnabled:options.execution!==false,dispatched:false});
   if(url.pathname.endsWith('/parameters/validate')) {
    if(f.unavailable)return json(res,{},503);
    const c=f.catalog(p.format?2:1),e=c.models.find(m=>m.selectionId===p.selectionId);
    if(!e||c.revision!==p.catalogRevision||e.selectionRevision!==p.selectionRevision||p.format&&e.capabilityRevision!==p.capabilityRevision)return json(res,{},409);
    const resolution=p.format?f.resolve(p):null;if(resolution?.error)return json(res,resolution,422);
    if(f.onCheck)await f.onCheck(p);
    return json(res,{format:'modelmirror.rpg.parameter-check/'+(p.format?2:1),catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,
     ...(p.format?{resolution}:{wireParameters:{max_tokens:p.parameters.maxOutputTokens}}),dispatched:false});
   }
   if(url.pathname==='/api/rpg/v1/authoring/tasks'&&req.method==='POST') {
    assert.notEqual(options.execution,false,'preview never executes a model task');assert.equal(p.format,'modelmirror.rpg.authoring-task/2');const key=p.sessionId+'/'+p.requestId;assert.ok(!f.results.has(key),'duplicate task POST');
    const resolution=f.resolve(p);assert.ok(!resolution.error);f.tasks.push(clone(p));
    const raw=options.text?.(p)??(p.purpose==='memory'?'{"operations":[]}':p.purpose==='story'?'A complete original reply.':p.purpose==='summary'&&options.compression?'L'.repeat(10001):'A shorter summary.');
    // Memory is raw protocol JSON; no model-specific repair or fence handling.
    const output=p.purpose==='memory'&&!options.text?'{"operations":[]}':raw;
    const dispatched=options.dispatched!==false,status=dispatched?'completed':'failed',sse=dispatched?'fake-sse:'+output:'',actualRaw=dispatched?output:'';
    const receipt={format:'modelmirror.rpg.authoring-task-receipt/2',gateway:'rpg_authoring',requestHash:digest(p),sourceRequestHash:p.sourceRequestHash,
     sessionId:p.sessionId,requestId:p.requestId,operationId:p.operationId,purpose:p.purpose,selectionId:p.selectionId,selectionRevision:p.selectionRevision,catalogRevision:p.catalogRevision,
     capabilityRevision:p.capabilityRevision,parameterResolution:dispatched?resolution:null,providerRequestHash:dispatched?digest({model:'synthetic/'+p.selectionId,messages:p.messages,...resolution.wireParameters}):null,
     requestedModel:dispatched?'synthetic/'+p.selectionId:null,actualModel:dispatched?'synthetic/'+p.selectionId:null,parameters:dispatched?resolution.wireParameters:null,
     runId:dispatched?'run-'+f.tasks.length:null,attemptId:dispatched?'attempt-'+f.tasks.length:null,dispatched,status,finishReason:dispatched?'stop':null,error:dispatched?null:'rpg_authoring_execution_disabled',httpStatus:dispatched?200:null,usage:null,retries:0,rawHash:sha256(actualRaw),sseHash:sha256(sse)};
    const result={status,requestHash:receipt.requestHash,raw:actualRaw,sse,receipt:{...receipt,sha256:digest(receipt)}};f.results.set(key,result);
    if(options.onTask)await options.onTask(p,f);
    if(options.dropPurpose===p.purpose&&!dropped){dropped=true;res.destroy();return;}
    const returned=clone(result);if(f.corrupt)f.corrupt(returned);return json(res,returned);
   }
   if(req.method==='GET'&&url.pathname.startsWith('/api/rpg/v1/authoring/tasks/')) {
    f.lookups.push(req.url);const result=f.results.get(url.pathname.slice('/api/rpg/v1/authoring/tasks/'.length));
    if(result)assert.equal(result.receipt.sourceRequestHash,url.searchParams.get('sourceRequestHash'));
    return json(res,result??{status:'absent',sourceRequestHash:url.searchParams.get('sourceRequestHash')});
   }
   throw new Error('unexpected fixture route '+req.url);
  } catch(error){f.error=error;return json(res,{error:'fixture-error'},500);}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));f.origin='http://127.0.0.1:'+server.address().port;
 f.control=new ControlPlaneModelControl({origin:f.origin,serviceToken:token,timeoutMs:options.controlTimeout??5000});await f.control.refresh();
 t.after(async()=>{server.closeAllConnections();await new Promise(r=>server.close(r));if(f.error)throw f.error;});return f;
}

async function webFixture(t,options={}){
 const directory=options.directory||await mkdtemp(join(tmpdir(),'rpg-parameter-ui-'));await mkdir(directory,{recursive:true});
 const f=await controlFixture(t,{...options,captureDirectory:directory});let provider,host;
 async function open(){
  provider=await ControlledProvider.open({directory:join(directory,'transport'),origin:f.origin,serviceToken:token});
  host=await startAuthoringServer({directory:join(directory,'works'),port:options.port||0,creatorExperience:options.creatorExperience!==false,
   modelSettings:{directory:join(directory,'sessions'),provider,control:f.control},historySettings:{directory:join(directory,'plugins')},jointSettings:{directory:join(directory,'joint'),allowance:{id:'p2a-offline-only',limit:20}}});
 }
 await open();t.after(async()=>{await host.close();await provider.close();});
 const call=async(path,body)=>{const response=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});return {status:response.status,data:await response.json()};};
 const ok=async(path,body)=>{const r=await call(path,body);assert.equal(r.status,200,JSON.stringify(r.data));return r.data;};
 const document=blankDocument();document.metadata.name='参数验收 · 离线控制面';document.metadata.audience='male';document.sources.system='只用本地固定回复验证参数。';document.sources.description='<h1>参数工作台</h1><p>这是独立离线验收作品。打开模型与参数，分别配置剧情、摘要和记忆。</p>';
 const work=await ok('/works',{operationId:'ui-work',expectedRevision:null,document});
 const version=await ok('/works/'+work.workId+'/publish',{operationId:'ui-version',expectedRevision:0});
 const made=options.createSession===false?null:await ok('/model-settings/sessions',{operationId:'ui-session',expectedRevision:null,reference:{workId:work.workId,versionId:version.result.versionId},openingText:'参数回放',presetId:null});
 return {...f,directory,call,ok,work,id:made?.session.id,get host(){return host;},async reopen(){await host.close();await provider.close();await open();}};
}
const entry=(key='story')=>{const p=policy();return {selectionId:key,selectionRevision:sha256(key),capabilityRevision:digest(p),modelId:'synthetic/'+key,available:true,parameterPolicy:{...p,fields:Object.fromEntries(Object.entries(p.fields).map(([k,{wireName,...v}])=>[k,v]))}};};
const explicit=(d,key,value)=>{d[key]={mode:'explicit',value};return d;};

if(process.argv.includes('--preview')){
 const cleanup=[],directory=process.argv[process.argv.indexOf('--preview')+1],port=Number(process.argv.at(-1));
 const f=await webFixture({after:fn=>cleanup.unshift(fn)},{directory,port,createSession:false,execution:false});
 await writeFile(join(directory,'preview.json'),JSON.stringify({origin:f.host.origin,workId:f.work.workId,mode:'loopback-fake-control-no-generation'},null,2));
 console.log(JSON.stringify({url:f.host.origin+'/?work='+f.work.workId,mode:'offline-only'}));
 process.on('SIGINT',async()=>{for(const close of cleanup)await close();process.exit(0);});
}else{
 test('default and explicit-zero remain distinct; default omission is never guessed',()=>{
  const e=entry(),d=typedParameterDraft(e);assert.deepEqual(typedDraftIssues(e,d,'story'),[]);
  assert.equal(typedParameterConfig(e,d).values.temperature.mode,'default');
  explicit(d,'temperature','0');assert.equal(typedParameterConfig(e,d).values.temperature.value,0);
  assert.equal(typedParameterConfig(e,d).values.maxOutputTokens.mode,'default');
 });
 for(const [key,bad] of [['temperature',''],['temperature','NaN'],['temperature','2.1'],['maxOutputTokens','4.2'],['seed','-1'],['stop',['same','same']],['stop',['x'.repeat(81)]],['thinking.enabled','true'],['thinking.effort','unsupported']])test('reject invalid draft '+key+' '+JSON.stringify(bad),()=>{
  const e=entry(),d=explicit(typedParameterDraft(e),key,bad);assert.ok(typedDraftIssues(e,d,'story').some(x=>x.field===key));
 });
 test('model switch preserves explicit values, unsupported fields require explicit removal',()=>{
  const a=entry(),b=entry('second'),draft=explicit(typedParameterDraft(a),'topK','20');delete b.parameterPolicy.fields.topK;
  const next=switchParameterDraft(draft,b);assert.equal(next.topK.value,'20');assert.ok(typedDraftIssues(b,next,'story').some(x=>x.code==='PARAMETER_UNSUPPORTED'));
  assert.throws(()=>typedSelection({revision:'r',models:[b]},b,next,'story'),code('PARAMETER_UNSUPPORTED'));
  delete next.topK;assert.deepEqual(typedDraftIssues(b,next,'story'),[]);assert.equal(draft.topK.value,'20');
 });
 test('restore default returns clean fingerprint and whitespace in stop text is preserved',()=>{
  const e=entry(),d=typedParameterDraft(e),before=typedDraftFingerprint(e,d);explicit(d,'temperature','0.70');assert.notEqual(typedDraftFingerprint(e,d),before);
  d.temperature.mode='default';assert.equal(typedDraftFingerprint(e,d),before);explicit(d,'stop',['\n',' 停止🪷 ']);assert.deepEqual(typedParameterConfig(e,d).values.stop.value,['\n',' 停止🪷 ']);
 });
 test('constraint diagnostics agree with host across effective defaults, conflicts and thinking dependencies',()=>{
  for(const key of ['story','summary','memory'])for(const values of [{},{temperature:'0.7',topP:'0.8'},{'thinking.enabled':false,'thinking.effort':'high'},{'thinking.budgetTokens':'4096'},{'thinking.enabled':true,'thinking.budgetTokens':'512'}]){
   const e=entry(key);e.parameterPolicy.exclusive=[['temperature','topP']];e.parameterPolicy.requires=[{if:{field:'thinking.budgetTokens',op:'set'},then:{field:'thinking.enabled',op:'eq',value:true}}];
   const d=typedParameterDraft(e);for(const [k,v] of Object.entries(values))explicit(d,k,v);
   const profile={format:PARAMETER_PROFILE,purpose:key,modelId:e.modelId,parameters:typedParameterConfig(e,d)};
   assert.deepEqual(typedDraftIssues(e,d,key).map(i=>i.code).sort(),parameterIssues(profile,e).map(i=>i.code).sort());
  }
 });
 test('fixed values, unknown capability, unavailable purpose and stale metadata fail closed',()=>{
  const e=entry(),d=typedParameterDraft(e);assert.throws(()=>typedSelection({revision:'r',models:[]},e,d,'story'),code('MODEL_CONTROL_CHANGED'));
  assert.ok(typedDraftIssues({...e,available:false},d,'story').length);assert.ok(typedDraftIssues({...e,parameterPolicy:null},d,'story').length);
  e.parameterPolicy.purposes=['memory'];assert.ok(typedDraftIssues(e,d,'story').length);
  e.parameterPolicy.kind='fixed';explicit(d,'maxOutputTokens','8');assert.ok(typedDraftIssues(e,d,'memory').length);
 });
 test('UI intents persist all purposes through formal HTTP without plugin grant or generation; restart and original ID recover',async t=>{
  const f=await webFixture(t),catalog=(await f.ok('/model-settings/catalog?parameterVersion=2')).catalog;
  for(const purpose of ['story','summary','memory']){
   const e=catalog.models[0],d=typedParameterDraft(e);explicit(d,'temperature',purpose==='story'?'0.9':'0.2');explicit(d,'stop',[' 结束 ']);
   const view=await f.ok('/model-settings/sessions/'+f.id),choice=typedSelection(catalog,e,d,purpose),record={sessionId:f.id,operationId:'configure-'+purpose,expectedRevision:view.session.revision,expectedModelRevision:view.session.models.revision,purpose,choice};
   assert.equal((await f.ok('/model-settings/check',{choice})).dispatched,false);await f.ok('/model-settings/configure',record);
   const verified=await f.ok('/model-settings/sessions/'+f.id);assert.ok(savedOperation(verified,{workId:f.work.workId,kind:'configure',body:record}));
   assert.equal((await f.ok('/model-settings/configure',record)).replayed,true);
  }
  assert.equal(f.tasks.length,0);const prior=await f.ok('/model-settings/sessions/'+f.id);assert.equal(prior.session.models.current.story.parameters.temperature,.9);assert.equal(prior.session.models.current.summary.parameters.temperature,.2);assert.equal(prior.session.models.changes.some(c=>c.pluginReceipt),false);
  await f.reopen();const after=await f.ok('/model-settings/sessions/'+f.id);assert.deepEqual(after,prior);
  assert.equal((await f.ok('/model-settings/reconcile',{sessionId:f.id,operationId:'configure-memory',kind:'configure'})).found,true);assert.equal(f.tasks.length,0);
  await writeFile(join(f.directory,'http-evidence.json'),JSON.stringify({current:after.session.models.current,tasks:f.tasks},null,2));
 });
 test('multi-tab stale configuration and capability drift reject without overwriting previous values',async t=>{
  const f=await webFixture(t),catalog=(await f.ok('/model-settings/catalog?parameterVersion=2')).catalog,e=catalog.models[0],d=explicit(typedParameterDraft(e),'temperature','0.8'),view=await f.ok('/model-settings/sessions/'+f.id),input={sessionId:f.id,operationId:'first',expectedRevision:view.session.revision,expectedModelRevision:0,purpose:'story',choice:typedSelection(catalog,e,d,'story')};
  await f.ok('/model-settings/configure',input);
  assert.equal((await f.call('/model-settings/configure',{...input,operationId:'stale'})).data.error.code,'OFFLINE_REVISION_CONFLICT');
  f.policies.story.fields.temperature.maximum=1;
  assert.notEqual((await f.call('/model-settings/check',{choice:input.choice})).status,200);
  const after=await f.ok('/model-settings/sessions/'+f.id);assert.equal(after.session.models.current.story.parameters.temperature,.8);assert.equal(f.tasks.length,0);
 });
 test('applied UI parameters reach the captured controlled request unchanged',async t=>{
  const f=await webFixture(t),c=(await f.ok('/model-settings/catalog?parameterVersion=2')).catalog,e=c.models[0],d=typedParameterDraft(e),initial=await f.ok('/model-settings/sessions/'+f.id);
  for(const [k,v] of Object.entries({temperature:'0.9',topK:'22',seed:'41',stop:[' 结束 '],'thinking.enabled':true,'thinking.budgetTokens':'512'}))explicit(d,k,v);
  await f.ok('/model-settings/configure',{sessionId:f.id,operationId:'settings',expectedRevision:initial.session.revision,expectedModelRevision:0,purpose:'story',choice:typedSelection(c,e,d,'story')});
  const view=await f.ok('/joint/sessions/'+f.id),s=view.session;
  await f.host.sessionHost.send({sessionId:f.id,operationId:'send',expectedRevision:s.revision,expectedModelRevision:s.models.revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision,expectedContextPolicyRevision:view.effectivePolicy.revision,kind:'send',input:'参数回放'});await f.host.sessionHost.waitForIdle(f.id);
  assert.equal(f.tasks.length,1);assert.deepEqual(f.tasks[0].parameters,typedParameterConfig(e,d));
  const result=await f.ok('/joint/sessions/'+f.id);assert.equal(result.session.turns.length,1);assert.equal(result.budget.used,1);
  const receipt=[...f.results.values()][0].receipt;assert.deepEqual(receipt.parameters,{max_tokens:4096,temperature:.9,top_k:22,seed:41,stop:[' 结束 '],reasoning:{enabled:true,max_tokens:512}});
  await writeFile(join(f.directory,'request-evidence.json'),JSON.stringify({request:f.tasks[0],receipt},null,2));
 });

 const presetInput=(choice,id='sample',revision=0)=>{const {format,profile,...selection}=choice;return {operationId:'save-'+id,expectedRevision:revision,id,name:'细腻叙述 '+id,expectedPresetRevision:0,profile,selection};};
 test('preset draft is purpose-bound, preserves unsupported fields and never mutates its source',()=>{
  const source=entry(),target=entry('other');delete target.parameterPolicy.fields.topK;
  const d=explicit(typedParameterDraft(source),'topK','23'),c=typedSelection({revision:'r',models:[source]},source,d,'story'),saved=clone(c.profile);
  const loaded=profileDraft(target,c.profile,'story');assert.equal(loaded.topK.value,23);assert.ok(typedDraftIssues(target,loaded,'story').some(i=>i.field==='topK'));
  assert.deepEqual(c.profile,saved);assert.throws(()=>profileDraft(target,c.profile,'memory'),code('PARAMETER_PURPOSE_INVALID'));
  assert.throws(()=>typedSelection({revision:'r',models:[target]},target,loaded,'story'),code('PARAMETER_UNSUPPORTED'));
 });
 test('recommendation changes only one purpose in a copy and refuses credentials or legacy overwrite',()=>{
  const document=blankDocument();document.sources.system='原文\r\n  缩进与空行\n';document.metadata.name='未保存标题';
  const first=withRecommendedProfile(document,profile('summary',{temperature:{mode:'explicit',value:.1}}));
  const second=withRecommendedProfile(first,profile('story',{stop:{mode:'explicit',value:[' 结束 ']}}));
  assert.deepEqual(second.model.recommendations.summary,first.model.recommendations.summary);assert.equal(second.model.recommendations.memory,null);assert.equal(document.model.recommendations,undefined);
  assert.equal(second.sources.system,document.sources.system);assert.equal(second.metadata.name,document.metadata.name);
  assert.throws(()=>withRecommendedProfile(document,{...profile('story'),serviceToken:'not-a-secret'}),code('PARAMETER_PROFILE_INVALID'));
  assert.throws(()=>withRecommendedProfile({...document,model:{...document.model,selection:{old:true}}},profile('story')),code('PARAMETER_LEGACY_RECOMMENDATION'));
 });
 test('preset journal refuses mismatched identity; receipt verification binds complete original intent',async()=>{
  const e=entry(),body=presetInput(typedSelection({revision:sha256('catalog'),models:[e]},e,typedParameterDraft(e),'story'));
  const key='modelmirror.authoring.parameter-preset-pending/'+body.operationId,storage={[key]:JSON.stringify(body),getItem(k){return this[k];}};
  assert.deepEqual(pendingPresetOperation(storage),body);
  const operation={operationId:body.operationId,revision:1,requestHash:digest(body),result:{id:body.id,name:body.name,revision:1,configuration:{profile:body.profile,selection:body.selection}}};
  assert.equal(await presetReceiptMatches(body,operation),true);assert.equal(await presetReceiptMatches({...body,name:'different'},operation),false);
  const wrong=clone(operation);wrong.result.configuration.profile.parameters.values.temperature={mode:'explicit',value:1};assert.equal(await presetReceiptMatches(body,wrong),false);
  storage[key]=JSON.stringify({...body,operationId:'other'});assert.throws(()=>pendingPresetOperation(storage),code('MODEL_UI_STORAGE_UNAVAILABLE'));
 });
 test('presets HTTP uses CAS and immutable idempotent receipts, zero tasks and separate purposes',async t=>{
  const f=await webFixture(t,{execution:false}),before=await f.ok('/joint/sessions/'+f.id),catalog=(await f.ok('/model-settings/catalog?parameterVersion=2')).catalog,e=catalog.models[0];
  assert.equal((await f.ok('/model-settings')).presetsEnabled,true);
  const bodies=[];
  for(const [index,purpose] of ['story','summary','memory'].entries()){
   const draft=explicit(typedParameterDraft(e),'temperature',String(index/10));explicit(draft,'stop',[' 结束 ']);
   const body=presetInput(typedSelection(catalog,e,draft,purpose),purpose,index),receipt=await f.ok('/model-settings/presets',body);bodies.push(body);
   assert.equal(await presetReceiptMatches(body,receipt),true);assert.equal((await f.ok('/model-settings/presets',body)).replayed,true);
  }
  const list=await f.ok('/model-settings/presets');assert.equal(list.revision,3);assert.equal(list.presets.length,3);
  assert.deepEqual(list.presets[0].profile.parameters.values.stop,{mode:'explicit',value:[' 结束 ']});assert.equal(list.presets[0].profile.parameters.values.topK.mode,'default');
  assert.equal((await f.call('/model-settings/presets',{...bodies[0],operationId:'collision',id:'collision'})).data.error.code,'PARAMETER_REVISION_CONFLICT');
  assert.equal((await f.call('/model-settings/presets',{...bodies[0],name:'changed'})).data.error.code,'PARAMETER_OPERATION_COLLISION');
  assert.deepEqual(await f.ok('/joint/sessions/'+f.id),before);assert.equal(f.tasks.length,0);
  await f.reopen();assert.deepEqual(await f.ok('/model-settings/presets'),list);
  const recovered=await f.ok('/model-settings/presets/reconcile',{operationId:bodies[0].operationId});assert.equal(recovered.status,'committed');assert.equal(await presetReceiptMatches(bodies[0],recovered.operation),true);
  await writeFile(join(f.directory,'p2b-preset-http.json'),JSON.stringify({list,recovered,budget:before.budget,tasks:f.tasks},null,2));
 });
 test('lost HTTP preset response is recovered after restart by original ID without a second save',async t=>{
  const f=await webFixture(t,{execution:false}),catalog=(await f.ok('/model-settings/catalog?parameterVersion=2')).catalog,e=catalog.models[0],body=presetInput(typedSelection(catalog,e,typedParameterDraft(e),'story'),'lost');let forwarded=0,proxyError;
  const proxy=createServer(async(req,res)=>{try{const chunks=[];for await(const chunk of req)chunks.push(chunk);forwarded++;await f.ok('/model-settings/presets',JSON.parse(Buffer.concat(chunks).toString()));res.destroy();}catch(e){proxyError=e;res.destroy();}});
  await new Promise(r=>proxy.listen(0,'127.0.0.1',r));t.after(async()=>{proxy.closeAllConnections();await new Promise(r=>proxy.close(r));if(proxyError)throw proxyError;});
  await assert.rejects(fetch('http://127.0.0.1:'+proxy.address().port,{method:'POST',body:JSON.stringify(body)}));assert.equal(forwarded,1);
  await f.reopen();const recovered=await f.ok('/model-settings/presets/reconcile',{operationId:body.operationId});assert.equal(recovered.status,'committed');assert.equal(await presetReceiptMatches(body,recovered.operation),true);
  assert.equal((await f.ok('/model-settings/presets')).revision,1);assert.equal(f.tasks.length,0);
 });
 test('preset capability drift and untrusted fields fail closed, with same-origin and feature gates',async t=>{
  const f=await webFixture(t,{execution:false}),c=(await f.ok('/model-settings/catalog?parameterVersion=2')).catalog,e=c.models[0],body=presetInput(typedSelection(c,e,explicit(typedParameterDraft(e),'temperature','1.5'),'story'));
  const response=await fetch(f.host.origin+'/rpg-app/api/authoring/model-settings/presets',{method:'POST',headers:{Origin:'http://different.invalid','Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});assert.equal(response.status,403);
  assert.notEqual((await f.call('/model-settings/presets',{...body,providerUrl:'http://forbidden.invalid'})).status,200);
  assert.notEqual((await f.call('/model-settings/presets/reconcile',{operationId:'absent',kind:'session'})).status,200);
  f.policies.story.fields.temperature.maximum=1;assert.notEqual((await f.call('/model-settings/presets',body)).status,200);assert.equal((await f.ok('/model-settings/presets')).revision,0);
  assert.equal((await f.ok('/model-settings/presets/reconcile',{operationId:'absent'})).status,'not_found');assert.equal(f.tasks.length,0);
  const disabled=await webFixture(t,{execution:false,creatorExperience:false});assert.equal((await disabled.ok('/model-settings')).presetsEnabled,undefined);assert.equal((await disabled.call('/model-settings/presets')).status,404);
 });
 test('work recommendation save, publish, export and clean-directory import preserve source and never configure sessions',async t=>{
  const f=await webFixture(t,{execution:false}),old=await f.ok('/joint/sessions/'+f.id),work=await f.ok('/works/'+f.work.workId);
  const draft=clone(work.draft);draft.metadata.name='未保存的标题';draft.sources.system='原文\r\n  空行\n\n';
  let document=withRecommendedProfile(draft,profile('story',{temperature:{mode:'explicit',value:0},stop:{mode:'explicit',value:[' 结束 ']}}));document=withRecommendedProfile(document,profile('memory',{temperature:{mode:'default'}}));
  const receipt=await f.ok('/works/'+work.id+'/save',{operationId:'recommended',expectedRevision:work.revision,document});
  assert.deepEqual((await f.ok('/works/'+work.id)).draft,document);assert.deepEqual(await f.ok('/joint/sessions/'+f.id),old);
  const stale=await f.call('/works/'+work.id+'/save',{operationId:'stale-recommended',expectedRevision:work.revision,document:draft});assert.equal(stale.data.error.code,'AUTHORING_REVISION_CONFLICT');
  const version=await f.ok('/works/'+work.id+'/publish',{operationId:'recommended-version',expectedRevision:receipt.revision});
  const exported=await f.ok('/works/'+work.id+'/export?kind=package&version='+version.result.versionId),fresh=await webFixture(t,{execution:false}),budget=await fresh.ok('/joint/sessions/'+fresh.id);
  const inspected=await fresh.ok('/imports/inspect',{payload:exported});assert.equal(inspected.valid,true);
  const imported=await fresh.ok('/imports',{operationId:'new-directory',expectedRevision:null,workId:null,payload:exported,expectedImportHash:inspected.payloadHash});assert.deepEqual((await fresh.ok('/works/'+imported.workId)).draft,document);
  for(const forbidden of ['serviceToken','capabilityRevision','selectionRevision','wireParameters','authorization','quota'])assert.equal(JSON.stringify(exported).includes('"'+forbidden+'"'),false);
  assert.deepEqual(await f.ok('/joint/sessions/'+f.id),old);assert.deepEqual(await fresh.ok('/joint/sessions/'+fresh.id),budget);assert.equal(f.tasks.length+fresh.tasks.length,0);
  await writeFile(join(f.directory,'p2b-recommendation-http.json'),JSON.stringify({receipt,version,imported,recommendations:document.model.recommendations,oldSessionUnchanged:true,tasks:[]},null,2));
 });
}

import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {canonical,sha256} from '../authoring/contracts.mjs';
import {ControlPlaneModelControl,PARAMETER_SELECTION_FORMAT} from '../authoring/model-control.mjs';
import {PARAMETER_PROFILE,PARAMETER_CONFIG} from '../authoring/generation-settings.mjs';
import {createGenerationCards,GENERATION_CARDS,frozenCardAssembly} from './generation-cards.mjs';
import {newSetup} from '../god-card/lib/setup.mjs';
import {openCardParameterTransport} from './card-generation.mjs';
const check=process.argv.includes('--preview')?()=>{}:test;
const digest=v=>sha256(canonical(v)),clone=structuredClone;
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
 const f={tasks:[],requests:[],lookups:[],results:new Map(),policies:Object.fromEntries(['story','summary','memory'].map(k=>[k,policy()])),onCheck:null,corrupt:null,unavailable:false,blockLookup:!!options.blockLookup};
 f.policies.summary.exclusive=[['temperature','topP']];delete f.policies.summary.fields.topK;
 f.policies.memory.requires=[{if:{field:'thinking.budgetTokens',op:'set'},then:{field:'thinking.enabled',op:'eq',value:true}}];
 f.catalog=(version=2)=>{
  const models=Object.entries(f.policies).map(([id,p])=>({selectionId:id,selectionRevision:sha256(id+'-qualification'),modelId:'synthetic/'+id,name:({story:'澄明 · 全参数样例',summary:'简约 · 互斥样例',memory:'深思 · 依赖样例'})[id],available:true,contextLength:options.contextLength??2000000,maxOutputTokens:16000,parameterEvidence:'provider-declared',
   ...(version===2?{capabilityRevision:digest(p),parameterPolicy:{...clone(p),fields:Object.fromEntries(Object.entries(p.fields).map(([k,{wireName,...v}])=>[k,v]))}}:
     {parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}})}));
  const body={format:'modelmirror.rpg.control-model-catalog/'+version,source:'control-plane-cache',catalogId:'parameter-ui-test',models};return {...body,revision:digest(body)};
 };
 f.selection=(purpose,values={})=>{const c=f.catalog(),e=c.models.find(m=>m.selectionId===purpose);return {format:PARAMETER_SELECTION_FORMAT,catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision,profile:profile(purpose,values)};};
 f.resolve=p=>JSON.parse(execFileSync(python,['-X','utf8','-B','-c',resolver,parameterSource],{input:JSON.stringify({policy:f.policies[p.selectionId],parameters:p.parameters,purpose:p.purpose,capabilityRevision:p.capabilityRevision}),encoding:'utf8',windowsHide:true}));
 const json=(res,value,status=200)=>{res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(value));};
 let dropped=false;
 f.setLookupBlocking=value=>{f.blockLookup=value;};
 const server=createServer(async(req,res)=>{
  try {
   assert.equal(req.headers.authorization,'Bearer '+token);const url=new URL(req.url,'http://localhost');
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const p=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
   f.requests.push({method:req.method,path:req.url,body:p});
   if(options.captureDirectory)await writeFile(join(options.captureDirectory,'control-capture.json'),JSON.stringify({requests:f.requests,tasks:f.tasks},null,2));
   if(url.pathname.endsWith('/models')){if(f.onCatalog)await f.onCatalog();return f.unavailable?json(res,{},503):json(res,f.catalog(url.searchParams.get('parameterVersion')==='2'?2:1));}
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
    f.lookups.push(req.url);if(f.blockLookup)return json(res,{},503);const result=f.results.get(url.pathname.slice('/api/rpg/v1/authoring/tasks/'.length));
    if(result)assert.equal(result.receipt.sourceRequestHash,url.searchParams.get('sourceRequestHash'));
    return json(res,result??{status:'absent',sourceRequestHash:url.searchParams.get('sourceRequestHash')});
   }
   throw new Error('unexpected fixture route '+req.url);
  } catch(error){f.error=error;return json(res,{error:'fixture-error'},500);}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));f.origin='http://127.0.0.1:'+server.address().port;
 f.control=new ControlPlaneModelControl({origin:f.origin,serviceToken:token,timeoutMs:options.controlTimeout??5000,ttlMs:options.ttlMs??30000});await f.control.refresh();
 t.after(async()=>{server.closeAllConnections();await new Promise(r=>server.close(r));if(f.error)throw f.error;});return f;
}

async function fixture(t,options={}){
 const directory=options.directory||await mkdtemp(join(tmpdir(),'rpg-six-card-'));await mkdir(directory,{recursive:true});
 const f=await controlFixture(t,{...options,captureDirectory:directory});
 const config={directory:join(directory,'sessions'),budgetDirectory:join(directory,'budget'),origin:f.origin,serviceToken:token,defaultModel:'synthetic/story',recoverDeadOwner:!!options.recoverDeadOwner,limits:Object.fromEntries(Object.keys(GENERATION_CARDS).map(k=>[k,30]))};
 const h=await createGenerationCards({...config,control:f.control});t.after(()=>h.close());return {...f,h,config,directory};
}
const inputFor=id=>id==='earth'?{characterText:'姓名：林舟\n身份：成年旅行者',world:'表世界'}:id==='god'?{setup:newSetup()}:{opening:'姓名：林舟\n身份：成年旅行者'};
async function grant(f,s,id,action='enable'){
 let c=await f.h.plugins.catalog(),e=c.plugins.find(x=>x.id===id);assert.ok(e,id);
 if(action==='enable'&&!e.installed){await f.h.plugins.change('install',{operationId:randomUUID(),pluginId:id,version:e.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:c.revision});c=await f.h.plugins.catalog();e=c.plugins.find(x=>x.id===id);}
 await f.h.plugins.change(action,{operationId:randomUUID(),pluginId:id,version:e.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:c.revision,sessionId:s.id,expectedSessionRevision:s.revision,...(action==='enable'?{permissions:e.permissions}:{})});
}
async function save(f,store,id,purpose,values,model=purpose){const s=await store.read(id),status=await store.parameterStatus(id),selection=f.selection(model,values);selection.profile.purpose=purpose;
 return store.configureParameter(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedParameterRevision:status.revision,baseHash:status.base[purpose].configHash,purpose,selection});}
async function send(store,id,input){const s=await store.read(id),p=await store.parameterStatus(id),m=await store.memoryTasks.status(id);const op=await store.send(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:s.memoryPalace.revision,expectedContextPolicyRevision:m.effectivePolicy.revision,input:input??s.characterText,expectedGeneration:p.expected});await store.memoryTasks.wait(id);await store.summaryTasks.wait(id);const done=await store.read(id);return done.memoryTasks.operations[op.input.operationId]||done.summaryTasks.operations[op.input.operationId];}
for(const id of Object.keys(GENERATION_CARDS))check(id+' sends actual typed parameters with original card assembly',async t=>{
 const f=await fixture(t),store=f.h.hosts.get(id).store,s=await store.create(inputFor(id));await grant(f,s,'rpg.generation-parameters');
 await save(f,store,s.id,'story',{temperature:{mode:'explicit',value:0},stop:{mode:'explicit',value:[' 终止 ' ]},seed:{mode:'explicit',value:42}},'story');assert.equal(f.tasks.length,0);
 const before=await store.read(s.id),expected=await (await frozenCardAssembly(id))(before,before.characterText),op=await send(store,s.id);assert.equal(op.status,'complete',JSON.stringify({error:op.error,tasks:op.tasks.map(t=>({status:t.status,output:t.output?.failure}))}));assert.equal(f.tasks.length,1);
 const task=f.tasks[0];assert.deepEqual(task.messages,expected.messages||expected);assert.equal(task.parameters.values.temperature.value,0);assert.deepEqual(task.parameters.values.stop.value,[' 终止 ']);
 const after=await store.read(s.id);assert.equal(after.turns.length,1);assert.equal(after.turns[0].model.selection.parameters.temperature,0);assert.equal(after.turns[0].raw,'A complete original reply.');
 await grant(f,after,'rpg.generation-parameters','disable');assert.equal((await send(store,s.id,'下一步')).status,'complete');assert.deepEqual(f.tasks[1].parameters.values,{});assert.equal((await store.read(s.id)).generationParameters.values.story.parameterProfile.parameters.values.seed.value,42);
});

async function setPurposeModels(f,store,id,{memory=true,summary=true}={}){
 if(summary){await grant(f,await store.read(id),'rpg.rolling-summary');const s=await store.read(id),e=f.catalog().models.find(m=>m.selectionId==='summary');await store.configureSummary(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:s.rollingSummary.revision,timing:'after',selectionId:e.selectionId,catalogRevision:e.selectionRevision},f.h.plugins);}
 if(memory){await grant(f,await store.read(id),'rpg.memory-palace');const s=await store.read(id),e=f.catalog().models.find(m=>m.selectionId==='memory');await store.memoryTasks.configure(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:s.memoryPalace.revision,selectionId:e.selectionId,catalogRevision:e.selectionRevision});}
}
check('three purposes use independent advanced values in story -> M2 -> M3 order',async t=>{
 const f=await fixture(t),store=f.h.hosts.get('god').store,s=await store.create(inputFor('god'));await grant(f,s,'rpg.generation-parameters');await setPurposeModels(f,store,s.id);
 await save(f,store,s.id,'story',{temperature:{mode:'explicit',value:0.7}},'story');await save(f,store,s.id,'summary',{temperature:{mode:'explicit',value:0.1}},'summary');await save(f,store,s.id,'memory',{'thinking.enabled':{mode:'explicit',value:true},'thinking.budgetTokens':{mode:'explicit',value:128}},'memory');
 assert.equal(f.tasks.length,0);await send(store,s.id);const op=await send(store,s.id,'继续');assert.equal(op.status,'complete',JSON.stringify({error:op.error,tasks:op.tasks.map(t=>({status:t.status,output:t.output?.failure}))}));assert.deepEqual(f.tasks.map(t=>t.purpose),['story','memory','story','summary','memory']);assert.equal(f.tasks[3].parameters.values.temperature.value,0.1);assert.equal(f.tasks[4].parameters.values['thinking.budgetTokens'].value,128);
 const after=await store.read(s.id);assert.equal(after.turns.length,2);assert.equal(after.memoryPalace.processedThrough,2);
});
check('M2 background uses its own frozen parameters',async t=>{const f=await fixture(t),store=f.h.hosts.get('earth').store,s=await store.create(inputFor('earth'));await grant(f,s,'rpg.generation-parameters');await setPurposeModels(f,store,s.id,{memory:false});await save(f,store,s.id,'summary',{temperature:{mode:'explicit',value:0.2}});await send(store,s.id);assert.equal((await send(store,s.id,'继续')).status,'complete');assert.deepEqual(f.tasks.map(t=>t.purpose),['story','story','summary'],JSON.stringify(Object.values((await store.read(s.id)).summaryTasks.operations).map(o=>({kind:o.kind,error:o.error,tasks:o.tasks.map(t=>({status:t.status,failure:t.output?.failure}))}))));assert.equal(f.tasks[2].parameters.values.temperature.value,0.2);const settled=await store.read(s.id);assert.ok(Object.values(settled.summaryTasks.operations).filter(o=>o.kind==='update').every(o=>o.status==='complete'));assert.ok(settled.rollingSummary.versions.length>0);});
check('stale revisions, unsupported parameters and changed model block before dispatch',async t=>{
 const f=await fixture(t),store=f.h.hosts.get('earth').store,s=await store.create(inputFor('earth'));await grant(f,s,'rpg.generation-parameters');const old=await store.parameterStatus(s.id);await save(f,store,s.id,'story',{seed:{mode:'explicit',value:1}},'story');
 const current=await store.read(s.id),m=await store.memoryTasks.status(s.id);await assert.rejects(store.send(s.id,{operationId:randomUUID(),input:s.characterText,expectedSessionRevision:current.revision,expectedMemoryRevision:current.memoryPalace.revision,expectedContextPolicyRevision:m.effectivePolicy.revision,expectedGeneration:old.expected}),{code:'PARAMETER_CONFIGURATION_CHANGED'});
 await assert.rejects(save(f,store,s.id,'story',{seed:{mode:'explicit',value:-1}},'story'));
 await grant(f,current,'rpg.model-selector');await store.select(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,selectionRevision:current.modelState.revision,selectionId:'summary',catalogRevision:f.catalog().models[1].selectionRevision});
 await assert.rejects(send(store,s.id),{code:'PARAMETER_MODEL_MISMATCH'});assert.equal(f.tasks.length,0);assert.equal((await f.h.hosts.get('earth').io.budget.status()).used,0);
});


check('unknown story recovers by original ID after restart without a second POST or debit',async t=>{
 const f=await fixture(t,{dropPurpose:'story',blockLookup:true}),store=f.h.hosts.get('earth').store,s=await store.create(inputFor('earth'));await grant(f,s,'rpg.generation-parameters');await save(f,store,s.id,'story',{seed:{mode:'explicit',value:7}},'story');
 const op=await send(store,s.id);assert.equal(op.status,'unknown');assert.equal((await store.read(s.id)).turns.length,0);assert.equal(f.tasks.length,1);await f.h.close();
 const restarted=await createGenerationCards({...f.config,control:f.control});t.after(()=>restarted.close());f.setLookupBlocking(false);
 const nextStore=restarted.hosts.get('earth').store;const result=await nextStore.recoverMemoryOperation(s.id,op.input.operationId);assert.equal(result.status,'complete');assert.equal((await nextStore.read(s.id)).turns.length,1);assert.equal(f.tasks.length,1);assert.ok(f.lookups.every(url=>url.includes(op.tasks[0].requestId)));assert.equal((await restarted.hosts.get('earth').io.budget.status()).used,1);assert.equal((await nextStore.recoverMemoryOperation(s.id,op.input.operationId)).status,'complete');assert.equal((await nextStore.read(s.id)).turns.length,1);
});
check('concurrent model list and parameter reads share metadata without invalidating selection',async t=>{
 const f=await fixture(t),store=f.h.hosts.get('god').store,s=await store.create(inputFor('god'));await grant(f,s,'rpg.generation-parameters');
 const results=await Promise.all([store.control.catalog(),store.parameterCatalog(s.id,'story'),store.parameterCatalog(s.id,'summary'),store.parameterCatalog(s.id,'memory')]);
 assert.equal(results[0].filter(e=>e.available).length,3);for(const c of results.slice(1))assert.equal(c.models.length,3);
 await save(f,store,s.id,'story',{temperature:{mode:'explicit',value:0.41}});const op=await send(store,s.id);assert.equal(op.status,'complete');assert.equal(f.tasks[0].parameters.values.temperature.value,0.41);
});
check('background validation waits for an overlapping catalog read and still rejects actual drift',async t=>{
 const f=await controlFixture(t),choice=await f.control.selectParameters(f.selection('memory'));
 async function overlap(drift=false){let entered,release;const started=new Promise(r=>entered=r),gate=new Promise(r=>release=r);f.onCatalog=async()=>{entered();await gate;};
  const refresh=f.control.parameterCatalog();await started;const checked=f.control.validate(choice);const observed=checked.then(v=>({value:v}),e=>({error:e.code}));
  if(drift)f.policies.memory.fields.maxOutputTokens.maximum=8000;f.onCatalog=null;release();await refresh;return observed;
 }
 assert.equal((await overlap()).value?.valid,true);
 assert.equal((await overlap(true)).error,'MODEL_SELECTION_CHANGED');assert.equal(f.tasks.length,0);
 f.unavailable=true;await assert.rejects(f.control.parameterCatalog());assert.throws(()=>f.control.assertCurrent(choice),{code:'MODEL_SELECTION_UNAVAILABLE'});
});
check('expired capability cache is refreshed for saved overrides and delayed memory',async t=>{
 const f=await fixture(t,{ttlMs:1500,onTask:async p=>{if(p.purpose==='story')await new Promise(r=>setTimeout(r,1800));}}),store=f.h.hosts.get('god').store,s=await store.create(inputFor('god'));
 await grant(f,s,'rpg.generation-parameters');await setPurposeModels(f,store,s.id,{summary:false});await save(f,store,s.id,'story',{temperature:{mode:'explicit',value:0.41}});await save(f,store,s.id,'memory',{temperature:{mode:'explicit',value:0.2}});
 await new Promise(r=>setTimeout(r,1800));const op=await send(store,s.id);assert.equal(op.status,'complete');assert.deepEqual(f.tasks.map(t=>t.purpose),['story','memory']);assert.equal(f.tasks[0].parameters.values.temperature.value,0.41);assert.equal(f.tasks[1].parameters.values.temperature.value,0.2);assert.equal((await store.read(s.id)).memoryPalace.processedThrough,1);
});
check('branch inherits the selected turn parameters, never future edits or authorization',async t=>{
 const f=await fixture(t),store=f.h.hosts.get('earth').store,s=await store.create(inputFor('earth'));await grant(f,s,'rpg.generation-parameters');await save(f,store,s.id,'story',{seed:{mode:'explicit',value:7}},'story');await send(store,s.id);await save(f,store,s.id,'story',{seed:{mode:'explicit',value:19}},'story');await grant(f,await store.read(s.id),'rpg.branch-save');
 const current=await store.read(s.id),out=await store.createBranch(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,turn:1,name:'分支核验'}),child=await store.read(out.session.id);await store.requireRuntime(child);assert.equal(child.generationParameters.values.story.parameterProfile.parameters.values.seed.value,7);assert.equal(child.generationParameters.revision,0);assert.equal((await f.h.plugins.parameterAuthorization(child.id)).enabled,false);assert.equal(f.tasks.length,1);
});
check('capacity failure is undispatched and cannot quietly truncate the card',async t=>{
 const f=await fixture(t,{contextLength:10000}),store=f.h.hosts.get('cultivation').store,s=await store.create(inputFor('cultivation'));
 const op=await send(store,s.id);assert.equal(op.status,'failed');assert.equal(op.tasks[0].output.failure,'MODEL_CONTEXT_CAPACITY');assert.equal(f.tasks.length,0);assert.equal((await f.h.hosts.get('cultivation').io.budget.status()).used,0);assert.equal((await store.read(s.id)).history.length,0);
});
check('saved settings are idempotent and file failure keeps the old revision',async t=>{
 const f=await fixture(t),store=f.h.hosts.get('earth').store,s=await store.create(inputFor('earth'));await grant(f,s,'rpg.generation-parameters');const q=await store.parameterStatus(s.id),input={operationId:randomUUID(),expectedSessionRevision:s.revision,expectedParameterRevision:q.revision,baseHash:q.base.story.configHash,purpose:'story',selection:f.selection('story',{seed:{mode:'explicit',value:0}})};
 const write=store.write.bind(store);store.write=async()=>{throw Error('simulated disk failure');};await assert.rejects(store.configureParameter(s.id,input));store.write=write;assert.equal((await store.parameterStatus(s.id)).revision,0);assert.equal((await store.parameterOperation(s.id,input.operationId)).status,'not-applied');const saved=await store.configureParameter(s.id,input);assert.deepEqual(await store.configureParameter(s.id,input),saved);assert.equal(f.tasks.length,0);
});

check('HTTP contract keeps nine plugins, six routes, presets, import and first-turn rewrite',async t=>{
 const f=await fixture(t);await f.h.close();const {start}=await import('./server.mjs');const {serialize}=await import('../plugins/transcript.mjs');
 const imageService={catalog:async()=>({status:'ready',profiles:[]}),generate:async()=>{throw Error('image generation is not authorized');}};
 const host=await start({port:0,directory:join(f.directory,'http-legacy'),godCard:true,godPlugins:true,transcript:true,illustrationService:imageService,generationCards:{...f.config,control:f.control}});
 t.after(async()=>{await host.generationHost.close();await host.plugins.close();await new Promise(r=>host.server.close(r));});
 const current={...f,h:host.generationHost},base='http://127.0.0.1:'+host.server.address().port;
 async function api(path,body){const r=await fetch(base+path,body?{method:'POST',headers:{origin:base,'content-type':'application/json'},body:JSON.stringify(body)}:{});const v=await r.json();assert.ok(r.ok,JSON.stringify(v));return v;}
 const catalog=await api('/rpg-app/api/plugins');assert.equal(catalog.plugins.length,9);
 assert.equal((await fetch(base+'/rpg-app/earth/api/sessions',{method:'POST',headers:{origin:'http://untrusted.invalid','content-type':'application/json'},body:'{}'})).status,403);
 for(const card of Object.keys(GENERATION_CARDS)){
  const prefix='/rpg-app/'+card+(['ancient','cultivation','lord-of-mysteries'].includes(card)?'/chat':'')+'/api/',store=current.h.hosts.get(card).store;
  const created=await api(prefix+'sessions',inputFor(card)),s=await store.read(created.id),path=prefix+'sessions/'+s.id;
  await grant(current,s,'rpg.generation-parameters');await grant(current,s,'rpg.transcript');await grant(current,s,'rpg.illustrations');
  const state=await api(path+'/generation-parameters');assert.equal(state.authorization.enabled,true);assert.equal((await api(prefix+'status')).providerEnabled,true);assert.deepEqual((await api(path+'/illustrations/models')).profiles,[]);
  const input={operationId:randomUUID(),expectedSessionRevision:state.sessionRevision,expectedParameterRevision:state.revision,baseHash:state.base.story.configHash,purpose:'story',selection:f.selection('story',{seed:{mode:'explicit',value:5}})};
  assert.equal((await api(path+'/generation-parameters/check',input)).dispatched,false);assert.equal((await api(path+'/generation-parameters',input)).status,'applied');assert.deepEqual(await api(path+'/generation-parameters/operations?operationId='+input.operationId),await api(path+'/generation-parameters',input));
  const presets=await api(path+'/generation-parameters/presets?purpose=story'),{format,profile:prof,...selection}=input.selection;
  const saved=await api(path+'/generation-parameters/presets',{operationId:randomUUID(),id:'preset-'+randomUUID(),expectedRevision:presets.revision,expectedPresetRevision:0,name:card+'参数',profile:prof,selection});assert.equal(saved.revision,presets.revision+1);
  const importId=randomUUID(),r=await fetch(base+path+'/transcript/import',{method:'POST',headers:{origin:base,'content-type':'text/plain','x-session-name':encodeURIComponent('导入回放'),'x-import-id':importId},body:serialize([{input:'旧输入',raw:'旧原文'}])});const imported=await r.json();assert.equal(r.status,201,JSON.stringify(imported));
  const child=await store.read(imported.id);await store.requireRuntime(child);assert.equal(child.generationParameters.values.story.parameterProfile.parameters.values.seed.value,5);assert.equal((await current.h.plugins.parameterAuthorization(child.id)).enabled,false);
  assert.equal(f.tasks.length,Object.keys(GENERATION_CARDS).indexOf(card),'configuration and import do not generate');
  const currentSession=await api(path),memory=await api(path+'/memory-palace'),operationId=randomUUID();
  const sent=await api(path+'/send',{operationId,expectedSessionRevision:currentSession.revision,expectedContextPolicyRevision:memory.effectivePolicy.revision,expectedMemoryRevision:memory.memoryRevision,expectedGeneration:currentSession.generationParameters.expected,input:currentSession.characterText});
  assert.equal(sent.requests[operationId].status,'complete');assert.equal(sent.turns[0].raw,'A complete original reply.');assert.match(sent.turns[0].html,/A complete original reply/);
  assert.equal((await api(path)).turns.length,1);assert.equal(f.tasks.at(-1).parameters.values.seed.value,5);
 }
 assert.equal(f.tasks.length,6);
 const store=current.h.hosts.get('earth').store,s=await store.create(inputFor('earth'));await grant(current,s,'rpg.generation-parameters');await grant(current,s,'rpg.message-tools');await save(current,store,s.id,'story',{seed:{mode:'explicit',value:9}});await send(store,s.id);await save(current,store,s.id,'story',{seed:{mode:'explicit',value:19}});
 await grant(current,await store.read(s.id),'rpg.history-window');await setPurposeModels(current,store,s.id);await save(current,store,s.id,'summary',{temperature:{mode:'explicit',value:0.2}});
 const beforeHttp=await api('/rpg-app/earth/api/sessions/'+s.id),mp=await api('/rpg-app/earth/api/sessions/'+s.id+'/memory-palace');
 const httpId=randomUUID(),httpSent=await api('/rpg-app/earth/api/sessions/'+s.id+'/send',{operationId:httpId,expectedSessionRevision:beforeHttp.revision,expectedMemoryRevision:mp.memoryRevision,expectedContextPolicyRevision:mp.effectivePolicy.revision,expectedGeneration:beforeHttp.generationParameters.expected,input:'继续'});assert.equal(httpSent.requests[httpId].status,'complete');await store.memoryTasks.wait(s.id);await store.summaryTasks.wait(s.id);const settledHttp=await store.read(s.id);assert.equal(settledHttp.memoryTasks.operations[httpId].status,'complete');assert.equal(settledHttp.memoryPalace.processedThrough,2);assert.equal(settledHttp.turns.length,2);
 const source=await store.read(s.id),fork=await api('/rpg-app/earth/api/sessions/'+s.id+'/message-tools/fork',{operationId:randomUUID(),revision:source.revision,turn:1}),child=await store.read(fork.id);await store.requireRuntime(child);assert.equal(child.generationParameters.values.story.parameterProfile.parameters.values.seed.value,9);assert.equal((await current.h.plugins.parameterAuthorization(child.id)).enabled,false);
});

if(process.argv.includes('--preview')){
 const directory=process.argv[process.argv.indexOf('--preview')+1],port=Number(process.argv[process.argv.indexOf('--preview')+2]);
 const f=await fixture({after(){}},{directory,recoverDeadOwner:true}),links={};
 for(const id of Object.keys(GENERATION_CARDS)){const store=f.h.hosts.get(id).store,existing=await store.list(),s=existing.length?await store.read(existing[0].id):await store.create(inputFor(id));
  for(const plugin of ['rpg.generation-parameters','rpg.model-selector','rpg.history-window','rpg.branch-save','rpg.message-tools'])if(!(await f.h.plugins.sessionStatus(s.id,plugin)).enabled)await grant(f,await store.read(s.id),plugin);
  if(!s.rollingSummary.config.model)await setPurposeModels(f,store,s.id);
  links[id]='http://127.0.0.1:'+port+'/rpg-app/'+id+(['ancient','cultivation','lord-of-mysteries'].includes(id)?'/chat/':'/')+'?session='+s.id;
 }
 await f.h.close();const {start}=await import('./server.mjs');
 const server=await start({port,directory:join(directory,'legacy-unmodified'),godCard:true,godPlugins:true,historyWindow:true,rollingSummary:true,memoryPalace:true,transcript:true,illustrationService:{catalog:async()=>({status:'ready',profiles:[]}),generate:async()=>{throw Error('OFFLINE_ONLY');}},generationCards:{...f.config,control:f.control}});
 for(const h of server.generationHost.hosts.values()){const original=h.store.send.bind(h.store);h.store.send=async(...args)=>{try{return await original(...args);}catch(e){console.error(e.stack);throw e;}};}
 await writeFile(join(directory,'preview.json'),JSON.stringify({links,port,mode:'loopback-fixture-only'},null,2));console.log(JSON.stringify({links,port,mode:'loopback-fixture-only'}));
}

check('zero allowance initializes an unrelated card but blocks every reservation without dispatch',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'card-zero-budget-'));
 const options={directory:join(dir,'requests'),budgetDirectory:join(dir,'budgets'),cardId:'earth',origin:'http://127.0.0.1:9',serviceToken:token,limit:0};
 const io=await openCardParameterTransport(options);t.after(()=>io.close());
 assert.deepEqual(await io.budget.status(),{limit:0,used:0,reserved:0,remaining:0});
 await assert.rejects(io.budget.reserve('a'.repeat(64),'unused-session',[{purpose:'story',stageId:'story'}]),e=>e.code==='BUDGET_EXHAUSTED');
 assert.equal(io.provider.dispatchCount,0);
 await assert.rejects(openCardParameterTransport({...options,limit:-1}),e=>e.code==='PARAMETER_BUDGET_REQUIRED');
 await assert.rejects(openCardParameterTransport({...options,limit:101}),e=>e.code==='PARAMETER_BUDGET_REQUIRED');
});

check('persistent requirements freeze by turn, survive restart, and cannot leak future edits into branches',async t=>{
 const f=await fixture(t),h=f.h.hosts.get('god'),store=h.store,s=await store.create(inputFor('god'));
 const status=await store.requirementsStatus(s.id),initialPolicy=(await store.memoryTasks.status(s.id)).effectivePolicy.revision;
 const intent={operationId:randomUUID(),expectedSessionRevision:status.sessionRevision,expectedRequirementsRevision:status.revision,text:'第二人称；直播关闭。'};
 const before=f.tasks.length,receipt=await store.configureRequirements(s.id,intent);
 assert.equal(f.tasks.length,before);assert.deepEqual(await store.configureRequirements(s.id,intent),receipt);
 assert.equal((await store.requirementsStatus(s.id)).text,intent.text);assert.notEqual((await store.memoryTasks.status(s.id)).effectivePolicy.revision,initialPolicy);
 await assert.rejects(store.configureRequirements(s.id,{...intent,operationId:randomUUID(),text:'过期写入'}),{code:'REQUIREMENTS_REVISION_CONFLICT'});
 let op=await send(store,s.id);assert.equal(op.status,'complete');assert.match(f.tasks.at(-1).messages.at(-1).content,/【持续要求】\n第二人称；直播关闭。\n【持续要求结束】$/);
 const first=(await store.read(s.id)).history[0].content;
 let st=await store.requirementsStatus(s.id);await store.configureRequirements(s.id,{operationId:randomUUID(),expectedSessionRevision:st.sessionRevision,expectedRequirementsRevision:st.revision,text:'第三人称；剧情辅助开启。'});
 op=await send(store,s.id,'检查门锁。');assert.equal(op.status,'complete');assert.match(f.tasks.at(-1).messages.at(-1).content,/第三人称；剧情辅助开启/);
 const current=await store.read(s.id);assert.equal(current.turns[1].input,'检查门锁。');assert.equal(current.history[0].content,first);await h.profile.context.verifyMemory(current);
 await grant(f,current,'rpg.branch-save');const {session:branch}=await store.createBranch(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,turn:1,name:'无未来要求'},f.h.plugins);
 assert.equal((await store.requirementsStatus(branch.id)).text,intent.text);await h.profile.context.verifyMemory(await store.read(branch.id));
 st=await store.requirementsStatus(s.id);const failed={operationId:randomUUID(),expectedSessionRevision:st.sessionRevision,expectedRequirementsRevision:st.revision,text:'不得部分保存'};
 const write=store.write.bind(store);store.write=async()=>{throw Error('disk failure');};await assert.rejects(store.configureRequirements(s.id,failed));store.write=write;
 assert.equal((await store.requirementsStatus(s.id)).text,st.text);assert.equal((await store.requirementsOperation(s.id,failed.operationId)).status,'not-applied');
 await assert.rejects(store.configureRequirements(s.id,{...failed,text:'字'.repeat(8001)}),{code:'REQUIREMENTS_INPUT_INVALID'});
 await f.h.close();const reopened=await createGenerationCards({...f.config,control:f.control});t.after(()=>reopened.close());
 assert.equal((await reopened.hosts.get('god').store.requirementsStatus(s.id)).text,st.text);await reopened.hosts.get('god').profile.context.verifyMemory(await reopened.hosts.get('god').store.read(s.id));
 assert.equal(f.tasks.length,2);
});

check('parameter host output editing keeps typed settings and receipts, without dispatch',async t=>{
 const f=await fixture(t),store=f.h.hosts.get('earth').store,s=await store.create(inputFor('earth'));
 await grant(f,s,'rpg.message-tools');await grant(f,s,'rpg.generation-parameters');
 await save(f,store,s.id,'story',{temperature:{mode:'explicit',value:0.25}},'story');
 await send(store,s.id);const before=await store.read(s.id),count=f.tasks.length;
 let result;
 const edit={operationId:randomUUID(),revision:before.revision,turn:1,text:'人工修订：旧码头开放。'};
 await f.h.handle({pathname:'/rpg-app/earth/api/sessions/'+s.id+'/message-tools/edit-output',method:'POST',data:edit,query:new URLSearchParams(),send:(status,value)=>{assert.equal(status,200);result=value;}});
 assert.equal(result.saved,true);assert.equal(f.tasks.length,count);
 const after=await store.read(s.id);assert.deepEqual(after.generationParameters,before.generationParameters);assert.deepEqual(after.requests,before.requests);assert.deepEqual(after.turns,before.turns);
 await send(store,s.id,'继续观察');const story=f.tasks.findLast(t=>t.purpose==='story');assert(story.messages.some(m=>m.role==='assistant'&&m.content===edit.text));
 assert.equal(story.parameters.values.temperature.value,0.25);await store.requireRuntime(await store.read(s.id));
 // Match the exact pre-edit generation-host descriptor, preserving all other bindings.
 const prior=clone(s);for(const p of ['studio/output-edits.mjs','studio/message-tools.mjs','studio/default-summary-v2.mjs'])delete prior.runtime.descriptor.hostArtifacts[p];
 prior.runtime.descriptor.hostArtifacts['studio/generation-cards.mjs']='6764cacd029b5f3723fbbc97f1b54353b05f22e09005e9a96fc52c062f7e6b4c';prior.runtime.hash=digest(prior.runtime.descriptor);
 assert.equal(store.status(prior).compatible,true);await store.requireRuntime(prior);
 prior.runtime.descriptor.hostArtifacts['card-replica/resources/prompts.json']='tampered';prior.runtime.hash=digest(prior.runtime.descriptor);assert.equal(store.status(prior).compatible,false);
});

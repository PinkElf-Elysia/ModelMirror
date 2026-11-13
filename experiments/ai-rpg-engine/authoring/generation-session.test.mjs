// P1-C protocol verification: loopback fake control plane, actual Python parameter
// resolver, durable authored sessions. No real provider, credential or live budget.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {AuthoringStore} from './store.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {ControlPlaneModelControl,PARAMETER_CHOICE_FORMAT,PARAMETER_SELECTION_FORMAT,validateModelChoice,validateModelState,modelChoiceInput} from './model-control.mjs';
import {PARAMETER_PROFILE,PARAMETER_CONFIG,ParameterSettingsStore} from './generation-settings.mjs';
import {ControlledProvider} from './controlled-provider.mjs';
import {controlEvidence,controlledPayload} from './controlled-receipt.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {jointSessionId,validateJointSession} from './joint-session-state.mjs';
import {JointBudget} from './joint-budget.mjs';
import {BranchCheckpoints} from './branch-storage.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
import {PARAMETER_PLUGIN} from './chat-parameters.mjs';
import {startAuthoringServer} from './server.mjs';
import {createJointClient,jointTaskIntent,pluginIntent,parameterEditIntent} from './joint-client.mjs';

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
 f.catalog=(version=2)=>{
  const models=Object.entries(f.policies).map(([id,p])=>({selectionId:id,selectionRevision:sha256(id+'-qualification'),modelId:'synthetic/'+id,name:id,available:true,contextLength:256000,maxOutputTokens:16000,parameterEvidence:'provider-declared',
   ...(version===2?{capabilityRevision:digest(p),parameterPolicy:{...clone(p),fields:Object.fromEntries(Object.entries(p.fields).map(([k,{wireName,...v}])=>[k,v]))}}:
     {parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}})}));
  const body={format:'modelmirror.rpg.control-model-catalog/'+version,source:'control-plane-cache',catalogId:'parameter-test',models};return {...body,revision:digest(body)};
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
   if(url.pathname.endsWith('/models'))return f.unavailable?json(res,{},503):json(res,f.catalog(url.searchParams.get('parameterVersion')==='2'?2:1));
   if(url.pathname.endsWith('/readiness'))return json(res,{format:'modelmirror.rpg.authoring-readiness/1',executionEnabled:true,dispatched:false});
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
    assert.equal(p.format,'modelmirror.rpg.authoring-task/2');const key=p.sessionId+'/'+p.requestId;assert.ok(!f.results.has(key),'duplicate task POST');
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
async function fixture(t,options={}) {
 const f=await controlFixture(t,options),directory=await mkdtemp(join(tmpdir(),'rpg-p1c-'));let host,provider,history,budget,branches;
 const store=await AuthoringStore.open(join(directory,'works')),doc=blankDocument();doc.metadata.name='Parameters independent of card names';doc.metadata.audience='male';doc.sources.system='AUTHOR SYSTEM';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 const created=await store.create({operationId:'work',expectedRevision:null,document:doc}),published=await store.publish(created.workId,{operationId:'publish',expectedRevision:0});
 const reference={workId:created.workId,versionId:published.result.versionId},resolveRuntime=r=>openPublishedRuntime(store,r),id=jointSessionId('session');
 async function open(){await f.control.refresh();history=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:id=>host.lookupSession(id),resolveRuntime,generationParameters:!!options.chatParameters});
  provider=await ControlledProvider.open({directory:join(directory,'transport'),origin:f.origin,serviceToken:token});budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'test-only',limit:options.limit??30});branches=await BranchCheckpoints.open({directory:join(directory,'branches')});
  host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,branchStore:branches,modelControl:f.control,historyControl:history,chatParameters:!!options.chatParameters,fault:options.fault,storageFault:options.storageFault});}
 async function close(){await host.close();await history.close();await provider.close();await budget.close();await branches.close();}
 await open();if(options.branches)assert.equal(host.readiness.branchExecution,!options.chatParameters);await host.create({operationId:'session',expectedRevision:null,reference,openingText:'Opening',presetId:null});
 t.after(async()=>{await close();await store.close();});
 Object.assign(f,{directory,id,getHost:()=>host,read:(target=id)=>host.read(target),reopen:async()=>{await close();await open();},
  async intent(purpose='story',values={},target=id){const s=(await host.read(target)).session;return {sessionId:target,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice:f.selection(purpose,values)};},
  async select(purpose='story',values={},target=id){return host.configureModel(await f.intent(purpose,values,target));},
  async parameterIntent(purpose='story',values={},target=id){const v=await host.read(target),s=v.session;return {sessionId:target,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,expectedParameterRevision:s.generationParameters.revision,expectedPurposeRevision:v.parameterSettings.purposeRevisions[purpose],purpose,choice:f.selection(purpose,values)};},
  async parameters(purpose='story',values={},target=id){return host.configureParameters(await f.parameterIntent(purpose,values,target));},
  async plugin(action,pluginId,target=id){const service=history.pluginService,c=await service.catalog(),p=c.plugins.find(p=>p.id===pluginId);return service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
   ...(['enable','disable'].includes(action)?{sessionId:target,expectedSessionRevision:(await host.lookupSession(target)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async sendIntent(operationId='one',input='Opening'){const v=await host.read(id);return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,kind:'send',input,...(options.chatParameters?{expectedParameterAuthorizationRevision:v.parameterSettings.authorization.revision}:{})};},
  async send(operationId='one',input='Opening'){const intent=await f.sendIntent(operationId,input);const result=await host.send(intent);await host.waitForIdle(id);return {intent,result};},
  async recover(operationId='one'){const v=await host.read(id);return host.recover({sessionId:id,operationId,expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision});},
 });
 for(const [flag,plugin] of [['m2','rpg.rolling-summary'],['m3','rpg.memory-palace'],['modelPlugin','rpg.model-selector'],['branches','rpg.branch-save'],['chatParameters',PARAMETER_PLUGIN]])if(options[flag]){await f.plugin('install',plugin);await f.plugin('enable',plugin);}
 return f;
}

test('three formal purposes freeze actual Python resolution; compression inherits summary',async t=>{
 const f=await fixture(t,{m2:true,m3:true,compression:true}),h=f.getHost();
 const values={story:{temperature:{mode:'explicit',value:.7},topP:{mode:'default'},maxOutputTokens:{mode:'default'},seed:{mode:'explicit',value:123},stop:{mode:'explicit',value:['\n\n',' 停止🪷 ']},'thinking.effort':{mode:'explicit',value:'high'}},
  summary:{temperature:{mode:'explicit',value:.2},'thinking.enabled':{mode:'explicit',value:true},'thinking.budgetTokens':{mode:'explicit',value:512}},
  memory:{temperature:{mode:'explicit',value:0},topK:{mode:'explicit',value:20},'thinking.enabled':{mode:'explicit',value:false},stop:{mode:'explicit',value:[]}}};
 for(const purpose of ['story','summary','memory'])await f.select(purpose,values[purpose]);assert.equal(f.tasks.length,0);
 const frozen=(await f.read()).session.models;assert.equal(frozen.changes.some(c=>c.pluginReceipt),false,'native authoring does not require selector plugin');
 await f.send();await f.send('two','Continue');const v=await f.read();assert.equal(v.session.turns.length,2);
 assert.deepEqual(f.tasks.map(p=>p.purpose),['story','memory','story','summary','compression','memory']);assert.equal(v.budget.used,6);assert.equal(v.budget.reserved,0);
 for(const task of f.tasks){const purpose=task.purpose==='compression'?'summary':task.purpose,c=frozen.current[purpose],r=f.results.get(task.sessionId+'/'+task.requestId).receipt;
  assert.deepEqual(task.parameters,c.parameterProfile.parameters);assert.equal(task.capabilityRevision,c.entry.capabilityRevision);assert.deepEqual(r.parameters,c.wireParameters);assert.deepEqual(r.parameterResolution,c.parameterResolution);assert.ok(r.providerRequestHash);}
 assert.equal(v.session.summary.snapshot.versions.length,1);assert.equal(v.session.memory.snapshot.processedThrough,2);
 assert.deepEqual(frozen.current.story.wireParameters,{temperature:.7,max_tokens:4096,seed:123,stop:['\n\n',' 停止🪷 '],reasoning:{effort:'high'}});
 assert.equal(frozen.current.story.parameterResolution.sources.topP.resolution,'omitted');assert.equal(frozen.current.story.parameterResolution.sources.maxOutputTokens.resolution,'declared-default');
 for(const op of v.session.operations){assert.equal(op.request.modelConfiguration.choice.format,PARAMETER_CHOICE_FORMAT);assert.deepEqual(op.request.parameters,frozen.current.story.wireParameters);assert.equal(op.request.messagesHash,digest(op.request.messages));}
 await f.reopen();assert.deepEqual((await f.read()).session.models,frozen);assert.equal(f.tasks.length,6);
 if(process.env.RPG_P1C_EVIDENCE){await mkdir(process.env.RPG_P1C_EVIDENCE,{recursive:true});await writeFile(join(process.env.RPG_P1C_EVIDENCE,'typed-session-capture.json'),JSON.stringify({models:frozen,tasks:f.tasks,results:[...f.results.values()],session:v.session,budget:v.budget},null,2));}
});

test('formal CAS, duplicate intent and purpose isolation preserve immutable configuration',async t=>{
 const f=await fixture(t),h=f.getHost(),intent=await f.intent();const first=await h.configureModel(intent),before=f.requests.length;
 f.unavailable=true;assert.deepEqual(await h.configureModel(intent),{...first,replayed:true});assert.equal(f.requests.length,before);f.unavailable=false;
 await assert.rejects(()=>h.configureModel({...intent,choice:f.selection('story',{temperature:{mode:'explicit',value:1}})}),code('OFFLINE_OPERATION_COLLISION'));
 await assert.rejects(()=>h.configureModel({...intent,operationId:'stale'}),code('OFFLINE_REVISION_CONFLICT'));
 const memory=await f.select('memory'),frozen=clone(memory.change.choice);await f.select('story',{temperature:{mode:'explicit',value:.9}});
 assert.deepEqual((await f.read()).session.models.current.memory,frozen);
 const clear=await f.intent('story');clear.choice=null;await h.configureModel(clear);const s=(await f.read()).session;assert.equal(s.models.current.story,null);assert.deepEqual(s.models.current.memory,frozen);assert.equal(f.tasks.length,0);
});

test('two tabs accept one formal configuration; cloned input resists async mutation',async t=>{
 const f=await fixture(t),h=f.getHost(),a=await f.intent(),b=await f.intent('memory'),out=await Promise.allSettled([h.configureModel(a),h.configureModel(b)]);
 assert.equal(out.filter(r=>r.status==='fulfilled').length,1);assert.equal(out.find(r=>r.status==='rejected').reason.code,'OFFLINE_REVISION_CONFLICT');
 const input=await f.intent('story',{temperature:{mode:'explicit',value:.5}}),job=h.configureModel(input);input.choice.profile.parameters.values.temperature.value=1.7;input.purpose='memory';await job;
 assert.equal((await f.read()).session.models.current.story.parameters.temperature,.5);assert.equal(f.tasks.length,0);
});

for(const scenario of ['purpose','unsupported','range','thinking-conflict','extra-wire','bad-capability'])test('rejected typed intent remains uncommitted: '+scenario,async t=>{
 const f=await fixture(t),h=f.getHost(),intent=await f.intent();
 if(scenario==='purpose')intent.choice.profile.purpose='memory';
 if(scenario==='unsupported'){delete f.policies.story.fields.topK;intent.choice=f.selection('story',{topK:{mode:'default'}});}
 if(scenario==='range')intent.choice.profile.parameters.values.topK={mode:'explicit',value:101};
 if(scenario==='thinking-conflict')intent.choice.profile.parameters.values={'thinking.enabled':{mode:'explicit',value:false},'thinking.effort':{mode:'explicit',value:'high'}};
 if(scenario==='extra-wire')intent.choice.wireParameters={api_key:'forbidden'};
 if(scenario==='bad-capability')intent.choice.capabilityRevision=sha256('not-current');
 await assert.rejects(async()=>h.configureModel(intent));assert.equal((await f.read()).session.models.revision,0);assert.equal(f.tasks.length,0);
});

test('capability drift blocks new sends without reinterpreting old configuration',async t=>{
 const f=await fixture(t);await f.select('story',{temperature:{mode:'explicit',value:.8}});const before=await f.read();f.policies.story.fields.temperature.maximum=1;
 await assert.rejects(()=>f.send(),code('MODEL_SELECTION_CHANGED'));const after=await f.read();assert.deepEqual(after.session,before.session);assert.equal(after.budget.used,0);assert.equal(f.tasks.length,0);
 const old=clone(before.session.models.current.story);await f.select('story',{temperature:{mode:'explicit',value:.8}});assert.notEqual((await f.read()).session.models.current.story.entry.capabilityRevision,old.entry.capabilityRevision);
 assert.deepEqual((await f.read()).session.models.changes[0].choice,old);
});

test('drift after freeze before claim is known undispatched and releases all budget',async t=>{
 let f;f=await fixture(t,{fault:async stage=>{if(stage==='beforeClaim')f.policies.story.fields.temperature.default={mode:'value',value:.1};}});await f.select();await f.send();const v=await f.read();
 assert.equal(f.tasks.length,0);assert.equal(v.session.operations[0].state,'not_dispatched');assert.equal(v.budget.used,0);assert.equal(v.budget.reserved,0);assert.equal(v.session.turns.length,0);
});

for(const purpose of ['story','memory'])test('unknown '+purpose+' resolves after restart and capability loss using frozen request ID only',async t=>{
 const f=await fixture(t,{m3:purpose==='memory',dropPurpose:purpose});await f.select();if(purpose==='memory')await f.select('memory');const {intent}=await f.send();const n=f.tasks.length,before=await f.read();
 assert.equal(before.status,'unknown');await assert.rejects(async()=>f.getHost().configureModel({...await f.intent(),choice:null}),code(purpose==='story'?'OFFLINE_RESULT_PENDING':'MEMORY_RESULT_PENDING'));
 await f.reopen();f.unavailable=true;delete f.policies.story.fields.temperature;delete f.policies.memory.fields.topK;
 const requests=f.requests.length;await f.recover();const after=await f.read();assert.equal(f.tasks.length,n);assert.equal(after.session.turns.length,1);assert.ok(f.lookups.length>=1);
 assert.ok(f.requests.slice(requests).every(r=>r.method==='GET'&&r.path.includes('/tasks/')));assert.equal((await f.getHost().send(intent)).replayed,true);assert.equal(f.tasks.length,n);
 if(purpose==='memory')assert.equal(after.session.memory.snapshot.processedThrough,1);
});

test('busy configuration cannot race an in-flight generation',async t=>{
 let release,started;const onStart=new Promise(r=>started=r),gate=new Promise(r=>release=r);
 const f=await fixture(t,{onTask:async()=>{started();await gate;}});await f.select();const intent=await f.sendIntent(),job=f.getHost().send(intent);await onStart;
 try{await assert.rejects(async()=>f.getHost().configureModel(await f.intent()),code('OFFLINE_SESSION_BUSY'));}finally{release();}
 await job;await f.getHost().waitForIdle(f.id);assert.equal(f.tasks.length,1);
});

for(const stage of ['beforeCommit','afterCommit'])test('parameter save '+stage+' failure settles without double commit',async t=>{
 let armed=false;const f=await fixture(t,{storageFault:async when=>{if(armed&&when===stage){armed=false;throw Error('synthetic disk failure');}}}),intent=await f.intent();armed=true;
 await assert.rejects(()=>f.getHost().configureModel(intent));await f.reopen();const s=(await f.read()).session;
 assert.equal(s.models.revision,stage==='afterCommit'?1:0);const result=await f.getHost().configureModel(intent);assert.equal(result.replayed,stage==='afterCommit');assert.equal((await f.read()).session.models.changes.length,1);assert.equal(f.tasks.length,0);
});

test('preset application copies intent, rechecks capabilities and never reads a mutable sidecar during send',async t=>{
 const f=await fixture(t),settings=await ParameterSettingsStore.open({directory:join(f.directory,'presets'),control:f.control});t.after(()=>settings.close());const choice=f.selection('story',{seed:{mode:'explicit',value:7}}),{format,profile:p,...selection}=choice;
 const saved=await settings.savePreset({operationId:'preset',expectedRevision:0,id:'repeatable',name:'重复采样',expectedPresetRevision:0,profile:p,selection});
 const intent=await f.intent();intent.choice={...choice,profile:saved.result.configuration.profile};await f.getHost().configureModel(intent);
 await settings.savePreset({operationId:'edit-preset',expectedRevision:1,id:'repeatable',name:'改变预设',expectedPresetRevision:1,profile:profile('story',{seed:{mode:'explicit',value:88}}),selection});
 await f.send();assert.equal(f.tasks[0].parameters.values.seed.value,7);assert.equal((await f.read()).session.models.current.story.parameters.seed,7);
});

test('existing model-selector grant remains checked and receipt binds the v2 choice',async t=>{
 const f=await fixture(t,{modelPlugin:true});const r=await f.select();assert.equal(r.change.pluginReceipt.pluginId,'rpg.model-selector');validateModelState((await f.read()).session.models,f.id,r.revision,(await f.read()).session.binding.resourceHash);
});

test('controlled non-dispatch receipt releases claimed slot without becoming unknown',async t=>{
 const f=await fixture(t,{dispatched:false});await f.select();await f.send();const v=await f.read();assert.equal(v.session.operations[0].state,'failed');assert.equal(v.status,'paused');assert.equal(v.budget.used,0);assert.equal(v.budget.reserved,0);assert.equal(v.session.turns.length,0);
});

for(const corrupt of ['capability','resolution','provider-hash','v1-format'])test('mismatched receipt remains unknown until original result is recovered: '+corrupt,async t=>{
 const f=await fixture(t);await f.select();f.corrupt=result=>{
  if(corrupt==='capability')result.receipt.capabilityRevision=sha256('wrong');
  if(corrupt==='resolution')result.receipt.parameterResolution.sources.maxOutputTokens.mode='explicit';
  if(corrupt==='provider-hash')result.receipt.providerRequestHash=null;
  if(corrupt==='v1-format')result.receipt.format='modelmirror.rpg.authoring-task-receipt/1';
 };await f.send();assert.equal((await f.read()).status,'unknown');assert.equal((await f.read()).session.turns.length,0);await f.recover();assert.equal((await f.read()).session.turns.length,1);assert.equal(f.tasks.length,1);
});

test('source purpose and frozen effective parameters are independently validated',async t=>{
 const f=await fixture(t);await f.select();await f.send();const req=(await f.read()).session.operations[0].request,c=clone(req.modelConfiguration.choice);assert.equal(validateModelChoice(c),c);assert.deepEqual(modelChoiceInput(c),f.selection('story'));
 const mutated=clone(c);mutated.parameters.maxOutputTokens=123;const {sha256:_,...body}=mutated;mutated.sha256=digest(body);assert.throws(()=>validateModelChoice(mutated));
 assert.throws(()=>controlledPayload({...req,kind:'memory'}),code('CONTROL_REQUEST_INVALID'));
 const evidence=clone(f.results.values().next().value);evidence.receipt.parameterResolution.wireParameters.max_tokens=1;assert.throws(()=>controlEvidence(evidence,req),code('CONTROL_RECEIPT_MISMATCH'));
});

test('formal HTTP catalog/check/configure/reconcile is zero-generation and serves exact v2 request',async t=>{
 const f=await controlFixture(t),directory=await mkdtemp(join(tmpdir(),'rpg-p1c-http-'));
 const provider=await ControlledProvider.open({directory:join(directory,'transport'),origin:f.origin,serviceToken:token});let server;
 const open=async()=>{await f.control.refresh();server=await startAuthoringServer({directory:join(directory,'works'),modelSettings:{directory:join(directory,'sessions'),provider,control:f.control},historySettings:{directory:join(directory,'plugins')},jointSettings:{directory:join(directory,'joint'),allowance:{id:'http-test',limit:2}}});};await open();
 t.after(async()=>{await server.close();await provider.close();});
 const call=async(path,body,headers={})=>{const r=await fetch(server.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:server.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
 const ok=async(path,body)=>{const r=await call(path,body);assert.equal(r.status,200,JSON.stringify(r.value));return r.value;};
 const doc=blankDocument();doc.metadata.name='HTTP generic parameters';doc.metadata.audience='male';doc.sources.system='Frozen author';const w=await ok('/works',{operationId:'work',expectedRevision:null,document:doc});
 const pub=await ok('/works/'+w.workId+'/publish',{operationId:'publish',expectedRevision:0}),reference={workId:w.workId,versionId:pub.result.versionId};
 const created=await ok('/model-settings/sessions',{operationId:'session',expectedRevision:null,reference,openingText:'Opening',presetId:null}),id=created.session.id;
 const catalog=await ok('/model-settings/sessions/'+id+'/models?parameterVersion=2');assert.equal(catalog.catalog.format,'modelmirror.rpg.control-model-catalog/2');
 assert.equal((await ok('/model-settings/catalog')).catalog.format,'modelmirror.rpg.control-model-catalog/1');
 assert.equal((await call('/model-settings/catalog?parameterVersion=3')).status,422);assert.equal((await call('/model-settings/catalog?parameterVersion=2&parameterVersion=1')).status,422);
 const choice=f.selection('story',{topP:{mode:'explicit',value:.8},'thinking.enabled':{mode:'explicit',value:false}}),checked=await ok('/model-settings/check',{choice});assert.equal(checked.dispatched,false);assert.equal(checked.choice.format,PARAMETER_CHOICE_FORMAT);
 const input={sessionId:id,operationId:'select',expectedRevision:created.session.revision,expectedModelRevision:0,purpose:'story',choice};
 assert.equal((await call('/model-settings/configure',input,{Origin:'http://evil.invalid'})).status,403);
 const configured=await ok('/model-settings/configure',input);assert.equal(configured.change.choice.format,PARAMETER_CHOICE_FORMAT);assert.equal(f.tasks.length,0);
 assert.equal((await ok('/model-settings/reconcile',{sessionId:id,operationId:'select',kind:'configure'})).found,true);
 const before=await ok('/joint/sessions/'+id);await server.close();await open();assert.deepEqual((await ok('/joint/sessions/'+id)).session,before.session);
 const state=await ok('/joint/sessions/'+id),intent={sessionId:id,operationId:'turn',expectedRevision:state.session.revision,expectedModelRevision:state.session.models.revision,expectedSummaryRevision:state.session.summary.snapshot.revision,expectedMemoryRevision:state.session.memory.snapshot.revision,expectedContextPolicyRevision:state.effectivePolicy.revision,kind:'send',input:'Opening'};
 await ok('/joint/send',intent);await server.close();await open();assert.equal(f.tasks.length,1);assert.deepEqual(f.tasks[0].parameters,choice.profile.parameters);
 const last=await ok('/joint/sessions/'+id);assert.equal(last.session.turns.length,1);assert.equal(last.budget.used,1);assert.equal((await ok('/model-settings/configure',input)).replayed,true);assert.equal(f.tasks.length,1);
});


test('unchanged concurrent catalog read does not invalidate parameter application',async t=>{
 const f=await fixture(t);let once=false;f.onCheck=async()=>{if(!once){once=true;await f.control.parameterCatalog();}};
 await f.select('story',{seed:{mode:'explicit',value:2}});assert.equal((await f.read()).session.models.current.story.parameters.seed,2);assert.equal(f.tasks.length,0);
});

test('changed concurrent catalog read blocks committing an outdated check',async t=>{
 const f=await fixture(t);let once=false;f.onCheck=async()=>{if(!once){once=true;f.policies.story.fields.temperature.default={mode:'value',value:.4};await f.control.parameterCatalog();}};
 await assert.rejects(()=>f.select(),code('MODEL_CATALOG_CHANGED'));assert.equal((await f.read()).session.models.revision,0);assert.equal(f.tasks.length,0);
});

test('unrelated row drift preserves exact selected row and frozen request',async t=>{
 const f=await fixture(t);await f.select('story',{seed:{mode:'explicit',value:4}});const c=(await f.read()).session.models.current.story;
 f.policies.memory.fields.temperature.maximum=1;assert.notEqual(f.catalog().revision,c.catalogRevision);await f.send();assert.equal(f.tasks[0].catalogRevision,c.catalogRevision);assert.equal(f.tasks.length,1);assert.equal((await f.read()).session.turns.length,1);
});

test('v2 branch seals past parameters and history without granting or selecting future configuration',async t=>{
 const f=await fixture(t,{branches:true,m3:true});await f.select('story',{seed:{mode:'explicit',value:1}});await f.select('memory');await f.send();const before=await f.read();
 const branch=await f.getHost().fork({sessionId:f.id,operationId:'fork',expectedRevision:before.session.revision,expectedSummaryRevision:before.session.summary.snapshot.revision,expectedMemoryRevision:before.session.memory.snapshot.revision,expectedContextPolicyRevision:before.effectivePolicy.revision,turn:1,name:'Child'});
 assert.equal(branch.session.turns.length,1);assert.deepEqual(branch.session.models.current,{story:null,summary:null,memory:null});assert.equal(branch.session.models.changes.length,0);
 const child=clone(branch.session);await f.select('story',{seed:{mode:'explicit',value:8}});await f.reopen();assert.deepEqual((await f.read(child.id)).session,child);assert.equal((await f.read(child.id)).budget.used,2);assert.equal(f.tasks.length,2);
});

test('revoking M3 after dispatch keeps original parameter receipt but does not apply memory',async t=>{
 let f;f=await fixture(t,{m3:true,onTask:async p=>{if(p.purpose==='memory')await f.plugin('disable','rpg.memory-palace');}});await f.select();await f.select('memory',{temperature:{mode:'explicit',value:.1}});await f.send();
 const s=(await f.read()).session,j=s.memory.jobs[0];assert.equal(j.state,'revoked');assert.equal(j.receipt.control.parameterResolution.effectiveParameters.temperature,.1);assert.equal(s.memory.snapshot.processedThrough,0);assert.equal(f.tasks.length,2);
});

test('insufficient reservation blocks v2 generation and retains saved parameters',async t=>{
 const f=await fixture(t,{m3:true,limit:1});await f.select();await f.select('memory');const models=clone((await f.read()).session.models);await f.send();const v=await f.read();assert.equal(f.tasks.length,0);assert.equal(v.budget.used,0);assert.equal(v.budget.reserved,0);assert.deepEqual(v.session.models,models);assert.equal(v.session.runs[0].diagnostic,'JOINT_BUDGET_EXHAUSTED');
});


test('a parameter preflight timeout is zero-generation, leaves no configuration and spends no budget',async t=>{
 const f=await fixture(t,{controlTimeout:250});f.onCheck=()=>new Promise(resolve=>setTimeout(resolve,500));const before=await f.read();
 await assert.rejects(()=>f.select('story',{seed:{mode:'explicit',value:9}}),code('MODEL_CONTROL_TIMEOUT'));
 const after=await f.read();assert.deepEqual(after.session,before.session);assert.deepEqual(after.budget,before.budget);assert.equal(f.tasks.length,0);
});


// P2-C2: the same durable host and HTTP transport, with chat grant semantics.
async function chatFixture(t,options={}){
 const f=await fixture(t,{...options,chatParameters:true,modelPlugin:true});
 await f.select('story');if(options.m2)await f.select('summary');if(options.m3)await f.select('memory');return f;
}
test('chat parameters freeze all three purposes on actual controlled wire including compression',async t=>{
 const f=await chatFixture(t,{m2:true,m3:true,compression:true}),defaults=clone((await f.read()).session.models);
 await f.parameters('story',{temperature:{mode:'explicit',value:.73},seed:{mode:'explicit',value:42},stop:{mode:'explicit',value:[' STOP中文 ']}});
 await f.parameters('summary',{temperature:{mode:'explicit',value:.17},'thinking.enabled':{mode:'explicit',value:true},'thinking.budgetTokens':{mode:'explicit',value:512}});
 await f.parameters('memory',{temperature:{mode:'explicit',value:0},topK:{mode:'explicit',value:13}});
 const saved=(await f.read()).session.generationParameters;assert.equal(saved.revision,3);assert.deepEqual((await f.read()).session.models,defaults);assert.equal(f.tasks.length,0);
 const catalog=await f.getHost().parameterCatalog(f.id,'summary');assert.deepEqual(catalog.catalog.models.map(m=>m.modelId),['synthetic/summary']);
 await f.send();await f.send('two','Continue');const v=await f.read();assert.equal(v.session.turns.length,2);assert.equal(v.session.memory.snapshot.processedThrough,2);assert.equal(v.session.summary.snapshot.versions.length,1);
 assert.deepEqual(f.tasks.map(t=>t.purpose),['story','memory','story','summary','compression','memory']);
 for(const task of f.tasks){const purpose=task.purpose==='compression'?'summary':task.purpose,choice=saved.current[purpose],receipt=f.results.get(task.sessionId+'/'+task.requestId).receipt;
  assert.deepEqual(task.parameters,choice.parameterProfile.parameters);assert.deepEqual(receipt.parameters,choice.wireParameters);assert.deepEqual(receipt.parameterResolution,choice.parameterResolution);}
 assert.ok(v.session.runs.every(r=>r.parameters.revision===3&&r.parameters.authorization.enabled));
 for(const op of v.session.operations)assert.deepEqual(op.request.parameters,saved.current.story.wireParameters);
 await f.reopen();assert.deepEqual((await f.read()).session.generationParameters,saved);assert.equal(f.tasks.length,6);
 if(process.env.RPG_P2C2_EVIDENCE){await mkdir(process.env.RPG_P2C2_EVIDENCE,{recursive:true});await writeFile(join(process.env.RPG_P2C2_EVIDENCE,'chat-three-purpose-capture.json'),JSON.stringify({tasks:f.tasks,results:[...f.results.values()],session:v.session,budget:v.budget},null,2));}
});
test('chat disabled overrides survive and next request uses actual defaults; reenable restores override',async t=>{
 const f=await chatFixture(t);await f.parameters('story',{seed:{mode:'explicit',value:77}});const saved=clone((await f.read()).session.generationParameters);
 await f.plugin('disable',PARAMETER_PLUGIN);const v=await f.read();assert.equal(v.parameterSettings.authorization.enabled,false);assert.deepEqual(v.parameterSettings.effective.story,v.session.models.current.story);
 await f.send();assert.deepEqual(f.tasks[0].parameters.values,{});assert.deepEqual((await f.read()).session.generationParameters,saved);
 await f.plugin('enable',PARAMETER_PLUGIN);await f.send('two','Continue');assert.equal(f.tasks[1].parameters.values.seed.value,77);assert.equal(f.tasks.length,2);
 await f.reopen();assert.equal((await f.read()).parameterSettings.effective.story.wireParameters.seed,77);
});
test('chat permission and model boundaries prevent native parameter bypass or cross-model application',async t=>{
 const f=await chatFixture(t),h=f.getHost();
 assert.throws(()=>h.configureModel(f.selection('story')),code('AUTHORING_UNKNOWN_FIELD'));
 await assert.rejects(async()=>h.configureModel(await f.intent('story',{temperature:{mode:'explicit',value:.1}})),code('CHAT_MODEL_DEFAULTS_REQUIRED'));
 const cross=await f.parameterIntent();cross.choice=f.selection('memory');cross.choice.profile.purpose='story';await assert.rejects(()=>h.configureParameters(cross),code('PARAMETER_MODEL_MISMATCH'));
 await f.plugin('disable',PARAMETER_PLUGIN);await assert.rejects(async()=>h.configureParameters(await f.parameterIntent()),code('PARAMETER_AUTHORIZATION_REQUIRED'));await assert.rejects(()=>h.parameterCatalog(f.id,'story'));
 assert.equal((await f.read()).session.generationParameters.revision,0);assert.equal(f.tasks.length,0);
});
test('chat parameter revisions, two-tab CAS and original operation replay',async t=>{
 const f=await chatFixture(t),h=f.getHost(),a=await f.parameterIntent('story',{seed:{mode:'explicit',value:1}}),b=await f.parameterIntent('story',{seed:{mode:'explicit',value:2}});
 const out=await Promise.allSettled([h.configureParameters(a),h.configureParameters(b)]);assert.equal(out.filter(x=>x.status==='fulfilled').length,1);assert.equal(out.find(x=>x.status==='rejected').reason.code,'OFFLINE_REVISION_CONFLICT');
 await f.plugin('disable',PARAMETER_PLUGIN);f.unavailable=true;const requests=f.requests.length;assert.equal((await h.configureParameters(a)).replayed,true);assert.equal(f.requests.length,requests);
 await assert.rejects(()=>h.configureParameters({...a,choice:b.choice}),code('OFFLINE_OPERATION_COLLISION'));f.unavailable=false;await f.plugin('enable',PARAMETER_PLUGIN);
 for(const key of ['expectedParameterRevision','expectedPurposeRevision','expectedModelRevision']){const i=await f.parameterIntent();i[key]++;await assert.rejects(()=>h.configureParameters(i),code(key==='expectedModelRevision'?'MODEL_REVISION_CONFLICT':'PARAMETER_REVISION_CONFLICT'));}
 const clear=await f.parameterIntent();clear.choice=null;await h.configureParameters(clear);assert.equal((await f.read()).parameterSettings.effective.story.wireParameters.seed,undefined);assert.equal((await f.read()).session.generationParameters.changes.length,2);assert.equal(f.tasks.length,0);
});
for(const stage of ['beforeCommit','afterCommit'])test('chat parameter atomic save '+stage+' recovers original intent',async t=>{
 let armed=false;const f=await chatFixture(t,{storageFault:async when=>{if(armed&&when===stage){armed=false;throw Error('offline disk fault');}}}),i=await f.parameterIntent('story',{seed:{mode:'explicit',value:61}});armed=true;
 await assert.rejects(()=>f.getHost().configureParameters(i));await f.reopen();assert.equal((await f.read()).session.generationParameters.revision,stage==='afterCommit'?1:0);
 const result=await f.getHost().configureParameters(i);assert.equal(result.replayed,stage==='afterCommit');assert.equal((await f.read()).session.generationParameters.revision,1);assert.equal(f.tasks.length,0);
});
test('chat revoke after parameter proposal leaves no saved change',async t=>{
 let f;f=await chatFixture(t,{fault:async stage=>{if(stage==='parameterPrepared')await f.plugin('disable',PARAMETER_PLUGIN);}});
 await assert.rejects(()=>f.parameters(),code('PLUGIN_NOT_AUTHORIZED'));assert.equal((await f.read()).session.generationParameters.revision,0);assert.equal(f.tasks.length,0);
});
test('chat revoke before dispatch releases all quota without sending',async t=>{
 let f;f=await chatFixture(t,{fault:async stage=>{if(stage==='beforeClaim')await f.plugin('disable',PARAMETER_PLUGIN);}});await f.parameters('story',{seed:{mode:'explicit',value:23}});await f.send();const v=await f.read();
 assert.equal(f.tasks.length,0);assert.equal(v.budget.used,0);assert.equal(v.budget.reserved,0);assert.equal(v.session.operations[0].state,'not_dispatched');assert.equal(v.session.operations[0].diagnostic,'PARAMETER_AUTHORIZATION_CHANGED');
});
test('chat in-flight stop keeps frozen result; busy edits cannot wait and commit later',async t=>{
 let release,started;const start=new Promise(r=>started=r),gate=new Promise(r=>release=r);const f=await chatFixture(t,{onTask:async()=>{started();await gate;}});await f.parameters('story',{seed:{mode:'explicit',value:9}});
 const job=f.getHost().send(await f.sendIntent());await start;
 try{await assert.rejects(async()=>f.getHost().configureParameters(await f.parameterIntent()),code('OFFLINE_SESSION_BUSY'));await f.plugin('disable',PARAMETER_PLUGIN);}finally{release();}
 await job;await f.getHost().waitForIdle(f.id);const v=await f.read();assert.equal(v.session.turns.length,1);assert.equal(v.session.operations[0].request.parameters.seed,9);assert.equal(v.parameterSettings.authorization.enabled,false);assert.equal(v.budget.used,1);
});
for(const purpose of ['story','memory'])test('chat unknown '+purpose+' recovers frozen operation after restart and parameter revoke',async t=>{
 const f=await chatFixture(t,{m3:purpose==='memory',dropPurpose:purpose});await f.parameters('story',{seed:{mode:'explicit',value:32}});if(purpose==='memory')await f.parameters('memory',{topK:{mode:'explicit',value:7}});
 await f.send();const before=await f.read(),n=f.tasks.length;assert.equal(before.pending,true);await assert.rejects(async()=>f.getHost().configureParameters(await f.parameterIntent()));
 await f.plugin('disable',PARAMETER_PLUGIN);await f.reopen();f.unavailable=true;const requests=f.requests.length;await f.recover();const after=await f.read();assert.equal(f.tasks.length,n);assert.equal(after.session.turns.length,1);assert.equal(after.pending,false);assert.ok(f.lookups.length);assert.ok(f.requests.slice(requests).every(r=>r.method==='GET'&&r.path.includes('/tasks/')));
 if(purpose==='memory')assert.equal(after.session.memory.snapshot.processedThrough,1);
});
test('chat capability drift blocks before reservation and saved data cannot tamper with frozen hashes',async t=>{
 const f=await chatFixture(t);await f.parameters('story',{seed:{mode:'explicit',value:47}});await f.send();const s=(await f.read()).session,hash=s.executionHash;
 const changed=clone(s);changed.runs[0].parameters.choiceHashes.story=sha256('forged');assert.throws(()=>validateJointSession(changed,hash),code('CHAT_PARAMETER_SNAPSHOT_INVALID'));
 const receipt=clone(s);receipt.generationParameters.changes[0].receipt.parameterRevision++;assert.throws(()=>validateJointSession(receipt,hash),code('CHAT_PARAMETER_RECEIPT_INVALID'));
 f.policies.story.fields.seed.maximum=40;const before=await f.read();await assert.rejects(()=>f.send('two','Continue'),code('MODEL_SELECTION_CHANGED'));assert.deepEqual((await f.read()).session,before.session);assert.equal(f.tasks.length,1);
});
test('chat model grants and unimplemented branch inheritance fail explicitly',async t=>{
 const f=await chatFixture(t,{branches:true});await f.plugin('disable','rpg.model-selector');await assert.rejects(()=>f.select(),code('CHAT_MODEL_AUTHORIZATION_REQUIRED'));
 await f.parameters('story',{seed:{mode:'explicit',value:5}});await f.send();const v=await f.read();assert.equal(f.tasks[0].parameters.values.seed.value,5);await assert.rejects(async()=>f.getHost().fork({sessionId:f.id,operationId:'branch-chat',expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,turn:1,name:'Child'}),code('CHAT_PARAMETER_BRANCH_UNAVAILABLE'));assert.equal(f.tasks.length,1);
});


test('chat stale parameter authorization from another tab blocks estimate and send before reservation',async t=>{
 const f=await chatFixture(t);await f.parameters('story',{seed:{mode:'explicit',value:3}});const input=await f.sendIntent();await f.plugin('disable',PARAMETER_PLUGIN);
 for(const method of ['estimate','send'])await assert.rejects(()=>f.getHost()[method](input),code('PARAMETER_AUTHORIZATION_CHANGED'));
 const v=await f.read();assert.equal(v.session.runs.length,0);assert.equal(v.budget.used,0);assert.equal(v.budget.reserved,0);assert.equal(f.tasks.length,0);
 await f.send();assert.deepEqual(f.tasks[0].parameters.values,{});
});
for(const purpose of ['summary','memory'])test('chat parameter revoke fences background '+purpose+' before network',async t=>{
 let f,armed=false;f=await chatFixture(t,{m2:purpose==='summary',m3:purpose==='memory',fault:async(stage,request)=>{if(armed&&stage==='beforeClaim'&&request.kind===purpose){armed=false;await f.plugin('disable',PARAMETER_PLUGIN);}}});await f.parameters(purpose,{temperature:{mode:'explicit',value:.25}});
 if(purpose==='summary')await f.send();armed=true;await f.send(purpose==='summary'?'two':'one',purpose==='summary'?'Continue':'Opening');const v=await f.read(),job=v.session[purpose].jobs.at(-1);
 assert.equal(job.state,'not_dispatched');assert.equal(job.diagnostic,'PARAMETER_AUTHORIZATION_CHANGED');assert.equal(v.budget.reserved,0);assert.equal(f.tasks.some(x=>x.purpose===purpose),false);assert.equal(v.session.turns.length,purpose==='summary'?2:1);
});
test('chat model change retains incompatible override, blocks sending and allows explicit reset',async t=>{
 const f=await chatFixture(t);await f.parameters('story',{seed:{mode:'explicit',value:12}});const old=clone((await f.read()).session.generationParameters.current.story),i=await f.intent();i.choice=f.selection('memory');i.choice.profile.purpose='story';await f.getHost().configureModel(i);
 assert.deepEqual((await f.read()).session.generationParameters.current.story,old);assert.equal((await f.read()).parameterSettings.diagnostic,'PARAMETER_MODEL_MISMATCH');await assert.rejects(()=>f.send(),code('PARAMETER_MODEL_MISMATCH'));assert.equal(f.tasks.length,0);
 const reset=await f.parameterIntent();reset.choice=null;await f.getHost().configureParameters(reset);await f.send();assert.equal(f.tasks[0].selectionId,i.choice.selectionId);assert.deepEqual(f.tasks[0].parameters.values,{});
});

// P2-C3: client -> same-origin HTTP -> formal chat host -> loopback control plane.
// Session creation and configuration below use public routes, never direct store writes.
function browserStorage(){const s={};Object.defineProperties(s,{getItem:{value:k=>s[k]??null},setItem:{value:(k,v)=>{s[k]=v;}},removeItem:{value:k=>{delete s[k];}}});return s;}
async function chatHttpFixture(t,options={}){
 const f=await controlFixture(t,options),directory=await mkdtemp(join(tmpdir(),'rpg-p2c3-http-'));let server,provider;
 const chat=options.chat!==false;
 async function open(){
  await f.control.refresh();provider=await ControlledProvider.open({directory:join(directory,'transport'),origin:f.origin,serviceToken:token});
  server=await startAuthoringServer({directory:join(directory,'works'),chatParameters:chat,
   modelSettings:{directory:join(directory,'sessions'),provider,control:f.control},historySettings:{directory:join(directory,'plugins')},
   jointSettings:{directory:join(directory,'joint'),allowance:{id:'http-chat-test',limit:options.limit??20},...(options.settingsFault?{fault:options.settingsFault}:{})}});
 }
 await open();t.after(async()=>{await server.close();await provider.close();});
 const call=async(path,body,headers={})=>{const r=await fetch(server.origin+'/rpg-app/api/authoring'+path,body===undefined?{headers}:{method:'POST',headers:{Origin:server.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
 const ok=async(path,body)=>{const r=await call(path,body);assert.equal(r.status,200,JSON.stringify(r.value));return r.value;};
 const api=async(path,body)=>{const r=await call(path,body);if(r.status!==200)throw {code:r.value.error.code,httpStatus:r.status};return r.value;};
 const doc=blankDocument();doc.metadata.name='HTTP chat parameter contract';doc.metadata.audience='female';doc.sources.system='Frozen generic author system';doc.sources.prefix='前置';doc.sources.suffix='后置';
 const w=await ok('/works',{operationId:'work',expectedRevision:null,document:doc}),pub=await ok('/works/'+w.workId+'/publish',{operationId:'publish',expectedRevision:0});
 const reference={workId:w.workId,versionId:pub.result.versionId},created=await ok('/model-settings/sessions',{operationId:'session',expectedRevision:null,reference,openingText:'Opening',presetId:null}),id=created.session.id;
 const storage=browserStorage(),client=createJointClient({api,storage,sessionId:id,workId:w.workId});
 Object.assign(f,{directory,id,reference,storage,client,api,call,ok,server:()=>server,
  view:()=>ok('/joint/sessions/'+id),settings:()=>ok('/joint/settings/'+id),idle:()=>server.sessionHost.waitForIdle(id),
  reopen:async()=>{await server.close();await provider.close();await open();},
  async change(purpose,action,operationId=randomUUID()){return ok('/joint/settings/change',pluginIntent(await f.settings(),purpose,action,operationId));},
  async enable(purpose){await f.change(purpose,'install');await f.change(purpose,'enable');},
  async modelIntent(purpose='story',values={}){const v=await f.view();return {sessionId:id,operationId:randomUUID(),expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,purpose,choice:f.selection(purpose,values)};},
  async select(purpose='story',values={}){return ok('/model-settings/configure',await f.modelIntent(purpose,values));},
  async parameterIntent(purpose='story',values={},operationId=randomUUID()){return parameterEditIntent(await f.view(),operationId,purpose,values===null?null:f.selection(purpose,values));},
  async parameters(purpose='story',values={}){return client.submit('parameters',await f.parameterIntent(purpose,values));},
  async send(operationId,input){const v=await client.view(),result=await client.submit('send',jointTaskIntent(v,operationId,input));await f.idle();await client.view();return result;},
  async ready(purposes=['story']){for(const purpose of purposes){await f.enable(purpose==='story'?'model':purpose);await f.select(purpose);}await f.enable('parameters');},
  async rejected(path,body,expected,headers){const r=await call(path,body,headers);assert.equal(r.value.error?.code,expected,JSON.stringify(r));return r;},
 });return f;
}

test('chat HTTP service opt-in is trusted and native authoring keeps its existing permissions',async t=>{
 const native=await chatHttpFixture(t,{chat:false}),v=await native.view(),s=await native.settings();
 assert.equal(Object.hasOwn(v,'parameterSettings'),false);assert.equal(Object.hasOwn(s.plugins,'parameters'),false);
 assert.equal(Object.hasOwn(await native.ok('/joint'),'chatParameters'),false);
 assert.equal((await fetch(native.server().origin)).status,200);
 const input={sessionId:native.id,operationId:'native-parameters',expectedRevision:0,expectedModelRevision:0,expectedParameterRevision:0,expectedPurposeRevision:0,purpose:'story',choice:native.selection('story')};
 await native.rejected('/joint/parameters/configure',input,'CHAT_PARAMETER_HOST_REQUIRED');
 await native.rejected('/joint/parameters/reconcile',input,'CHAT_PARAMETER_HOST_REQUIRED');
 await native.rejected('/joint/parameters/'+native.id+'/story',undefined,'CHAT_PARAMETER_HOST_REQUIRED');
 await native.rejected('/joint/settings/change',{...pluginIntent(s,'model','install','native-grant'),purpose:'parameters',expectedParameterRevision:0},'CHAT_PARAMETER_HOST_REQUIRED');
 await native.select('story',{seed:{mode:'explicit',value:9}});assert.equal((await native.view()).session.models.current.story.parameters.seed,9);
 await native.change('summary','install');await native.rejected('/joint/settings/change',pluginIntent(await native.settings(),'summary','enable','native-summary'),'SUMMARY_MODEL_REQUIRED');
 assert.equal(native.tasks.length,0);
 const directory=await mkdtemp(join(tmpdir(),'rpg-p2c3-options-'));
 for(const extra of [{chatParameters:'true'},{chatParameters:true},{chatParameters:true,creatorExperience:true}])await assert.rejects(()=>startAuthoringServer({directory,...extra}),e=>['AUTHORING_SERVER_OPTIONS','CHAT_PARAMETER_SERVER_OPTIONS'].includes(e.code));
 const chat=await chatHttpFixture(t);assert.equal((await chat.ok('/joint')).chatParameters,true);assert.equal((await chat.ok('/model-settings')).chatParameters,true);
 assert.equal((await fetch(chat.server().origin)).status,404);
 await chat.rejected('/model-settings/sessions',{operationId:'bad-mode',expectedRevision:null,reference:chat.reference,openingText:'Opening',presetId:null,chatParameters:false},'AUTHORING_UNKNOWN_FIELD');
});

test('chat HTTP bootstrap authorizes purpose before model without a circular prerequisite or generation',async t=>{
 const f=await chatHttpFixture(t),initial=await f.view();
 for(const purpose of ['story','summary','memory']){
  await f.rejected('/model-settings/configure',await f.modelIntent(purpose),'CHAT_MODEL_AUTHORIZATION_REQUIRED');
  await f.enable(purpose==='story'?'model':purpose);assert.equal((await f.view()).session.models.current[purpose],null);
  await f.select(purpose);
 }
 await f.change('parameters','install');await f.rejected('/joint/parameters/'+f.id+'/story',undefined,'PLUGIN_NOT_AUTHORIZED');
 await f.change('parameters','enable');
 for(const purpose of ['story','summary','memory']){
  const c=await f.ok('/joint/parameters/'+f.id+'/'+purpose);assert.equal(c.purpose,purpose);assert.equal(c.catalog.models.length,1);assert.equal(c.catalog.models[0].modelId,'synthetic/'+purpose);
 }
 const s=await f.settings();assert.equal(s.plugins.parameters.enabled,true);assert.equal(s.parameterRevision,0);assert.deepEqual((await f.view()).budget,initial.budget);assert.equal(f.tasks.length,0);
 await f.reopen();assert.equal((await f.settings()).plugins.parameters.enabled,true);assert.equal(f.tasks.length,0);
});

test('chat HTTP parameter grants cannot select models or authorize another session',async t=>{
 const f=await chatHttpFixture(t);await f.enable('parameters');await f.rejected('/joint/parameters/'+f.id+'/story',undefined,'PARAMETER_MODEL_REQUIRED');
 await f.enable('model');await f.select();await f.change('model','disable');await f.parameters('story',{temperature:{mode:'explicit',value:.55}});
 const before=await f.view();await f.rejected('/model-settings/configure',await f.modelIntent(),'CHAT_MODEL_AUTHORIZATION_REQUIRED');
 const wrong=await f.parameterIntent();wrong.choice=f.selection('memory');wrong.choice.profile.purpose='story';await f.rejected('/joint/parameters/configure',wrong,'PARAMETER_MODEL_MISMATCH');
 const second=await f.ok('/model-settings/sessions',{operationId:'second',expectedRevision:null,reference:f.reference,openingText:'Opening',presetId:null}),sid=second.session.id;
 assert.equal((await f.ok('/joint/settings/'+sid)).plugins.parameters.enabled,false);
 await f.rejected('/joint/parameters/'+sid+'/story',undefined,'PLUGIN_NOT_AUTHORIZED');assert.deepEqual((await f.view()).session,before.session);assert.equal(f.tasks.length,0);
});

test('chat HTTP three-purpose requests use edited values through the browser client and retain native model defaults',async t=>{
 const f=await chatHttpFixture(t,{compression:true});await f.ready(['story','summary','memory']);const defaults=clone((await f.view()).session.models);
 const values={story:{temperature:{mode:'explicit',value:.72},stop:{mode:'explicit',value:[' 保留空格 ','终止🪷']},seed:{mode:'explicit',value:29}},summary:{temperature:{mode:'explicit',value:.15},'thinking.enabled':{mode:'explicit',value:true},'thinking.budgetTokens':{mode:'explicit',value:128}},memory:{topP:{mode:'explicit',value:.83},temperature:{mode:'explicit',value:.21}}};
 for(const purpose of ['story','summary','memory'])await f.parameters(purpose,values[purpose]);
 const before=await f.view();assert.equal(before.parameterSettings.revision,3);assert.equal(f.client.pending().length,0);assert.equal(f.tasks.length,0);
 const estimate=await f.ok('/joint/estimate',jointTaskIntent(before,'turn-one','Opening'));assert.equal(estimate.plan.maximumCalls,2);assert.equal(f.tasks.length,0);assert.deepEqual((await f.view()).budget,before.budget);
 await f.send('turn-one','Opening');await f.send('turn-two','Continue');
 assert.deepEqual(f.tasks.map(p=>p.purpose),['story','memory','story','summary','compression','memory']);
 for(const p of f.tasks){const purpose=p.purpose==='compression'?'summary':p.purpose;assert.deepEqual(p.parameters.values,values[purpose]);const receipt=f.results.get(p.sessionId+'/'+p.requestId).receipt;assert.deepEqual(receipt.parameters,f.resolve(p).wireParameters);}
 const after=await f.view();assert.deepEqual(after.session.models,defaults);assert.equal(after.session.turns.length,2);assert.equal(after.session.memory.snapshot.processedThrough,2);assert.equal(after.budget.used,6);assert.equal(f.client.pending().length,0);
 if(process.env.RPG_P2C3_EVIDENCE){await mkdir(process.env.RPG_P2C3_EVIDENCE,{recursive:true});await writeFile(join(process.env.RPG_P2C3_EVIDENCE,'http-three-purpose-capture.json'),JSON.stringify({tasks:f.tasks,receipts:[...f.results.values()].map(v=>v.receipt),session:after.session,budget:after.budget},null,2));}
});

test('chat HTTP rejects origin, content, unknown authority and all stale configuration revisions before writes',async t=>{
 const f=await chatHttpFixture(t);await f.ready();const input=await f.parameterIntent(),before=await f.view();
 assert.equal((await f.rejected('/joint/parameters/configure',input,'AUTHORING_ORIGIN_REJECTED',{Origin:'http://untrusted.invalid'})).status,403);
 await f.rejected('/joint/parameters/configure',input,'AUTHORING_CONTENT_TYPE',{'Content-Type':'text/plain'});
 for(const extra of [{system:'override'},{history:[]},{providerUrl:'https://untrusted.invalid'},{resolution:{}},{wireParameters:{temperature:1}}])await f.rejected('/joint/parameters/configure',{...input,...extra},'AUTHORING_UNKNOWN_FIELD');
 for(const [field,error] of [['expectedRevision','OFFLINE_REVISION_CONFLICT'],['expectedModelRevision','MODEL_REVISION_CONFLICT'],['expectedParameterRevision','PARAMETER_REVISION_CONFLICT'],['expectedPurposeRevision','PARAMETER_REVISION_CONFLICT']])await f.rejected('/joint/parameters/configure',{...input,[field]:99},error);
 const auth=pluginIntent(await f.settings(),'parameters','disable','stale-auth');auth.expectedParameterRevision=1;await f.rejected('/joint/settings/change',auth,'PARAMETER_REVISION_CONFLICT');
 await f.rejected('/joint/parameters/'+f.id+'/story?unexpected=1',undefined,'AUTHORING_QUERY_INVALID');
 await f.rejected('/joint/parameters/'+f.id+'/other',undefined,'AUTHORING_ROUTE_NOT_FOUND');
 const huge={...input,untrusted:'x'.repeat(1024*1024)};assert.equal((await f.rejected('/joint/parameters/configure',huge,'AUTHORING_BODY_SIZE')).status,413);
 assert.deepEqual((await f.view()).session,before.session);assert.deepEqual((await f.view()).budget,before.budget);assert.equal(f.tasks.length,0);
});

test('chat HTTP concurrent tabs apply one configuration; original replay works after grant revocation and restart',async t=>{
 const f=await chatHttpFixture(t);await f.ready();const a=await f.parameterIntent('story',{seed:{mode:'explicit',value:1}}),b={...clone(a),operationId:'other-tab',choice:f.selection('story',{seed:{mode:'explicit',value:2}})};
 const results=await Promise.all([f.call('/joint/parameters/configure',a),f.call('/joint/parameters/configure',b)]);assert.deepEqual(results.map(r=>r.status).sort(),[200,409]);
 const winner=results[0].status===200?a:b,success=results.find(r=>r.status===200).value;
 await f.change('parameters','disable');await f.reopen();const checks=f.requests.length;f.unavailable=true;
 const replay=await f.ok('/joint/parameters/configure',winner);assert.equal(replay.replayed,true);assert.deepEqual(replay.change,success.change);
 const recovered=await f.ok('/joint/parameters/reconcile',winner);assert.equal(recovered.state,'applied');assert.deepEqual(recovered.change,success.change);assert.equal(f.requests.length,checks);
 await f.rejected('/joint/parameters/reconcile',{...winner,choice:null},'OFFLINE_OPERATION_COLLISION');
 assert.equal((await f.view()).session.generationParameters.changes.length,1);assert.equal(f.tasks.length,0);
});

test('chat HTTP old send authorization blocks before reserve; disabled overrides remain saved and re-enable intact',async t=>{
 const f=await chatHttpFixture(t);await f.ready();await f.parameters('story',{seed:{mode:'explicit',value:8}});const saved=clone((await f.view()).session.generationParameters);
 const stale=jointTaskIntent(await f.view(),'stale','Opening');await f.change('parameters','disable');
 await f.rejected('/joint/estimate',stale,'PARAMETER_AUTHORIZATION_CHANGED');await f.rejected('/joint/send',stale,'PARAMETER_AUTHORIZATION_CHANGED');assert.equal((await f.view()).budget.used,0);
 await f.send('defaults','Opening');assert.deepEqual(f.tasks[0].parameters.values,{});assert.deepEqual((await f.view()).session.generationParameters,saved);
 await f.change('parameters','uninstall');await f.change('parameters','install');assert.equal((await f.settings()).plugins.parameters.enabled,false);
 await f.change('parameters','enable');await f.send('restored','Continue');assert.equal(f.tasks[1].parameters.values.seed.value,8);
 assert.deepEqual((await f.view()).session.generationParameters,saved);assert.equal(f.tasks.length,2);
});

test('chat HTTP lost parameter response recovers by original ID with no validation, rewrite or provider call',async t=>{
 const f=await chatHttpFixture(t);await f.ready();const intent=await f.parameterIntent('story',{stop:{mode:'explicit',value:['终止']}}),storage=browserStorage(),calls=[];
 const client=createJointClient({storage,sessionId:f.id,workId:f.reference.workId,api:async(p,b)=>{calls.push(p);const result=await f.api(p,b);if(p==='/joint/parameters/configure')throw {code:'NETWORK_RESULT_UNKNOWN'};return result;}});
 await assert.rejects(()=>client.submit('parameters',intent),code('NETWORK_RESULT_UNKNOWN'));assert.equal(client.pending().length,1);const saved=clone((await f.view()).session.generationParameters);
 await f.reopen();f.unavailable=true;const count=f.requests.length,second=createJointClient({storage,api:f.api,sessionId:f.id,workId:f.reference.workId});
 assert.equal((await second.recover(second.pending()[0])).state,'settled');assert.equal(second.pending().length,0);assert.deepEqual((await f.view()).session.generationParameters,saved);assert.equal(f.requests.length,count);assert.equal(f.tasks.length,0);assert.deepEqual(calls,['/joint/parameters/configure']);
});

test('chat HTTP never infers absence while a parameter save is still in progress',async t=>{
 const f=await chatHttpFixture(t);await f.ready();const input=await f.parameterIntent('story',{seed:{mode:'explicit',value:9}});let entered,release;
 const arrived=new Promise(r=>entered=r),hold=new Promise(r=>release=r);f.onCheck=async()=>{entered();await hold;};
 const saving=f.call('/joint/parameters/configure',input);await arrived;let settled=false;const checking=f.call('/joint/parameters/reconcile',input).then(r=>{settled=true;return r;});
 await new Promise(r=>setTimeout(r,20));assert.equal(settled,false);release();assert.equal((await saving).status,200);const r=await checking;assert.equal(r.status,200);assert.equal(r.value.state,'applied');assert.equal(f.tasks.length,0);
});

test('chat HTTP unapplied records can be explicitly cleared; IDs from model or settings writes cannot masquerade as parameters',async t=>{
 const f=await chatHttpFixture(t);await f.ready();const input=await f.parameterIntent();const absent=await f.ok('/joint/parameters/reconcile',input);assert.equal(absent.state,'not_applied');assert.equal(absent.change,null);
 const model=await f.modelIntent();await f.ok('/model-settings/configure',model);
 await f.rejected('/joint/parameters/reconcile',{...input,operationId:model.operationId},'OFFLINE_OPERATION_COLLISION');
 const settings=await f.settings(),grant=pluginIntent(settings,'parameters','disable','grant-collision');await f.ok('/joint/settings/change',grant);
 await f.rejected('/joint/parameters/configure',{...input,operationId:grant.operationId},'JOINT_SETTINGS_OPERATION_COLLISION');
 await f.rejected('/joint/parameters/reconcile',{...input,operationId:grant.operationId},'JOINT_SETTINGS_OPERATION_COLLISION');assert.equal(f.tasks.length,0);
});

test('chat HTTP unknown plugin grant journals block configuration until explicit original-operation reconciliation',async t=>{
 let inject=false;const f=await chatHttpFixture(t,{settingsFault:(stage)=>{if(inject&&stage==='afterChange'){inject=false;throw Error('lost settings response');}}});
 await f.enable('model');await f.select();await f.change('parameters','install');const i=pluginIntent(await f.settings(),'parameters','enable','authorize');inject=true;
 await f.rejected('/joint/settings/change',i,'JOINT_SETTINGS_RESULT_UNKNOWN');assert.equal((await f.settings()).plugins.parameters.enabled,true);
 await f.rejected('/joint/parameters/configure',await f.parameterIntent(),'JOINT_SETTINGS_RESULT_PENDING');
 await f.reopen();assert.equal((await f.ok('/joint/settings/reconcile',i)).state,'applied');await f.parameters('story',{seed:{mode:'explicit',value:4}});assert.equal(f.tasks.length,0);
});

test('chat HTTP unknown memory request survives restart; browser recovery only looks up the original frozen request',async t=>{
 const f=await chatHttpFixture(t,{dropPurpose:'memory'});await f.ready(['story','memory']);await f.parameters('memory',{temperature:{mode:'explicit',value:.19}});
 await f.send('memory-unknown','Opening');const v=await f.view();assert.equal(v.status,'unknown');assert.equal(f.client.pending().length,1);assert.equal(f.tasks.length,2);const frozen=clone(f.tasks);
 await f.reopen();assert.equal(f.tasks.length,2);await f.change('parameters','disable');
 const c=createJointClient({api:f.api,storage:f.storage,sessionId:f.id,workId:f.reference.workId});assert.equal((await c.recover(c.pending()[0])).state,'settled');
 assert.equal(c.pending().length,0);assert.deepEqual(f.tasks,frozen);assert.equal((await f.view()).session.memory.snapshot.processedThrough,1);assert.ok(f.lookups.length>0);
});

for(const kind of ['parameters','model'])test('chat HTTP serializes '+kind+' save against a same-ID plugin grant across journals',async t=>{
 const f=await chatHttpFixture(t);await f.ready();const settings=await f.settings(),input=kind==='parameters'?await f.parameterIntent('story',{seed:{mode:'explicit',value:6}}):await f.modelIntent();
 const grant=pluginIntent(settings,'history','install',input.operationId);let entered,release;
 const arrived=new Promise(r=>entered=r),hold=new Promise(r=>release=r);f.onCheck=async()=>{entered();await hold;};
 const saving=f.call(kind==='parameters'?'/joint/parameters/configure':'/model-settings/configure',input);await arrived;
 let finished=false;const granting=f.call('/joint/settings/change',grant).then(r=>{finished=true;return r;});
 await new Promise(r=>setTimeout(r,20));assert.equal(finished,false);release();assert.equal((await saving).status,200);
 const refused=await granting;assert.equal(refused.status,409);assert.equal(refused.value.error.code,'OFFLINE_REVISION_CONFLICT');
 const fresh=pluginIntent(await f.settings(),'history','install',input.operationId);await f.rejected('/joint/settings/change',fresh,'OFFLINE_OPERATION_COLLISION');
 assert.equal((await f.settings()).plugins.history.installed,false);assert.equal(f.tasks.length,0);
});

for(const purpose of ['summary','memory'])test('chat HTTP missing '+purpose+' model blocks before plan or reserve',async t=>{
 const f=await chatHttpFixture(t);await f.enable('model');await f.select();await f.enable(purpose);
 const before=await f.view(),input=jointTaskIntent(before,'not-ready','Opening'),error=purpose==='summary'?'SUMMARY_MODEL_REQUIRED':'MEMORY_MODEL_REQUIRED';
 await f.rejected('/joint/estimate',input,error);await f.rejected('/joint/send',input,error);
 assert.deepEqual((await f.view()).session,before.session);assert.deepEqual((await f.view()).budget,before.budget);assert.equal(f.tasks.length,0);
 await f.select(purpose);assert.equal((await f.ok('/joint/estimate',jointTaskIntent(await f.view(),'ready','Opening'))).plan.maximumCalls,purpose==='memory'?2:1);assert.equal(f.tasks.length,0);
});

async function futureSetup(f,operationId='future-config'){
 const h=f.getHost(),status=await h.comparisonFutureStatus(f.id),choice=await f.control.selectParameters(f.selection('story',{temperature:{mode:'explicit',value:.6},topK:{mode:'explicit',value:23},stop:{mode:'explicit',value:[' END ']}}));
 const {sessionId,expectedRevision,expectedModelRevision,expectedParameterRevision,expectedAuthorizationRevision}=status;
 return {intent:{sessionId,expectedRevision,expectedModelRevision,expectedParameterRevision,expectedAuthorizationRevision,operationId,batchId:'compare-'+sha256('trusted-test'),candidateId:'a'},choice};
}
for(const chat of [false,true])test('C2-B future configuration is atomic, idempotent and used by ordinary send: '+(chat?'chat':'native'),async t=>{
 const f=await fixture(t,{chatParameters:chat,modelPlugin:chat}),h=f.getHost();await f.select();
 const old=await f.read(),{intent,choice}=await futureSetup(f),r=await h.configureFutureStory(intent,choice);
 assert.equal(r.revision,old.session.revision+1);assert.equal(f.tasks.length,0);assert.deepEqual(await h.configureFutureStory(intent,choice),r);
 const after=(await f.read()).session;assert.equal(after.models.revision,old.session.models.revision+1);
 assert.deepEqual(after.models.current.memory,old.session.models.current.memory);assert.deepEqual(after.models.current.summary,old.session.models.current.summary);
 if(chat){assert.deepEqual(after.generationParameters.current.story,choice);assert.deepEqual(after.models.current.story.parameterProfile.parameters.values,{});}
 else assert.deepEqual(after.models.current.story,choice);
 await assert.rejects(()=>h.configureFutureStory({...intent,operationId:'stale-future'},choice),code('COMPARISON_FUTURE_CONFIGURATION_CHANGED'));
 await f.reopen();assert.deepEqual(await f.getHost().comparisonFutureOperation(f.id,intent.operationId),r);
 await f.send('after-future','Opening');assert.deepEqual(f.results.get(f.id+'/'+f.tasks[0].requestId).receipt.parameters,choice.wireParameters);
 assert.equal(f.tasks.length,1);
});
test('C2-B future transfer rejects missing permissions and late revoke without partial changes',async t=>{
 let f,revoke=false;f=await fixture(t,{chatParameters:true,modelPlugin:true,fault:async stage=>{if(stage==='futurePrepared'&&revoke)await f.plugin('disable',PARAMETER_PLUGIN);}});
 await f.select();let x=await futureSetup(f);await f.plugin('disable',PARAMETER_PLUGIN);
 const before=(await f.read()).session;const status=await f.getHost().comparisonFutureStatus(f.id);assert.equal(status.allowed,false);
 x.intent.expectedAuthorizationRevision=status.expectedAuthorizationRevision;
 await assert.rejects(()=>f.getHost().configureFutureStory(x.intent,x.choice),code('COMPARISON_FUTURE_AUTHORIZATION_REQUIRED'));
 assert.deepEqual((await f.read()).session,before);
 await f.plugin('enable',PARAMETER_PLUGIN);x=await futureSetup(f);revoke=true;
 await assert.rejects(()=>f.getHost().configureFutureStory(x.intent,x.choice));assert.deepEqual((await f.read()).session,before);assert.equal(f.tasks.length,0);
});
for(const point of ['futurePrepared','futureSaved'])test('C2-B future persistence fault '+point+' preserves an all-or-nothing receipt',async t=>{
 let crash=true;const f=await fixture(t,{chatParameters:true,modelPlugin:true,fault:async stage=>{if(crash&&stage===point)throw Error('synthetic interruption');}});
 await f.select();const before=(await f.read()).session,x=await futureSetup(f);
 await assert.rejects(()=>f.getHost().configureFutureStory(x.intent,x.choice));crash=false;await f.reopen();
 const found=await f.getHost().comparisonFutureOperation(f.id,x.intent.operationId);
 if(point==='futurePrepared'){assert.equal(found.status,'not_applied');assert.deepEqual((await f.read()).session,before);}
 else{assert.equal(found.status,'applied');assert.equal((await f.read()).session.revision,before.revision+1);assert.deepEqual(await f.getHost().configureFutureStory(x.intent,x.choice),found);}
 assert.equal(f.tasks.length,0);
});

// P2-C4 chat UI contracts and isolated loopback preview.
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
import {chatParameterDraft,chatDraftDirty,chatDraftIssues,addChatStop,parameterValueText,createChatModelClient} from './chat-parameter-ui.mjs';
import {pluginIntent,parameterEditIntent,createJointClient,jointTaskIntent} from './joint-client.mjs';
import {readFile} from 'node:fs/promises';
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

const storage=()=>{const s={};Object.defineProperties(s,{getItem:{value:k=>s[k]??null},setItem:{value:(k,v)=>s[k]=v},removeItem:{value:k=>delete s[k]}});return s;};
async function fixture(t,options={}){
 const directory=options.directory||await mkdtemp(join(tmpdir(),'rpg-chat-panel-'));await mkdir(directory,{recursive:true});
 const f=await controlFixture(t,{...options,captureDirectory:directory}),provider=await ControlledProvider.open({directory:join(directory,'transport'),origin:f.origin,serviceToken:token,recoverDeadOwner:options.recoverDeadOwner===true});
 const host=await startAuthoringServer({directory:join(directory,'works'),port:options.port||0,chatParameters:true,recoverDeadOwner:options.recoverDeadOwner===true,
  modelSettings:{directory:join(directory,'sessions'),provider,control:f.control},historySettings:{directory:join(directory,'plugins')},
  playbackSettings:{directory:join(directory,'playback')},jointSettings:{directory:join(directory,'joint'),allowance:{id:'p2c4-offline-only',limit:30}}});
 t.after(async()=>{await host.close();await provider.close();});
 const api=async(path,body)=>{const r=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw {...value.error,httpStatus:r.status};return value;};
 let info;try{info=JSON.parse(await readFile(join(directory,'preview.json'),'utf8'));}catch{}
 if(!info){const document=blankDocument();document.metadata.name='港口夜航 · 离线参数验收';document.metadata.audience='female';document.sources.system='使用本地样例验证参数，不连接真实供应商。';document.sources.outputCss='.reply { color: #26364a; font-family: system-ui; line-height: 1.8; padding: 12px; }';
  const w=await api('/works',{operationId:'panel-work',expectedRevision:null,document}),pub=await api('/works/'+w.workId+'/publish',{operationId:'panel-version',expectedRevision:0});
  const made=await api('/model-settings/sessions',{operationId:'panel-session',expectedRevision:null,reference:{workId:w.workId,versionId:pub.result.versionId},openingText:'走到港口，观察夜航的灯火。',presetId:null});
  info={workId:w.workId,sessionId:made.session.id};
 }
 const client=createJointClient({api,storage:storage(),sessionId:info.sessionId,workId:info.workId});
 async function change(p,action){const s=await api('/joint/settings/'+info.sessionId);return client.submit('settings',pluginIntent(s,p,action,crypto.randomUUID()));}
 async function enable(p){await change(p,'install');await change(p,'enable');}
 async function select(p){const v=await client.view();return api('/model-settings/configure',{sessionId:info.sessionId,operationId:crypto.randomUUID(),expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,purpose:p,choice:f.selection(p)});}
 return {...f,...info,directory,api,client,host,enable,select,change};
}
function draftFixture(){const p=policy(),e={selectionId:'story',selectionRevision:sha256('story'),capabilityRevision:digest(p),modelId:'synthetic/story',available:true,parameterPolicy:p},view={session:{id:'s',revision:0,models:{current:{story:{entry:e}}},generationParameters:{current:{story:null,summary:null,memory:null}}}};return {e,view,catalog:{revision:'r',models:[e]}};}
if(process.argv.includes('--preview')){
 const arg=process.argv.indexOf('--preview'),directory=process.argv[arg+1],port=Number(process.argv[arg+2]),cleanup=[];
 const f=await fixture({after:fn=>cleanup.unshift(fn)},{directory,port,recoverDeadOwner:process.argv.includes('--recover-dead-owner'),text:p=>p.purpose==='memory'?'{"operations":[]}':p.purpose==='story'?'<section class="reply"><h2>港口的灯火</h2><p>夜航的灯光映在水面。这是离线固定回复，用于核对请求中的模型与参数。</p><details><summary>航行记录</summary><p>剧情只在明确点击发送后进入历史。</p></details></section>':'港口夜航的固定摘要。'});
 await writeFile(join(directory,'preview.json'),JSON.stringify({origin:f.host.origin,workId:f.workId,sessionId:f.sessionId,mode:'loopback-fake-only'},null,2));
 console.log(JSON.stringify({url:f.host.origin+'/chat?session='+f.sessionId,mode:'offline-fake-provider-only'}));
 process.on('SIGINT',async()=>{for(const fn of cleanup)await fn();process.exit(0);});
}else{
 test('chat draft distinguishes default, zero, same-value edit and incompatible old model',()=>{
  const {view,e,catalog}=draftFixture(),d=chatParameterDraft(view,'story',catalog);assert.equal(chatDraftDirty(d),false);d.draft.temperature={mode:'explicit',value:'0'};assert.equal(chatDraftDirty(d),true);assert.equal(chatDraftIssues(d).length,0);d.draft.temperature.mode='default';assert.equal(chatDraftDirty(d),false);
  view.session.generationParameters.current.story={entry:{modelId:'older',selectionId:'old'},parameterProfile:{parameters:{values:{topK:{mode:'explicit',value:20}}}}};delete e.parameterPolicy.fields.topK;const changed=chatParameterDraft(view,'story',catalog);assert.equal(changed.incompatible,true);assert.equal(changed.draft.topK.value,20);assert.ok(chatDraftIssues(changed).some(i=>i.code==='PARAMETER_UNSUPPORTED'));
 });
 test('chat stop list retains Unicode and whitespace and rejects blank duplicates and overflow',()=>{
  const {view,catalog}=draftFixture(),d=chatParameterDraft(view,'story',catalog);d.draft.stop.mode='explicit';d.buffer='未添加';assert.ok(chatDraftIssues(d).some(x=>x.code==='STOP_NOT_ADDED'));addChatStop(d,' 再见🪷 ');assert.deepEqual(d.draft.stop.value,[' 再见🪷 ']);assert.equal(d.buffer,'');assert.throws(()=>addChatStop(d,''));assert.throws(()=>addChatStop(d,' 再见🪷 '));assert.throws(()=>addChatStop(d,'字'.repeat(81)));
 });
 test('chat displayed configuration reports defaults without exposing provider routing',()=>{
  assert.equal(parameterValueText(null),'尚未选择模型');assert.equal(parameterValueText({parameterProfile:{parameters:{values:{temperature:{mode:'default'}}}}}),'使用模型默认值');assert.match(parameterValueText({parameterProfile:{parameters:{values:{temperature:{mode:'explicit',value:0}}}}}),/0/);
 });
 test('chat model write journals before submit and recovers original intent without replay',async()=>{
  const s=storage(),body={sessionId:'session-1',operationId:'model-1',purpose:'story'},record={kind:'configure',workId:'work-1',body};let sends=0,queries=0;
  const c=createChatModelClient({storage:s,sessionId:'session-1',workId:'work-1',api:async(path,p)=>{if(path.endsWith('configure')){sends++;assert.deepEqual(c.pending(),record);throw {code:'NETWORK_RESULT_UNKNOWN'};}queries++;assert.equal(p.operationId,body.operationId);return {found:true,view:{session:{id:'session-1',binding:{workId:'work-1'},models:{changes:[{intent:body}]}}}};}});
  await assert.rejects(c.submit(body));assert.ok(c.pending());assert.equal((await c.recover()).state,'settled');assert.equal(c.pending(),null);assert.equal(sends,1);assert.equal(queries,1);
 });
 test('chat unknown model save cannot be discarded; confirmed absence can be discarded',async()=>{
  let running=true;const s=storage(),c=createChatModelClient({storage:s,sessionId:'s',workId:'w',api:async(path)=>{if(path.endsWith('configure'))throw {code:'NETWORK_RESULT_UNKNOWN'};return {found:false,view:{busy:running,session:{id:'s',binding:{workId:'w'}}}};}});await assert.rejects(c.submit({sessionId:'s',operationId:'o'}));assert.equal((await c.recover()).state,'unknown');await assert.rejects(c.discard());running=false;await c.discard();assert.equal(c.pending(),null);
 });
 test('chat refuses false model receipts and local storage failure before mutation',async()=>{
  const s=storage(),c=createChatModelClient({storage:s,sessionId:'s',workId:'w',api:async()=>({change:{intent:{wrong:true}}})});await assert.rejects(c.submit({sessionId:'s',operationId:'o'}));assert.ok(c.pending());let sent=0;const noStore={getItem:()=>null,setItem:()=>{throw Error();}};const blocked=createChatModelClient({storage:noStore,sessionId:'s',workId:'w',api:async()=>sent++});await assert.rejects(blocked.submit({sessionId:'s',operationId:'x'}));assert.equal(sent,0);
 });
 test('chat valid receipt equality ignores object key ordering',async()=>{const c=createChatModelClient({storage:storage(),sessionId:'s',workId:'w',api:async()=>({change:{intent:{purpose:'story',operationId:'o',sessionId:'s'}}})});await c.submit({sessionId:'s',operationId:'o',purpose:'story'});assert.equal(c.pending(),null);});
 test('chat UI page and explicit preset routes bind formal session without calls',async t=>{
  const f=await fixture(t),r=await fetch(f.host.origin+'/chat?session='+f.sessionId),html=await r.text();assert.equal(r.status,200);assert.match(html,/chat-parameter-ui.mjs/);assert.match(r.headers.get('content-security-policy'),/script-src 'self'/);assert.doesNotMatch(html,/onload=|onclick=/);
  for(const name of ['chat-parameter-ui.mjs','chat-parameter-ui.css','model-ui.mjs','history-ui.mjs','browser-api.mjs','joint-client.mjs'])assert.equal((await fetch(f.host.origin+'/'+name)).status,200,name);
  assert.equal((await fetch(f.host.origin+'/')).status,404);assert.equal((await fetch(f.host.origin+'/chat?session=wrong')).status,404);
  assert.equal((await f.api('/model-settings')).presetsEnabled,true);assert.deepEqual((await f.api('/model-settings/presets')).presets,[]);assert.equal(f.tasks.length,0);
 });
 test('chat panel commands apply all purposes and preserve defaults, presets, and exact wire',async t=>{
  const f=await fixture(t);for(const p of ['story','summary','memory']){await f.enable(p==='story'?'model':p);await f.select(p);}await f.enable('parameters');
  let view=await f.client.view();const baseline=clone(view.session.models.current);
  for(const [p,n] of [['story','0.73'],['summary','0.19'],['memory','0.24']]){
   const {catalog}=await f.api('/joint/parameters/'+f.sessionId+'/'+p),d=chatParameterDraft(view,p,catalog);d.draft.temperature={mode:'explicit',value:n};const choice=typedSelection(catalog,d.entry,d.draft,p);
   assert.equal((await f.api('/model-settings/check',{choice})).dispatched,false);
   await f.client.submit('parameters',parameterEditIntent(view,crypto.randomUUID(),p,choice));view=await f.client.view();
   const {format,profile,...selection}=choice,presets=await f.api('/model-settings/presets'),body={operationId:crypto.randomUUID(),expectedRevision:presets.revision,id:crypto.randomUUID(),name:p+' 预设',expectedPresetRevision:0,profile,selection};const saved=await f.api('/model-settings/presets',body);assert.equal(await presetReceiptMatches(body,saved),true);
  }
  assert.deepEqual(view.session.models.current,baseline);assert.equal(f.tasks.length,0);assert.equal((await f.api('/model-settings/presets')).presets.length,3);
  await f.client.submit('send',jointTaskIntent(view,crypto.randomUUID(),view.session.binding.openingText));await f.host.sessionHost.waitForIdle(f.sessionId);await f.client.view();assert.deepEqual(f.tasks.map(x=>x.purpose),['story','memory']);assert.equal(f.results.values().next().value.receipt.parameters.temperature,0.73);assert.equal([...f.results.values()].at(-1).receipt.parameters.temperature,0.24);
  const before=await f.client.view();await f.change('parameters','disable');const disabled=await f.client.view();assert.deepEqual(disabled.session.generationParameters,before.session.generationParameters);assert.equal(disabled.parameterSettings.authorization.enabled,false);assert.deepEqual(disabled.parameterSettings.effective.story,baseline.story);
 });
 test('chat UI assets contain no inline execution or external provider fields',async()=>{
  const ui=await readFile(new URL('./chat-parameter-ui.mjs',import.meta.url),'utf8'),css=await readFile(new URL('./chat-parameter-ui.css',import.meta.url),'utf8');assert.doesNotMatch(ui,/\beval\s*\(|new Function|api[_-]?key|OPENROUTER|LLM_GATEWAY/);assert.match(css,/@media\(max-width:600px\)/);assert.match(css,/prefers-reduced-motion/);
 });
}

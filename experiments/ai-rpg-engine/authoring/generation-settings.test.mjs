import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,readFile,writeFile,readdir,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {blankDocument,validateDocument,canonical,sha256} from './contracts.mjs';
import {AuthoringStore} from './store.mjs';
import {ControlPlaneModelControl} from './model-control.mjs';
import {ParameterSettingsStore,PARAMETER_PROFILE,PARAMETER_CONFIG,PARAMETER_RECOMMENDATIONS,PARAMETER_PURPOSES,validateParameterProfile,validateRecommendations} from './generation-settings.mjs';

const hash=v=>sha256(canonical(v)),clone=v=>structuredClone(v),code=c=>e=>e?.code===c;
const denied=(fn,c)=>assert.rejects(async()=>fn(),code(c));
const sessionId='session-'+sha256('parameter-session'),otherSession='session-'+sha256('other');
const python=process.env.RPG_TEST_PYTHON||'python';
const parameterSource=fileURLToPath(new URL('../../../server/model_router/rpg_parameters.py',import.meta.url));
const resolvePython=`import sys,json,runpy,hashlib\nm=runpy.run_path(sys.argv[1]);v=json.load(sys.stdin)\ntry:\n r=m['resolve_parameters'](v['policy'],v['config'],v['purpose']);r={'format':'modelmirror.rpg.parameter-resolution/2','capabilityRevision':v['capabilityRevision'],**r};r['sha256']=hashlib.sha256(json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest();print(json.dumps({'resolution':r}))\nexcept m['ParameterError'] as e:print(json.dumps({'error':e.code}))`;
const field=(type,extra={},value=null)=>({type,required:false,default:value===null?{mode:'omit'}:{mode:'value',value},...extra});
const policy=()=>({kind:'editable',fields:{
  temperature:field('number',{minimum:0,maximum:2,wireName:'temperature'}),
  topP:field('number',{minimum:0,maximum:1,wireName:'top_p'}),
  maxOutputTokens:field('integer',{minimum:1,maximum:16384,required:true,wireName:'max_tokens'},4096),
  topK:field('integer',{minimum:0,maximum:100,wireName:'top_k'}),
  stop:field('text-list',{minItems:1,maxItems:4,maxItemLength:80,wireName:'stop'}),
  seed:field('integer',{minimum:0,maximum:9999,wireName:'seed'}),
  'thinking.enabled':field('boolean',{wireName:'reasoning.enabled'}),
  'thinking.effort':field('enum',{options:['low','high'],wireName:'reasoning.effort'}),
  'thinking.budgetTokens':field('integer',{minimum:0,maximum:8192,wireName:'reasoning.max_tokens'}),
},exclusive:[],requires:[],lessThan:[['thinking.budgetTokens','maxOutputTokens']],purposes:[...PARAMETER_PURPOSES]});
const profile=(purpose='story',values={temperature:{mode:'explicit',value:.7}},modelId='fixture/gemini')=>({format:PARAMETER_PROFILE,purpose,modelId,parameters:{format:PARAMETER_CONFIG,values:clone(values)}});
function selection(c,id='route-a'){const e=c.models.find(e=>e.selectionId===id);return {catalogRevision:c.revision,selectionId:id,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision};}
async function fixture(t,options={}) {
  const directory=await mkdtemp(join(tmpdir(),'rpg-p1b-'));
  const f={directory,calls:[],providerCalls:0,policies:{'route-a':policy(),'route-b':policy()},names:{'route-a':'fixture/gemini','route-b':'fixture/other'},mutateResult:null,invalidStatus:null};
  f.catalog=()=>{const models=Object.entries(f.policies).map(([id,p])=>({selectionId:id,selectionRevision:hash({id,p}),capabilityRevision:hash(p),modelId:f.names[id],name:id,available:true,contextLength:128000,maxOutputTokens:16384,parameterEvidence:'provider-declared',parameterPolicy:{...clone(p),fields:Object.fromEntries(Object.entries(p.fields).map(([k,{wireName,...field}])=>[k,field]))}}));const body={format:'modelmirror.rpg.control-model-catalog/2',source:'control-plane-cache',catalogId:'fixture-catalog',models};return {...body,revision:hash(body)};};
  const server=http.createServer(async(req,res)=>{
    try {
      const chunks=[];for await(const c of req)chunks.push(c);const body=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
      f.calls.push({method:req.method,path:req.url,body});
      assert.equal(req.headers.authorization,'Bearer '+ 'x'.repeat(40));
      res.setHeader('content-type','application/json');
      if(f.invalidStatus){res.statusCode=f.invalidStatus;res.end('{}');return;}
      if(req.method==='GET'&&req.url==='/api/rpg/v1/authoring/models?parameterVersion=2'){const c=f.catalog();if(f.mutateCatalog)f.mutateCatalog(c);res.end(JSON.stringify(c));return;}
      if(req.method==='POST'&&req.url==='/api/rpg/v1/authoring/parameters/validate') {
        const c=f.catalog(),entry=c.models.find(e=>e.selectionId===body.selectionId);
        if(body.catalogRevision!==c.revision||body.selectionRevision!==entry?.selectionRevision||body.capabilityRevision!==entry?.capabilityRevision){res.statusCode=409;res.end('{}');return;}
        const result=JSON.parse(execFileSync(python,['-X','utf8','-B','-c',resolvePython,parameterSource],{input:JSON.stringify({policy:f.policies[body.selectionId],config:body.parameters,purpose:body.purpose,capabilityRevision:body.capabilityRevision}),encoding:'utf8',windowsHide:true}));
        if(result.error){res.statusCode=422;res.end(JSON.stringify(result));return;}
        const output={format:'modelmirror.rpg.parameter-check/2',catalogRevision:c.revision,selectionId:entry.selectionId,selectionRevision:entry.selectionRevision,resolution:result.resolution,dispatched:false};
        if(f.mutateResult)f.mutateResult(output);res.end(JSON.stringify(output));return;
      }
      f.providerCalls++;res.statusCode=500;res.end('{}');
    } catch(error){res.statusCode=500;res.end(JSON.stringify({error:'fixture-error'}));f.serverError=error;}
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  f.control=new ControlPlaneModelControl({origin:'http://127.0.0.1:'+server.address().port,serviceToken:'x'.repeat(40)});
  f.store=await ParameterSettingsStore.open({directory:join(directory,'settings'),control:f.control,...options});
  f.config=(p='story',revision=0,pRevision=0,extra={})=>({sessionId,operationId:'set-'+p+'-'+revision,expectedRevision:revision,expectedPurposeRevision:pRevision,purpose:p,profile:profile(p),selection:selection(f.catalog()),...extra});
  f.preset=(extra={})=>({operationId:'save-preset',expectedRevision:0,id:'precise',name:'精确叙述',expectedPresetRevision:0,profile:profile(),selection:selection(f.catalog()),...extra});
  f.apply=(extra={})=>({sessionId,operationId:'apply-preset',expectedRevision:0,expectedPurposeRevision:0,purpose:'story',presetId:'precise',expectedPresetRevision:1,selection:selection(f.catalog()),...extra});
  t.after(async()=>{await f.store.close();server.closeAllConnections();await new Promise(r=>server.close(r));assert.equal(f.providerCalls,0);if(f.serverError)throw f.serverError;});
  return f;
}

test('portable profiles preserve default/explicit, Unicode and whitespace without authority',()=>{
  const p=profile('memory',{stop:{mode:'explicit',value:['\n\n',' 停止🪷 ']},'thinking.enabled':{mode:'explicit',value:false},maxOutputTokens:{mode:'default'}});
  assert.deepEqual(validateParameterProfile(p),p);
  const rec={format:PARAMETER_RECOMMENDATIONS,story:null,summary:null,memory:p};assert.deepEqual(validateRecommendations(rec),rec);
  for(const extra of ['serviceToken','wireParameters','route','quota','authorization','selectionRevision'])assert.throws(()=>validateParameterProfile({...p,[extra]:'forbidden'}),code('AUTHORING_UNKNOWN_FIELD'));
  assert.throws(()=>validateRecommendations({...rec,story:p}),code('PARAMETER_PURPOSE_INVALID'));
});
for(const [name,values] of Object.entries({unknown:{provider:{mode:'default'}},mode:{temperature:{mode:'inherit'}},defaultValue:{temperature:{mode:'default',value:.7}},infinite:{temperature:{mode:'explicit',value:Infinity}},integer:{seed:{mode:'explicit',value:1.5}},duplicateStop:{stop:{mode:'explicit',value:['a','a']}},unsafeInteger:{seed:{mode:'explicit',value:2**53}},invalidBoolean:{'thinking.enabled':{mode:'explicit',value:'true'}}}))test('reject malformed portable '+name,()=>assert.throws(()=>validateParameterProfile(profile('story',values))));

test('three purposes are isolated, versioned, immutable and survive reload',async t=>{
  const f=await fixture(t);assert.equal((await f.store.readSession(sessionId)).revision,0);assert.deepEqual(await readdir(join(f.directory,'settings','records')),[]);
  for(const [i,p] of PARAMETER_PURPOSES.entries())await f.store.configure(f.config(p,i,0,{profile:profile(p,{temperature:{mode:'explicit',value:.2+i*.3}})}));
  let state=await f.store.readSession(sessionId);assert.equal(state.revision,3);
  for(const [i,p] of PARAMETER_PURPOSES.entries()){assert.equal(state.current[p].revision,1);assert.equal(state.current[p].configuration.resolution.effectiveParameters.temperature,.2+i*.3);}
  const frozen=clone(state.current);await f.store.configure(f.config('story',3,1,{profile:null,selection:null}));state=await f.store.readSession(sessionId);
  assert.equal(state.current.story.configuration,null);assert.deepEqual(state.current.memory,frozen.memory);assert.deepEqual(state.current.summary,frozen.summary);
  await f.store.close();f.store=await ParameterSettingsStore.open({directory:join(f.directory,'settings'),control:f.control});assert.deepEqual(await f.store.readSession(sessionId),state);
  assert.equal((await f.store.readSession(otherSession)).revision,0);
});

test('default omission differs from explicit defaults; actual Python resolution is captured',async t=>{
  const f=await fixture(t),p=profile('story',{maxOutputTokens:{mode:'default'},topP:{mode:'default'},stop:{mode:'explicit',value:['\n\n','终止🪷']},'thinking.enabled':{mode:'explicit',value:true},'thinking.budgetTokens':{mode:'explicit',value:1000}});
  const op=await f.store.configure(f.config('story',0,0,{profile:p}));const r=op.result.configuration.resolution;
  assert.equal(r.sources.topP.resolution,'omitted');assert.equal(r.sources.maxOutputTokens.resolution,'declared-default');assert.deepEqual(r.wireParameters,{max_tokens:4096,stop:['\n\n','终止🪷'],reasoning:{enabled:true,max_tokens:1000}});
  const next=await f.store.configure(f.config('story',1,1,{profile:profile('story',{maxOutputTokens:{mode:'explicit',value:4096}})}));assert.equal(next.result.configuration.resolution.sources.maxOutputTokens.mode,'explicit');
  if(process.env.RPG_P1B_EVIDENCE){await mkdir(process.env.RPG_P1B_EVIDENCE,{recursive:true});await writeFile(join(process.env.RPG_P1B_EVIDENCE,'preflight-capture.json'),JSON.stringify({requests:f.calls,configuration:op.result.configuration},null,2));}
});

test('caller edits during async preflight cannot mutate accepted intent',async t=>{
  const f=await fixture(t),input=f.config();const job=f.store.configure(input);input.profile.parameters.values.temperature.value=1.9;input.purpose='memory';await job;
  const s=await f.store.readSession(sessionId);assert.equal(s.current.story.configuration.profile.parameters.values.temperature.value,.7);assert.equal(s.current.memory.configuration,null);
});

test('idempotent retry is read-only, collisions and both revision checks reject',async t=>{
  const f=await fixture(t),input=f.config(),first=await f.store.configure(input),before=f.calls.length;f.invalidStatus=503;
  assert.deepEqual(await f.store.configure(input),{...first,replayed:true});assert.equal(f.calls.length,before);
  await denied(()=>f.store.configure({...input,profile:profile('story',{})}),'PARAMETER_OPERATION_COLLISION');
  await denied(()=>f.store.configure({...input,operationId:'new'}),'PARAMETER_REVISION_CONFLICT');
  await denied(()=>f.store.configure(f.config('story',1,0)),'PARAMETER_PURPOSE_REVISION_CONFLICT');
  assert.equal(f.calls.length,before);
});

test('concurrent tabs accept one CAS; changing one purpose does not reset another',async t=>{
  const f=await fixture(t);const results=await Promise.allSettled([f.store.configure(f.config()),f.store.configure(f.config('memory'))]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(results.find(r=>r.status==='rejected').reason.code,'PARAMETER_REVISION_CONFLICT');assert.equal((await f.store.readSession(sessionId)).revision,1);
});

test('preset save is separate; apply always revalidates and retains old evidence',async t=>{
  const f=await fixture(t);await f.store.savePreset(f.preset());assert.equal((await f.store.readSession(sessionId)).revision,0);
  const old=await f.store.listPresets(),before=f.calls.length,oldSelection=selection(f.catalog());await f.store.applyPreset(f.apply());assert.ok(f.calls.length>before);
  f.policies['route-a'].fields.temperature.maximum=1;const current=selection(f.catalog());
  await denied(()=>f.store.applyPreset(f.apply({operationId:'stale',expectedRevision:1,expectedPurposeRevision:1,selection:oldSelection})),'MODEL_CATALOG_CHANGED');
  await f.store.applyPreset(f.apply({operationId:'recheck',expectedRevision:1,expectedPurposeRevision:1,selection:current}));
  assert.deepEqual(await f.store.listPresets(),old);const s=await f.store.readSession(sessionId);assert.notEqual(s.current.story.configuration.selection.capabilityRevision,old.current[0].configuration.selection.capabilityRevision);
  assert.equal(s.operations[0].result.configuration.selection.capabilityRevision,old.current[0].configuration.selection.capabilityRevision);
});

test('capability removal blocks saved preset with useful issue; original parameters retained',async t=>{
  const f=await fixture(t);await f.store.savePreset(f.preset({profile:profile('story',{topK:{mode:'explicit',value:30}})}));delete f.policies['route-a'].fields.topK;
  const original=(await f.store.listPresets()).current[0].configuration.profile;
  const inspection=await f.store.inspect(original,selection(f.catalog()));assert.equal(inspection.valid,false);assert.ok(inspection.issues.some(i=>i.code==='PARAMETER_UNSUPPORTED'&&i.field==='topK'));assert.deepEqual(inspection.profile,original);
  await assert.rejects(()=>f.store.applyPreset(f.apply({selection:selection(f.catalog())})),e=>e.code==='PARAMETER_PREFLIGHT_REJECTED'&&e.details.issues.some(i=>i.field==='topK'));
  assert.equal((await f.store.readSession(sessionId)).revision,0);
});

test('cross-model application does not convert; purpose and preset revision are enforced',async t=>{
  const f=await fixture(t);await f.store.savePreset(f.preset());
  const inspect=await f.store.inspect(profile(),selection(f.catalog(),'route-b'));assert.deepEqual(inspect.issues,[{code:'PARAMETER_MODEL_MISMATCH',field:null}]);
  await denied(()=>f.store.applyPreset(f.apply({selection:selection(f.catalog(),'route-b')})),'PARAMETER_PREFLIGHT_REJECTED');
  await denied(()=>f.store.applyPreset(f.apply({purpose:'memory'})),'PARAMETER_PURPOSE_INVALID');
  await denied(()=>f.store.applyPreset(f.apply({expectedPresetRevision:2})),'PARAMETER_PRESET_REVISION_CONFLICT');
  await denied(()=>f.store.savePreset(f.preset({operationId:'rewrite',expectedRevision:1,expectedPresetRevision:1,profile:profile('memory')})),'PARAMETER_PRESET_IDENTITY_CONFLICT');
  await f.store.savePreset(f.preset({operationId:'new-memory',expectedRevision:1,id:'memory',profile:profile('memory')}));assert.equal((await f.store.listPresets()).current.length,2);
});

test('constraint diagnostics do not call validation or mutate settings',async t=>{
  const f=await fixture(t);f.policies['route-a'].exclusive=[['temperature','topP']];f.policies['route-a'].requires=[{if:{field:'topK',op:'set'},then:{field:'seed',op:'set'}}];
  const result=await f.store.inspect(profile('story',{temperature:{mode:'explicit',value:.7},topP:{mode:'explicit',value:.8},topK:{mode:'explicit',value:2},'thinking.effort':{mode:'explicit',value:'high'},'thinking.budgetTokens':{mode:'explicit',value:5000}}),selection(f.catalog()));
  assert.ok(result.issues.some(i=>i.code==='PARAMETER_CONFLICT'));assert.ok(result.issues.some(i=>i.code==='PARAMETER_DEPENDENCY'));assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
});

for(const [name,mutate] of Object.entries({dispatched:r=>r.dispatched=true,wire:r=>r.resolution.wireParameters.api_key='forbidden',capability:r=>r.resolution.capabilityRevision=sha256('other'),purpose:r=>r.resolution.purpose='memory',effective:r=>r.resolution.effectiveParameters.temperature=1,default:r=>{r.resolution.effectiveParameters.maxOutputTokens=5000;r.resolution.wireParameters.max_tokens=5000;},source:r=>r.resolution.sources.temperature.mode='default'}))test('reject corrupted control receipt '+name,async t=>{const f=await fixture(t);f.mutateResult=mutate;await denied(()=>f.store.configure(f.config()),'MODEL_CONTROL_RESPONSE_INVALID');assert.equal((await f.store.readSession(sessionId)).revision,0);});

for(const [phase,error] of [['beforeCommit','OFFLINE_WRITE_FAILED'],['afterCommit','OFFLINE_WRITE_UNKNOWN']])test('storage '+phase+' is recoverable without revalidation or reset',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async p=>{if(armed&&p===phase){armed=false;throw Error('disk fixture');}}});const input=f.config();await denied(()=>f.store.configure(input),error);
  const requests=f.calls.length;await f.store.close();f.store=await ParameterSettingsStore.open({directory:join(f.directory,'settings'),control:f.control});
  if(phase==='afterCommit')await denied(()=>f.store.recover('presets','presets',input.operationId),'OFFLINE_RECOVERY_TARGET_CONFLICT');
  const recovered=await f.store.recover('session',sessionId,input.operationId);assert.equal(recovered.status,phase==='afterCommit'?'committed':'not_found');assert.equal(f.calls.length,requests);
  if(phase==='afterCommit'){const retry=await f.store.configure(input);assert.equal(retry.replayed,true);assert.equal(f.calls.length,requests);}else{assert.equal((await f.store.readSession(sessionId)).revision,0);await f.store.configure(input);}
});

test('file corruption fails closed; a live second owner cannot open store',async t=>{
  const f=await fixture(t);await f.store.configure(f.config());await denied(()=>ParameterSettingsStore.open({directory:join(f.directory,'settings'),control:f.control}),'OFFLINE_STORE_BUSY');
  const path=join(f.directory,'settings','records','session-'+sha256('generation-parameters:'+sessionId)+'.json'),record=JSON.parse(await readFile(path,'utf8'));record.data.current.story.revision=99;record.sha256=hash(record.data);await writeFile(path,JSON.stringify(record));
  await denied(()=>f.store.readSession(sessionId),'PARAMETER_STORE_CORRUPT');
});

test('recommendations roundtrip through real work store, publish, copy, import; old version unchanged',async t=>{
  const f=await fixture(t);for(const [i,p] of PARAMETER_PURPOSES.entries())await f.store.configure(f.config(p,i));
  const rec=await f.store.recommendations(sessionId),state=await f.store.readSession(sessionId);const original=blankDocument();assert.deepEqual(validateDocument(original),original);
  const document=blankDocument();document.metadata.name='参数推荐验收';document.metadata.audience='female';document.sources.system='作者原文\r\n  不改写';document.model.recommendations=rec;
  const first=await AuthoringStore.open(join(f.directory,'works'));t.after(()=>first.close());const made=await first.create({operationId:'create',expectedRevision:null,document});
  const published=await first.publish(made.workId,{operationId:'publish',expectedRevision:0});const v1=await first.exportConfiguration(made.workId,published.result.versionId);
  document.model.recommendations.story.parameters.values.temperature.value=1.2;await first.save(made.workId,{operationId:'save',expectedRevision:1,document});assert.deepEqual(await first.exportConfiguration(made.workId,published.result.versionId),v1);
  assert.deepEqual(await f.store.readSession(sessionId),state);assert.equal(f.calls.filter(c=>c.method==='POST').length,3);
  const copied=await first.copy(made.workId,{operationId:'copy',expectedRevision:2,name:'副本'});assert.deepEqual((await first.read(copied.workId)).draft.model.recommendations,document.model.recommendations);
  const payload=await first.exportPackage(made.workId,published.result.versionId);const serialized=JSON.stringify(payload);for(const word of ['capabilityRevision','selectionRevision','resolution','wireParameters','serviceToken','authorization','quota'])assert.equal(serialized.includes('"'+word+'"'),false);
  const second=await AuthoringStore.open(join(f.directory,'import'));t.after(()=>second.close());const inspected=await second.inspectImport(payload);assert.equal(inspected.valid,true);
  const imported=await second.importPackage({operationId:'import',expectedRevision:null,workId:null,payload,expectedImportHash:inspected.payloadHash});assert.deepEqual((await second.read(imported.workId)).draft,v1.document);
  const bad=clone(payload);bad.configuration.document.model.recommendations.story.serviceToken='forbidden';assert.equal((await second.inspectImport(bad)).valid,false);assert.deepEqual((await second.read(imported.workId)).draft,v1.document);
  const ambiguous=clone(document);ambiguous.model.parameters={maxOutputTokens:4096};assert.throws(()=>validateDocument(ambiguous),code('PARAMETER_RECOMMENDATIONS_AMBIGUOUS'));
});

test('empty stop list is accepted only when the current capability permits it',async t=>{
  const f=await fixture(t),p=profile('story',{stop:{mode:'explicit',value:[]}});
  assert.deepEqual(validateParameterProfile(p),p);let check=await f.store.inspect(p,selection(f.catalog()));assert.equal(check.valid,false);
  f.policies['route-a'].fields.stop.minItems=0;check=await f.store.inspect(p,selection(f.catalog()));assert.equal(check.valid,true);assert.deepEqual(check.resolution.wireParameters.stop,[]);
});

test('fixed parameters cannot be changed; unsupported default is not silently removed',async t=>{
  const f=await fixture(t);delete f.policies['route-a'].fields.topK;
  const check=await f.store.inspect(profile('story',{topK:{mode:'default'}}),selection(f.catalog()));assert.ok(check.issues.some(i=>i.field==='topK'));
  f.policies['route-a']={kind:'fixed',fields:{maxOutputTokens:field('integer',{minimum:1,maximum:16384,required:true,wireName:'max_tokens'},4096)},exclusive:[],requires:[],lessThan:[],purposes:['story']};
  const fixed=await f.store.inspect(profile('story',{maxOutputTokens:{mode:'explicit',value:8192}}),selection(f.catalog()));assert.ok(fixed.issues.some(i=>i.code==='PARAMETER_RANGE'));
  const wrong=await f.store.inspect(profile('memory',{}),selection(f.catalog()));assert.ok(wrong.issues.some(i=>i.code==='PARAMETER_PURPOSE_UNSUPPORTED'));
});

test('read/recovery in a fresh Node process does not preflight or dispatch',async t=>{
  const f=await fixture(t);await f.store.configure(f.config());const expected=await f.store.readSession(sessionId),before=f.calls.length;await f.store.close();
  const script=`import {ParameterSettingsStore} from ${JSON.stringify(new URL('./generation-settings.mjs',import.meta.url).href)};import {ControlPlaneModelControl} from ${JSON.stringify(new URL('./model-control.mjs',import.meta.url).href)};const store=await ParameterSettingsStore.open({directory:process.argv[1],control:new ControlPlaneModelControl({origin:'http://127.0.0.1:1',serviceToken:'x'.repeat(40)})});console.log(JSON.stringify(await store.readSession(process.argv[2])));await store.close();`;
  const actual=JSON.parse(execFileSync(process.execPath,['--input-type=module','-e',script,join(f.directory,'settings'),sessionId],{encoding:'utf8',windowsHide:true}));assert.deepEqual(actual,expected);assert.equal(f.calls.length,before);
});

test('full operation history is rejected before preflight and never auto-pruned',async t=>{
  const f=await fixture(t);await f.store.configure(f.config('story',0,0,{profile:null,selection:null}));
  const path=join(f.directory,'settings','records','session-'+sha256('generation-parameters:'+sessionId)+'.json'),envelope=JSON.parse(await readFile(path,'utf8')),data=envelope.data;
  data.operations=Array.from({length:1000},(_,i)=>({operationId:'reset-'+i,requestHash:sha256('boundary-'+i),revision:i+1,result:{purpose:'story',purposeRevision:i+1,configuration:null}}));data.revision=1000;data.current.story={revision:1000,configuration:null};envelope.sha256=hash(data);await writeFile(path,JSON.stringify(envelope));
  await denied(()=>f.store.configure(f.config('story',1000,1000)),'PARAMETER_OPERATION_CAPACITY');assert.equal((await f.store.readSession(sessionId)).operations.length,1000);assert.equal(f.calls.length,0);
});

test('100 presets is a hard capacity; update retains identity and old revisions',async t=>{
  const f=await fixture(t);await f.store.savePreset(f.preset());const path=join(f.directory,'settings','records','presets.json'),envelope=JSON.parse(await readFile(path,'utf8')),sample=envelope.data.current[0];
  envelope.data.current=Array.from({length:100},(_,i)=>({...clone(sample),id:'preset-'+i,name:'预设 '+i}));envelope.data.operations=envelope.data.current.map((p,i)=>({operationId:'save-'+i,requestHash:sha256('preset-boundary-'+i),revision:i+1,result:p}));envelope.data.revision=100;envelope.sha256=hash(envelope.data);await writeFile(path,JSON.stringify(envelope));
  const calls=f.calls.length;await denied(()=>f.store.savePreset(f.preset({operationId:'full',expectedRevision:100})),'PARAMETER_PRESET_CAPACITY');assert.equal(f.calls.length,calls);
  await f.store.savePreset(f.preset({operationId:'update',expectedRevision:100,id:'preset-0',expectedPresetRevision:1,name:'更新',profile:profile('story',{temperature:{mode:'explicit',value:.6}})}));const s=await f.store.listPresets();assert.equal(s.current.length,100);assert.equal(s.current[0].revision,2);assert.equal(s.operations[0].result.configuration.profile.parameters.values.temperature.value,.7);
});

test('capability/selection drift and malformed public fields fail closed',async t=>{
  const f=await fixture(t),s=selection(f.catalog());await denied(()=>f.store.inspect(profile(),{...s,capabilityRevision:sha256('outdated')}),'MODEL_CAPABILITY_CHANGED');await denied(()=>f.store.inspect(profile(),{...s,selectionRevision:sha256('outdated')}),'MODEL_SELECTION_CHANGED');
  f.mutateCatalog=c=>c.models[0].parameterPolicy.fields.temperature.wireName='arbitrary';await denied(()=>f.store.inspect(profile(),s),'MODEL_CONTROL_RESPONSE_INVALID');assert.equal((await f.store.readSession(sessionId)).revision,0);
});

test('large trusted defaults remain valid; explicit request over 16 KiB is blocked before POST',async t=>{
  const f=await fixture(t),stops=Array.from({length:16},(_,i)=>'界'.repeat(1020)+i);
  f.policies['route-a'].fields.stop={...field('text-list',{minItems:0,maxItems:16,maxItemLength:1024,wireName:'stop'}),default:{mode:'value',value:stops}};
  const checked=await f.store.inspect(profile(),selection(f.catalog()));assert.equal(checked.valid,true);assert.deepEqual(checked.resolution.wireParameters.stop,stops);
  const before=f.calls.filter(c=>c.method==='POST').length;
  await denied(()=>f.store.inspect(profile('story',{stop:{mode:'explicit',value:stops}}),selection(f.catalog())),'PARAMETER_CONFIG_CAPACITY');assert.equal(f.calls.filter(c=>c.method==='POST').length,before);
});


test('legacy UUID and generic authored session IDs share storage without preset-key collision',async t=>{
  const f=await fixture(t);await f.store.savePreset(f.preset());
  for(const id of ['88900dd0-e723-41cd-ad6f-284b4197660f','presets',sessionId]){
    await f.store.configure(f.config('story',0,0,{sessionId:id,operationId:'same-local-operation',profile:null,selection:null}));
    assert.equal((await f.store.readSession(id)).targetId,id);
    assert.equal((await f.store.recover('session',id,'same-local-operation')).status,'committed');
  }
  assert.equal((await f.store.listPresets()).current.length,1);
  assert.equal((await readdir(join(f.directory,'settings','records'))).length,4);
  for(const id of ['../other','x/y','x\\y','x'.repeat(97),''])assert.throws(()=>f.store.readSession(id),code('PARAMETER_SESSION_INVALID'));
});

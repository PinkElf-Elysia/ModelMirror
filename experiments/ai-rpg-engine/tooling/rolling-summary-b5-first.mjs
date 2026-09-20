// Explicit first prepare/send. No loop, retry, certification, or implicit summary dispatch.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {canonical,sha} from '../plugins/catalog.mjs';
import {summaryRuntime} from '../studio/summary-runtime.mjs';
import {assembleSummaryRequest} from '../studio/summary-assembly.mjs';
import {SUMMARY_INSTRUCTION_HASH} from '../studio/summary-state.mjs';
import {assemble} from '../card-replica/lib/assembly.mjs';
const root=new URL('../.rpg04-work/rolling-summary-b5/',import.meta.url),origin='http://127.0.0.1:18473';
const read=async name=>JSON.parse(await readFile(new URL(name,root),'utf8'));
async function save(name,data){const f=await open(new URL(name,root),'wx');try{await f.writeFile(JSON.stringify(data,null,2)+'\n');await f.sync();}finally{await f.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,error:v.error}));return v;}
const action=process.argv[2];assert.ok(['prepare','send'].includes(action));
assert.equal(summaryRuntime.hash,'aaca2ad2796eaec07235d85b989358cc3fd64fdf3745a57e4e512587b67d14b9');await summaryRuntime.verify();
const auth=await read('AUTHORIZATION.json');assert.equal(auth.limit,4);assert.equal(auth.summaryModel,'google/gemini-3.8-flash');
if(action==='prepare'){
 const input={characterText:'姓名：林遥（虚构验收角色）\n性别：女性\n开局24岁，在城市图书馆做古籍修复学徒。',world:'表世界',mode:'real',params:{max_tokens:16384}};
 let s;try{const prior=await read('session-created.json');s=await api('earth/api/sessions/'+prior.id);assert.equal(s.turns.length,0);}catch(e){if(e.code!=='ENOENT')throw e;s=await api('earth/api/sessions',input);await save('session-created.json',{id:s.id});}
 for(const pluginId of ['rpg.model-selector','rpg.rolling-summary']){
  let c=await api('api/plugins'),p=c.plugins.find(x=>x.id===pluginId);assert.ok(p);
  const binding=()=>({pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision});
  if(!p.installed){await api('api/plugins/'+pluginId+'/install',{...binding(),operationId:'m2-install-'+pluginId.replaceAll('.','-')});c=await api('api/plugins');p=c.plugins.find(x=>x.id===pluginId);}
  await api('earth/api/sessions/'+s.id+'/plugins/'+pluginId+'/enable',{...binding(),operationId:'m2-enable-'+pluginId.replaceAll('.','-'),sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});
  s=await api('earth/api/sessions/'+s.id);
 }
 const choices=await api('earth/api/sessions/'+s.id+'/model-catalog'),m=choices.models.find(x=>x.model===auth.storyModel);assert.ok(m?.available);
 s=(await api('earth/api/sessions/'+s.id+'/model-selection',{operationId:'m2-story-gemini',expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:m.selectionId,catalogRevision:m.selectionRevision})).session;
 const base='earth/api/sessions/'+s.id+'/rolling-summary';let h=await api(base);
 const options=await api(base+'/catalog'),model=options.models.find(x=>x.model===auth.summaryModel);assert.ok(model?.available);
 s=(await api(base+'/settings',{operationId:'m2-summary-gemini-before',expectedSessionRevision:h.sessionRevision,expectedSummaryRevision:h.summaryRevision,timing:'before',selectionId:model.selectionId,catalogRevision:model.selectionRevision,text:null})).session;
 h=await api(base);assert.equal(h.enabled,true);assert.equal(h.effectivePolicy.mode,'rolling-summary');assert.equal(h.config.timing,'before');assert.equal(h.versions.length,0);
 const disk=await read('data/earth/'+s.id+'.json'),query='请开始。',built=assembleSummaryRequest(disk,query,h.effectivePolicy);
 assert.equal(disk.turns.length,0);assert.deepEqual(built.messages,assemble({characterText:input.characterText,world:input.world,input:query,history:[]}).messages);assert.equal(built.messages.length,2);assert.deepEqual(built.triggered,[]);
 const status=await api('earth/api/status');assert.equal(status.budget.used,0);assert.equal(status.budget.limit,4);
 const f={at:new Date().toISOString(),sessionId:s.id,characterText:input.characterText,characterHash:sha(input.characterText),storyModel:s.modelSelection.current,summaryModel:h.config.model,summaryTiming:h.config.timing,summaryInstructionHash:SUMMARY_INSTRUCTION_HASH,runtimeHash:summaryRuntime.hash,messages:built.messages,messagesHash:sha(canonical(built.messages)),contextPolicy:built.contextPolicy,send:{operationId:'m2-b5-story-1',expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input:query}};
 await save('first-input-freeze.json',f);console.log(JSON.stringify({prepared:true,sessionId:s.id,storyModel:f.storyModel.model,summaryModel:f.summaryModel.model,parameters:f.storyModel.parameters,messages:2,messagesHash:f.messagesHash,realCalls:0}));
}else{
 const f=await read('first-input-freeze.json'),s=await api('earth/api/sessions/'+f.sessionId),disk=await read('data/earth/'+f.sessionId+'.json'),h=await api('earth/api/sessions/'+f.sessionId+'/rolling-summary');
 assert.equal(s.turns.length,0);assert.equal(s.revision,f.send.expectedSessionRevision);assert.equal(h.effectivePolicy.revision,f.send.expectedContextPolicyRevision);assert.deepEqual(s.modelSelection.current,f.storyModel);assert.deepEqual(h.config.model,f.summaryModel);assert.deepEqual(assembleSummaryRequest(disk,f.send.input,h.effectivePolicy).messages,f.messages);
 const status=await api('earth/api/status');assert.equal(status.budget.used,0);assert.equal(status.budget.reserved,0);
 await save('first-generation-invocation.json',{at:new Date().toISOString(),operationId:f.send.operationId,sessionId:f.sessionId,messagesHash:f.messagesHash,maximumDispatches:1,noRetry:true,authorization:auth.id});
 const result=await api('earth/api/sessions/'+f.sessionId+'/send',f.send);await save('first-host-result.json',result);
 console.log(JSON.stringify({turns:result.turns?.length,actualModel:result.turns?.at(-1)?.model?.actualModel,rawLength:result.turns?.at(-1)?.raw?.length,operation:result.operation?.status,error:result.operation?.error,budget:(await api('earth/api/status')).budget}));
}

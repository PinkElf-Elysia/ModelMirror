// Explicit M3 first group: prepare without calls; send once. No retries.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {canonical,sha} from '../plugins/catalog.mjs';
import {memoryRuntime} from '../studio/memory-runtime.mjs';
import {assembleMemoryRequest} from '../studio/memory-assembly.mjs';
import {MEMORY_INSTRUCTION_HASH} from '../studio/memory-task-state.mjs';
import {assemble} from '../card-replica/lib/assembly.mjs';
const root=new URL('../.rpg04-work/memory-palace-b5/',import.meta.url),origin='http://127.0.0.1:18497';
const read=async n=>JSON.parse(await readFile(new URL(n,root),'utf8'));
async function save(n,v){const f=await open(new URL(n,root),'wx');try{await f.writeFile(JSON.stringify(v,null,2)+'\n');await f.sync();}finally{await f.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,{...(body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({httpStatus:r.status,error:v.error}));return v;}
const action=process.argv[2],a=await read('AUTHORIZATION.json');assert.equal(a.generationLimit,4);assert.equal(a.memoryModel,'google/gemini-3.8-flash');await memoryRuntime.verify();
if(action==='prepare'){
 // Actual B4 source identity is checked separately before preparation.
 const input={characterText:'姓名：林遥（虚构验收角色）\n性别：女性\n开局24岁，生活在中国上海，在城市图书馆做古籍修复学徒。',world:'表世界',mode:'real',params:{max_tokens:16384}};
 let s;try{const prior=await read('session-created.json');s=await api('earth/api/sessions/'+prior.id);assert.equal(s.turns.length,0);}catch(e){if(e.code!=='ENOENT')throw e;s=await api('earth/api/sessions',input);await save('session-created.json',{id:s.id});}
 for(const pluginId of ['rpg.model-selector','rpg.memory-palace']){
  let c=await api('api/plugins'),p=c.plugins.find(x=>x.id===pluginId);assert.ok(p);
  const binding=()=>({pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision});
  if(!p.installed){await api('api/plugins/'+pluginId+'/install',{...binding(),operationId:'m3-install-'+pluginId.replaceAll('.','-')});c=await api('api/plugins');p=c.plugins.find(x=>x.id===pluginId);}
  await api('earth/api/sessions/'+s.id+'/plugins/'+pluginId+'/enable',{...binding(),operationId:'m3-enable-'+pluginId.replaceAll('.','-'),sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});s=await api('earth/api/sessions/'+s.id);
 }
 let m=(await api('earth/api/sessions/'+s.id+'/model-catalog')).models.find(x=>x.model===a.storyModel);assert.ok(m?.available);
 s=(await api('earth/api/sessions/'+s.id+'/model-selection',{operationId:'m3-story-gemini',expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:m.selectionId,catalogRevision:m.selectionRevision})).session;
 const base='earth/api/sessions/'+s.id+'/memory-palace';let h=await api(base);m=(await api(base+'/catalog')).models.find(x=>x.model===a.memoryModel);assert.ok(m?.available);
 s=(await api(base+'/settings',{operationId:'m3-memory-gemini',expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,selectionId:m.selectionId,catalogRevision:m.selectionRevision})).session;
 h=await api(base);assert.ok(h.enabled);assert.equal(h.maxCalls,2);assert.equal(h.entries.length,0);assert.equal((await api('earth/api/sessions/'+s.id+'/rolling-summary')).enabled,false);
 const disk=await read('data/earth/'+s.id+'.json'),query='今天是周五。我和朋友阿宁约好，明天下午三点在图书馆门口见面，她会带来祖父留下的一封旧信。现在我刚到修复室，准备先完成手头的工作。请从这里开始。',built=assembleMemoryRequest(disk,query,h.effectivePolicy);
 assert.deepEqual(built.messages,assemble({characterText:input.characterText,world:input.world,input:query,history:[]}).messages);assert.equal(built.messages.length,2);
 const status=await api('earth/api/status');assert.equal(status.budget.used,0);assert.equal(status.budget.limit,4);
 const f={at:new Date().toISOString(),sessionId:s.id,characterText:input.characterText,characterHash:sha(input.characterText),runtimeHash:memoryRuntime.hash,memoryInstructionHash:MEMORY_INSTRUCTION_HASH,storyModel:s.modelSelection.current,memoryModel:h.config.model,contextPolicy:h.effectivePolicy,messages:built.messages,messagesHash:sha(canonical(built.messages)),maxCalls:2,send:{operationId:'m3-b5-group-1',expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input:query}};
 await save('first-input-freeze.json',f);console.log(JSON.stringify({prepared:true,sessionId:s.id,messages:2,messagesHash:f.messagesHash,model:a.memoryModel,parameters:f.memoryModel.parameters,realGenerationCalls:0}));
}else if(action==='send'){
 const f=await read('first-input-freeze.json'),disk=await read('data/earth/'+f.sessionId+'.json'),h=await api('earth/api/sessions/'+f.sessionId+'/memory-palace'),status=await api('earth/api/status');
 assert.equal(memoryRuntime.hash,f.runtimeHash);assert.equal(disk.turns.length,0);assert.equal(h.sessionRevision,f.send.expectedSessionRevision);assert.equal(h.memoryRevision,f.send.expectedMemoryRevision);assert.equal(h.effectivePolicy.revision,f.send.expectedContextPolicyRevision);assert.deepEqual(h.config.model,f.memoryModel);assert.deepEqual(assembleMemoryRequest(disk,f.send.input,h.effectivePolicy).messages,f.messages);assert.equal(status.budget.used,0);assert.equal(status.budget.reserved,0);
 await save('first-generation-invocation.json',{at:new Date().toISOString(),operationId:f.send.operationId,sessionId:f.sessionId,messagesHash:f.messagesHash,maximumDispatches:2,noRetry:true,authorization:a.id});
 const result=await api('earth/api/sessions/'+f.sessionId+'/send',f.send);await save('first-host-result.json',result);console.log(JSON.stringify({turns:result.turns.length,operation:result.requests[f.send.operationId],backgroundMemory:'inspect original operation; never resend'}));
}else throw Error('UNSUPPORTED_ACTION');

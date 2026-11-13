import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {models} from './provider.mjs';
import {GOD_REAL_PARAMETERS} from '../god-card/lib/real-profile.mjs';
import {loadOutputSuffix} from '../god-card/lib/output-suffix.mjs';
import {beforeFreeOpening} from './free-opening-runtime-compat.mjs';
import {modelRuntime} from './model-runtime.mjs';
import {historyRuntime,historyRuntimeStatus} from './history-runtime.mjs';
import {summaryRuntime,summaryRuntimeStatus} from './summary-runtime.mjs';
import {memoryRuntime,memoryRuntimeStatus} from './memory-runtime.mjs';
import {godPluginProfile} from '../god-card/lib/plugin-runtime.mjs';
import {setupHash} from './runtime-binding.mjs';

test('six normal chats create without a bound character; only the edited first message dispatches',async t=>{
 const wires=[];
 const control={baseURL:'http://127.0.0.1:1/',serviceToken:'fixture-free-opening-not-a-credential',enabled:true,limit:100,
  fetcher:async(url,options)=>{
   if(options.method==='GET')return Response.json({models:[{model:models.earth.model,name:'Fixture',selectionId:'fixture',selectionRevision:'v1',available:true,parameters:new URL(url).searchParams.get('profile')==='god-long-v1'?GOD_REAL_PARAMETERS:models.earth.parameters}]});
   const b=JSON.parse(options.body);wires.push(b);
   const raw='时间:清晨|地点:城门|角色:旅人|情节:踏入城门。',sse='fixture response';
   return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:models.earth.model,actualModel:models.earth.model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
  }};
 const host=await start({port:0,directory:await mkdtemp(join(tmpdir(),'rpg-free-opening-')),memoryPalace:true,autoSummaryV2:true,godCard:true,godPlugins:true,limit:100,modelControl:control,godControl:control,xingjianControl:control,archiveControls:{ancient:control,cultivation:control,'lord-of-mysteries':control}});
 t.after(async()=>{await host.plugins.close();await new Promise(resolve=>host.server.close(resolve));});
 const origin='http://127.0.0.1:'+host.server.address().port;
 async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),v=await r.json();assert(r.ok,path+': '+JSON.stringify(v));return v;}
 for(const card of ['earth','god','xingjian','ancient','cultivation','lord-of-mysteries'])await t.test(card,async()=>{
  const base=card+(['ancient','cultivation','lord-of-mysteries'].includes(card)?'/chat/api/':'/api/');
  const registry=['earth','god'].includes(card)?'api/':base;
  const count=wires.length;
  const session=await api(base+'sessions',card==='earth'?{characterText:'',world:'表世界',mode:'real',params:{}}:card==='god'?{setup:null,mode:'real'}:{opening:''}),path=base+'sessions/'+session.id;
  assert.equal(session.characterText,'');assert.equal(session.turns.length,0);assert.equal(session.runtime.compatible,true);assert.equal(wires.length,count,'entry never calls a model');
  async function enable(pluginId){
   let c=await api(registry+'plugins');const p=c.plugins.find(p=>p.id===pluginId),binding={pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256};
   if(!p.installed){await api(registry+'plugins/'+pluginId+'/install',{...binding,operationId:randomUUID(),expectedRegistryRevision:c.revision});c=await api(registry+'plugins');}
   const s=await api(path);await api(path+'/plugins/'+pluginId+'/enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:c.revision,sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});
  }
  if(card==='earth'){
   await enable('rpg.model-selector');const s=await api(path);
   await api(path+'/model-selection',{operationId:randomUUID(),expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:'fixture',catalogRevision:'v1'});
  }
  async function send(input){const m=await api(path+'/memory-palace');const operation={operationId:randomUUID(),expectedSessionRevision:m.sessionRevision,expectedMemoryRevision:m.memoryRevision,expectedContextPolicyRevision:m.effectivePolicy.revision,input};const v=await api(path+'/send',operation);assert.ok(['complete','pending'].includes(v.operation.status),JSON.stringify(v.operation));assert.equal(v.turns.at(-1).input,input);return operation;}
  const edited='姓名：自行修改的角色\n我从城门开始，而不是原档案。';
  const first=await send(edited),firstWire=wires.filter(w=>w.sessionId===session.id)[0];
  assert.equal(firstWire.messages.length,2);assert.equal(firstWire.messages[1].content,card==='god'?edited+'\n\n'+loadOutputSuffix().text:edited);
  const calls=wires.length;await api(path+'/send',first);assert.equal(wires.length,calls,'same request is not dispatched again');
  await enable('rpg.history-window');const h=await api(path+'/history-window');
  await api(path+'/history-window',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedConfigRevision:h.configRevision,turns:1,includeInitialCharacter:true});
  await send('第二步');await send('第三步');
  const recent=wires.filter(w=>w.sessionId===session.id).at(-1);
  assert.equal(recent.messages.length,4);assert(!recent.messages.some(m=>m.content.includes('开局角色资料（原始快照）')));
  const saved=await api(path);assert.equal(saved.characterText,'');assert.equal(saved.turns[0].input,edited);assert.equal(saved.turns.length,3);
  await enable('rpg.rolling-summary');await send('开启总结后继续');
  let status=await api(path+'/rolling-summary');
  for(let n=0;(status.busy||!status.v2.turns.length)&&n<100;n++){await new Promise(r=>setTimeout(r,25));status=await api(path+'/rolling-summary');}
  assert.equal(status.enabled,true);assert.equal(status.v2.turns.length,1);assert.equal((await api(path)).runtime.compatible,true);
 });
});

test('previous opening bindings are admitted exactly; unrelated prompt and setup drift still fail',()=>{
 const model=beforeFreeOpening(modelRuntime.descriptor);
 const history={...beforeFreeOpening(historyRuntime.descriptor),baseRuntimeHash:sha(canonical(model))};
 const summary={...beforeFreeOpening(summaryRuntime.descriptor),baseRuntimeHash:sha(canonical(history))};
 const memory={...beforeFreeOpening(memoryRuntime.descriptor),baseRuntimeHash:sha(canonical(summary))};
 for(const [descriptor,status] of [[history,historyRuntimeStatus],[summary,summaryRuntimeStatus],[memory,memoryRuntimeStatus],[beforeFreeOpening(godPluginProfile.runtime.descriptor),godPluginProfile.status]]){
  const s={cardId:descriptor.cardId,characterText:'旧资料完整保留',world:descriptor.cardId==='god'?null:'表世界',params:{},mode:'offline'};
  s.runtime={format:descriptor.format,descriptor,hash:sha(canonical(descriptor)),setupHash:setupHash(s)};
  const before=canonical(s);assert.equal(status(s).compatible,true);assert.equal(canonical(s),before);
  const other=structuredClone(s);other.runtime.descriptor.hostArtifacts['plugins/history-window.mjs']='0'.repeat(64);other.runtime.hash=sha(canonical(other.runtime.descriptor));assert.equal(status(other).compatible,false);
  assert.equal(status({...s,characterText:'被篡改'}).compatible,false);
 }
});

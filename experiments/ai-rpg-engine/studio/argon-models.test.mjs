import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {choice} from './card-model-policy.mjs';
import {createGodRealProfile,GOD_REAL_PARAMETERS} from '../god-card/lib/real-profile.mjs';
import {createArchiveHost} from '../xingjian-card/host.mjs';
const ids=['google/gemini-3.8-flash','google/gemini-4-argon'];
const entry=(model,index,parameters)=>({model,name:model,selectionId:'model-'+index,selectionRevision:'catalog-v2',available:true,parameters});

test('six card model selectors share one unchanged 100-call pool across Gemini versions',async t=>{
 const wires=[],parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
 const control={baseURL:'http://127.0.0.1:1/',serviceToken:'synthetic-argon-test-not-a-credential',enabled:true,limit:100,
  fetcher:async(url,options)=>{
   const params=new URL(url).searchParams.get('profile')==='god-long-v1'?GOD_REAL_PARAMETERS:parameters;
   const models=ids.map((id,i)=>entry(id,i,params));
   if(options.method==='GET')return Response.json({models});
   const b=JSON.parse(options.body),selected=models.find(m=>m.selectionId===b.selectionId);wires.push(b);
   assert(selected);assert.deepEqual(b.parameters,params);
   const raw='清晨，旅人推开门。',sse='synthetic complete response';
   return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:selected.model,actualModel:selected.model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
  }};
 const host=await start({port:0,directory:await mkdtemp(join(tmpdir(),'rpg-argon-')),memoryPalace:true,autoSummaryV2:true,godCard:true,godPlugins:true,limit:100,modelControl:control,godControl:control,xingjianControl:control,archiveControls:{ancient:control,cultivation:control,'lord-of-mysteries':control}});
 t.after(async()=>{await host.plugins.close();await new Promise(r=>host.server.close(r));});
 const origin='http://127.0.0.1:'+host.server.address().port;
 async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),v=await r.json();assert(r.ok,path+': '+JSON.stringify(v));return v;}
 for(const card of ['earth','god','xingjian','ancient','cultivation','lord-of-mysteries'])await t.test(card,async()=>{
  const base=card+(['ancient','cultivation','lord-of-mysteries'].includes(card)?'/chat/api/':'/api/'),registry=['earth','god'].includes(card)?'api/':base;
  const before=wires.length,s=await api(base+'sessions',card==='earth'?{characterText:'',world:'表世界',mode:'real',params:{}}:card==='god'?{setup:null,mode:'real'}:{opening:''}),path=base+'sessions/'+s.id;
  let catalog=await api(registry+'plugins');const plugin=catalog.plugins.find(p=>p.id==='rpg.model-selector'),binding={pluginId:plugin.id,version:plugin.version,artifactSha256:plugin.artifactSha256,manifestSha256:plugin.manifestSha256};
  if(!plugin.installed){await api(registry+'plugins/'+plugin.id+'/install',{...binding,operationId:randomUUID(),expectedRegistryRevision:catalog.revision});catalog=await api(registry+'plugins');}
  await api(path+'/plugins/'+plugin.id+'/enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:catalog.revision,sessionId:s.id,expectedSessionRevision:s.revision,permissions:plugin.permissions});
  assert.deepEqual((await api(path+'/model-catalog')).models.map(m=>m.model),ids);
  for(let i=0;i<ids.length;i++){
   const current=await api(path);
   await api(path+'/model-selection',{operationId:randomUUID(),expectedSessionRevision:current.revision,selectionRevision:current.modelSelection.revision,selectionId:'model-'+i,catalogRevision:'catalog-v2'});
   assert.equal(wires.length,before+i,'selection never dispatches');
   const m=await api(path+'/memory-palace');
   const budget=(await api(base+'status')).budget;assert.equal(budget.limit,100);assert.equal(budget.remaining,100-i);
   const result=await api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:m.sessionRevision,expectedMemoryRevision:m.memoryRevision,expectedContextPolicyRevision:m.effectivePolicy.revision,input:'仅用于模型切换验收 '+i});
   assert.equal(result.operation.status,'complete');assert.equal(result.turns.at(-1).model.actualModel,ids[i]);
  }
  assert.equal(wires.length,before+2);assert.equal((await api(base+'status')).budget.remaining,98);
  assert.equal((await api(path)).runtime.compatible,true);
 });
});

test('reviewed real runtimes survive catalog revision refresh, but route, prompt and setup tampering fail',async t=>{
 const current=entry(ids[0],0,GOD_REAL_PARAMETERS),god=await createGodRealProfile(current);
 const dir=await mkdtemp(join(tmpdir(),'rpg-argon-binding-'));
 const archive=await createArchiveHost(dir,{baseURL:'http://127.0.0.1:1/',serviceToken:'synthetic-argon-compat-not-a-credential',enabled:true,limit:100,fetcher:async()=>Response.json({models:[entry(ids[0],0,{temperature:0.7,top_p:0.8,max_tokens:16384})]})},{cardId:'lord-of-mysteries',name:'fixture',system:'frozen author text'});
 t.after(()=>archive.plugins.close());
 const saved=await archive.store.create({opening:''});
 const godSession={cardId:'god',mode:'real',characterText:'',world:null,params:{}};godSession.runtime=god.bind(godSession);
 for(const [session,status,oldArtifact] of [[saved,s=>archive.store.status(s),'archive'],[godSession,s=>god.status(s),'god']]){
  const previous=structuredClone(session),d=previous.runtime.descriptor,bound=oldArtifact==='god'?d.models[0]:d.model;
  const {configHash,...values}=bound,oldChoice=choice({...values,selectionRevision:'catalog-v1'});
  if(oldArtifact==='god'){
   d.models=[oldChoice];d.hostArtifacts['god-card/lib/real-profile.mjs']='afbad6f48c1d1a851ec28f35dd490793041e3762376d8646c1c4669444cf1614';
   d.hostArtifacts['god-card/lib/real-host.mjs']='e49a9aab8705e308f9b57ba98baa2364580dad6968df2f7ec5043e164a6e641f';
  }else{d.model=oldChoice;d.adapterHash='1f10675e1cad2945b803c116965892274a9af494c1929623ab38a006a425a6d4';}
  previous.runtime.hash=sha(canonical(d));const bytes=canonical(previous);assert.equal(status(previous).compatible,true);assert.equal(canonical(previous),bytes);
  assert.equal(status({...previous,characterText:'tampered'}).compatible,false);
  const changed=structuredClone(previous),route=choice({...values,selectionId:'different-route'});
  if(oldArtifact==='god')changed.runtime.descriptor.models=[route];else changed.runtime.descriptor.model=route;
  changed.runtime.hash=sha(canonical(changed.runtime.descriptor));assert.equal(status(changed).compatible,false);
  const prompt=structuredClone(previous);if(oldArtifact==='god')prompt.runtime.descriptor.author='changed';else prompt.runtime.descriptor.systemHash='0'.repeat(64);
  prompt.runtime.hash=sha(canonical(prompt.runtime.descriptor));assert.equal(status(prompt).compatible,false);
 }
});

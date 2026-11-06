import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {start} from './server.mjs';
import {newSetup} from '../god-card/lib/setup.mjs';

test('all six normal chat hosts authorize summary v2 in place and save to the same session',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'normal-summary-'));
 const {server}=await start({port:0,directory,memoryPalace:true,autoSummaryV2:true,godCard:true,godPlugins:true});
 t.after(async()=>{await new Promise(resolve=>server.close(resolve));await rm(directory,{recursive:true,force:true});});
 const origin='http://127.0.0.1:'+server.address().port;
 const api=async(path,body)=>{
  const response=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();
  assert.ok(response.ok,path+' '+response.status+' '+data.error);
  return data;
 };
 const godSetup=newSetup();godSetup.user.姓名='虚构神明';godSetup.opening='初临钟楼';
 const opening='姓名：验证角色\n在旧钟楼观察雾气。';
 const cards=[['earth',{characterText:opening,world:'表世界',params:{},mode:'offline'}],
  ['god',{setup:godSetup,mode:'offline'}],...['xingjian','ancient/chat','cultivation/chat','lord-of-mysteries/chat'].map(id=>[id,{opening}])];
 const pluginId='rpg.rolling-summary';
 for(const [card,configuration] of cards)await t.test(card,async()=>{
  const base=card+'/api/',registry=['earth','god'].includes(card)?'api/':base;
  const session=await api(base+'sessions',configuration),path=base+'sessions/'+session.id;
  assert.equal(session.runtime.compatible,true);
  const read=()=>api(path+'/rolling-summary');
  const initial=await read();assert.equal(initial.enabled,false);assert.deepEqual(initial.v2.turns,[]);
  async function plugin(action){
   const catalog=await api(registry+'plugins'),item=catalog.plugins.find(p=>p.id===pluginId);
   return api((action==='install'?registry:path+'/')+'plugins/'+pluginId+'/'+action,{
    operationId:randomUUID(),pluginId,version:item.version,artifactSha256:item.artifactSha256,manifestSha256:item.manifestSha256,expectedRegistryRevision:catalog.revision,
    ...(action==='install'?{}:{sessionId:session.id,expectedSessionRevision:(await api(path)).revision}),...(action==='enable'?{permissions:item.permissions}:{})
   });
  }
  if(!(await api(registry+'plugins')).plugins.find(p=>p.id===pluginId).installed)await plugin('install');
  await plugin('enable');
  const status=await read();assert.equal(status.enabled,true);assert.equal(status.config.model,null);
  assert.equal((await api(path+'/rolling-summary/catalog')).models.some(model=>model.available),true);
  const memory=await api(path+'/memory-palace');assert.equal(memory.maxCalls,2);
  const send={operationId:randomUUID(),expectedSessionRevision:memory.sessionRevision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input:session.characterText};
  const sent=await api(path+'/send',send);assert.equal(sent.turns.length,1,JSON.stringify(sent.operation));assert.equal(sent.turns[0].contextPolicy.policy.summaryVersion,2);
  let settled=await read();
  for(let n=0;(settled.busy||!settled.v2.turns[0]?.small)&&n<100;n++){await delay(35);settled=await read();}
  assert.equal(settled.busy,false);assert.equal(settled.v2.turns[0]?.id,1,JSON.stringify(settled.taskState));assert.ok(settled.v2.turns[0].small);
  const edited=await api(path+'/rolling-summary/v2-edit',{operationId:randomUUID(),expectedSessionRevision:settled.sessionRevision,expectedSummaryRevision:settled.summaryRevision,turn:1,text:'用户修订的虚构小总结'});
  assert.equal(edited.session.id,session.id);assert.equal((await read()).v2.turns[0].small,'用户修订的虚构小总结');
  const current=await read();
  const refreshed=await api(path+'/rolling-summary/v2-regenerate',{operationId:randomUUID(),expectedSessionRevision:current.sessionRevision,expectedContextPolicyRevision:current.effectivePolicy.revision,summaryTurn:1});
  assert.equal(refreshed.operation.status,'complete');assert.equal(refreshed.session.turns.length,1);
  await plugin('disable');const disabled=await read();assert.equal(disabled.enabled,false);assert.equal(disabled.v2.turns.length,1);
 });
});

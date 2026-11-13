import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {assemble} from './assembly.mjs';
import {configFetch,installCustomConfig} from '../plugins/custom-config/host.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {start} from '../studio/server.mjs';
test('controlled fixture preserves the full prompt and history while toggling a common Mod',async t=>{
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{name:'fixture',model,parameters,available:true,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;
  seen.push({base,messages});
  const raw='已在自由港寻找向导。',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const directory=await mkdtemp(join(tmpdir(),'elderland-control-'));
 const host=await start({port:0,directory,archiveControls:{'elderland':{enabled:true,limit:2,baseURL:'http://127.0.0.1:8000',serviceToken:'synthetic-elderland-test-only-credential',fetcher:(u,o)=>configFetch(u,o,fake)}}});
 t.after(()=>new Promise(resolve=>host.server.close(resolve)));
 const origin='http://127.0.0.1:'+host.server.address().port;
 installCustomConfig(host.server,[origin],directory);
 const root='/rpg-app/elderland/chat/api/';
 const post=async(path,body,headers={})=>{const r=await fetch(origin+root+path,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;};
 const catalog=await (await fetch(origin+'/rpg-app/mod-panel/catalog')).json();
 const mod=catalog.items.find(item=>item.id==='common.rationality.v1');
 const session=await post('sessions',{opening:''}),path='sessions/'+session.id;
 assert.equal(seen.length,0,'creation and catalog must never dispatch');
 for(const [i,input] of ['在自由港寻找向导。','确认航船的出发时间。'].entries()){
  const memory=await (await fetch(origin+root+path+'/memory-palace')).json();
  const request={operationId:randomUUID(),expectedSessionRevision:memory.sessionRevision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input};
  const result=await post(path+'/send',i?request:{modIds:[mod.id],request},i?{}:{'X-Rpg-Mod-Panel':'1'});
  assert.equal(result.turns.length,i+1);assert.equal(result.turns.at(-1).raw,'已在自由港寻找向导。');
  const system=assemble({input}).messages[0].content;
  assert.equal(seen[i].base.messages[0].content,system);
  assert.equal(seen[i].messages[0].content,system+(i?'':'\n\n'+mod.text));
  assert.equal(seen[i].messages.at(-1).content,input);
 }
 assert.deepEqual(seen[1].messages.slice(1,-1),[{role:'user',content:'在自由港寻找向导。'},{role:'assistant',content:'已在自由港寻找向导。'}]);
 assert.equal(seen.length,2);
});

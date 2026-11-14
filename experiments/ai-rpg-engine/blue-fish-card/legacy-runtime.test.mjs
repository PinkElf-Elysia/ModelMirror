import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createArchiveHost} from './session-host.mjs';
import {systemPrompt} from './assembly.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
const adapters=['4bc419471a92b7ddaf0838b5d0ba1eb576c5af01a2eb532337f092e1fc95f6df','d27155b04a9759ee66794c841c5415e6d356a35b56d3497605571763ba807f8e'];
const oldTransport={
 'transport/memory-budget.mjs':'f01bb51fa0341c4058575d84aea34bbede45259f875913fa9b76101ae380edaa',
 'transport/controlled-provider.mjs':'6e4ebcf19bc9f18628975bfde672294736ad3ba228d1541ac0b2f0bd9e5b913d',
 'transport/memory-task-provider.mjs':'cfb7499925a8ef0da88f431c6c3647305f0d9869f6a6964018d401d708df679a',
 'transport/offline-store.mjs':'85dacf644a09c89686cce67586e924162c5b91da47151041be82ff69de604048',
};
async function fixture(t){
 const directory=await mkdtemp(join(tmpdir(),'blue-fish-compat-')),seen=[];
 const model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
 const fetcher=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'合成离线模型',available:true,parameters,selectionId:'synthetic-route',selectionRevision:'v1'}]});
  const body=JSON.parse(options.body);seen.push(body);const raw='合成离线回复',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:'synthetic-route',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const open=()=>createArchiveHost(directory,{enabled:true,limit:4,baseURL:'http://127.0.0.1:8000',serviceToken:'synthetic-offline-only-test-credential',fetcher},{cardId:'blue-fish',name:'离线测试',system:systemPrompt(),autoSummaryV2:true});
 let host=await open();t.after(()=>host.plugins.close());
 const call=async(route,method='GET',data={})=>{let result;await host.handle({route,method,data,plugins:host.plugins,send:(code,body)=>{assert.equal(code,200,JSON.stringify(body));result=body;}});return result;};
 return {directory,seen,call,get host(){return host;},async reopen(){await host.plugins.close();host=await open();},async send(s,input){const p=await call('api/sessions/'+s.id+'/memory-palace');return call('api/sessions/'+s.id+'/send','POST',{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:p.memoryRevision,expectedContextPolicyRevision:p.effectivePolicy.revision,input});}};
}
test('historical Blue Fish save reopens byte-for-byte, continues with its original prompt, and new saves use current prompt',async t=>{
 const f=await fixture(t);const legacy=await readFile(new URL('./resources/system-pre-20261113.txt',import.meta.url),'utf8');
 assert.equal(sha(legacy),'885def05b8628dce5428b39dcc5387c2548d082f7dc1635f7c765d5fa810f59a');
 const s=await f.host.store.create({opening:'合成验收角色'});
 s.runtime.descriptor={...s.runtime.descriptor,adapterHash:adapters[0],systemHash:sha(legacy),transportArtifacts:oldTransport};s.runtime.hash=sha(canonical(s.runtime.descriptor));
 await f.host.store.write(s);const file=join(f.directory,s.id+'.json'),before=await readFile(file);
 await f.reopen();assert.equal(f.host.store.status(await f.host.store.read(s.id)).compatible,true);
 await f.call('api/sessions/'+s.id);assert.deepEqual(await readFile(file),before);assert.equal(f.seen.length,0);
 const sent=await f.send(s,'继续旧会话');assert.equal(sent.turns.length,1);assert.equal(f.seen[0].messages[0].content,legacy);
 const continued=await f.host.store.read(s.id);assert.deepEqual(continued.runtime,s.runtime,'sending must not rewrite old runtime binding');
 const fresh=await f.host.store.create({opening:'新会话'});await f.send(fresh,'新会话开局');assert.equal(f.seen[1].messages[0].content,systemPrompt());
});
test('both reviewed current-prompt adapters remain readable and all unrelated runtime/setup changes fail closed',async t=>{
 const f=await fixture(t),original=await f.host.store.create({opening:'离线校验'});
 for(const adapterHash of adapters){const s=structuredClone(original);s.runtime.descriptor.adapterHash=adapterHash;s.runtime.hash=sha(canonical(s.runtime.descriptor));assert.equal(f.host.store.status(s).compatible,true);}
 const legacy=structuredClone(original);legacy.runtime.descriptor={...legacy.runtime.descriptor,adapterHash:adapters[0],systemHash:'885def05b8628dce5428b39dcc5387c2548d082f7dc1635f7c765d5fa810f59a',transportArtifacts:oldTransport};legacy.runtime.hash=sha(canonical(legacy.runtime.descriptor));
 const reject=fn=>{const s=structuredClone(legacy);fn(s);s.runtime.hash=sha(canonical(s.runtime.descriptor));assert.equal(f.host.store.status(s).compatible,false);};
 reject(s=>{s.characterText='篡改角色';});
 reject(s=>{s.runtime.descriptor.systemHash='0'.repeat(64);});
 reject(s=>{s.runtime.descriptor.adapterHash='0'.repeat(64);});
 reject(s=>{s.runtime.descriptor.transportArtifacts['transport/memory-budget.mjs']='0'.repeat(64);});
 reject(s=>{s.runtime.descriptor.transportArtifacts['unknown.mjs']='0'.repeat(64);});
 reject(s=>{s.runtime.descriptor.model.model='unreviewed/model';});
 reject(s=>{s.runtime.descriptor.extra='unknown';});
 assert.equal(f.host.store.status({...legacy,runtime:{...legacy.runtime,hash:'0'.repeat(64)}}).compatible,false);
 assert.equal(f.seen.length,0);
});

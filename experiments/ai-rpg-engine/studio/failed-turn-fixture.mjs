// Synthetic provider, real local HTTP/store/plugin pipeline. No external calls.
import {mkdir,mkdtemp} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
export async function failureFixture(port=0){
 const root=new URL('../.rpg04-work/',import.meta.url);await mkdir(root,{recursive:true});const directory=await mkdtemp(new URL('failed-turn-',root));
 const models=['google/gemini-3.8-flash','google/gemini-4-argon'].map((model,i)=>({model,name:'离线验收模型 '+(i+1),selectionId:'mock-'+i,selectionRevision:'v1',available:true,parameters:{temperature:0.7,top_p:0.8,max_tokens:16384}}));
 const calls=[],seen=new Set();
 const control={enabled:true,baseURL:'http://127.0.0.1:1',serviceToken:'synthetic-failure-test-token-not-a-real-key',limit:100,fetcher:async(url,init)=>{
  if(init.method==='GET')return Response.json({models});
  const b=JSON.parse(init.body);calls.push(b);const input=b.messages.at(-1).content,marker=['模拟截断','模拟审查','模拟网关中断'].find(x=>input===x),key=b.sessionId+input;
  const broken=marker&&!seen.has(key);seen.add(key);
  const raw=broken?'<p>旅人抵达山间驿站，正准备询问路线。</p>':'<p>离线验收：旅人继续前往书院。</p>';
  const finish=broken?(marker==='模拟截断'?'length':marker==='模拟审查'?'content_filter':null):'stop';
  const sse='data: '+JSON.stringify({choices:[{delta:{content:raw},finish_reason:finish}]})+'\n\n'+(finish?'data: [DONE]\n\n':'');
  const model=models.find(m=>m.selectionId===b.selectionId).model;
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,status:broken?'failed_or_unknown':'complete',error:broken?'rpg_incomplete_output':null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse),runId:'mock-run-'+calls.length,attemptId:'mock-attempt-'+calls.length}},{status:broken?502:200});
 }};
 const host=await start({port,directory,messageTools:true,autoSummaryV2:true,archiveControls:{ancient:control}}),origin='http://127.0.0.1:'+host.server.address().port;
 async function call(path,body){const r=await fetch(origin+'/rpg-app/ancient/chat/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),v=await r.json();if(!r.ok)throw Object.assign(Error(v.error),{status:r.status});return v;}
 async function create(){const s=await call('sessions',{opening:'成年旅人离线测试'}),base='sessions/'+s.id;
  for(const id of ['rpg.model-selector','rpg.rolling-summary','rpg.message-tools'])for(const action of ['install','enable']){const c=await call('plugins'),p=c.plugins.find(p=>p.id===id);if(action==='install'&&p.installed)continue;const body={operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision};if(action==='enable')Object.assign(body,{sessionId:s.id,expectedSessionRevision:(await call(base)).revision,permissions:p.permissions});await call((action==='enable'?base+'/':'')+'plugins/'+id+'/'+action,body);}
  return s.id;
 }
 async function send(id,input,operationId=randomUUID()){const base='sessions/'+id,h=await call(base+'/memory-palace');const body={operationId,input,expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,expectedContextPolicyRevision:h.effectivePolicy.revision};await call(base+'/send',body);for(let i=0;i<100;i++){const h=await call(base+'/memory-palace');if(!h.busy)return {session:await call(base),body};await new Promise(r=>setTimeout(r,50));}throw Error('fixture busy');}
 return {host,origin,directory,call,create,send,calls,async close(){await new Promise(r=>host.server.close(r));await host.plugins.close();}};
}

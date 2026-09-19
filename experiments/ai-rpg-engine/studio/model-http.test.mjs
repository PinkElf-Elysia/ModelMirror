import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {ModelSessionStore} from './model-session-store.mjs';
import {models} from './provider.mjs';
import {canonical,sha,MODEL_SELECTOR_ID,PLUGIN_ID} from '../plugins/catalog.mjs';
import {activeSystem,loadFrozen} from '../card-replica/lib/assembly.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/model-selector-b3/http/',import.meta.url));await mkdir(root,{recursive:true});
const token='synthetic-b3-service-token-test-only';
const A={selectionId:'model-a',selectionRevision:'catalog-a',model:'synthetic/A',name:'A',available:true,parameters:models.earth.parameters};
const B={selectionId:'model-b',selectionRevision:'catalog-b',model:'synthetic/B',name:'B',available:true,parameters:models.earth.parameters};
async function fixture(t,{limit=4}={}){
 const directory=await mkdtemp(join(root,'h-')),wires=[],outputs=[];let catalog=[A,B],onChat=null;
 const upstream=createServer(async(req,res)=>{
  assert.equal(req.headers.authorization,'Bearer '+token);res.setHeader('Content-Type','application/json');
  if(req.url==='/api/rpg/v1/models'){res.end(JSON.stringify({models:catalog}));return;}
  const chunks=[];for await(const c of req)chunks.push(c);const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));wires.push(body);
  if(onChat)return onChat(res,body);
  const model=[A,B].find(m=>m.selectionId===body.selectionId).model,raw='原始 '+model+'\n\t回合 '+wires.length;outputs.push(raw);
  const sse='data: '+JSON.stringify({model,choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
  res.end(JSON.stringify({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:body.selectionId,selectionRevision:body.selectionRevision,requestedModel:model,actualModel:model,parameters:body.parameters,dispatched:true,retries:0,status:'complete',error:null,rawHash:sha(raw),sseHash:sha(sse),requestHash:sha(canonical(body)),runId:'run-'+wires.length,attemptId:'attempt-'+wires.length}}));
 });await new Promise(r=>upstream.listen(0,'127.0.0.1',r));
 const opts={port:0,directory,enabled:true,key:'synthetic-old-key',limit,modelControl:{baseURL:'http://127.0.0.1:'+upstream.address().port+'/',serviceToken:token,enabled:true},fetcher:async()=>new Response('data: '+JSON.stringify({model:models.earth.model,provider:models.earth.provider,choices:[{delta:{content:'默认原文'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}})};
 const host=await start(opts),origin='http://127.0.0.1:'+host.server.address().port;
 t.after(async()=>{await host.plugins.close();await new Promise(r=>host.server.close(r));await new Promise(r=>upstream.close(r));});
 const f={directory,host,wires,outputs,opts,setCatalog(x){catalog=x;},onChat(x){onChat=x;},async api(path,body,from=origin){const r=await fetch(origin+'/rpg-app/earth/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:from,'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};},async create(){const r=await this.api('sessions',{characterText:'姓名：B3 虚构者',world:'表世界',mode:'real',params:{max_tokens:16384}});assert.equal(r.status,201);return r.data;},async change(action,id,s){const c=await host.plugins.catalog(),p=c.plugins.find(x=>x.id===id);return host.plugins.change(action,{operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async grant(s,id=MODEL_SELECTOR_ID){if(!(await host.plugins.catalog()).plugins.find(p=>p.id===id).installed)await this.change('install',id);await this.change('enable',id,s);},async select(s,model){const r=await this.api('sessions/'+s.id+'/model-selection',{operationId:randomUUID(),expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:model.selectionId,catalogRevision:model.selectionRevision});assert.equal(r.status,200,JSON.stringify(r));return r.data.session;},async send(s,input='继续',requestId=randomUUID()){const r=await this.api('sessions/'+s.id+'/send',{revision:s.revision,expectedSelectionRevision:s.modelSelection.revision,input,requestId});assert.equal(r.status,200,JSON.stringify(r));return r.data;}};return f;
}

test('actual HTTP: parent A then B, branch inherits A, child changes independently, one budget and exact messages',async t=>{
 const f=await fixture(t);let parent=await f.create();await f.grant(parent);parent=await f.select(parent,A);parent=await f.send(parent,'开始');
 const first=structuredClone(parent);parent=await f.select(parent,B);parent=await f.send(parent,'父路线后续');assert.equal(parent.turns.length,2,'second controlled turn must complete before branch grant');
 await f.grant(parent,PLUGIN_ID);const result=await f.api('sessions/'+parent.id+'/branches',{operationId:'branch',expectedSessionRevision:parent.revision,turn:1,name:'不进模型的分支名'});assert.equal(result.status,201,JSON.stringify(result));let child=result.data.session;
 assert.equal(child.modelSelection.current.model,A.model);assert.equal(child.modelSelection.revision,0);assert.equal(f.wires.length,2);
 child=await f.send(child,'分支续玩');assert.equal(child.turns.at(-1).model.actualModel,A.model);
 await f.grant(child);child=await f.select(child,B);child=await f.send(child,'子路线再选B');
 assert.equal((await f.api('sessions/'+parent.id)).data.modelSelection.current.model,B.model);
 assert.equal(parent.turns[0].model.actualModel,A.model);assert.equal(parent.turns[1].model.actualModel,B.model);
 const {prompts}=loadFrozen(),wrap=x=>prompts.prefix+'\n'+x+'\n'+prompts.suffix;
 assert.deepEqual(f.wires[0].messages,[{role:'system',content:activeSystem},{role:'user',content:'姓名：B3 虚构者\n\n开始'}]);
 assert.deepEqual(f.wires[2].messages,[...f.wires[0].messages,{role:'assistant',content:f.outputs[0]},{role:'user',content:wrap('分支续玩')}]);
 assert.ok(!JSON.stringify(f.wires).includes('不进模型的分支名'));
 assert.equal((await f.host.control.status()).used,4);
 const exhausted=await f.send(child,'超额');assert.equal(exhausted.turns.length,3);assert.equal(f.wires.length,4);
 const noBudgetChoice=await f.select(child,A);assert.equal(noBudgetChoice.modelSelection.current.model,A.model);assert.equal((await f.host.control.status()).used,4);assert.equal(f.wires.length,4);
 const persisted=await f.host.earth.read(child.id);assert.equal(Object.values(persisted.requests).at(-1).status,'failed');
 const record=parent.turns[1].model.evidence;assert.equal(record.record.actualModel,B.model);assert.equal(record.sha256,sha(await readFile(join(f.directory,'dispatches',record.ref))));
 assert.equal(parent.turns[0].raw,first.turns[0].raw);
});

test('zero-plugin real default records existing receipt; no automatic selection or controlled dispatch',async t=>{
 const f=await fixture(t,{limit:1});let s=await f.create();s=await f.send(s,'默认继续');
 assert.equal(s.turns.length,1);assert.equal(s.turns[0].model.selection.kind,'fixed');assert.equal(s.turns[0].model.actualModel,models.earth.model);assert.equal(f.wires.length,0);
 assert.equal((await f.host.control.status()).used,1);
 assert.equal((await f.api('sessions/'+s.id+'/model-catalog')).status,409);
});

test('same-origin and field boundaries; unavailable selection leaves model and history unchanged',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);
 const input={operationId:'op',expectedSessionRevision:0,selectionRevision:0,selectionId:A.selectionId,catalogRevision:A.selectionRevision};
 assert.equal((await f.api('sessions/'+s.id+'/model-selection',input,'https://foreign.invalid')).status,403);
 assert.equal((await f.api('sessions/'+s.id+'/model-selection',{...input,providerUrl:'https://bad.invalid'})).status,400);
 assert.equal((await f.api('sessions/'+s.id+'/send',{revision:0,input:'缺选择版本',requestId:'missing'})).status,400);
 const selected=await f.select(s,A);f.setCatalog([{...A,available:false},B]);
 const failed=await f.send(selected);assert.equal(failed.turns.length,0);assert.equal(failed.modelSelection.current.model,A.model);assert.equal(f.wires.length,0);assert.equal((await f.host.control.status()).used,0);
});

test('uninstall revokes selection but core continues selected model; repeat request is not retried',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.select(s,A);
 await f.change('uninstall',MODEL_SELECTOR_ID);const before=s;s=await f.send(s,'继续','unique-request');assert.equal(s.turns[0].model.actualModel,A.model);
 const replay=await f.send(before,'继续','unique-request');assert.equal(replay.turns.length,1);assert.equal(f.wires.length,1);
 await f.change('install',MODEL_SELECTOR_ID);assert.equal((await f.api('sessions/'+s.id+'/model-catalog')).status,403);
});

test('unknown result persists selected request and blocks new IDs, model switch and branch after restart',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.select(s,A);
 f.onChat(res=>{res.statusCode=502;res.end('{}');});s=await f.send(s);assert.equal(s.turns.length,0);assert.equal((await f.host.control.status()).used,1);
 const file=JSON.parse(await readFile(join(f.directory,'earth',s.id+'.json'),'utf8'));const pending=Object.values(file.requests)[0];assert.equal(pending.status,'pending');assert.equal(pending.selection.model,A.model);
 assert.equal((await f.api('sessions/'+s.id+'/send',{revision:0,expectedSelectionRevision:1,requestId:'another',input:'不得重放'})).status,409);
 assert.equal((await f.api('sessions/'+s.id+'/model-catalog')).status,409);
 const restarted=new ModelSessionStore(f.host.earth.directory,async()=>'',null,{control:f.host.control,evidence:f.host.control.evidence});await restarted.init();
 await assert.rejects(restarted.send(s.id,{revision:0,expectedSelectionRevision:1,requestId:'new-after-restart',input:'继续'}),/MODEL_UNCONFIRMED_REQUEST/);assert.equal(f.wires.length,1);
});

test('terminal persistence failure leaves pending on disk and provider evidence; no replay after recovery',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.select(s,A);const oldWrite=f.host.earth.write.bind(f.host.earth);let writes=0;
 f.host.earth.write=async state=>{if(++writes===2)throw Error('synthetic lost terminal write');return oldWrite(state);};
 const r=await f.api('sessions/'+s.id+'/send',{revision:0,expectedSelectionRevision:1,requestId:'crash',input:'开始'});assert.equal(r.status,400);
 f.host.earth.write=oldWrite;const persisted=await f.host.earth.read(s.id);assert.equal(persisted.requests.crash.status,'pending');assert.equal(persisted.turns.length,0);assert.equal(f.wires.length,1);
 assert.equal((await f.host.control.evidence(s.id,'crash')).record.status,'complete');
 assert.equal((await f.api('sessions/'+s.id+'/send',{revision:0,expectedSelectionRevision:1,requestId:'new-crash',input:'继续'})).status,409);
});


test('model status requires explicit grant and disables again after uninstall without hiding core selection',async t=>{
 const f=await fixture(t);let s=await f.create();
 assert.equal((await f.api('sessions/'+s.id+'/model-selection')).data.canSelect,false);
 await f.grant(s);assert.equal((await f.api('sessions/'+s.id+'/model-selection')).data.canSelect,true);
 s=await f.select(s,A);await f.change('uninstall',MODEL_SELECTOR_ID);
 const status=(await f.api('sessions/'+s.id+'/model-selection')).data;assert.equal(status.canSelect,false);assert.equal(status.modelSelection.current.model,A.model);
});

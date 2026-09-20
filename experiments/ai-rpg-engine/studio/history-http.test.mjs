import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {HistorySessionStore} from './history-session-store.mjs';
import {models} from './provider.mjs';
import {canonical,sha,HISTORY_WINDOW_ID as H,MODEL_SELECTOR_ID as M,PLUGIN_ID as B} from '../plugins/catalog.mjs';
import {activeSystem,loadFrozen} from '../card-replica/lib/assembly.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/history-window-b2/http/',import.meta.url));await mkdir(root,{recursive:true});
const token='synthetic-history-test-token-only-0000';
const A={selectionId:'model-a',selectionRevision:'catalog-a',model:'synthetic/A',name:'A',available:true,parameters:models.earth.parameters};
const C={...A,selectionId:'model-c',selectionRevision:'catalog-c',model:'synthetic/C',name:'C'};
const {prompts}=loadFrozen(),wrap=x=>prompts.prefix+'\n'+x+'\n'+prompts.suffix;
async function fixture(t,limit=8){
 const directory=await mkdtemp(join(root,'case-')),wires=[],outputs=[];let onCatalog=null,onControlled=null,onFixed=null;
 const record=(body,kind)=>{wires.push({kind,body});const raw='虚构原文 '+wires.length+'\n\t<LSM>现在'+(24+wires.length)+'岁；记忆原样</LSM>';outputs.push(raw);return raw;};
 const upstream=createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');assert.equal(req.headers.authorization,'Bearer '+token);
  if(req.url==='/api/rpg/v1/models'){if(onCatalog)await onCatalog();res.end(JSON.stringify({models:[A,C]}));return;}
  assert.equal(req.url,'/api/rpg/v1/chat/completions');const chunks=[];for await(const c of req)chunks.push(c);const body=JSON.parse(Buffer.concat(chunks));const raw=record(body,'controlled');
  if(onControlled)return onControlled(res,body);
  const model=body.selectionId===A.selectionId?A.model:C.model,sse='synthetic SSE '+raw;
  res.end(JSON.stringify({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:body.selectionId,selectionRevision:body.selectionRevision,requestedModel:model,actualModel:model,parameters:body.parameters,dispatched:true,retries:0,status:'complete',error:null,rawHash:sha(raw),sseHash:sha(sse),requestHash:sha(canonical(body))}}));
 });await new Promise(r=>upstream.listen(0,'127.0.0.1',r));
 const host=await start({historyWindow:true,port:0,directory,enabled:true,key:'synthetic-history-fixed-only',limit,modelControl:{baseURL:'http://127.0.0.1:'+upstream.address().port+'/',serviceToken:token,enabled:true},fetcher:async(url,options)=>{
  assert.equal(url,'https://openrouter.ai/api/v1/chat/completions');const body=JSON.parse(options.body),raw=record(body,'fixed');if(onFixed)await onFixed();
  return new Response('data: '+JSON.stringify({model:models.earth.model,provider:models.earth.provider,choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
 }}),origin='http://127.0.0.1:'+host.server.address().port;
 t.after(async()=>{await writeFile(join(directory,'wire-capture.json'),JSON.stringify({kind:'LOCAL_SYNTHETIC_PROVIDER_ONLY',wires,outputs},null,2));await host.plugins.close();await new Promise(r=>host.server.close(r));await new Promise(r=>upstream.close(r));});
 const f={directory,host,wires,outputs,onCatalog(x){onCatalog=x;},onControlled(x){onControlled=x;},onFixed(x){onFixed=x;},async api(path,body,from=origin){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:from,'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};},async create(){const r=await f.api('earth/api/sessions',{characterText:'姓名：M1虚构者\n开局24岁',world:'表世界',mode:'real',params:{max_tokens:16384}});assert.equal(r.status,201,JSON.stringify(r));return r.data;},async change(action,id,s){const c=await host.plugins.catalog(),p=c.plugins.find(x=>x.id===id);return host.plugins.change(action,{operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async grant(s,id=H){if(!(await host.plugins.catalog()).plugins.find(x=>x.id===id).installed)await f.change('install',id);await f.change('enable',id,s);},async policy(s){const r=await f.api('earth/api/sessions/'+s.id+'/history-window');assert.equal(r.status,200,JSON.stringify(r));return r.data;},async configuration(s,turns,includeInitialCharacter=false){const p=await f.policy(s),body={operationId:randomUUID(),expectedSessionRevision:s.revision,expectedConfigRevision:p.configRevision,turns,includeInitialCharacter};const r=await f.api('earth/api/sessions/'+s.id+'/history-window',body);assert.equal(r.status,200,JSON.stringify(r));return r.data.session;},async sendBody(s,text='继续',requestId=randomUUID()){return {input:text,requestId,revision:s.revision,expectedSelectionRevision:s.modelSelection.revision,expectedHistoryPolicyRevision:(await f.policy(s)).historyPolicyRevision};},async send(s,text='继续',requestId=randomUUID()){const r=await f.api('earth/api/sessions/'+s.id+'/send',await f.sendBody(s,text,requestId));assert.equal(r.status,200,JSON.stringify(r));return r.data;},async select(s,m=A){const r=await f.api('earth/api/sessions/'+s.id+'/model-selection',{operationId:randomUUID(),expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:m.selectionId,catalogRevision:m.selectionRevision});assert.equal(r.status,200,JSON.stringify(r));return r.data.session;},async branch(s,turn=1,operationId='branch-op'){const r=await f.api('earth/api/sessions/'+s.id+'/branches',{operationId,expectedSessionRevision:s.revision,turn,name:'只在界面的路线名'});assert.ok([200,201].includes(r.status),JSON.stringify(r));return r.data.session;}};return f;
}
function expected(s,text,N=null,initial=false){const T=s.history.length/2,start=N===null?0:Math.max(0,T-N),history=structuredClone(s.history.slice(start*2));if(initial&&start>0)history[0].content='开局角色资料（原始快照）\n'+s.characterText+'\n\n'+history[0].content;return [{role:'system',content:activeSystem},...history,{role:'user',content:T?wrap(text):s.characterText+'\n\n'+text}];}
test('actual M1 HTTP wires: zero plugin, crop, optional snapshot, model switch, branch point settings and shared budget',async t=>{
 const f=await fixture(t,7);let s=await f.create();const id=s.id;
 let before=await f.host.earth.read(id);s=await f.send(s,'开局');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'开局'));
 await f.grant(s);s=await f.configuration(s,1);before=await f.host.earth.read(id);s=await f.send(s,'第二轮');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'第二轮',1));
 before=await f.host.earth.read(id);s=await f.send(s,'第三轮');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'第三轮',1));assert.ok(!JSON.stringify(f.wires.at(-1).body.messages.slice(1)).includes('开局24岁'));
 const branchPrefix=structuredClone((await f.host.earth.read(id)).history);s=await f.configuration(s,2,true);await f.grant(s,M);const rev=(await f.policy(s)).configRevision;s=await f.select(s);assert.equal((await f.policy(s)).configRevision,rev);
 before=await f.host.earth.read(id);s=await f.send(s,'父线第四轮');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'父线第四轮',2,true));
 await f.grant(s,B);let child=await f.branch(s,3);assert.equal(f.wires.length,4);let disk=await f.host.earth.read(child.id);
 assert.deepEqual(disk.history,branchPrefix);assert.deepEqual(disk.requests,{});assert.equal(child.modelSelection.current.kind,'fixed');assert.deepEqual((await f.policy(child)).config,{turns:1,includeInitialCharacter:false});assert.equal((await f.policy(child)).enabled,false);
 const snapshot=(await f.host.earth.archive.read(child.id)).snapshotHash;before=disk;child=await f.send(child,'子线完整历史');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'子线完整历史'));
 await f.grant(child);await f.grant(child,M);child=await f.select(child,C);before=await f.host.earth.read(child.id);child=await f.send(child,'子线窗口续玩');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'子线窗口续玩',1));assert.equal((await f.host.earth.archive.read(child.id)).snapshotHash,snapshot);
 before=await f.host.earth.read(id);s=await f.send(s,'原路线继续');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'原路线继续',2,true));assert.equal((await f.host.control.status()).used,7);
 const exhausted=await f.send(child,'额度已满');assert.equal(exhausted.turns.length,child.turns.length);assert.equal(f.wires.length,7);
 await f.change('uninstall',H);await f.change('install',H);assert.equal((await f.policy(child)).enabled,false);assert.equal((await f.host.control.status()).used,7);
 await f.grant(s);s=await f.configuration(s,4);const again=await f.branch(s,3,'after-budget');assert.deepEqual((await f.policy(again)).config,{turns:1,includeInitialCharacter:false});assert.equal(f.wires.length,7);
 for(const state of [await f.host.earth.read(id),await f.host.earth.read(child.id)])for(const turn of state.turns){const record=state.requests[turn.requestId]||state.provenance.requestOrigins.find(x=>x.requestId===turn.requestId).record;assert.deepEqual(record.historyPolicy,turn.historyPolicy);assert.equal(record.assemblyHash,turn.historyPolicy.requestHash);}
 for(const wire of f.wires){assert.equal(wire.body.messages.filter(m=>m.role==='system').length,1);assert.ok(!JSON.stringify(wire.body.messages).includes('只在界面的路线名'));}
 const inherited=await f.api('earth/api/sessions/'+child.id+'/send',await f.sendBody(child,'禁止原请求重放',s.turns[0].requestId));assert.equal(inherited.status,409);
});
test('same origin and strict config/send fields; GET recovery is read-only, same-ID POST resolves pending without applying',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);const path='earth/api/sessions/'+s.id+'/history-window',body={operationId:'recover',expectedSessionRevision:0,expectedConfigRevision:0,turns:2,includeInitialCharacter:true};
 assert.equal((await f.api(path,body,'https://foreign.invalid')).status,403);assert.equal((await f.api(path,{...body,system:'injection'})).status,400);
 const send=await f.sendBody(s);delete send.expectedHistoryPolicyRevision;assert.equal((await f.api('earth/api/sessions/'+s.id+'/send',send)).status,400);
 const publish=f.host.earth.publishHistoryConfiguration;f.host.earth.publishHistoryConfiguration=async()=>{throw Error('synthetic failure');};assert.equal((await f.api(path,body)).status,400);
 const bytes=await readFile(f.host.earth.path(s.id));assert.equal((await f.api(path+'?operationId=recover')).data.operation.status,'pending');assert.deepEqual(await readFile(f.host.earth.path(s.id)),bytes);
 assert.equal((await f.api(path,{...body,operationId:'new-id'})).status,409);assert.equal((await f.api('earth/api/sessions/'+s.id+'/send',await f.sendBody(s))).status,409);
 const recovered=await f.api(path,body);assert.equal(recovered.status,200);assert.equal(recovered.data.operation.status,'not-applied');assert.deepEqual(recovered.data.session.historyWindow.config,{turns:3,includeInitialCharacter:false});
 f.host.earth.publishHistoryConfiguration=publish;assert.equal((await f.api(path,{...body,operationId:'confirmed-new'})).data.operation.status,'complete');assert.equal(f.wires.length,0);
});
test('disable while controlled catalog waits restores full history before wire freezes',async t=>{
 const f=await fixture(t);let s=await f.create();s=await f.send(s,'一');s=await f.send(s,'二');await f.grant(s);s=await f.configuration(s,1,true);await f.grant(s,M);s=await f.select(s);
 const before=await f.host.earth.read(s.id),body=await f.sendBody(s,'撤权中继续');let entered,release;const ready=new Promise(r=>entered=r);f.onCatalog(async()=>{entered();await new Promise(r=>release=r);});
 const pending=f.api('earth/api/sessions/'+s.id+'/send',body);await ready;await f.change('disable',H,s);release();const result=await pending;assert.equal(result.status,200);assert.equal(result.data.turns.length,3);assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'撤权中继续'));assert.equal(result.data.turns.at(-1).historyPolicy.enabled,false);
});
test('late revocation at transport boundary reserves but never posts the stale wire; no retry',async t=>{
 const f=await fixture(t,2);let s=await f.create();await f.grant(s);await f.grant(s,M);s=await f.select(s);const generate=f.host.control.generate;
 f.host.control.generate=async(card,args)=>{await f.change('disable',H,s);return generate(card,args);};
 const sent=await f.send(s,'不得外发旧窗口','late-revoke');assert.equal(sent.turns.length,0);assert.equal(f.wires.length,0);assert.equal((await f.host.control.status()).used,1);
 const disk=await f.host.earth.read(s.id);assert.equal(disk.requests['late-revoke'].status,'failed');assert.equal(disk.requests['late-revoke'].error,'HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH');
 const same=await f.api('earth/api/sessions/'+s.id+'/send',{input:'不得外发旧窗口',requestId:'late-revoke',revision:0,expectedSelectionRevision:s.modelSelection.revision,expectedHistoryPolicyRevision:disk.requests['late-revoke'].historyPolicy.policyRevision});assert.equal(same.status,200);assert.equal((await f.host.control.status()).used,1);
});
test('cancelled late output stays in receipt, never history; generation blocks configuration/model/branch',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);await f.grant(s,M);await f.grant(s,B);let entered,release;const ready=new Promise(r=>entered=r);f.onFixed(async()=>{entered();await new Promise(r=>release=r);});
 const body=await f.sendBody(s,'取消本轮','cancelled'),pending=f.api('earth/api/sessions/'+s.id+'/send',body);await ready;
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/history-window',{operationId:'busy',expectedSessionRevision:0,expectedConfigRevision:0,turns:1,includeInitialCharacter:false})).status,409);
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/model-selection',{operationId:'busy-model',expectedSessionRevision:0,selectionRevision:0,selectionId:A.selectionId,catalogRevision:A.selectionRevision})).status,409);
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/branches',{operationId:'busy-branch',expectedSessionRevision:0,turn:1,name:'忙'})).status,409);
 await f.change('disable',H,s); // Registry queue must not wait for the model response.
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/cancel',{})).data.cancelRequested,true);release();const result=await pending;assert.equal(result.data.turns.length,0);assert.equal(result.data.requests.cancelled.status,'cancelled');assert.equal((await f.host.control.status()).used,1);assert.equal((await f.host.control.evidence(s.id,'cancelled')).record.status,'failed_or_unknown');
});
test('unknown provider result survives restart, cannot switch/settings/branch or use new request ID',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);await f.grant(s,M);s=await f.select(s);f.onControlled(res=>{res.statusCode=502;res.end('{}');});s=await f.send(s,'未知结果','unknown');assert.equal(s.turns.length,0);assert.equal(s.requests.unknown.status,'pending');
 const restarted=new HistorySessionStore(f.host.earth.directory,()=>assert.fail('no replay'),null,{control:f.host.control,evidence:f.host.control.evidence});await restarted.init();
 await assert.rejects(restarted.send(s.id,await f.sendBody(s,'新ID','new-id'),f.host.plugins),/MODEL_UNCONFIRMED_REQUEST/);
 await assert.rejects(restarted.saveHistoryWindow(s.id,{operationId:'unknown-change',expectedSessionRevision:0,expectedConfigRevision:0,turns:2,includeInitialCharacter:false},f.host.plugins),/HISTORY_UNCONFIRMED_REQUEST/);
 await assert.rejects(restarted.select(s.id,{operationId:'unknown-model',expectedSessionRevision:0,selectionRevision:s.modelSelection.revision,selectionId:C.selectionId,catalogRevision:C.selectionRevision},f.host.plugins),/MODEL_UNCONFIRMED_REQUEST/);
 assert.equal(f.wires.length,1);assert.equal((await f.host.control.status()).used,1);
});

test('unknown authorization cannot silently choose full history; disabled plugin restores full on next request',async t=>{
 const f=await fixture(t);let s=await f.create();s=await f.send(s,'开局');await f.grant(s);s=await f.configuration(s,1,true);const body=await f.sendBody(s,'应拒绝'),auth=f.host.plugins.historyAuthorization;
 f.host.plugins.historyAuthorization=async()=>{throw Object.assign(Error('HISTORY_AUTHORIZATION_UNKNOWN'),{status:409,code:'HISTORY_AUTHORIZATION_UNKNOWN'});};
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/send',body)).status,409);assert.equal(f.wires.length,1);f.host.plugins.historyAuthorization=auth;
 await f.change('disable',H,s);const before=await f.host.earth.read(s.id);s=await f.send(s,'明确停用后');assert.deepEqual(f.wires.at(-1).body.messages,expected(before,'明确停用后'));assert.equal(s.turns.at(-1).historyPolicy.initialSnapshotAdded,false);
});
test('branch retries are idempotent, parent-child configs isolated and pending settings block selection/branch',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.configuration(s,1);s=await f.send(s,'节点');await f.grant(s,B);const child=await f.branch(s,1,'same-branch');assert.equal((await f.branch(s,1,'same-branch')).id,child.id);assert.equal(f.wires.length,1);
 await f.grant(child);await f.configuration(child,5,true);assert.deepEqual((await f.policy(s)).config,{turns:1,includeInitialCharacter:false});assert.equal((await f.host.earth.read(s.id)).turns.length,1);
 await f.grant(s,M);f.host.earth.publishHistoryConfiguration=async()=>{throw Error('synthetic failure');};
 const p=await f.policy(s);await f.api('earth/api/sessions/'+s.id+'/history-window',{operationId:'pending-settings',expectedSessionRevision:s.revision,expectedConfigRevision:p.configRevision,turns:2,includeInitialCharacter:true});
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/model-selection',{operationId:'switch-pending',expectedSessionRevision:s.revision,selectionRevision:0,selectionId:A.selectionId,catalogRevision:A.selectionRevision})).status,409);
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/branches',{operationId:'branch-pending',expectedSessionRevision:s.revision,turn:1,name:'禁止新分支'})).status,409);
 assert.equal(f.wires.length,1);
});

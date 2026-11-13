import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startComparisonUIFixture} from './comparison-ui-fixture.mjs';
import {pluginIntent} from './joint-client.mjs';

test('C2 native and chat HTTP projections: permissions, inert previews, adoption and readonly history', {timeout:180000},async t=>{
 const root=fileURLToPath(new URL('../.rpg04-work/c2-http/',import.meta.url));await mkdir(root,{recursive:true});
 const f=await startComparisonUIFixture({directory:await mkdtemp(join(root,'run-')),port:0});t.after(()=>f.close());
 function transport(origin,prefix){return async(path,body,expected=200)=>{const response=await fetch(origin+prefix+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});const v=await response.json();assert.equal(response.status,expected,JSON.stringify(v));return v;};}
 const selection=(catalog,entry,temp)=>({format:'modelmirror.rpg.parameter-selection/2',catalogRevision:catalog.revision,selectionId:entry.selectionId,selectionRevision:entry.selectionRevision,capabilityRevision:entry.capabilityRevision,profile:{format:'modelmirror.rpg.parameter-profile/1',purpose:'story',modelId:entry.modelId,parameters:{format:'modelmirror.rpg.parameter-config/2',values:{temperature:{mode:'explicit',value:temp}}}}});
 for(const name of ['creator','chat'])await t.test(name+' catalog and preview never append a formal turn',async()=>{
  const entry=f.info[name],id=entry.sessionId,api=transport(entry.origin,'/rpg-app/api/authoring'),base='/joint/comparisons/'+id;
  const settings=await api('/joint/settings/'+id);assert.equal(!!settings.plugins.parameters?.enabled,false);assert.equal(!!settings.plugins.comparison?.enabled,name==='chat');
  const options=await api(base+'/options'),e=options.catalog.models.find(m=>m.available);assert.ok(e.parameterPolicy);assert.ok(options.defaultChoice);assert.ok(Array.isArray(options.presets.current));
  const state=(await api(base)).state,intent={sessionId:id,operationId:'comparison-'+name,expectedContextRevision:state.contextRevision,expectedAuthorizationRevision:state.authorizationRevision,input:'我在雾港码头打开来信。',candidates:[{id:'a',choice:selection(options.catalog,e,.2)},{id:'b',choice:selection(options.catalog,e,.9)}]};
  const count=(await f.remote.evidence()).calls.length;await api(base,{action:'preflight',intent});assert.equal((await f.remote.evidence()).calls.length,count);
  const started=await api(base,{action:'create',intent});let b;
  for(let i=0;i<600;i++){b=await api(base+'/'+started.plan.id);if(['awaiting-choice','blocked'].includes(b.status))break;await new Promise(resolve=>setTimeout(resolve,25));}
  assert.equal(b.status,'awaiting-choice',JSON.stringify(b.events));const before=await api('/joint/sessions/'+id);assert.equal(before.session.turns.length,0);const original=JSON.stringify(before.session);
  const frozen=b.tasks.filter(task=>task.purpose==='story'||task.kind==='candidate'||task.result?.raw);assert.equal(frozen.length,2);assert.equal(frozen[0].request.messagesHash,frozen[1].request.messagesHash);
  for(const candidate of ['a','b']){const frame=await api(base+'/'+b.plan.id+'/preview/'+candidate);assert.ok(frame.token);const r=await fetch(entry.origin+frame.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}}),html=await r.text();assert.equal(r.status,200);assert.match(html,/候选原文/);assert.ok(html.includes(candidate==='a'?'0.2':'0.9'));await api('/presentations/release',{token:frame.token});}
  assert.equal(JSON.stringify((await api('/joint/sessions/'+id)).session),original);assert.equal((await f.remote.evidence()).calls.length,count+2);
  const adopt={operationId:'adopt-'+name,expectedContextRevision:b.context.contextRevision,candidateId:'b'};await api(base+'/'+b.plan.id+'/adopt',adopt);await api(base+'/'+b.plan.id+'/adopt',adopt);
  const after=(await api('/joint/sessions/'+id)).session;assert.equal(after.turns.length,1);assert.equal(after.turns[0].assistant,'候选原文 0.9。');assert.deepEqual(after.models,before.session.models);assert.equal((await f.remote.evidence()).calls.length,count+2);
  if(name==='chat'){const s=await api('/joint/settings/'+id);await api('/joint/settings/change',pluginIntent(s,'comparison','disable','disable-comparison'));
   const res=await fetch(entry.origin+'/rpg-app/api/authoring'+base+'/options');assert.notEqual(res.status,200);const old=await api(base);assert.equal(old.state,null);assert.equal(old.batches.length,1);assert.equal(old.diagnostic,'COMPARISON_AUTHORIZATION_REQUIRED');const saved=await api(base+'/'+b.plan.id+'/preview/a');assert.ok(saved.frameUrl);await api('/presentations/release',{token:saved.token});}
 });
 await t.test('all six card catalogs work with only comparison authorization',async()=>{
  for(const [card,entry]of Object.entries(f.info.cards)){const origin=new URL(entry.url).origin,api=transport(origin,'/rpg-app/'+card+'/api'),data=await api('/sessions/'+entry.sessionId+'/comparisons/options');assert.ok(data.catalog.models.some(m=>m.available),card);assert.ok(Array.isArray(data.presets.current));assert.equal(data.defaultChoice.entry.modelId,data.catalog.models.find(m=>m.available).modelId);}
 });
});

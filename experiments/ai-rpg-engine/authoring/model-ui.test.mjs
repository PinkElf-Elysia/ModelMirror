import test from 'node:test';
import {createServer} from 'node:http';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startAuthoringServer} from './server.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {OfflineModelControl,ControlPlaneModelControl,CONTROL_CATALOG_FORMAT} from './model-control.mjs';
import {blankDocument,sha256,canonical} from './contracts.mjs';
import {parameterDraft,parametersFromDraft,parameterStep,filteredModels,savedOperation,pendingOperation} from './model-ui.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6e/tests/',import.meta.url));await mkdir(root,{recursive:true});
const api='/rpg-app/api/authoring',mp='/model-settings';
const field=(minimum,maximum,defaultValue,required,wireName)=>({minimum,maximum,defaultValue,required,wireName});
const row=(selectionId='editable',extra={})=>({selectionId,selectionRevision:sha256(selectionId),modelId:'fixture/story',name:'离线可调样例',available:true,contextLength:128000,maxOutputTokens:32000,parameterPolicy:{kind:'editable',fields:{temperature:field(0,2,.7,false,'temperature'),topP:field(0,1,.8,false,'top_p'),maxOutputTokens:field(1,32000,4096,true,'max_tokens')},exclusive:[]},...extra});
const catalog=()=>({catalogId:'ui-offline',models:[row(),row('second'),row('unknown',{parameterPolicy:null}),row('denied',{available:false}),row('tokens',{modelId:'fixture/token-only',name:'仅输出长度样例',parameterPolicy:{kind:'editable',fields:{maxOutputTokens:field(1,8000,2048,true,'max_completion_tokens')},exclusive:[]}}),row('fixed',{name:'固定档样例',parameterPolicy:{kind:'fixed',fields:{maxOutputTokens:field(4096,4096,4096,true,'max_tokens')},exclusive:[]}})]});
async function fixture(t,options={}){
  const directory=await mkdtemp(join(root,'run-')),control=options.control||new OfflineModelControl(catalog());
  let provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:{one:{status:'completed',text:'离线完整回复',finishReason:'stop'}},fault:options.providerFault});
  let host=await startAuthoringServer({directory:join(directory,'works'),modelSettings:{directory:join(directory,'sessions'),provider,control}});
  t.after(async()=>{await host.close();await provider.close();});
  const request=async(path,body,headers={})=>{
    const response=await fetch(host.origin+api+path,body===undefined?{headers}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});
    return {status:response.status,value:await response.json()};
  };
  const document=blankDocument();document.metadata.name='模型界面验证';document.metadata.audience='female';document.sources.system='冻结 SYSTEM';document.sources.suffix='冻结 POST';
  const work=(await request('/works',{operationId:'create-work',expectedRevision:null,document})).value;
  const version=(await request('/works/'+work.workId+'/publish',{operationId:'seal',expectedRevision:0})).value.result.versionId;
  const creation={operationId:'create-session',expectedRevision:null,reference:{workId:work.workId,versionId:version},openingText:'测试开局',presetId:null};
  const session=(await request(mp+'/sessions',creation)).value.session;
  const read=async()=> (await request(mp+'/sessions/'+session.id)).value;
  async function intent(purpose='story',selectionId='editable',operationId='model-'+purpose){
    const s=(await read()).session,c=control.catalog(),e=c.models.find(x=>x.selectionId===selectionId);
    return {sessionId:s.id,operationId,expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice:{catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,parameters:parametersFromDraft(e,parameterDraft(e))}};
  }
  return {directory,request,read,intent,control,session,work,creation,get host(){return host;},get provider(){return provider;},async reopen(){await host.close();await provider.close();provider=await OfflineFixtureProvider.open(join(directory,'provider'));host=await startAuthoringServer({directory:join(directory,'works'),modelSettings:{directory:join(directory,'sessions'),provider,control}});}};
}

test('参数草稿保留省略项；模型切换取所选能力默认值，固定档不能变成范围',()=>{
  const e=row(),d=parameterDraft(e,{maxOutputTokens:1234});assert.equal(d.enabled.temperature,false);assert.equal(d.values.temperature,'0.7');assert.deepEqual(parametersFromDraft(e,d),{maxOutputTokens:1234});
  d.enabled.temperature=true;d.values.temperature='1.2';assert.equal(parametersFromDraft(e,d).temperature,1.2);
  assert.equal(parameterDraft(e).values.temperature,'0.7');
  assert.equal(parameterStep('temperature',e.parameterPolicy.fields.temperature,'0.7'),'0.1');assert.equal(parameterStep('temperature',e.parameterPolicy.fields.temperature,'0.735'),'0.001');
  const fixed=catalog().models.at(-1),f=parameterDraft(fixed);f.values.maxOutputTokens='5000';assert.throws(()=>parametersFromDraft(fixed,f),e=>e.code==='MODEL_PARAMETER_RANGE');
});
test('空值不被当作零；整数、边界和互斥校验；未知能力阻断',()=>{
  const e=row(),d=parameterDraft(e);d.values.temperature='';assert.throws(()=>parametersFromDraft(e,d),e=>e.code==='MODEL_PARAMETER_REQUIRED');
  d.values.temperature='0';assert.equal(parametersFromDraft(e,d).temperature,0);d.values.maxOutputTokens='1.2';assert.throws(()=>parametersFromDraft(e,d));
  e.parameterPolicy.exclusive=[['temperature','topP']];const exclusive=parameterDraft(e);assert.equal(exclusive.enabled.topP,false);parametersFromDraft(e,exclusive);exclusive.enabled.topP=true;assert.throws(()=>parametersFromDraft(e,exclusive),e=>e.code==='MODEL_PARAMETER_EXCLUSIVE');
  assert.throws(()=>parametersFromDraft({parameterPolicy:null},d),e=>e.code==='MODEL_CAPABILITY_UNKNOWN');
});
test('中文英文搜索保留同名不同线路和不可用项，不改变目录',()=>{
  const c=catalog(),before=structuredClone(c);assert.equal(filteredModels(c,'STORY').length,5);assert.equal(filteredModels(c,'长度').length,1);assert.equal(filteredModels(c,'','missing').length,0);assert.deepEqual(c,before);
});
test('默认不启用模型配置；启用必须是受信控制器和离线 Provider',async t=>{
  const dir=await mkdtemp(join(root,'disabled-')),host=await startAuthoringServer({directory:dir});t.after(()=>host.close());
  assert.deepEqual(await (await fetch(host.origin+api+mp)).json(),{enabled:false,realProvider:false,pluginExecution:false});
  assert.equal((await fetch(host.origin+api+mp+'/catalog')).status,404);
  const css=await fetch(host.origin+'/model-ui.css');assert.equal(css.status,200);
  const bad=await mkdtemp(join(root,'bad-'));await assert.rejects(()=>startAuthoringServer({directory:bad,modelSettings:{directory:join(bad,'sessions'),provider:{},control:{}}}),e=>e.code==='MODEL_CONTROL_HOST_REQUIRED');
});
test('读目录与参数校验不创建配置、请求或调用；界面资源不暴露宿主源码',async t=>{
  const f=await fixture(t),before=await f.read();
  const c=(await f.request(mp+'/catalog')).value.catalog;assert.equal(c.source,'offline-fixture');assert.equal(c.models.find(e=>e.selectionId==='unknown').eligible,false);
  const body=await f.intent();const checked=await f.request(mp+'/check',{choice:body.choice});assert.equal(checked.status,200);assert.equal(checked.value.dispatched,false);
  await f.request(mp+'/refresh',{});assert.deepEqual(await f.read(),before);assert.equal(f.provider.dispatchCount,0);
  for(const file of ['/model-control.mjs','/offline-session.mjs','/session-storage.mjs'])assert.equal((await fetch(f.host.origin+file)).status,404);
  assert.equal((await f.request(mp+'/send',body)).status,404);
  assert.doesNotMatch(JSON.stringify(c),/serviceToken|origin|apiKey/);
});
test('选择 UI 结构与取消语义接入；HTTP 禁止跨站、端点、伪造能力与旧版本',async t=>{
  const f=await fixture(t),input=await f.intent();
  assert.equal((await f.request(mp+'/configure',input,{Origin:'https://outside.invalid'})).status,403);
  assert.equal((await f.request(mp+'/configure',{...input,endpoint:'https://outside.invalid'})).value.error.code,'AUTHORING_UNKNOWN_FIELD');
  assert.equal((await f.request(mp+'/configure',{...input,choice:{...input.choice,parameterPolicy:{}}})).value.error.code,'AUTHORING_UNKNOWN_FIELD');
  assert.equal((await f.request(mp+'/configure',{...input,choice:{...input.choice,catalogRevision:sha256('old-catalog')}})).value.error.code,'MODEL_CATALOG_CHANGED');
  assert.equal(f.provider.dispatchCount,0);
});
test('剧情摘要记忆独立保存；取消草稿不写入；实际离线请求使用已保存参数',async t=>{
  let captured;const f=await fixture(t,{providerFault:async(phase,request)=>{if(phase==='afterAccepted')captured=request;}});
  for(const purpose of ['story','summary','memory'])assert.equal((await f.request(mp+'/configure',await f.intent(purpose))).status,200);
  const prior=(await f.read()).session.models.current;
  const input=await f.intent('story','tokens','change-story');input.choice.parameters.maxOutputTokens=3210;
  assert.equal((await f.request(mp+'/configure',input)).status,200);
  const s=(await f.read()).session;assert.deepEqual(s.models.current.summary,prior.summary);assert.deepEqual(s.models.current.memory,prior.memory);
  const draft=parameterDraft(catalog().models[0]);draft.values.temperature='1.9';assert.equal((await f.read()).session.models.revision,s.models.revision);
  const result=await f.host.sessionHost.send({sessionId:s.id,operationId:'one',expectedRevision:s.revision,expectedModelRevision:s.models.revision,input:s.binding.openingText});
  assert.equal(result.operation.state,'completed');assert.deepEqual(captured.parameters,{max_completion_tokens:3210});
  assert.equal(captured.messages[0].content,'冻结 SYSTEM');assert.match(captured.messages.at(-1).content,/冻结 POST/);assert.equal(f.provider.dispatchCount,1);
});
test('重复操作幂等、多标签双 revision 冲突不覆盖，线路撤销不可再存',async t=>{
  const f=await fixture(t),input=await f.intent();await f.request(mp+'/configure',input);
  assert.equal((await f.request(mp+'/configure',input)).value.replayed,true);
  assert.equal((await f.request(mp+'/configure',{...input,operationId:'other-tab'})).value.error.code,'OFFLINE_REVISION_CONFLICT');
  const next=await f.intent('story','editable','revoked');const c=catalog();c.models[0].available=false;f.control.replace(c);
  assert.equal((await f.request(mp+'/configure',next)).value.error.code,'MODEL_CATALOG_CHANGED');assert.equal((await f.read()).session.models.revision,1);
});
test('保存回执丢失后核对原 ID；重启恢复已保存选择，无重派或自动选择',async t=>{
  const f=await fixture(t),input=await f.intent();await f.request(mp+'/configure',input);
  const before=await f.read();await f.reopen();assert.deepEqual(await f.read(),before);
  const result=(await f.request(mp+'/reconcile',{sessionId:f.session.id,operationId:input.operationId,kind:'configure'})).value;
  assert.equal(result.found,true);assert.ok(savedOperation(result.view,{kind:'configure',workId:f.work.workId,body:input}));
  assert.equal(savedOperation(result.view,{kind:'configure',workId:f.work.workId,body:{...input,purpose:'memory'}}),false);
  assert.equal((await f.request(mp+'/reconcile',{sessionId:null,operationId:f.creation.operationId,kind:'create'})).value.found,true);
  assert.equal((await f.request(mp+'/reconcile',{sessionId:null,operationId:'missing',kind:'create'})).value.found,false);
  assert.equal(f.provider.dispatchCount,0);
});
test('未知剧情结果锁定模型配置；核对配置不会重派或解除原未知请求',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted')throw Error('unknown fixture');}});await f.request(mp+'/configure',await f.intent());
  const s=(await f.read()).session;
  const result=await f.host.sessionHost.send({sessionId:s.id,operationId:'one',expectedRevision:s.revision,expectedModelRevision:s.models.revision,input:s.binding.openingText});assert.equal(result.operation.state,'unknown');
  const input=await f.intent('memory','editable','blocked');assert.equal((await f.request(mp+'/configure',input)).value.error.code,'OFFLINE_RESULT_PENDING');
  await f.request(mp+'/reconcile',{sessionId:s.id,operationId:'blocked',kind:'configure'});assert.equal((await f.read()).pending,true);assert.equal(f.provider.dispatchCount,1);
});


test('界面接口接入正式适配器的离线控制面，显式刷新与校验只传参数且保留原配置',async t=>{
  const calls=[],token='synthetic-b6e-control-token-for-offline-tests';let unavailable=false;
  const raw={format:CONTROL_CATALOG_FORMAT,source:'control-plane-cache',...catalog()};
  raw.models=raw.models.map(e=>({...e,available:e.parameterPolicy?e.available:false,parameterEvidence:e.parameterPolicy?'provider-declared':'unknown'}));
  const data={...raw,revision:sha256(canonical(raw))};
  const bridge=createServer(async(req,res)=>{
    assert.equal(req.headers.authorization,'Bearer '+token);
    let text='';for await(const chunk of req)text+=chunk;
    const input=text?JSON.parse(text):null;calls.push({path:req.url,body:input});
    res.setHeader('Content-Type','application/json');
    if(unavailable){res.statusCode=503;res.end('{}');return;}
    const e=input?data.models.find(e=>e.selectionId===input.selectionId):null;
    res.end(JSON.stringify(input?{format:'modelmirror.rpg.parameter-check/1',catalogRevision:data.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,wireParameters:Object.fromEntries(Object.entries(input.parameters).map(([k,v])=>[e.parameterPolicy.fields[k].wireName,v])),dispatched:false}:data));
  });
  await new Promise(resolve=>bridge.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{bridge.closeAllConnections();await new Promise(resolve=>bridge.close(resolve));});
  const control=new ControlPlaneModelControl({origin:'http://127.0.0.1:'+bridge.address().port,serviceToken:token});
  const f=await fixture(t,{control});assert.equal(calls.length,0);
  assert.equal((await f.request(mp+'/catalog')).value.error.code,'MODEL_CONTROL_REFRESH_REQUIRED');assert.equal(calls.length,0);
  const published=(await f.request(mp+'/refresh',{})).value;assert.equal(published.catalog.source,'control-plane-cache');assert.equal(calls.length,1);
  assert.equal(JSON.stringify(published).includes(token),false);
  const input=await f.intent();assert.equal((await f.request(mp+'/check',{choice:input.choice})).status,200);
  assert.equal(calls.length,2);assert.deepEqual(Object.keys(calls[1].body).sort(),['catalogRevision','parameters','selectionId','selectionRevision']);
  assert.equal((await f.request(mp+'/configure',input)).status,200);const before=await f.read();
  unavailable=true;assert.equal((await f.request(mp+'/refresh',{})).status,503);
  assert.deepEqual(await f.read(),before);
  assert.equal((await f.request(mp+'/reconcile',{kind:'configure',operationId:input.operationId,sessionId:f.session.id})).value.found,true);
  assert.equal(calls.length,3);assert.equal(f.provider.dispatchCount,0);
});


test('多标签待核对记录逐份恢复；其他作品隔离，损坏记录阻断而不静默丢弃',()=>{
  const prefix='modelmirror.authoring.model-pending/';
  const storage={};Object.defineProperty(storage,'getItem',{value:key=>storage[key]??null});
  const record=(workId,operationId)=>({kind:'configure',workId,body:{operationId}});
  storage[prefix+'one/a']=JSON.stringify(record('one','a'));
  storage[prefix+'one/b']=JSON.stringify(record('one','b'));
  storage[prefix+'other/a']=JSON.stringify(record('other','a'));
  assert.equal(pendingOperation(storage,'one').body.operationId,'a');
  delete storage[prefix+'one/a'];
  assert.equal(pendingOperation(storage,'one').body.operationId,'b');
  delete storage[prefix+'one/b'];assert.equal(pendingOperation(storage,'one'),null);
  assert.equal(pendingOperation(storage,'other').workId,'other');
  storage[prefix+'one/b']='{broken';assert.throws(()=>pendingOperation(storage,'one'),e=>e.code==='MODEL_UI_STORAGE_UNAVAILABLE');
  assert.equal(storage[prefix+'one/b'],'{broken');
  storage[prefix+'one/b']=JSON.stringify(record('other','b'));assert.throws(()=>pendingOperation(storage,'one'),e=>e.code==='MODEL_UI_STORAGE_UNAVAILABLE');
});


test('expired control cache revalidates the same frozen selection without dispatch; changed selection still rejects',async t=>{
 const {fakeControl,syntheticToken}=await import('./controlled-test-support.mjs');const bridge=await fakeControl(t);
 const control=new ControlPlaneModelControl({origin:bridge.origin,serviceToken:syntheticToken,ttlMs:50});
 const f=await fixture(t,{control});await control.refresh();const c=control.catalog(),e=c.models[0];
 const choice={catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,parameters:{maxOutputTokens:4096}};
 await new Promise(r=>setTimeout(r,80));const checked=await f.request(mp+'/check',{choice});
 assert.equal(checked.status,200);assert.equal(checked.value.choice.entry.selectionRevision,choice.selectionRevision);assert.equal(bridge.calls.length,0);
 bridge.catalog.models[0].selectionRevision=sha256('changed-selection');
 await new Promise(r=>setTimeout(r,80));const changed=await f.request(mp+'/check',{choice});
 assert.equal(changed.status,422);assert.equal(changed.value.error.code,'MODEL_SELECTION_CHANGED');
 assert.equal((await f.read()).session.models.revision,0);assert.equal(bridge.calls.length,0);assert.equal(f.provider.dispatchCount,0);
});

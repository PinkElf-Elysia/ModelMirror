import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,rename} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {createPluginService} from './host.mjs';
import {invoke,validParameterChange} from './generation-parameters.mjs';
import {loadReviewedPlugins,loadReviewedCatalog,verifyPackage,canonical,sha,GENERATION_PARAMETERS_ID as P,MODEL_SELECTOR_ID as M,REVIEWED_PLUGIN_IDS,KNOWN_PLUGIN_IDS} from './catalog.mjs';

// Test projections and host-owned commit callbacks; no production chat adapter or
// Provider is created. Keep fixture records for inspection, outside version control.
const root=resolve(fileURLToPath(new URL('../.rpg04-work/parameter-plugin-tests/',import.meta.url)));
await mkdir(root,{recursive:true});
const clone=v=>structuredClone(v),err=code=>e=>e?.code===code;
const change=(purpose='story',parameterRevision=0,configurationHash=sha('intent'))=>({purpose,parameterRevision,configurationHash});
async function fixture(t,{adapter,loadParameters=true}={}) {
  const directory=await mkdtemp(join(root,'host-'));
  const sessions=new Map(['one','two'].map(id=>[id,{id,cardId:'earth',revision:0,parameterRevision:0,completedTurns:1,resourceHash:sha(id),generationParametersCompatible:true,busy:false,pending:false,privateHistory:'must-not-reach-adapter',credential:'synthetic-only'}]));
  const e=await loadReviewedCatalog(P);const options={directory,lookupSession:async id=>clone(sessions.get(id)),loadCatalog:async()=>{
    const entries=await loadReviewedPlugins({generationParameters:loadParameters});
    return entries.map(x=>x.manifest.id===P?{...x,invoke:adapter??x.invoke}:x);
  }};
  let host=await createPluginService(options),generations=0;
  const f={directory,sessions,entry:e,get host(){return host;},get generations(){return generations;},
    async payload(action,id='one') {return {operationId:randomUUID(),pluginId:P,version:e.manifest.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:(await host.catalog()).revision,
      ...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:sessions.get(id).revision}:{}),...(action==='enable'?{permissions:e.manifest.permissions}: {})};},
    async change(action,id){return host.change(action,await f.payload(action,id));},
    async ready(){await f.change('install');await f.change('enable');},
    invoke(input=change(),capability='session.parameters.configure',sessionId='one'){return host.invoke({pluginId:P,sessionId,capability,input});},
    async expected(){const s=sessions.get('one'),a=await host.parameterAuthorization('one');return {authorizationRevision:a.revision,sessionRevision:s.revision,parameterRevision:s.parameterRevision};},
    dispatch(expected,start=()=>{generations++;return Promise.resolve('offline-result');}){return host.startParameterDispatch('one',expected,start);},
    async reopen(override={}){await host.close();host=await createPluginService({...options,...override});},
  };
  t.after(()=>host.close());return f;
}

test('new package is opt-in; legacy advertised list and default catalog stay unchanged',async()=>{
  assert.equal(REVIEWED_PLUGIN_IDS.length,8);assert.equal(KNOWN_PLUGIN_IDS.length,11);assert.equal(new Set(KNOWN_PLUGIN_IDS).size,11);assert.ok(KNOWN_PLUGIN_IDS.includes('rpg.shop'));assert.equal(REVIEWED_PLUGIN_IDS.includes('rpg.shop'),false);assert.ok(KNOWN_PLUGIN_IDS.includes('rpg.model-comparison'));
  assert.equal(REVIEWED_PLUGIN_IDS.includes(P),false);
  assert.deepEqual((await loadReviewedPlugins()).map(x=>x.manifest.id),['rpg.branch-save',M,'rpg.history-window']);
  const before=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true,messageTools:true});
  const after=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true,messageTools:true,generationParameters:true});
  assert.deepEqual(after.filter(x=>x.manifest.id!==P),before);assert.equal(after.at(-1).manifest.id,P);
  assert.equal(after.some(x=>x.manifest.id==='rpg.model-comparison'),false);const opted=await loadReviewedPlugins({modelComparison:true});assert.deepEqual(opted.map(x=>x.manifest.id),['rpg.branch-save',M,'rpg.history-window','rpg.model-comparison']);
});

test('manifest binds exact bytes, capabilities and no transport or model-selection authority',async()=>{
  const manifest=await readFile(new URL('./generation-parameters.manifest.json',import.meta.url)),artifact=await readFile(new URL('./generation-parameters.mjs',import.meta.url));
  const e=verifyPackage(manifest,artifact);assert.equal(e.manifest.network,'none');assert.equal(e.manifest.modelAccess,false);assert.equal(e.manifest.dataRetention,'retain');
  assert.deepEqual(e.manifest.permissions,['model.parameters.read','session.parameters.configure','ui.contribute']);
  for(const patch of [{modelAccess:true},{permissions:[...e.manifest.permissions,'session.model.select']},{capabilities:[...e.manifest.capabilities,'model.dispatch']},{compatibleCards:['*']},{entrypoint:'unreviewed.mjs'}])
    assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify({...e.manifest,...patch})),artifact),err('PLUGIN_MANIFEST_INVALID'));
  assert.throws(()=>verifyPackage(manifest,Buffer.from('different source')),err('PLUGIN_ARTIFACT_MISMATCH'));
});

test('adapter has bounded purpose/hash proposals only and does not modify caller state',()=>{
  const session={id:'one',revision:3,parameterRevision:2,completedTurns:1},original=clone(session);
  for(const purpose of ['story','summary','memory']) {
    const input=change(purpose,2),snapshot=clone(input);
    assert.deepEqual(invoke({session,input,capability:'session.parameters.configure'}),{kind:'parameter-configuration-request',sessionId:'one',...input});
    assert.deepEqual(invoke({session,input:{purpose},capability:'model.parameters.read'}),{kind:'parameter-capability-request',sessionId:'one',purpose});
    assert.deepEqual(input,snapshot);
  }
  for(const input of [null,[],{},change('other',2),change('story',-1),change('story',1.5),change('story',Number.MAX_SAFE_INTEGER+1),change('story',2,'g'.repeat(64)),change('story',2,'A'.repeat(64)),{...change('story',2),model:'other'},{...change('story',2),parameters:{temperature:1}},{...change('story',2),url:'https://provider.invalid'}]) {
    assert.ok(!validParameterChange(input));assert.throws(()=>invoke({session,input,capability:'session.parameters.configure'}),/INVALID_PARAMETER_REQUEST/);
  }
  assert.throws(()=>invoke({session,input:change('story',1),capability:'session.parameters.configure'}),/INVALID_PARAMETER_REQUEST/);
  assert.throws(()=>invoke({session,input:{purpose:'memory',credential:'no'},capability:'model.parameters.read'}),/INVALID_PARAMETER_REQUEST/);
  assert.deepEqual(session,original);
});

test('plugin never sees history, credentials, raw parameters or host session objects',async t=>{
  const calls=[];const f=await fixture(t,{adapter:args=>{calls.push(args);return invoke(args);}});await f.ready();await f.invoke();
  assert.deepEqual(calls[0].session,{id:'one',revision:0,completedTurns:1,parameterRevision:0});assert.deepEqual(calls[0].input,change());
  calls[0].session.parameterRevision=500;assert.equal(f.sessions.get('one').parameterRevision,0);
});

test('install, per-session grant, capability reads and restart are separate from model selector',async t=>{
  const f=await fixture(t);assert.equal((await f.host.parameterAuthorization('one')).enabled,false);
  await assert.rejects(f.invoke(),err('PLUGIN_NOT_INSTALLED'));await f.change('install');await assert.rejects(f.invoke(),err('PLUGIN_NOT_AUTHORIZED'));
  await f.change('enable');assert.equal((await f.host.sessionStatus('one',M)).enabled,false);
  for(const purpose of ['story','summary','memory'])assert.equal((await f.invoke({purpose},'model.parameters.read')).proposal.purpose,purpose);
  assert.equal((await f.invoke({},'ui.parameters-action')).proposal.label,'生成参数');await f.reopen();
  assert.equal((await f.host.parameterAuthorization('one')).enabled,true);assert.equal((await f.host.parameterAuthorization('two')).enabled,false);
  for(const capability of ['session.model.select','model.catalog.read','model.dispatch','network.request','session.summary.configure','session.memory.configure'])await assert.rejects(f.invoke({},capability),err('PLUGIN_CAPABILITY_DENIED'));
});

test('each declared card requires an explicit compatible runtime and parameter revision',async t=>{
  const f=await fixture(t);await f.change('install');
  for(const cardId of f.entry.manifest.compatibleCards) {
    f.sessions.get('one').cardId=cardId;delete f.sessions.get('one').generationParametersCompatible;
    await assert.rejects(f.change('enable'),err('PARAMETER_RUNTIME_INCOMPATIBLE'));
    f.sessions.get('one').generationParametersCompatible=true;f.sessions.get('one').parameterRevision=-1;
    await assert.rejects(f.change('enable'),err('PARAMETER_RUNTIME_INCOMPATIBLE'));
    f.sessions.get('one').parameterRevision=0;await f.change('enable');assert.equal((await f.invoke()).proposal.sessionId,'one');await f.change('disable');
  }
  f.sessions.get('one').cardId='unreviewed';await assert.rejects(f.change('enable'),err('PLUGIN_CARD_INCOMPATIBLE'));
});

test('wrong version, broad permissions and stale registry writes never grant',async t=>{
  const f=await fixture(t),p=await f.payload('install');
  await assert.rejects(f.host.change('install',{...p,version:'2.0.0'}),err('PLUGIN_VERSION_MISMATCH'));
  await f.host.change('install',p);const e=await f.payload('enable');
  await assert.rejects(f.host.change('enable',{...e,permissions:[...e.permissions,'model.dispatch']}),err('PLUGIN_PERMISSION_MISMATCH'));
  await f.host.change('enable',e);await assert.rejects(f.host.change('disable',{...await f.payload('disable'),expectedRegistryRevision:e.expectedRegistryRevision}),err('PLUGIN_REGISTRY_CONFLICT'));
});

test('host-owned atomic commits preserve the other purposes and consume the ticket once',async t=>{
  const f=await fixture(t);await f.ready();const path=join(f.directory,'host-configurations.json');
  let configurations={story:null,summary:null,memory:null};const evidence=[];
  for(const [i,purpose] of ['story','summary','memory'].entries()) {
    // These are already host-validated fixture profiles, not a provider assertion.
    const profile={purpose,modelId:'synthetic/model',parameters:{temperature:.2+i*.2,stop:['\n\n',' 保留空格 ']}};
    const before=clone(configurations),ticket=await f.invoke(change(purpose,i,sha(canonical(profile))));
    assert.equal(ticket.parameterRevision,i);assert.equal(ticket.proposal.configurationHash,sha(canonical(profile)));
    await f.host.commitResult(ticket,async()=>{
      const next={...configurations,[purpose]:clone(profile)};await writeFile(path+'.tmp',JSON.stringify(next));await rename(path+'.tmp',path);
      configurations=next;f.sessions.get('one').parameterRevision++;f.sessions.get('one').revision++;
    });
    for(const other of ['story','summary','memory'].filter(p=>p!==purpose))assert.deepEqual(configurations[other],before[other]);
    await assert.rejects(f.host.commitResult(ticket,()=>{throw Error('must not run');}),err('PLUGIN_RESULT_INVALID'));
    evidence.push({purpose,profile,ticket});
  }
  assert.deepEqual(JSON.parse(await readFile(path,'utf8')),configurations);await f.change('disable');await f.reopen();assert.deepEqual(JSON.parse(await readFile(path,'utf8')),configurations);
  assert.equal((await f.host.parameterAuthorization('one')).enabled,false);assert.equal(f.generations,0);
  if(process.env.RPG_P2C1_EVIDENCE){await mkdir(process.env.RPG_P2C1_EVIDENCE,{recursive:true});await writeFile(join(process.env.RPG_P2C1_EVIDENCE,'permission-commit-capture.json'),JSON.stringify({scope:'offline host callbacks only; not production card integration',generations:f.generations,evidence,configurations},null,2));}
});

test('purpose, configuration hash and lease cannot be replaced after issuance',async t=>{
  const f=await fixture(t);await f.ready();const ticket=await f.invoke();
  for(const patch of [{parameterRevision:99},{sessionId:'two'},{proposal:{...ticket.proposal,purpose:'memory'}},{proposal:{...ticket.proposal,configurationHash:sha('different')}}])await assert.rejects(f.host.commitResult({...ticket,...patch},()=>assert.fail('forged commit')),err('PLUGIN_RESULT_INVALID'));
  const read=await f.invoke({purpose:'story'},'model.parameters.read');await assert.rejects(f.host.commitResult(read,()=>assert.fail('read cannot write')),err('PLUGIN_COMMIT_INVALID'));
});

test('parameter revision invalidates a ticket even when a host has not changed formal revision yet',async t=>{
  const f=await fixture(t);await f.ready();const ticket=await f.invoke();f.sessions.get('one').parameterRevision++;
  await assert.rejects(f.host.commitResult(ticket,()=>assert.fail('stale commit')),err('PLUGIN_RESULT_STALE'));
  await assert.rejects(f.invoke(),err('PLUGIN_EXECUTION_FAILED'));
});

test('concurrent tabs can commit only one revision; another completed proposal is stale',async t=>{
  const f=await fixture(t);await f.ready();const a=await f.invoke(),b=await f.invoke(change('memory'));let writes=0;
  const persist=async()=>{writes++;f.sessions.get('one').parameterRevision++;f.sessions.get('one').revision++;};
  const results=await Promise.allSettled([f.host.commitResult(a,persist),f.host.commitResult(b,persist)]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(results.find(r=>r.status==='rejected').reason.code,'PLUGIN_RESULT_STALE');assert.equal(writes,1);
});

test('busy or unknown requests block configuration and activation, while disable stays available',async t=>{
  for(const flag of ['busy','pending']) {
    const f=await fixture(t);await f.ready();f.sessions.get('one')[flag]=true;
    await assert.rejects(f.invoke(),err('PLUGIN_SESSION_BUSY'));await f.change('disable');await assert.rejects(f.change('enable'),err('PLUGIN_SESSION_BUSY'));
  }
});

test('disable and re-enable invalidate old proposals; reinstallation never restores session grants',async t=>{
  const f=await fixture(t);await f.ready();await f.change('enable','two');const ticket=await f.invoke(),oldAuth=await f.expected();
  await f.change('disable');await f.change('enable');await assert.rejects(f.host.commitResult(ticket,()=>assert.fail('late')),err('PLUGIN_RESULT_STALE'));
  await assert.rejects(f.dispatch(oldAuth),err('PARAMETER_AUTHORIZATION_CHANGED'));await f.change('uninstall');await f.change('install');
  assert.equal((await f.host.parameterAuthorization('one')).enabled,false);assert.equal((await f.host.parameterAuthorization('two')).enabled,false);
});

test('replaying old enable is read-only and cannot resurrect revoked consent',async t=>{
  const f=await fixture(t);await f.change('install');const payload=await f.payload('enable');await f.host.change('enable',payload);await f.change('disable');
  assert.equal((await f.host.change('enable',payload)).replayed,true);assert.equal((await f.host.parameterAuthorization('one')).enabled,false);
});

test('resource changes fail closed; another session never inherits parent authorization',async t=>{
  const f=await fixture(t);await f.ready();f.sessions.get('two').resourceHash=f.sessions.get('one').resourceHash;
  await assert.rejects(f.invoke(change(),'session.parameters.configure','two'),err('PLUGIN_NOT_AUTHORIZED'));
  const prior=await f.expected();f.sessions.get('one').resourceHash=sha('new frozen card version');
  await assert.rejects(f.host.parameterAuthorization('one'),err('PLUGIN_NOT_AUTHORIZED'));await assert.rejects(f.dispatch(prior),err('PLUGIN_NOT_AUTHORIZED'));
});

test('late adapter completion cannot commit after revocation',async t=>{
  let started,release;const entered=new Promise(r=>started=r);
  const f=await fixture(t,{adapter:async args=>{started();await new Promise(r=>release=r);return invoke(args);}});await f.ready();
  const pending=f.invoke();await entered;await f.change('disable');release();await assert.rejects(pending,err('PLUGIN_NOT_AUTHORIZED'));
});

test('parameter commit and revocation serialize; failed commit cannot be replayed using its ticket',async t=>{
  const f=await fixture(t);await f.ready();const ticket=await f.invoke();let release,began;const started=new Promise(r=>began=r),order=[];
  const commit=f.host.commitResult(ticket,async()=>{began();await new Promise(r=>release=r);order.push('commit');});await started;
  const payload=await f.payload('disable');const revoke=f.host.change('disable',payload).then(()=>order.push('disable'));release();await Promise.all([commit,revoke]);assert.deepEqual(order,['commit','disable']);
  await f.change('enable');const failing=await f.invoke();await assert.rejects(f.host.commitResult(failing,()=>{throw Error('offline disk failure');}),/offline disk failure/);
  await assert.rejects(f.host.commitResult(failing,()=>assert.fail('do not replay')),err('PLUGIN_RESULT_INVALID'));assert.equal(f.sessions.get('one').parameterRevision,0);
});

test('unknown commit preserves host record; plugin never retries the host callback',async t=>{
  const f=await fixture(t);await f.ready();const ticket=await f.invoke();let writes=0;const path=join(f.directory,'host-operation.json');
  await assert.rejects(f.host.commitResult(ticket,async()=>{writes++;await writeFile(path,JSON.stringify({operationId:'fixed-operation',configurationHash:ticket.proposal.configurationHash}));throw Error('response lost after host save');}),/response lost/);
  await assert.rejects(f.host.commitResult(ticket,()=>writes++),err('PLUGIN_RESULT_INVALID'));await f.reopen();
  assert.equal(JSON.parse(await readFile(path,'utf8')).operationId,'fixed-operation');assert.equal(writes,1);
});

test('dispatch uses the frozen grant and revisions; disabled fallback also has a guard',async t=>{
  const f=await fixture(t);const disabled=await f.expected();assert.equal(await (await f.dispatch(disabled)).response,'offline-result');
  await f.ready();await assert.rejects(f.dispatch(disabled),err('PARAMETER_AUTHORIZATION_CHANGED'));const current=await f.expected();
  assert.equal(await (await f.dispatch(current)).response,'offline-result');await f.change('disable');await assert.rejects(f.dispatch(current),err('PARAMETER_AUTHORIZATION_CHANGED'));
  assert.equal(f.generations,2);
});

test('configuration drift blocks network start, and mutable caller data cannot replace a queued expectation',async t=>{
  const f=await fixture(t);await f.ready();let current=await f.expected();f.sessions.get('one').parameterRevision++;
  await assert.rejects(f.dispatch(current),err('PARAMETER_CONFIGURATION_CHANGED'));current=await f.expected();f.sessions.get('one').revision++;
  await assert.rejects(f.dispatch(current),err('PARAMETER_CONFIGURATION_CHANGED'));assert.equal(f.generations,0);
  const expected=await f.expected();expected.parameterRevision--;
  const pending=f.dispatch(expected);expected.parameterRevision++;await assert.rejects(pending,err('PARAMETER_CONFIGURATION_CHANGED'));
  for(const wrong of [null,{}, {...await f.expected(),url:'https://invalid'}, {...await f.expected(),parameterRevision:NaN}])await assert.rejects(f.dispatch(wrong),err('PARAMETER_DISPATCH_GUARD_REQUIRED'));
});

test('in-flight completion is not blocked or cancelled by disabling the plugin',async t=>{
  const f=await fixture(t);await f.ready();let finish,started=0;
  const frozen={modelId:'synthetic/model',parameters:{temperature:.5}};const captured=clone(frozen);
  const sent=await f.dispatch(await f.expected(),()=>{started++;return new Promise(r=>finish=r);});
  await f.change('disable');assert.equal((await f.host.parameterAuthorization('one')).enabled,false);
  finish({raw:'offline completed',request:captured});assert.deepEqual((await sent.response).request,frozen);assert.equal(started,1);
});

test('opting out retains registry history and does not advertise the new plugin',async t=>{
  const f=await fixture(t);await f.ready();const bytes=await readFile(join(f.directory,'registry.json'));
  await f.reopen({loadCatalog:loadReviewedPlugins});assert.equal((await f.host.catalog()).plugins.some(p=>p.id===P),false);
  await assert.rejects(f.host.parameterAuthorization('one'),err('PLUGIN_UNKNOWN'));assert.deepEqual(await readFile(join(f.directory,'registry.json')),bytes);
});

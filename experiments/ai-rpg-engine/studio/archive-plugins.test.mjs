import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {createArchiveHost} from '../xingjian-card/host.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {parse,serialize} from '../plugins/transcript.mjs';
import {models} from './provider.mjs';

for(const card of ['xingjian','ancient','cultivation','lord-of-mysteries','douluo','dragon-raja','medieval','elderland','harry-potter'])test(card+' supports all eight plugins with isolated grants, import, rewrite and image receipts',async()=>{
  let imageCalls=0;
  const rewrites=[],selection={model:models.earth.model,name:'fixture',available:true,selectionId:'image-writer',selectionRevision:'v1',parameters:models.earth.parameters};
  const rewriteControl={enabled:true,model:selection.model,baseURL:'http://127.0.0.1:8000/',serviceToken:'fixture-only-archive-illustration-writer-token',limit:1,
    fetcher:async(url,options)=>{
      if(url.pathname.endsWith('/models'))return Response.json({models:[selection]});
      assert.equal(url.pathname,'/api/rpg/v1/chat/completions');
      const body=JSON.parse(options.body);rewrites.push(body);
      const raw='清晨的城门，一位旅人沿青石路前行。';
      return Response.json({raw,sse:'fixture',receipt:{...body,gateway:'rpg_scoped',requestedModel:selection.model,actualModel:selection.model,dispatched:true,retries:0,status:'complete',requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha('fixture')}});
    }};
  const asset=Buffer.from('fixture-image');
  const host=await start({port:0,directory:await mkdtemp(join(tmpdir(),'rpg-eight-')),autoSummaryV2:true,messageTools:true,transcript:true,rewriteControl,
    illustrationService:{catalog:async()=>({profiles:[{model_id:'fixture/image',options:{}}]}),generate:async job=>{
      imageCalls++;return {requested_model:job.model,actual_model:job.model,request_id:'fixture-receipt',images:[{media_type:'image/png',data_url:'data:image/png;base64,'+asset.toString('base64'),output_bytes:asset.length}]};
    }}});
  const origin='http://127.0.0.1:'+host.server.address().port;
  const root='/rpg-app/'+card+(card==='xingjian'?'/':'/chat/')+'api/';
  const request=(path,body,extra={})=>fetch(origin+root+path,{...(body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),...extra});
  async function api(path,body){const r=await request(path,body),v=await r.json();assert(r.ok,path+': '+JSON.stringify(v));return v;}
  const sessionPath=id=>'sessions/'+id;
  async function installAndGrant(id,pluginId){
    const c=await api('plugins'),p=c.plugins.find(p=>p.id===pluginId);
    const binding={version:p.version,pluginId:p.id,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256};
    if(!p.installed)await api('plugins/'+p.id+'/install',{...binding,expectedRegistryRevision:c.revision,operationId:randomUUID()});
    const latest=await api('plugins'),s=await api(sessionPath(id));
    await api(sessionPath(id)+'/plugins/'+p.id+'/enable',{...binding,expectedRegistryRevision:latest.revision,operationId:randomUUID(),sessionId:id,expectedSessionRevision:s.revision,permissions:p.permissions});
  }
  async function send(id,input){
    const path=sessionPath(id),m=await api(path+'/memory-palace');
    return api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:m.sessionRevision,expectedMemoryRevision:m.memoryRevision,expectedContextPolicyRevision:m.effectivePolicy.revision,input});
  }
  try{
    const initial=await api('plugins');assert.equal(initial.plugins.length,8);assert(initial.plugins.every(p=>!p.installed));
    const s=await api('sessions',{opening:'姓名：测试读者，城门初见'}),path=sessionPath(s.id);
    for(const route of ['/illustrations/models','/message-tools/progress?request='+randomUUID(),'/transcript/export']){
      const denied=await request(path+route),value=await denied.json();
      assert([403,409].includes(denied.status),JSON.stringify(value));
      assert(['PLUGIN_NOT_AUTHORIZED','PLUGIN_NOT_INSTALLED'].includes(value.error),JSON.stringify(value));
    }
    for(const plugin of ['rpg.message-tools','rpg.transcript','rpg.illustrations'])await installAndGrant(s.id,plugin);
    const one=await send(s.id,s.characterText);assert.equal(one.turns.length,1);
    const before=await api(path);
    const fork={operationId:randomUUID(),revision:before.revision,turn:1};
    const child=await api(path+'/message-tools/fork',fork);
    assert.deepEqual(await api(path+'/message-tools/fork',fork),child);
    const rewritten=await send(child.id,'改从南门出发');assert.equal(rewritten.turns.length,1);assert.equal(rewritten.turns[0].input,'改从南门出发');
    assert.deepEqual(await api(path),before,'regeneration leaves the original conversation intact');
    const exported=await request(path+'/transcript/export');assert.equal(exported.status,200);
    assert.deepEqual(parse(Buffer.from(await exported.arrayBuffer())).turns,[{input:s.characterText,raw:one.turns[0].raw}]);
    const importedTurns=[{input:'从前的提问',raw:'从前的回答'}],key=randomUUID();
    const imported=await api(path+'/transcript/import',undefined).catch(()=>null);assert.equal(imported,null,'GET cannot import');
    const transfer=()=>request(path+'/transcript/import',undefined,{method:'POST',headers:{Origin:origin,'Content-Type':'text/plain','x-import-id':key,'x-session-name':encodeURIComponent('导入的故事')},body:serialize(importedTurns)});
    const upload=await transfer(),newSession=await upload.json();assert.equal(upload.status,201,JSON.stringify(newSession));
    assert.deepEqual(await (await transfer()).json(),newSession,'same import ID is idempotent');
    assert.equal((await send(newSession.id,'接着往下走')).turns.length,1);
    await installAndGrant(newSession.id,'rpg.transcript');
    const reexport=await request(sessionPath(newSession.id)+'/transcript/export');
    const combined=parse(Buffer.from(await reexport.arrayBuffer())).turns;assert.equal(combined.length,2);assert.deepEqual(combined[0],importedTurns[0]);
    const imagePath=path+'/illustrations/';assert.equal((await api(imagePath+'models')).profiles.length,1);
    const rewrite={operationId:randomUUID(),turn:one.turns[0].requestId,source:'旅人从城门走进晨光。',focus:'回合剧情'};
    assert((await api(imagePath+'rewrite',rewrite)).prompt.includes('旅人'));
    assert.equal(rewrites.length,1);assert.equal(rewrites[0].sessionId,s.id);
    assert.equal((await api(imagePath+'models')).writer.remaining,0);
    assert.equal((await request(imagePath+'rewrite',rewrite)).status,409,'rewrites do not automatically replay');
    const job={operationId:randomUUID(),turn:one.turns[0].requestId,model:'fixture/image',prompt:'城门前的清晨',options:{}};
    await api(imagePath+'jobs',job);await api(imagePath+'jobs',job);
    let jobs=[];for(let n=0;n<40;n++){jobs=await api(imagePath+'jobs');if(jobs[0]?.state==='complete')break;await new Promise(r=>setTimeout(r,10));}
    assert.equal(imageCalls,1);assert.equal(jobs[0]?.state,'complete');
    assert.deepEqual(Buffer.from(await (await request(imagePath+job.operationId)).arrayBuffer()),asset);
    const other=await api('sessions',{opening:'另一位读者'});
    assert.equal((await request(sessionPath(other.id)+'/illustrations/jobs')).status,403);
    const c=await api('plugins'),p=c.plugins.find(p=>p.id==='rpg.illustrations');
    await api('plugins/'+p.id+'/uninstall',{pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,operationId:randomUUID()});
    assert.equal((await request(imagePath+'jobs')).status,403);
    assert.deepEqual(await api(path),before);
  }finally{await new Promise(r=>host.server.close(r));await host.plugins.close();}
});

test('previous archive adapter bindings remain readable without changing saved data',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'rpg-archive-compat-'));
  const host=await createArchiveHost(directory,null,{cardId:'cultivation',name:'测试',system:'测试系统'});
  try{
    const session=await host.store.create({opening:'旧开局'});
    session.runtime.descriptor.adapterHash='865c4c9bd9e106f435ee240e7b9b036283e7cb102078720106eb2623880aecf0';
    session.runtime.hash=sha(canonical(session.runtime.descriptor));
    await host.store.write(session);
    const before=await readFile(join(directory,session.id+'.json'));
    assert.equal(host.store.status(await host.store.read(session.id)).compatible,true);
    assert.deepEqual(await readFile(join(directory,session.id+'.json')),before);
    const changed=structuredClone(session);changed.runtime.descriptor.systemHash=sha('different prompt');changed.runtime.hash=sha(canonical(changed.runtime.descriptor));
    assert.equal(host.store.status(changed).compatible,false,'only the reviewed adapter hash is admitted');
    assert.equal((await host.plugins.catalog()).plugins.length,5,'disabled capabilities remain unavailable');
  }finally{await host.plugins.close();}
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {models} from './provider.mjs';
import {imageService} from './illustrations.mjs';

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jHtkAAAAASUVORK5CYII=','base64');
test('authorized scene and free generation use catalog contracts and durable, non-replayed images',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'rpg-picture-'));
 const ready={model_id:'fixture/art',display_name:'画笔',invocable:true,interaction_status:'ready',operation:'generate_image',
   pricing:[{billable:'output_image',unit:'image',cost_usd:.1}],supported_parameters:{aspect_ratio:{type:'enum',values:['1:1','16:9']}}};
 let calls=0,failCall=false,release;
 const gate=new Promise(r=>{release=r;});
 const service=imageService('http://127.0.0.1:8000',async(url,options)=>{
   if(url.pathname.endsWith('/models'))return Response.json({status:'online',profiles:[ready,{...ready,model_id:'edit-only',supported_parameters:{input_references:{min:1}}},{...ready,invocable:false}]});
   calls++;assert.equal(url.pathname,'/api/multimodal/image/generations');assert.equal(options.body.get('model_id'),ready.model_id);assert.equal(options.body.get('n'),'1');assert(options.headers['Idempotency-Key']);assert(!options.body.has('messages'));
   if(failCall)throw Error('transport lost');
   assert.equal(options.body.get('aspect_ratio'),'16:9');await gate;
   return Response.json({requested_model:ready.model_id,actual_model:ready.model_id,request_id:'fixture-receipt',images:[{media_type:'image/png',data_url:'data:image/png;base64,'+png.toString('base64'),output_bytes:png.length}],usage:{cost_kind:'actual',cost_usd:.1}});
 });
 const draft='游戏叙事插画。黑发少女独坐破败剧院舞台，绯红侧光穿过残幕。';
 const selection={model:models.earth.model,selectionRevision:'rev',selectionId:'model',available:true,name:'fixture',parameters:models.earth.parameters};
 const textRequests=[];
 const rewriteControl={enabled:true,model:selection.model,baseURL:'http://127.0.0.1:8000/',serviceToken:'fixture-only-illustration-writer-token',limit:1,
  fetcher:async(url,options)=>{
    if(url.pathname.endsWith('/models'))return Response.json({models:[selection]});
    assert.equal(url.pathname,'/api/rpg/v1/chat/completions');
    const body=JSON.parse(options.body);textRequests.push(body);
    return Response.json({raw:draft,sse:'fixture',receipt:{...body,gateway:'rpg_scoped',requestedModel:selection.model,actualModel:selection.model,
      dispatched:true,retries:0,status:'complete',requestHash:sha(canonical(body)),rawHash:sha(draft),sseHash:sha('fixture')}});
  }};
 let host,origin;
 const boot=async()=>{host=await start({directory,port:0,illustrationService:service,rewriteControl});origin='http://127.0.0.1:'+host.server.address().port;};
 const stop=async()=>{await new Promise(r=>host.server.close(r));await host.plugins.close();};
 await boot();
 const api=async(path,body)=>fetch(origin+'/rpg-app/'+path,body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 try{
   const s=await (await api('earth/api/sessions',{characterText:'姓名：测试',mode:'offline',world:'表世界',params:{}})).json();
   const path='earth/api/sessions/'+s.id,base=path+'/illustrations/';
   const turn=randomUUID();await api(path+'/send',{requestId:turn,input:'看看钟楼',revision:s.revision});
   const parent=await host.earth.read(s.id);
   const request={operationId:randomUUID(),turn,prompt:'铅笔画，黄昏中的钟楼',model:ready.model_id,options:{aspect_ratio:'16:9'}};
   const rewrite={operationId:randomUUID(),turn,source:'世界观说明。'.repeat(120)+'白羽绫音在剧院抬手。',focus:'关键画面特写'};
   assert.equal((await api(base+'rewrite',rewrite)).status,403);
   assert.equal((await api(base+'jobs',request)).status,403);
   const catalog=await (await api('api/plugins')).json(),p=catalog.plugins.find(p=>p.id==='rpg.illustrations');
   const binding={pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256};
   const rev=await (await api('api/plugins/'+p.id+'/install',{...binding,operationId:randomUUID(),expectedRegistryRevision:catalog.revision})).json();
   assert((await api(path+'/plugins/'+p.id+'/enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:rev.revision,sessionId:s.id,expectedSessionRevision:parent.revision,permissions:p.permissions})).ok);
   const catalogInfo=await (await api(base+'models')).json();
   assert.equal(catalogInfo.writer.remaining,1);assert.equal(catalogInfo.profiles.length,1);
   assert.equal((await api(base+'rewrite',{...rewrite,source:'x'.repeat(16001)})).status,400);
   assert.equal((await api(base+'rewrite',{...rewrite,turn:randomUUID()})).status,400);
   assert.equal(textRequests.length,0);
   const result=await (await api(base+'rewrite',rewrite)).json();
   assert.deepEqual(result,{prompt:draft});assert.equal(calls,0);
   assert.deepEqual(JSON.parse(textRequests[0].messages[1].content),{取景:rewrite.focus,素材:rewrite.source});
   assert.equal(textRequests[0].messages.length,2);
   assert.equal((await api(base+'rewrite',rewrite)).status,409);
   assert.equal((await api(base+'rewrite',{...rewrite,operationId:randomUUID()})).status,409);
   assert.equal(textRequests.length,1);assert.deepEqual(await host.earth.read(s.id),parent);
   request.prompt=draft+' 水彩与墨线。';
   assert.equal((await api(base+'jobs',{...request,options:{resolution:'bogus'}})).status,409);
   assert.equal((await api(base+'jobs',{...request,turn:randomUUID()})).status,400);assert.equal(calls,0);
   assert((await api(base+'jobs',request)).ok);assert((await api(base+'jobs',request)).ok);
   assert.equal((await api(base+'jobs',{...request,prompt:'different'})).status,409);
   assert.equal((await api(base+'jobs',{...request,operationId:randomUUID()})).status,409);assert.equal(calls,1);release();
   async function settled(key){
    for(let i=0;i<100;i++){const j=(await (await api(base+'jobs')).json()).find(r=>r.id===key);if(j&&j.state!=='pending')return j;await new Promise(r=>setTimeout(r,10));}
    throw Error('unfinished fixture');
   }
   const job=await settled(request.operationId);assert.equal(job.state,'complete');assert.equal(job.turn,turn);assert.equal(job.prompt,request.prompt);assert.equal(job.costUsd,.1);
   failCall=true;const free={...request,turn:null,operationId:randomUUID()};
   assert((await api(base+'jobs',free)).ok);assert.equal((await settled(free.operationId)).state,'unknown');
   await stop();await boot();assert.deepEqual(Buffer.from(await (await api(base+job.id)).arrayBuffer()),png);
   assert.equal((await (await api(base+'jobs',free)).json()).state,'unknown');assert.equal(calls,2);
   const record=join(directory,'illustrations','earth',s.id,free.operationId,'job.json');
   const saved=JSON.parse(await readFile(record));await writeFile(record,JSON.stringify({...saved,state:'pending'}));
   assert.equal((await settled(free.operationId)).state,'unknown');assert.equal(calls,2);
   assert.deepEqual(await host.earth.read(s.id),parent);
 }finally{release();await stop();}
});

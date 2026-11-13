import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {syncBuiltinESMExports} from 'node:module';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {createIllustrations} from './illustrations.mjs';

// Test files run in separate processes; restore builtin bindings before teardown.
async function fixture(t,failures,stage='complete'){
 const directory=await fs.mkdtemp(join(tmpdir(),'rpg-image-publish-')),id=randomUUID(),key=randomUUID();
 const file=join(directory,'harry-potter',id,key,'job.json'),asset=Buffer.from('offline image');
 let calls=0,attempts=0;const sources=new Set();
 const original=fs.rename;
 t.mock.method(fs,'rename',async(from,to)=>{
  if(to===file&&JSON.parse(await fs.readFile(from,'utf8')).state===stage){
   sources.add(from);attempts++;
   const code=failures[attempts-1];if(code)throw Object.assign(Error('injected local publication failure'),{code});
  }
  return original(from,to);
 });
 syncBuiltinESMExports();
 t.after(()=>{t.mock.restoreAll();syncBuiltinESMExports();});
 const service={catalog:async()=>({profiles:[{model_id:'fixture/image',options:{}}]}),generate:async()=>{
  calls++;return {requested_model:'fixture/image',actual_model:'fixture/image',request_id:'fixture-only',images:[{media_type:'image/png',data_url:'data:image/png;base64,'+asset.toString('base64'),output_bytes:asset.length}]};
 }};
 const plugins={sessionStatus:async()=>({enabled:true}),invoke:async()=>({}),commitResult:async(ticket,commit)=>commit()};
 const store={read:async()=>({turns:[]})},data={operationId:key,model:'fixture/image',prompt:'offline fixture',turn:null,options:{}};
 let handler=createIllustrations(directory,service);
 const request=async(method='GET')=>{let result;await handler({card:'harry-potter',id,action:'jobs',method,data,plugins,store,send:(status,value)=>{result={status,value};}});return result;};
 const settled=async()=>{
  const deadline=Date.now()+5000;
  while(Date.now()<deadline){const {value}=await request();if(value[0]?.state!=='pending')return value[0];await delay(10);}
  assert.fail('local publication did not settle');
 };
 return {file,asset,request,settled,counts:()=>({calls,attempts,sources:sources.size}),restart:()=>{handler=createIllustrations(directory,service);}};
}

test('transient completion publication locks retry only the same file, never image generation',async t=>{
 const f=await fixture(t,['EPERM','EACCES','EBUSY']);
 assert.equal((await f.request('POST')).status,202);
 const job=await f.settled();assert.equal(job.state,'complete');
 assert.deepEqual(f.counts(),{calls:1,attempts:4,sources:1});
 assert.equal(JSON.parse(await fs.readFile(f.file,'utf8')).state,'complete');
 assert.deepEqual(await fs.readFile(join(f.file,'..','asset')),f.asset);
 f.restart();assert.equal((await f.request('POST')).value.state,'complete');
 assert.deepEqual(f.counts(),{calls:1,attempts:4,sources:1});
});

test('persistent publication locks exhaust a bounded retry and remain unknown without replay',async t=>{
 const f=await fixture(t,Array(6).fill('EPERM'));
 await f.request('POST');assert.equal((await f.settled()).state,'unknown');
 assert.deepEqual(f.counts(),{calls:1,attempts:6,sources:1});
 assert.equal(JSON.parse(await fs.readFile(f.file,'utf8')).state,'pending');
 f.restart();assert.equal((await f.request('POST')).value.state,'unknown');assert.equal(f.counts().calls,1);
});

test('non-transient publication errors are not retried and never trigger regeneration',async t=>{
 const f=await fixture(t,['ENOSPC']);
 await f.request('POST');assert.equal((await f.settled()).state,'unknown');
 assert.deepEqual(f.counts(),{calls:1,attempts:1,sources:1});
 f.restart();assert.equal((await f.request('POST')).value.state,'unknown');assert.equal(f.counts().calls,1);
});

test('pending record publication recovers before the single dispatch',async t=>{
 const f=await fixture(t,['EPERM'],'pending');
 assert.equal((await f.request('POST')).status,202);
 assert.equal((await f.settled()).state,'complete');
 assert.deepEqual(f.counts(),{calls:1,attempts:2,sources:1});
});

test('failure to publish the initial intent prevents dispatch',async t=>{
 const f=await fixture(t,['ENOSPC'],'pending');
 await assert.rejects(f.request('POST'),{code:'ENOSPC'});
 assert.deepEqual(f.counts(),{calls:0,attempts:1,sources:1});
 await assert.rejects(fs.readFile(f.file),{code:'ENOENT'});
 f.restart();await assert.rejects(f.request('POST'),{code:'ENOENT'});
 assert.equal(f.counts().calls,0);
});

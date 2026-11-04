import test from 'node:test';
import assert from 'node:assert/strict';
import {createAuthoringApi} from './browser-api.mjs';
for(const phase of ['fetch','json','http'])test('读取/写入 '+phase+' 失败分类；均不自动重发',async()=>{
 for(const body of [undefined,{operationId:'same-id'}]){
  const calls=[],api=createAuthoringApi('/api',async(...args)=>{calls.push(args);if(phase==='fetch')throw Error('network');return {ok:false,status:502,json:async()=>{if(phase==='json')throw Error('html error');return {};}};});
  await assert.rejects(api('/path',body),e=>e.code===(body===undefined?'NETWORK_READ_FAILED':'NETWORK_RESULT_UNKNOWN'));
  assert.equal(calls.length,1);if(body)assert.deepEqual(JSON.parse(calls[0][1].body),body);
 }
});
test('有效服务端诊断和正常结果原样保留',async()=>{
 const api=createAuthoringApi('/api',async()=>({ok:false,status:409,json:async()=>({error:{code:'OFFLINE_REVISION_CONFLICT'}})}));
 await assert.rejects(api('/path'),e=>e.code==='OFFLINE_REVISION_CONFLICT'&&e.httpStatus===409);
 assert.deepEqual(await createAuthoringApi('/api',async()=>({ok:true,json:async()=>({revision:3})}))('/path'),{revision:3});
});

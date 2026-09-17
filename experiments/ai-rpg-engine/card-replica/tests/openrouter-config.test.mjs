import test from 'node:test';
import assert from 'node:assert/strict';
import {inspectCredential,buildComparison,MODEL,ENDPOINT} from '../lib/openrouter-config.mjs';
import {activeSystem} from '../lib/assembly.mjs';
const catalog={data:{id:MODEL,endpoints:[{tag:ENDPOINT,status:0,context_length:1048576,supported_parameters:['temperature','top_p','max_tokens','reasoning']}]}};
test('credential check exposes only presence and neutral errors',async()=>{
 const a=await inspectCredential(async()=>'OPENROUTER_API_KEY="synthetic-secret"\nUNRELATED=private');
 assert.equal(a.present,true);assert.ok(!JSON.stringify(a).includes('synthetic-secret'));assert.ok(!JSON.stringify(a).includes('private'));
 await assert.rejects(inspectCredential(async()=>{throw Error('private secret');}),/^Error: CREDENTIAL_SOURCE_UNAVAILABLE$/);
 assert.equal((await inspectCredential(async()=>'OTHER=x')).present,false);
});
test('Gemini configuration retains character literally and disables fallback with only the authorized system variant',()=>{
 const role='姓名:测试  出生:2008\n背景:2010年代  ';
 const {profile,payload}=buildComparison(role,catalog);
 assert.deepEqual(payload.messages,[{role:'system',content:activeSystem},{role:'user',content:role+'\n\n开始这一世。'}]);
 assert.deepEqual(payload.provider,{only:[ENDPOINT],order:[ENDPOINT],allow_fallbacks:false,require_parameters:true});
 assert.equal(profile.status,'configured-not-dispatchable');
 assert.equal(payload.max_tokens,16384);assert.equal(profile.requestedParameters.max_tokens,16384);
 assert.deepEqual(payload.transforms,[]);assert.ok(!('reasoning' in payload));assert.ok(!('response_format' in payload));
 assert.ok(!('top_k' in payload));assert.ok(!JSON.stringify(payload).includes('OPENROUTER_API_KEY'));
});
test('absent exact model or endpoint parameters fail closed without selecting a substitute',()=>{
 assert.throws(()=>buildComparison('角色',{data:{...catalog.data,id:'different'}}),/EXACT_ENDPOINT_UNAVAILABLE/);
 assert.throws(()=>buildComparison('角色',{data:{id:MODEL,endpoints:[{tag:ENDPOINT,status:0,supported_parameters:[]}]}}),/REQUIRED_PARAMETERS_UNAVAILABLE/);
});

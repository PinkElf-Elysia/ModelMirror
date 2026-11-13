import test from 'node:test';
import assert from 'node:assert/strict';
import {unconfirmedOutsideComparison,createComparisonClient,comparisonReceiptMatches,decisionBody,comparisonProgress,comparisonCandidates} from './comparison-client.mjs';
import {comparisonFrame} from '../studio/comparison-frame.mjs';
const storage=()=>{const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)};};
const profile={purpose:'story',parameters:{values:{temperature:{mode:'explicit',value:.4}}}};
const intent={sessionId:'s',operationId:'create-1',input:'原始输入',candidates:[{id:'a',choice:{selectionId:'model-a',profile}}]};
const batch=()=>({plan:{id:'compare-a',stages:[{id:'stage-a',candidateId:'a'}],intent:{...structuredClone(intent),candidates:[{id:'a',choice:{entry:{selectionId:'model-a'},parameterProfile:structuredClone(profile)}}]}},revision:4,status:'awaiting-choice',context:{contextRevision:17},tasks:[{id:'stage-a',state:'completed'}],decision:null,stopOperations:[]});
function setup(api,s=storage()){return createComparisonClient({api,storage:s,base:'/s',sessionId:'s'});}
test('preflight does not write a mutation journal or dispatch',async()=>{let body;const c=setup(async(_p,v)=>{body=v;return {maximumCalls:2};});await c.preflight(intent);assert.equal(body.action,'preflight');assert.equal(c.pending(),null);});
test('lost create acknowledgement recovers original ID without another POST',async()=>{let writes=0;const c=setup(async(_p,v)=>{if(v){writes++;throw {code:'NETWORK_RESULT_UNKNOWN'};}return {batches:[batch()]};});await assert.rejects(c.mutate('create',{action:'create',intent}));assert.equal(c.pending().body.intent.operationId,'create-1');await assert.rejects(c.mutate('create',{action:'create',intent:{...intent,operationId:'other'}}));await c.recover();assert.equal(c.pending(),null);assert.equal(writes,1);});
test('missing create remains uncertain and is not silently replayed',async()=>{let writes=0;const c=setup(async(_p,v)=>{if(v){writes++;throw {code:'NETWORK_RESULT_UNKNOWN'};}return {batches:[]};});await assert.rejects(c.mutate('create',{action:'create',intent}));await c.recover();assert.ok(c.pending());assert.equal(writes,1);});
test('browser storage failure blocks network mutations',async()=>{let writes=0;const c=setup(async()=>{writes++;},{getItem:()=>null,setItem:()=>{throw Error();},removeItem:()=>{}});await assert.rejects(c.mutate('create',{action:'create',intent}),{code:'COMPARISON_STORAGE_UNAVAILABLE'});assert.equal(writes,0);});
test('explicit budget rejection clears the intent but server failures retain it',async()=>{for(const [status,kept]of [[400,false],[503,true]]){const c=setup(async()=>{throw {httpStatus:status,code:'COMPARISON_BUDGET_EXHAUSTED'};});await assert.rejects(c.mutate('create',{action:'create',intent}));assert.equal(!!c.pending(),kept);}});
test('receipt comparison ignores key order but detects changed model parameters',()=>{const r={action:'create',body:{intent}},b=batch();assert.equal(comparisonReceiptMatches(r,b),true);b.plan.intent.candidates[0].choice.parameterProfile.parameters.values.temperature.value=.9;assert.equal(comparisonReceiptMatches(r,b),false);});
test('adoption uses frozen context revision, not candidate progress revision',()=>{assert.deepEqual(decisionBody(batch(),'adopt','a','pick'),{operationId:'pick',expectedContextRevision:17,candidateId:'a'});assert.deepEqual(decisionBody(batch(),'stop',null,'stop'),{operationId:'stop',expectedRevision:4});});
test('lost adoption receipt is recovered as the same immutable decision',async()=>{let posts=0;const body=decisionBody(batch(),'adopt','a','pick'),saved={...batch(),decision:{kind:'adopt',intent:body}};const c=setup(async(_p,v)=>{if(v){posts++;throw {code:'NETWORK_RESULT_UNKNOWN'};}return {batches:[saved]};});await assert.rejects(c.mutate('adopt',body,'compare-a'));await c.recover();assert.equal(c.pending(),null);assert.equal(posts,1);});
test('other-tab different adoption never acknowledges this pending operation',()=>{assert.equal(comparisonReceiptMatches({action:'adopt',body:decisionBody(batch(),'adopt','a','pick')},{...batch(),decision:{kind:'adopt',intent:decisionBody(batch(),'adopt','b','other')}}),false);});
test('completed adoption stays pending until remaining work settles',()=>{const b={...batch(),decision:{kind:'adopt'}};assert.equal(comparisonProgress(b),'已选定 · 正在收束');b.status='settled';assert.equal(comparisonProgress(b),'已采用 · 对比已收束');});
test('six card candidate preview uses the reviewed renderer and an inert frame',async()=>{const raw='<div class="page"><details><summary>信息</summary>完整正文</details><script>fetch("https://evil.invalid")</script></div>';for(const card of ['earth','god','xingjian','ancient','cultivation','lord-of-mysteries']){const {frame}=await comparisonFrame(card,raw);assert.match(frame,/script-src 'none'/);assert.match(frame,/完整正文/);assert.doesNotMatch(frame,/<script|evil.invalid/);}});
test('God image mapping and checkbox animation markup survive isolated preview',async()=>{const {frame}=await comparisonFrame('god','<div class="aa"><label><input type="checkbox" class="bb"><img src="https://catai.wiki/31a557a3-828e-44e7-bcc7-972771e4bb00/w=3000"></label><div class="cc"><div class="dd">提示</div></div></div>');assert.match(frame,/src="data:image\/webp;base64,/);assert.match(frame,/type="checkbox"/);assert.match(frame,/@keyframes/);assert.doesNotMatch(frame,/src="https:/);});

test('comparison placeholders do not masquerade as unknown story or memory requests',()=>{
 const operations={batch:{status:'pending'}};
 assert.equal(unconfirmedOutsideComparison(operations,['batch']),false);
 assert.equal(unconfirmedOutsideComparison(operations,[]),true);
 assert.equal(unconfirmedOutsideComparison({...operations,memory:{status:'unknown'}},['batch']),true);
 assert.equal(unconfirmedOutsideComparison(operations,['batch'],true),true);
 assert.equal(unconfirmedOutsideComparison({batch:{status:'complete'}},[]),false);
});

test('future config acknowledgement recovery is a GET with original complete intent, never another write',async()=>{
 const body={operationId:'future-1',candidateId:'a',expectedRevision:3,expectedModelRevision:1,expectedParameterRevision:0,expectedAuthorizationRevision:'a'.repeat(64)};
 const receipt={status:'applied',intent:{sessionId:'s',batchId:'compare-a',...body}};let writes=0,found=false;
 const api=async(path,value)=>{if(value){writes++;throw {code:'NETWORK_RESULT_UNKNOWN'};}if(path.endsWith('/future/future-1'))return found?receipt:{status:'not_applied'};return {batches:[batch()]};};
 const c=setup(api);await assert.rejects(c.mutate('future',body,'compare-a'));await c.recover();assert.ok(c.pending());
 found=true;await c.recover();assert.equal(c.pending(),null);assert.equal(writes,1);
 const record={sessionId:'s',batchId:'compare-a',action:'future',body};
 assert.equal(comparisonReceiptMatches(record,receipt),true);assert.equal(comparisonReceiptMatches(record,{...receipt,intent:{...receipt.intent,candidateId:'b'}}),false);
});

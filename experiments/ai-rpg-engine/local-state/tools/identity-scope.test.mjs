import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {assembleToolsContext} from './adapter.mjs';
import {ToolsHost} from './host.mjs';
import {applyOperations,candidate} from '../rules.mjs';
import {exampleState} from '../example.mjs';
const strict={strictRelations:true},scope={...strict,quantityOnly:{id:'medicine',delta:-2}};
const quantity={type:'quantity',id:'medicine',delta:-2,reason:'按本轮要求服药'};
const add=(id,name='周禾')=>({type:'addRelation',id,name,reason:'登记'});
const relation={type:'relation',id:'zhou',axis:'relation',event:'help',delta:6,reason:'帮忙'};
const reply=operations=>({status:'completed',finishReason:'stop',provider:'offline-native-simulator',usage:{kind:'simulated',costUSD:0},message:{role:'assistant',content:'你服下两份养息丹，周禾点头。<local_relation id="zhou"></local_relation><state_delta>'+JSON.stringify({operations})+'</state_delta>'}});
const record=()=>({state:exampleState(),stateRevision:0,config:{profiles:[],mods:[]},source:{history:[]},turns:[],events:[]});

test('identity index supplies Zhou even when input and history omit him; strict scope reaches both stages',async()=>{
 const c=await assembleToolsContext(record(),'核对丹药与功法，然后服用两份；其他状态不变',{quantityOnly:scope.quantityOnly});
 assert.deepEqual(c.query.relations,[]);
 assert.ok(c.messages[0].content.includes('[{"id":"zhou","name":"周禾"}]'));
 assert.ok(c.messages[0].content.includes('不允许下划线'));
 assert.ok(c.messages[0].content.includes('"type":"quantity","id":"medicine","delta":-2'));
 assert.deepEqual(c.statePolicy,scope);
 assert.equal(c.messages.filter(m=>m.role==='user').length,1);
 await assert.rejects(()=>assembleToolsContext(record(),'服药',{quantityOnly:{id:'missing',delta:-2}}),{code:'STATE_CHANGE_SCOPE'});
});

test('invalid ID, existing ID and same-name duplicate are distinct from a new character',()=>{
 const s=exampleState();
 assert.throws(()=>applyOperations(s,[add('zhou_he')],strict),{code:'STATE_ENTRY_ID'});
 for(const op of [add('zhou'),add('zhou-he'),add('new-id',' 周禾 ')])
  assert.throws(()=>applyOperations(s,[op],strict),{code:'STATE_RELATION_ALREADY_REGISTERED'});
 assert.throws(()=>applyOperations(s,[add('chen','陈溪'),add('chen-two','陈溪')],strict),{code:'STATE_RELATION_ALREADY_REGISTERED'});
 const updated=applyOperations(s,[relation],strict);assert.equal(updated.state.relations[0].relation,6);
 assert.equal(applyOperations(s,[add('chen','陈溪')],strict).state.relations.length,2);
 assert.equal(s.relations.length,1);
 // Old mode retains its historical interpretation; new guards are opt-in through v2 context.
 assert.equal(applyOperations(s,[add('zhou-he')]).state.relations.length,2);
});

test('quantity-only is exact, rejects extra operations atomically rather than silently filtering',()=>{
 const s=exampleState(),ok=applyOperations(s,[quantity],scope),expected=structuredClone(s);expected.items[0].quantity=3;
 assert.deepEqual(ok.state,expected);
 for(const ops of [[],[quantity,relation],[quantity,add('new-person','新人')],[{...quantity,id:'bag'}],[{...quantity,delta:-1}],[{...quantity,delta:-3},{...quantity,delta:1}],[quantity,{type:'set',field:'stamina',value:'充盈',reason:'额外恢复'}]])
  assert.throws(()=>applyOperations(s,ops,scope),{code:'STATE_CHANGE_SCOPE'});
 assert.deepEqual(s,exampleState());
 assert.throws(()=>candidate(s,{status:'completed',finishReason:'stop',text:reply([quantity,relation]).message.content},scope),{code:'STATE_CHANGE_SCOPE'});
});

async function setup(t,operations,{unknown=false}={}){
 const dir=await mkdtemp(join(tmpdir(),'rpg-identity-'));const host=await ToolsHost.open({directory:dir,enabled:true});
 t.after(async()=>{await host.close();assert.ok(dir.startsWith(join(tmpdir(),'rpg-identity-')));await rm(dir,{recursive:true,force:true});});
 let calls=0;const generate=host.provider.generate.bind(host.provider);
 host.provider.generate=async(...args)=>{
  calls++;
  if(args.at(-1).stage===1)return generate(...args);
  return unknown?{status:'unknown'}:reply(operations);
 };
 host.provider.lookup=async()=>reply(operations);
 const r=await host.create({operationId:'init',initial:exampleState(),confirmed:true});
 return {host,r,calls:()=>calls};
}
const input='先查询丹药，再服用两份养息丹；其他状态不变';
test('native query and final commit keep every unrelated field unchanged; duplicate cannot replace scope',async t=>{
 const {host,r,calls}=await setup(t,[quantity]);
 const action={operationId:'consume',revision:0,input};
 const n=await host.turn(r.id,action,{quantityOnly:scope.quantityOnly});
 assert.equal(n.operations.consume.status,'completed');assert.equal(calls(),2);
 const expected=exampleState();expected.items[0].quantity=3;assert.deepEqual(n.state,expected);
 assert.ok(n.operations.consume.native.stages[1].request.messages[0].content.includes('本轮唯一允许的状态变化'));
 await host.turn(r.id,action,{quantityOnly:scope.quantityOnly});assert.equal(calls(),2);
 await assert.rejects(()=>host.turn(r.id,action,{quantityOnly:{id:'medicine',delta:-1}}),{code:'STATE_OPERATION_COLLISION'});
});

test('extra relationship cannot pass by using a valid ID; recovery retains frozen constraint',async t=>{
 const {host,r,calls}=await setup(t,[quantity,relation],{unknown:true});
 let n=await host.turn(r.id,{operationId:'consume',revision:0,input},{quantityOnly:scope.quantityOnly});
 assert.equal(n.operations.consume.status,'unknown');assert.equal(calls(),2);
 n=await host.recover(r.id);
 assert.equal(n.operations.consume.error,'STATE_CHANGE_SCOPE');assert.equal(n.operations.consume.status,'rejected');
 assert.deepEqual(n.state,exampleState());assert.equal(n.turns.length,0);
 await host.recover(r.id);assert.equal(calls(),2);
});

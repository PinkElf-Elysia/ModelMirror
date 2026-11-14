import test from 'node:test';import assert from 'node:assert/strict';
import {queryLocalState,validateQueries} from './query.mjs';
import {exampleState} from '../example.mjs';import {hash} from '../definition.mjs';
import {loadToolsAdapter,assembleToolsContext} from './adapter.mjs';import {loadAdapter} from '../adapter.mjs';
test('query read only, category, fuzzy name, quantities and unknown coverage',()=>{
 const s=exampleState(),before=hash(s);s.balances.mid=null;const frozen=hash(s);
 const r=queryLocalState(s,2,{queries:[{domain:'items',kind:'丹药',name:'养息'},{domain:'balances',id:'mid'},{domain:'items',name:'沉香'}]});
 assert.equal(r.results[0].entries[0].quantity,5);assert.equal(r.results[1].status,'unset');assert.equal(r.results[2].status,'no-match');assert.equal(hash(s),frozen);assert.ok(before);
 assert.equal(r.stateRevision,2);assert.equal(r.results[0].coverage,'complete-registered-scope');
});
test('limit explicit; reject foreign scope, arbitrary filters and oversized batch',()=>{
 const s=exampleState();const r=queryLocalState(s,0,{queries:[{domain:'items',limit:1}]});assert.equal(r.results[0].coverage,'partial');
 for(const v of [{queries:[],sessionId:'other'},{queries:[]},{queries:Array(4).fill({domain:'items'})},{queries:[{domain:'items',limit:51}]},{queries:[{domain:'items',path:'/etc'}]},{queries:[{domain:'balances',kind:'丹药'}]}])assert.throws(()=>validateQueries(v));
});
test('adapter version and actual assembly preserve source but not old binding',async()=>{
 const old=await loadAdapter(),a=await loadToolsAdapter();assert.equal(a.system,old.system);assert.notEqual(hash(a.binding),hash(old.binding));
 const r={source:{history:[]},turns:[],events:[],state:exampleState(),stateRevision:0,config:{profiles:[],mods:[]}};
 const c=await assembleToolsContext(r,'查看丹药');assert.equal(c.tools[0].function.name,'query_local_state');assert.equal(c.tool_choice,'auto');assert.match(c.messages[0].content,/不适用于工具调用阶段/);
});

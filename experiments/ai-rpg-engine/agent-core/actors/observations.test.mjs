import {readFile} from 'node:fs/promises';
import test from 'node:test';import assert from 'node:assert/strict';
import {project,query} from './observations.mjs';
import {assembleActor} from './context.mjs';
import {blankCard} from '../../character/card.mjs';
const world={id:'world-a',stateRevision:1,state:{items:[{id:'medicine',name:'养息丹',kind:'丹药',quantity:3,secret:'ITEM_SECRET'}],private:'WORLD_SECRET'},
 events:[{id:'event-a',kind:'action',stateRevision:1,before:{secret:'BEFORE_SECRET'},receipt:{result:{actionId:'use_medicine',itemId:'medicine',name:'养息丹',label:'服用',quantity:2,before:5,after:3}}}]};
const actor=(witness,inventory)=>({binding:{worldId:'world-a',joinedAtVersion:0,grant:{version:1,witness,inventory}}});
test('observed consumption does not disclose remaining balance or raw receipt',()=>{
 const f=project(world,actor(true,false));assert.equal(f.events[0].quantityUsed,2);assert.deepEqual(f.inventory,[]);
 assert.doesNotMatch(JSON.stringify(f),/SECRET|before|after|quantity":3/);
 assert.deepEqual(query(f,{domain:'inventory',id:'medicine'}),query(f,{domain:'inventory',id:'nonexistent'}));
});
test('inventory independent grant; absent role and joining later see no prior event',()=>{
 const f=project(world,actor(false,true));assert.equal(f.events.length,0);assert.equal(query(f,{domain:'inventory'}).items[0].quantity,3);
 const a=actor(true,false);a.binding.joinedAtVersion=1;assert.equal(project(world,a).events.length,0);
 assert.throws(()=>project(world,{binding:{worldId:'another'}}),{code:'ACTOR_WORLD_MISMATCH'});
});
test('query rejects cross-session paths, writes and identifiers; reads are copies',()=>{
 const f=project(world,actor(true,true));
 for(const args of [{domain:'inventory',session:'other'},{domain:'world'},{domain:'inventory',id:'../secret'}])assert.throws(()=>query(f,args));
 query(f,{domain:'inventory'}).items[0].quantity=999;assert.equal(f.inventory[0].quantity,3);
 assert.equal(query(f,{domain:'inventory',name:'不存在'}).status,'no-match');
});
test('merged character assembly + observation has no owner data; no observation book retrigger',()=>{
 const card=blankCard();Object.assign(card.data,{name:'林舟',first_mes:'你好',description:'成年修士',creator_notes:'AUTHOR_NOT_CONTEXT',
 character_book:{extensions:{},entries:[{keys:['养息丹'],content:'BOOK_SHOULD_NOT_TRIGGER',extensions:{},enabled:true,insertion_order:0}]}});
 const a={...actor(true,false),card,opening:'你好',player:'访客',history:[]};
 const result=assembleActor(a,project(world,a),'你看到了什么？');
 assert.match(JSON.stringify(result.request),/养息丹/);assert.doesNotMatch(JSON.stringify(result.request),/SECRET|AUTHOR_NOT_CONTEXT|BOOK_SHOULD_NOT_TRIGGER/);
 assert.equal(result.trace.inputSource,'player-claim');
});

test('concrete world shape stays in adapter, not the public coordinator',async()=>{
 const source=await readFile(new URL('./host.mjs',import.meta.url),'utf8');
 assert.doesNotMatch(source,/use_medicine|cultivation|\.state\.items/);
 assert.match(source,/worldPort\.project/);
});

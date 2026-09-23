import test from 'node:test';
import assert from 'node:assert/strict';
import {characters,validFields,validChange,requireLibrary,changeEntries,applyModelChanges,recallMemories,invoke} from './memory-palace.mjs';
const value=(content='已经抵达书店；仍未收到地图。',extra={})=>({name:'人物线索',keywords:['周岚','Map'],match:'any',roles:['user','assistant'],content,enabled:true,...extra});
const add=(library,id,v=value())=>changeEntries(library,{action:'create',value:v},id);
const unlocked=(library,id)=>changeEntries(library,{action:'protect',id,baseVersion:library.find(e=>e.id===id).version,protected:false});
const model=(library,ops,ids=[])=>applyModelChanges(library,ops,{from:1,through:3,outputHash:'a'.repeat(64),newIds:ids});
const proposal=(type='create',extra={})=>{const {enabled,...v}=value();return {type,...v,sourceTurns:[1],...extra};};
test('strict Unicode limits, roles, keys and proposal fields; no card-specific schema',()=>{
 assert.equal(characters('😀'),1);assert.equal(validFields(value('😀'.repeat(2000))),true);assert.equal(validFields(value('😀'.repeat(2001))),false);
 for(const v of [value(' '),value('ok',{keywords:[]}),value('ok',{keywords:['Map','map']}),value('ok',{keywords:[' x']}),value('ok',{keywords:['x'.repeat(81)]}),value('ok',{keywords:Array.from({length:21},(_,i)=>''+i)}),value('ok',{roles:[]}),value('ok',{roles:['system']}),value('ok',{roles:['user','user']}),value('ok',{system:'injected'}),value('ok',{content:'\ud800'})])assert.equal(validFields(v),false);
 assert.equal(validFields(value('plain text',{name:'unrelated entity',keywords:['item']})),true);
 assert.equal(validChange({action:'create',value:value(),protected:false}),false);
 assert.throws(()=>invoke({capability:'provider.dispatch',session:{id:'s'}}));
});
test('manual create/edit protects; explicit unlock preserves origin; restore creates new version',()=>{
 let book=add([],'a');const before=structuredClone(book);book=unlocked(book,'a');assert.equal(book[0].protected,false);assert.equal(book[0].origin,'manual');assert.deepEqual(before[0].version,1);
 book=changeEntries(book,{action:'update',id:'a',baseVersion:2,value:value('新状态')});assert.equal(book[0].protected,true);assert.equal(book[0].revisions[0].value.content,before[0].value.content);
 book=changeEntries(book,{action:'delete',id:'a',baseVersion:3});assert.equal(requireLibrary(book).count,0);
 assert.throws(()=>changeEntries(book,{action:'update',id:'a',baseVersion:4,value:value()}),/MEMORY_ENTRY_DELETED/);
 book=changeEntries(book,{action:'restore',id:'a',baseVersion:4,version:1});assert.equal(book[0].version,5);assert.equal(book[0].protected,true);assert.equal(book[0].value.content,before[0].value.content);assert.equal(book[0].revisions[4].source.restoredFrom,1);
 assert.throws(()=>changeEntries(book,{action:'delete',id:'a',baseVersion:1}),/MEMORY_ENTRY_CONFLICT/);
});
test('model proposals atomic; protected targets, duplicate targets and foreign source references reject',()=>{
 let book=add([],'a');const before=structuredClone(book);
 assert.throws(()=>model(book,[proposal(),proposal('update',{id:'a',baseVersion:1})],['b']),/MEMORY_PROPOSAL_FORBIDDEN/);assert.deepEqual(book,before);
 book=unlocked(book,'a');const update=proposal('update',{id:'a',baseVersion:2,content:'人物说法改变，旧承诺仍未兑现。'});
 const next=model(book,[update]);assert.equal(next[0].value.content,update.content);assert.equal(next[0].revisions[0].value.content,before[0].value.content);assert.equal(next[0].origin,'manual');
 for(const ops of [[update,update],[proposal('update',{id:'missing',baseVersion:1})],[proposal('update',{id:'a',baseVersion:1})],[proposal('update',{id:'a',baseVersion:2,protected:false})],[proposal('delete',{id:'a',baseVersion:2})],[proposal('create',{sourceTurns:[4]})]])assert.throws(()=>model(book,ops,['new']));
 assert.deepEqual(model(book,[]),book);
});
test('disabled automatic entries cannot be reenabled by model; raw source metadata retained',()=>{
 let book=model([],[proposal()],['a']);book=changeEntries(book,{action:'update',id:'a',baseVersion:1,value:value('暂时停用',{enabled:false})});book=unlocked(book,'a');
 const next=model(book,[proposal('update',{id:'a',baseVersion:3})]);assert.equal(next[0].value.enabled,false);assert.equal(next[0].origin,'model');assert.equal(next[0].revisions[0].source.outputHash,'a'.repeat(64));
 assert.deepEqual(recallMemories({entries:next,turns:[],input:'周岚'}).entries,[]);
});
test('100 entries / 60000 codepoints include disabled entries; restore obeys capacity',()=>{
 let book=[];for(let i=0;i<100;i++)book=add(book,'e-'+i,value('x',{enabled:false}));assert.equal(requireLibrary(book).count,100);assert.throws(()=>add(book,'overflow'),/MEMORY_CAPACITY_EXCEEDED/);assert.throws(()=>model(book,[]),/MEMORY_CAPACITY_FULL/);
 book=changeEntries(book,{action:'delete',id:'e-0',baseVersion:1});book=add(book,'replacement');assert.throws(()=>changeEntries(book,{action:'restore',id:'e-0',baseVersion:2,version:1}),/MEMORY_CAPACITY_EXCEEDED/);
 let full=[];for(let i=0;i<30;i++)full=add(full,'u-'+i,value('😀'.repeat(2000)));assert.equal(requireLibrary(full).bodyCharacters,60000);assert.throws(()=>add(full,'one-more',value('x')),/MEMORY_CAPACITY_EXCEEDED/);
});
test('literal case-insensitive aliases, AND across messages, role filters and no regex',()=>{
 let book=add([],'a',value('fact',{match:'all'}));
 const turns=[{input:'周岚',raw:'A MAP is mentioned.'}];assert.equal(recallMemories({entries:book,turns,input:'unrelated'}).entries.length,1);
 book=add(book,'b',value('no regex',{keywords:['.*']}));assert.equal(recallMemories({entries:book,turns,input:'nothing'}).entries.length,1);
 book=add(book,'c',value('AI only',{keywords:['周岚'],roles:['assistant']}));const r=recallMemories({entries:book,turns,input:'周岚'});assert.equal(r.record.results.some(x=>x.id==='c'),false);
 assert.equal(recallMemories({entries:book,turns:[{input:'周',raw:'岚'}],input:'MAP'}).entries.length,0);
 const twice=add(add([],'same-1'), 'same-2');assert.equal(recallMemories({entries:twice,turns:[],input:'周岚'}).entries.length,2,'same name is not implicit dedup');
});
test('scan last ten complete turns independent of window; never character/prefix/summary fields',()=>{
 const book=add([],'a',value('fact',{keywords:['trigger']}));
 const turns=Array.from({length:11},(_,i)=>({input:i===0?'trigger':'quiet',raw:'quiet',assembledUser:'trigger',summary:'trigger'}));
 assert.equal(recallMemories({entries:book,turns:turns.slice(0,10),input:''}).entries.length,1);
 assert.equal(recallMemories({entries:book,turns,input:''}).entries.length,0);
 assert.equal(recallMemories({entries:book,turns,input:'TRIGGER'}).entries.length,1);
 for(const turn of [{input:'pending'},{input:'trigger',raw:''},{input:'trigger',raw:'late',status:'cancelled'}])assert.throws(()=>recallMemories({entries:book,turns:[turn],input:''}),/MEMORY_HISTORY_INVALID/);
});
test('current input / latest history / stable ID ordering; no recursion and no input mutation',()=>{
 let book=[];for(const [id,keyword] of [['z','early'],['b','late'],['a','late'],['c','current']])book=add(book,id,value('current',{keywords:[keyword]}));
 const turns=[{input:'early',raw:'late'}],before=structuredClone({book,turns});const r=recallMemories({entries:book,turns,input:'current'});
 assert.deepEqual(r.record.results.map(x=>x.id),['c','a','b','z']);r.entries[0].content='mutated';assert.deepEqual({book,turns},before);
 assert.equal(recallMemories({entries:book,turns:[],input:'early'}).entries.length,1,'injected current is not recursively scanned');
});
test('whole-entry 8000 body cap skips oversized remaining entry then admits short one; max ten',()=>{
 let book=[];for(let i=0;i<4;i++)book=add(book,'a-'+i,value('x'.repeat(i===3?1999:2000),{keywords:['k']}));book=add(book,'b',value('xx',{keywords:['k']}));book=add(book,'c',value('x',{keywords:['k']}));
 const r=recallMemories({entries:book,turns:[],input:'k'});assert.equal(r.record.bodyCharacters,8000);assert.equal(r.record.results.find(x=>x.id==='b').reason,'body-limit');assert.equal(r.record.results.find(x=>x.id==='c').selected,true);
 book=[];for(let i=0;i<11;i++)book=add(book,'e-'+i,value('x',{keywords:['k']}));const ten=recallMemories({entries:book,turns:[],input:'k'});assert.equal(ten.entries.length,10);assert.equal(ten.record.results.at(-1).reason,'entry-limit');
});
test('corrupted current/revision values fail closed',()=>{
 const book=add([],'a');book[0].value.content='tampered';assert.throws(()=>requireLibrary(book),/MEMORY_LIBRARY_INVALID/);
});

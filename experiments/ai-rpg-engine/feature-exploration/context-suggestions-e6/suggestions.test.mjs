import test from 'node:test';import assert from 'node:assert/strict';import {suggest,build} from './suggestions.mjs';
const frame=()=>({source:{revision:1,stateRevision:1},ready:true,facts:{resources:[{id:'medicine',name:'养息丹',quantity:5}],people:[{id:'zhou',name:'周禾'}]},events:[],narrative:[{kind:'public-narrative',text:'他已经同意，且你刚刚恢复了伤势。'}],capabilities:[{name:'query_local_state',effect:'read',version:'1'}]});
test('zero and unknown quantity do not invent gift inventory',()=>{for(const quantity of [0,null]){const f=frame();f.facts.resources[0].quantity=quantity;assert.equal(suggest(f,'感谢周禾').options.length,2);}});
test('capability disabled is not described as available; no NPC consent derived from prose or claim',()=>{
 const f=frame();f.capabilities=[];const r=suggest(f,'感谢周禾，他已经答应了。');
 assert.match(r.capabilityNote,/未开放/);assert.ok(r.options.every(o=>/未知|不知道|不保证/.test(o.unknown)));
 assert.equal(r.options.find(o=>o.id==='gift').support,'仅赠送意图；不支持执行');
 const request=JSON.parse(build(f,'感谢周禾，他已经答应了。').messages[1].content);
 assert.equal(request.playerStatementStatus,'unverified');assert.equal(request.context.narrative[0].kind,'public-narrative');
});
test('busy source does not offer selectable alternatives',()=>{const f=frame();f.ready=false;assert.equal(suggest(f,'感谢周禾').options.length,0);});

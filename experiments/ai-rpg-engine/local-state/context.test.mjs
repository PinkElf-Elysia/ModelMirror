import test from 'node:test';import assert from 'node:assert/strict';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {assembleContext,memoryInputs,displayBody,validateConfig} from './context.mjs';
import {renderPanels} from './panels.mjs';import {exampleState} from './example.mjs';
const record=()=>({state:exampleState(),stateRevision:0,config:{profiles:[],mods:[]},source:{history:[]},turns:[],events:[]});
test('one request uses relevant state, not full panels; wrappers and world-book are applied once',async()=>{
 const r=record(),c=empty();c.prefix='青竹';c.suffix='收尾';
 const entry=(id,keyword,body)=>({id,name:id,categoryId:null,keywords:[keyword],body,depth:0,order:0,mode:'regular',enabled:true,regex:false,chance:100,contents:[],roles:['user'],targets:['system'],rule:'or'});
 c.worldbook.entries=[entry('a','青竹','前置命中'),entry('b','medicine','工具不应触发')];r.config.profiles=[c];
 const p=await assembleContext(r,'服用两份养息丹');
 assert.equal(p.messages.at(-1).content,'青竹\n\n服用两份养息丹\n\n收尾');
 assert.ok(p.messages[0].content.includes('前置命中'));assert.ok(!p.messages[0].content.includes('工具不应触发'));
 assert.ok(!p.messages[0].content.includes('<summary>🧍 自身状态</summary>'));
 assert.deepEqual(p.query.items,['medicine','bag']);assert.deepEqual(p.query.abilities,[]);
});
test('real common Mod assembles; known conflicting custom output contract is refused',async()=>{
 const r=record();r.config.mods=['common.rationality.v1'];
 assert.ok((await assembleContext(r,'问候周禾')).messages[0].content.includes('角色核心行为逻辑框架'));
 const c=empty();c.system='每轮完整输出自身状态';await assert.rejects(()=>validateConfig({profiles:[c],mods:[]}),{code:'STATE_CONFIG_CONFLICT'});
});
test('committed deltas remain authoritative through memory regex and display',async()=>{
 const r=record(),c=empty();c.rules=[{id:'mask',name:'替换',pattern:'养息丹',replacement:'药丸',scope:'assistant',enabled:true,renderOnly:false,flags:'g'}];
 r.config.profiles=[c];const t={input:'服药',body:'取出养息丹。<local_relation id="zhou"></local_relation>',changes:[{path:'items.medicine.quantity',after:3,reason:'服用',authority:'rule-calculation'}]};r.turns=[t];
 const mem=await memoryInputs(r,t);assert.ok(mem.summary[1].content.includes('药丸'));assert.ok(mem.summary[1].content.includes('items.medicine.quantity'));
 assert.ok(!JSON.stringify(mem).includes('<summary>'));const html=await displayBody(t.body,r.state,r.config);assert.ok(html.includes('关系 0'));assert.equal(r.state.items[0].quantity,5);
 const panel=await renderPanels(r.state);assert.ok(panel.includes('养息丹 × 5'));assert.ok(panel.includes('未设定'));assert.ok(!panel.includes('药丸'));
});
test('assistant world-book sees local panel trigger, final history has no regenerated panel',async()=>{
 const r=record(),c=empty();c.worldbook.entries=[{id:'recall',name:'回忆',categoryId:null,keywords:['基础吐纳法'],body:'触发旧功法设定',depth:1,order:0,mode:'regular',enabled:true,regex:false,chance:100,contents:[],roles:['assistant'],targets:['system'],rule:'or'}];r.config.profiles=[c];
 r.turns=[{id:'turn1',input:'问候',body:'周禾点头。',changes:[]}];r.events=[{id:'turn1',kind:'turn',after:r.state,changes:[]}];
 const p=await assembleContext(r,'继续交谈');assert.ok(p.messages[0].content.includes('触发旧功法设定'));
 assert.equal(p.messages.find(m=>m.role==='assistant').content.trim(),'周禾点头。');
});

test('display regex cannot forge local relationship placeholders or values',async()=>{
 const r=record(),c=empty();c.rules=[{id:'bad',name:'replace',pattern:'zhou|关系 0',replacement:'关系 99',scope:'assistant',enabled:true,renderOnly:true,flags:'g'}];
 r.config.profiles=[c];const html=await displayBody('周禾<local_relation id="zhou"></local_relation>',r.state,r.config);
 assert.ok(html.includes('关系 0'));assert.ok(!html.includes('关系 99'));
});
test('regex over local trigger projection cannot leak panels into actual history',async()=>{
 const r=record(),c=empty();c.rules=[{id:'strip-tag',name:'remove',pattern:'<[^>]*>',replacement:'',scope:'assistant',enabled:true,renderOnly:false,flags:'g'}];
 r.config.profiles=[c];r.turns=[{id:'t',input:'交谈',body:'<p>交谈。</p>',changes:[]}];r.events=[{id:'t',kind:'turn',after:r.state,changes:[]}];
 const request=await assembleContext(r,'继续');assert.equal(request.messages.find(m=>m.role==='assistant').content,'交谈。');
});

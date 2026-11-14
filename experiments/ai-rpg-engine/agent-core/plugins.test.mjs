import {test} from 'node:test';import assert from 'node:assert/strict';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {fixture,operationId} from './test-support.mjs';import {assembleToolsContext} from '../local-state/tools/adapter.mjs';
test('actual plugin combination preserves triggers, wrappers, Mod and committed memory through native continuation',async t=>{
 const f=await fixture(t,'cultivation'),h=f.host,c=empty();c.prefix='青竹';c.suffix='收尾';
 const entry=(id,keyword,body,roles=['user'],depth=0)=>({id,name:id,categoryId:null,keywords:[keyword],body,depth,order:0,mode:'regular',enabled:true,regex:false,chance:100,contents:[],roles,targets:['system'],rule:'or'});
 c.worldbook.entries=[entry('prefix','青竹','前置词只装配一次'),entry('tool','tool_call_id','工具不应成为用户输入'),entry('ability','基础吐纳法','面板仅供旧触发使用',['assistant'],1)];
 c.rules=[{id:'mask',name:'展示药名',pattern:'养息丹',replacement:'药丸',scope:'assistant',enabled:true,renderOnly:false,flags:'g'}];
 let r=await h.create({operationId:operationId(),initial:h.adapter.initial(),confirmed:true,config:{profiles:[c],mods:['common.rationality.v1']}});
 const input='先查询丹药，再服用两份养息丹',expected=await assembleToolsContext(r,input);
 r=await h.turn(r.id,{operationId:'op-plugin-turn',revision:r.revision,input},{contextOptions:{quantityOnly:{id:'medicine',delta:-2}}});
 assert.equal(r.pending,null);assert.equal(r.state.items[0].quantity,3);assert.equal(r.state.relations[0].relation,0);
 const op=r.operations['op-plugin-turn'];assert.deepEqual(op.context.messages.at(-1),expected.messages.at(-1));
 const first=op.stages[0].request,second=op.stages[1].request;
 assert.deepEqual(second.messages.slice(0,first.messages.length),first.messages);assert.equal(second.messages.filter(m=>m.role==='user').length,1);
 assert.match(first.messages[0].content,/角色核心行为逻辑框架/);assert.match(first.messages[0].content,/前置词只装配一次/);assert.doesNotMatch(first.messages[0].content,/工具不应成为用户输入/);
 const next=await h.adapter.assemble(r,'继续交谈');assert.match(next.messages[0].content,/面板仅供旧触发使用/);assert.doesNotMatch(next.messages.find(m=>m.role==='assistant').content,/<summary>|<state_delta>|tool_call_id/);
 const memory=JSON.stringify(await h.adapter.memory(r,r.turns[0]));assert.match(memory,/药丸/);assert.match(memory,/items.medicine.quantity/);assert.doesNotMatch(memory,/thought_signature|tool_call_id/);
 const view=await h.view(r.id);assert.match(view.messages[0].html,/药丸/);assert.match(view.panels,/养息丹 × 3/);
});
test('known conflicting output instruction rejected before dispatch',async t=>{
 const f=await fixture(t,'cultivation'),c=empty();c.system='每轮完整输出自身状态';
 await assert.rejects(f.host.create({operationId:operationId(),initial:f.host.adapter.initial(),confirmed:true,config:{profiles:[c],mods:[]}}),{code:'STATE_CONFIG_CONFLICT'});assert.equal(f.host.provider.calls,0);
});

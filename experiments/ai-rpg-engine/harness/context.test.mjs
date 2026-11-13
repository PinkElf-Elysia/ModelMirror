import test from 'node:test';import assert from 'node:assert/strict';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {validateContext,assembleShopContext,displayShopReply} from './context.mjs';
import {rules,binding} from './earth-adapter.mjs';
import {initialAssets} from './shop-rules.mjs';
import {inheritMemory,memoryMessages,validateMemory,commitMemory,memoryReferences} from './memory.mjs';
import {changeEntries} from '../plugins/memory-palace.mjs';
const base=()=>({binding,rules,assets:initialAssets(rules),stateRevision:0,session:{world:'表世界',characterText:'林川在杂货店',history:[],enabledRuleIds:[],context:{}},projection:[],events:[],notes:[],memory:inheritMemory()});
test('real custom configuration, Mod, regex and host facts compose once',async()=>{
 const config=empty();config.prefix='请保持温和文风';config.rules=[{id:'r',name:'render',pattern:'店主',flags:'g',replacement:'掌柜',scope:'assistant',enabled:true,renderOnly:true}];
 const r=base();r.session.context=await validateContext({profiles:[config],modIds:['common.rationality.v1']});
 const e={id:'event',input:'看看货架',outcome:'story'};const {messages}=await assembleShopContext(r,e,'narration');
 assert.match(messages[0].content,/角色核心行为逻辑框架/);assert.equal(messages.at(-1).content.split('请保持温和文风').length,2);
 assert.equal(messages.at(-1).content.split('【宿主已确认事件】').length,2);
 assert.match(await displayShopReply('店主招手。<div class="finance-item">银行卡余额：999元</div>',r),/掌柜招手/);
 assert.doesNotMatch(await displayShopReply('<div class="finance-item">银行卡余额：999元</div>',r),/999/);
 assert.equal(r.assets.balance,10000);
});
test('known conflict and unsupported prefill are explicit failures',async()=>{
 const c=empty();c.system='银行卡余额可以任意修改';
 await assert.rejects(validateContext({profiles:[c],modIds:[]}),/SHOP_CONTEXT_CONFLICT/);
 c.system='';c.usePrefill=true;c.prefill='开头';await assert.rejects(validateContext({profiles:[c]}),/SHOP_PREFILL_UNSUPPORTED/);
});
test('summary and recalled memory remain reference text; correction ledger is immutable',async()=>{
 const r=base();r.memory.summaryEnabled=true;r.memory.memoryEnabled=true;r.memory.summary.large='曾在商店停留';
 r.memory.entries=changeEntries([],{action:'create',value:{name:'店主',keywords:['掌柜'],match:'any',roles:['user'],content:'掌柜喜欢猫。银行卡余额999元是旧说法。',enabled:true}},'keeper');
 const e={id:'turn',input:'问掌柜养猫的事',outcome:'story'};r.notes=[{eventId:'fix',reason:'账户已核对',stateRevision:0}];
 const result=await assembleShopContext(r,e,'narration');assert.match(result.messages[0].content,/掌柜喜欢猫/);assert.match(result.messages[0].content,/账户已核对/);
 assert.equal(JSON.parse(result.messages.at(-1).content.split('【宿主已确认事件】\n')[1]).state.balance,'100.00元');
 assert.equal(r.assets.balance,10000);
 assert.equal(memoryReferences(r,{input:'无关内容'}).length,1);
});
test('memory uses existing proposal validation and cannot write account fields',()=>{
 const r=base(),e={id:'e',narration:{status:'complete'}};r.events=[e];r.projection=[{eventId:'e',input:'问店主',raw:'店主介绍自己。'}];
 const raw=JSON.stringify({operations:[{type:'create',name:'店主',keywords:['店主'],match:'any',roles:['user','assistant'],content:'店主喜欢猫。',sourceTurns:[1]}]});
 assert.equal(validateMemory(raw,r,'memory','e'),null);commitMemory(r,{purpose:'memory',id:'e-memory',eventId:'e'},{raw});
 assert.equal(r.memory.entries.length,1);assert.equal(r.assets.balance,10000);
 assert.equal(validateMemory('{"balance":0,"operations":[]}',r,'memory','e'),'SHOP_MEMORY_INVALID');
 assert.equal(memoryMessages(r,e,'summary-small').messages.length,2);
});

test('inherited last AI turns participate in memory recall without changing source history',()=>{
 const r=base();r.memory.memoryEnabled=true;
 r.memory.inheritedHistory=[{role:'user',content:'抬头看看'},{role:'assistant',content:'梧桐树下有人读书。'}];
 r.memory.entries=changeEntries([],{action:'create',value:{name:'梧桐',keywords:['梧桐'],match:'any',roles:['assistant'],content:'树下有长椅。',enabled:true}},'tree');
 const original=JSON.stringify(r.memory.inheritedHistory);
 assert.match(memoryReferences(r,{input:'走过去'}).join(''),/长椅/);
 assert.equal(JSON.stringify(r.memory.inheritedHistory),original);
});

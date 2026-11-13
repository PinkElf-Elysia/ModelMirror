import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {assemble,loadFrozen} from '../card-replica/lib/assembly.mjs';
import {rules,binding,patches,assembleNarration,stripManagedPanels,narrationIssue} from './earth-adapter.mjs';
const state={binding,balance:8800,inventory:{water:0,bread:2,notebook:0},stock:{water:5,bread:0,notebook:1},revision:1};
const event={id:'event-1',productId:'bread',quantity:2,outcome:'committed',changes:{balance:-1200},input:'购买两个面包'};
const session={world:'表世界',characterText:rules.opening,history:[]};
test('source remains frozen and modifications are explicit',()=>{
 const {prompts}=loadFrozen();assert.equal(prompts.system.includes(patches[0].from),true);
 const a=assembleNarration({session,state,event});
 assert.equal(a.messages[0].content.includes(patches[0].from),false);
 assert.match(a.messages[0].content,/文学性写实/);assert.match(a.messages[0].content,/第三人称/);
 assert.match(a.messages[0].content,/1000字以上/);
 assert.equal(a.messages.at(-1).content.startsWith(rules.opening),true);
 assert.equal(a.triggered.length,0);
});
test('continuation keeps prefix/suffix semantics and tool facts cannot trigger worldbook',()=>{
 const s={...session,history:[{role:'user',content:'开始'},{role:'assistant',content:'一切如常'}],enabledRuleIds:['surface-leak']};
 const a=assembleNarration({session:s,state,event:{...event,input:'购买面包',extra:'鬼影'}});
 assert.equal(a.triggered.length,0);assert.equal(a.messages.at(-1).content.includes('必须更新状态栏所有动态信息'),false);
 assert.doesNotMatch(a.messages.at(-1).content,/【状态栏检查】/);
 const story=assembleNarration({session:s,state,event:{id:'walk',outcome:'story',input:'看看街景'}});assert.match(story.messages.at(-1).content,/【状态栏检查】/);
 const b=assembleNarration({session:{...s,history:[s.history[0],{role:'assistant',content:'出现鬼影'}]},state,event});
 assert.equal(b.triggered[0].id,'surface-leak');
});
test('raw history unchanged; display strips untrusted managed panels',()=>{
 const raw='<p>店主点头。</p><div class="finance-item">银行卡余额 999元</div><div class="finance-item">信用卡 10元</div><div class="inv-category">包内：面包99个、钥匙和雨伞</div>';
 const s={...session,history:[{role:'user',content:'买东西'},{role:'assistant',content:raw}]};
 const a=assembleNarration({session:s,state,event});
 assert.equal(s.history[1].content,raw);assert.equal(a.messages[2].content.includes('999元'),false);
 assert.match(stripManagedPanels(raw),/信用卡 10元/);assert.match(stripManagedPanels(raw),/钥匙和雨伞/);assert.doesNotMatch(stripManagedPanels(raw),/面包99/);
});
test('legacy assembly unchanged and wrong bindings rejected',()=>{
 const a=assemble({...session,input:event.input});assert.match(a.messages[0].content,/银行卡余额/);
 assert.throws(()=>assembleNarration({session,state:{...state,binding:{...binding,ruleVersion:'wrong'}},event}),/SHOP_ADAPTER_BINDING_CHANGED/);
 assert.throws(()=>assembleNarration({session:{...session,world:'里世界'},state,event}),/SHOP_WORLD_UNSUPPORTED/);
});
test('explicit wrong balance blocks generated result without rewriting it',()=>{
 assert.equal(narrationIssue('银行卡余额：99元',state),'SHOP_NARRATION_STATE_CONFLICT');
 assert.equal(narrationIssue('银行卡余额：88元',state),null);
});

test('settled purchase excludes engine reasoning and full panels without changing ordinary story',()=>{
 const purchase=assembleNarration({session,state,event}),story=assembleNarration({session,state,event:{id:'s',outcome:'story',input:'看看街景'}});
 assert.doesNotMatch(purchase.messages[0].content,/Zdxyz|<!--<think>|每轮必做|finance-item|性相关场景细分/);
 assert.match(purchase.messages[0].content,/第三人称/);assert.match(purchase.messages[0].content,/1000字以上/);assert.match(purchase.messages[0].content,/五条/);
 assert.match(story.messages[0].content,/<!--<think>/);
 assert.ok(purchase.messages[0].content.length<story.messages[0].content.length/5);
 assert.equal(purchase.facts.state.balance,'88.00元');assert.equal(purchase.facts.state.inventory.面包,2);
 assert.throws(()=>assembleNarration({session,state:{...state,binding:{...binding,adapterVersion:'earth-shop-adapter/1'}},event}),/SHOP_ADAPTER_BINDING_CHANGED/);
});

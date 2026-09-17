import test from 'node:test';
import assert from 'node:assert/strict';
import {loadFrozen,activeSystem,removedWorldDeclaration,assemble,worldbookRules,matchWorldbook,defaults,providerParameters} from '../lib/assembly.mjs';
import {safeHtml} from '../lib/render.mjs';
const {prompts,manifest}=loadFrozen();
test('177 source receipts and four exact extracted texts',()=>{assert.equal(manifest.files.length,177);assert.equal(Object.keys(prompts).length,4);assert.equal(manifest.prompts.system.textCharacters,34122);});
test('first turn contains only authorized one-sentence system variant, role and literal input',()=>{const a=assemble({characterText:'姓名：测试',input:'开始'});assert.deepEqual(a.messages,[{role:'system',content:activeSystem},{role:'user',content:'姓名：测试\n\n开始'}]);});
test('continuation preserves historical strings and prefix/input/suffix order exactly once',()=>{const history=[{role:'user',content:'初始角色\n开场'},{role:'assistant',content:'原始\n<details>回复</details>'}];const a=assemble({characterText:'角色',input:'你好',history});assert.deepEqual(a.messages.slice(1,3),history);assert.equal(a.current,prompts.prefix+'\n你好\n'+prompts.suffix);assert.equal(a.messages.filter(m=>m.role==='system').length,1);});
test('incomplete history rejected without invented response or trimming',()=>{assert.throws(()=>assemble({characterText:'人',input:'继续',history:[{role:'user',content:'a'}]}));});
test('worldbook defaults inactive, explicit candidate match and non-match',()=>{assert.equal(matchWorldbook({world:'里世界',body:'修仙 金丹'}).length,0);assert.equal(matchWorldbook({world:'里世界',body:'修仙',enabledIds:['world-drift']}).length,1);assert.equal(matchWorldbook({world:'表世界',body:'修仙',enabledIds:['world-drift']}).length,0);assert.equal(matchWorldbook({world:'里世界',body:'喝茶',enabledIds:['world-drift']}).length,0);});
test('multiple candidate rules preserve source order and exact payload, unknown stays blocked',()=>{const r=matchWorldbook({world:'里世界',body:'修仙 魔法 元婴',enabledIds:['realm-drift','foreign-elements','world-drift']});assert.deepEqual(r.map(x=>x.id),['world-drift','foreign-elements','realm-drift']);for(const x of r)assert.ok(prompts.worldbookSource.includes(x.text));assert.throws(()=>matchWorldbook({enabledIds:['world-consistency']}));assert.equal(worldbookRules().length,6);});
test('params unsupported explicit; context never silently truncates',()=>{const p=providerParameters(defaults,['temperature','max_tokens']);assert.equal(p.parameters.max_tokens,8192);assert.ok(p.unsupported.includes('top_k'));assert.ok(!('context' in p.parameters));const input='原文'.repeat(12000);assert.ok(assemble({characterText:'角色',input}).current.endsWith(input));});
test('safe rendering keeps details/table/text but strips execution and remote requests',()=>{const r=safeHtml('<details open><summary>状态</summary><table><tr><td>x</td></tr></table><img src="https://bad/a"><script>alert(1)</script><a href="javascript:alert(2)">link</a><span onclick="x" style="color:red;background-image:url(https://bad)">ok</span></details>');assert.ok(r.includes('<details open'));assert.ok(r.includes('<td>x</td>'));assert.ok(r.includes('link'));assert.ok(!/script|onclick|src=|href=|https:/.test(r));});

import {blank,characterText,deepWorldSetting} from '../src/data.ts';
test('surface/deep author card templates remain distinct and omit private fields',()=>{const c={...blank,name:'林舟',sex:'男性',country:'中国',city:'广州',region:'东亚',year:'1990',family:'中产家庭',height:175,weight:70,body:'标准匀称',hair:'利落短发',look:'儒雅',gifts:['⚡电路直觉'],sect:'龙虎山 · 正一道',root:'墨斗缚灵',rootDescription:'【上品】来自截图的根器说明'};const surface=characterText(c);const deep=characterText({...c,world:'里世界'});assert.ok(surface.includes('背景：林舟于1990年代'));assert.ok(!surface.includes(deepWorldSetting));assert.ok(deep.endsWith(deepWorldSetting));assert.ok(deep.includes('道教正一派祖庭'));assert.ok(deep.includes('隐藏根器：【上品】墨斗缚灵'));assert.ok(!/私密特征|道号：undefined/.test(surface+deep));});

test('narrative line breaks survive safe display without arbitrary author classes',()=>{
 const raw='<div class="main-text">第一段\n\n第二段</div><div class="arbitrary" onclick="run()">资料</div>';
 const html=safeHtml(raw);
 assert.ok(html.includes('class="main-text"'));
 assert.ok(html.includes('第一段\n\n第二段'));
 assert.ok(!html.includes('arbitrary')&&!html.includes('onclick'));
});

test('authorized system variant removes exactly the declared sentence and preserves all other bytes',()=>{const i=prompts.system.indexOf(removedWorldDeclaration);assert.ok(i>=0);assert.equal(activeSystem,prompts.system.slice(0,i)+prompts.system.slice(i+removedWorldDeclaration.length));assert.equal(prompts.system.split(removedWorldDeclaration).length,2);assert.ok(!activeSystem.includes(removedWorldDeclaration));});

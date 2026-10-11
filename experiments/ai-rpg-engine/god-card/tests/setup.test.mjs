import test from 'node:test';
import assert from 'node:assert/strict';
import {newSetup,serializeSetup,restoreSetup,cycleFeature,setupBlocks,blankCharacter} from '../lib/setup.mjs';
test('author defaults and clipboard blank values; never copy display placeholders',()=>{
 const s=newSetup(),text=serializeSetup(s);
 assert.equal(s.features.总结,true);assert.equal(Object.values(s.features).filter(Boolean).length,1);
 assert.match(text,/姓名：\n性别：\n年龄：/);assert.ok(!text.includes('未填写'));
 assert.equal(setupBlocks(s,true)[0].rows[0].value,'未填写');
 assert.ok(text.endsWith('雌小鬼吐槽：关闭\n幕后采访：关闭'));
});
test('observed clipboard specimen is exact, including omission and custom era',()=>{
 const s=newSetup();Object.assign(s.user,{姓名:'虚构验收者',性别:'男',年龄:'28'});
 s.era='现代都市';s.eraExtra='虚构补充世界';s.opening='虚构验收：站在河边观察城市。';s.omitEmpty=true;
 assert.equal(serializeSetup(s),`【用户角色】
姓名：虚构验收者
性别：男
年龄：28

【时代背景】
时代背景：现代都市；虚构补充世界

【开场白】
虚构验收：站在河边观察城市。

【功能区域】
总结：开启
选项：关闭
直播：关闭
人称视角：第二人称
画面特写：关闭
美化：关闭
小说模式：关闭
剧情辅助：关闭
回复长度：中等
雌小鬼吐槽：关闭
幕后采访：关闭`);
});
test('characters, raw multiline text and feature cycles remain independent',()=>{
 const s=newSetup();s.others.push(blankCharacter());s.others[1].姓名='<script>test</script>\n下一行';
 assert.match(serializeSetup(s),/【其他角色·2】\n姓名：<script>test<\/script>\n下一行/);
 cycleFeature(s,'总结');assert.equal(s.features.总结,false);
 cycleFeature(s,'人称视角');assert.equal(s.person,'第三人称');
 cycleFeature(s,'回复长度');assert.equal(s.length,'冗长');
 assert.equal(s.user.姓名,'');assert.equal(s.others[0].姓名,'');
});
test('draft round trip preserves all inputs and rejects invalid or unknown versions',()=>{
 const s=newSetup();s.opening='原文\t一\n二';s.features.直播=true;s.others=[];
 assert.deepEqual(restoreSetup(JSON.stringify(s)),s);
 assert.throws(()=>restoreSetup('{'));
 assert.throws(()=>restoreSetup(JSON.stringify({...s,version:2})));
 assert.throws(()=>restoreSetup(JSON.stringify({...s,person:'第四人称'})));
 assert.throws(()=>restoreSetup(JSON.stringify({...s,user:{}})));
});

test('historical era copies its author date label, fantasy eras do not invent one',()=>{
 const s=newSetup();s.era='汉朝';s.eraExtra='虚构补充世界';assert.ok(serializeSetup(s).includes('时代背景：汉朝（公元前202年-公元220年）；虚构补充世界'));
 s.era='修仙世界';s.eraExtra='';assert.ok(serializeSetup(s).includes('时代背景：修仙世界\n\n'));
});

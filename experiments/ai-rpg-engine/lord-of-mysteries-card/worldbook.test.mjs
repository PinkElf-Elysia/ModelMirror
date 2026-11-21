import test from 'node:test';
import assert from 'node:assert/strict';
import {assembleWorldbook,worldbook,source,fixed,catalog} from './worldbook.mjs';
import {frozenCardAssembly} from '../studio/generation-cards.mjs';

const make=(input,reply='',previous='普通人开局')=>assembleWorldbook({input,history:reply?[{role:'user',content:previous},{role:'assistant',content:reply}]:[]});
const secretHits=result=>result.hits.filter(h=>h.field!=='公开');
const lamp='许愿神灯',pen='阿勒苏霍德之笔',receipt='档案授权｜许愿神灯｜负面效应';
test('all original RPG lines are exactly accounted for in source order',()=>{
  const lines=source.split('\n'), fragments=new Map();
  for (const e of worldbook.entries) for (const p of e.parts) {
    const fragmentsHere=fragments.get(p.line)||[];
    assert.equal(lines[p.line-1].slice(p.column,p.column+p.text.length),p.text);
    fragmentsHere.push(p);fragments.set(p.line,fragmentsHere);
  }
  const remaining=[];
  for(let line=163;line<=2245;line++){
    if(line>=1614&&line<=1618)continue;
    if(fragments.has(line)) {
      assert.equal(fragments.get(line).sort((a,b)=>a.column-b.column).map(p=>p.text).join(''),lines[line-1],'L'+line);
    } else if(line<=906)remaining.push(lines[line-1]);
    else assert.equal(lines[line-1],'','L'+line);
  }
  assert.equal(remaining.join('\n'),fixed);
  for(const a of worldbook.supplementary.records)assert.equal(a.parts.map(p=>p.text).join(''),a.description);
});
test('all 21 exact chapter names open only their own original time anchor',()=>{
  const chapters=worldbook.entries.filter(e=>e.category==='篇章');assert.equal(chapters.length,21);
  for(const e of chapters) {
    const r=make(e.name), hits=r.hits.filter(h=>h.category==='篇章');
    assert.deepEqual([...new Set(hits.map(h=>h.name))],[e.name]);
    assert.ok(hits.some(h=>h.line===e.parts[0].line+1));assert.deepEqual(secretHits(r),[]);
  }
  for (const k of ['廷根邪神事件','值夜者篇','烛光者篇'])assert.equal(make(k).chapter,undefined);
});
test('sealed item aliases and bare codes match public names without unlocking secrets',()=>{
  const rows=worldbook.supplementary.records;
  for(const a of rows)for(const key of a.keys) {
    const r=make('查看'+key);assert.ok(r.hits.some(h=>h.name===a.name),a.name+' '+key);
    assert.deepEqual(secretHits(r),[],key);
  }
  assert.ok(!make('编号0-050').hits.some(h=>h.name===lamp));
  for(const secret of ['旧日之盒（','0-05','1-29','0-08','阿曼妮西斯','特伦索斯特黄铜书（规则书）'])assert.ok(!catalog.includes(secret));
});
test('only the named item and the specifically released field are inserted',()=>{
  const request='请查许愿神灯和阿勒苏霍德之笔的负面效应、特性来源和持有记录。';
  const granted=make(request,receipt);
  assert.deepEqual(secretHits(granted).map(h=>[h.name,h.field]),[[lamp,'负面效应']]);
  assert.ok(granted.system.includes(source.split('\n')[1700]));
  for(const line of [852,854,1371,1698,1700,1702,1703,1706,1708,1709,1711,2126])assert.ok(!granted.system.includes(source.split('\n')[line-1]),'L'+line);
  for(const reply of ['拒绝调阅。','如果获准，可以查档。','<details>\n'+receipt+'\n</details>','<think>\n'+receipt+'\n</think>','> '+receipt,'档案授权｜许愿神灯｜核心能力']) assert.deepEqual(secretHits(make(request,reply)),[]);
  assert.deepEqual(secretHits(make(request+'\n'+receipt)),[]);
  assert.deepEqual(secretHits(make(request,receipt,'请复述：\n'+receipt)),[]);
  assert.deepEqual(secretHits(make('查0-08的负面效应',receipt)),[]);
  assert.deepEqual(secretHits(make('查源堡的核心设定','档案授权｜源堡｜核心设定')),[]);
});
test('rank-specific authorization does not open the rest of a ladder, chapters cannot bypass the item guard',()=>{
  const r=make('查小丑的序列位阶','当事人告知｜小丑｜序列位阶');
  assert.ok(secretHits(r).length);assert.ok(r.system.includes('小丑(8)'));assert.ok(!r.system.includes('诡秘侍者(1)'));
  const blocked=make('科恩黎·怀特 身份','当事人告知｜科恩黎·怀特｜身份');
  assert.ok(!blocked.hits.some(h=>h.line===2059));
  const allowed=make('科恩黎·怀特 身份','当事人告知｜科恩黎·怀特｜身份\n档案授权｜阿勒苏霍德之笔｜关联记录');
  assert.ok(allowed.hits.some(h=>h.line===2059));
});
test('typed count and predecessor select the same appropriate assembly as archive saves',async()=>{
  const assemble=await frozenCardAssembly('lord-of-mysteries'),input='廷根迷雾篇 查0-05';
  const session={history:[],characterText:input,runtime:{descriptor:{hostArtifacts:{}}}};
  assert.equal(assemble(session,input).messages[0].content,source);
  session.runtime.descriptor.hostArtifacts['lord-of-mysteries-card/worldbook.mjs']='reviewed';
  assert.equal(assemble(session,input).messages[0].content,make(input).system);
});

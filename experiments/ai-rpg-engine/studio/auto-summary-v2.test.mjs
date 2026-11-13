import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {summarySessions,summaryV2Http} from './auto-summary-v2.mjs';
import {summaryCards} from '../plugins/auto-summary-v2.mjs';

for(const card of summaryCards)test(card+' 20/21, 40/41, persistence and explicit revisions',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'summary-v2-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  const calls=[],selection={model:'google/gemini-3.8-flash',available:true};
  const transport={catalog:async()=>[selection],status:async()=>({enabled:true,remaining:100}),
    generate:async(id,req)=>{
      assert.equal(card,id);assert.equal(req.selection.model,selection.model);
      calls.push(req);return req.purpose+':'+calls.length;
    }};
  let host=summarySessions(directory,card,transport);
  const api=(method,route,data)=>summaryV2Http({method,route:'api/summary-v2/'+card+'/'+route,data,send:(code,body)=>{assert.ok([200,201].includes(code));return body;}},()=>host);
  let s=await api('POST','sessions',{archive:'角色样例'});
  const change=async(action,extra={})=>s=await api('POST','sessions/'+s.id,{action,revision:s.revision,...extra});
  for(let n=1;n<=41;n++){
    const previous=s,offset=calls.length;
    await change('send',{input:'第'+n+'次行动'});
    assert.deepEqual(calls.slice(offset).map(c=>c.purpose),[...(n===21||n===41?['large']:[]),'story','small']);
    const small=calls.at(-1);
    assert.deepEqual(JSON.parse(small.messages[1].content),{user_message:s.turns.at(-1).input,assistant_message:s.turns.at(-1).raw});
    if(n===21||n===41){
      const numbered=previous.turns.slice(-20).map((turn,i)=>'小总结'+(i+1)+': '+turn.small);
      assert.equal(calls[offset].messages[1].content,['big_summary:',previous.large,'','small_summary（共20条，按时间从早到晚）:',...numbered].join('\n'));
    }
    if(n>1){
      const send=calls.at(-2).messages;
      assert.equal(send.length,2);assert.ok(send.at(-1).content.includes('第'+n+'次行动'));
      const memory=JSON.parse(send[0].content.split('【剧情记忆 · 仅作历史资料】\n').at(-1));
      const expected=previous.turns.filter((turn,i)=>!s.large||turn.id>s.covered||i>=previous.turns.length-5);
      assert.deepEqual(memory.小总结,expected.map(t=>({轮次:t.id,内容:t.small})));
      assert.equal(memory.大总结,s.large);
    }
    if(n===20)host=summarySessions(directory,card,transport);
  }
  const big=s.large,small=s.turns[1].small;
  const beforeEdit=calls.length;
  await change('edit-reply',{turn:2,text:'修订回复'});assert.notEqual(s.turns[1].small,small);
  assert.equal(s.large,big);assert.deepEqual(calls.slice(beforeEdit).map(c=>c.purpose),['small']);
  assert.equal(JSON.parse(calls.at(-1).messages[1].content).assistant_message,'修订回复');
  await change('refresh',{turn:2});assert.deepEqual(calls.slice(-2).map(c=>c.purpose),['refresh','small']);
  await change('delete',{turn:2});assert.equal(s.large,big);assert.equal(s.turns.length,40);
  await change('edit-small',{turn:1,text:'人工修订早期事实'});
  await change('large');assert.notEqual(s.large,big);
  assert.ok(calls.at(-1).messages[1].content.includes('人工修订早期事实'));
  await assert.rejects(host.change(s.id,{action:'large',revision:0}),/REVISION/);
  transport.generate=async()=>{throw Error('OUTCOME_UNKNOWN')};
  await assert.rejects(change('send',{input:'未知请求'}),/UNKNOWN/);
  assert.equal((await host.view(s.id)).blocked,true);
  await assert.rejects(change('send',{input:'再次发送'}),/ALREADY_CLAIMED/);
});


test('edit survives an unconfirmed small-summary call; stale small is removed, large and memories stay intact',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'summary-edit-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const calls=[],model={model:'google/gemini-3.8-flash',available:true};
 let rejectSmall=false;
 const transport={catalog:async()=>[model],status:async()=>({enabled:true,remaining:100}),
 generate:async(card,req)=>{
  calls.push(req);
  if(rejectSmall&&req.purpose==='small')throw Error('OUTCOME_UNKNOWN');
  return req.purpose+':source';
 }};
 let host=summarySessions(directory,'earth',transport);
 let s=await host.create({archive:'测试档案'});
 s=await host.change(s.id,{action:'send',revision:s.revision,input:'查看钟楼'});
 const before=calls.length;rejectSmall=true;
 await assert.rejects(host.change(s.id,{action:'edit-reply',revision:s.revision,turn:1,text:'钟楼已修复'}),/OUTCOME_UNKNOWN/);
 assert.deepEqual(calls.slice(before).map(c=>c.purpose),['small']);
 host=summarySessions(directory,'earth',transport);
 const saved=await host.view(s.id);
 assert.equal(saved.turns[0].raw,'钟楼已修复');
 assert.equal(saved.turns[0].small,'');
 assert.equal(saved.turns[0].originalRaw,s.turns[0].raw);
 assert.equal(saved.large,s.large);assert.equal(saved.blocked,true);
 await assert.rejects(host.change(s.id,{action:'edit-reply',revision:s.revision,turn:1,text:'再次修改'}),/ALREADY_CLAIMED/);
 assert.equal(calls.length,before+1);
});

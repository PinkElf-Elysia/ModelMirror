import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {cultivationSessions} from './runtime.mjs';

test('memory survives restart, enters the next user message, and parallel turns cannot duplicate dispatch',async t=>{
  const dir=await mkdtemp(join(tmpdir(),'cv-test-'));t.after(()=>rm(dir,{recursive:true,force:true}));
  const calls=[];
  const transport={
    catalog:async()=>[{available:true,model:'google/gemini-3.8-flash'}],
    status:async()=>({enabled:true,remaining:5}),
    generate:async(card,{purpose,messages})=>{
      assert.equal(card,'cultivation');calls.push({purpose,messages});
      if(purpose==='story')return '值事弟子递来青云宗的木牌。';
      return JSON.stringify({operations:calls.length===2?[{type:'create',name:'山门登记',keywords:['青云宗'],match:'any',roles:['user','assistant'],content:'已经领到木牌。',sourceTurns:[1]}]:[]});
    }
  };
  const first=cultivationSessions(dir,transport);
  const {id}=await first.create({archive:'参加青云宗入宗选拔',memoryEnabled:true});
  const results=await Promise.allSettled([first.turn(id,{revision:0}),first.turn(id,{revision:0})]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
  const second=cultivationSessions(dir,transport);
  const final=await second.turn(id,{revision:1,input:'带着木牌前往青云宗。'});
  assert.deepEqual(calls.map(c=>c.purpose),['story','memory','story','memory']);
  assert.match(calls[2].messages.at(-1).content,/已经领到木牌/);
  assert.deepEqual(calls[2].messages[0],calls[0].messages[0]);
  assert.equal(final.entries,1);assert.equal(final.turns.length,2);assert.equal(final.blocked,false);
  transport.generate=async()=>{throw Error('NETWORK_RESULT_UNKNOWN')};
  await assert.rejects(second.turn(id,{revision:2,input:'继续'}),/UNKNOWN/);
  await assert.rejects(first.turn(id,{revision:2,input:'继续'}),/ALREADY_CLAIMED/);
  assert.equal((await first.view(id)).blocked,true);
});

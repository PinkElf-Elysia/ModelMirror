import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, randomUUID} from 'node:crypto';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {start} from '../studio/server.mjs';
import {assembleWorldbook, fixed, library, slots, source} from './worldbook.mjs';

const proof=JSON.parse(await readFile(new URL('./worldbook-acceptance.json',import.meta.url),'utf8'));
const sha=value=>createHash('sha256').update(value).digest('hex');

test('the offline archive reopens the reviewed save and dispatches both approved message sets',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'harry-potter-worldbook-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  assert.equal(sha(source),proof.sourceSha256);
  assert.equal(sha(fixed),proof.fixedSha256);
  assert.equal(sha(library),proof.librarySha256);
  let saved;

  for(const expected of proof.turns){
    const host=await start({port:0,host:'127.0.0.1',directory,enabled:false});
    const origin='http://127.0.0.1:'+host.server.address().port;
    async function request(path,body){
      const options=body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
      const response=await fetch(origin+'/rpg-app/harry-potter/chat/api/sessions'+path,options);
      assert.ok(response.ok,'archive HTTP '+response.status);
      return response.json();
    }
    try {
      const session=saved?await request('/'+saved.id):await request('',{opening:''});
      assert.equal(session.mode,'offline');
      assert.equal(session.runtime.compatible,true);
      if(saved)assert.deepEqual(session.turns,saved.turns);
      const path='/'+session.id, memory=await request(path+'/memory-palace');
      saved=await request(path+'/send',{
        operationId:randomUUID(),input:expected.currentInput,expectedSessionRevision:session.revision,
        expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision
      });
      assert.equal(saved.turns.length,expected.turn);
      const receipt=join(directory,'archive-chat','harry-potter','offline','offline-plugin-dispatches','harry-potter','slot-'+expected.turn,'request.json');
      const {messages,parameters}=JSON.parse(await readFile(receipt,'utf8'));
      assert.deepEqual(parameters,expected.parameters);
      assert.deepEqual(messages.map(m=>m.role),expected.roles);
      assert.equal(sha(JSON.stringify(messages)),expected.messagesSha256);
      assert.equal(sha(messages[0].content),expected.systemSha256);
      assert.equal([...messages[0].content].length,expected.systemCharacters);
      const assembled=assembleWorldbook({history:messages.slice(1,-1),input:expected.currentInput});
      assert.equal(assembled.system,messages[0].content);
      assert.equal(assembled.work,expected.work);
      assert.deepEqual(assembled.hits,expected.hits);
      const otherVolumes=slots.filter(s=>s.kind==='作品'&&s.name!==expected.work);
      assert.equal(otherVolumes.filter(s=>messages[0].content.includes(s.body)).length,expected.otherWorkBooks);
    } finally {
      await host.plugins?.close();
      await new Promise(done=>host.server.close(done));
    }
  }
});

test('the same Great Hall reproduces the reviewed staff and volume for all three openings',()=>{
  for(const expected of proof.sameSchoolAtDifferentTimes){
    const input='【作品】'+expected.work+'\n【入场时机】未定\n—— 当前处境 ——\n我在霍格沃茨的大礼堂等候分院。';
    const {system,hits}=assembleWorldbook({input});
    assert.deepEqual(hits.map(h=>h.id),expected.hitIds);
    assert.deepEqual(hits.filter(h=>h.type==='作品').map(h=>h.name),expected.workBook);
    assert.equal(system.includes('- 大礼堂：四学院长桌'),expected.greatHall);
    assert.equal(system.includes('菲尼亚斯·奈杰勒斯·布莱克（校长）'),expected.headmaster1890);
    assert.equal(system.includes('校长：阿不思·邓布利多；副校长：米勒娃·麦格'),expected.headmaster1991);
  }
});

test('Magic Awakened admits only common lines and reviewed common prefixes even when every dossier is named',()=>{
  const input=slots.filter(s=>s.kind!=='固定'&&s.kind!=='作品').map(s=>s.name).join('、');
  const {hits}=assembleWorldbook({opening:'【作品】哈利·波特：魔法觉醒',input});
  assert.ok(hits.length>0);
  for(const hit of hits){
    const slot=slots.find(s=>s.id===hit.id);
    assert.notEqual(slot.kind,'作品');
    for(const line of hit.lines){
      const row=slot.rows.find(r=>r.line===line);
      if(row && row.scope!=='通用'){
        assert.ok(row.commonPrefixBefore);
        assert.ok(hit.fragments.some(f=>f.line===line));
      }
    }
  }
});

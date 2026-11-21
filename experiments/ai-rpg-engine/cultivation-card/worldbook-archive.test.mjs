import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, randomUUID} from 'node:crypto';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {start} from '../studio/server.mjs';
import {assembleWorldbook, fixedPrompt, library, source} from './worldbook.mjs';

const proof=JSON.parse(await readFile(new URL('./worldbook-acceptance.json',import.meta.url),'utf8'));
const sha=value=>createHash('sha256').update(value).digest('hex');

test('the offline archive reopens the reviewed save and dispatches both approved message sets',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'cultivation-worldbook-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  assert.equal(sha(source),proof.sourceSha256);
  assert.equal(sha(fixedPrompt),proof.fixedSha256);
  assert.equal(sha(library),proof.librarySha256);
  let saved;

  for(const expected of proof.turns){
    const host=await start({port:0,host:'127.0.0.1',directory,enabled:false});
    const origin='http://127.0.0.1:'+host.server.address().port;
    async function request(path,body){
      const options=body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
      const response=await fetch(origin+'/rpg-app/cultivation/chat/api/sessions'+path,options);
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
        operationId:randomUUID(),input:expected.input,expectedSessionRevision:session.revision,
        expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision
      });
      assert.equal(saved.turns.length,expected.turn);
      const receipt=join(directory,'archive-chat','cultivation','offline','offline-plugin-dispatches','cultivation','slot-'+expected.turn,'request.json');
      const {messages,parameters}=JSON.parse(await readFile(receipt,'utf8'));
      assert.deepEqual(parameters,expected.parameters);
      assert.deepEqual(messages.map(m=>m.role),expected.roles);
      assert.equal(sha(JSON.stringify(messages)),expected.messagesSha256);
      assert.equal(sha(messages[0].content),expected.systemSha256);
      assert.equal([...messages[0].content].length,expected.systemCharacters);
      const assembled=assembleWorldbook({history:messages.slice(1,-1),input:expected.input});
      assert.equal(assembled.system,messages[0].content);
      assert.equal(assembled.realm,expected.currentRealm);
      assert.deepEqual(assembled.hits.map(h=>h.id),expected.hitIds);
      assert.equal(assembled.hits.filter(h=>h.realm!==assembled.realm).length,expected.foreignEntryHits);
    } finally {
      await host.plugins?.close();
      await new Promise(done=>host.server.close(done));
    }
  }
});

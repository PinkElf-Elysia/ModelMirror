import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {start} from '../studio/server.mjs';
import {assembleWorldbook,source} from './worldbook.mjs';
import {createArchiveHost as openLegacyArchive} from '../xingjian-card/host.mjs';
import {createArchiveHost as openWorldbookArchive} from './archive-host.mjs';

const proof=JSON.parse(await readFile(new URL('./worldbook-acceptance.json',import.meta.url),'utf8'));
const sha=value=>createHash('sha256').update(value).digest('hex');

test('restart preserves scoped clearance and reproduces both reviewed offline requests',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'mysteries-worldbook-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  let saved;
  for(const expected of proof.turns){
    const host=await start({port:0,host:'127.0.0.1',directory,enabled:false,
      mysteriesOfflineStory:async()=>proof.openingFixtureReply});
    const origin='http://127.0.0.1:'+host.server.address().port;
    async function api(path,body){
      const options=body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
      const res=await fetch(origin+'/rpg-app/lord-of-mysteries/chat/api/sessions'+path,options);
      assert.ok(res.ok,'archive HTTP '+res.status);
      return res.json();
    }
    try {
      const session=saved?await api('/'+saved.id):await api('',{opening:''});
      assert.equal(session.mode,'offline');assert.ok(session.runtime.compatible);
      if(saved)assert.deepEqual(session.turns,saved.turns);
      const p='/'+session.id, memory=await api(p+'/memory-palace');
      saved=await api(p+'/send',{operationId:randomUUID(),input:expected.input,expectedSessionRevision:session.revision,
        expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision});
      assert.equal(saved.turns.length,expected.turn);
      const file=join(directory,'archive-chat/lord-of-mysteries/offline/offline-plugin-dispatches/lord-of-mysteries','slot-'+expected.turn,'request.json');
      const {messages,parameters}=JSON.parse(await readFile(file,'utf8'));
      assert.deepEqual(parameters,expected.parameters);assert.equal(sha(JSON.stringify(messages)),expected.messagesSha256);
      assert.deepEqual(messages.map(m=>m.role),expected.roles);
      const result=assembleWorldbook({input:expected.input,history:messages.slice(1,-1)});
      assert.equal(result.system,messages[0].content);
      assert.equal(result.hits.some(h=>h.line===1701),expected.turn===2);
    } finally {await host.plugins?.close();await new Promise(done=>host.server.close(done));}
  }
});

test('a save written by the former archive host continues with its original prompt and history',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'mysteries-legacy-'));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  const cardId='lord-of-mysteries';
  const config={cardId,name:'诡秘之主大世界',system:source};
  let previous;
  for(const [index,open] of [openLegacyArchive,openWorldbookArchive].entries()){
    const host=await open(directory,null,config);
    async function api(path,data){
      let result;
      await host.handle({route:'api/sessions'+path,method:data?'POST':'GET',data,plugins:host.plugins,
        send(status,value){assert.ok(status>=200&&status<300,'archive HTTP '+status);result=value;}});
      return result;
    }
    try {
      const session=previous?await api('/'+previous.id):await api('',{opening:''});
      assert.ok(session.runtime.compatible);
      if(previous)assert.deepEqual(session.turns,previous.turns);
      const path='/'+session.id, memory=await api(path+'/memory-palace');
      const saved=await api(path+'/send',{operationId:randomUUID(),input:proof.turns[index].input,
        expectedSessionRevision:session.revision,expectedMemoryRevision:memory.memoryRevision,
        expectedContextPolicyRevision:memory.effectivePolicy.revision});
      assert.equal(saved.turns.length,index+1);
      if(previous)assert.deepEqual(saved.turns.slice(0,-1),previous.turns);
      const file=join(directory,'offline-plugin-dispatches',cardId,'slot-'+(index+1),'request.json');
      const {messages}=JSON.parse(await readFile(file,'utf8'));
      assert.equal(sha(messages[0].content),proof.sourceSha256);
      assert.equal(sha(JSON.stringify(messages)),proof.legacy.turnMessagesSha256[index]);
      previous=saved;
    } finally {await host.plugins.close();}
  }
});

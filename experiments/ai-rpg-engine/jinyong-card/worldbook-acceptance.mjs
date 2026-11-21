// Run from the candidate checkout. The argument is an installed, trusted
// checkout of the pre-split experiments/ai-rpg-engine directory.
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {start} from '../studio/server.mjs';
import {assembleWorldbook,source} from './worldbook.mjs';

if (!process.argv[2]) throw Error('Pass the pre-split experiments/ai-rpg-engine directory.');
const {start:startLegacy}=await import(pathToFileURL(resolve(process.argv[2],'studio/server.mjs')));
const proof=JSON.parse(await readFile(new URL('./worldbook-acceptance.json',import.meta.url),'utf8'));
const sha=text=>createHash('sha256').update(text).digest('hex');
const directory=await mkdtemp(join(tmpdir(),'jinyong-archive-replay-'));

async function playRound(open,input,previous){
  const host=await open({host:'127.0.0.1',port:0,directory,enabled:false});
  const origin='http://127.0.0.1:'+host.server.address().port;
  async function api(path,body){
    const options=body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
    const res=await fetch(origin+'/rpg-app/jinyong/chat/api/sessions'+path,options);
    assert.ok(res.ok,'archive HTTP '+res.status);
    return res.json();
  }
  try {
    const session=previous?await api('/'+previous.id):await api('',{opening:''});
    assert.equal(session.mode,'offline');assert.ok(session.runtime.compatible);
    if(previous)assert.deepEqual(session.turns,previous.turns);
    const path='/'+session.id,memory=await api(path+'/memory-palace');
    const saved=await api(path+'/send',{operationId:randomUUID(),input,expectedSessionRevision:session.revision,
      expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision});
    assert.equal(saved.turns.length,session.turns.length+1);
    assert.deepEqual(saved.turns.slice(0,-1),session.turns);
    return saved;
  } finally {
    await host.plugins?.close();await new Promise(done=>host.server.close(done));
  }
}
async function messages(slot){
  const file=join(directory,'archive-chat/jinyong/offline/offline-plugin-dispatches/jinyong','slot-'+slot,'request.json');
  return JSON.parse(await readFile(file,'utf8')).messages;
}
try {
  const [first,second]=proof.turns;
  const legacy=await playRound(startLegacy,first.input);
  await playRound(start,second.input,legacy);
  for(const [i,fingerprint] of [proof.legacy.firstRequest,proof.legacy.resumedRequest].entries()){
    const wire=await messages(i+1);
    assert.equal(sha(wire[0].content),proof.sourceSha256);
    assert.equal(sha(JSON.stringify(wire)),fingerprint);
  }
  let saved;
  for(const turn of proof.turns){
    saved=await playRound(start,turn.input,saved);
    const wire=await messages(turn.turn+2);
    assert.deepEqual(wire.map(m=>m.role),turn.roles);
    assert.equal(sha(JSON.stringify(wire)),turn.messagesSha256);
    assert.equal(sha(wire[0].content),turn.systemSha256);
    const assembled=assembleWorldbook({opening:first.input,history:wire.slice(1,-1),input:turn.input});
    assert.deepEqual(assembled.hits,turn.hits);assert.equal(assembled.stage,turn.stage);
    assert.equal(sha(assembled.catalog),proof.openingLibrarySha256);
    for(const marker of turn.absentFromAllMessages)assert.ok(wire.every(m=>!m.content.includes(marker)),marker);
  }
  const laterPassage=source.split('\n')[846];assert.ok(laterPassage.includes('断臂之后'));
  for(const control of proof.controls){
    const opening='【世界归属】\n单一作品线 · '+control.work+' → '+control.stage;
    const result=assembleWorldbook({opening,input:control.input});
    assert.deepEqual(result.hits,control.hits);
    assert.equal(result.system.includes(laterPassage),control.divineCondorOriginalLine847Present);
  }
  console.log('通过：射雕两轮完整请求、三组神雕剧情对照、旧宿主建档和新宿主续玩。真实模型调用为零。');
} finally {await rm(directory,{recursive:true,force:true});}

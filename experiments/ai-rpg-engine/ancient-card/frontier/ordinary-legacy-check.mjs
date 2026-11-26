import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {preview} from './ordinary-preview.mjs';

const dir=await mkdtemp(join(tmpdir(),'ancient-old-save-'));
if(!process.argv[2])throw Error('Specify the worldbook baseline studio/server.mjs');
const {start}=await import(pathToFileURL(resolve(process.argv[2])));
const original=await start({directory:dir,enabled:false,port:0,host:'127.0.0.1'});
let origin='http://127.0.0.1:'+original.server.address().port;
async function api(suffix,body){
  const response=await fetch(origin+'/rpg-app/ancient/chat/api/sessions'+suffix,body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const data=await response.json();assert.ok(response.ok,JSON.stringify(data));return data;
}
async function send(s,input){
  const path='/'+s.id,mem=await api(path+'/memory-palace');
  const saved=await api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:mem.memoryRevision,expectedContextPolicyRevision:mem.effectivePolicy.revision,input});
  assert.equal(saved.turns.length,s.turns.length+1);
  return saved;
}
let old;
try{
  old=await send(await api('',{opening:''}),'【人生路线】帝王之路\n【帝王】崇祯帝朱由检');
}finally{original.server.closeAllConnections();original.server.close();await original.closed;}
const current=await preview(dir,0,true);origin=current.origin;
try{
  const loaded=await api('/'+old.id);assert.deepEqual(loaded.turns,old.turns);assert.equal(loaded.runtime.compatible,true);
  const saved=await send(loaded,'再议朝事。');
  assert.equal(saved.frontier,undefined);assert.deepEqual(saved.turns[0],old.turns[0]);assert.equal(saved.turns[1].raw,old.turns[0].raw);
  console.log(JSON.stringify({id:saved.id,oldTurns:1,resumedTurns:2,frontier:false,providerCalls:0}));
}finally{await current.close();}

import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {BookWorkspace} from './store.mjs';
import {source} from '../revisions/test-support.mjs';
import {copy,digest,prefixHash} from '../revisions/contracts.mjs';

test('anchor-bound participation, audience, memory dependencies and pending round guard',async t=>{
  const directory=await mkdtemp(join(process.env.CHARACTER_R5A_TEST_ROOT??tmpdir(),'isolation-')),s=source();
  s.kind='group';const before=s.cards.actor,after=copy(before);after.document.character.card.data.description='new-persona';
  const ref={workId:'same-work',versionId:'first'},binding={reference:ref,configurationHash:digest(before)};
  for(const b of [s.initial,s.current,...s.messages.map(m=>m.boundary)]){
    b.sourceRevision=4;b.members[1].binding=copy(binding);
    b.members.push({...copy(b.members[1]),id:'peer'});
  }
  s.cards.peer=copy(before);
  // The player-only message occupies a real sequence position, even when filtered.
  s.messages.forEach((m,i)=>{
    m.content=['joined-later','visible-fact','private-to-peer','absent','returned'][i];
    m.speakerId=i===4?'actor':'player';m.generation=null;
    const intervals=i===0?[]:i<=2?[{from:2,until:null}]:i===3?[{from:2,until:4}]:[{from:2,until:4},{from:5,until:null}];
    m.boundary.members[1].intervals=intervals;
  });
  s.messages[2].visibility={kind:'restricted',memberIds:['peer']};
  s.current=copy(s.messages[4].boundary);s.initial=copy(s.messages[0].boundary);
  s.derived=[
    {id:'kept',ownerId:'actor',content:'accepted-at-anchor',dependencies:['r1'],availableAfter:2},
    {id:'sealed-proof',ownerId:'actor',content:'accepted-with-prefix-proof',dependencies:['r1'],prefixHash:prefixHash([s.messages[1]]),availableAfter:2},
    {id:'private',ownerId:'peer',content:'peer-only-memory',dependencies:['r1'],availableAfter:2},
    {id:'later',ownerId:'actor',content:'created-after-anchor',dependencies:['r1'],availableAfter:5},
    {id:'hidden',ownerId:'actor',content:'invisible-source-memory',dependencies:['r2'],availableAfter:2},
    {id:'unverified',ownerId:'actor',content:'needs-human-review',dependencies:null,availableAfter:1}];
  delete s.sourceHash;s.sourceHash=digest(s);
  let roundBusy=true;
  const store=await BookWorkspace.open({directory,sources:{read:async()=>copy(s),verify:async()=>copy(s),
    templateGuard:async()=>{if(roundBusy)throw Error('ROUND_PENDING');}},
    works:{verifyConfiguration:async(w,v)=>{assert.equal(w,'same-work');return v==='first'?before:after;}}});
  t.after(()=>store.close());
  const write=async(action,payload)=>store.command(action,{operationId:crypto.randomUUID(),expectedRevision:(await store.list()).revision,payload});
  const root=await write('open',{kind:'group',sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[]});
  const fork=point=>write('fork',{...root,title:'换版',point,template:{memberId:'actor',versionId:'second'},player:null,session:null});
  await assert.rejects(fork(null),/ROUND_PENDING/);roundBusy=false;
  await assert.rejects(fork('m3'),/ANCHOR_HIDDEN/);
  await assert.rejects(fork('m2'),/ANCHOR_HIDDEN/);
  const tip=await fork(null),past=await fork('m1');
  const preview=(where,targetId)=>store.preview(where.familyId,{branchId:where.branchId,targetId,messageId:null,input:'核对'},'compare');
  const a=JSON.stringify((await preview(tip,'actor')).messages),p=JSON.stringify((await preview(tip,'peer')).messages);
  assert.match(a,/returned/);assert.match(a,/accepted-at-anchor/);assert.match(a,/created-after-anchor/);
  assert.doesNotMatch(a,/private-to-peer|joined-later|absent|peer-only-memory|invisible-source-memory|needs-human-review/);
  assert.match(p,/private-to-peer/);assert.match(p,/peer-only-memory/);assert.doesNotMatch(p,/accepted-at-anchor|new-persona/);
  for(let i=0;i<2;i++){
    const intent={branchId:past.branchId,targetId:'actor',input:'继续',messageId:null},operationId='past-send-'+i;
    const plan=await store.preview(past.familyId,intent,operationId);
    assert.match(JSON.stringify(plan.messages),/accepted-at-anchor/);assert.match(JSON.stringify(plan.messages),/accepted-with-prefix-proof/);
    assert.doesNotMatch(JSON.stringify(plan.messages),/created-after-anchor|returned|needs-human-review/);
    await store.command('send',{operationId,expectedRevision:plan.revision,payload:{familyId:past.familyId,intent,previewHash:plan.previewHash}});
  }
});

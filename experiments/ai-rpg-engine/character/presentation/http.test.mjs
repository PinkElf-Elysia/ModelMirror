import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {zipSync} from 'fflate';
import {startAuthoringServer} from '../../authoring/server.mjs';
import {sha256} from '../../authoring/contracts.mjs';
import {blankCard} from '../card.mjs';
import {placeholder} from '../png.mjs';
import {DailyWorkspace} from '../daily/workspace.mjs';
import {GroupStore} from '../group/store.mjs';
import {startGroupServer} from '../group/server.mjs';
import {offlineModel} from '../group/test-support.mjs';

test('same-origin group image and preference routes bind the sealed version; scene changes leave history, request assembly, budgets and export untouched',async t=>{
  const dir=await mkdtemp(join(tmpdir(),'r7-http-'));
  const workspace=await DailyWorkspace.open(dir);
  const studio=await startAuthoringServer({directory:join(dir,'studio'),creatorExperience:true,characterSettings:{workspace}});
  let store,app;
  t.after(async()=>{await app?.close();await store?.close();await studio.close();await workspace.close();});
  const api=(path,data)=>fetch(studio.origin+'/rpg-app/api/authoring'+path,data?{method:'POST',
    headers:{Origin:studio.origin,'x-authoring-request':'1','content-type':'application/json'},body:JSON.stringify(data)}:{});
  async function json(path,data){const r=await api(path,data);assert.equal(r.status,200);return r.json();}
  const png=placeholder(),path='assets/icon/images/lamp.png',card=blankCard();
  Object.assign(card,{spec:'chara_card_v3',spec_version:'3.0'});
  Object.assign(card.data,{name:'中性画面夹具',first_mes:'今天记什么？',group_only_greetings:[],
    assets:[['icon','main'],['emotion','微笑'],['background','灯室']].map(([type,name])=>({type,name,ext:'png',uri:'embeded://'+path}))});
  // Two complete Layer III frames; this test checks transport and ownership, not decoding.
  const frame=Buffer.alloc(417);Buffer.from('fffb9000','hex').copy(frame);
  const sound=Buffer.concat([frame,frame]),soundPath='assets/other/audio/quiet.mp3';
  card.data.assets.push({type:'other',name:'场间',ext:'mp3',uri:'embeded://'+soundPath},
    {type:'other',name:'远程',ext:'mp3',uri:'https://fixture.invalid/remote.mp3'});
  const raw=Buffer.from(zipSync({'card.json':Buffer.from(JSON.stringify(card)),[path]:png,[soundPath]:sound}));
  const {workId}=await json('/characters/imports',{operationId:'import',expectedHash:sha256(raw),selection:null,
    base64:raw.toString('base64'),fileName:'neutral.charx',origin:'fixture'});
  const work=await json('/works/'+workId);
  const {result:{versionId}}=await json('/works/'+workId+'/publish',{operationId:'seal',expectedRevision:work.revision});
  const reference={workId,versionId};
  store=await GroupStore.open({directory:join(dir,'groups'),works:studio.works,models:[offlineModel],
    authorization:{id:'fixture-tasks',mode:'fixture',calls:4,microUsd:1000000}});await store.recover();
  const {id}=await store.create({operationId:'group',expectedRevision:null,payload:{scene:'灯室',player:{id:null,name:'旅人'},
    members:[{reference,modelId:'fixture'},{reference,modelId:'fixture'}],opening:{memberIndex:0,kind:'ordinary',index:0},budget:{calls:2,microUsd:100000}}});
  app=await startGroupServer({store,works:studio.works,workspace});
  const cookie=(await fetch(app.origin+'/groups')).headers.get('set-cookie').split(';')[0];
  const {csrf}=await (await fetch(app.origin+'/api/bootstrap',{headers:{cookie}})).json();
  const endpoint=app.origin+'/api/appearance/group/'+id;
  const headers={cookie,origin:app.origin,'content-type':'application/json','x-group-csrf':csrf};
  const initial=await (await fetch(endpoint,{headers})).json(), member=initial.people[1], choice=member.expressions[0].key;
  const before=await store.read(id),context=await store.preview(id,member.id);
  const exported=async()=>Buffer.from(await (await api('/characters/charx/export',reference)).arrayBuffer());
  const bytes=await exported(),body=JSON.stringify({revision:initial.revision,memberId:member.id,role:'expression',key:choice});
  assert.equal((await fetch(endpoint)).status,403);
  assert.equal((await fetch(endpoint,{method:'POST',headers:{...headers,'x-group-csrf':''},body})).status,403);
  const saved=await fetch(endpoint,{method:'POST',headers,body});assert.equal(saved.status,200);
  assert.deepEqual((await saved.json()).expressions,{[member.id]:choice});
  assert.equal((await fetch(endpoint,{method:'POST',headers,body})).ok,false);
  const image=await fetch(endpoint+'/image/'+choice,{headers});assert.equal(image.headers.get('content-type'),'image/png');
  assert.deepEqual(Buffer.from(await image.arrayBuffer()),png);
  assert.equal(member.media.length,1);
  const track=member.media[0].key,url=endpoint+'/media/'+track;
  assert.notEqual(initial.people[0].media[0].key,track);
  assert.equal((await fetch(url)).status,403);
  assert.equal((await fetch(url,{headers:{...headers,origin:'https://fixture.invalid'}})).status,403);
  const full=await fetch(url,{headers});
  assert.equal(full.status,200);assert.equal(full.headers.get('content-type'),'audio/mpeg');
  assert.match(full.headers.get('cache-control'),/no-store/);
  assert.deepEqual(Buffer.from(await full.arrayBuffer()),sound);
  for(const [range,start,end] of [['bytes=0-3',0,3],['bytes=-2',sound.length-2,sound.length-1],['bytes=7-',7,sound.length-1]]){
    const part=await fetch(url,{headers:{...headers,range}});
    assert.equal(part.status,206);assert.equal(part.headers.get('content-range'),`bytes ${start}-${end}/${sound.length}`);
    assert.deepEqual(Buffer.from(await part.arrayBuffer()),sound.subarray(start,end+1));
  }
  for(const range of ['bytes=1000-','bytes=2-1','bytes=0-1,3-4','bytes=-0','bytes=-']){
    const invalid=await fetch(url,{headers:{...headers,range}});
    assert.equal(invalid.status,416);assert.equal(invalid.headers.get('content-range'),'bytes */'+sound.length);
    assert.equal((await invalid.arrayBuffer()).byteLength,0);
  }
  assert.equal((await fetch(endpoint+'/image/'+track,{headers})).ok,false);
  assert.equal((await fetch(endpoint+'/media/'+choice,{headers})).ok,false);
  assert.equal((await api('/characters/appearance/preview/'+workId+'/'+versionId+'/media/'+track)).ok,false);
  assert.deepEqual(await store.read(id),before);assert.deepEqual(await store.preview(id,member.id),context);
  assert.deepEqual(await exported(),bytes);
  for(const action of ['leave','rejoin']){
    await store.change(id,action,{operationId:action,expectedRevision:(await store.read(id)).revision,payload:{memberId:member.id}});
    assert.equal((await fetch(url,{headers})).ok,false);
  }
  const rejoined=await (await fetch(endpoint,{headers})).json();
  assert.notEqual(rejoined.people.find(p=>p.id===member.id).media[0].key,track);
});

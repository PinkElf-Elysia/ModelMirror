import test from 'node:test';
import assert from 'node:assert/strict';
import {deflateSync} from 'node:zlib';
import {zipSync,unzipSync} from 'fflate';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startAuthoringServer} from '../authoring/server.mjs';
import {sha256} from '../authoring/contracts.mjs';
import {blankCard} from './card.mjs';
import {chunk,placeholder,pngChunks} from './png.mjs';
import {inspectAnimatedPortrait,portraitLimits} from './apng.mjs';
import {inspectCharx} from './charx.mjs';

const sig=placeholder().subarray(0,8);
const integer=n=>{const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;};
function frame(seq,w,h){
  const b=Buffer.alloc(26);integer(seq).copy(b);integer(w).copy(b,4);integer(h).copy(b,8);
  b.writeUInt16BE(1,20);b.writeUInt16BE(8,22);return b;
}
function fixture({count=2,width=1,height=1,seq=1,offset=0,defaultOnly=false,broken=false,filter=0}={}){
  const h=Buffer.from(pngChunks(placeholder())[0].data);h.writeUInt32BE(width);h.writeUInt32BE(height,4);
  const control=frame(defaultOnly?0:seq,width,height);control.writeUInt32BE(offset,12);
  const encoded=deflateSync(Buffer.from([filter,160,35,89,255]));
  return Buffer.concat([sig,chunk('IHDR',h),chunk('acTL',Buffer.concat([integer(count),integer(2)])),
    ...defaultOnly?[]:[chunk('fcTL',frame(0,width,height))],chunk('IDAT',encoded),
    chunk('fcTL',control),chunk('fdAT',Buffer.concat([integer(defaultOnly?1:2),broken?Buffer.from('broken'):encoded])),
    chunk('IEND',Buffer.alloc(0))]);
}
test('both APNG default-image arrangements yield an independent nonanimated PNG',()=>{
  for(const data of [fixture(),fixture({defaultOnly:true,count:1})]){
    const decoded=inspectAnimatedPortrait(data);
    assert.deepEqual(pngChunks(decoded.poster).map(c=>c.type),['IHDR','IDAT','IEND']);
    assert.equal(decoded.plays,2);assert.deepEqual(decoded.playback,data);
  }
});
test('limits apply before inflating: bytes, frame count, canvas area and total decode work',()=>{
  for(const bytes of [Buffer.alloc(portraitLimits.bytes+1),fixture({count:61}),fixture({width:1100,height:1100}),fixture({width:1024,height:1024,count:12})])assert.throws(()=>inspectAnimatedPortrait(bytes));
});
test('rejects bad sequence, subframe extent, filter, compressed pixels, frame count and CRC',()=>{
  const crc=fixture();crc[crc.length-1]^=1;
  for(const bytes of [crc,fixture({seq:3}),fixture({offset:1}),fixture({broken:true}),fixture({filter:5}),fixture({count:3})])assert.throws(()=>inspectAnimatedPortrait(bytes));
});
test('CHARX admits checked main portraits only; ordinary PNG, unrelated animations and damaged archives retain their own paths',async()=>{
  for(const [ext,type,image,classification] of [['png','icon',placeholder(),'images'],['apng','icon',fixture(),'images'],['png','icon',fixture(),'images'],['png','emotion',fixture(),'original-only'],['png','icon',fixture({broken:true}),'original-only']]){
    const path='icon.'+ext,card=blankCard();Object.assign(card,{spec:'chara_card_v3',spec_version:'3.0'});
    Object.assign(card.data,{group_only_greetings:[],assets:[{name:'main',type,ext,uri:'embeded://'+path}]});
    const report=await inspectCharx(Buffer.from(zipSync({'card.json':Buffer.from(JSON.stringify(card)),[path]:image})));
    assert.equal(report.classification,classification);
  }
});

test('sealed APNG keeps exact source bytes across a new cover and restart; scoped handles cannot cross versions',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'portrait-'));
  let server=await startAuthoringServer({directory,creatorExperience:true});
  t.after(()=>server.close());
  const api=(route,input)=>fetch(server.origin+'/rpg-app/api/authoring'+route,input?{
    method:'POST',headers:{origin:server.origin,'content-type':'application/json','x-authoring-request':'1'},body:JSON.stringify(input)}:{});
  async function json(route,input){const res=await api(route,input);if(!res.ok)assert.fail(route+" "+await res.text());return res.json();}
  async function bytes(route,input){const res=await api(route,input);assert.ok(res.ok);return Buffer.from(await res.arrayBuffer());}
  const card=blankCard(),original=fixture(),path='assets/main.apng';
  Object.assign(card,{spec:'chara_card_v3',spec_version:'3.0'});
  Object.assign(card.data,{name:'纸灯',first_mes:'今天点哪盏灯？',group_only_greetings:[],assets:[{type:'icon',name:'main',ext:'apng',uri:'embeded://'+path}]});
  const source=Buffer.from(zipSync({'card.json':Buffer.from(JSON.stringify(card)),[path]:original}));
  const {workId}=await json('/characters/imports',{operationId:'import',selection:null,expectedHash:sha256(source),
    base64:source.toString('base64'),fileName:'lamp.charx',origin:'fixture'});
  const work='/works/'+workId,scope='/characters/appearance/preview/'+workId+'/';
  async function seal(operationId){return (await json(work+'/publish',{operationId,expectedRevision:(await json(work)).revision})).result.versionId;}
  const v1=await seal('first'),exported=()=>bytes('/characters/charx/export',{workId,versionId:v1});
  const old=await exported();
  assert.deepEqual(Buffer.from(unzipSync(old)[path]),original);
  const member=(await json(scope+v1)).people[0],key=member.animation.key;
  assert.deepEqual(await bytes(scope+v1+'/animation/'+key),inspectAnimatedPortrait(original).playback);
  assert.deepEqual(await bytes(scope+v1+'/image/'+member.portrait.key),inspectAnimatedPortrait(original).poster);
  assert.equal((await api(scope+v1+'/image/'+key)).ok,false);
  await json('/characters/portrait',{workId,operationId:'cover',expectedRevision:(await json(work)).revision,base64:placeholder().toString('base64')});
  const updated=await json(work);updated.draft.settings.media.cover='portrait';
  await json(work+'/save',{operationId:'change',expectedRevision:updated.revision,document:updated.draft});
  const v2=await seal('second');
  await server.close();server=await startAuthoringServer({directory,creatorExperience:true});
  assert.deepEqual(await exported(),old);assert.equal((await json(scope+v2)).people[0].animation,undefined);
  assert.equal((await api(scope+v2+'/animation/'+key)).ok,false);
  assert.equal((await json(scope+v1)).people[0].animation.key,key);
});

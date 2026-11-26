import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {zipSync} from 'fflate';
import {startAuthoringServer} from '../authoring/server.mjs';
import {LIMITS,sha256} from '../authoring/contracts.mjs';
import {openCharx} from './charx.mjs';
import {blankCard} from './card.mjs';
import {chunk,placeholder} from './png.mjs';

const path='assets/icon/images/lamp.png',png=placeholder();
function pack(name,image=png,extras={}) {
  const card=blankCard();Object.assign(card,{spec:'chara_card_v3',spec_version:'3.0'});
  Object.assign(card.data,{name,first_mes:'今天点亮哪盏纸灯？',group_only_greetings:[],
    extensions:{neutral:{preserve:[1,2]}},assets:[{name:'main',type:'icon',ext:'png',uri:'embeded://'+path},
      {name:'远景',type:'background',ext:'png',uri:'embeded://'+path},{name:'纸云',type:'emotion',ext:'png',uri:'https://fixture.invalid/cloud.png'}]});
  return Buffer.from(zipSync({'card.json':Buffer.from(JSON.stringify(card)),[path]:image,...extras}));
}
async function fixture(t,fault) {
  const directory=await mkdtemp(join(tmpdir(),'charx-version-'));
  let server=await startAuthoringServer({directory,creatorExperience:true,fault});
  t.after(()=>server.close());
  async function request(path,body) {
    return fetch(server.origin+'/rpg-app/api/authoring'+path,body?{method:'POST',headers:{origin:server.origin,'x-authoring-request':'1','content-type':'application/json'},body:JSON.stringify(body)}:{});
  }
  async function json(path,body) {
    const res=await request(path,body),value=await res.json();assert.equal(res.ok,true,JSON.stringify(value));return value;
  }
  function intent(bytes,id){return {base64:bytes.toString('base64'),expectedHash:sha256(bytes),selection:null,fileName:'纸灯.charx',origin:'原创中性夹具',operationId:id};}
  return {request,json,intent,async restart(){await server.close();server=await startAuthoringServer({directory,creatorExperience:true});}};
}
const imports='/characters/imports';
const resources=(w,v)=>'/characters/charx/resources?work='+w+(v?'&version='+v:'');
const imageUrl=(w,v)=>resources(w,v).replace('/resources?','/image?')+'&path='+encodeURIComponent(path);
test('confirm binds all resources once, rejects switched sources and preserves prior version and session',async t=>{
  const {json,request,intent,restart}=await fixture(t);
  const bytes=pack('中性纸灯'),body=intent(bytes,'card-a');
  const info=await json(imports+'/inspect',{base64:body.base64,selection:null});
  assert.equal(info.source.sha256,body.expectedHash);
  const invalid=await request(imports,{...body,base64:pack('换包').toString('base64')});
  assert.equal((await invalid.json()).error.code,'CHARACTER_IMPORT_CHANGED');
  const mixed=await request(imports,intent(pack('混合',png,{'notes.txt':Buffer.from('说明')}),'mixed'));
  assert.equal((await mixed.json()).error.code,'CHARACTER_CHARX_ORIGINAL_ONLY');
  assert.deepEqual(await json('/characters/works'),[]);
  const [a,b]=await Promise.all([json(imports,body),json(imports,{...body,operationId:'other-tab'})]);
  assert.equal(a.workId,b.workId);const id=a.workId,w='/works/'+id;
  assert.equal((await json('/characters/works')).length,1);
  const first=await json(w);assert.equal(first.revision,0);assert.equal(first.assets.length,1);assert.equal(first.draft.settings.media.cover,first.assets[0].alias);
  assert.deepEqual(first.draft.character.card.data.extensions,{neutral:{preserve:[1,2]}});
  const list=await json(resources(id));assert.equal(list.rows.length,3);assert.equal(list.rows[0].sha256,sha256(png));assert.equal(list.rows[1].sha256,list.rows[0].sha256);
  assert.equal((await request(imageUrl(id)+'&hash='+'0'.repeat(64))).ok,false);
  const v=(await json(w+'/publish',{operationId:'first-version',expectedRevision:first.revision})).result.versionId;
  const s=await json('/characters/sessions',{operationId:'old-chat',reference:{workId:id,versionId:v},openingIndex:0,player:{id:null,name:'访客'}});
  assert.equal(s.messages.length,1);
  const updated=await json(w);updated.draft.character.card.data.description='新的描述';updated.draft.character.card.data.assets=[];
  await json(w+'/save',{operationId:'new-draft',expectedRevision:updated.revision,document:updated.draft});
  assert.equal((await json(resources(id))).rows.length,0);
  assert.equal((await request(imageUrl(id))).ok,false);
  const other=await json(imports,intent(pack('第二位纸灯'),'second'));
  assert.equal((await request(imageUrl(other.workId,v))).ok,false);
  assert.equal((await request(imports,intent(pack('第三位纸灯'),'card-a'))).ok,false);
  await restart();
  const old=await json('/characters/sessions/'+s.id);
  assert.deepEqual(old.binding.reference,s.binding.reference);assert.deepEqual(old.messages,s.messages);
  assert.deepEqual(await json(resources(id,v)),list);
  assert.deepEqual(Buffer.from(await (await request(imageUrl(id,v))).arrayBuffer()),png);
  const original=await request('/characters/source?work='+id+'&version='+v);
  assert.match(original.headers.get('content-disposition'),/\.charx/);
  assert.deepEqual(Buffer.from(await original.arrayBuffer()),bytes);
  assert.equal((await json(imports,body)).workId,id);
  assert.equal((await json(w)).draft.character.card.data.description,'新的描述');
});
test('larger static pictures stay in the local package and resolve by version',async t=>{
  const {json,request,intent}=await fixture(t);
  const large=Buffer.concat([png.subarray(0,33),chunk('raNd',Buffer.alloc(LIMITS.imageBytes)),png.subarray(33)]);
  const {workId}=await json(imports,intent(pack('大图纸灯',large),'large'));
  const work=await json('/works/'+workId);assert.equal(work.assets.length,0);
  const v=(await json('/works/'+workId+'/publish',{operationId:'large-version',expectedRevision:0})).result.versionId;
  assert.equal((await json('/characters/works'))[0].charxCover,path);
  assert.equal((await json(resources(workId,v))).rows[0].sha256,sha256(large));
  assert.deepEqual(Buffer.from(await (await request(imageUrl(workId,v))).arrayBuffer()),large);
  const response=await request('/characters/charx/export',{workId,versionId:v});
  assert.equal(response.status,200);
  const exported=await openCharx(Buffer.from(await response.arrayBuffer()));
  assert.deepEqual(exported.files.get(path),large);
});
test('exports only the chosen sealed version and reimports it without losing shared images or extensions',async t=>{
  const {json,request,intent,restart}=await fixture(t);
  const original=pack('版本夹具'),{workId}=await json(imports,intent(original,'export-origin'));
  const work='/works/'+workId,route='/characters/charx/export';
  const publish=async operationId=>(await json(work+'/publish',{operationId,expectedRevision:(await json(work)).revision})).result.versionId;
  const v1=await publish('version-one');
  const session=await json('/characters/sessions',{operationId:'read-only-session',reference:{workId,versionId:v1},openingIndex:0,player:{id:null,name:'PRIVATE_PLAYER_775'}});
  await json('/characters/sessions/'+session.id+'/memory',{operationId:'private-memory',expectedRevision:session.revision,change:{action:'create',value:{name:'私有记忆',content:'PRIVATE_MEMORY_775',enabled:true,match:'any',roles:['user'],keywords:['私有']}}});
  const changed=Buffer.concat([png.subarray(0,33),chunk('raNd',Buffer.from('replacement')),png.subarray(33)]);
  const upload=await json('/characters/portrait',{workId,expectedRevision:(await json(work)).revision,operationId:'replace-portrait',base64:changed.toString('base64')});
  assert.ok(upload);
  const after=await json(work),draft=after.draft;
  draft.settings.media.cover='portrait';
  draft.character.card.data.description='第二版\n中文多行';
  draft.character.card.data.assets[0].vendor={keep:[3,2,1]};
  await json(work+'/save',{operationId:'save-two',expectedRevision:after.revision,document:draft});
  const v2=await publish('version-two');
  const later=await json(work);later.draft.character.card.data.description='仅在草稿';
  await json(work+'/save',{operationId:'draft-only',expectedRevision:later.revision,document:later.draft});
  const download=async(versionId)=>{
    const res=await request(route,{workId,versionId});assert.equal(res.status,200);
    assert.match(res.headers.get('content-disposition'),/\.charx/);return Buffer.from(await res.arrayBuffer());
  };
  const b1=await download(v1),b2=await download(v2);
  assert.deepEqual(await download(v1),b1);
  const a=await openCharx(b1),b=await openCharx(b2),source=await openCharx(original);
  assert.deepEqual(a.card,source.card);assert.deepEqual(a.files.get(path),png);
  assert.deepEqual([...a.files.keys()].sort(),['card.json',path].sort());
  const [icon,bg,remote]=b.card.data.assets;
  const newPath=icon.uri.slice('embeded://'.length);
  assert.notEqual(newPath,path);assert.deepEqual(icon.vendor,{keep:[3,2,1]});
  assert.deepEqual(bg,source.card.data.assets[1]);assert.deepEqual(remote,source.card.data.assets[2]);
  assert.deepEqual([...b.files.keys()].sort(),['card.json',path,newPath].sort());
  assert.deepEqual(b.files.get(newPath),changed);assert.deepEqual(b.files.get(path),png);
  assert.equal(b.card.data.description,'第二版\n中文多行');assert.deepEqual(b.card.data.extensions,source.card.data.extensions);
  assert.doesNotMatch(b.files.get('card.json').toString(),/PRIVATE_|仅在草稿|work-|session-|version-/);
  await restart();assert.deepEqual(await download(v2),b2);
  const again=await json(imports,intent(b2,'roundtrip'));
  const restored=await json('/works/'+again.workId);
  assert.deepEqual(restored.draft.character.card,b.card);
  assert.equal(restored.assets.find(x=>x.alias===restored.draft.settings.media.cover).sha256,sha256(changed));
  assert.deepEqual(Buffer.from(await (await request('/characters/source?work='+workId+'&version='+v2)).arrayBuffer()),original);
  for(const body of [{workId,versionId:null},{workId:again.workId,versionId:v2},{workId,versionId:v2,assets:[]}]){
    assert.equal((await request(route,body)).ok,false);
  }
});
test('native V3 versions export without an archive binding; a V2 version is refused',async t=>{
  const {json,request,intent}=await fixture(t);
  for(const v3 of [true,false]){
    const card=blankCard();card.data.name='自建角色';card.data.first_mes='你好';
    if(v3){card.spec='chara_card_v3';card.spec_version='3.0';card.data.group_only_greetings=[];}
    const body=intent(Buffer.from(JSON.stringify(card)),v3?'plain-v3':'plain-v2');
    const {workId}=await json(imports,{...body,fileName:'fixture.json'});
    const {result}=await json('/works/'+workId+'/publish',{operationId:'publish-json',expectedRevision:0});
    const res=await request('/characters/charx/export',{workId,versionId:result.versionId});
    if(v3){
      assert.equal(res.status,200);
      const decoded=await openCharx(Buffer.from(await res.arrayBuffer()));
      assert.deepEqual(decoded.card,card);assert.deepEqual([...decoded.files.keys()],['card.json']);
    }else{assert.equal(res.ok,false);assert.equal((await res.json()).error.code,'CHARACTER_CHARX_EXPORT_INVALID');}
  }
});
test('version export rejects a declared image that the version cannot resolve',async t=>{
  const {json,request,intent}=await fixture(t);
  const {workId}=await json(imports,intent(pack('缺图夹具'),'create-missing'));
  const work='/works/'+workId, first=await json(work);
  first.draft.character.card.data.assets[1].uri='embeded://assets/missing.png';
  await json(work+'/save',{operationId:'change-path',expectedRevision:first.revision,document:first.draft});
  const v=(await json(work+'/publish',{operationId:'seal-missing',expectedRevision:1})).result.versionId;
  const response=await request('/characters/charx/export',{workId,versionId:v});
  const {error}=await response.json();assert.equal(error.code,'CHARACTER_CHARX_EXPORT_INVALID');assert.equal(error.path,'assets/missing.png');
});
for(const phase of ['beforeCommit','afterCommit']) test('restart after '+phase+' leaves one complete work on explicit confirmation',async t=>{
  let once=true;
  const {json,request,intent,restart}=await fixture(t,(stage,op)=>{
    if(once && stage===phase && op.operationId.startsWith('charx-')) {once=false;throw Error('neutral fixture interruption');}
  });
  const body=intent(pack('中断夹具'),phase);assert.equal((await request(imports,body)).ok,false);
  await restart();const result=await json(imports,body);
  const works=await json('/characters/works');assert.equal(works.length,1);assert.equal(works[0].id,result.workId);
  assert.equal((await json(resources(result.workId))).rows[0].sha256,sha256(png));
});

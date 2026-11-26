import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {openPresentation} from './store.mjs';
import {sha256} from '../../authoring/contracts.mjs';

const key=(type,letter)=>type+'-'+letter.repeat(64);
const ref=letter=>({workId:key('work','f'),versionId:key('version',letter)});
const group='group/'+key('group','a'), other='group/'+key('group','b');
const rows=[['main','icon'],['笑','emotion'],['湾','background']].map(([name,type],i)=>({
  name,type,status:'图片',main:i===0,path:name+'.png',mediaType:'image/png',sha256:sha256(name)
}));
const works={verifyConfiguration:async()=>({assets:[],document:{kind:'character',settings:{media:{cover:null}},character:{card:{data:{name:'同名'}}}}})};
const imports={view:async()=>({rows:[...rows,{type:'emotion',status:'仅保留引用',path:'https://fixture.invalid/face.png'}]}),
  image:async(w,v,path,hash)=>({bytes:Buffer.from(path),mediaType:'image/png',hash})};
async function fixture(t){
  const directory=await mkdtemp(join(tmpdir(),'look-'));
  const members=[{id:'first',reference:ref('a'),period:1},{id:'second',reference:ref('b'),period:1}];
  let store;const open=async()=>store=await openPresentation({directory,works,imports,resolveMembers:async()=>members});
  await open();t.after(()=>store.close());
  return {members,read:(...args)=>store.access(...args),async restart(){await store.close();await open();}};
}
const edit=(view,role,memberId,key)=>({revision:view.revision,role,memberId,key});
test('version preview is read only, defaults are independent, handles cannot cross context or purpose',async t=>{
  const f=await fixture(t), a=await f.read(group), b=await f.read(other);
  const preview='preview/'+ref('a').workId+'/'+ref('a').versionId, pv=await f.read(preview);
  assert.equal(pv.people[0].expressions.length,1);
  await assert.rejects(f.read(preview,edit(pv,'expression','character',null)));
  const one=a.people[0],two=a.people[1],face=one.expressions[0].key;
  for(const [view,scope,role,id] of [[a,group,'background',one.id],[a,group,'expression',two.id],[b,other,'expression',one.id]]) {
    await assert.rejects(f.read(scope,edit(view,role,id,face)));
  }
  await assert.rejects(f.read(other,null,face));
  const changed=await f.read(group,edit(a,'expression',one.id,face));
  assert.deepEqual(changed.expressions,{first:face});
  await assert.rejects(f.read(group,edit(a,'expression',one.id,null)),{code:'CHARACTER_APPEARANCE_CONFLICT'});
  assert.deepEqual(await f.read(other),b);
  await f.restart();assert.deepEqual(await f.read(group),changed);
  assert.equal((await f.read(group,null,face)).bytes.toString(),'笑.png');
  const cleared=await f.read(group,edit(changed,'expression',one.id,null));
  assert.notEqual(cleared.revision,a.revision);
});
test('absence clears background across a whole unseen leave/rejoin cycle; other member artwork stays',async t=>{
  const f=await fixture(t);let view=await f.read(group);
  const face=view.people[1].expressions[0].key,sky=view.people[0].backgrounds[0].key;
  view=await f.read(group,edit(view,'expression','second',face));
  await f.read(group,edit(view,'background','first',sky));
  f.members[0].period++;
  view=await f.read(group);
  assert.equal(view.background,null);assert.equal(view.expressions.second,face);
  await assert.rejects(f.read(group,null,sky));
  f.members.shift();assert.deepEqual((await f.read(group)).people.map(p=>p.id),['second']);
});
test('branch identity scopes choices to the exact family and branch; same character on child starts at defaults',async t=>{
  const f=await fixture(t), prefix='branch/'+key('family','a')+'/', source=prefix+key('branch','b'), child=prefix+key('branch','c');
  const resolve=async()=>f.members;
  const parent=await f.read(source,null,null,resolve),face=parent.people[0].expressions[0].key;
  await f.read(source,edit(parent,'expression','first',face),null,resolve);
  const fresh=await f.read(child,null,null,resolve);assert.deepEqual(fresh.expressions,{});assert.equal(fresh.background,null);
  await assert.rejects(f.read(child,null,face,resolve));
  assert.equal((await f.read(source,null,null,resolve)).expressions.first,face);
});

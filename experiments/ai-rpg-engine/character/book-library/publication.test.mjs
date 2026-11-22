import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startPreview} from './preview.mjs';
import {pin} from './bindings.mjs';

test('标准创作入口固定两个角色的书；归档、发布新版与重启均不替换原版',async t=>{
  const root=process.env.CHARACTER_R5A_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const dir=await mkdtemp(join(root,'publish-books-'));let host=await startPreview(dir);t.after(()=>host.close());
  let state=await host.api('catalog'),v1=pin(state.books[0].versions[0]);
  const references=[];
  for(const role of state.roles){
    await host.write('role-draft',{workId:role.id,workRevision:role.revision,refs:[v1,v1]});
    const origin=new URL(host.url).origin,url=origin+'/rpg-app/api/authoring/works/'+role.id+'/publish';
    const post=async()=>{const r=await fetch(url,{method:'POST',headers:{origin,'content-type':'application/json','x-authoring-request':'1'},body:JSON.stringify({operationId:'regular-publish',expectedRevision:role.revision})});assert.equal(r.status,200);return r.json();};
    const v=await post();assert.deepEqual(await post(),v);references.push({workId:role.id,versionId:v.result.versionId});
  }
  const group=await host.store.create({operationId:'two-bound',expectedRevision:null,payload:{player:{id:null,name:'过客'},scene:'书库',members:references.map(reference=>({reference,modelId:'fixture'})),opening:null,budget:{calls:8,microUsd:10000}}});
  const kind='group',sourceId=group.id,source=await host.api('source',{kind,sourceId});
  const parent=await host.write('open',{kind,sourceId,sourceHash:source.sourceHash,player:[],session:[]});
  const d=await host.api('draft',{bookId:v1.bookId});d.book.entries[0].content='已更改';await host.write('save',{bookId:v1.bookId,title:d.title,book:d.book});await host.write('seal',{bookId:v1.bookId});await host.write('archive',{bookId:v1.bookId});
  // Same template, new instance. An open consent form from before this join must be stale.
  const ahead=await host.api('source',{kind,sourceId});
  const g=await host.store.read(sourceId);await host.store.change(sourceId,'join',{operationId:'late-join',expectedRevision:g.revision,payload:{reference:references[0],modelId:'fixture'}});
  await assert.rejects(host.write('open',{kind,sourceId,sourceHash:ahead.sourceHash,session:[],player:[]}),/SOURCE_CHANGED/);
  await host.close();host=await startPreview(dir);
  for(const m of source.members){
    const v=await host.api('view',{...parent,targetId:m.id});assert.deepEqual(v.bindings.map(b=>b.selected),[v1]);assert.equal(v.members.length,2);
    const plan=await host.api('preview',{familyId:parent.familyId,intent:{branchId:parent.branchId,targetId:m.id,messageId:null,input:'渡口'},operationId:'look'});
    assert.equal(plan.books.sources[0].selected.hash,v1.hash);assert.doesNotMatch(JSON.stringify(plan.messages),/已更改/);
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startAuthoringServer} from '../../authoring/server.mjs';
import {blankCard} from '../card.mjs';

test('organization, duplicate inspection and provenance stay outside published cards and sessions',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'library-'));
  let server=await startAuthoringServer({directory,creatorExperience:true});
  t.after(()=>server.close());
  async function call(path,body){
    const res=await fetch(server.origin+'/rpg-app/api/authoring'+path,{method:body?'POST':'GET',headers:body?
      {origin:server.origin,'content-type':'application/json','x-authoring-request':'1'}:{},body:body?JSON.stringify(body):undefined});
    const result=await res.json();if(!res.ok)throw new Error(result.error?.code??JSON.stringify(result));return result;
  }
  const src=blankCard();Object.assign(src.data,{name:'双名',description:'潮尺',first_mes:'开始',creator_notes:'仅限本地展示'});
  src.data.extensions={unknown:{kept:true}};
  const inspect=raw=>call('/characters/imports/inspect',{base64:Buffer.from(raw).toString('base64'),selection:null});
  const add=async(raw,op)=>{
    const info=await inspect(raw);
    return (await call('/characters/imports'+(info.duplicates.length?'/new':''),{operationId:op,expectedHash:info.source.sha256,
      base64:Buffer.from(raw).toString('base64'),fileName:'tide.json',origin:'观测站手记',selection:null})).workId;
  };
  const raw=JSON.stringify(src),a=await add(raw,'first'),published=await call('/works/'+a+'/publish',{operationId:'publish',expectedRevision:0});
  const version=published.result.versionId,session=await call('/characters/sessions',{operationId:'start',reference:{workId:a,versionId:version},openingIndex:0,player:{id:null,name:'记录员'}});
  const first=await call('/works/'+a+'/published?version='+version),originalSession=await call('/characters/sessions/'+session.id);
  assert.deepEqual((await inspect(raw)).duplicates.map(x=>x.relation),['file']);
  const formatted=JSON.stringify(src,null,2);assert.deepEqual((await inspect(formatted)).duplicates.map(x=>x.relation),['definition']);
  const different=structuredClone(src);different.data.extensions.unknown.kept=false;assert.deepEqual((await inspect(JSON.stringify(different))).duplicates,[]);
  const b=await add(formatted,'second');assert.notEqual(a,b);
  const provenance=await call('/characters/library/source?work='+a);
  assert.ok(provenance.origins.some(o=>o.origin==='观测站手记'));assert.equal(provenance.creatorNotes,'仅限本地展示');
  const change={operationId:'both',expectedRevision:0,ids:[a,b],action:'add-tag',value:'沿岸'};
  const edit=body=>call('/characters/library',body);
  const tagged=await edit(change);assert.ok(tagged.canUndo);assert.deepEqual(tagged.items[a].tags,['沿岸']);assert.deepEqual(await edit(change),tagged);
  await assert.rejects(edit({...change,value:'换标签'}),/COLLISION/);
  await assert.rejects(edit({...change,operationId:'stale'}),/CONFLICT/);
  await assert.rejects(edit({...change,expectedRevision:1,ids:[a,'work-'+'e'.repeat(64)],operationId:'missing'}));
  assert.deepEqual(await call('/characters/library'),tagged);
  const race=await Promise.allSettled(['tab-a','tab-b'].map(operationId=>edit({operationId,expectedRevision:1,ids:[a],action:'favorite',value:true})));
  assert.equal(race.filter(r=>r.status==='fulfilled').length,1);
  await server.close();server=await startAuthoringServer({directory,creatorExperience:true});
  const current=await call('/characters/library');assert.equal(current.revision,2);assert.equal(current.items[a].favorite,true);
  const undone=await edit({operationId:'undo',expectedRevision:2,ids:[],action:'undo',value:null});
  assert.deepEqual(undone.items,tagged.items);assert.equal(undone.canUndo,false);
  const work=await call('/works/'+a),copied=await call('/works/'+a+'/copy',{operationId:'copy',expectedRevision:work.revision,name:'潮尺副本'});
  const copy=await call('/works/'+copied.workId);assert.equal(copy.origin.workId,a);assert.deepEqual(copy.versions,[]);
  assert.equal(copy.draft.character.card.data.name,'潮尺副本');assert.deepEqual((await call('/works/'+a)).draft.character.card,src);
  const exported=await call('/characters/export',{workId:a,versionId:version,format:'json',version:'original',acceptLoss:false});
  assert.deepEqual(JSON.parse(Buffer.from(exported.base64,'base64')),src);
  assert.deepEqual(await call('/works/'+a+'/published?version='+version),first);assert.deepEqual(await call('/characters/sessions/'+session.id),originalSession);
});

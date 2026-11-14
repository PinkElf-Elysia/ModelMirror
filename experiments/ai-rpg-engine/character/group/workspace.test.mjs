import {mkdtemp,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {request as rawRequest} from 'node:http';
import test from 'node:test';import assert from 'node:assert/strict';
import {startAuthoringServer} from '../../authoring/server.mjs';
import {GroupStore} from './store.mjs';import {startGroupServer} from './server.mjs';
import {offlineModel} from './test-support.mjs';

test('sealed studio versions enter group catalog while old groups keep their exact binding across recovery',async t=>{
 const root=process.env.CHARACTER_R2_TEST_ROOT||'C:/tmp/mm-cr2-data/tests';await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'flow-'));
 const settings={};const studio=await startAuthoringServer({directory:join(directory,'studio'),creatorExperience:true,characterSettings:settings});
 let store,app;t.after(async()=>{await app?.close();await store?.close();await studio.close();});
 const api=async(path,body)=>{const r=await fetch(studio.origin+'/rpg-app/api/authoring'+path,{method:body?'POST':'GET',headers:body?{Origin:studio.origin,'Content-Type':'application/json','x-authoring-request':'1'}:{},body:body?JSON.stringify(body):undefined});assert.equal(r.status,200);return r.json();};
 const doc=await api('/characters/blank');doc.character.card.data.name='原创流程夹具';doc.character.card.data.first_mes='旧开场';doc.metadata.name='原创流程夹具';
 const w=await api('/works',{operationId:'make',expectedRevision:null,document:doc});const v1=await api('/works/'+w.workId+'/publish',{operationId:'v1',expectedRevision:0});
 const reference={workId:w.workId,versionId:v1.result.versionId};const config=await studio.works.verifyConfiguration(reference.workId,reference.versionId);
 const options={directory:join(directory,'groups'),works:studio.works,models:[offlineModel],authorization:{id:'offline-flow',mode:'fixture',calls:20,microUsd:1000000}};
 store=await GroupStore.open(options);await store.recover();app=await startGroupServer({store,works:studio.works,charactersUrl:studio.origin+'/characters'});settings.groupsUrl=app.origin+'/groups';
 // The character host was initialized above. A late settings mutation must not change its navigation snapshot.
 assert.equal((await api('/characters/navigation')).groupsUrl,null);
 const page=await fetch(app.origin+'/groups');const cookie=page.headers.get('set-cookie').split(';')[0];
 const bootstrap=async()=>{const r=await fetch(app.origin+'/api/bootstrap',{headers:{cookie}});assert.equal(r.status,200);return r.json();};
 const initial=await bootstrap();assert.equal(initial.cards.length,1);assert.equal(initial.charactersUrl,studio.origin+'/characters');
 const make=operationId=>({operationId,expectedRevision:null,payload:{player:{id:null,name:'玩家'},scene:'相遇',members:[{reference,modelId:'fixture'},{reference,modelId:'fixture'}],opening:{memberIndex:0,kind:'ordinary',index:0},budget:{calls:5,microUsd:100000}}});
 const old=await store.create(make('old'));const before=await store.read(old.id);const work=await studio.works.read(w.workId);const changed=structuredClone(work.draft);changed.character.card.data.first_mes='新开场';
 await api('/works/'+w.workId+'/save',{operationId:'edit',expectedRevision:work.revision,document:changed});assert.equal((await bootstrap()).cards.length,1);
 const saved=await studio.works.read(w.workId);const v2=await api('/works/'+w.workId+'/publish',{operationId:'v2',expectedRevision:saved.revision});
 const catalog=await bootstrap();assert.equal(catalog.cards.length,2);assert.ok(catalog.cards.some(c=>c.reference.versionId===v2.result.versionId));
 const input=make('new');input.payload.members[0].reference={workId:w.workId,versionId:v2.result.versionId};const made=await store.create(input);assert.equal((await store.view(made.id)).messages[0].content,'新开场');
 assert.deepEqual(await store.read(old.id),before);assert.deepEqual(await studio.works.verifyConfiguration(reference.workId,reference.versionId),config);
 await app.close();app=null;await store.close();store=await GroupStore.open(options);await store.recover();assert.deepEqual(await store.read(old.id),before);assert.equal((await store.view(old.id)).messages[0].content,'旧开场');
 app=await startGroupServer({store,works:studio.works});
 const status=await new Promise((resolve,reject)=>{const req=rawRequest(app.origin+'/groups?create=1',{headers:{'sec-fetch-site':'same-site','sec-fetch-mode':'navigate','sec-fetch-dest':'document'}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});req.on('error',reject);req.end();});assert.equal(status,200);
 assert.equal((await fetch(app.origin+'/api/bootstrap',{headers:{'sec-fetch-site':'same-site'}})).status,403);
 assert.equal((await api('/characters/status')).budget.used,0);assert.equal((await store.view(old.id)).requests.length,0);
});

test('navigation is opt-in and rejects non-loopback destinations',async()=>{
 await assert.rejects(startGroupServer({charactersUrl:'https://outside.invalid/characters'}),/NAVIGATION_INVALID/);
});

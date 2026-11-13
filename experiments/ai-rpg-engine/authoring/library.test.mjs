import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startAuthoringServer} from './server.mjs';
import {blankDocument} from './contracts.mjs';
import {descriptionText,isLoopbackOrigin} from './library.mjs';
const parent=fileURLToPath(new URL('../.rpg04-work/library-tests/',import.meta.url));
await mkdir(parent,{recursive:true});
const api='/rpg-app/api/authoring';
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=';
async function fixture(t){
 const directory=await mkdtemp(join(parent,'store-'));
 const host=await startAuthoringServer({directory,creatorExperience:true});t.after(()=>host.close());
 const request=async(path,body,headers={})=>{
  const response=await fetch(host.origin+api+path,body===undefined?{headers}:{method:'POST',headers:{'Content-Type':'application/json',Origin:host.origin,'X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});
  return {response,value:await response.json()};
 };
 const document=blankDocument();document.metadata.name='雾港';document.metadata.audience='female';document.sources.system='PRIVATE_PROMPT';document.sources.description='<p>雾中的来信 &amp; 远行</p><script>PRIVATE_SCRIPT</script>';document.sources.startupHtml='<h1>已发布的开局</h1>';
 const id=(await request('/works',{operationId:'create',expectedRevision:null,document})).value.workId;
 return {host,request,document,id};
}
test('drafts are private; publication lists one frozen entry and later releases replace that entry',async t=>{
 const {request,document,id}=await fixture(t);
 const read=async()=> (await request('/library')).value;
 assert.deepEqual((await read()).works,[]);
 const first=(await request(`/works/${id}/publish`,{operationId:'publish',expectedRevision:0})).value;
 const initial=await read();assert.equal(initial.works.length,1);assert.equal(initial.works[0].name,'雾港');assert.equal(initial.works[0].cover,null);assert.equal(initial.works[0].description,'雾中的来信 & 远行');
 assert.doesNotMatch(JSON.stringify(initial),/PRIVATE|startupHtml|sources|assets/);
 document.metadata.name='草稿新标题';document.sources.startupHtml='<h1>尚未发布的开局</h1>';
 await request(`/works/${id}/save`,{operationId:'edit',expectedRevision:1,document});
 assert.deepEqual(await read(),initial);
 const old=(await request(`/works/${id}/published?version=${first.result.versionId}`)).value;
 assert.equal(old.draft.metadata.name,'雾港');assert.equal(old.versions.length,1);assert.equal(old.publishedVersionId,first.result.versionId);
 const preview=await request('/presentations/prepare',{workId:id,versionId:first.result.versionId,expectedRevision:old.revision,document:old.draft,mode:'opening',reply:''});
 assert.equal(preview.response.status,200);
 const frame=await fetch((new URL(preview.value.frameUrl,(await request('/state')).response.url)).href,{headers:{'Sec-Fetch-Dest':'iframe'}});
 assert.match(await frame.text(),/已发布的开局/);
 const tampered=await request('/presentations/prepare',{workId:id,versionId:first.result.versionId,expectedRevision:old.revision,document,mode:'opening',reply:''});
 assert.equal(tampered.response.status,409);
 await request(`/works/${id}/publish`,{operationId:'publish-2',expectedRevision:2});
 const latest=await read();assert.equal(latest.works.length,1);assert.equal(latest.works[0].id,id);assert.equal(latest.works[0].name,'草稿新标题');assert.equal(latest.works[0].versionNumber,2);
 assert.equal((await request(`/works/${id}/published?version=${first.result.versionId}`)).value.draft.metadata.name,'雾港');
 assert.equal((await request(`/works/${id}/published`)).response.status,422);
});
test('loopback read access exposes only listing and frozen cover; private APIs and writes stay same-origin',async t=>{
 const {host,request,document,id}=await fixture(t),local={Origin:'http://127.0.0.1:18473'},other={Origin:'https://outside.invalid'};
 await request(`/works/${id}/assets`,{operationId:'asset',expectedRevision:0,alias:'cover',fileName:'cover.png',mediaType:'image/png',base64:png});
 document.settings.media.cover='cover';await request(`/works/${id}/save`,{operationId:'edit',expectedRevision:1,document});
 const pub=(await request(`/works/${id}/publish`,{operationId:'publish',expectedRevision:2})).value;
 const catalog=await request('/library',undefined,local);assert.equal(catalog.response.status,200);assert.equal(catalog.response.headers.get('access-control-allow-origin'),local.Origin);assert.equal(catalog.response.headers.get('access-control-allow-credentials'),null);
 const cover=catalog.value.works[0].cover;
 const image=await fetch(host.origin+cover,{headers:local});assert.equal(image.status,200);assert.deepEqual(Buffer.from(await image.arrayBuffer()),Buffer.from(png,'base64'));
 await request(`/works/${id}/assets/remove`,{operationId:'remove',expectedRevision:3,alias:'cover'});
 assert.equal((await fetch(host.origin+cover,{headers:local})).status,200);
 for(const path of ['/state','/works',`/works/${id}`,`/works/${id}/published?version=${pub.result.versionId}`,`/works/${id}/assets/cover?version=${pub.result.versionId}`])assert.equal((await request(path,undefined,local)).response.status,403,path);
 assert.equal((await request(`/works/${id}/publish`,{operationId:'forbidden',expectedRevision:4},local)).response.status,403);
 for(const headers of [other,{Origin:'null'},{Origin:'http://127.0.0.1.evil.test'},{'Sec-Fetch-Site':'cross-site'}])assert.equal((await request('/library',undefined,headers)).response.status,403);
 assert.equal((await fetch(host.origin+cover,{headers:other})).status,403);
 assert.equal((await request('/library?version=draft')).response.status,422);
});
test('description extraction skips executable content and loopback origin validation rejects URL tricks',()=>{
 assert.equal(descriptionText('<style>hidden</style><p>Hello<br>world</p><template>hidden</template>'),'Hello world');
 for(const url of ['http://localhost:5173','http://127.0.0.1:5173'])assert.equal(isLoopbackOrigin(url),true);
 for(const url of ['https://localhost','http://localhost/x','http://user@localhost','http://localhost.evil','null'])assert.equal(isLoopbackOrigin(url),false);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {start} from '../../studio/server.mjs';
import {newSetup} from '../lib/setup.mjs';
const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b3-ui/',import.meta.url));
test('God UI serves only reviewed local files; setup can frame under same origin and API remains restricted',async t=>{
 await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'static-'));
 const h=await start({port:0,directory,godCard:true,godPlugins:true,limit:0});
 t.after(async()=>{await h.plugins.close();await new Promise(r=>h.server.close(r));});
 const base='http://127.0.0.1:'+h.server.address().port;
 for(const path of ['setup/index.html','setup/styles.css','setup/app.mjs','lib/setup.mjs']){
  const r=await fetch(base+'/rpg-app/god/'+path);assert.equal(r.status,200,path);assert.match(r.headers.get('content-security-policy'),/frame-ancestors 'self'/);
  if(path==='setup/index.html'){const text=await r.text();assert.match(text,/href="\.\/styles.css"/);assert.match(text,/src="\.\/app.mjs"/);}
 }
 assert.equal((await fetch(base+'/rpg-app/god/lib/assembly.mjs')).status,404);
 assert.equal((await fetch(base+'/rpg-app/god/resources/prompts.json')).status,404);
 const status=await (await fetch(base+'/rpg-app/god/api/status')).json();assert.equal(status.providerEnabled,false);assert.equal(status.offlineBudget.used,0);
 const rejected=await fetch(base+'/rpg-app/god/api/sessions',{method:'POST',headers:{Origin:'https://example.com','Content-Type':'application/json'},body:'{}'});assert.equal(rejected.status,403);
});

test('both HTTP projections use card-specific presentation without changing saved raw or history',async t=>{
 const directory=await mkdtemp(join(root,'projection-'));
 const h=await start({port:0,directory,godCard:true,limit:0});
 t.after(async()=>{await h.plugins.close();await new Promise(r=>h.server.close(r));});
 const base='http://127.0.0.1:'+h.server.address().port;
 for(const [card,s,store] of [
  ['god',await h.god.create({setup:newSetup(),mode:'offline'}),h.god],
  ['earth',await h.earth.create({characterText:'姓名：测试',world:'表世界',params:{},mode:'offline'}),h.earth],
 ]){
  const raw=card==='god'?'<div class="danmu-track"><span>文字</span></div>':'<div class="char-card">文字</div>';
  s.history=[{role:'user',content:'开局'},{role:'assistant',content:raw}];s.turns=[{raw,input:'开局',requestId:'fixture'}];await store.write(s);
  const got=await (await fetch(base+'/rpg-app/'+card+'/api/sessions/'+s.id)).json();
  assert.match(got.turns[0].html,new RegExp('data-profile="'+card+'"'));assert.equal(got.turns[0].raw,raw);
  assert.deepEqual((await store.read(s.id)).history,s.history);assert.equal((await store.read(s.id)).turns[0].raw,raw);
 }
});

test('isolated build path serves both card entrypoints and only fixed God images with image MIME',async t=>{
 const directory=await mkdtemp(join(root,'presentation-')),dist=join(directory,'isolated-dist');
 await mkdir(join(dist,'assets'),{recursive:true});
 for(const name of ['god.html','index.html'])await writeFile(join(dist,name),'test-entry-'+name);
 for(const name of ['god-commentary-v1.webp','god-commentary-alt-v1.jpg','unknown.jpg'])await writeFile(join(dist,'assets',name),'fixture');
 const h=await start({port:0,directory:join(directory,'data'),cardDist:dist,godCard:true,godPlugins:true,limit:0});
 t.after(async()=>{await h.plugins.close();await new Promise(r=>h.server.close(r));});
 const base='http://127.0.0.1:'+h.server.address().port;
 assert.equal(await (await fetch(base+'/rpg-app/god/')).text(),'test-entry-god.html');
 assert.equal(await (await fetch(base+'/rpg-app/earth/')).text(),'test-entry-index.html');
 for(const [name,mime] of [['god-commentary-v1.webp','image/webp'],['god-commentary-alt-v1.jpg','image/jpeg']]){
  const r=await fetch(base+'/rpg-app/god/assets/'+name);assert.equal(r.status,200);assert.equal(r.headers.get('content-type'),mime);
  assert.match(r.headers.get('content-security-policy'),/img-src 'self' data:/);
 }
 assert.equal((await fetch(base+'/rpg-app/god/assets/unknown.jpg')).status,404);
});

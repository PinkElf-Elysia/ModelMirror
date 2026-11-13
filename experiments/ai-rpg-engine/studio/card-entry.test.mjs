import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from './server.mjs';

test('nested card documents load shared entry scripts and styles from the static card route',async t=>{
  const root=fileURLToPath(new URL('../.rpg04-work/card-entry-tests/',import.meta.url));
  await mkdir(root,{recursive:true});
  const directory=await mkdtemp(join(root,'run-')),dist=join(directory,'web');
  await mkdir(join(dist,'assets'),{recursive:true});
  await writeFile(join(dist,'index.html'),'<link href="./assets/card.css" rel="stylesheet"><script type="module" src="./assets/card.js"></script>');
  await writeFile(join(dist,'assets/card.css'),'body{}');await writeFile(join(dist,'assets/card.js'),'export {}');
  const {server}=await start({port:0,directory:join(directory,'data'),cardDist:dist,enabled:false});
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const origin='http://127.0.0.1:'+server.address().port;
  for(const path of ['earth/','xingjian/','ancient/chat/','cultivation/chat/','lord-of-mysteries/chat/','jinyong/chat/','medieval/chat/','harry-potter/chat/']){
    const url=origin+'/rpg-app/'+path,document=await fetch(url);
    assert.equal(document.status,200);
    const assets=[...(await document.text()).matchAll(/(?:src|href)="([^"]+)"/g)].map(m=>new URL(m[1],url));
    assert.deepEqual(assets.map(u=>u.pathname),['/rpg-app/earth/assets/card.css','/rpg-app/earth/assets/card.js']);
    for(const asset of assets)assert.equal((await fetch(asset)).status,200);
  }
});

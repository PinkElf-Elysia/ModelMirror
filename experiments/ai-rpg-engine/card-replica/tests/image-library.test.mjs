import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import library from '../resources/image-library.json' with {type:'json'};
import inventory from '../../douluo-card/resources/assets.json' with {type:'json'};
import disabled from '../../douluo-card/resources/unavailable-images.json' with {type:'json'};
import {imageMap,imageCandidates} from '../lib/image-library.mjs';
test('unified image library verifies every local file and has no remote fallback',async()=>{
 assert.equal(library.format,1);assert.equal(library.images.length,75);
 const keys=new Set();
 for(const a of library.images){
  const key=a.card+':'+a.source;assert.ok(!keys.has(key));keys.add(key);
  if(!a.enabled){assert.equal(a.status,'unavailable');assert.equal(a.file,undefined);continue;}
  assert.equal(a.status,'local');assert.match(a.file,/^(card-replica|douluo-card)\/public\/assets\/[a-zA-Z0-9.-]+$/);
  assert.ok(!a.local.includes('..'));assert.ok(!/^https?:/.test(a.local));
  const b=await readFile(new URL('../../'+a.file,import.meta.url));assert.equal(b.length,a.bytes);assert.equal(createHash('sha256').update(b).digest('hex'),a.sha256);
 }
 assert.equal(imageCandidates('god','commentary').length,17);assert.deepEqual(imageCandidates('douluo','commentary'),[]);
 assert.equal(imageCandidates('douluo','opening').length,46);assert.deepEqual(imageCandidates('unknown','commentary'),[]);
});
test('unified inventory preserves frozen source receipts and excludes all disabled avatars',()=>{
 for(const a of inventory.filter(a=>a.contentType.startsWith('image/'))){const found=library.images.find(i=>i.source===a.url&&i.card==='douluo');assert.equal(found.sha256,a.sha256);assert.equal(imageMap('douluo')[a.url],'/rpg-app/douluo/'+a.file);}
 for(const a of disabled.images){const found=library.images.find(i=>i.source===a.url&&i.card==='douluo');assert.equal(found.enabled,false);assert.equal(found.httpStatus,404);assert.equal(imageMap('douluo')[a.url],undefined);}
});

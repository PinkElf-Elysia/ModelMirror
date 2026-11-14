import {sha256} from '../authoring/contracts.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
import {legacyArtifacts} from './legacy-compat.mjs';
test('precise transition rejects a changed digest',async()=>{
 const review=JSON.parse(await readFile(new URL('./legacy-transition.json',import.meta.url)));
 for(const [p,c]of Object.entries(review.changes))assert.equal(sha256(await readFile(new URL('../'+p,import.meta.url))),c.after,'reviewed current artifact: '+p);
 const map=Object.fromEntries(Object.entries(review.changes).map(([p,c])=>[p,c.after]));
 const got=await legacyArtifacts(map,new URL('../',import.meta.url));for(const [p,c]of Object.entries(review.changes))assert.equal(got[p],c.before);
 const bad={...map,'authoring/contracts.mjs':'0'.repeat(64)};assert.deepEqual(await legacyArtifacts(bad,new URL('../',import.meta.url)),bad);
});

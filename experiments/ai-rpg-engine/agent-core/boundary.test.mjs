import {test} from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';import {adapters} from './composition.mjs';import {hash} from './contracts.mjs';
test('core does not import or branch on a card and both adapters consume shared numeric primitive',async()=>{
 for(const name of ['contracts','arithmetic','store','host','query-protocol','simulator','manifest']){
  const s=await readFile(new URL('./'+name+'.mjs',import.meta.url),'utf8');
  assert.doesNotMatch(s,/cultivation|lighthouse|local-state\/|local-state['"]|adapters\//,name);
 }
 for(const p of ['./adapters/cultivation/rules.mjs','./adapters/lighthouse/index.mjs'])assert.match(await readFile(new URL(p,import.meta.url),'utf8'),/integerDelta\(/);
 const {list,manifest}=await adapters();assert.equal(list[0].binding.manifestHash,list[1].binding.manifestHash);assert.equal(hash(manifest.files),manifest.hash);
 assert.ok(manifest.files['agent-core/store.mjs']);assert.ok(manifest.files['authoring/session-storage.mjs']);assert.ok(manifest.files['cultivation-card/author-system.txt']);
 const raw=await readFile(new URL('../cultivation-card/author-system.txt',import.meta.url),'utf8');
 assert.equal(hash(raw),'9a1e707828659b3ee1daa9628c334c1782b6526b066287836e7f8db7806a5dc2');
});

test('legacy bytes remain frozen except the exact authorized real-bridge overlay',async()=>{
 const {createHash}=await import('node:crypto');const b=JSON.parse(await readFile(new URL('./baseline.json',import.meta.url),'utf8'));
 const overlay=JSON.parse(await readFile(new URL('./real/authorized-bridge.json',import.meta.url),'utf8'));
 assert.deepEqual(Object.keys(overlay.files).sort(),['server/model_router/rpg_bridge.py','server/model_router/rpg_local_state_tools.py']);
 for(const [path,expected] of Object.entries(overlay.files)){
  if(b.files[path])assert.equal(expected.before,b.files[path].workingSha256,path);
  assert.equal(createHash('sha256').update(await readFile(new URL('../../../'+path,import.meta.url))).digest('hex'),expected.after,path);
 }
 for(const [path,expected] of Object.entries(b.files)){
  const content=await readFile(new URL('../../../'+path,import.meta.url));assert.equal(createHash('sha256').update(content).digest('hex'),overlay.files[path]?.after??expected.workingSha256,path);
 }
});

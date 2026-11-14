import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openSources,project,query} from './source.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/e4-tests/',import.meta.url));await mkdir(root,{recursive:true});
test('R5 independent sources, registry capabilities and readonly queries',async()=>{
 const dir=await mkdtemp(root+'source-'),sources=await openSources(dir);
 for(const [id,s] of sources){const f=await s.read(),before=JSON.stringify(f.record);assert.equal(f.record.binding.adapterVersion,'5');
 assert.equal(f.capabilities[1].id,s.adapter.rule.id);assert.equal(query(s.adapter,f).results[0].coverage,'complete-registered-scope');
 assert.equal(JSON.stringify((await s.read()).record),before);assert.equal(f.record.events.length,0);
 const bad=structuredClone(f.record);bad.binding.adapterVersion='4';assert.throws(()=>project(s.adapter,bad),{code:'STATE_BINDING_CHANGED'});
 const custom=structuredClone(f.record);custom.config.mods=['x'];assert.throws(()=>project(s.adapter,custom),{code:'E4_CONFIG_UNSUPPORTED'});
 assert.equal((await readFile(dir+'/'+id+'.json','utf8')).includes('e4-adult'),true);
 }
 const again=await openSources(dir);assert.equal((await again.get('cultivation').read()).reference.recordHash,(await sources.get('cultivation').read()).reference.recordHash);
});

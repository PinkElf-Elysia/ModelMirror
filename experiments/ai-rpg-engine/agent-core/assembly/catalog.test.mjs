import test from 'node:test';import assert from 'node:assert/strict';
import {countRegistered} from './capabilities.mjs';import {createRegistry} from '../actions/registry.mjs';
import {compileCatalog,exampleCatalog} from './catalog.mjs';import {clone} from '../contracts.mjs';
test('count distinguishes missing, unset and complete without mutating state',()=>{
 const e=countRegistered(s=>s.rows),r={stateRevision:2,state:{rows:[{id:'a',category:'x',quantity:5},{id:'b',category:'x',quantity:null}]}};
 const reg=createRegistry([e]),before=clone(r),v=reg.execute(e.name,{category:'x'},r,{allowed:[e.name]}).output;
 assert.equal(v.knownTotal,5);assert.deepEqual(v.unknown,['b']);assert.equal(v.coverage,'unset-quantities');assert.deepEqual(r,before);
 assert.equal(reg.execute(e.name,{category:'z'},r,{allowed:[e.name]}).output.coverage,'no-match');
 assert.throws(()=>reg.execute(e.name,{category:'x',session:'foreign'},r,{allowed:[e.name]}));assert.throws(()=>reg.execute(e.name,{category:'x'},r,{allowed:[]}));
 assert.throws(()=>createRegistry([e,e]));assert.throws(()=>createRegistry([{...e,effect:'write'}]));
 const huge={stateRevision:0,state:{rows:[{id:'a',category:'x',quantity:Number.MAX_SAFE_INTEGER},{id:'b',category:'x',quantity:1}]}};
 assert.throws(()=>reg.execute(e.name,{category:'x'},huge,{allowed:[e.name]}),/COUNT_OVERFLOW/);
});
test('catalog registers two state shapes, new tool and independent owner',async()=>{
 const c=await exampleCatalog(),r=compileCatalog(c);assert.equal(r.worlds.size,2);assert.equal(r.owners.size,3);
 for(const w of r.worlds.values()){const v=w.adapter.tools.execute('count_registered',{category:w.category},{state:w.adapter.initial(),stateRevision:0},{allowed:['count_registered']});assert.ok(v.output.knownTotal>0);}
 assert.throws(()=>compileCatalog({...c,owners:[...c.owners,c.owners[0]]}));
});

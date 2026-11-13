import test from 'node:test';
import assert from 'node:assert/strict';
import {createStatusRead} from '../src/status-read.ts';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
test('polls and manual refresh join a slow status read instead of starving its result',async()=>{
 const gate=deferred(),reader=createStatusRead();let calls=0,sequence=0,committed=null;
 const read=async()=>{calls++;const version=++sequence;const status=await gate.promise;if(version===sequence)committed=status;return status;};
 const first=reader.run('earth',read);await Promise.resolve();
 const polls=Array.from({length:8},()=>reader.run('earth',read));
 assert.equal(calls,1);for(const poll of polls)assert.equal(poll,first);
 gate.resolve({compatible:true,busy:false,sessionRevision:7});
 await Promise.all([first,...polls]);assert.deepEqual(committed,{compatible:true,busy:false,sessionRevision:7});
 await reader.run('earth',async()=>{calls++;return null;});assert.equal(calls,2);
});
test('session changes and unmount invalidate joining without cancelling old work',async()=>{
 const reader=createStatusRead(),old=deferred(),next=deferred();let calls=0;
 const a=reader.run('earth',()=>old.promise),b=reader.run('god',()=>next.promise);
 old.resolve('old');assert.equal(await a,'old');
 assert.equal(reader.run('god',async()=>{calls++;return 'wrong';}),b);
 reader.clear();const c=reader.run('god',async()=>{calls++;return 'fresh';});
 assert.equal(await c,'fresh');next.resolve('late');assert.equal(await b,'late');assert.equal(calls,1);
});
test('a failed read releases the slot and does not suppress the diagnostic',async()=>{
 const reader=createStatusRead(),gate=deferred();const first=reader.run('earth',()=>gate.promise);
 const joined=reader.run('earth',async()=>null);assert.equal(joined,first);
 gate.reject(new Error('status unavailable'));await assert.rejects(first,/status unavailable/);
 assert.equal(await reader.run('earth',async()=> 'recovered'),'recovered');
});

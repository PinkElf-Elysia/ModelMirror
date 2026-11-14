import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';
import {startServer} from './server.mjs';import {exampleState} from '../../local-state/example.mjs';
test('HTTP prototype allows draft edits only; source stays unchanged and invalid origin rejected',async t=>{
 const state=exampleState(),before=structuredClone(state),root=fileURLToPath(new URL('../../.rpg03-work/',import.meta.url));await mkdir(root,{recursive:true});
 const app=await startServer({port:0,directory:await mkdtemp(join(root,'e3-http-')),source:{read:async()=>({session:'http-test',branch:'http-test',version:1,enabled:true,pending:null,state:structuredClone(state)})}});t.after(()=>app.close());
 assert.equal((await fetch(app.url)).status,200);
 assert.equal((await fetch(app.url+'api/execute',{method:'POST'})).status,404);
 assert.equal((await fetch(app.url+'api/draft',{method:'POST',headers:{origin:'http://evil.invalid'}})).status,400);
 const headers={'Content-Type':'application/json','x-rpg-e3':'proposal-only'};
 const r=await fetch(app.url+'api/draft',{method:'POST',headers,body:JSON.stringify({kind:'reply',operationId:'test',text:'给那人一些丹药',expectedHash:null})});assert.equal(r.status,200);
 const v=await r.json();assert.equal(v.draft.current.status,'clarification');assert.equal(v.simulatedCalls,1);assert.equal((await fetch(app.url+'api/export')).status,400);
 await fetch(app.url+'api/view');assert.equal(app.provider.calls,1);assert.deepEqual(state,before);
 assert.equal((await fetch(app.url+'api/draft',{method:'POST',headers,body:JSON.stringify({kind:'execute',operationId:'bad',expectedHash:v.recordHash})})).status,400);
});

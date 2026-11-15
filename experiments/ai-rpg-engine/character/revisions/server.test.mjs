import test from 'node:test';import assert from 'node:assert/strict';import {startRevisionServer} from './server.mjs';
test('HTTP owner, same-origin, CSRF and command allowlist reject forged source and receipt',async t=>{
 let writes=0;const store={list:async()=>[],importSource:async()=>{writes++;return {};},view:async()=>({})},sources={list:async()=>[]},coordinator={run:fn=>fn()};const s=await startRevisionServer({store,host:{},sources,coordinator,charactersUrl:'http://127.0.0.1:1/characters',groupsUrl:'http://127.0.0.1:2/groups'});t.after(()=>s.close());
 const page=await fetch(s.origin+'/revisions'),cookie=page.headers.get('set-cookie').split(';')[0];assert.equal(page.status,200);assert.equal((await fetch(s.origin+'/api/bootstrap')).status,400);const boot=await(await fetch(s.origin+'/api/bootstrap',{headers:{cookie}})).json();
 const post=(path,headers,body)=>fetch(s.origin+path,{method:'POST',headers:{cookie,'content-type':'application/json',...headers},body:JSON.stringify(body)});
 assert.equal((await post('/api/import',{},{})).status,400);assert.equal((await post('/api/import',{origin:'https://other.test','x-csrf-token':boot.csrf},{})).status,400);
 assert.equal((await post('/api/settle',{origin:s.origin,'x-csrf-token':boot.csrf},{familyId:'f',operationId:'o',expectedRevision:0,payload:{receipt:'forged'}})).status,400);assert.equal(writes,0);
 assert.equal((await fetch(s.origin+'/api/bootstrap',{headers:{cookie,origin:'https://other.test'}})).status,400);
});

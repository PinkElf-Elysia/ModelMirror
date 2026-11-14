import test from 'node:test';import assert from 'node:assert/strict';import {fixture} from './test-support.mjs';import {startGroupServer} from './server.mjs';import {GroupHost} from './host.mjs';import {GroupFixtureProvider} from './fixture-provider.mjs';import {join} from 'node:path';
test('loopback owner, CSRF, strict action fields, safe DTO and reload',async t=>{
 const f=await fixture(t),p=await GroupFixtureProvider.open(join(f.directory,'provider')),host=new GroupHost(f.store,p),app=await startGroupServer({store:f.store,works:f.works,host});t.after(async()=>{await app.close();await p.close();});
 const displayModule=await fetch(app.origin+'/message-display.mjs');assert.equal(displayModule.status,200);assert.match(displayModule.headers.get('content-type'),/javascript/);assert.match(await displayModule.text(),/export function renderMessageBody/);
 assert.equal((await fetch(app.origin+'/api/bootstrap')).status,403);assert.equal((await fetch(app.origin+'/groups',{headers:{Origin:'https://evil.invalid'}})).status,403);
 const page=await fetch(app.origin+'/groups'),cookie=page.headers.get('set-cookie').split(';')[0],boot=await (await fetch(app.origin+'/api/bootstrap',{headers:{cookie}})).json();assert.ok(boot.csrf);assert.equal(boot.cards.length,1);
 const headers={cookie,Origin:app.origin,'Content-Type':'application/json','X-Group-CSRF':boot.csrf};const url=app.origin+'/api/groups/'+f.id;
 const safe=await (await fetch(url,{headers:{cookie}})).json();assert.doesNotMatch(JSON.stringify(safe),/configurationHash|privateRecords|EXAMPLE_ONLY/);
 const body={operationId:'send',expectedRevision:0,payload:{targetId:safe.members[1].id,input:'hi'}};
 assert.equal((await fetch(url+'/send',{method:'POST',headers:{cookie,Origin:app.origin,'Content-Type':'application/json'},body:JSON.stringify(body)})).status,403);
 assert.equal((await fetch(url+'/send',{method:'POST',headers,body:JSON.stringify({...body,payload:{...body.payload,system_prompt:'bad'}})})).status,409);
 const result=await (await fetch(url+'/send',{method:'POST',headers,body:JSON.stringify(body)})).json();assert.equal(result.messages.length,3);assert.equal(result.requests[0].status,'completed');assert.deepEqual(await (await fetch(url,{headers:{cookie}})).json(),result);
 assert.equal((await fetch(url+'/adopt',{method:'POST',headers,body:'{}'})).status,409);
});

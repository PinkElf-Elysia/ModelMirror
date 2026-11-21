import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp, readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {canonical, sha} from '../plugins/catalog.mjs';
import {installTypeMoon} from './integration.mjs';

test('the published HTTP session restores a persisted launch and injects only the selected entry at its original anchor', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'type-moon-session-'));
  const seen = [];
  const model = 'google/gemini-3.8-flash';
  const parameters = {temperature:0.7, top_p:0.8, max_tokens:16384};
  async function fetcher(url, options) {
    if (options.method === 'GET') return Response.json({models:[{model, name:'离线验收', available:true, parameters, selectionId:'fixture', selectionRevision:'v1'}]});
    const body = JSON.parse(options.body);
    seen.push(body.messages);
    const raw = '<p>雨停了。</p><say class="npc" data-speaker="所长" data-portrait="奥尔加玛丽-FGO" data-face="neutral"><button class="say-avatar"></button>准备好了吗？</say>', sse = 'data: [DONE]\n\n';
    return Response.json({raw, sse, receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}});
  }
  async function open() {
    const server = createServer((req,res)=>{res.writeHead(404);res.end();});
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const origin='http://127.0.0.1:'+server.address().port;
    installTypeMoon(server,{directory,origins:[origin],control:{enabled:true,limit:4,baseURL:'http://server:8000',serviceToken:'synthetic-type-moon-local-test-credential',fetcher}});
    const call=async(path,body)=>{
      const response=await fetch(origin+'/rpg-app/type-moon/chat/api/sessions'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
      const result=await response.json();
      assert.equal(response.status, path===''&&body!==undefined?201:200,JSON.stringify(result));
      return result;
    };
    const close=()=>new Promise(resolve=>server.close(resolve));
    t.after(()=>server.listening?close():undefined);
    return {call,close};
  }
  async function send(client,session,input){
    const path='/'+session.id;
    const state=await client.call(path+'/memory-palace');
    return client.call(path+'/send',{operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input});
  }
  const first=await open();
  const opening='第四次圣杯战争，我在冬木市登记档案。';
  const created=await first.call('',{opening});
  assert.deepEqual(created.turns,[]);
  assert.equal(seen.length,0);
  const session=await send(first,created,'图鉴编号3');
  assert.equal(session.turns.length,1);
  assert.match(session.turns[0].html,/\/rpg-app\/type-moon\/(?:portraits|dialogue)\//);
  assert.ok(session.turns[0].html.includes('准备好了吗？'));
  assert.ok(!session.turns[0].raw.includes('npc-26.webp'));
  assert.deepEqual(seen[0].map(m=>m.role),['system','user']);
  assert.ok(seen[0].at(-1).content.includes(opening));
  const reviewed=await readFile(new URL('./resources/reviewed-no3.txt',import.meta.url),'utf8');
  const system=seen[0][0].content;
  assert.ok(system.indexOf(reviewed)>system.indexOf('###英灵图鉴（全）至 No.461'));
  assert.ok(system.indexOf('<Memory_rules>')>system.indexOf(reviewed));
  await first.close();
  const reopened=await open();
  const restored=await reopened.call('/'+session.id);
  assert.ok(restored.runtime.compatible);
  assert.deepEqual(restored.turns,session.turns);
  const second=await send(reopened,restored,'我继续等候。');
  assert.equal(second.turns.length,2);
  assert.equal(seen.length,2);
  assert.deepEqual(seen[1].map(m=>m.role),['system','user','assistant','user']);
  assert.equal(seen[1][1].content,seen[0][1].content);
  assert.ok(!seen[1][0].content.includes(reviewed));
});

test('local preview dispatches a first turn to its offline adapter and saves the wire messages',async t=>{
  const directory=await mkdtemp(join(tmpdir(),'type-moon-offline-first-turn-'));
  const server=createServer();
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const origin='http://127.0.0.1:'+server.address().port;
  installTypeMoon(server,{directory,origins:[origin]});
  async function call(route,body){
    const response=await fetch(origin+'/rpg-app/type-moon/chat/api/sessions'+route,body===undefined?{}:{
      method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    assert.ok(response.ok,JSON.stringify(data));
    return data;
  }
  const session=await call('',{opening:''});
  const scope='/'+session.id;
  const state=await call(scope+'/memory-palace');
  const input='第四次圣杯战争，我走进冬木市档案室查阅英灵图鉴 No.3。';
  const result=await call(scope+'/send',{operationId:randomUUID(),expectedSessionRevision:session.revision,
    expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input});
  assert.equal(result.turns.length,1);
  assert.match(result.turns[0].raw,/离线演示/);
  const root=join(directory,'archive-chat','type-moon','offline','offline-plugin-dispatches','type-moon','slot-1');
  const request=JSON.parse(await readFile(join(root,'request.json'),'utf8'));
  assert.deepEqual(request.messages.map(m=>m.role),['system','user']);
  assert.equal(request.messages[1].content,input);
  const {assembleTurn,systemPrompt}=await import('./assembly.mjs');
  assert.equal(request.messages[0].content,assembleTurn(systemPrompt(),[],input).messages[0].content);
});

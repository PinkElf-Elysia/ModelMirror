import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir,mkdtemp,readFile,readdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../../studio/server.mjs';
import {createControlledProvider} from '../../studio/controlled-provider.mjs';
import {createModelPolicy} from '../../studio/card-model-policy.mjs';
import {canonical,sha} from '../../plugins/catalog.mjs';
import {loadFrozen as earthFrozen,activeSystem} from '../../card-replica/lib/assembly.mjs';
import {newSetup} from '../lib/setup.mjs';
import {loadFrozen} from '../lib/assembly.mjs';
const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b2-http/',import.meta.url));await mkdir(root,{recursive:true});
const token='synthetic-god-control-test-not-a-real-key';
const choice={selectionId:'mock-model',selectionRevision:'mock-v1',model:'synthetic/model'};
const params={max_tokens:16384,temperature:.7,top_p:.8}; // Synthetic transport fixture, not approved God defaults.
const raw='\n正文\t<details open><summary>资料</summary>原文</details><script>bad()</script><img src="https://example.invalid/remote">\n';
async function mock(directory){
 const wires=[];
 const server=createServer(async(req,res)=>{const chunks=[];for await(const c of req)chunks.push(c);
  assert.equal(req.headers.authorization,'Bearer '+token);const b=JSON.parse(Buffer.concat(chunks));wires.push(b);
  const slots=(await readdir(join(directory,'god'))).filter(s=>/^slot-\d+$/.test(s));assert.ok(slots.length);
  assert.ok((await Promise.all(slots.map(s=>readFile(join(directory,'god',s,'request.json'),'utf8')))).includes(JSON.stringify(b)));
  const sse='data: '+JSON.stringify({model:choice.model,choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({raw,sse,receipt:{...b,messages:undefined,gateway:'rpg_scoped',requestedModel:choice.model,actualModel:choice.model,dispatched:true,retries:0,status:'complete',error:null,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}}));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 return {wires,baseURL:'http://127.0.0.1:'+server.address().port+'/',close:()=>new Promise(r=>server.close(r))};
}
test('same HTTP host: God wire is frozen/plain, Earth remains wrapped, budgets and sessions isolated',async()=>{
 const directory=await mkdtemp(join(root,'wire-')),dispatches=join(directory,'test-dispatches'),route=await mock(dispatches);
 const provider=await createControlledProvider({directory:dispatches,baseURL:route.baseURL,serviceToken:token,enabled:true,limit:2,cardId:'god',modelPolicy:createModelPolicy({cardId:'god',parametersFor:model=>model===choice.model?params:null})});
 let earthWire;
 const host=await start({port:0,directory:join(directory,'host'),godCard:true,godGenerate:a=>provider.generate('god',{...a,params,selection:choice}),key:'synthetic-earth-key',enabled:true,limit:2,fetcher:async(u,o)=>{
  earthWire=JSON.parse(o.body);return new Response('data: '+JSON.stringify({model:earthWire.model,provider:'Google AI Studio',choices:[{delta:{content:'earth'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
 }});
 const origin='http://127.0.0.1:'+host.server.address().port;
 const api=async(card,path,data,from=origin)=>{const r=await fetch(origin+'/rpg-app/'+card+'/api/'+path,data===undefined?{}:{method:'POST',headers:{Origin:from,'Content-Type':'application/json'},body:JSON.stringify(data)});return {status:r.status,data:await r.json()};};
 try{
  const setup=newSetup();setup.user.姓名='虚构神明';setup.opening='不使用地球字段的开局';
  assert.equal((await api('god','sessions',{setup,mode:'offline'},'https://foreign.invalid')).status,403);
  assert.equal((await api('god','sessions',{setup,mode:'real'})).status,400);
  const s=(await api('god','sessions',{setup,mode:'offline'})).data;
  assert.equal((await api('god','sessions/'+s.id+'/send',{input:s.characterText,requestId:'injected',revision:0,history:[]})).status,400);
  const first={input:s.characterText,requestId:'first',revision:0};
  const one=(await api('god','sessions/'+s.id+'/send',first)).data;
  assert.equal(one.turns[0].raw,raw);assert.ok(!one.turns[0].html.includes('<script'));assert.ok(!one.turns[0].html.includes('https://'));
  await api('god','sessions/'+s.id+'/send',first);assert.equal(route.wires.length,1);
  await api('god','sessions/'+s.id+'/send',{input:' 继续\n原样 ',requestId:'second',revision:1});
  assert.deepEqual(route.wires[0].messages,[{role:'system',content:loadFrozen().system},{role:'user',content:s.characterText}]);
  assert.deepEqual(route.wires[1].messages,[...route.wires[0].messages,{role:'assistant',content:raw},{role:'user',content:' 继续\n原样 '}]);
  assert.equal((await provider.status()).used,2);assert.equal((await host.provider.status('earth')).used,0);
  assert.equal((await provider.evidence(s.id,'first')).ref,'god/slot-1/result.json');
  assert.equal((await api('earth','sessions/'+s.id)).status,404);
  const earth=(await api('earth','sessions',{characterText:'姓名：地球对照',world:'表世界',params:{},mode:'real'})).data;
  assert.equal((await api('god','sessions/'+earth.id)).status,404);
  await api('earth','sessions/'+earth.id+'/send',{input:'开局',requestId:'e1',revision:0});
  assert.deepEqual(earthWire.messages,[{role:'system',content:activeSystem},{role:'user',content:'姓名：地球对照\n\n开局'}]);
  await api('earth','sessions/'+earth.id+'/send',{input:'续轮',requestId:'e2',revision:1});
  assert.equal(earthWire.messages.at(-1).content,earthFrozen().prompts.prefix+'\n续轮\n'+earthFrozen().prompts.suffix);
  assert.equal((await api('god','sessions/'+s.id)).data.turns.length,2);
  assert.equal((await api('god','status')).data.providerEnabled,false);
  await assert.rejects(provider.generate('earth',{}),/CONTROL_CARD_INCOMPATIBLE/);
  const plugin=await api('god','sessions/'+s.id+'/plugins/rpg.branch-save');
  assert.equal(plugin.status,409);assert.equal(plugin.data.error,'PLUGIN_CARD_INCOMPATIBLE');
  await writeFile(join(directory,'wire-assertions.json'),JSON.stringify({kind:'synthetic-only',godFirst:route.wires[0].messages,godContinuation:route.wires[1].messages,earthContinuation:earthWire.messages,godBudget:await provider.status(),productionGodProviderEnabled:false},null,2));
 }finally{await host.plugins?.close();await new Promise(r=>host.server.close(r));await route.close();}
});
test('God HTTP route is absent unless explicitly enabled',async()=>{
 const dir=await mkdtemp(join(root,'disabled-'));const host=await start({port:0,directory:dir});
 try{const r=await fetch('http://127.0.0.1:'+host.server.address().port+'/rpg-app/god/api/status');assert.equal(r.status,404);assert.equal(host.god,null);}
 finally{await host.plugins?.close();await new Promise(r=>host.server.close(r));}
});

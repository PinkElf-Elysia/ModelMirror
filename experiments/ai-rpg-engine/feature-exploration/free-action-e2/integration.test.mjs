import {test} from 'node:test';import assert from 'node:assert/strict';import {createServer} from 'node:http';
import {mkdtemp,mkdir} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';
import {startServer} from './server.mjs';import {httpSource,project} from './source.mjs';import {exampleState} from '../../local-state/example.mjs';import {SOURCE_HASH} from '../../local-state/definition.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/',import.meta.url));await mkdir(root,{recursive:true});
function record(){return {id:'core-live-test',format:'modelmirror.rpg.agent-core-session/3',state:exampleState(),revision:0,stateRevision:0,pending:null,enabled:true,config:{profiles:[],mods:[]},
 binding:{adapterId:'cultivation',adapterVersion:'4',sourceHash:SOURCE_HASH,stateSchemaVersion:'local-state/1',rulesVersion:'cultivation-rules/3',manifestHash:'1'.repeat(64),effectiveContextHash:'2'.repeat(64)}};}
async function rig(t){let row=record();const methods=[];
 const upstream=createServer((req,res)=>{methods.push(req.method+' '+req.url);res.setHeader('content-type','application/json');res.end(JSON.stringify(row));});
 await new Promise(ok=>upstream.listen(0,'127.0.0.1',ok));t.after(()=>new Promise(ok=>upstream.close(ok)));
 const source=httpSource('http://127.0.0.1:'+upstream.address().port+'/api/cultivation/sessions/'+row.id,row.id);
 const directory=await mkdtemp(join(root,'e2-http-')),app=await startServer({port:0,source,directory:join(directory,'drafts')});t.after(()=>app.close());
 const get=async path=>(await fetch(app.url+path)).json();
 const post=async data=>{const r=await fetch(app.url+'api/draft',{method:'POST',headers:{'content-type':'application/json','x-rpg-e2':'proposal-only'},body:JSON.stringify(data)});return {status:r.status,...await r.json()};};
 return {app,get,post,row,methods};
}
test('actual HTTP GET chain reads changed versions and never emits a source write',async t=>{
 const {get,post,row,methods}=await rig(t);
 assert.equal((await get('api/view')).world.state.items[0].quantity,5);
 let v=await post({command:'revise',input:{text:'想拿两份养息丹给周禾，先拟方案',selection:[]},expectedHash:null});assert.equal(v.status,200);assert.equal(v.draft.current.expected[0].after,3);
 row.state.items[0].quantity=3;row.stateRevision++;row.revision++;
 assert.equal((await get('api/view')).world.state.items[0].quantity,3);assert((await get('api/view')).stale);
 assert.equal((await post({command:'confirm',expectedHash:v.recordHash,proposalHash:v.draft.current.hash})).error,'SOURCE_STALE');
 assert(methods.every(m=>m==='GET /api/cultivation/sessions/core-live-test'));
});
test('local quantity edit clears confirmation without model generation; insufficient amount cannot confirm',async t=>{
 const {app,post,row}=await rig(t),before=structuredClone(row);
 let v=await post({command:'revise',input:{text:'想拿两份养息丹给周禾，先拟方案',selection:[]},expectedHash:null});
 v=await post({command:'confirm',expectedHash:v.recordHash,proposalHash:v.draft.current.hash});
 v=await post({command:'quantity',quantity:3,expectedHash:v.recordHash,proposalHash:v.draft.current.hash});assert.equal(v.draft.current.expected[0].after,2);assert.equal(v.draft.current.confirmation,null);assert.equal(app.provider.calls,1);assert.equal(v.draft.current.goal,'修订方案用量：养息丹 3份');
 v=await post({command:'quantity',quantity:6,expectedHash:v.recordHash,proposalHash:v.draft.current.hash});assert.equal(v.draft.current.status,'blocked');
 assert.equal((await post({command:'confirm',expectedHash:v.recordHash,proposalHash:v.draft.current.hash})).error,'NOT_READY');assert.deepEqual(row,before);
});
test('no execution routes, cross origin write denied, inputs cannot select source or provider',async t=>{
 const {app,post}=await rig(t);assert.equal((await fetch(app.url+'api/execute',{method:'POST'})).status,404);
 assert.equal((await fetch(app.url+'api/draft',{method:'POST',headers:{origin:'http://untrusted.invalid','content-type':'application/json','x-rpg-e2':'proposal-only'},body:'{}'})).status,400);
 assert.equal((await post({command:'revise',source:'file:///private'})).error,'REQUEST_FIELDS');
 assert.throws(()=>httpSource('https://example.com','x'),{code:'SOURCE_URL'});
 assert.throws(()=>httpSource('http://127.0.0.1:1234/api/cultivation/sessions/other','x'),{code:'SOURCE_URL'});
});
test('customizations are diagnosed; no silent execution of Mod or source replacement',()=>{
 const r=record();r.config.mods=['a'];assert.throws(()=>project(r,{},r.id),{code:'SOURCE_CUSTOMIZATION_UNSUPPORTED'});
});

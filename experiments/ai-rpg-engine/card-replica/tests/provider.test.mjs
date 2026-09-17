import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {receive,reserve,settle,wirePayload,MODEL,ROUTE,createProvider} from '../lib/provider.mjs';
import {assemble,defaults,hash} from '../lib/assembly.mjs';
import {createHost} from '../server.mjs';
const receipt={actual_model:MODEL,requested_model:MODEL,fallback_attempts:0,engine:'newapi',strategy:'newapi_preferred',reason_codes:['qualified']};
function sse(text,complete=true){return 'data: '+JSON.stringify({model:MODEL,choices:[{delta:{content:text},finish_reason:null}]})+'\n\ndata: '+JSON.stringify({choices:[{delta:{},finish_reason:'stop'}]})+'\n\n'+(complete?'event: route_receipt\ndata: '+JSON.stringify(receipt)+'\n\ndata: [DONE]\n\n':'');}
const response=(text,complete=true)=>new Response(sse(text,complete),{headers:{'content-type':'text/event-stream'}});
async function ledger(){const dir=await mkdtemp(join(tmpdir(),'earth-provider-')),path=join(dir,'ledger.json');await writeFile(path,JSON.stringify({model:MODEL,route:ROUTE,limit:4,used:0,remaining:4,entries:[]}));return {dir,path};}
test('route payload preserves official strings and only explicitly supported parameters',()=>{
 const a=assemble({characterText:'虚构测试角色',input:'开始'}),w=wirePayload(a.messages,defaults);
 assert.deepEqual(w.payload.messages,a.messages);assert.equal(w.payload.temperature,.7);assert.equal(w.payload.top_p,.8);assert.equal(w.payload.max_tokens,8192);assert.deepEqual(w.effective.unsupported,['top_k','presence_penalty','frequency_penalty','thinking_budget']);assert.equal(w.payload.response_format,undefined);assert.equal(w.payload.compression.mode,'off');
});
test('full text retained, missing receipt blocks acceptance, no retries',async()=>{
 let calls=0;const a=await receive({}, {fetchImpl:async()=>{calls++;return response('\n原文\n');}});
 assert.equal(a.raw,'\n原文\n');assert.equal(a.status,'complete');
 const bad=await receive({}, {fetchImpl:async()=>{calls++;return response('部分正文',false);}});
 assert.equal(bad.raw,'部分正文');assert.equal(bad.status,'unknown');assert.equal(calls,2);
});
test('stream UTF8 and CRLF boundaries preserve exact text',async()=>{
 const bytes=new TextEncoder().encode(sse('全文🌍\n 保留').replaceAll('\n','\r\n'));
 const r=await receive({}, {fetchImpl:async()=>new Response(new ReadableStream({start(c){for(const b of bytes)c.enqueue(Uint8Array.of(b));c.close();}}),{headers:{'content-type':'text/event-stream'}})});
 assert.equal(r.status,'complete');assert.equal(r.raw,'全文🌍\n 保留');
});
test('reservation persists before dispatch; unknown and unreviewed output block new IDs',async()=>{
 const {path}=await ledger();const first=await reserve(path,{kind:'generation',requestHash:'a',freezeHash:'b'});
 assert.equal(JSON.parse(await readFile(path)).used,1);
 await assert.rejects(reserve(path,{kind:'generation',requestHash:'c',freezeHash:'b'}));
 await settle(path,first.id,{status:'complete'});await assert.rejects(reserve(path,{kind:'generation',requestHash:'d',freezeHash:'b'}));
});
test('real-mode actual host sends frozen wire bytes through adapter and keeps raw output',async()=>{
 const {dir,path}=await ledger(),messages=assemble({characterText:'姓名：独立虚构角色',input:'开始'}).messages;
 const freeze={requestHash:hash(JSON.stringify(wirePayload(messages,defaults).payload)),files:[]};
 let sent=null,count=0;
 const generate=createProvider({ledgerPath:path,evidenceDirectory:join(dir,'evidence'),freeze,fetchImpl:async(url,opts)=>{if(url.includes('provider-chat-control'))return Response.json({model_id:MODEL,capability:'chat_text',available:true,reason_code:'qualified'});count++;sent=JSON.parse(opts.body);assert.equal(JSON.parse(await readFile(path)).used,1);return response('\n完整输出\n');}});
 const {server,port}=await createHost({port:0,directory:join(dir,'sessions'),realGenerate:generate});
 const origin='http://127.0.0.1:'+port,post=async(p,b)=>{const r=await fetch(origin+p,{method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify(b)});return r.json();};
 try{const s=await post('/api/sessions',{characterText:'姓名：独立虚构角色',input:'ignored',world:'表世界',params:defaults,mode:'real'});const done=await post('/api/sessions/'+s.id+'/send',{input:'开始',requestId:'one',revision:0,messages:[{role:'system',content:'evil'}],model_id:'evil'});
 assert.equal(done.turns[0].raw,'\n完整输出\n');assert.deepEqual(sent,wirePayload(messages,defaults).payload);assert.equal(count,1);
 await post('/api/sessions/'+s.id+'/send',{input:'开始',requestId:'one',revision:0});assert.equal(count,1);
 }finally{await new Promise(r=>server.close(r));}
});

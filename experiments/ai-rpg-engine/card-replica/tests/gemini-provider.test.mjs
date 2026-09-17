import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {hash,assemble,defaults} from '../lib/assembly.mjs';
import {MODEL,ROUTE,reserve,settle,createGeminiProvider} from '../lib/gemini-provider.mjs';
import {createHost} from '../server.mjs';
async function fixture(){
 const work=await mkdtemp(join(tmpdir(),'earth-gemini-')),ledgerPath=join(work,'ledger.json');
 const ledger={schema:'earth-card-dispatch-ledger/2',model:MODEL,route:ROUTE,limit:4,used:2,remaining:2,entries:[{slot:1,id:'old-cert',kind:'certification',status:'complete',model:'gpt-5.6-luna'},{slot:2,id:'old-gen',kind:'generation',status:'complete',userReviewed:true,model:'gpt-5.6-luna'}]};
 await writeFile(ledgerPath,JSON.stringify(ledger));return {work,ledgerPath};
}
test('Gemini reservations preserve prior two calls and stop after certification and one output',async()=>{
 const {ledgerPath}=await fixture(),a=await reserve(ledgerPath,{kind:'certification',requestHash:'x',freezeHash:'y'});
 assert.equal(a.slot,3);await assert.rejects(reserve(ledgerPath,{kind:'generation'}),/hold/);
 await settle(ledgerPath,a.id,{status:'complete'});const b=await reserve(ledgerPath,{kind:'generation',requestHash:'z',freezeHash:'y'});
 assert.equal(b.slot,4);await settle(ledgerPath,b.id,{status:'complete'});
 await assert.rejects(reserve(ledgerPath,{kind:'generation'}),/hold/);
 const l=JSON.parse(await readFile(ledgerPath));assert.equal(l.used,4);assert.equal(l.remaining,0);assert.equal(l.entries[1].model,'gpt-5.6-luna');
});
test('actual HTTP host matches frozen Gemini request and reserves before a mocked route dispatch',async()=>{
 const {work,ledgerPath}=await fixture(),role='姓名:离线虚构角色\n背景:2008与2010年代矛盾保留  ',payload={model:MODEL,messages:assemble({characterText:role,input:'开始这一世。'}).messages,temperature:.7,top_p:.8,max_tokens:8192};
 const body=JSON.stringify(payload),freeze={params:defaults,files:[],requests:{generation:hash(body)}};
 await writeFile(join(work,'generation-request.json'),body);await writeFile(join(work,'freeze.json'),JSON.stringify(freeze));
 let count=0;
 const fetchImpl=async(url,opts)=>{
  if(url.includes('/api/preview'))return Response.json({payload,requestHash:hash(body),qualified:true});
  assert.equal(url,ROUTE+'/api/dispatch');assert.deepEqual(Object.keys(JSON.parse(opts.body)),['reservationId']);
  const l=JSON.parse(await readFile(ledgerPath)),e=l.entries.at(-1);assert.equal(l.used,3);assert.equal(e.status,'pending');count++;
  const raw='\n模型原文🌍\n',dir=join(work,'dispatches','slot-'+e.slot);await mkdir(dir,{recursive:true});await writeFile(join(dir,'output.txt'),raw);
  return Response.json({status:'complete',reservationId:e.id,requestHash:hash(body),rawHash:hash(raw),requestedModel:MODEL,actualModel:MODEL,actualProvider:'Google AI Studio',done:true,finishReason:'stop'});
 };
 const {server,port}=await createHost({port:0,directory:join(work,'sessions'),realGenerate:createGeminiProvider({ledgerPath,work,fetchImpl})});
 const origin='http://127.0.0.1:'+port,post=async(path,data)=>{const r=await fetch(origin+path,{method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify(data)});return r.json();};
 try{
  const s=await post('/api/sessions',{characterText:role,world:'表世界',params:defaults,mode:'real'});
  const done=await post('/api/sessions/'+s.id+'/send',{input:'开始这一世。',requestId:'one',revision:0,model:'evil',messages:[{role:'system',content:'evil'}]});
  assert.equal(done.turns[0].raw,'\n模型原文🌍\n');assert.equal(count,1);
  await post('/api/sessions/'+s.id+'/send',{input:'开始这一世。',requestId:'one',revision:0});assert.equal(count,1);
 }finally{await new Promise(r=>server.close(r));}
});
test('host input drift is rejected before any route request or reservation',async()=>{
 const {work,ledgerPath}=await fixture();
 await writeFile(join(work,'freeze.json'),JSON.stringify({params:defaults}));
 await writeFile(join(work,'generation-request.json'),JSON.stringify({messages:[]}));
 let calls=0;const fn=createGeminiProvider({ledgerPath,work,fetchImpl:async()=>{calls++;throw Error();}});
 await assert.rejects(fn({messages:[{role:'system',content:'changed'}],params:defaults}),/differs/);
 assert.equal(calls,0);assert.equal(JSON.parse(await readFile(ledgerPath)).used,2);
});

import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,open,rename,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {createHost} from '../server.mjs';
import {hash,loadFrozen,activeSystem,removedWorldDeclaration} from '../lib/assembly.mjs';
import {buildComparison,MODEL} from '../lib/openrouter-config.mjs';
const base=fileURLToPath(new URL('../',import.meta.url)),work=join(base,'.local/gemini-retest'),ledgerPath=join(base,'resources/RETEST_LEDGER.json'),route='http://127.0.0.1:18308';
const readj=async p=>JSON.parse(await readFile(p,'utf8'));
const mode=process.argv[2];
async function verify(){
 const f=await readj(join(work,'freeze.json'));
 for(const x of f.files)assert.equal(hash(await readFile(x.path)),x.sha256,'Source drift '+x.path);
 const req=await readFile(join(work,'generation-request.json'),'utf8');assert.equal(hash(req),f.requests.generation);
 return {f,req};
}
if(mode==='--prepare'){
 await mkdir(work,{recursive:true});
 const characterText=await readFile(join(base,'.local/comparison-openrouter-20260917/character.txt'),'utf8');
 const catalog=await readj(join(base,'.local/comparison-openrouter-20260917/catalog.json'));
 const {payload,profile}=buildComparison(characterText,catalog),old=await readj(join(base,'.local/gemini-real/dispatches/slot-4/request.json'));
 assert.equal(payload.messages[0].content,old.messages[0].content.replace(removedWorldDeclaration,''));
 assert.deepEqual({...payload,max_tokens:old.max_tokens,messages:old.messages},old);
 assert.deepEqual(payload.messages.slice(1),old.messages.slice(1));assert.equal(payload.max_tokens,16384);
 const paths=['server.mjs','lib/store.mjs','lib/assembly.mjs','lib/render.mjs','lib/openrouter-config.mjs','tools/retest.mjs','tools/openrouter-route.py','resources/prompts.json','resources/MANIFEST.json','resources/RUN_SETTINGS.json','package.json','package-lock.json','src/main.tsx','src/platform.tsx','src/styles.css','.local/gemini-real/transport/mm_transport/__init__.py','.local/gemini-real/transport/mm_transport/egress.py','.local/gemini-real/transport/mm_transport/provider_chat.py','.local/gemini-real/qualification.json'];
 const files=await Promise.all(paths.map(async p=>({path:join(base,p),sha256:hash(await readFile(join(base,p)))})));
 const body=JSON.stringify(payload),f={at:new Date().toISOString(),model:MODEL,route,world:'表世界',characterText,input:'开始这一世。',params:profile.requestedParameters,requests:{generation:hash(body)},files,worldbookEnabled:[],sourceSystemHash:hash(loadFrozen().prompts.system),activeSystemHash:hash(activeSystem),removedSentence:removedWorldDeclaration,changeFromPrior:['system: exact one-sentence deletion; paragraph breaks preserved','max_tokens: 8192 -> 16384'],qualification:'prior same-model Google AI Studio certification; exact transport unchanged; no repeated certification',automaticRetry:false};
 await writeFile(join(work,'generation-request.json'),body,{flag:'wx'});
 await writeFile(join(work,'freeze.json'),JSON.stringify(f),{flag:'wx'});
 const ledger={schema:'earth-card-single-retest/1',authorization:'用户：同意结论，删除这句授权复测。新增单次；原4次账本保留不变；失败、取消、未知均计数，无重试。',model:MODEL,route,limit:1,used:0,remaining:1,entries:[],priorLedger:'resources/CALL_LEDGER.json',priorUsed:4,priorRemaining:0};
 await writeFile(ledgerPath,JSON.stringify(ledger,null,2)+'\n',{flag:'wx'});
 for(const x of files){const rel=x.path.slice(base.length);const dest=join(work,'source-at-dispatch',rel);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,await readFile(x.path),{flag:'wx'});}
 console.log(JSON.stringify({prepared:true,requestHash:hash(body),activeSystemHash:f.activeSystemHash,realCalls:0}));
}else if(['--check','--generate'].includes(mode)){
 const {f,req}=await verify();let count=0;
 const realGenerate=async({messages,params,signal})=>{
  assert.deepEqual(messages,JSON.parse(req).messages);assert.deepEqual(params,f.params);
  await writeFile(join(work,mode==='--check'?'actual-host-preflight.json':'actual-host-request.json'),JSON.stringify({messages,params,requestHash:hash(req)}));
  if(mode==='--check'){count++;return '离线装配核对样例';}
  const p=await fetch(route+'/api/preview?kind=generation',{signal:AbortSignal.timeout(10000),redirect:'error'});
  assert.ok(p.ok);const preview=await p.json();assert.equal(preview.requestHash,hash(req));assert.deepEqual(preview.payload,JSON.parse(req));assert.equal(preview.qualified,true);assert.ok(!signal.aborted);
  const lock=await open(ledgerPath+'.lock','wx');let e;
  try{
   const l=await readj(ledgerPath);assert.equal(l.limit,1);assert.equal(l.used,0);assert.equal(l.entries.length,0);assert.equal(l.remaining,1);assert.equal(l.model,MODEL);assert.equal(l.route,route);
   e={id:randomUUID(),slot:1,kind:'generation',status:'pending',requestHash:hash(req),freezeHash:hash(await readFile(join(work,'freeze.json'))),at:new Date().toISOString(),userReviewed:false};
   l.entries.push(e);l.used=1;l.remaining=0;await writeFile(ledgerPath+'.tmp',JSON.stringify(l,null,2)+'\n');await rename(ledgerPath+'.tmp',ledgerPath);
  }finally{await lock.close();await unlink(ledgerPath+'.lock');}
  let result;
  try{
   const r=await fetch(route+'/api/dispatch',{method:'POST',headers:{origin:route,'content-type':'application/json'},body:JSON.stringify({reservationId:e.id}),redirect:'error',signal:AbortSignal.any([signal,AbortSignal.timeout(330000)])});
   assert.ok(r.ok);result=await r.json();assert.equal(result.reservationId,e.id);assert.equal(result.requestHash,e.requestHash);
   if(result.status==='complete'){assert.equal(result.done,true);assert.equal(result.finishReason,'stop');assert.equal(result.requestedModel,MODEL);assert.ok([MODEL,'google/gemini-3.8-flash-20260902'].includes(result.actualModel));assert.equal(result.actualProvider,'Google AI Studio');}
  }catch{result={status:'unknown',error:'DISPATCH_UNCONFIRMED_NO_RETRY'};}
  const l=await readj(ledgerPath);Object.assign(l.entries[0],{status:result.status==='complete'?'complete':'unknown',result,completedAt:new Date().toISOString(),evidence:'.local/gemini-retest/dispatches/slot-1'});
  await writeFile(ledgerPath+'.tmp',JSON.stringify(l,null,2)+'\n');await rename(ledgerPath+'.tmp',ledgerPath);
  if(result.status!=='complete')throw Error('Unknown/incomplete; no retry');
  const raw=await readFile(join(work,'dispatches/slot-1/output.txt'),'utf8');assert.equal(hash(raw),result.rawHash);assert.ok(raw.trim());count++;return raw;
 };
 const h=await createHost({port:0,directory:join(work,mode==='--check'?'preflight-sessions':'sessions'),realGenerate});
 const origin='http://127.0.0.1:'+h.port,post=async(path,body)=>{const r=await fetch(origin+path,{method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify(body)});assert.ok(r.ok);return r.json();};
 try{
  const s=await post('/api/sessions',{characterText:f.characterText,world:f.world,params:f.params,mode:'real'});
  const done=await post('/api/sessions/'+s.id+'/send',{input:f.input,requestId:'single-authorized-retest',revision:0});
  await writeFile(join(work,mode==='--check'?'preflight-result.json':'host-result.json'),JSON.stringify(done));
  if(mode==='--check')assert.equal(count,1);
  console.log(JSON.stringify({mode,sessionId:s.id,completeTurns:done.turns.length,requests:done.requests,realCalls:mode==='--check'?0:(await readj(ledgerPath)).used}));
 }finally{await new Promise(r=>h.server.close(r));}
}else throw Error('Use --prepare, --check or --generate');
